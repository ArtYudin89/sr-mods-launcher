# SR Mods Launcher

Лёгкий лаунчер для Space Rangers HD: ведёт список модов, скачивает и ставит их в папку
`Mods`, показывает даты обновления/установки, запускает игру. Профили и темизация.

Два типа модов:

- **ZIP** — ссылка на GitHub Release: берётся zip-ассет и распаковывается в `Mods`
  (фикс CP866-имён, нормализация путей по сегменту `Mods/`).
- **Юнит агрегатора** — мод из `sr-mods-aggregator`: сборка по рецепту. **И код, и ассеты**
  берутся content-addressed чанками с Hugging Face по `code.manifest.json` +
  `assets.manifest.json` + `state/asset_index.json` (дедуп по sha256, публичные чанки — без
  токена). Можно поставить **весь юнит** или **один мод**: в диалоге добавления нажми
  «⟳ Список» и выбери мод (`Категория/Имя`); пусто = весь юнит. `_base` — общие файлы игры.

## Запуск из исходников

```
pip install -r requirements.txt
python launcher.py
```

Зависимости минимальны: `tkinter` (входит в стандартный Python) и `requests`.

## Сборка .exe (Windows)

Требуется Python 3.8+ и PyInstaller.

```
pip install -r requirements.txt pyinstaller
build.bat
```

Либо вручную:

```
pyinstaller --onefile --windowed --name SRModsLauncher --add-data "theme.json;." launcher.py
```

Результат: `dist\SRModsLauncher.exe` — самодостаточный файл (Python и библиотеки внутри).

Замечания по сборке:
- `--windowed` — без консольного окна (GUI-приложение).
- `theme.json` встроен в exe как тема по умолчанию. Чтобы переопределить оформление,
  положи свой `theme.json` **рядом с .exe** — он имеет приоритет.
- Конфиг (`launcher_config.json`) и `profiles/` создаются рядом с .exe при первом запуске.
- Иконку можно добавить флагом `--icon app.ico`.

## Приватный репозиторий агрегатора

Если репозиторий с модами приватный, укажи **GitHub token** в поле настроек лаунчера
(или переменную окружения `GH_TOKEN`). Нужны права `repo` (чтение релизов и файлов).
Токен сохраняется в `launcher_config.json` рядом с лаунчером.

## Темизация (`theme.json`)

```jsonc
{
  "name": "Space Dark",
  "bg": "#0b0e17", "panel": "#141a26", "fg": "#d6e1ff", "muted": "#7f8db0",
  "accent": "#3a6df0", "accent_fg": "#ffffff",
  "tree_bg": "#0f1521", "tree_sel": "#1f3a6e",
  "font_family": "Segoe UI", "font_size": 10, "mono_family": "Consolas",
  "banner": ""            // путь к PNG-баннеру (опц.), рисуется в шапке
}
```

Меняешь цвета/шрифты/баннер → перезапускаешь лаунчер. Можно оформить под стиль игры.

## Файлы

```
launcher.py            GUI (tkinter)
launcher_core.py       движок: GitHub, скачивание, сборка по рецепту (тестируется headless)
theme.json             тема оформления
build.bat              сборка .exe через PyInstaller
requirements.txt       зависимости (requests)
```
