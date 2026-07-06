"""Юнит-тест прунинга файлов-сирот (п.10): prune_orphans_by_snapshot удаляет только
НАШИ неизменённые модовые файлы, которых нет в новом наборе; правки игрока и файлы
нового набора не трогает. Запуск: python webui/test_prune_orphans.py"""
import sys, hashlib, tempfile, shutil
from pathlib import Path
sys.path.insert(0, r'C:\claude_sandbox\sr-mods-launcher')
import launcher_core as c

PASS = []; FAIL = []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(('[OK ] ' if cond else '[FAIL] ') + name)

def sha(data):
    return hashlib.sha256(data).hexdigest()

tmp = Path(tempfile.mkdtemp(prefix='prune_'))
snap = tmp / 'snaps'
mods = tmp / 'Mods'
try:
    mid = "Huk'sShit/Mod_Interface"
    folder = mods / "Huk'sShit" / 'Mod_Interface'
    (folder / 'CFG' / 'Rus').mkdir(parents=True)
    # файлы на диске (как после установки варианта solyanka: есть Lang.dat)
    keep_data = b'keep-main'
    lang_data = b'lang-solyanka'          # будет сиротой (в новом варианте huk его нет)
    edited_data = b'player-edited'        # игрок правил — не трогать, даже если сирота
    (folder / 'Main.dat').write_bytes(keep_data)
    (folder / 'CFG' / 'Rus' / 'Lang.dat').write_bytes(lang_data)
    (folder / 'CFG' / 'Player.dat').write_bytes(b'orig-player')

    # прошлый снимок (вариант solyanka): все три файла с их sha, КРОМЕ Player.dat,
    # который снимок помнит с ИСХОДНЫМ sha, а игрок потом изменил
    def rel(*p): return "Huk'sShit/Mod_Interface/" + '/'.join(p)
    old_files = {
        rel('Main.dat'): sha(keep_data),
        rel('CFG', 'Rus', 'Lang.dat'): sha(lang_data),
        rel('CFG', 'Player.dat'): sha(b'orig-player'),
    }
    c.save_install_snapshot(mods, mid, 'v-old', old_files, snap_dir=snap)
    # игрок изменил Player.dat ПОСЛЕ установки
    (folder / 'CFG' / 'Player.dat').write_bytes(edited_data)

    # новый дескриптор (вариант huk): только Main.dat (Lang.dat и Player.dat отсутствуют)
    new_desc = {'id': mid, 'version': 'v-new',
                'files': {'code': {rel('Main.dat'): {'sha256': sha(keep_data)}}, 'assets': {}}}

    removed = c.prune_orphans_by_snapshot(mods, new_desc, snap_dir=snap, log=lambda *a: None)

    check('удалён ровно 1 сирота (Lang.dat)', removed == 1)
    check('Lang.dat-сирота удалён', not (folder / 'CFG' / 'Rus' / 'Lang.dat').exists())
    check('Main.dat (в новом наборе) на месте', (folder / 'Main.dat').exists())
    check('Player.dat, изменённый игроком, НЕ тронут', (folder / 'CFG' / 'Player.dat').read_bytes() == edited_data)
    check('пустая папка Rus подчищена', not (folder / 'CFG' / 'Rus').exists())
    check('папка мода не удалена', folder.exists())

    # без прошлого снимка — ничего не удаляем
    c.snapshot_path(mods, mid, snap).unlink()
    r2 = c.prune_orphans_by_snapshot(mods, new_desc, snap_dir=snap, log=lambda *a: None)
    check('нет снимка → 0 удалений', r2 == 0)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f'\n===== ИТОГ: PASS={len(PASS)} FAIL={len(FAIL)} =====')
sys.exit(1 if FAIL else 0)
