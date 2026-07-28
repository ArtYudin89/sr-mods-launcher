"""Уровень-3 тест САМООБНОВЛЕНИЯ: реальная подмена .exe помощником-батником.

Игроки сообщали «автообновление не работает». Скачивание было ни при чём — ломался
помощник `_sr_selfupdate.bat`: он писался в utf-8 с ПОЛНЫМИ путями внутри, а cmd читает
батник в OEM-кодировке (cp866). Путь `C:\\Users\\Артём\\…` превращался в мусор, `move`
молча не срабатывал, а `start` показывал окно «Не удаётся найти …» — лаунчер закрывался
и не возвращался. Плюс помощник запускался отцепленным (без консоли), где cmd теряет
кодовую страницу вовсе, а ожидание через `tasklist | find <pid>` в таком процессе молча
убивало весь скрипт.

Тест гоняет НАСТОЯЩИЙ помощник на настоящих файлах: ascii-путь, путь с кириллицей, имя
exe с кириллицей, реально запущенный (залоченный) exe и смерть процесса-родителя.
«Лаунчер» — копия cmd.exe, крутящая ping; «новая версия» — копия where.exe с маркером.

Запуск: python webui/test_self_update.py   (только Windows)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import launcher_core as core

PASS, FAIL = [], []
MARK = b'NEWVERSION'
WHERE = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'where.exe'
CMD = Path(os.environ.get('ComSpec', r'C:\Windows\System32\cmd.exe'))


def check(name, expect, actual):
    ok = expect == actual
    (PASS if ok else FAIL).append(name)
    print(f'[{"OK " if ok else "FAIL"}] {name}\n      ожидание: {expect!r}\n      факт:     {actual!r}')


def check_true(name, val):
    check(name, True, bool(val))


def _mkcase(dirname, exename, running):
    """Песочница: <tmp>/<dirname>/<exename> (+ .new.exe с маркером). -> (root, cur, new, proc)"""
    root = Path(tempfile.mkdtemp(prefix='sr_selfupd_'))
    d = root / dirname
    d.mkdir(parents=True)
    cur = d / exename
    new = d / (Path(exename).stem + '.new.exe')
    shutil.copy2(CMD if running else WHERE, cur)
    shutil.copy2(WHERE, new)
    with open(new, 'ab') as f:
        f.write(MARK)
    proc = None
    if running:                       # «лаунчер» запущен и держит свой файл
        proc = subprocess.Popen([str(cur), '/c', 'ping -n 6 127.0.0.1 >nul'],
                                creationflags=0x08000000)
        time.sleep(0.4)
    return root, cur, new, proc


def _wait_swap(cur, secs=25):
    for _ in range(secs * 2):
        time.sleep(0.5)
        try:
            if cur.exists() and cur.read_bytes().endswith(MARK):
                return True
        except OSError:               # файл ровно сейчас переименовывают
            pass
    return False


def _wait_clean(d, exename, secs=25):
    """Дождаться, пока в папке останется только сам exe (.old.exe удалён, батник — тоже)."""
    left = []
    for _ in range(secs * 2):
        left = sorted(x.name for x in d.iterdir())
        if left == [exename]:
            return left
        time.sleep(0.5)
    return left


def _cleanup(root, proc):
    if proc:
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
    time.sleep(1.0)                   # дать помощнику удалить себя
    shutil.rmtree(root, ignore_errors=True)


def case(title, dirname, exename='SRModsLauncher.exe', running=False, in_child=False):
    """in_child=True — spawn_self_replace вызывается из ОТДЕЛЬНОГО процесса, который тут
    же умирает: помощник обязан пережить смерть родителя (в бою так и происходит)."""
    print(f'\n--- {title} ---')
    root, cur, new, proc = _mkcase(dirname, exename, running)
    d = cur.parent
    if in_child:
        code = ('import sys, os; sys.path.insert(0, r"%s"); import launcher_core as c; '
                'c.spawn_self_replace(r"%s", r"%s", log=lambda m: None); os._exit(0)'
                % (str(Path(core.__file__).parent), str(cur), str(new)))
        subprocess.run([sys.executable, '-c', code], timeout=30)
    else:
        core.spawn_self_replace(cur, new, log=lambda m: None)
    ok = _wait_swap(cur)
    check_true(f'{title}: exe заменён новой версией', ok)
    if ok:                            # помощник добивает .old.exe и удаляет себя — не сразу:
        left = _wait_clean(d, exename)    # он ждёт, пока прежний процесс отпустит файл
        check(f'{title}: мусор не остался', [exename], left)
    _cleanup(root, proc)


print('=== Самообновление: подмена exe помощником ===')
case('T1 ascii-путь', 'games/launcher')
case('T2 путь с кириллицей', 'Игры/Артём/лаунчер')          # регресс: главный баг
case('T3 кириллица в имени exe', 'Игры', 'Лаунчер модов.exe')
case('T4 exe запущен (залочен)', 'games/running', running=True)
case('T5 родитель умирает сразу', 'Игры/Артём', running=True, in_child=True)

print('\n--- T6 новая версия исчезла → откат на прежнюю ---')
_root, _cur, _new, _ = _mkcase('Игры/откат', 'SRModsLauncher.exe', False)
_orig = _cur.read_bytes()
_new.unlink()                                     # скачанный файл пропал
core.spawn_self_replace(_cur, _new, log=lambda m: None)
time.sleep(4)
check_true('T6: лаунчер на месте (игрок не остался без exe)', _cur.exists())
import hashlib as _hl
_sha = lambda b: _hl.sha256(b).hexdigest()[:16]
check('T6: содержимое прежнее (sha)', _sha(_orig), _sha(_cur.read_bytes()) if _cur.exists() else '-')
check_true('T6: .old.exe убран', not (_cur.parent / 'SRModsLauncher.old.exe').exists())
_cleanup(_root, None)

print('\n--- T7 текст помощника: путей внутри нет, кодировка OEM ---')
_root, _cur, _new, _ = _mkcase('Игры/Артём', 'SRModsLauncher.exe', False)
_bat = _cur.with_name('_sr_selfupdate.bat')
import unittest.mock as _mock
with _mock.patch.object(subprocess, 'Popen', lambda *a, **k: None):
    core.spawn_self_replace(_cur, _new, log=lambda m: None)
_raw = _bat.read_bytes()
_txt = _raw.decode('cp866')
check_true('T7: батник написан (Popen подменён)', _bat.exists())
check('T7: папки в тексте нет — только %~dp0', False, 'Артём' in _txt or str(_cur.parent) in _txt)
check('T7: utf-8 не используется (кириллица в cp866)', False, b'\xd0' in _raw)
check('T7: ожидание без tasklist/конвейера', False, 'tasklist' in _txt or '|' in _txt)
check('T7: пауза через ping (timeout требует консоли)', True, 'ping -n' in _txt)
check('T7: есть откат, если новая версия не встала', True,
      'if not exist "%EXE%" move /Y "%BAK%" "%EXE%"' in _txt)
_cleanup(_root, None)

print(f'\n===== ИТОГ: PASS={len(PASS)}  FAIL={len(FAIL)} =====')
if FAIL:
    print('ПРОВАЛЫ:', FAIL)
    sys.exit(1)
