#!/usr/bin/env python3
"""Движок лаунчера (без GUI) — тестируется headless.

Две модели модов:
  * "zip"  — generic: ссылка на GitHub Release -> берём zip-ассет -> распаковка в Mods.
  * "unit" — мод из агрегатора sr-mods-aggregator: сборка по рецепту
             (код-трек релиз + content-addressed ассет-чанки по manifest+asset_index).

Пути установки нормализуются по сегменту 'Mods/': всё после него кладётся в игровую
папку Mods (архивы модов часто имеют обёртку вида '<Имя>/Mods/<Mod>/...').
"""
import hashlib
import json
import os
import re
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

import requests

API = 'https://api.github.com'

# Сколько частей качать одновременно. На канале с большой задержкой/DPI один поток
# не насыщает полосу (предел = окно/RTT) — параллель даёт основной выигрыш (как у
# браузера). Умеренно (по умолч. 6), чтобы не словить rate-limit HF / лишний DPI.
PARALLEL_DOWNLOADS = int(os.environ.get('SRML_PARALLEL', '6'))

# Общая HTTP-сессия с пулом соединений: переиспользование TCP+TLS между частями
# (без неё requests.get на каждую часть = новый handshake). Пул потокобезопасен.
_SESSION = requests.Session()
_adapter = requests.adapters.HTTPAdapter(
    pool_connections=PARALLEL_DOWNLOADS * 2, pool_maxsize=PARALLEL_DOWNLOADS * 2,
    max_retries=0)
_SESSION.mount('https://', _adapter)
_SESSION.mount('http://', _adapter)

# Детальность лога: True — показывать построчно загрузку каждой части; False —
# только итоги по модам. GUI переключает перед операцией.
LOG_VERBOSE = True


class OperationCancelled(Exception):
    """Операция отменена пользователем (кооперативная отмена через should_cancel)."""


def _check_cancel(should_cancel):
    if should_cancel and should_cancel():
        raise OperationCancelled()


def _with_retries(fn, attempts=4, base_delay=1.5, should_cancel=None):
    """Повторять сетевой вызов при таймаутах/обрывах/5xx (нестабильная сеть/DPI).
    4xx (кроме 429) не повторяем. Между попытками — нарастающая пауза."""
    last = None
    for k in range(attempts):
        _check_cancel(should_cancel)
        try:
            return fn()
        except (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
            last = e
        except requests.HTTPError as e:
            sc = e.response.status_code if e.response is not None else 0
            if sc == 429 or 500 <= sc < 600:
                last = e
            else:
                raise
        if k < attempts - 1:
            time.sleep(base_delay * (k + 1))
    raise last


# ---------------------------------------------------------------------------
# GitHub helpers (поддержка приватных репозиториев через token)
# ---------------------------------------------------------------------------

def _headers(token, accept='application/vnd.github+json'):
    h = {'Accept': accept, 'X-GitHub-Api-Version': '2022-11-28'}
    if token:
        h['Authorization'] = 'Bearer ' + token
    return h


def gh_json(url, token, params=None, should_cancel=None):
    def do():
        r = requests.get(url, headers=_headers(token), params=params, timeout=(10, 30))
        r.raise_for_status()
        return r.json()
    return _with_retries(do, should_cancel=should_cancel)


def repo_file_bytes(repo, path, token, should_cancel=None):
    """Сырой файл из репозитория (contents API, работает и для приватных).
    С ретраями: нестабильная сеть/DPI рвут TLS — раньше падало по таймауту."""
    def do():
        r = requests.get(f'{API}/repos/{repo}/contents/{path}',
                         headers=_headers(token, 'application/vnd.github.raw'),
                         timeout=(10, 60))
        r.raise_for_status()
        return r.content
    return _with_retries(do, should_cancel=should_cancel)


def list_releases(repo, token):
    return gh_json(f'{API}/repos/{repo}/releases', token, {'per_page': 100})


def latest_release_with_prefix(repo, token, prefix):
    cand = [r for r in list_releases(repo, token)
            if r['tag_name'].startswith(prefix)]
    cand.sort(key=lambda r: r.get('created_at', ''), reverse=True)
    return cand[0] if cand else None


def release_by_tag(repo, tag, token):
    return gh_json(f'{API}/repos/{repo}/releases/tags/{tag}', token)


def _zip_asset(release):
    for a in release.get('assets', []):
        if a['name'].lower().endswith('.zip'):
            return a
    return None


def _stream_download(url, headers, dest, progress_cb=None, should_cancel=None, byte_cb=None):
    """Потоковое скачивание с ретраями и кооперативной отменой.
    byte_cb(delta) — инкремент скачанных байт (для агрегатного счётчика при параллели)."""
    def do():
        with _SESSION.get(url, headers=headers, stream=True, timeout=(10, 120)) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            done = 0
            with open(dest, 'wb') as f:
                for c in r.iter_content(1 << 20):
                    _check_cancel(should_cancel)
                    f.write(c)
                    done += len(c)
                    if byte_cb:
                        byte_cb(len(c))
                    if progress_cb and total:
                        progress_cb(done, total)
        return dest
    return _with_retries(do, should_cancel=should_cancel)


def download_asset(asset, token, dest, progress_cb=None, should_cancel=None, byte_cb=None):
    """Скачать ассет релиза по api-url (Accept: octet-stream). Работает для приватных."""
    return _stream_download(asset['url'], _headers(token, 'application/octet-stream'),
                            dest, progress_cb, should_cancel, byte_cb)


def download_url(url, token, dest, progress_cb=None, should_cancel=None, byte_cb=None):
    """Скачать произвольный URL (для generic zip — browser_download_url)."""
    return _stream_download(url, _headers(token, '*/*'), dest, progress_cb, should_cancel, byte_cb)


def _parallel_fetch_extract(need, mods_dir, tmp, resolver, log,
                            should_cancel=None, part_cb=None,
                            workers=None, byte_cb=None, sha_sink=None):
    """Скачать нужные части ПАРАЛЛЕЛЬНО и извлечь файлы во все целевые пути.

    need: {chunk_name: {sha256: [(relpath, kind), ...]}}.
    resolver(chunk, cpath, should_cancel, byte_cb): скачать часть chunk в файл cpath.
    Параллелится только СКАЧИВАНИЕ (узкое место — сеть); распаковка/запись идут в
    главном потоке по мере готовности частей → на диске одновременно не больше
    ~workers скачанных частей. Кооперативная отмена и побайтовые ретраи сохранены.
    """
    mods_dir = Path(mods_dir); tmp = Path(tmp)
    total = len(need)
    if not total:
        return
    workers = max(1, min(workers or PARALLEL_DOWNLOADS, total))

    def fetch(chunk):
        _check_cancel(should_cancel)
        cpath = tmp / f'_chunk_{chunk}'
        resolver(chunk, cpath, should_cancel, byte_cb)   # ретраи внутри _stream_download
        return chunk, cpath

    done = 0
    ex = ThreadPoolExecutor(max_workers=workers)
    futs = {ex.submit(fetch, ch): ch for ch in need}
    try:
        for fut in as_completed(futs):
            _check_cancel(should_cancel)
            chunk, cpath = fut.result()
            shamap = need[chunk]
            with zipfile.ZipFile(cpath) as z:
                for sh, targets in shamap.items():
                    data = z.read(sh)
                    for relpath, _kind in targets:
                        where, rel = install_route(relpath)
                        if where is None:            # мусор инсталлятора — пропустить
                            continue
                        base = mods_dir if where == 'mods' else mods_dir.parent
                        target = base / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(data)
                        if sha_sink and where == 'mods':   # отпечаток для индекса
                            sha_sink(rel, target, data)
            cpath.unlink(missing_ok=True)
            done += 1
            if part_cb:
                part_cb(done, total)
            if LOG_VERBOSE:
                log(f'Часть {done}/{total} готова ({len(shamap)} файлов)')
    except BaseException:
        for f in futs:                               # отменить ещё не начатые
            f.cancel()
        raise
    finally:
        ex.shutdown(wait=True)                        # дождаться/завершить потоки
        for ch in need:                               # подчистить временные части
            (tmp / f'_chunk_{ch}').unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Пути установки
# ---------------------------------------------------------------------------

# Куда раскладывать файл при установке. Источники (Inno-распаковка агрегатором) дают
# пути трёх видов: 1) контент Mods (`.../Mods/<Кат>/<Мод>/...`); 2) корневые файлы игры
# (`{app}/matrix/...`, `Rangers.exe`, `CFG/`, `DATA/` — у базы/фиксов `{app}`=корень игры);
# 3) мусор инсталлятора (readme, .iss, .exe-установщик, Inno-плейсхолдеры `{userdocs}`).
# А ещё бывают staging-обёртки `<X>_unpacked/...` (вложенный инсталлятор) — их срезаем.
# Имена корня игры — небольшой фиксированный набор; всё прочее в папке мода — это мод.
ROOT_DIRS = {'cfg', 'data', 'matrix', 'soundtrack', 'help', 'man', 'build', 'dist'}
ROOT_FILES = {
    'rangers.exe', 'cassandra.exe', 'manualrus.exe', 'matrixgame.dll',
    'build version.txt', 'cachedata.txt', 'cfg.txt', 'cfg.dat', 'lang.txt',
    'main.txt', 'changelog_rus.txt', 'generatemergedcfg.bat',
    'install.txt', 'install_russian.txt', 'readme_ru_ru.txt',
}
ROOT_EXTS = {'.dll'}        # libogg-0.dll, zlib.dll, steam_api.dll … — в корень игры


def install_route(relpath):
    """Решить назначение файла: ('mods', rel) — в папку Mods; ('root', rel) — в корень
    игры; (None, None) — пропустить (мусор инсталлятора). rel — путь относительно
    выбранного корня. См. комментарий выше про виды путей."""
    parts = [p for p in relpath.replace('\\', '/').split('/') if p]
    # 0) staging агрегатора (.temp/.tmp и пр. скрытые сегменты) — не контент мода
    if any(p.startswith('.') for p in parts):
        return (None, None)
    # 1) срезать staging-обёртки '<X>_unpacked/' (вложенные инсталляторы)
    while parts and parts[0].lower().endswith('_unpacked'):
        parts = parts[1:]
    if not parts:
        return (None, None)
    # 1b) сам файл-инсталлятор/скрипт инсталлятора (.exe не из набора игры, .iss) —
    # это не контент мода, где бы ни лежал → пропустить
    base = parts[-1].lower()
    ext = os.path.splitext(base)[1]
    if ext == '.iss' or (ext == '.exe' and base not in ROOT_FILES):
        return (None, None)
    # 2) контент Mods/ — всё после последнего сегмента 'Mods'
    low = [p.lower() for p in parts]
    if 'mods' in low:
        i = max(j for j, p in enumerate(low) if p == 'mods')
        rest = parts[i + 1:]
        return ('mods', '/'.join(rest)) if rest else (None, None)
    # 3) срезать ведущий Inno '{app}' (= игровая папка), затем перепроверить Mods/
    if parts and parts[0] == '{app}':
        parts = parts[1:]
        low = [p.lower() for p in parts]
        if not parts:
            return (None, None)
        if 'mods' in low:
            i = max(j for j, p in enumerate(low) if p == 'mods')
            rest = parts[i + 1:]
            return ('mods', '/'.join(rest)) if rest else (None, None)
    top = parts[0]
    topl = top.lower()
    # 4) прочие Inno-плейсхолдеры ({userdocs},{commondocs},…) — не в игру
    if topl.startswith('{') and topl.endswith('}'):
        return (None, None)
    # 5) корневые папки/файлы игры
    if topl in ROOT_DIRS:
        return ('root', '/'.join(parts))
    if len(parts) == 1:                      # одиночный файл на верхнем уровне
        ext = os.path.splitext(topl)[1]
        if topl in ROOT_FILES or ext in ROOT_EXTS:
            return ('root', top)
        return (None, None)                  # readme/changelog/.iss/.exe-установщик
    # 6) всё прочее (папка категории/мода) — это мод
    return ('mods', '/'.join(parts))


def install_target(relpath, mods_dir):
    """Path назначения файла на диске или None (пропустить). mods_dir = <игра>/Mods;
    корневые файлы кладутся в mods_dir.parent (= папка игры)."""
    where, rel = install_route(relpath)
    if where is None:
        return None
    base = Path(mods_dir) if where == 'mods' else Path(mods_dir).parent
    return base / rel


def install_relpath(relpath):
    """Путь относительно Mods/ (для модовых файлов — корректный; используется в
    отображении и 3-way merge, который работает только с модами)."""
    where, rel = install_route(relpath)
    return rel if rel is not None else relpath.replace('\\', '/')


# ---------------------------------------------------------------------------
# Generic zip
# ---------------------------------------------------------------------------

def resolve_zip(url, token):
    """По ссылке на GitHub Releases вернуть (download_url, published_at, asset_updated_at)."""
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+)/releases(?:/tag/([^/]+))?', url)
    if not m:
        return {'download_url': url, 'updated': None}
    owner, repo, tag = m.groups()
    slug = f'{owner}/{repo}'
    rel = release_by_tag(slug, tag, token) if tag else \
        gh_json(f'{API}/repos/{slug}/releases/latest', token)
    a = _zip_asset(rel)
    if a:
        return {'download_url': a['browser_download_url'], 'asset': a,
                'updated': a.get('updated_at') or rel.get('published_at')}
    return {'download_url': rel.get('zipball_url'),
            'updated': rel.get('published_at')}


def install_zip(url, mods_dir, token, progress_cb=None, log=print, tmp_dir=None,
                should_cancel=None):
    info = resolve_zip(url, token)
    tmp = Path(tmp_dir or mods_dir).parent / '_dl.zip'
    log('Скачивание zip...')
    if info.get('asset'):
        download_asset(info['asset'], token, tmp, progress_cb, should_cancel)
    else:
        download_url(info['download_url'], token, tmp, progress_cb, should_cancel)
    log('Распаковка...')
    _extract_zip_to(tmp, Path(mods_dir))
    tmp.unlink(missing_ok=True)
    return info.get('updated')


def _extract_zip_to(zip_path, mods_dir):
    """Распаковать zip в mods_dir с CP866-фиксом имён и нормализацией по 'Mods/'."""
    mods_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if not (info.flag_bits & 0x800):
                try:
                    name = name.encode('cp437').decode('cp866')
                except Exception:
                    pass
            target = install_target(name, mods_dir)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, 'wb') as out:
                shutil.copyfileobj(src, out)


# ---------------------------------------------------------------------------
# Aggregator unit (сборка по рецепту) — код И ассеты раздаются с HF
# по content-addressed чанкам (asset_index). Поддержка установки одного мода.
# ---------------------------------------------------------------------------

def mod_key(relpath):
    """Идентичность мода по пути: каталог мода после последнего 'Mods'.
    'Mods/<Кат>/<Имя>/DATA|CFG/...' -> 'Кат/Имя'; файл прямо в корне мода
    ('Mods/<Кат>/<Имя>/ModuleInfo.txt') -> тоже 'Кат/Имя' (имя файла отбрасывается);
    вне Mods/ -> '_base'."""
    parts = relpath.replace('\\', '/').split('/')
    idxs = [i for i, p in enumerate(parts) if p.lower() == 'mods']
    if not idxs:
        return '_base'
    key = []
    for seg in parts[idxs[-1] + 1:-1]:        # без последнего сегмента (имя файла)
        if seg.lower() in ('data', 'cfg'):
            break
        key.append(seg)
    return '/'.join(key) if key else '_base'


def _load_manifest(repo, path, token):
    """Манифест {files:{relpath:{sha256,size}}} из репо; {} если файла нет."""
    try:
        return json.loads(repo_file_bytes(repo, path, token)).get('files', {})
    except requests.HTTPError:
        return {}


def list_unit_mods(repo, camp, unit, token):
    """Список mod_key, доступных в юните (по code+asset манифестам), для выбора в GUI.
    '_base' (общие файлы игры) идёт первым, если есть."""
    mods = set()
    for kind in ('code', 'assets'):
        man = _load_manifest(repo, f'mods/{camp}/{unit}/{kind}.manifest.json', token)
        for relpath in man:
            mods.add(mod_key(relpath))
    base = ['_base'] if '_base' in mods else []
    return base + sorted(m for m in mods if m != '_base')


def reconstruct_unit(repo, camp, unit, mods_dir, token, progress_cb=None,
                     log=print, tmp_dir=None, dry_run=False, mod=None, should_cancel=None,
                     part_cb=None, byte_cb=None, sha_sink=None, skip_present=False):
    """Собрать юнит (или один мод mod=mod_key) из HF: код И ассеты берутся из
    content-addressed чанков asset_index по code.manifest + assets.manifest.
    mod=None -> весь юнит; mod='Кат/Имя' или '_base' -> только этот мод.
    dry_run: посчитать и проверить без скачивания/записи. Возвращает stats.
    skip_present: «починка» — файлы, которые УЖЕ есть на диске с верным sha,
    не скачиваются (части без единого недостающего блоба пропускаются целиком).
    Это сверяет хеши на диске (без сети) и качает только отличия/недостающее."""
    mods_dir = Path(mods_dir)
    tmp = Path(tmp_dir or mods_dir.parent)
    tmp.mkdir(parents=True, exist_ok=True)
    stats = {'code_files': 0, 'asset_files': 0, 'chunks': [], 'missing': 0,
             'mod': mod, 'updated': None, 'skipped': 0}

    index = json.loads(repo_file_bytes(repo, 'state/asset_index.json', token))
    code_man = _load_manifest(repo, f'mods/{camp}/{unit}/code.manifest.json', token)
    asset_man = _load_manifest(repo, f'mods/{camp}/{unit}/assets.manifest.json', token)
    if not code_man and not asset_man:
        raise RuntimeError(f'нет манифестов для {camp}/{unit} в {repo}')

    if skip_present:
        log('Проверяю, что уже на диске (сверка хешей, без скачивания)…')

    def _on_disk_ok(relpath, sh):
        """Файл уже лежит на диске по своему маршруту с верным sha?"""
        where, rel = install_route(relpath)
        if where is None:
            return False
        tgt = (mods_dir if where == 'mods' else mods_dir.parent) / rel
        return tgt.is_file() and file_sha256(tgt) == sh

    # Сгруппировать нужные блобы по чанкам. Один sha может вести к нескольким
    # путям (дубли) и из обоих манифестов — храним список (relpath, kind).
    need = {}    # chunk -> {sha256: [(relpath, kind), ...]}
    for kind, man in (('code', code_man), ('asset', asset_man)):
        for relpath, meta in man.items():
            if mod is not None and mod_key(relpath) != mod:
                continue
            sh = meta['sha256']
            if skip_present:
                _check_cancel(should_cancel)
                if _on_disk_ok(relpath, sh):
                    stats['skipped'] += 1
                    continue
            b = index['blobs'].get(sh)
            if not b:
                stats['missing'] += 1
                continue
            need.setdefault(b['chunk'], {}).setdefault(sh, []).append((relpath, kind))

    if skip_present and stats['skipped']:
        log(f'Уже на диске и совпадает: {stats["skipped"]} файлов — скачивать не нужно.')

    stats['chunks'] = list(need.keys())
    for shamap in need.values():
        for targets in shamap.values():
            for _relpath, kind in targets:
                stats[f'{kind}_files'] += 1
    scope = f'мод {mod}' if mod else 'весь пак'
    log(f'{scope}: {stats["code_files"]} код + {stats["asset_files"]} ассетов '
        f'в {len(need)} частях'
        + (f', НЕ найдено файлов: {stats["missing"]}' if stats['missing'] else ''))
    if dry_run:
        return stats

    # Скачать нужные части ПАРАЛЛЕЛЬНО и извлечь файлы во все целевые пути.
    def resolve(chunk, cpath, sc, bcb):
        meta = index['chunks'].get(chunk, {})
        url = meta.get('url')
        if url:                                  # HF public / любой прямой URL
            ctoken = token if meta.get('store') == 'github' else None
            download_url(url, ctoken, cpath, None, sc, bcb)
        else:                                    # back-compat: GitHub release по тегу
            tag = meta.get('release_tag')
            crel = release_by_tag(repo, tag, token)
            casset = next((a for a in crel.get('assets', []) if a['name'] == chunk), None)
            if not casset:
                raise RuntimeError(f'часть {chunk}: нет ссылки/релиза')
            download_asset(casset, token, cpath, None, sc, bcb)

    _parallel_fetch_extract(need, mods_dir, tmp, resolve, log,
                            should_cancel=should_cancel, part_cb=part_cb, byte_cb=byte_cb,
                            sha_sink=sha_sink)
    log(f'Готово: {stats["code_files"]} код + {stats["asset_files"]} ассетов в {mods_dir}')
    return stats


def unit_remote_updated(repo, camp, token):
    """Дата последнего коммита репо (отражает обновление кода/ассетов/индекса).
    Код больше не в релизах — staleness считаем по последнему коммиту ветки."""
    try:
        commits = gh_json(f'{API}/repos/{repo}/commits', token, {'per_page': 1})
        if commits:
            return commits[0]['commit']['committer']['date']
    except requests.HTTPError:
        pass
    return None


# ---------------------------------------------------------------------------
# Фаза 1: моды-дескрипторы (мод = самоописываемый пакет по URL).
# Дескриптор: {id, source, version, name, depends[], conflicts[], chunk_index_url,
#              files:{code:{relpath:{sha256,size}}, assets:{...}}}.
# Каталог descriptors/catalog.json группирует id -> {variants:[...], default_source}.
# Источник дескриптора может быть: http(s)-URL (форк где угодно), путь в репо
# (descriptors/<camp>/<unit>/<id>.json), или локальный файл.
# ---------------------------------------------------------------------------

def _fetch_json(ref, repo=None, token=None):
    """JSON из http(s)-URL / локального файла / пути в репозитории."""
    if ref.startswith('http://') or ref.startswith('https://'):
        r = requests.get(ref, headers=_headers(token, '*/*'), timeout=30)
        r.raise_for_status()
        return r.json()
    p = Path(ref)
    if p.exists():
        return json.loads(p.read_text(encoding='utf-8'))
    if repo:
        return json.loads(repo_file_bytes(repo, ref, token))
    raise FileNotFoundError(ref)


def load_catalog(ref='descriptors/catalog.json', repo=None, token=None):
    """Каталог модов: {mod_id: {name, author, default_source, versions_differ, variants}}."""
    return _fetch_json(ref, repo, token).get('mods', {})


def load_descriptor(ref, repo=None, token=None):
    """Дескриптор одного мода-варианта."""
    return _fetch_json(ref, repo, token)


def _variant_path(catalog, mod_id, source=None):
    """Путь дескриптора для mod_id (конкретный source или дефолтный)."""
    ent = catalog.get(mod_id)
    if not ent:
        return None
    vs = ent['variants']
    if source:
        for v in vs:
            if v['source'] == source:
                return v['path']
    default = ent.get('default_source')
    for v in vs:
        if v['source'] == default:
            return v['path']
    return vs[0]['path'] if vs else None


def descriptor_for(sel, catalog=None, repo=None, token=None):
    """Загрузить ОДИН дескриптор по выбору: {'url':..} (форк) | {'id':..,'source':..}.
    Для id нужен catalog (берётся вариант source или дефолтный). -> descriptor | None."""
    if sel.get('url'):
        return load_descriptor(sel['url'], repo, token)
    path = _variant_path(catalog or {}, sel.get('id'), sel.get('source'))
    if not path:
        return None
    return load_descriptor(path, repo, token)


def pick_disk_variant(catalog, mod_id, mods_dir, repo=None, token=None, prefer_camp=None,
                      log=print, should_cancel=None):
    """Подобрать вариант мода из каталога, лучше всего совпадающий с тем, что лежит
    на диске (для дисковых модов без снимка). Совпадение = сколько файлов варианта
    физически есть в Mods (cover), затем сколько с тем же sha (match). prefer_camp —
    предпочесть варианты этого лагеря при равенстве. -> (descriptor, info) | (None, None).
    info['reason'] при неудаче: 'not_in_catalog' | 'load_failed'."""
    catalog = catalog or {}
    ent = catalog.get(mod_id)
    if not ent:                                  # fallback: матч по короткому имени
        leaf = mod_id.split('/')[-1]
        cands = [k for k in catalog if k.split('/')[-1] == leaf]
        if len(cands) == 1:
            ent = catalog[cands[0]]
        elif not cands:
            return None, {'reason': 'not_in_catalog'}
        else:                                    # неоднозначно — берём с тем же лагерем
            ent = catalog[cands[0]]
    mods_dir = Path(mods_dir)
    best, best_desc, best_info = None, None, None
    load_errors = 0
    for v in ent['variants']:
        _check_cancel(should_cancel)
        try:
            desc = load_descriptor(v['path'], repo, token)
        except OperationCancelled:
            raise
        except Exception as e:
            load_errors += 1
            log(f'[warn] вариант {v["source"]}: не удалось загрузить дескриптор ({e})')
            continue
        flat = desc_files_flat(desc)
        cover = match = 0
        for rp, meta in flat.items():
            fp = mods_dir / install_relpath(rp)
            if fp.exists() and fp.is_file():
                cover += 1
                if file_sha256(fp) == meta['sha256']:
                    match += 1
        same_camp = bool(prefer_camp and v['source'].startswith(prefer_camp))
        score = (cover, match, same_camp)
        if best is None or score > best:
            best = score
            best_desc = desc
            best_info = {'source': v['source'], 'cover': cover, 'match': match,
                         'total': len(flat), 'same_camp': same_camp, 'reason': 'ok'}
    if best_desc is None:
        return None, {'reason': 'load_failed' if load_errors else 'not_in_catalog'}
    return best_desc, best_info


def _build_leaf_index(catalog):
    """Индекс короткое-имя(лист id) -> [полные id]. depends/conflicts в ModuleInfo
    ссылаются по короткому имени мода, а id каталога = 'Категория/Имя'."""
    idx = {}
    for mid in catalog:
        idx.setdefault(mid.split('/')[-1], []).append(mid)
    return idx


def _resolve_ref_ids(catalog, leaf_index, ref):
    """ref (полный id или короткое имя) -> список полных id из каталога."""
    if ref in catalog:
        return [ref]
    return list(leaf_index.get(ref, []))


def resolve_set(selections, catalog, repo=None, token=None):
    """Разрешить НАБОР модов: подтянуть зависимости (замыкание по depends через
    дефолтные варианты каталога) и найти конфликты (ТОЛЬКО показать — не снимаем).
    depends/conflicts в ModuleInfo ссылаются по короткому имени -> матчим через
    индекс лист-имя -> id.
    selections: список выбора, каждый — либо mod_id (str), либо dict
      {'id':..., 'source':...} (конкретный вариант), либо {'url':...} (форк по URL).
    Возвращает план: {'mods': {id: descriptor}, 'order': [...], 'requested': set,
      'added_deps': [...], 'missing_deps': [...], 'conflicts': [(a,b), ...]}."""
    leaf_index = _build_leaf_index(catalog)
    mods = {}            # id -> descriptor
    requested = set()
    added_deps = []
    missing_deps = []
    queue = [(sel if isinstance(sel, dict) else {'id': sel}, False) for sel in selections]

    while queue:
        sel, is_dep = queue.pop(0)
        if sel.get('url'):
            desc = load_descriptor(sel['url'], repo, token)
        else:
            ref = sel['id']
            ids = _resolve_ref_ids(catalog, leaf_index, ref)
            if not ids:
                missing_deps.append(ref)
                continue
            mid0 = ids[0]                      # при неоднозначности берём первый
            if mid0 in mods:
                if not is_dep:
                    requested.add(mid0)
                continue
            desc = load_descriptor(_variant_path(catalog, mid0, sel.get('source')),
                                   repo, token)
        mid = desc['id']
        if not is_dep:
            requested.add(mid)
        if mid in mods:
            continue
        mods[mid] = desc
        if is_dep:
            added_deps.append(mid)
        for dep in desc.get('depends', []):
            queue.append(({'id': dep}, True))

    # конфликты внутри набора (по короткому имени, двунаправленно)
    present_leaf = {}     # лист-имя -> id (для обратного мэппинга)
    for mid in mods:
        present_leaf[mid.split('/')[-1]] = mid
    conflicts = []
    seen_pairs = set()
    for mid, desc in mods.items():
        for c in desc.get('conflicts', []):
            other = present_leaf.get(c) or (c if c in mods else None)
            if other and other != mid:
                pair = tuple(sorted((mid, other)))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    conflicts.append(pair)

    order = sorted(mods)
    return {'mods': mods, 'order': order, 'requested': requested,
            'added_deps': sorted(set(added_deps) - requested),
            'missing_deps': sorted(set(missing_deps)), 'conflicts': conflicts}


def load_packs(ref='state/packs.json', repo=None, token=None):
    """Загрузить packs.json (тиры юнитов: base/fix/mod/assets + fix_parent)."""
    try:
        return (_fetch_json(ref, repo, token) or {}).get('packs', {})
    except Exception:
        return {}


def camp_packs(packs):
    """{лагерь: [{'key':'camp/unit','unit':..,'name':..,'tier':..}]} для выпадающих списков.
    Сортировка по load_order.

    Лагерь 'shared' — НЕ отдельная база (люди путались, что ставить): его юниты
    подмешиваются в каждый реальный лагерь. Реальный key/camp ('shared/...')
    сохраняется, чтобы установка тянула манифесты из mods/shared/<unit>/; флаг
    'shared': True помечает такие записи для UI."""
    out = {}
    shared = []
    for key, p in packs.items():
        entry = {
            'key': key, 'camp': p['camp'], 'unit': p['name'], 'tier': p['tier'],
            'name': p.get('display_name', p['name']),
            'load_order': p.get('load_order', 999),
            'shared': p['camp'] == 'shared',
        }
        (shared if entry['shared'] else out.setdefault(p['camp'], [])).append(entry)
    for camp in out:
        out[camp].extend(shared)                 # shared доступен в каждой базе
        out[camp].sort(key=lambda x: (x['load_order'], x['unit']))
    return out


BASE_MARKERS = [
    # (базовый юнит, лагерь, маркеры — папки/моды уникальные для этой базы в Mods/)
    ('redux_base_installer', 'redux', ['Den', 'Solyanka', 'FairansVision', 'AnotherMods']),
    ('universe_community', 'universe',
     ['Tweaks/CheatsOff', 'Evolution/EvoFreeInflation', 'PlanetaryBattles/PBHelpFromAbove']),
    ('original_installer', 'original',
     ['Tweaks/SR2Balance', 'Revolution/RevBomber', 'Tweaks/UIRecolor_ShuKlissan']),
]


def detect_installed_base(mods_dir):
    """Определить установленную базу по уникальным маркерам в папке Mods.
    Возвращает {'base': юнит, 'camp': лагерь} или None. Порядок маркеров важен
    (redux самый отличимый — проверяется первым)."""
    import os
    root = Path(mods_dir)
    if not root.is_dir():
        return None
    for base, camp, markers in BASE_MARKERS:
        for mk in markers:
            if (root / mk.replace('/', os.sep)).exists():
                return {'base': base, 'camp': camp}
    return None


def read_module_info(modinfo_path):
    """ModuleInfo.txt -> dict 'Ключ'->'Значение' (cp1251 / UTF-16 BOM / UTF-8).
    Повторяющиеся ключи склеиваются переводом строки. {} при ошибке."""
    out = {}
    try:
        b = open(modinfo_path, 'rb').read()
        if b[:2] in (b'\xff\xfe', b'\xfe\xff'):
            txt = b.decode('utf-16', 'replace')
        else:
            try:
                txt = b.decode('utf-8')
            except UnicodeDecodeError:
                txt = b.decode('cp1251', 'replace')
        for line in txt.splitlines():
            if '=' not in line:
                continue
            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip()
            if k:
                out[k] = (out[k] + '\n' + v) if k in out else v
    except Exception:
        pass
    return out


def read_module_section(modinfo_path):
    """Поле Section из ModuleInfo.txt. '' если нет."""
    return read_module_info(modinfo_path).get('Section', '')


def _strip_color(s):
    """Убрать игровую разметку <color=...>...</color> для показа текста."""
    return re.sub(r'</?color[^>]*>', '', s or '').strip()


def module_card(modinfo_path):
    """Карточка мода из ModuleInfo.txt для показа в лаунчере (как в самой игре):
    краткое/полное описание, авторы, требования, конфликты, раздел. Русские поля
    с фолбэком на английские; цветовая разметка <color> убирается. {} если файла нет."""
    mi = read_module_info(modinfo_path)
    if not mi:
        return {}

    def g(*keys):
        for k in keys:
            v = mi.get(k)
            if v:
                return _strip_color(v)
        return ''

    return {
        'name': g('Name'),
        'authors': g('Author', 'Autor'),
        'small': g('SmallDescription', 'SmallDescriptionEng'),
        'full': g('FullDescription', 'FullDescriptionEng'),
        'requires': _split_modlist(mi.get('Dependence', '')),
        'conflicts': _split_modlist(mi.get('Conflict', '')),
        'section': g('Section', 'SectionEng'),
        'priority': g('Priority'),
    }


def _split_modlist(val):
    """Conflict/Dependence -> список имён модов (разделители , ; пробел)."""
    if not val:
        return []
    return [x for x in re.split(r'[,;\s]+', val.strip()) if x]


def check_disk_dependencies(mods_dir, progress_cb=None, should_cancel=None):
    """Проверить зависимости (поле Dependence в ModuleInfo) установленных модов.
    Возвращает список {'mod': id, 'name': имя, 'missing': [неуст. зависимости]} —
    только для модов, у которых есть НЕустановленные зависимости. Сопоставление по
    короткому имени (Dependence ссылается по имени мода, не по 'Кат/Имя')."""
    mods_dir = Path(mods_dir)
    mids = scan_installed_mods(mods_dir)
    present = set(mids) | {m.split('/')[-1] for m in mids}
    out = []
    n = max(1, len(mids))
    for i, mid in enumerate(mids):
        _check_cancel(should_cancel)
        mi = read_module_info(mods_dir / mid.replace('/', os.sep) / 'ModuleInfo.txt')
        deps = _split_modlist(mi.get('Dependence', ''))
        missing = [d for d in deps if d not in present and d.split('/')[-1] not in present]
        if missing:
            out.append({'mod': mid, 'name': mi.get('Name', '') or mid.split('/')[-1],
                        'missing': missing})
        if progress_cb:
            progress_cb(i + 1, n)
    return out


def scan_installed_mods(mods_dir):
    """Найти физически установленные моды в папке Mods игры — каталоги с ModuleInfo.txt.
    Возвращает список mod_id (путь после последнего 'Mods/' до папки мода, напр.
    'OtherMods/DrKlesMod'). Без сети — читает только файловую систему."""
    import os
    root = Path(mods_dir)
    if not root.is_dir():
        return []
    found = set()
    for dirpath, _dirs, files in os.walk(root):
        if any(f.lower() == 'moduleinfo.txt' for f in files):
            rel = os.path.relpath(dirpath, root).replace(os.sep, '/')
            if rel and rel != '.':
                found.add(rel)
    return sorted(found)


def read_modcfg(mods_dir):
    """Моды, ПОДКЛЮЧЁННЫЕ в игре — поле CurrentMod из Mods/ModCFG.txt.
    Возвращает список mod_id с '/' (как scan_installed_mods), порядок сохраняется.
    [] если файла нет. Без сети — только файловая система."""
    p = Path(mods_dir) / 'ModCFG.txt'
    if not p.is_file():
        return []
    raw = read_module_info(p).get('CurrentMod', '')
    out, seen = [], set()
    for part in raw.replace('\n', ',').split(','):
        mid = part.strip().replace('\\', '/').strip('/')
        if mid and mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


def write_modcfg(mods_dir, mod_ids):
    """Записать ПОДКЛЮЧЁННЫЕ моды в Mods/ModCFG.txt (CurrentMod=Кат\\Мод, ...).
    Прочие строки и кодировка существующего файла сохраняются; '/' -> '\\'.
    Возвращает Path к файлу."""
    p = Path(mods_dir) / 'ModCFG.txt'
    line = 'CurrentMod=' + ', '.join(m.replace('/', '\\') for m in mod_ids)
    enc, lines, replaced = 'utf-8', [], False
    if p.is_file():
        b = open(p, 'rb').read()
        if b[:2] in (b'\xff\xfe', b'\xfe\xff'):
            enc, txt = 'utf-16', b.decode('utf-16', 'replace')
        else:
            try:
                txt = b.decode('utf-8')
            except UnicodeDecodeError:
                enc, txt = 'cp1251', b.decode('cp1251', 'replace')
        for ln in txt.splitlines():
            if ln.split('=', 1)[0].strip().lower() == 'currentmod':
                if not replaced:
                    lines.append(line)
                    replaced = True
            else:
                lines.append(ln)
    if not replaced:
        lines.append(line)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, 'w', encoding=enc, newline='') as f:
        f.write('\r\n'.join(lines) + '\r\n')
    return p


def check_pack_compatibility(selected_units, packs, installed_base=None):
    """Структурная совместимость НАБОРА на уровне паков (Фаза 2). Только показывает.
    selected_units: список ключей 'camp/unit'. packs: из load_packs.
    installed_base: имя базового юнита текущей установки (для предупреждения о сейвах).
    Возвращает: bases (выбранные базы), fixes, fix_orphans [(fix, нужный_родитель)],
      base_conflict (>1 базы), missing_base, save_warning, mandatory (base/fix к обновлению)."""
    present_names = {packs[u]['name'] for u in selected_units if u in packs}
    bases, fixes, fix_orphans, mandatory = [], [], [], []
    has_playable = False           # есть ли в наборе моды/фиксы (им нужна база)
    for u in selected_units:
        p = packs.get(u)
        if not p:
            continue
        tier = p['tier']
        if tier == 'base':
            bases.append(u)
        elif tier == 'fix':
            fixes.append(u)
            has_playable = True
            parent = p.get('fix_parent')
            if parent and parent not in present_names:
                fix_orphans.append((u, parent))
        elif tier == 'mod':
            has_playable = True
        if p.get('update_required'):
            mandatory.append(u)

    base_names = [packs[b]['name'] for b in bases]
    save_warning = None
    if installed_base and base_names and installed_base not in base_names:
        save_warning = (f'Смена базового пака ({installed_base} → {base_names[0]}): '
                        f'сейвы текущей партии НЕСОВМЕСТИМЫ. Новую партию начать можно.')
    return {
        'bases': bases, 'fixes': fixes, 'fix_orphans': fix_orphans,
        'base_conflict': len(bases) > 1,
        # «нет базы» — только если её нет ни в наборе, ни уже установленной на диске
        'missing_base': len(bases) == 0 and not installed_base and has_playable,
        'installed_base': installed_base,
        'save_warning': save_warning,
        'mandatory': mandatory,
    }


def load_chunk_index(desc=None, url=None, repo=None, token=None):
    """Загрузить индекс чанков (blobs/chunks). Источник: chunk_index_url из дескриптора
    (HF public) с фолбэком на state/asset_index.json в репозитории."""
    src = url or (desc or {}).get('chunk_index_url')
    last_err = None
    if src:
        try:
            return _fetch_json(src, repo, token)
        except Exception as e:
            last_err = e
    if repo:
        return json.loads(repo_file_bytes(repo, 'state/asset_index.json', token))
    if last_err:
        raise last_err
    raise ValueError('load_chunk_index: не указан ни url/chunk_index_url, ни repo')


def install_descriptor(desc, mods_dir, index, token=None, progress_cb=None,
                       log=print, tmp_dir=None, dry_run=False, snap_dir=None,
                       should_cancel=None, part_cb=None, byte_cb=None, sha_sink=None):
    """Установить мод из дескриптора: блобы (code+assets) резолвятся по sha через index
    -> чанки -> извлечение -> запись в mods_dir/install_relpath(relpath)."""
    mods_dir = Path(mods_dir)
    tmp = Path(tmp_dir or mods_dir.parent)
    tmp.mkdir(parents=True, exist_ok=True)
    files = desc.get('files', {})
    stats = {'id': desc.get('id'), 'code_files': 0, 'asset_files': 0,
             'chunks': [], 'missing': 0}
    need = {}    # chunk -> {sha256: [(relpath, kind)]}
    for kind in ('code', 'assets'):
        for relpath, meta in files.get(kind, {}).items():
            sh = meta['sha256']
            b = index['blobs'].get(sh)
            if not b:
                stats['missing'] += 1
                continue
            need.setdefault(b['chunk'], {}).setdefault(sh, []).append((relpath, kind))
    stats['chunks'] = list(need.keys())
    for shamap in need.values():
        for targets in shamap.values():
            for _rel, kind in targets:
                stats[('code_files' if kind == 'code' else 'asset_files')] += 1
    log(f'{desc.get("id")}: {stats["code_files"]} код + {stats["asset_files"]} ассетов '
        f'в {len(need)} частях'
        + (f', НЕ найдено файлов: {stats["missing"]}' if stats['missing'] else ''))
    if dry_run:
        return stats
    def resolve(chunk, cpath, sc, bcb):
        meta = index['chunks'].get(chunk, {})
        ctoken = token if meta.get('store') == 'github' else None
        download_url(meta.get('url'), ctoken, cpath, None, sc, bcb)

    _parallel_fetch_extract(need, mods_dir, tmp, resolve, log,
                            should_cancel=should_cancel, part_cb=part_cb, byte_cb=byte_cb,
                            sha_sink=sha_sink)
    # снимок установки -> база для будущего 3-way merge при обновлении (Фаза 4)
    save_snapshot_from_desc(mods_dir, desc, snap_dir)
    log(f'Готово: {desc.get("id")} -> {mods_dir}')
    return stats


def install_set(plan, mods_dir, index, token=None, log=print, tmp_dir=None, dry_run=False,
                should_cancel=None, part_cb=None, byte_cb=None, sha_sink=None):
    """Установить весь разрешённый набор (resolve_set -> план). Ставит все mods плана."""
    results = {}
    for mid in plan['order']:
        _check_cancel(should_cancel)
        results[mid] = install_descriptor(plan['mods'][mid], mods_dir, index,
                                          token=token, log=log, tmp_dir=tmp_dir,
                                          dry_run=dry_run, should_cancel=should_cancel,
                                          part_cb=part_cb, byte_cb=byte_cb, sha_sink=sha_sink)
    return results


# ---------------------------------------------------------------------------
# Фаза 4: сохранение правок игрока при обновлении мода (3-way merge).
#
# База 3-way НЕ хранится байтами: снимок установки = манифест (relpath->sha256)
# + version. Содержимое base-файлов при необходимости переподтягивается по sha
# из тех же content-addressed чанков (как при установке).
#   base   = снимок прошлой установки (то, что лаунчер положил)
#   theirs = новая версия мода (манифест нового дескриптора)
#   mine   = то, что сейчас на диске (правки игрока)
# Текст мёржим diff3 (чистый Python, без git): непересекающиеся изменения
# сливаются авто; пересекающиеся -> конфликт (решает игрок). Бинарь не мёржим.
#
# Снимок лежит ВНЕ папки Mods (её могут очистить целиком) — в домашнем каталоге
# пользователя, с привязкой к конкретной инсталляции по пути Mods.
# После применения обновления снимок := манифест новой версии (то, что отгрузил
# мод) — он и есть общий предок для следующего 3-way; на диске при этом может
# остаться правка игрока (это `mine` в следующий раз).
# ---------------------------------------------------------------------------

TEXT_EXTS = {
    '.txt', '.rson', '.scr', '.cfg', '.ini', '.json', '.xml', '.lng',
    '.csv', '.lua', '.h', '.c', '.cpp', '.md', '.log', '.script', '.def',
    '.yml', '.yaml', '.html', '.htm', '.glsl', '.fx', '.shader',
}


def is_text_path(relpath):
    """Текстовый ли файл (по расширению). ModuleInfo.txt и скрипты -> текст."""
    return os.path.splitext(relpath)[1].lower() in TEXT_EXTS


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _safe_id(s):
    return re.sub(r'[^A-Za-z0-9._-]', '_', str(s))


def _snap_dir(mods_dir, snap_dir=None):
    """Каталог снимков для данной инсталляции. ВНЕ Mods (её могут очистить).
    По умолчанию ~/.sr-mods-launcher/snapshots/<хэш пути Mods>/."""
    if snap_dir is not None:
        return Path(snap_dir)
    key = hashlib.sha1(str(Path(mods_dir).resolve()).lower().encode('utf-8')).hexdigest()[:16]
    return Path.home() / '.sr-mods-launcher' / 'snapshots' / key


def snapshot_path(mods_dir, mod_id, snap_dir=None):
    return _snap_dir(mods_dir, snap_dir) / (_safe_id(mod_id) + '.json')


def desc_files_flat(desc):
    """Плоский манифест дескриптора: {relpath: {'sha256':.., 'kind':..}}."""
    out = {}
    files = desc.get('files', {})
    for kind in ('code', 'assets'):
        for relpath, meta in files.get(kind, {}).items():
            out[relpath] = {'sha256': meta['sha256'], 'kind': kind}
    return out


def save_install_snapshot(mods_dir, mod_id, version, files, source=None, snap_dir=None):
    """Сохранить снимок установки (relpath->sha256) для будущего 3-way."""
    p = snapshot_path(mods_dir, mod_id, snap_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {'id': mod_id, 'source': source, 'version': version, 'files': files},
        ensure_ascii=False, indent=2), encoding='utf-8')
    return p


def load_install_snapshot(mods_dir, mod_id, snap_dir=None):
    p = snapshot_path(mods_dir, mod_id, snap_dir)
    if p.exists():
        return json.loads(p.read_text(encoding='utf-8'))
    return None


def save_snapshot_from_desc(mods_dir, desc, snap_dir=None):
    """Снимок из дескриптора: {relpath: sha256}. Вызывается после установки/обновления."""
    flat = {rp: m['sha256'] for rp, m in desc_files_flat(desc).items()}
    return save_install_snapshot(mods_dir, desc.get('id'), desc.get('version'),
                                 flat, desc.get('source'), snap_dir)


def fetch_blobs(index, shas, token, tmp, log=print, progress_cb=None, should_cancel=None,
                byte_cb=None):
    """Скачать блобы по sha256 из content-addressed чанков. -> {sha: bytes}.
    Чанк скачивается один раз, из него извлекаются все нужные блобы."""
    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    need = {}    # chunk -> set(sha)
    for sh in set(shas):
        b = index['blobs'].get(sh)
        if b:
            need.setdefault(b['chunk'], set()).add(sh)
    out = {}
    chunks = [c for c in need if index['chunks'].get(c, {}).get('url')]
    for c in need:                                # части без ссылки — пропустить с логом
        if c not in chunks:
            log(f'[!] для части пропущено {len(need[c])} файлов (нет ссылки)')
    if not chunks:
        return out

    def fetch(chunk):
        _check_cancel(should_cancel)
        meta = index['chunks'].get(chunk, {})
        cpath = tmp / f'_chunk_{chunk}'
        ctoken = token if meta.get('store') == 'github' else None
        download_url(meta['url'], ctoken, cpath, None, should_cancel, byte_cb)
        return chunk, cpath

    workers = max(1, min(PARALLEL_DOWNLOADS, len(chunks)))
    ex = ThreadPoolExecutor(max_workers=workers)
    futs = {ex.submit(fetch, c): c for c in chunks}
    try:
        for fut in as_completed(futs):
            _check_cancel(should_cancel)
            chunk, cpath = fut.result()
            with zipfile.ZipFile(cpath) as z:
                for sh in need[chunk]:
                    try:
                        out[sh] = z.read(sh)
                    except KeyError:
                        log(f'[!] файл {sh[:12]} не найден в части')
            cpath.unlink(missing_ok=True)
    except BaseException:
        for f in futs:
            f.cancel()
        raise
    finally:
        ex.shutdown(wait=True)
        for c in chunks:
            (tmp / f'_chunk_{c}').unlink(missing_ok=True)
    return out


# --- diff3 (3-way merge) на списках строк ----------------------------------

def _find_sync_regions(base, a, b):
    """Точки синхронизации: куски, совпадающие во всех трёх. Алгоритм bzr/merge3."""
    amatches = SequenceMatcher(None, base, a).get_matching_blocks()
    bmatches = SequenceMatcher(None, base, b).get_matching_blocks()
    ia = ib = 0
    sl = []
    while ia < len(amatches) and ib < len(bmatches):
        abase, amatch, alen = amatches[ia]
        bbase, bmatch, blen = bmatches[ib]
        i = max(abase, bbase)
        j = min(abase + alen, bbase + blen)
        if i < j:
            asub = amatch + (i - abase)
            bsub = bmatch + (i - bbase)
            sl.append((i, j, asub, asub + (j - i), bsub, bsub + (j - i)))
        if abase + alen <= bbase + blen:
            ia += 1
        else:
            ib += 1
    n = len(base)
    sl.append((n, n, len(a), len(a), len(b), len(b)))
    return sl


def merge3(base, a, b):
    """3-way merge списков строк. -> (merged_list, conflict: bool).
    conflict=True, если a и b правят одно и то же место (пересекающееся изменение).
    При conflict содержимое merged_list не используется (решение — по файлу целиком)."""
    out = []
    conflict = False
    iz = ia = ib = 0
    for zmatch, zend, amatch, aend, bmatch, bend in _find_sync_regions(base, a, b):
        a_reg = a[ia:amatch]
        b_reg = b[ib:bmatch]
        base_reg = base[iz:zmatch]
        if a_reg or b_reg or base_reg:
            if a_reg == b_reg:
                out += a_reg                 # оба сделали одинаковую правку
            elif a_reg == base_reg:
                out += b_reg                 # изменил только b (новая версия)
            elif b_reg == base_reg:
                out += a_reg                 # изменил только a (игрок)
            else:
                conflict = True              # оба правят -> конфликт
        if zend > zmatch:
            out += base[zmatch:zend]         # стабильный кусок
        iz, ia, ib = zend, aend, bend
    return out, conflict


def merge3_bytes(base_b, mine_b, theirs_b):
    """3-way merge байтовых файлов построчно (кодировка-агностично: cp1251/UTF-16
    переживают, т.к. строки — точные байтовые срезы оригиналов). -> (bytes, conflict)."""
    merged, conflict = merge3(base_b.splitlines(keepends=True),
                              mine_b.splitlines(keepends=True),
                              theirs_b.splitlines(keepends=True))
    return b''.join(merged), conflict


# --- планирование и применение обновления ----------------------------------

# Статусы действий: add/update/unchanged/player_only/automerge/deleted_clean —
# применяются автоматически; conflict_* — требуют решения игрока.
CONFLICT_STATUSES = ('conflict_text', 'conflict_binary', 'conflict_deleted')
DECISION_DEFAULTS = {
    'conflict_text': 'mine',      # оставить файл игрока (мод не сломается)
    'conflict_binary': 'mine',
    'conflict_deleted': 'keep',   # не удалять правленый игроком файл
}


def plan_update_merge(desc, mods_dir, index, token=None, log=print, snapshot=None,
                      snap_dir=None, tmp_dir=None, progress_cb=None, should_cancel=None):
    """Спланировать обновление мода с 3-way merge (НЕ пишет на диск).
    desc — новый дескриптор; snapshot — снимок прошлой установки (или авто-загрузка).
    Возвращает план: {id, version_old/new, has_snapshot, actions:[...], summary}."""
    mods_dir = Path(mods_dir)
    mod_id = desc.get('id')
    if snapshot is None:
        snapshot = load_install_snapshot(mods_dir, mod_id, snap_dir)
    base = (snapshot or {}).get('files', {})            # relpath -> sha256
    theirs = desc_files_flat(desc)                      # relpath -> {sha256,kind}
    tmp = Path(tmp_dir or mods_dir.parent)

    relpaths = set(base) | set(theirs)
    disk = {}
    for rp in relpaths:
        _check_cancel(should_cancel)
        fp = mods_dir / install_relpath(rp)
        if fp.exists() and fp.is_file():
            disk[rp] = file_sha256(fp)

    actions = []
    both_text = []   # relpaths, требующие diff3 (нужно скачать base+theirs)
    for rp in sorted(relpaths):
        bsha = base.get(rp)
        tinfo = theirs.get(rp)
        tsha = tinfo['sha256'] if tinfo else None
        msha = disk.get(rp)
        text = is_text_path(rp)
        rec = {'relpath': rp, 'kind': (tinfo or {}).get('kind', 'code'),
               'text': text, 'base': bsha, 'theirs': tsha, 'mine': msha}
        if tinfo is None:                       # был в прошлой версии, в новой нет
            if msha is None or msha == bsha:
                rec['status'] = 'deleted_clean'    # игрок не трогал -> удалить
            else:
                rec['status'] = 'conflict_deleted'  # игрок правил -> спросить
        elif msha is None:
            rec['status'] = 'add'               # нового файла ещё нет на диске
        elif msha == tsha:
            rec['status'] = 'unchanged'         # уже совпадает с новой версией
        elif bsha is None:                      # нет базы (дисковый мод без снимка):
            rec['status'] = 'conflict_text' if text else 'conflict_binary'
            # без базы 3-way невозможен -> всегда конфликт, diff3 не запускаем
        elif msha == bsha:
            rec['status'] = 'update'            # игрок не трогал, мод изменил
        elif tsha == bsha:
            rec['status'] = 'player_only'       # мод не менял, игрок правил -> оставить
        else:                                   # оба меняли
            if text:
                rec['status'] = 'conflict_text'  # уточним diff3 ниже
                both_text.append(rp)
            else:
                rec['status'] = 'conflict_binary'
        actions.append(rec)

    if both_text:
        recmap = {r['relpath']: r for r in actions}
        shas = set()
        for rp in both_text:
            r = recmap[rp]
            for s in (r['base'], r['theirs']):
                if s:
                    shas.add(s)
        blobs = fetch_blobs(index, shas, token, tmp, log, progress_cb, should_cancel)
        for rp in both_text:
            r = recmap[rp]
            fp = mods_dir / install_relpath(rp)
            mine_b = fp.read_bytes()
            base_b = blobs.get(r['base'], b'')
            theirs_b = blobs.get(r['theirs'], b'')
            merged, conflict = merge3_bytes(base_b, mine_b, theirs_b)
            if conflict:
                r['status'] = 'conflict_text'
            else:
                r['status'] = 'automerge'
                r['_merged'] = merged           # готовые байты слияния

    plan = {'id': mod_id, 'source': desc.get('source'),
            'version_old': (snapshot or {}).get('version'),
            'version_new': desc.get('version'),
            'has_snapshot': snapshot is not None,
            'actions': actions}
    plan['summary'] = summarize_plan(actions)
    return plan


def summarize_plan(actions):
    s = {}
    for r in actions:
        s[r['status']] = s.get(r['status'], 0) + 1
    s['conflicts'] = sum(s.get(k, 0) for k in CONFLICT_STATUSES)
    return s


def apply_update_plan(desc, plan, decisions, mods_dir, index, token=None, log=print,
                      snap_dir=None, tmp_dir=None, progress_cb=None, dry_run=False,
                      should_cancel=None):
    """Применить план обновления. decisions: {relpath: решение} для conflict_*:
      conflict_text/binary -> 'mine' | 'theirs' | 'both' (both: новая рядом как .srnew)
      conflict_deleted     -> 'keep' | 'delete'
    Снимок установки после применения := манифест новой версии (desc)."""
    mods_dir = Path(mods_dir)
    tmp = Path(tmp_dir or mods_dir.parent)
    decisions = decisions or {}
    actions = plan['actions']

    need_theirs = set()
    for r in actions:
        st, rp, tsha = r['status'], r['relpath'], r.get('theirs')
        dec = decisions.get(rp, DECISION_DEFAULTS.get(st))
        if st in ('add', 'update'):
            need_theirs.add(tsha)
        elif st in ('conflict_text', 'conflict_binary') and dec in ('theirs', 'both'):
            need_theirs.add(tsha)
    need_theirs.discard(None)
    blobs = ({} if dry_run else
             fetch_blobs(index, need_theirs, token, tmp, log, progress_cb, should_cancel))

    stats = {'written': 0, 'merged': 0, 'kept': 0, 'deleted': 0,
             'sidecar': 0, 'conflict': 0, 'skipped': 0}
    for r in actions:
        st, rp, tsha = r['status'], r['relpath'], r.get('theirs')
        fp = mods_dir / install_relpath(rp)
        dec = decisions.get(rp, DECISION_DEFAULTS.get(st))

        if st in ('add', 'update'):
            data = blobs.get(tsha)
            if data is None:
                stats['skipped'] += 1
            elif not dry_run:
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_bytes(data)
                stats['written'] += 1
            else:
                stats['written'] += 1
        elif st == 'automerge':
            if not dry_run:
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_bytes(r['_merged'])
            stats['merged'] += 1
        elif st == 'deleted_clean':
            if not dry_run and fp.exists():
                fp.unlink()
            stats['deleted'] += 1
        elif st == 'conflict_deleted':
            if dec == 'delete':
                if not dry_run and fp.exists():
                    fp.unlink()
                stats['deleted'] += 1
            else:
                stats['kept'] += 1
        elif st in ('conflict_text', 'conflict_binary'):
            stats['conflict'] += 1
            if dec == 'theirs':
                data = blobs.get(tsha)
                if data is not None and not dry_run:
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    fp.write_bytes(data)
                stats['written'] += 1
            elif dec == 'both':
                data = blobs.get(tsha)
                if data is not None and not dry_run:
                    side = fp.with_name(fp.name + '.srnew')
                    side.parent.mkdir(parents=True, exist_ok=True)
                    side.write_bytes(data)
                stats['sidecar'] += 1
            else:                              # 'mine' — оставить файл игрока
                stats['kept'] += 1
        else:                                  # unchanged / player_only
            stats['kept'] += 1

    if not dry_run:
        save_snapshot_from_desc(mods_dir, desc, snap_dir)
    return stats


# ---------------------------------------------------------------------------
# Индексация дисковых модов (локально, без сети). Определяет базу, знакомые/
# незнакомые моды и «знакомые с изменениями» (version-хэш файлсета с диска не
# совпал ни с одной версией вариантов каталога). version-хэш считается тем же
# рецептом, что в агрегаторе: sha256(json sorted [(путь_после_Mods, sha)])[:16].
# Инкрементально: sha файла переиспользуется, если size+mtime не изменились.
# ---------------------------------------------------------------------------

def disk_mod_fingerprint(mods_dir, mid, prev_files=None, sha_cache=None):
    """Отпечаток мода с диска: ({rel: {size,mtime,sha}}, version). prev_files —
    отпечаток из прошлого индекса; sha_cache — глобальные отпечатки, посчитанные при
    самой установке (sha берётся повторно при тех же size+mtime → файл НЕ перечитывается)."""
    mods_dir = Path(mods_dir)
    base = mods_dir / mid.replace('/', os.sep)
    prev_files = prev_files or {}
    sha_cache = sha_cache or {}
    files = {}
    if base.is_dir():
        for dp, _dirs, fs in os.walk(base):
            for fn in fs:
                fp = Path(dp) / fn
                try:
                    rel = fp.relative_to(mods_dir).as_posix()
                    stt = fp.stat()
                except (OSError, ValueError):
                    continue
                size, mtime = stt.st_size, int(stt.st_mtime)
                pv = prev_files.get(rel) or sha_cache.get(rel)
                if pv and pv.get('size') == size and pv.get('mtime') == mtime and pv.get('sha'):
                    sha = pv['sha']
                else:
                    sha = file_sha256(fp)
                files[rel] = {'size': size, 'mtime': mtime, 'sha': sha}
    pairs = sorted([(rel, f['sha']) for rel, f in files.items()])
    version = hashlib.sha256(
        json.dumps(pairs, ensure_ascii=False).encode('utf-8')).hexdigest()[:16]
    return files, version


def index_disk_mods(mods_dir, catalog, prev_index=None, log=print,
                    progress_cb=None, should_cancel=None, sha_cache=None):
    """Локальная индексация Mods (без сети): база + статус каждого мода:
    'known' (есть в каталоге) | 'unknown' (нет в каталоге). Хранит отпечаток
    (sha/size/mtime) + version-хэш для инкрементального реиндекса и точечных сверок.
    ВАЖНО: «изменён?» тут НЕ определяем — version-хэш всего файлсета ненадёжен
    (дисковый набор файлов часто != дескриптору → ложные срабатывания). Точная
    проверка правок — на этапе самого обновления мода (пофайловая сверка)."""
    catalog = catalog or {}
    prev_mods = (prev_index or {}).get('mods', {})
    try:
        base = detect_installed_base(mods_dir)
    except Exception:
        base = None
    mids = scan_installed_mods(mods_dir)
    out = {'base': base, 'mods': {}, 'count': len(mids)}
    n = max(1, len(mids))
    for i, mid in enumerate(mids):
        _check_cancel(should_cancel)
        files, version = disk_mod_fingerprint(
            mods_dir, mid, (prev_mods.get(mid) or {}).get('files'), sha_cache)
        ent = catalog.get(mid)
        if not ent:
            leaf = mid.split('/')[-1]
            cands = [k for k in catalog if k.split('/')[-1] == leaf]
            ent = catalog[cands[0]] if len(cands) == 1 else None
        status = 'known' if ent else 'unknown'
        out['mods'][mid] = {'status': status, 'version': version,
                            'n_files': len(files), 'files': files}
        if progress_cb:
            progress_cb(i + 1, n)
    out['known'] = sum(1 for m in out['mods'].values() if m['status'] == 'known')
    out['unknown'] = sum(1 for m in out['mods'].values() if m['status'] == 'unknown')
    return out


def index_path(mods_dir, snap_dir=None):
    return _snap_dir(mods_dir, snap_dir) / '_index.json'


def load_disk_index(mods_dir, snap_dir=None):
    p = index_path(mods_dir, snap_dir)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None


def save_disk_index(mods_dir, index, snap_dir=None):
    p = index_path(mods_dir, snap_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(index, ensure_ascii=False), encoding='utf-8')
    return p
