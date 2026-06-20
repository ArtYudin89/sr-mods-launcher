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
import zipfile
from pathlib import Path

import requests

API = 'https://api.github.com'


# ---------------------------------------------------------------------------
# GitHub helpers (поддержка приватных репозиториев через token)
# ---------------------------------------------------------------------------

def _headers(token, accept='application/vnd.github+json'):
    h = {'Accept': accept, 'X-GitHub-Api-Version': '2022-11-28'}
    if token:
        h['Authorization'] = 'Bearer ' + token
    return h


def gh_json(url, token, params=None):
    r = requests.get(url, headers=_headers(token), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def repo_file_bytes(repo, path, token):
    """Сырой файл из репозитория (contents API, работает и для приватных)."""
    r = requests.get(f'{API}/repos/{repo}/contents/{path}',
                     headers=_headers(token, 'application/vnd.github.raw'),
                     timeout=30)
    r.raise_for_status()
    return r.content


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


def download_asset(asset, token, dest, progress_cb=None):
    """Скачать ассет релиза по api-url (Accept: octet-stream). Работает для приватных."""
    with requests.get(asset['url'],
                      headers=_headers(token, 'application/octet-stream'),
                      stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get('content-length', 0))
        done = 0
        with open(dest, 'wb') as f:
            for c in r.iter_content(1 << 20):
                f.write(c)
                done += len(c)
                if progress_cb and total:
                    progress_cb(done, total)
    return dest


def download_url(url, token, dest, progress_cb=None):
    """Скачать произвольный URL (для generic zip — browser_download_url)."""
    with requests.get(url, headers=_headers(token, '*/*'),
                      stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get('content-length', 0))
        done = 0
        with open(dest, 'wb') as f:
            for c in r.iter_content(1 << 20):
                f.write(c)
                done += len(c)
                if progress_cb and total:
                    progress_cb(done, total)
    return dest


# ---------------------------------------------------------------------------
# Пути установки
# ---------------------------------------------------------------------------

def install_relpath(relpath):
    """Нормализовать путь к виду относительно игровой Mods/.
    Берём всё ПОСЛЕ последнего сегмента 'Mods'. Если 'Mods' нет — путь как есть."""
    parts = relpath.replace('\\', '/').split('/')
    idx = None
    for i, p in enumerate(parts):
        if p.lower() == 'mods':
            idx = i
    if idx is not None and idx < len(parts) - 1:
        return '/'.join(parts[idx + 1:])
    return '/'.join(parts)


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


def install_zip(url, mods_dir, token, progress_cb=None, log=print, tmp_dir=None):
    info = resolve_zip(url, token)
    tmp = Path(tmp_dir or mods_dir).parent / '_dl.zip'
    log('Скачивание zip...')
    if info.get('asset'):
        download_asset(info['asset'], token, tmp, progress_cb)
    else:
        download_url(info['download_url'], token, tmp, progress_cb)
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
            target = mods_dir / install_relpath(name)
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
                     log=print, tmp_dir=None, dry_run=False, mod=None):
    """Собрать юнит (или один мод mod=mod_key) из HF: код И ассеты берутся из
    content-addressed чанков asset_index по code.manifest + assets.manifest.
    mod=None -> весь юнит; mod='Кат/Имя' или '_base' -> только этот мод.
    dry_run: посчитать и проверить без скачивания/записи. Возвращает stats."""
    mods_dir = Path(mods_dir)
    tmp = Path(tmp_dir or mods_dir.parent)
    tmp.mkdir(parents=True, exist_ok=True)
    stats = {'code_files': 0, 'asset_files': 0, 'chunks': [], 'missing': 0,
             'mod': mod, 'updated': None}

    index = json.loads(repo_file_bytes(repo, 'state/asset_index.json', token))
    code_man = _load_manifest(repo, f'mods/{camp}/{unit}/code.manifest.json', token)
    asset_man = _load_manifest(repo, f'mods/{camp}/{unit}/assets.manifest.json', token)
    if not code_man and not asset_man:
        raise RuntimeError(f'нет манифестов для {camp}/{unit} в {repo}')

    # Сгруппировать нужные блобы по чанкам. Один sha может вести к нескольким
    # путям (дубли) и из обоих манифестов — храним список (relpath, kind).
    need = {}    # chunk -> {sha256: [(relpath, kind), ...]}
    for kind, man in (('code', code_man), ('asset', asset_man)):
        for relpath, meta in man.items():
            if mod is not None and mod_key(relpath) != mod:
                continue
            sh = meta['sha256']
            b = index['blobs'].get(sh)
            if not b:
                stats['missing'] += 1
                continue
            need.setdefault(b['chunk'], {}).setdefault(sh, []).append((relpath, kind))

    stats['chunks'] = list(need.keys())
    for shamap in need.values():
        for targets in shamap.values():
            for _relpath, kind in targets:
                stats[f'{kind}_files'] += 1
    scope = f'мод {mod}' if mod else 'весь юнит'
    log(f'{scope}: {stats["code_files"]} код + {stats["asset_files"]} ассетов '
        f'в {len(need)} чанках'
        + (f', НЕ найдено блобов: {stats["missing"]}' if stats['missing'] else ''))
    if dry_run:
        return stats

    # Скачать каждый нужный чанк и извлечь блобы во все целевые пути.
    for chunk, shamap in need.items():
        meta = index['chunks'].get(chunk, {})
        cpath = tmp / f'_chunk_{chunk}'
        log(f'Скачивание чанка {chunk} ({len(shamap)} блобов) ...')
        url = meta.get('url')
        if url:                                  # HF public / любой прямой URL
            ctoken = token if meta.get('store') == 'github' else None
            download_url(url, ctoken, cpath, progress_cb)
        else:                                    # back-compat: GitHub release по тегу
            tag = meta.get('release_tag')
            crel = release_by_tag(repo, tag, token)
            casset = next((a for a in crel.get('assets', []) if a['name'] == chunk), None)
            if not casset:
                log(f'[warn] чанк {chunk} не найден (url/release)')
                continue
            download_asset(casset, token, cpath, progress_cb)
        with zipfile.ZipFile(cpath) as z:
            for sh, targets in shamap.items():
                data = z.read(sh)
                for relpath, _kind in targets:
                    target = mods_dir / install_relpath(relpath)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
        cpath.unlink(missing_ok=True)
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
