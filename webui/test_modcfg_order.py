"""order_modcfg — порядок подключаемых модов как в игре (по Priority), чтобы игра не
ругалась «моды подключены с нарушением порядка приоритета» (отзыв 2).
Запуск: python webui/test_modcfg_order.py"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, r'C:\claude_sandbox\sr-mods-launcher')
import launcher_core as core

PASS, FAIL = [], []
def check(name, cond, extra=''):
    (PASS if cond else FAIL).append(name)
    print(('[OK ] ' if cond else '[FAIL] ') + name + (f'  -> {extra}' if extra and not cond else ''))

d = Path(tempfile.mkdtemp(prefix='modcfg_order_'))
def mk(mid, prio=None, enc='utf-8'):
    p = d / mid.replace('/', os.sep)
    p.mkdir(parents=True, exist_ok=True)
    txt = 'Name=' + mid.split('/')[-1] + '\r\n'
    if prio is not None:
        txt += 'Priority=%s\r\n' % prio
    (p / 'ModuleInfo.txt').write_bytes(txt.encode(enc))

# группа по умолчанию (без Priority → 0), плюс явные приоритетные тиры
mk('Solyanka/LEODefendOrder')            # 0
mk('AnotherMods/AMod_CammonPanel')       # 0
mk('Den/DenButtonsPack')                 # 0
mk('FHD_FON/FHDBG')                       # 0 (проверка FHD_FON < FairansVision)
mk('FairansVision/FairanCursorG')        # 0
mk('Expansion/ExpBK', 5)
mk('Expansion/ExpBasesAutoUpgrade', 5)   # тот же приоритет → тайбрейк по пути (K<a)
mk('Kotyanka/Cat_Return', 10)
mk('Kotyanka/Cat_Guns', 8)
mk('Cyr/Приоритет7', 7, enc='cp1251')    # кириллический ModuleInfo (cp1251)

ids = ['Kotyanka/Cat_Return', 'Expansion/ExpBasesAutoUpgrade', 'FairansVision/FairanCursorG',
       'Solyanka/LEODefendOrder', 'Expansion/ExpBK', 'AnotherMods/AMod_CammonPanel',
       'Kotyanka/Cat_Guns', 'FHD_FON/FHDBG', 'Den/DenButtonsPack', 'Cyr/Приоритет7']
out = core.order_modcfg(d, ids)

expected = [
    'AnotherMods/AMod_CammonPanel', 'Den/DenButtonsPack', 'FHD_FON/FHDBG',
    'FairansVision/FairanCursorG', 'Solyanka/LEODefendOrder',   # приоритет 0, по алфавиту
    'Expansion/ExpBK', 'Expansion/ExpBasesAutoUpgrade',          # 5, тайбрейк K<a
    'Cyr/Приоритет7',                                            # 7
    'Kotyanka/Cat_Guns',                                        # 8
    'Kotyanka/Cat_Return',                                      # 10
]
check('полный порядок совпал с игровым', out == expected, str(out))
check('группа по умолчанию (0) идёт первой и по алфавиту',
      out[:5] == expected[:5], str(out[:5]))
check('FHD_FON раньше FairansVision (H<a, порядковое сравнение)',
      out.index('FHD_FON/FHDBG') < out.index('FairansVision/FairanCursorG'))
check('тайбрейк при равном Priority: ExpBK раньше ExpBasesAutoUpgrade (K<a)',
      out.index('Expansion/ExpBK') < out.index('Expansion/ExpBasesAutoUpgrade'))
check('cp1251-ModuleInfo распарсен (Priority=7 встал между 5 и 8)',
      out.index('Cyr/Приоритет7') == 7, str(out.index('Cyr/Приоритет7')))
check('mod_priority: нет поля Priority → 0', core.mod_priority(d, 'Den/DenButtonsPack') == 0)
check('mod_priority: не найден на диске → 0', core.mod_priority(d, 'Nope/Missing') == 0)
check('стабильность: тот же набор → тот же порядок',
      core.order_modcfg(d, ids) == out)
check('идемпотентность: повторная сортировка не меняет порядок',
      core.order_modcfg(d, out) == out)

print(f'\n===== ИТОГ: PASS={len(PASS)} FAIL={len(FAIL)} =====')
sys.exit(1 if FAIL else 0)
