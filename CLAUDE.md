# SR Mods Launcher — правила для Claude

Public `ArtYudin89/sr-mods-launcher`, ПРОД у игроков (авто-self-update). Живой статус/версия — в памяти `project-sr-mods-launcher`.

## Архитектура
- Рабочий код: `webui/` — `app.py` (класс Api) + `launcher_core.py` (движок, headless-тестируемый) + `web/{app.js,index.html,style.css}`.
- Корневые `launcher.py` / `build.bat` — СТАРЫЙ tkinter-лаунчер: не трогать, не путать.
- Данные у игрока: `%Documents%\SpaceRangersHD\Launcher`. Каталог/дескрипторы — raw.githubusercontent без токена; блобы — HF по `asset_index.json`.

## Главный инвариант — «детект == апдейт»
Детект обновлений (`_check_updates_worker` → `_merged_camp_files`) и апдейт (`_plan_merge_disk` → `_full_variant_descriptor`) ОБЯЗАНЫ целиться в один и тот же слитый набор (base+fixes+форк по load_order). Любая асимметрия (сырые поюнитные манифесты, куцый default_source, невыбранный вариант, фикс-оверлей без обратной карты `fix_parent`) = класс багов «вечное обновление». Каждую правку установки/детекта сверять с обеими сторонами; на новую топологию каталога — регресс-тест в `test_integration.py`.

## Тесты (в `webui\`)
- `python -m pytest test_integration.py test_ui_logic.py test_vdiff_variants.py ...` с `PYTHONIOENCODING=utf-8`.
- Playwright: ~10 сценариев флейкуют исторически — не считать регрессией; клипающиеся/absolute элементы кликать `dispatch_event`.
- Self-QA: новую UI-логику покрывать в `test_ui_logic` ДО ручной проверки (Api.__new__ без __init__, no-op save/emit).

## Сборка exe
`cd webui && pyinstaller --noconfirm --clean SRModsLauncher.spec` при ЗАКРЫТОМ exe (иначе WinError 5). Свежий exe = `webui/dist/SRModsLauncher.exe` (корневой `dist/` — старый).

## Релиз (только по команде юзера)
bump `LAUNCHER_VERSION` в `app.py` → тесты → exe → commit/push master (ff) → `gh release create vX.Y.Z webui/dist/SRModsLauncher.exe` → в агрегаторе `state/launcher_release.json` → vX.Y.Z (сначала `git reset --hard origin/master` поверх ночного бота, потом точечная правка, ff push) → README/ИНСТРУКЦИЯ.md/ИНСТРУКЦИЯ-ПРОСТАЯ.md.

## Уроки
- json-данные агрегатора НЕ прогонять через json.dump (реформат) — точечный `str.replace`.
- Манифесты читать только `core._load_manifest` (разворачивает `files`), не `_fetch_json`.
- Объявленный Conflict показывается ВСЕГДА, зависимость его не гасит (решение юзера).
- Дата мода = dev-mtime из каталога (`meta['mtime']`, max по файлам варианта); способ установки на дату не влияет.
- Термины UI: «Сборка» = дистрибутив (redux/universe/original), «Профиль» = набор игрока; выделение (active, драйвит связи) ≠ выбор (чекбоксы, массовые действия).
