"""Уровень-1 ассерты на маршрутизацию путей установки: ничто не должно писаться
мимо папки игры.

Пути в install_route приходят из двух чужих источников: дескрипторы каталога
(raw.githubusercontent) и имена записей в zip форков/сборок. Ни json, ни zipfile
их не проверяют. Опаснее всего запись с буквой диска: `base / 'C:/Windows/x'`
в pathlib возвращает АБСОЛЮТНЫЙ путь — правая часть побеждает целиком.

Запуск: python webui/test_install_paths.py
"""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import launcher_core as core

PASS = []; FAIL = []


def check(name, expect, actual):
    ok = expect == actual
    (PASS if ok else FAIL).append(name)
    mark = 'OK ' if ok else 'FAIL'
    print(f'[{mark}] {name}\n      ожидание: {expect!r}\n      факт:     {actual!r}')


MODS = Path('D:/Game/Mods')

# ── Побеги отклоняются ────────────────────────────────────────────────────────
for bad in ('C:/Windows/System32/evil.dll', 'C:\\Windows\\System32\\evil.dll',
            '{app}/../../evil.txt', '../../evil.txt', 'Mods/../../evil.txt',
            'D:/evil.txt'):
    check(f'побег отклонён: {bad}', (None, None), core.install_route(bad))
    check(f'цели нет: {bad}', None, core.install_target(bad, MODS))

# ── Обычные пути не задеты ────────────────────────────────────────────────────
check('мод по пути Mods/', ('mods', 'Good/f.txt'), core.install_route('Mods/Good/f.txt'))
check('мод с Inno-префиксом {app}', ('mods', 'Good/f.txt'),
      core.install_route('{app}/Mods/Good/f.txt'))
check('мод с обратными слэшами', ('mods', 'Good/f.txt'),
      core.install_route('Mods\\Good\\f.txt'))
check('вложенный ресурс мода', ('mods', 'Cool/DATA/a.gi'),
      core.install_route('Mods/Cool/DATA/a.gi'))
check('корневой exe игры', ('root', 'Rangers.exe'), core.install_route('Rangers.exe'))
check('корневой каталог игры', ('root', 'DATA/x.pkg'), core.install_route('DATA/x.pkg'))
check('staging агрегатора пропускается', (None, None), core.install_route('.temp/x'))
check('staging-обёртка _unpacked срезается', ('mods', 'Cool/f.txt'),
      core.install_route('Fork_unpacked/Mods/Cool/f.txt'))
check('цель мода на диске', MODS / 'Good' / 'f.txt',
      core.install_target('Mods/Good/f.txt', MODS))
check('цель корневого файла — папка игры', MODS.parent / 'Rangers.exe',
      core.install_target('Rangers.exe', MODS))

# ── Русские имена не задеты ───────────────────────────────────────────────────
check('кириллица в пути мода', ('mods', 'Мод/Файл.txt'),
      core.install_route('Mods/Мод/Файл.txt'))

# ── Сквозь настоящий zip ──────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as _d:
    _root = Path(_d)
    _mods = _root / 'Game' / 'Mods'
    _mods.mkdir(parents=True)
    _zip = _root / 'fork.zip'
    with zipfile.ZipFile(_zip, 'w') as z:
        z.writestr('Mods/Cool/ModuleInfo.txt', b'info')
        z.writestr('../../pwned.txt', b'x')
        z.writestr('C:/Windows/pwned.dll', b'x')
    core._extract_zip_to(_zip, _mods)
    check('обычный файл из zip установлен', b'info',
          (_mods / 'Cool' / 'ModuleInfo.txt').read_bytes())
    check('запись ../../ наружу не записана', False, (_root / 'pwned.txt').exists())
    check('запись ../../ не записана и уровнем выше', False,
          (_root / 'Game' / 'pwned.txt').exists())
    check('запись с буквой диска пропущена', False,
          any(p.name == 'pwned.dll' for p in _root.rglob('*')))

print(f'\n===== ИТОГ: PASS={len(PASS)}  FAIL={len(FAIL)} =====')
if FAIL:
    print('ПРОВАЛЫ:', FAIL)
    sys.exit(1)
