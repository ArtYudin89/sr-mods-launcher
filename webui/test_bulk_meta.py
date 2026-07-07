"""Ассерты массовых действий над метаданными модов (отзыв 11): скрыть/показать,
добавить теги, записать заметку — сразу для НЕСКОЛЬКИХ модов, один раз пишем config.
Запуск: python test_bulk_meta.py"""
import sys, threading
sys.path.insert(0, r'C:\claude_sandbox\sr-mods-launcher\webui')
import app

PASS = []; FAIL = []
def check(name, expect, actual):
    ok = expect == actual
    (PASS if ok else FAIL).append(name)
    print(f'[{"OK " if ok else "FAIL"}] {name}\n      ожидание: {expect!r}\n      факт:     {actual!r}')

def fresh_api():
    a = app.Api.__new__(app.Api)
    a.config = {'mod_meta': {}}
    a._saves = 0
    a._save_config = lambda: setattr(a, '_saves', a._saves + 1)
    a._emit = lambda *x, **k: None
    return a

# ── скрыть несколько модов одним вызовом ──
a = fresh_api()
a.set_mods_hidden(['A/x', 'B/y', 'C/z'], True)
check('hidden: все три скрыты', [True, True, True],
      [a._meta_of(m)['hidden'] for m in ('A/x', 'B/y', 'C/z')])
check('hidden: config записан РОВНО один раз', 1, a._saves)
a.set_mods_hidden(['A/x', 'B/y', 'C/z'], False)
check('hidden: все три показаны', [False, False, False],
      [a._meta_of(m)['hidden'] for m in ('A/x', 'B/y', 'C/z')])
check('hidden: пустая запись убрана из хранилища', {}, a.config['mod_meta'])

# ── добавить теги нескольким (существующие сохраняются, дедуп без регистра) ──
a = fresh_api()
a.set_mod_tags('A/x', ['уже'])              # у мода уже есть тег
a.add_mods_tags(['A/x', 'B/y'], ['боёвка', 'УЖЕ'])
check('tags: к существующему добавлен новый, дубль не задвоен', ['уже', 'боёвка'],
      a._meta_of('A/x')['tags'])
check('tags: второму моду проставлены оба (регистр как передан)', ['боёвка', 'УЖЕ'],
      a._meta_of('B/y')['tags'])

# ── одна заметка всем ──
a = fresh_api()
a.set_mods_note(['A/x', 'B/y'], '  правь осторожно  ')
check('note: заметка записана и обрезана обоим',
      ['правь осторожно', 'правь осторожно'],
      [a._meta_of('A/x')['note'], a._meta_of('B/y')['note']])

print(f'\n===== ИТОГ: PASS={len(PASS)}  FAIL={len(FAIL)} =====')
sys.exit(1 if FAIL else 0)
