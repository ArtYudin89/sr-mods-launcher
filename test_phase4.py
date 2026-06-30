#!/usr/bin/env python3
"""Headless-тест Фазы 4 (3-way merge правок игрока при обновлении мода).
Сеть не нужна: fetch_blobs подменяется in-memory хранилищем блобов по sha256."""
import hashlib
import tempfile
from pathlib import Path

import launcher_core as core

BLOBS = {}          # sha256 -> bytes (имитация content-addressed чанков)


def put(data: bytes) -> str:
    sh = hashlib.sha256(data).hexdigest()
    BLOBS[sh] = data
    return sh


def fake_fetch_blobs(index, shas, token, tmp, log=print, progress_cb=None, should_cancel=None,
                     **kwargs):
    return {s: BLOBS[s] for s in set(shas) if s in BLOBS}


core.fetch_blobs = fake_fetch_blobs    # monkeypatch: никакой сети


def mk_desc(mod_id, version, files: dict) -> dict:
    """files: {relpath: (kind, bytes)} -> дескриптор с манифестом (sha кладём в BLOBS)."""
    code, assets = {}, {}
    for rp, (kind, data) in files.items():
        sh = put(data)
        (code if kind == 'code' else assets)[rp] = {'sha256': sh, 'size': len(data)}
    return {'id': mod_id, 'source': 'test', 'version': version,
            'files': {'code': code, 'assets': assets}}


def write_disk(mods_dir, relpath, data: bytes):
    fp = Path(mods_dir) / core.install_relpath(relpath)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(data)


def read_disk(mods_dir, relpath):
    fp = Path(mods_dir) / core.install_relpath(relpath)
    return fp.read_bytes() if fp.exists() else None


U16 = lambda s: s.encode('utf-16-le')   # noqa: E731


def main():
    tmp = Path(tempfile.mkdtemp(prefix='srphase4_'))
    mods = tmp / 'Mods'
    snap = tmp / 'snap'              # снимок вне Mods
    P = 'Mods/Test/Mod/'

    base_files = {
        P + 'a.txt': ('code', b'A base\n'),                       # -> player_only
        P + 'b.txt': ('code', b'B base\n'),                       # -> update
        P + 'c.txt': ('code', b'L1\nL2\nL3\nL4\nL5\n'),           # -> automerge
        P + 'd.txt': ('code', b'd1\nd2\nd3\n'),                   # -> conflict_text
        P + 'e.png': ('assets', b'\x89PNG-base-bytes'),           # -> conflict_binary
        P + 'f.txt': ('code', b'F base\n'),                       # -> deleted_clean
        P + 'g.txt': ('code', b'G base\n'),                       # -> conflict_deleted
        P + 'i.txt': ('code', b'I same\n'),                       # -> unchanged
        P + 'j.txt': ('code', U16('j1\nj2\nj3\n')),               # -> automerge (UTF-16)
    }
    base_desc = mk_desc('Test/Mod', 'v1', base_files)

    # «установка»: пишем base на диск + снимок (как сделал бы install_descriptor)
    for rp, (_k, data) in base_files.items():
        write_disk(mods, rp, data)
    core.save_snapshot_from_desc(mods, base_desc, snap_dir=snap)

    # правки игрока на диске
    write_disk(mods, P + 'a.txt', b'A PLAYER\n')                  # игрок правил, мод нет
    write_disk(mods, P + 'c.txt', b'L1-mine\nL2\nL3\nL4\nL5\n')   # правка строки 1
    write_disk(mods, P + 'd.txt', b'd1\nd2-MINE\nd3\n')           # правка строки 2
    write_disk(mods, P + 'e.png', b'\x89PNG-PLAYER-bytes')        # игрок заменил бинарь
    write_disk(mods, P + 'g.txt', b'G PLAYER\n')                  # игрок правил удаляемый
    write_disk(mods, P + 'j.txt', U16('j1-mine\nj2\nj3\n'))       # UTF-16 правка строки 1

    # новая версия мода (theirs)
    new_files = {
        P + 'a.txt': ('code', b'A base\n'),                       # мод не менял
        P + 'b.txt': ('code', b'B NEW\n'),                        # мод изменил
        P + 'c.txt': ('code', b'L1\nL2\nL3\nL4\nL5-new\n'),       # правка строки 5 (не пересек.)
        P + 'd.txt': ('code', b'd1\nd2-THEIRS\nd3\n'),            # правка строки 2 (пересек.!)
        P + 'e.png': ('assets', b'\x89PNG-NEW-bytes'),            # мод изменил бинарь
        # f.txt удалён (нет в новой версии) — игрок не трогал
        # g.txt удалён — игрок трогал
        P + 'h.txt': ('code', b'H new file\n'),                   # новый файл
        P + 'i.txt': ('code', b'I same\n'),                       # без изменений
        P + 'j.txt': ('code', U16('j1\nj2\nj3-new\n')),           # UTF-16 правка строки 3
    }
    new_desc = mk_desc('Test/Mod', 'v2', new_files)

    plan = core.plan_update_merge(new_desc, mods, index={}, snap_dir=snap, tmp_dir=tmp)
    got = {r['relpath'].split('/')[-1]: r['status'] for r in plan['actions']}
    expect = {
        'a.txt': 'player_only', 'b.txt': 'update', 'c.txt': 'automerge',
        'd.txt': 'conflict_text', 'e.png': 'conflict_binary',
        'f.txt': 'deleted_clean', 'g.txt': 'conflict_deleted',
        'h.txt': 'add', 'i.txt': 'unchanged', 'j.txt': 'automerge',
    }
    ok = True
    for k, v in expect.items():
        mark = 'OK' if got.get(k) == v else f'FAIL (got {got.get(k)})'
        if got.get(k) != v:
            ok = False
        print(f'  {k:8} -> {v:16} {mark}')
    print('summary:', plan['summary'])

    # применяем: конфликты — текст оставить мой, бинарь взять новый,
    # удалённый правленый — оставить
    decisions = {
        P + 'd.txt': 'mine',
        P + 'e.png': 'theirs',
        P + 'g.txt': 'keep',
    }
    stats = core.apply_update_plan(new_desc, plan, decisions, mods,
                                   index={}, snap_dir=snap, tmp_dir=tmp)
    print('apply stats:', stats)

    checks = [
        ('a.txt оставлен игроку', read_disk(mods, P + 'a.txt') == b'A PLAYER\n'),
        ('b.txt обновлён', read_disk(mods, P + 'b.txt') == b'B NEW\n'),
        ('c.txt авто-слит', read_disk(mods, P + 'c.txt') == b'L1-mine\nL2\nL3\nL4\nL5-new\n'),
        ('d.txt оставлен мой', read_disk(mods, P + 'd.txt') == b'd1\nd2-MINE\nd3\n'),
        ('e.png взят новый', read_disk(mods, P + 'e.png') == b'\x89PNG-NEW-bytes'),
        ('f.txt удалён', read_disk(mods, P + 'f.txt') is None),
        ('g.txt оставлен', read_disk(mods, P + 'g.txt') == b'G PLAYER\n'),
        ('h.txt добавлен', read_disk(mods, P + 'h.txt') == b'H new file\n'),
        ('j.txt авто-слит UTF-16', read_disk(mods, P + 'j.txt') == U16('j1-mine\nj2\nj3-new\n')),
    ]
    for name, cond in checks:
        if not cond:
            ok = False
        print(f'  {"OK" if cond else "FAIL"}  {name}')

    # снимок обновлён до новой версии и НЕ содержит удалённых файлов
    sn = core.load_install_snapshot(mods, 'Test/Mod', snap_dir=snap)
    snap_ok = (sn['version'] == 'v2'
               and (P + 'f.txt') not in sn['files']
               and (P + 'h.txt') in sn['files'])
    ok = ok and snap_ok
    print(f'  {"OK" if snap_ok else "FAIL"}  снимок -> v2, без f.txt, с h.txt')

    print('\n=== ИТОГ:', 'ВСЕ ТЕСТЫ ПРОШЛИ ===' if ok else 'ЕСТЬ ОШИБКИ ===')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
