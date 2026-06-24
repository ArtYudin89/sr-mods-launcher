#!/usr/bin/env python3
"""SR Mods Launcher — GUI (tkinter, stdlib + requests).

Тонкая обёртка над launcher_core. Поддерживает два типа модов:
  * zip  — ссылка на GitHub Release (любой мод);
  * unit — мод из агрегатора sr-mods-aggregator (сборка по рецепту).

Темизация: всё оформление берётся из theme.json — можно перекрасить под игру
без правки кода. Запуск: python launcher.py
"""
import json
import os
import queue
import random
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext

import launcher_core as core


def app_dir():
    """Каталог для записи (конфиг/профили). Рядом с .exe в frozen-режиме."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir():
    """Каталог встроенных ресурсов (тема по умолчанию). _MEIPASS в frozen."""
    if getattr(sys, 'frozen', False):
        return Path(getattr(sys, '_MEIPASS', app_dir()))
    return Path(__file__).resolve().parent


HERE = app_dir()
CONFIG_FILE = HERE / 'launcher_config.json'
PROFILES_DIR = HERE / 'profiles'
# Тема: рядом с exe (пользовательская) с фолбэком на встроенную (bundled).
THEME_FILE = HERE / 'theme.json'
if not THEME_FILE.exists():
    THEME_FILE = resource_dir() / 'theme.json'

DEFAULT_THEME = {
    "name": "Space Dark",
    "bg": "#0b0e17", "panel": "#141a26", "fg": "#d6e1ff", "muted": "#7f8db0",
    "accent": "#3a6df0", "accent_fg": "#ffffff",
    "tree_bg": "#0f1521", "tree_sel": "#1f3a6e",
    "font_family": "Segoe UI", "font_size": 10, "mono_family": "Consolas",
    "banner": ""
}


def fmt_date(iso):
    if not iso:
        return '—'
    try:
        return datetime.fromisoformat(iso.replace('Z', '+00:00')).strftime('%d.%m.%Y %H:%M')
    except Exception:
        return iso


class Launcher:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('SR Mods Launcher')
        self.root.geometry('1040x720')
        self.theme = self._load_json(THEME_FILE, DEFAULT_THEME)
        self.config = self._load_json(CONFIG_FILE, {
            'last_profile': 'default', 'profiles': ['default'], 'github_token': '',
            'repo': 'ArtYudin89/sr-mods-aggregator', 'tree_mode': 'folder'})
        PROFILES_DIR.mkdir(exist_ok=True)
        self.current_profile = self.config.get('last_profile', 'default')
        self.profile = self._load_profile(self.current_profile)
        self.busy = False
        self._pulsing = False                # прогресс-бар в режиме «бегунок»
        self._cancel = threading.Event()     # кооперативная отмена текущей операции
        self._op_buttons = []                # кнопки, блокируемые на время операции
        self.verbose_var = tk.BooleanVar(value=self.config.get('log_verbose', False))
        self._disk_index = None              # индекс дисковых модов (из снимков)
        self._sections = {}              # кэш Section из ModuleInfo: mid -> раздел
        self.progress_var = tk.DoubleVar(value=0)
        self._apply_theme()
        self._build_ui()
        self._install_hotkeys()          # Ctrl+C/V/A/X независимо от раскладки
        try:
            self._disk_index = core.load_disk_index(self._mods_dir())
        except Exception:
            self._disk_index = None
        self._refresh_list()
        self.root.after(800, self._offer_first_index)

    # ---------- storage ----------
    def _load_json(self, path, default):
        try:
            if Path(path).exists():
                d = json.loads(Path(path).read_text(encoding='utf-8'))
                return {**default, **d} if isinstance(default, dict) else d
        except Exception as e:
            print('load error', path, e)
        return dict(default)

    def _save_config(self):
        CONFIG_FILE.write_text(json.dumps(self.config, ensure_ascii=False, indent=2),
                               encoding='utf-8')

    def _load_profile(self, name):
        p = PROFILES_DIR / f'{name}.json'
        default = {'name': name, 'game_path': '', 'mods': [],
                   'created': datetime.now().isoformat()}
        return self._load_json(p, default)

    def _save_profile(self):
        self.profile['game_path'] = self.game_path_var.get()
        self.profile['updated'] = datetime.now().isoformat()
        (PROFILES_DIR / f'{self.profile["name"]}.json').write_text(
            json.dumps(self.profile, ensure_ascii=False, indent=2), encoding='utf-8')
        if self.profile['name'] not in self.config['profiles']:
            self.config['profiles'].append(self.profile['name'])
        self.config['github_token'] = self.token_var.get().strip()
        if hasattr(self, 'repo_var'):
            self.config['repo'] = self.repo_var.get().strip()
        self._save_config()

    def _token(self):
        return self.token_var.get().strip() or os.environ.get('GH_TOKEN', '')

    def _repo(self):
        return (self.repo_var.get().strip() if hasattr(self, 'repo_var')
                else self.config.get('repo', 'ArtYudin89/sr-mods-aggregator'))

    def _game_root(self):
        """Корень игры из game_path (поддерживает и папку, и путь к Rangers.exe)."""
        gp = self.game_path_var.get().strip()
        if not gp:
            return None
        p = Path(gp)
        if p.is_file():
            return p.parent
        if p.is_dir():
            return p
        return None

    def _mods_dir(self):
        root = self._game_root() or HERE
        d = root / 'Mods'
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _autofind_game(self):
        """Поиск папки игры по типовым путям Steam, если путь не задан."""
        cands = []
        for drive in ('C:', 'D:', 'E:', 'F:'):
            cands += [
                Path(f'{drive}/Program Files (x86)/Steam/steamapps/common/Space Rangers HD A War Apart'),
                Path(f'{drive}/Program Files/Steam/steamapps/common/Space Rangers HD A War Apart'),
                Path(f'{drive}/SteamLibrary/steamapps/common/Space Rangers HD A War Apart'),
                Path(f'{drive}/Games/Space Rangers HD A War Apart'),
            ]
        for c in cands:
            if (c / 'Rangers.exe').exists():
                return str(c)
        return ''

    # ---------- theming ----------
    def _apply_theme(self):
        t = self.theme
        f = (t['font_family'], t['font_size'])
        fb = (t['font_family'], t['font_size'], 'bold')
        self.root.configure(bg=t['bg'])
        st = ttk.Style()
        st.theme_use('clam')
        st.configure('.', background=t['panel'], foreground=t['fg'],
                     fieldbackground=t['tree_bg'], font=f, bordercolor=t['panel'])
        st.configure('TFrame', background=t['bg'])
        st.configure('Panel.TFrame', background=t['panel'])
        st.configure('TLabel', background=t['bg'], foreground=t['fg'], font=f)
        st.configure('Head.TLabel', background=t['bg'], foreground=t['fg'], font=fb)
        st.configure('Title.TLabel', background=t['bg'], foreground=t['accent'],
                     font=(t['font_family'], t['font_size'] + 7, 'bold'))
        st.configure('TLabelframe', background=t['bg'], foreground=t['muted'],
                     bordercolor=t['panel'])
        st.configure('TLabelframe.Label', background=t['bg'], foreground=t['muted'], font=fb)
        st.configure('TButton', background=t['panel'], foreground=t['fg'], font=f,
                     borderwidth=0, focuscolor=t['accent'])
        st.map('TButton', background=[('active', t['accent']), ('pressed', t['accent'])],
               foreground=[('active', t['accent_fg'])])
        st.configure('Accent.TButton', background=t['accent'], foreground=t['accent_fg'], font=fb)
        st.map('Accent.TButton', background=[('active', t['accent']), ('pressed', t['bg'])])
        st.configure('TEntry', fieldbackground=t['tree_bg'], foreground=t['fg'],
                     insertcolor=t['fg'])
        st.configure('TCombobox', fieldbackground=t['tree_bg'], foreground=t['fg'])
        st.configure('Treeview', background=t['tree_bg'], fieldbackground=t['tree_bg'],
                     foreground=t['fg'], font=f, rowheight=int(t['font_size'] * 2.4))
        st.configure('Treeview.Heading', background=t['panel'], foreground=t['muted'], font=fb)
        st.map('Treeview', background=[('selected', t['tree_sel'])],
               foreground=[('selected', t['fg'])])
        st.configure('TProgressbar', background=t['accent'], troughcolor=t['tree_bg'])

    def _build_starfield(self, parent):
        """Процедурная «космическая» шапка на Canvas (без внешних картинок)."""
        t = self.theme
        h = int(t.get('header_height', 96))
        cv = tk.Canvas(parent, height=h, bg=t['bg'], highlightthickness=0, bd=0)
        cv.pack(fill=tk.X)
        self._starfield_canvas = cv

        def draw(_=None):
            cv.delete('all')
            w = cv.winfo_width() or 1000
            rnd = random.Random(42)            # фикс. сид — стабильный узор
            for _i in range(int(w / 6)):
                x = rnd.randint(0, w); y = rnd.randint(0, h)
                r = rnd.choice([0, 0, 0, 1, 1, 2])
                col = rnd.choice([t['muted'], t['fg'], t['accent'], t['tree_sel']])
                cv.create_oval(x - r, y - r, x + r, y + r, fill=col, outline='')
            # лёгкая «туманность» — полупрозрачность tkinter не умеет, делаем линией
            cv.create_line(0, h - 1, w, h - 1, fill=t['accent'])
            title = t.get('title', '◆ SPACE RANGERS · MODS LAUNCHER')
            cv.create_text(20, h // 2 - 8, anchor=tk.W, text=title, fill=t['accent'],
                           font=(t['font_family'], int(t['font_size']) + 9, 'bold'))
            sub = t.get('subtitle', 'агрегатор модов · сборка по рецепту')
            cv.create_text(22, h // 2 + 18, anchor=tk.W, text=sub, fill=t['muted'],
                           font=(t['font_family'], int(t['font_size']) - 1))

        cv.bind('<Configure>', draw)
        self.root.after(50, draw)

    # ---------- UI ----------
    def _build_ui(self):
        root = ttk.Frame(self.root, style='TFrame')
        root.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # banner / title
        head = ttk.Frame(root, style='TFrame')
        head.pack(fill=tk.X)
        self.banner_img = None
        bp = self.theme.get('banner')
        if bp and Path(HERE / bp).exists():
            try:
                self.banner_img = tk.PhotoImage(file=str(HERE / bp))
                tk.Label(head, image=self.banner_img, bg=self.theme['bg']).pack(anchor=tk.W)
            except Exception:
                pass
        if self.banner_img is None and self.theme.get('header_style') == 'starfield':
            self._build_starfield(head)
        elif self.banner_img is None:
            ttk.Label(head, text='◆ SR MODS LAUNCHER', style='Title.TLabel').pack(anchor=tk.W)

        # settings row
        box = ttk.LabelFrame(root, text='Настройки', padding=10)
        box.pack(fill=tk.X, pady=(8, 8))
        r1 = ttk.Frame(box, style='Panel.TFrame'); r1.pack(fill=tk.X, pady=2)
        ttk.Label(r1, text='Профиль:', background=self.theme['panel']).pack(side=tk.LEFT)
        self.profile_var = tk.StringVar(value=self.current_profile)
        self.profile_combo = ttk.Combobox(r1, textvariable=self.profile_var, width=14,
                                           values=self.config['profiles'], state='readonly')
        self.profile_combo.pack(side=tk.LEFT, padx=6)
        self.profile_combo.bind('<<ComboboxSelected>>', self._on_profile_change)
        for txt, cmd in [('Новый', self._new_profile), ('Сохранить', self._save_clicked),
                         ('Удалить', self._del_profile)]:
            ttk.Button(r1, text=txt, command=cmd).pack(side=tk.LEFT, padx=2)
        ttk.Separator(r1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(r1, text='Папка игры:', background=self.theme['panel']).pack(side=tk.LEFT)
        gp0 = self.profile.get('game_path', '') or self._autofind_game()
        self.game_path_var = tk.StringVar(value=gp0)
        ttk.Entry(r1, textvariable=self.game_path_var).pack(side=tk.LEFT, fill=tk.X,
                                                            expand=True, padx=6)
        ttk.Button(r1, text='Обзор', command=self._browse_game).pack(side=tk.LEFT)

        r2 = ttk.Frame(box, style='Panel.TFrame'); r2.pack(fill=tk.X, pady=(6, 2))
        ttk.Button(r2, text='▶  Запустить игру', style='Accent.TButton',
                   command=self._launch).pack(side=tk.LEFT)
        ttk.Button(r2, text='📂 Папка модов', command=self._open_mods).pack(side=tk.LEFT, padx=8)
        ttk.Label(r2, text='Репозиторий:', background=self.theme['panel']).pack(side=tk.LEFT,
                                                                                padx=(14, 4))
        self.repo_var = tk.StringVar(value=self.config.get('repo', 'ArtYudin89/sr-mods-aggregator'))
        e_repo = ttk.Entry(r2, textvariable=self.repo_var, width=26); e_repo.pack(side=tk.LEFT)
        ttk.Label(r2, text='GitHub token:', background=self.theme['panel']).pack(side=tk.LEFT,
                                                                                 padx=(14, 4))
        self.token_var = tk.StringVar(value=self.config.get('github_token', ''))
        e_tok = ttk.Entry(r2, textvariable=self.token_var, width=22, show='•'); e_tok.pack(side=tk.LEFT)
        # при изменении токена/репо — сбросить кэш каталога и перечитать (камп подтянется)
        for e in (e_repo, e_tok):
            e.bind('<FocusOut>', self._on_creds_change)
            e.bind('<Return>', self._on_creds_change)

        # main split
        main = ttk.Frame(root, style='TFrame'); main.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(main, style='TFrame'); left.pack(side=tk.LEFT, fill=tk.BOTH,
                                                          expand=True, padx=(0, 6))
        hdr = ttk.Frame(left, style='TFrame'); hdr.pack(fill=tk.X)
        ttk.Label(hdr, text='Моды', style='Head.TLabel').pack(side=tk.LEFT)
        ttk.Label(hdr, text='  Вид:', background=self.theme['bg']).pack(side=tk.LEFT)
        self.tree_mode_var = tk.StringVar(
            value={'folder': 'По папкам', 'section': 'По разделу'}.get(
                self.config.get('tree_mode', 'folder'), 'По папкам'))
        self.mode_cb = mode_cb = ttk.Combobox(hdr, textvariable=self.tree_mode_var, width=12,
                                              state='readonly', values=['По папкам', 'По разделу'])
        mode_cb.pack(side=tk.LEFT, padx=4)
        mode_cb.bind('<<ComboboxSelected>>', self._on_tree_mode)
        _Tooltip(mode_cb, 'Группировка установленных модов: по папкам Mods или по разделу из ModuleInfo')
        self.attention_var = tk.BooleanVar(value=False)
        att = ttk.Checkbutton(hdr, text='Только требующие внимания',
                              variable=self.attention_var, command=self._refresh_list)
        att.pack(side=tk.LEFT, padx=10)
        _Tooltip(att, 'Скрыть «✅ установлен» — оставить обновления, добавленные и прочее')
        bf = ttk.Frame(hdr, style='TFrame'); bf.pack(side=tk.RIGHT)
        for txt, cmd, tip, lock in [
                ('＋', self._add_mod, 'Добавить мод/пак/лагерь', True),
                ('－', self._remove_mod, 'Убрать выбранное из набора', True),
                ('↑', self._move_up, 'Выше в порядке загрузки', True),
                ('↓', self._move_down, 'Ниже в порядке загрузки', True),
                ('⊞', self._expand_all, 'Развернуть всё', False),
                ('⊟', self._collapse_all, 'Свернуть всё', False),
                ('🗂', self._reindex, 'Проиндексировать моды на диске (база, знакомые/'
                 'незнакомые, изменённые) — локально, без сети', True),
                ('⟳', self._refresh_remote, 'Проверить обновления (перечитать каталог)', True)]:
            b = ttk.Button(bf, text=txt, width=3, command=cmd); b.pack(side=tk.LEFT, padx=1)
            _Tooltip(b, tip)
            if lock:
                self._op_buttons.append(b)

        cols = ('kind', 'status', 'installed')
        self.tree = ttk.Treeview(left, columns=cols, show='tree headings', height=18)
        self.tree.heading('#0', text='Лагерь / Пак / Мод')
        self.tree.column('#0', width=320, anchor=tk.W)
        for c, txt, w in [('kind', 'Тип', 70), ('status', 'Статус', 110),
                          ('installed', 'Установлен', 130)]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor=tk.CENTER)
        sb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind('<Button-3>', self._tree_menu)        # ПКМ — контекстное меню

        right = ttk.Frame(main, style='TFrame'); right.pack(side=tk.RIGHT, fill=tk.BOTH)
        act = ttk.LabelFrame(right, text='Действия', padding=10); act.pack(fill=tk.X)
        for txt, cmd, style, tip in [
                ('⬇  Установить выбранный', lambda: self._install(False), 'Accent.TButton',
                 'Поставить выделенный элемент ВАШЕГО НАБОРА (📋). Не для строк из '
                 '«Установлено в игре» — те уже на диске (для них — «🔀 Обновить»).'),
                ('⬇  Установить весь набор', lambda: self._install(True), 'TButton',
                 'Поставить все элементы вашего набора (📋) по порядку'),
                ('🧩 Установить набор (с зависим.)', self._install_with_deps, 'Accent.TButton',
                 'Поставить набор и АВТОМАТИЧЕСКИ подтянуть зависимости модов '
                 '(Dependence из ModuleInfo), показать конфликты'),
                ('🔀 Обновить (сохранить правки)', self._update_merge, 'TButton',
                 'Обновить выделенный(е) мод(ы) с 3-way merge: ваши правки сохраняются. '
                 'Работает и для модов из «💾 Установлено в игре».'),
                ('🛡 Проверить совместимость', self._check_compat, 'TButton',
                 'Анализ ВСЕГО набора: одна ли база, фиксы→родитель, сейвы, '
                 'конфликты/зависимости (по ModuleInfo). Только показывает.'),
                ('🗑  Очистить Mods', self._clear_mods, 'TButton',
                 'Очистить папку Mods игры')]:
            b = ttk.Button(act, text=txt, style=style, command=cmd); b.pack(fill=tk.X, pady=2)
            _Tooltip(b, tip)
            self._op_buttons.append(b)
        pr = ttk.Frame(right, style='TFrame'); pr.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(pr, text='Прогресс:', background=self.theme['bg']).pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(pr, text='✖ Отмена', width=10,
                                     command=self._cancel_op, state='disabled')
        self.cancel_btn.pack(side=tk.RIGHT)
        _Tooltip(self.cancel_btn, 'Отменить текущую операцию')
        self.prog_label = ttk.Label(pr, text='', background=self.theme['bg'])
        self.prog_label.pack(side=tk.RIGHT, padx=8)
        self.progress = ttk.Progressbar(right, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X)
        lf = ttk.LabelFrame(right, text='Лог', padding=6); lf.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        vb = ttk.Checkbutton(lf, text='Подробный лог', variable=self.verbose_var,
                             command=self._on_verbose)
        vb.pack(anchor=tk.W)
        _Tooltip(vb, 'Краткий: только итоги по модам. Подробный: каждая загружаемая часть.')
        self.log_text = scrolledtext.ScrolledText(
            lf, width=42, height=16, bg=self.theme['tree_bg'], fg=self.theme['fg'],
            insertbackground=self.theme['fg'], relief=tk.FLAT,
            font=(self.theme['mono_family'], 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.status = ttk.Label(root, text='Готов', style='TLabel')
        self.status.pack(fill=tk.X, pady=(8, 0))
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ---------- thread-safe helpers ----------
    def _post(self, fn, *a):
        self.root.after(0, lambda: fn(*a))

    def log(self, msg):
        def _do():
            ts = datetime.now().strftime('%H:%M:%S')
            self.log_text.insert(tk.END, f'[{ts}] {msg}\n')
            self.log_text.see(tk.END)
            self.status.config(text=msg[:60])
        self._post(_do)

    def _progress(self, done, total):
        if not total:
            return                       # неизвестен объём — оставляем «бегунок»
        pct = max(0, min(100, int(done / total * 100)))

        def _do():
            if self._pulsing:            # перейти от «бегунка» к процентам
                try:
                    self.progress.stop(); self.progress.configure(mode='determinate')
                except Exception:
                    pass
                self._pulsing = False
            self.progress_var.set(pct)
            self.prog_label.config(text=f'{pct}%')
        self._post(_do)

    # ---------- управление операцией: блокировка UI / отмена / индикатор ----------
    def should_cancel(self):
        return self._cancel.is_set()

    def _begin_op(self, name):
        """Начать операцию (главный поток): заблокировать кнопки, запустить индикатор,
        включить «Отмена». Возвращает False, если уже занято."""
        if self.busy:
            messagebox.showwarning('Занято', 'Дождитесь окончания или отмените операцию')
            return False
        self.busy = True
        self._cancel.clear()
        for b in self._op_buttons:
            try:
                b.configure(state='disabled')
            except Exception:
                pass
        for w in (getattr(self, 'profile_combo', None), getattr(self, 'mode_cb', None)):
            try:
                w.configure(state='disabled')
            except Exception:
                pass
        self.cancel_btn.configure(state='normal')
        core.LOG_VERBOSE = bool(self.verbose_var.get())   # краткий/детальный лог
        try:
            self.prog_label.config(text='')
            self.progress.configure(mode='indeterminate'); self.progress.start(60)
            self._pulsing = True
        except Exception:
            pass
        self.status.config(text=name)
        return True

    def _end_op(self, msg='Готов'):
        self.busy = False
        try:
            self.progress.stop(); self.progress.configure(mode='determinate')
        except Exception:
            pass
        self._pulsing = False
        self.progress_var.set(0)
        self.prog_label.config(text='')
        for b in self._op_buttons:
            try:
                b.configure(state='normal')
            except Exception:
                pass
        for w in (getattr(self, 'profile_combo', None), getattr(self, 'mode_cb', None)):
            try:
                w.configure(state='readonly')
            except Exception:
                pass
        self.cancel_btn.configure(state='disabled')
        self.status.config(text=msg)

    def _on_verbose(self):
        core.LOG_VERBOSE = bool(self.verbose_var.get())
        self.config['log_verbose'] = self.verbose_var.get()
        self._save_config()

    def _cancel_op(self):
        if self.busy:
            self._cancel.set()
            self.log('Отмена операции…')
            self.cancel_btn.configure(state='disabled')

    # ---------- горячие клавиши в полях ввода независимо от раскладки ----------
    def _install_hotkeys(self):
        """Ctrl+C/V/X/A работают и при не-английской раскладке (по keycode, не keysym)."""
        codes = {67: '<<Copy>>', 86: '<<Paste>>', 88: '<<Cut>>', 65: '<<SelectAll>>'}

        def handler(event):
            if not (event.state & 0x4):        # Control
                return
            virt = codes.get(event.keycode)
            if virt:
                event.widget.event_generate(virt)
                return 'break'
        for cls in ('TEntry', 'Entry', 'Text'):
            self.root.bind_class(cls, '<Control-KeyPress>', handler, add='+')
        # дефолтного <<SelectAll>> у Entry/Text нет — добавим обработчики
        self.root.bind_class('TEntry', '<<SelectAll>>',
                             lambda e: (e.widget.select_range(0, 'end'), 'break')[1], add='+')
        self.root.bind_class('Entry', '<<SelectAll>>',
                             lambda e: (e.widget.select_range(0, 'end'), 'break')[1], add='+')
        self.root.bind_class('Text', '<<SelectAll>>',
                             lambda e: (e.widget.tag_add('sel', '1.0', 'end'), 'break')[1], add='+')

    # ---------- индексация дисковых модов ----------
    def _offer_first_index(self):
        """При старте: если в Mods есть моды, но индекса нет — предложить проиндексировать."""
        if self.busy or self._disk_index is not None:
            return
        if self.config.get('skip_index_offer'):
            return
        try:
            mids = core.scan_installed_mods(self._mods_dir())
        except Exception:
            mids = []
        if not mids:
            return
        ans = messagebox.askyesno(
            'Индексация модов',
            f'В папке Mods найдено модов: {len(mids)}, но они ещё не проиндексированы.\n\n'
            'Проиндексировать сейчас? Лаунчер определит базу, какие моды ему знакомы '
            '(есть в каталоге), какие — нет, и какие изменены относительно каталога.\n\n'
            'Это локально (без скачивания) и нужно один раз; потом — кнопка 🗂.')
        if ans:
            self._reindex()
        else:
            self.config['skip_index_offer'] = True
            self._save_config()

    def _reindex(self):
        if not self._begin_op('Индексация модов…'):
            return
        threading.Thread(target=self._reindex_worker, daemon=True).start()

    def _reindex_worker(self):
        mods_dir = self._mods_dir()
        try:
            cat = getattr(self, '_catalog_cache', None)
            if not cat:
                self.log('Загрузка каталога для классификации…')
                try:
                    cat = core.load_catalog('descriptors/catalog.json', self._repo(),
                                            self._token()) or {}
                    self._catalog_cache = cat
                except core.OperationCancelled:
                    raise
                except Exception as e:
                    self.log(f'[warn] каталог не загружен ({e}) — моды будут «не в каталоге»')
                    cat = {}
            self.log('Индексирую моды на диске…')
            idx = core.index_disk_mods(mods_dir, cat, prev_index=self._disk_index,
                                       log=self.log, progress_cb=self._progress,
                                       should_cancel=self.should_cancel)
            core.save_disk_index(mods_dir, idx)
            self._disk_index = idx
            self.config['skip_index_offer'] = True
            self._post(self._save_config)
            self._post(self._refresh_list)
            base = idx.get('base') or {}
            self.log(f'Индексация готова: всего {idx["count"]}, знакомых каталогу '
                     f'{idx["known"]}, не в каталоге {idx["unknown"]}. '
                     f'База: {base.get("camp", "?")}.')
            self._post(self._end_op, 'Индексация готова')
        except core.OperationCancelled:
            self.log('Индексация отменена.')
            self._post(self._end_op, 'Отменено')
        except Exception as e:
            self.log(f'ОШИБКА индексации: {e}')
            self._post(self._end_op, 'Ошибка индексации')

    def _reindex_one(self, mid):
        """Быстрый реиндекс ОДНОГО мода (после обновления/слияния) — без сети."""
        if not mid or self._disk_index is None:
            return
        try:
            cat = getattr(self, '_catalog_cache', None) or {}
            files, ver = core.disk_mod_fingerprint(self._mods_dir(), mid)
            status = 'known' if cat.get(mid) else 'unknown'
            self._disk_index.setdefault('mods', {})[mid] = {
                'status': status, 'version': ver, 'n_files': len(files), 'files': files}
            core.save_disk_index(self._mods_dir(), self._disk_index)
        except Exception:
            pass

    # ---------- list (единое дерево: Лагерь→Пак→Мод; колонка «Установлен» = есть на диске) ----------
    def _disk_mods(self):
        """{mod_id: дата(isoformat) по mtime папки} — что физически лежит в Mods."""
        import os
        mods_dir = self._mods_dir()
        out = {}
        try:
            for mid in core.scan_installed_mods(mods_dir):
                try:
                    p = mods_dir / mid.replace('/', os.sep)
                    out[mid] = datetime.fromtimestamp(p.stat().st_mtime).isoformat()
                except Exception:
                    out[mid] = ''
        except Exception:
            pass
        return out

    def _get_installed_base(self):
        return getattr(self, '_inst_base', None)

    def _pack_group(self, unit):
        """Юнит-пак для группировки: фикс-пак сворачивается в родительский (фикс — не
        самостоятельный пак, а частые мелкие обновления того же пака)."""
        return (getattr(self, '_fixparent', {}) or {}).get(unit, unit)

    def _tree_mode(self):
        return 'section' if self.tree_mode_var.get() == 'По разделу' else 'folder'

    def _section_of(self, mid):
        """Section из ModuleInfo мода (с кэшем)."""
        if mid not in self._sections:
            p = self._mods_dir() / mid.replace('/', os.sep) / 'ModuleInfo.txt'
            self._sections[mid] = core.read_module_section(p) if p.exists() else ''
        return self._sections[mid]

    def _mod_group(self, mid):
        """Группа мода в дереве: папка-категория из пути ('folder') или раздел из
        ModuleInfo ('section', фолбэк на папку). Едино для дисковых и профильных модов."""
        folder = mid.split('/')[0] if '/' in mid else None
        if self._tree_mode() == 'section':
            return self._section_of(mid) or folder or 'Не указан'
        return folder

    def _disk_place(self, mid):
        """(лагерь, группа) для установленного мода — ТОЛЬКО по данным с диска.
        Лагерь = установленная база."""
        ib = self._get_installed_base()
        camp = ib['camp'] if ib else 'прочее'
        return camp, self._mod_group(mid)

    def _on_tree_mode(self, _e=None):
        self.config['tree_mode'] = self._tree_mode()
        self._save_config()
        self._refresh_list()

    def _refresh_list(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        try:
            self._inst_base = core.detect_installed_base(self._mods_dir())
        except Exception:
            self._inst_base = None
        disk = self._disk_mods()

        def st_of(m, on_disk):
            if m.get('update_available'):
                return '⬆ обновление'
            return '✅ установлен' if on_disk else '➕ добавлен'

        items = []          # (camp, pack, kind, label, status, date, iid)
        seen = set()
        for idx, m in enumerate(self.profile.get('mods', [])):
            typ = m.get('type'); iid = f'p{idx}'
            if typ == 'unit' and m.get('mod'):
                mid = m['mod']; seen.add(mid)
                on = (mid in disk) or bool(m.get('last_downloaded'))
                date = m.get('last_downloaded') or disk.get(mid, '')
                items.append((m.get('camp', 'прочее'), self._mod_group(mid), 'мод',
                              mid.split('/')[-1], st_of(m, on), date if on else '', iid))
            elif typ == 'unit':
                on = bool(m.get('last_downloaded'))
                items.append((m.get('camp', 'прочее'), self._pack_group(m.get('unit')), 'пак',
                              m.get('name', m.get('unit')), st_of(m, on),
                              m.get('last_downloaded', '') if on else '', iid))
            elif typ == 'camp':
                on = bool(m.get('last_downloaded'))
                items.append((m.get('camp', 'прочее'), None, 'лагерь', '★ весь лагерь',
                              st_of(m, on), m.get('last_downloaded', '') if on else '', iid))
            else:
                on = bool(m.get('last_downloaded'))
                items.append(('прочее', None, 'форк' if typ == 'desc' else 'zip',
                              m.get('name') or m.get('id') or m.get('url', ''),
                              st_of(m, on), m.get('last_downloaded', '') if on else '', iid))
        idx_mods = (self._disk_index or {}).get('mods', {})
        for mid, ts in sorted(disk.items()):     # на диске, но не в наборе
            if mid in seen:
                continue
            camp, pack = self._disk_place(mid)
            st = ('❓ не в каталоге'
                  if (idx_mods.get(mid) or {}).get('status') == 'unknown'
                  else '✅ установлен')
            items.append((camp, pack, 'мод', mid.split('/')[-1], st, ts, 'd:' + mid))

        camp_nodes, pack_nodes = {}, {}

        def camp_node(c):
            c = c or 'прочее'
            if c not in camp_nodes:
                camp_nodes[c] = self.tree.insert('', tk.END, text=f'🗂 {c}', open=True)
            return camp_nodes[c]

        def pack_node(c, pk):
            key = (c, pk)
            if key not in pack_nodes:
                pack_nodes[key] = self.tree.insert(camp_node(c), tk.END,
                                                   text=f'■ {pk}', open=True)
            return pack_nodes[key]

        only_attn = getattr(self, 'attention_var', None) and self.attention_var.get()
        for camp, pack, kind, label, status, date, iid in sorted(items, key=lambda x: (
                x[0], x[1] or '', x[3])):
            if only_attn and status.startswith('✅'):     # скрыть просто установленные
                continue
            parent = pack_node(camp, pack) if (pack and kind == 'мод') else camp_node(camp)
            vals = (kind, status, fmt_date(date) if date else '')
            if iid:
                self.tree.insert(parent, tk.END, iid=iid, text=label, values=vals)
            else:
                self.tree.insert(parent, tk.END, text=label, values=vals)

        # лениво подгрузить каталог (для группировки дисковых модов по лагерю), один раз
        if getattr(self, '_catalog_cache', None) is None and not getattr(self, '_cat_loading', False):
            self._cat_loading = True

            def bg():
                try:
                    self._catalog_cache = core.load_catalog(
                        'descriptors/catalog.json', self._repo(), self._token()) or {}
                except Exception:
                    self._catalog_cache = {}
                try:                          # карта фикс→родитель для группировки
                    pk = core.load_packs('state/packs.json', self._repo(), self._token())
                    self._fixparent = {p['name']: p['fix_parent']
                                       for p in pk.values() if p.get('fix_parent')}
                except Exception:
                    self._fixparent = {}
                self._cat_loading = False
                if self._catalog_cache:
                    self._post(self._refresh_list)
            threading.Thread(target=bg, daemon=True).start()

    def _selected(self):
        """Индекс записи профиля для выбранного листа дерева (или None для групп)."""
        s = self.tree.selection()
        if not s:
            return None
        iid = s[0]
        if iid.startswith('p') and iid[1:].isdigit():
            return int(iid[1:])
        return None

    # ---------- profiles ----------
    def _on_profile_change(self, e=None):
        name = self.profile_var.get()
        if name == self.current_profile:
            return
        self._save_profile()
        self.current_profile = name
        self.profile = self._load_profile(name)
        self.game_path_var.set(self.profile.get('game_path', ''))
        self._refresh_list()
        self.log(f'Профиль: {name}')

    def _new_profile(self):
        name = _ask(self.root, 'Новый профиль', 'Имя профиля:')
        if not name:
            return
        if name in self.config['profiles']:
            messagebox.showerror('Ошибка', 'Профиль уже существует')
            return
        self.config['profiles'].append(name)
        self.profile_combo['values'] = self.config['profiles']
        self.profile_var.set(name)
        self._save_profile()
        self._on_profile_change()

    def _save_clicked(self):
        self._save_profile()
        self.log('Профиль сохранён')

    def _del_profile(self):
        name = self.profile_var.get()
        if name == 'default':
            messagebox.showwarning('Нельзя', 'Профиль default удалить нельзя')
            return
        if not messagebox.askyesno('Удалить', f'Удалить профиль {name}?'):
            return
        (PROFILES_DIR / f'{name}.json').unlink(missing_ok=True)
        self.config['profiles'].remove(name)
        self.profile_combo['values'] = self.config['profiles']
        self.profile_var.set('default')
        self.current_profile = 'default'
        self.profile = self._load_profile('default')
        self.game_path_var.set(self.profile.get('game_path', ''))
        self._save_config()
        self._refresh_list()

    # ---------- mods ----------
    def _add_mod(self):
        AddModDialog(self.root, self.theme, self._on_mod_added, self._token(), self._repo())

    def _on_mod_added(self, mod):
        self.profile.setdefault('mods', []).append(mod)
        self._save_profile()
        self._refresh_list()
        self.log(f'Добавлен мод: {mod["name"]}')

    def _get_packs(self, tok):
        """packs.json (тиры юнитов) с кэшем в рамках сессии."""
        if getattr(self, '_packs_cache', None) is None:
            repo = next((m.get('repo') for m in self.profile.get('mods', [])
                         if m.get('type') == 'unit' and m.get('repo')),
                        'ArtYudin89/sr-mods-aggregator')
            self._packs_cache = core.load_packs('state/packs.json', repo, tok)
        return self._packs_cache or {}

    def _get_catalog(self, repo, tok):
        """Каталог модов с кэшем в рамках сессии."""
        if getattr(self, '_catalog_cache', None) is None:
            self._catalog_cache = core.load_catalog('descriptors/catalog.json', repo, tok)
        return self._catalog_cache or {}

    def _invalidate_remote_cache(self):
        """Сбросить кэши каталога/паков/дат — чтобы перечитать с новым токеном/репо."""
        self._catalog_cache = None
        self._packs_cache = None
        self._camp_updated = {}
        self._cat_loading = False

    def _on_creds_change(self, _e=None):
        self._invalidate_remote_cache()
        self._refresh_list()

    def _check_compat(self):
        """Совместимость: паки набора (Фаза 2) + незакрытые зависимости (Dependence)
        установленных на диске модов."""
        units = [f'{m["camp"]}/{m["unit"]}' for m in self.profile.get('mods', [])
                 if m.get('type') == 'unit' and m.get('camp') and m.get('unit')]
        if not self._begin_op('Проверка совместимости…'):
            return
        threading.Thread(target=self._check_compat_worker, args=(units,),
                         daemon=True).start()

    def _check_compat_worker(self, units):
        tok = self._token()
        problems, notes = [], []
        # --- уровень паков набора (если в наборе есть юниты-паки) ---
        if units:
            try:
                packs = self._get_packs(tok)
                ib = core.detect_installed_base(self._mods_dir())
                installed_base = (ib['base'] if ib else None) or self.profile.get('installed_base')
                info = core.check_pack_compatibility(units, packs, installed_base=installed_base)
                bnames = [packs[b]['name'] for b in info['bases']]
                if info['base_conflict']:
                    problems.append('⛔ В наборе НЕСКОЛЬКО базовых паков: ' + ', '.join(bnames) +
                                    '.\n   Нельзя смешивать базы — оставь ровно одну.')
                elif info['missing_base']:
                    problems.append('⚠ В наборе НЕТ базового пака (с Rangers.exe).\n'
                                    '   Модам/фиксам нужна база (redux/original/universe…).')
                elif bnames:
                    notes.append('✅ Базовый пак (в наборе): ' + bnames[0])
                elif info.get('installed_base'):
                    notes.append('✅ Базовый пак уже установлен: ' + info['installed_base'])
                for fix, parent in info['fix_orphans']:
                    problems.append(f'⚠ Фикс «{packs[fix]["name"]}» требует родительский пак '
                                    f'«{parent}», которого нет в наборе.')
                if info['save_warning']:
                    problems.append('💾 ' + info['save_warning'])
                if info['mandatory']:
                    mand = ', '.join(packs[u]['name'] for u in info['mandatory'])
                    notes.append('❗ Базовые/фикс-паки (обновление обязательно):\n   ' + mand)
            except core.OperationCancelled:
                self.log('Проверка отменена.'); self._post(self._end_op, 'Отменено'); return
            except Exception as e:
                self.log(f'Совместимость (паки): ошибка {e}')
        # --- незакрытые зависимости модов на ДИСКЕ (поле Dependence в ModuleInfo) ---
        try:
            self.log('Проверяю зависимости установленных модов (ModuleInfo)…')
            dep_problems = core.check_disk_dependencies(
                self._mods_dir(), progress_cb=self._progress,
                should_cancel=self.should_cancel)
        except core.OperationCancelled:
            self.log('Проверка отменена.'); self._post(self._end_op, 'Отменено'); return
        except Exception as e:
            self.log(f'Совместимость (зависимости): ошибка {e}'); dep_problems = []
        if dep_problems:
            lines = [f'   • {d["mod"]} → нужен: {", ".join(d["missing"])}'
                     for d in dep_problems[:30]]
            extra = f'\n   … и ещё {len(dep_problems) - 30}' if len(dep_problems) > 30 else ''
            problems.append('⚠ НЕУСТАНОВЛЕННЫЕ зависимости модов (Dependence):\n'
                            + '\n'.join(lines) + extra)
        else:
            notes.append('✅ Незакрытых зависимостей (Dependence) у установленных модов не найдено.')
        notes.append('ℹ Смена базового пака делает несовместимыми сейвы текущей партии '
                     '(новую партию начать можно).')

        # --- «Мост»: что из недостающих зависимостей реально есть в каталоге? ---
        bridge = None
        if dep_problems:
            miss = sorted({d for dp in dep_problems for d in dp['missing']})
            try:
                cat = core.load_catalog('descriptors/catalog.json', self._repo(), tok)
                bp = core.resolve_set([{'id': n} for n in miss], cat, self._repo(), tok)
                if bp['order']:
                    bridge = bp
                    head = ', '.join(bp['order'][:20])
                    more = f' … (+{len(bp["order"]) - 20})' if len(bp['order']) > 20 else ''
                    problems.append('🧩 Доступно к установке из каталога: ' + head + more)
                if bp['missing_deps']:
                    problems.append('❓ Нет в каталоге (поставить нельзя): '
                                    + ', '.join(bp['missing_deps'][:20]))
            except core.OperationCancelled:
                self.log('Проверка отменена.'); self._post(self._end_op, 'Отменено'); return
            except Exception as e:
                self.log(f'Мост зависимостей: не удалось разрешить ({e})')

        title = 'Совместимость: проблемы' if problems else 'Совместимость: ок'
        body = ''
        if problems:
            body += 'НАЙДЕНЫ ВОПРОСЫ:\n\n' + '\n\n'.join(problems) + '\n\n'
        body += '— — —\n' + '\n'.join(notes)
        self.log('Совместимость: ' + ('проблемы' if problems else 'ок'))
        self._post(self._compat_result, title, body, bool(problems), bridge)

    def _compat_result(self, title, body, problems, bridge):
        """Показать отчёт совместимости и, если недостающие зависимости есть в
        каталоге, предложить «мост» — доустановить их (через тот же резолвер/инсталлер,
        что и «🧩 Установить набор»)."""
        (messagebox.showwarning if problems else messagebox.showinfo)(title, body)
        if bridge and bridge.get('order'):
            n = len(bridge['order'])
            deps = set(bridge.get('added_deps') or [])
            lines = ['  • ' + m + ('   (зависимость)' if m in deps else '')
                     for m in bridge['order'][:25]]
            if n > 25:
                lines.append(f'  … и ещё {n - 25}')
            extra = ('\n\n⚠ Не в каталоге (пропустим): ' + ', '.join(bridge['missing_deps'])
                     if bridge.get('missing_deps') else '')
            if messagebox.askyesno(
                    'Доустановить зависимости',
                    f'Недостающие зависимости можно поставить из каталога ({n}):\n\n'
                    + '\n'.join(lines) + extra + '\n\nУстановить сейчас?'):
                self.log(f'Мост зависимостей: устанавливаю {n} модов…')
                threading.Thread(
                    target=self._deps_install_worker,
                    args=(bridge, self._repo(), self._token()), daemon=True).start()
                return
        self._end_op('Готов')

    def _remove_mod(self):
        i = self._selected()
        if i is None:
            return
        name = self.profile['mods'][i]['name']
        if messagebox.askyesno('Удалить', f'Удалить «{name}» из списка?'):
            del self.profile['mods'][i]
            self._save_profile()
            self._refresh_list()

    def _move_up(self):
        i = self._selected()
        if i and i > 0:
            m = self.profile['mods']
            m[i - 1], m[i] = m[i], m[i - 1]
            self._save_profile(); self._refresh_list()
            try: self.tree.selection_set(f'p{i - 1}')
            except Exception: pass

    def _move_down(self):
        i = self._selected()
        if i is not None and i < len(self.profile['mods']) - 1:
            m = self.profile['mods']
            m[i + 1], m[i] = m[i], m[i + 1]
            self._save_profile(); self._refresh_list()
            try: self.tree.selection_set(f'p{i + 1}')
            except Exception: pass

    def _refresh_remote(self):
        if not self._begin_op('Проверка обновлений…'):
            return
        self._invalidate_remote_cache()        # ⟳ форсит свежий каталог (камп подтянется)
        threading.Thread(target=self._refresh_remote_worker, daemon=True).start()

    def _latest_version(self, cat, mod_id, source):
        """Версия мода из каталога для конкретного источника (или дефолтного)."""
        ent = cat.get(mod_id)
        if not ent:
            return None
        src = source or ent.get('default_source')
        return next((v['version'] for v in ent['variants'] if v['source'] == src), None) \
            or (ent['variants'][0]['version'] if ent['variants'] else None)

    def _refresh_remote_worker(self):
        tok = self._token()
        cat_cache = {}

        def catalog(repo, ref='descriptors/catalog.json'):
            key = (repo, ref)
            if key not in cat_cache:
                cat_cache[key] = core.load_catalog(ref, repo, tok)
            return cat_cache[key]

        for m in self.profile.get('mods', []):
            if self.should_cancel():
                break
            try:
                typ = m.get('type')
                repo = m.get('repo', self._repo())
                # есть резолвимый id каталога (desc по id, или unit с конкретным модом)
                cat_id = m.get('id') if typ == 'desc' else (
                    m.get('mod') if typ == 'unit' and m.get('mod') else None)
                if cat_id:
                    src = m.get('source') or (f"{m['camp']}/{m['unit']}"
                                              if typ == 'unit' else None)
                    ver = self._latest_version(catalog(repo), cat_id, src)
                    m['latest_version'] = ver
                    m['update_available'] = bool(m.get('installed_version') and ver
                                                 and m['installed_version'] != ver)
                elif typ == 'unit':                 # пак целиком — по дате обновления
                    up = core.unit_remote_updated(repo, m['camp'], tok)
                    if up:
                        m['last_updated'] = up
                        m['update_available'] = bool(m.get('last_downloaded')
                                                     and up > m['last_downloaded'])
                elif typ == 'camp':
                    up = core.unit_remote_updated(repo, m['camp'], tok)
                    if up:
                        m['last_updated'] = up
                        m['update_available'] = bool(m.get('last_downloaded')
                                                     and up > m['last_downloaded'])
                elif typ == 'zip':
                    up = core.resolve_zip(m['url'], tok).get('updated')
                    if up:
                        m['last_updated'] = up
            except Exception as e:
                self.log(f'{m.get("name","?")}: ошибка обновления ({e})')
        self._post(self._save_profile)
        self._post(self._refresh_list)
        cancelled = self.should_cancel()
        self.log('Проверка отменена' if cancelled else 'Информация об обновлениях получена')
        self._post(self._end_op, 'Отменено' if cancelled else 'Готов')

    # ---------- install ----------
    def _install(self, all_mods):
        mods = self.profile.get('mods', [])
        if all_mods:
            if not mods:
                messagebox.showinfo(
                    'Набор пуст',
                    'В вашем наборе (📋) ничего нет — устанавливать нечего.\n'
                    'Добавьте моды/паки кнопкой ＋. (Моды из «💾 Установлено в игре» — '
                    'это уже стоящее на диске, не набор.)')
                return
            # пропустить уже установленные и не требующие обновления (идемпотентность)
            need = [i for i, m in enumerate(mods)
                    if not (m.get('last_downloaded') and not m.get('update_available'))]
            skipped = len(mods) - len(need)
            if not need:
                messagebox.showinfo(
                    'Всё актуально',
                    f'Все элементы набора ({len(mods)}) уже установлены и без обновлений '
                    '— скачивать нечего.\n\nЧтобы переустановить конкретный мод принудительно — '
                    'выделите его и «Установить выбранный».')
                return
            names = [mods[i].get('name') or mods[i].get('id') or mods[i].get('url', '?')
                     for i in need]
            preview = '\n'.join(f'  • {n}' for n in names[:15])
            if len(names) > 15:
                preview += f'\n  … и ещё {len(names) - 15}'
            tail = (f'\n\nПропущено как уже актуальные: {skipped}.' if skipped else '')
            if not messagebox.askyesno(
                    'Установить набор',
                    f'Будет установлено: {len(need)}\n\n{preview}{tail}\n\n'
                    'Продолжить? (Перезапишет файлы этих модов в Mods.)'):
                return
            targets = need
        else:
            i = self._selected()
            if i is None:
                msg = ('Выберите мод из вашего НАБОРА (раздел 📋 Набор).\n\n'
                       'Кнопки «Установить» работают с набором (что вы добавили через ＋).\n'
                       'Если выделен мод из «💾 Установлено в игре» — он уже на диске; '
                       'чтобы обновить его с сохранением правок, жмите «🔀 Обновить».')
                messagebox.showwarning('Выбор', msg)
                return
            targets = [i]
        if not targets:
            return
        if not self._begin_op('Установка…'):
            return
        threading.Thread(target=self._install_worker, args=(targets,), daemon=True).start()

    def _install_worker(self, targets):
        tok = self._token()
        mods_dir = self._mods_dir()
        try:
            desc_idx = [i for i in targets if self.profile['mods'][i].get('type') == 'desc']
            other_idx = [i for i in targets if self.profile['mods'][i].get('type') != 'desc']
            for i in other_idx:
                m = self.profile['mods'][i]
                self.log(f'=== {m["name"]} ===')
                try:
                    if m.get('type') == 'camp':
                        packs = self._get_packs(tok)
                        units = sorted([p for p in packs.values() if p['camp'] == m['camp']],
                                       key=lambda p: p.get('load_order', 999))
                        if not units:
                            self.log(f'  нет паков для лагеря {m["camp"]}')
                        for p in units:
                            self.log(f'--- {p["name"]} ({p["tier"]}) ---')
                            try:
                                core.reconstruct_unit(m['repo'], p['camp'], p['name'],
                                                      mods_dir, tok, self._progress, self.log,
                                                      tmp_dir=HERE, should_cancel=self.should_cancel)
                                if p.get('tier') == 'base':
                                    self.profile['installed_base'] = p['name']
                            except core.OperationCancelled:
                                raise
                            except Exception as e:
                                self.log(f'ОШИБКА {p["name"]}: {e}')
                        m['last_downloaded'] = datetime.now().isoformat()
                        self._post(self._save_profile); self._post(self._refresh_list)
                        continue
                    if m.get('type') == 'unit':
                        st = core.reconstruct_unit(m['repo'], m['camp'], m['unit'],
                                                   mods_dir, tok, self._progress, self.log,
                                                   tmp_dir=HERE, mod=m.get('mod') or None,
                                                   should_cancel=self.should_cancel)
                        m['last_updated'] = st.get('updated') or m.get('last_updated')
                    else:
                        up = core.install_zip(m['url'], mods_dir, tok, self._progress,
                                              self.log, tmp_dir=HERE,
                                              should_cancel=self.should_cancel)
                        m['last_updated'] = up or m.get('last_updated')
                    m['last_downloaded'] = datetime.now().isoformat()
                    m['update_available'] = False
                    if m.get('type') == 'unit':            # запомнить установленную базу
                        pk = self._get_packs(tok).get(f'{m["camp"]}/{m["unit"]}', {})
                        if pk.get('tier') == 'base':
                            self.profile['installed_base'] = pk['name']
                        if m.get('mod'):                   # версия мода для детекции обновлений
                            try:
                                cat = self._get_catalog(m.get('repo', self._repo()), tok)
                                m['installed_version'] = self._latest_version(
                                    cat, m['mod'], f"{m['camp']}/{m['unit']}")
                            except Exception:
                                pass
                    self._post(self._save_profile)
                    self._post(self._refresh_list)
                except core.OperationCancelled:
                    raise
                except Exception as e:
                    self.log(f'ОШИБКА {m["name"]}: {e}')
            if desc_idx:
                self._install_desc_set(desc_idx, mods_dir, tok)
            self.log('Готово')
            self._post(self._end_op, 'Готово')
        except core.OperationCancelled:
            self.log('Установка отменена.')
            self._post(self._end_op, 'Отменено')
        except Exception as e:
            self.log(f'ОШИБКА установки: {e}')
            self._post(self._end_op, 'Ошибка')

    def _desc_sel(self, m):
        if m.get('url'):
            return {'url': m['url']}
        sel = {'id': m['id']}
        if m.get('source'):
            sel['source'] = m['source']
        return sel

    def _install_desc_set(self, desc_idx, mods_dir, tok):
        """Разрешить набор дескрипторов (зависимости+конфликты) и установить."""
        mods = self.profile['mods']
        repo = mods[desc_idx[0]].get('repo', 'ArtYudin89/sr-mods-aggregator')
        cat_ref = mods[desc_idx[0]].get('catalog', 'descriptors/catalog.json')
        self.log(f'=== Набор: {len(desc_idx)} мод(ов) — разрешаю зависимости ===')
        try:
            cat = core.load_catalog(cat_ref, repo, tok)
            sels = [self._desc_sel(mods[i]) for i in desc_idx]
            plan = core.resolve_set(sels, cat, repo, tok)
        except Exception as e:
            self.log(f'ОШИБКА разрешения набора: {e}')
            return
        if plan['added_deps']:
            self.log(f'➕ подтянуты зависимости: {", ".join(plan["added_deps"])}')
        if plan['missing_deps']:
            self.log(f'⚠ НЕ найдены зависимости: {", ".join(plan["missing_deps"])}')
        if plan['conflicts']:
            for a, b in plan['conflicts']:
                self.log(f'⚠ КОНФЛИКТ: {a} ⟷ {b}')
            self.log('(конфликты показаны; устанавливаю как просили — решение за вами)')
        self.log(f'Набор: {len(plan["order"])} модов к установке')
        try:
            idx_url = next((d.get('chunk_index_url') for d in plan['mods'].values()
                            if d.get('chunk_index_url')), None)
            index = core.load_chunk_index(url=idx_url, repo=repo, token=tok)
            results = core.install_set(plan, mods_dir, index, token=tok,
                                       log=self.log, tmp_dir=HERE,
                                       should_cancel=self.should_cancel)
        except Exception as e:
            self.log(f'ОШИБКА установки набора: {e}')
            return
        # обновить установленные версии у запрошенных модов профиля
        now = datetime.now().isoformat()
        for i in desc_idx:
            m = mods[i]
            mid = m.get('id')
            if mid and mid in plan['mods']:
                m['installed_version'] = plan['mods'][mid].get('version')
                m['last_updated'] = m['installed_version']
            m['last_downloaded'] = now
        self._post(self._save_profile)
        self._post(self._refresh_list)
        self.log(f'Набор установлен: {len(results)} модов')

    # ---------- установка с резолвом зависимостей (Dependence) ----------
    def _install_with_deps(self):
        """Поставить набор + АВТОМАТИЧЕСКИ подтянуть зависимости (Dependence). Записи
        набора (desc и unit-моды) приводятся к каталожным выборам; resolve_set строит
        замыкание зависимостей, ставим дескрипторами (deps подтягиваются единообразно)."""
        sels = []
        for m in self.profile.get('mods', []):
            t = m.get('type')
            if t == 'desc':
                sels.append(self._desc_sel(m))
            elif t == 'unit' and m.get('mod'):
                sel = {'id': m['mod']}
                if m.get('camp') and m.get('unit'):
                    sel['source'] = f"{m['camp']}/{m['unit']}"
                sels.append(sel)
        if not sels:
            messagebox.showinfo(
                'Зависимости',
                'В наборе нет модов с каталожным id для резолва зависимостей.\n'
                'Добавьте моды из каталога (＋ → Мод) или по ссылке-форку. '
                'Для паков целиком/лагерей зависимости не резолвятся.')
            return
        if not self._begin_op('Резолв зависимостей…'):
            return
        threading.Thread(target=self._resolve_deps_worker, args=(sels,), daemon=True).start()

    def _resolve_deps_worker(self, sels):
        repo, tok = self._repo(), self._token()
        try:
            cat = core.load_catalog('descriptors/catalog.json', repo, tok)
            plan = core.resolve_set(sels, cat, repo, tok)
        except core.OperationCancelled:
            self.log('Отменено.'); self._post(self._end_op, 'Отменено'); return
        except Exception as e:
            self.log(f'ОШИБКА разрешения зависимостей: {e}')
            self._post(self._end_op, 'Ошибка'); return
        if plan['added_deps']:
            self.log(f'➕ зависимости (Dependence): {", ".join(plan["added_deps"])}')
        if plan['missing_deps']:
            self.log(f'⚠ зависимости не в каталоге: {", ".join(plan["missing_deps"])}')
        for a, b in plan['conflicts']:
            self.log(f'⚠ КОНФЛИКТ: {a} ⟷ {b}')
        self._post(self._confirm_deps_install, plan, repo, tok)

    def _confirm_deps_install(self, plan, repo, tok):
        order = plan['order']
        deps = set(plan.get('added_deps') or [])
        lines = []
        for mid in order[:25]:
            lines.append(f'  • {mid}' + ('   (зависимость)' if mid in deps else ''))
        if len(order) > 25:
            lines.append(f'  … и ещё {len(order) - 25}')
        extra = ''
        if plan['missing_deps']:
            extra += f'\n\n⚠ НЕ найдены в каталоге: {", ".join(plan["missing_deps"])}'
        if plan['conflicts']:
            extra += '\n\n⚠ Конфликты (показаны, не снимаются): ' + \
                     '; '.join(f'{a}⟷{b}' for a, b in plan['conflicts'])
        ok = messagebox.askyesno(
            'Установить с зависимостями',
            f'Будет установлено модов: {len(order)} '
            f'(из них зависимостей: {len(deps)})\n\n' + '\n'.join(lines) + extra +
            '\n\nПродолжить?')
        if not ok:
            self.log('Установка отменена.'); self._end_op('Отменено'); return
        threading.Thread(target=self._deps_install_worker, args=(plan, repo, tok),
                         daemon=True).start()

    def _deps_install_worker(self, plan, repo, tok):
        mods_dir = self._mods_dir()
        try:
            idx_url = next((d.get('chunk_index_url') for d in plan['mods'].values()
                            if d.get('chunk_index_url')), None)
            index = core.load_chunk_index(url=idx_url, repo=repo, token=tok)
            results = core.install_set(plan, mods_dir, index, token=tok, log=self.log,
                                       tmp_dir=HERE, should_cancel=self.should_cancel)
        except core.OperationCancelled:
            self.log('Установка отменена.'); self._post(self._end_op, 'Отменено'); return
        except Exception as e:
            self.log(f'ОШИБКА установки: {e}'); self._post(self._end_op, 'Ошибка'); return
        # отметить установленные версии у совпавших записей набора
        now = datetime.now().isoformat()
        for m in self.profile.get('mods', []):
            mid = m.get('id') or m.get('mod')
            if mid and mid in plan['mods']:
                m['installed_version'] = plan['mods'][mid].get('version')
                m['last_updated'] = m['installed_version']
                m['last_downloaded'] = now
                m['update_available'] = False
        self._post(self._save_profile)
        self._post(self._refresh_list)
        self.log(f'Готово: установлено {len(results)} модов (с зависимостями).')
        self._post(self._end_op, 'Готово')

    # ---------- Фаза 4: обновление с сохранением правок игрока (3-way merge) ----------
    def _selected_disk(self):
        """mod_id для выбранного ЛИСТА дискового мода (iid 'd:<mid>') или None."""
        s = self.tree.selection()
        if s and s[0].startswith('d:'):
            return s[0][2:]
        return None

    def _update_merge(self):
        """Обновить ВЫБРАННЫЕ моды, сохранив ручные правки игрока (3-way merge).
        Поддержаны desc-моды из набора и дисковые моды (ветка «Установлено в игре»).
        Несколько выделенных строк обрабатываются по очереди."""
        targets = []           # [('profile', idx) | ('disk', mid)]
        skipped = 0
        for iid in self.tree.selection():
            if iid.startswith('p') and iid[1:].isdigit():
                if self.profile['mods'][int(iid[1:])].get('type') == 'desc':
                    targets.append(('profile', int(iid[1:])))
                else:
                    skipped += 1
            elif iid.startswith('d:'):
                targets.append(('disk', iid[2:]))
        if not targets:
            messagebox.showinfo(
                'Обновление',
                'Выберите мод-лист дерева для обновления с сохранением правок:\n'
                '• мод из вашего набора (📋), добавленный из каталога/по ссылке, или\n'
                '• мод из «💾 Установлено в игре».\n\n'
                'Для паков/лагерей целиком используйте «Установить».')
            return
        if not self._begin_op(f'Обновление ({len(targets)})…'):
            return
        if skipped:
            self.log(f'Пропущено {skipped} (паки/лагеря обновляются через «Установить»).')
        self._merge_queue = targets
        self._merge_advance()

    def _merge_advance(self):
        """Взять следующий мод из очереди обновления (главный поток)."""
        if self.should_cancel():
            self._merge_queue = []
        if not getattr(self, '_merge_queue', None):
            self._end_op('Готово')
            return
        kind, ref = self._merge_queue.pop(0)
        if kind == 'profile':
            threading.Thread(target=self._plan_merge_worker, args=(ref,), daemon=True).start()
        else:
            threading.Thread(target=self._plan_merge_disk_worker, args=(ref,), daemon=True).start()

    def _merge_after_item(self, msg):
        """Завершить текущий мод и перейти к следующему (или закончить операцию)."""
        self.status.config(text=msg)
        self._merge_advance()

    def _plan_merge_disk_worker(self, mid):
        """Дисковый мод: подобрать вариант каталога по совпадению файлов, спланировать
        обновление (база — снимок, если есть; иначе пусто → отличия = конфликты)."""
        repo, tok = self._repo(), self._token()
        mods_dir = self._mods_dir()
        self.log(f'=== Обновление дискового мода: {mid} ===')
        try:
            cat = core.load_catalog('descriptors/catalog.json', repo, tok)
            ib = self._get_installed_base()
            prefer = ib['camp'] if ib else None
            self.log('Подбираю вариант мода по файлам на диске…')
            desc, info = core.pick_disk_variant(cat, mid, mods_dir, repo, tok,
                                                prefer_camp=prefer, log=self.log,
                                                should_cancel=self.should_cancel)
            if not desc:
                reason = (info or {}).get('reason')
                if reason == 'load_failed':
                    self.log(f'Мод {mid}: не удалось загрузить дескриптор (сеть?). '
                             'Повторите — запросы теперь с ретраями.')
                else:
                    self.log(f'Мод {mid} не найден в каталоге — обновить нечем.')
                self._post(self._merge_after_item, 'Не найдено'); return
            self.log(f'Вариант: {info["source"]} (совпало файлов {info["match"]}/{info["cover"]} '
                     f'из {info["total"]})')
            snap = core.load_install_snapshot(mods_dir, desc.get('id'))
            index = core.load_chunk_index(desc=desc, repo=repo, token=tok)
            plan = core.plan_update_merge(desc, mods_dir, index, token=tok, log=self.log,
                                          snapshot=snap, tmp_dir=HERE, progress_cb=self._progress,
                                          should_cancel=self.should_cancel)
        except core.OperationCancelled:
            self.log('Планирование отменено.'); self._post(self._merge_after_item, 'Отменено'); return
        except Exception as e:
            self.log(f'ОШИБКА планирования обновления: {e}')
            self._post(self._merge_after_item, 'Ошибка'); return
        s = plan['summary']
        actionable = sum(s.get(k, 0) for k in
                         ('add', 'update', 'automerge', 'deleted_clean')) + s.get('conflicts', 0)
        if actionable == 0:
            self.log(f'{mid}: файлы совпадают с каталогом — обновлять нечего.')
            self._post(self._merge_after_item, 'Актуально'); return
        # индекс профиля тут не нужен — передаём -1 (apply не трогает профиль для диска)
        self._post(self._open_merge_dialog, -1, desc, plan, index)

    def _plan_merge_worker(self, i):
        m = self.profile['mods'][i]
        repo, tok = self._repo(), self._token()
        mods_dir = self._mods_dir()
        self.log(f'=== Обновление с сохранением правок: {m.get("name", m.get("id", "?"))} ===')
        try:
            cat = {}
            if not m.get('url'):
                cat = core.load_catalog(m.get('catalog', 'descriptors/catalog.json'), repo, tok)
            desc = core.descriptor_for(self._desc_sel(m), cat, repo, tok)
            if not desc:
                self.log('Не удалось получить дескриптор мода (нет в каталоге?).')
                self._post(self._merge_after_item, 'Не найдено'); return
            snap = core.load_install_snapshot(mods_dir, desc.get('id'))
            if snap is None:
                self.log('Нет снимка прошлой установки — поставьте мод лаунчером один раз '
                         '(или используйте обновление для дискового мода из «Установлено в игре»).')
                self._post(self._merge_after_item, 'Нет снимка'); return
            index = core.load_chunk_index(desc=desc, repo=repo, token=tok)
            plan = core.plan_update_merge(desc, mods_dir, index, token=tok,
                                          log=self.log, snapshot=snap, tmp_dir=HERE,
                                          progress_cb=self._progress,
                                          should_cancel=self.should_cancel)
        except core.OperationCancelled:
            self.log('Планирование отменено.'); self._post(self._merge_after_item, 'Отменено'); return
        except Exception as e:
            self.log(f'ОШИБКА планирования обновления: {e}')
            self._post(self._merge_after_item, 'Ошибка'); return
        s = plan['summary']
        actionable = sum(s.get(k, 0) for k in
                         ('add', 'update', 'automerge', 'deleted_clean')) + s.get('conflicts', 0)
        if actionable == 0:
            self.log('Изменений для применения нет (всё совпадает / только ваши правки).')
            self._post(self._merge_after_item, 'Актуально'); return
        self._post(self._open_merge_dialog, i, desc, plan, index)

    def _open_merge_dialog(self, i, desc, plan, index):
        def on_apply(decisions):
            self.status.config(text='Применение обновления…')
            threading.Thread(target=self._apply_merge_worker,
                             args=(i, desc, plan, index, decisions), daemon=True).start()

        def on_cancel():
            self.log(f'{plan.get("id")}: пропущено (изменения не применялись).')
            self._merge_after_item('Пропущено')
        MergePreviewDialog(self.root, self.theme, plan, on_apply, on_cancel)

    def _apply_merge_worker(self, i, desc, plan, index, decisions):
        mods_dir = self._mods_dir()
        try:
            stats = core.apply_update_plan(desc, plan, decisions, mods_dir, index,
                                           token=self._token(), log=self.log,
                                           tmp_dir=HERE, progress_cb=self._progress,
                                           should_cancel=self.should_cancel)
        except core.OperationCancelled:
            self.log('Применение отменено (часть файлов могла быть записана).')
            self._post(self._merge_after_item, 'Отменено'); return
        except Exception as e:
            self.log(f'ОШИБКА применения обновления: {e}')
            self._post(self._merge_after_item, 'Ошибка'); return
        if i is not None and i >= 0:        # дисковый мод (i=-1) не в профиле
            m = self.profile['mods'][i]
            m['installed_version'] = plan.get('version_new')
            m['last_updated'] = plan.get('version_new')
            m['last_downloaded'] = datetime.now().isoformat()
            self._post(self._save_profile)
        # обновить индекс этого мода (на диске теперь новая версия/слияния)
        self._post(self._reindex_one, desc.get('id'))
        self._post(self._refresh_list)
        self.log(f'Обновлено {desc.get("id")}: записано {stats["written"]}, слито {stats["merged"]}, '
                 f'оставлено {stats["kept"]}, удалено {stats["deleted"]}, '
                 f'рядом .srnew {stats["sidecar"]}, конфликтов решено {stats["conflict"]}.')
        self._post(self._merge_after_item, 'Обновлено')

    def _clear_mods(self):
        if self.busy:
            return
        d = self._mods_dir()
        n = sum(1 for _ in d.rglob('*') if _.is_file())
        if n == 0:
            messagebox.showinfo('Пусто', 'Папка Mods уже пуста')
            return
        if not messagebox.askyesno('Очистить', f'Удалить всё из {d}?\n({n} файлов)'):
            return
        import shutil
        for it in d.iterdir():
            shutil.rmtree(it, ignore_errors=True) if it.is_dir() else it.unlink(missing_ok=True)
        for m in self.profile.get('mods', []):
            m['last_downloaded'] = None
        self._save_profile(); self._refresh_list()
        self.log(f'Очищено: {n} файлов')

    # ---------- game ----------
    def _browse_game(self):
        p = filedialog.askdirectory(title='Папка игры (где лежит Rangers.exe)')
        if p:
            self.game_path_var.set(p)
            self._sections = {}
            self._refresh_list()

    def _launch(self):
        root = self._game_root()
        if not root:
            messagebox.showerror('Ошибка', 'Укажите папку игры'); return
        exe = root / 'Rangers.exe'
        if not exe.exists():
            messagebox.showerror('Ошибка', f'Rangers.exe не найден в {root}'); return
        try:
            subprocess.Popen(['Rangers.exe'], cwd=str(root), shell=True)
            self.log(f'Запуск: {exe}')
        except Exception as e:
            messagebox.showerror('Ошибка', str(e))

    def _open_mods(self):
        d = self._mods_dir()
        os.startfile(d) if sys.platform == 'win32' else subprocess.Popen(['xdg-open', str(d)])

    # ---------- дерево: раскрытие / контекстное меню ----------
    def _set_open_all(self, opened):
        def walk(it):
            for c in self.tree.get_children(it):
                self.tree.item(c, open=opened); walk(c)
        walk('')

    def _expand_all(self):
        self._set_open_all(True)

    def _collapse_all(self):
        self._set_open_all(False)

    def _path_for_iid(self, iid):
        """Относительный путь мода под Mods для строки дерева (или None)."""
        if iid.startswith('d:'):
            return iid[2:]
        if iid.startswith('p') and iid[1:].isdigit():
            m = self.profile['mods'][int(iid[1:])]
            if m.get('type') == 'unit' and m.get('mod'):
                return m['mod']
        return None

    def _tree_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        path = self._path_for_iid(iid)
        menu = tk.Menu(self.root, tearoff=0)
        # «Обновить с сохранением правок» — для листа desc-мода или дискового мода
        if (not self.busy and (iid.startswith('d:')
                or (iid.startswith('p') and iid[1:].isdigit()
                    and self.profile['mods'][int(iid[1:])].get('type') == 'desc'))):
            menu.add_command(label='🔀 Обновить (сохранить правки)', command=self._update_merge)
            menu.add_separator()
        if path:
            menu.add_command(label='📂 Открыть папку мода',
                             command=lambda: self._open_mod_folder(path))
        menu.add_command(label='📂 Открыть папку Mods', command=self._open_mods)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_mod_folder(self, relpath):
        d = self._mods_dir() / relpath.replace('/', os.sep)
        if not d.exists():
            messagebox.showinfo('Папка мода', f'Не найдена на диске:\n{relpath}')
            return
        os.startfile(d) if sys.platform == 'win32' else subprocess.Popen(['xdg-open', str(d)])

    def _on_close(self):
        self.config['last_profile'] = self.current_profile
        self._save_profile()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


class _Tooltip:
    """Простая всплывающая подсказка при наведении на виджет."""
    def __init__(self, widget, text):
        self.w = widget; self.text = text; self.tip = None
        widget.bind('<Enter>', self._show)
        widget.bind('<Leave>', self._hide)

    def _show(self, _=None):
        if self.tip or not self.text:
            return
        x = self.w.winfo_rootx() + 18
        y = self.w.winfo_rooty() + self.w.winfo_height() + 2
        self.tip = tk.Toplevel(self.w)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f'+{x}+{y}')
        tk.Label(self.tip, text=self.text, bg='#ffffe0', fg='#000', relief=tk.SOLID,
                 borderwidth=1, font=('Segoe UI', 9), padx=5, pady=2).pack()

    def _hide(self, _=None):
        if self.tip:
            self.tip.destroy(); self.tip = None


def _ask(parent, title, prompt):
    """Простой модальный ввод строки."""
    dlg = tk.Toplevel(parent); dlg.title(title); dlg.transient(parent); dlg.grab_set()
    ttk.Label(dlg, text=prompt).pack(padx=20, pady=(16, 4))
    var = tk.StringVar()
    e = ttk.Entry(dlg, textvariable=var, width=30); e.pack(padx=20); e.focus_set()
    out = {}
    def ok(): out['v'] = var.get().strip(); dlg.destroy()
    ttk.Button(dlg, text='OK', command=ok, style='Accent.TButton').pack(pady=14)
    dlg.bind('<Return>', lambda e: ok())
    parent.wait_window(dlg)
    return out.get('v')


ALL_CAMP = '— весь лагерь —'
ALL_PACK = '— весь пак —'


class AddModDialog:
    """Добавление каскадом Лагерь → Пак → Мод (с «весь лагерь/весь пак» по умолчанию),
    либо по ссылке на форк. Данные — packs.json (лагеря/паки) и каталог (моды)."""
    def __init__(self, parent, theme, on_ok, token='', repo='ArtYudin89/sr-mods-aggregator'):
        self.on_ok = on_ok
        self.token = token
        self.repo = repo
        self.cp = {}             # {camp: [pack dict]}
        self.pack_map = {}       # label -> pack dict (для текущего лагеря)
        self.dlg = d = tk.Toplevel(parent)
        d.title('Добавить'); d.transient(parent); d.grab_set(); d.geometry('560x300')

        self.mode = tk.StringVar(value='src')
        tf = ttk.Frame(d); tf.pack(fill=tk.X, padx=14, pady=(14, 6))
        ttk.Label(tf, text='Источник:').pack(side=tk.LEFT)
        ttk.Radiobutton(tf, text='Из репозитория', variable=self.mode, value='src',
                        command=self._switch).pack(side=tk.LEFT, padx=8)
        ttk.Radiobutton(tf, text='По ссылке (форк)', variable=self.mode, value='fork',
                        command=self._switch).pack(side=tk.LEFT, padx=8)

        self.camp_var = tk.StringVar(); self.pack_var = tk.StringVar()
        self.mod_var = tk.StringVar(); self.url_var = tk.StringVar()

        # --- блок «из репозитория»: три каскадных поля ---
        self.src = ttk.Frame(d)
        self.camp_combo = self._combo(self.src, 'Лагерь:', self.camp_var, self._on_camp)
        self.pack_combo = self._combo(self.src, 'Пак:', self.pack_var, self._on_pack)
        self.mod_combo = self._combo(self.src, 'Мод:', self.mod_var, None)
        self.pack_combo.configure(state='disabled')
        self.mod_combo.configure(state='disabled')
        # --- блок «по ссылке» ---
        self.fork = ttk.Frame(d)
        r = ttk.Frame(self.fork); r.pack(fill=tk.X, pady=5)
        ttk.Label(r, text='URL дескриптора:', width=14).pack(side=tk.LEFT)
        ttk.Entry(r, textvariable=self.url_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(self.fork, text='Прямая ссылка на <id>.json форка мода.').pack(anchor=tk.W)

        self.status = ttk.Label(d, text='Загрузка списка паков…')
        self.status.pack(side=tk.BOTTOM, anchor=tk.W, padx=14, pady=(0, 6))
        bf = ttk.Frame(d); bf.pack(side=tk.BOTTOM, pady=8)
        ttk.Button(bf, text='Добавить', style='Accent.TButton',
                   command=self._ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text='Отмена', command=self.dlg.destroy).pack(side=tk.LEFT)
        self._switch()
        threading.Thread(target=self._load_packs, daemon=True).start()

    def _combo(self, parent, label, var, on_pick):
        r = ttk.Frame(parent); r.pack(fill=tk.X, pady=5)
        ttk.Label(r, text=label, width=14).pack(side=tk.LEFT)
        cb = ttk.Combobox(r, textvariable=var, state='readonly')
        cb.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if on_pick:
            cb.bind('<<ComboboxSelected>>', on_pick)
        return cb

    def _switch(self):
        self.src.pack_forget(); self.fork.pack_forget()
        if self.mode.get() == 'fork':
            self.fork.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)
        else:
            self.src.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)

    # --- данные ---
    def _alive(self):
        try:
            return bool(self.dlg.winfo_exists())
        except Exception:
            return False

    def _set_status(self, text):
        if self._alive() and self.status.winfo_exists():
            self.status.config(text=text)

    def _load_packs(self):
        try:
            cp = core.camp_packs(core.load_packs('state/packs.json', self.repo, self.token))
        except Exception as e:
            self.dlg.after(0, lambda: self._set_status(f'Ошибка загрузки паков: {e}'))
            return
        def apply():
            if not self._alive():
                return
            self.cp = cp
            self._set_status(f'Лагерей: {len(cp)} · паков: {sum(len(v) for v in cp.values())}')
            self.camp_combo['values'] = sorted(cp)
        self.dlg.after(0, apply)

    def _on_camp(self, _e=None):
        camp = self.camp_var.get()
        packs = self.cp.get(camp, [])
        self.pack_map = {f"{p['name']}  [{p['unit']}]": p for p in packs}
        self.pack_combo.configure(state='readonly')
        self.pack_combo['values'] = [ALL_CAMP] + list(self.pack_map)
        self.pack_var.set(ALL_CAMP)
        self.mod_var.set('')
        self.mod_combo.configure(state='disabled')   # пак=весь лагерь -> мод недоступен

    def _on_pack(self, _e=None):
        self.mod_var.set('')
        if self.pack_var.get() == ALL_CAMP:
            self.mod_combo.configure(state='disabled')
            return
        p = self.pack_map.get(self.pack_var.get())
        if not p:
            return
        self._set_status('Загрузка модов пака…')

        def work():
            try:
                mods = core.list_unit_mods(self.repo, p['camp'], p['unit'], self.token)
            except Exception as e:
                self.dlg.after(0, lambda: self._set_status(f'Ошибка: {e}'))
                return
            def apply():
                if not self._alive():
                    return
                self.mod_combo.configure(state='readonly')
                self.mod_combo['values'] = [ALL_PACK] + mods
                self.mod_var.set(ALL_PACK)
                self._set_status(f'Модов в паке: {len(mods)}')
            self.dlg.after(0, apply)
        threading.Thread(target=work, daemon=True).start()

    def _ok(self):
        if self.mode.get() == 'fork':
            url = self.url_var.get().strip()
            if not url:
                messagebox.showerror('Ошибка', 'Укажите URL дескриптора'); return
            nm = url.rsplit('/', 1)[-1].replace('.json', '') or 'форк'
            self.dlg.destroy()
            self.on_ok({'type': 'desc', 'name': nm, 'repo': self.repo,
                        'catalog': 'descriptors/catalog.json', 'id': '', 'source': '',
                        'url': url, 'installed_version': None,
                        'last_downloaded': None, 'last_updated': None}); return

        camp = self.camp_var.get().strip()
        if not camp:
            messagebox.showerror('Ошибка', 'Выберите лагерь'); return
        pack_sel = self.pack_var.get()
        if not pack_sel or pack_sel == ALL_CAMP:          # весь лагерь
            self.dlg.destroy()
            self.on_ok({'type': 'camp', 'camp': camp, 'repo': self.repo,
                        'name': f'{camp} — весь лагерь',
                        'last_downloaded': None, 'last_updated': None}); return
        p = self.pack_map.get(pack_sel)
        if not p:
            messagebox.showerror('Ошибка', 'Выберите пак'); return
        mod = {'type': 'unit', 'repo': self.repo, 'camp': p['camp'], 'unit': p['unit'],
               'name': p['name'], 'mod': '', 'last_downloaded': None, 'last_updated': None}
        mod_sel = self.mod_var.get()
        if mod_sel and mod_sel != ALL_PACK:               # конкретный мод
            mod['mod'] = mod_sel
            mod['name'] = mod_sel
        self.dlg.destroy()
        self.on_ok(mod)


# Фаза 4: предпросмотр обновления + поштучное решение конфликтов
STATUS_LABELS = {
    'add': '➕ добавить (новый файл)',
    'update': '⬆ обновить (вы не меняли)',
    'automerge': '🔀 авто-слить ваши правки',
    'player_only': '✋ оставить вашу правку (мод не менял)',
    'unchanged': '· без изменений',
    'deleted_clean': '🗑 удалить (нет в новой версии)',
    'conflict_text': '⚠ КОНФЛИКТ текста',
    'conflict_binary': '⚠ КОНФЛИКТ бинарного файла',
    'conflict_deleted': '⚠ КОНФЛИКТ: удалён в новой версии, вы правили',
}
# опции выбора для конфликтов: подпись -> код решения (см. core.apply_update_plan)
CONFLICT_OPTIONS = {
    'conflict_text': [('оставить мой', 'mine'), ('взять новый', 'theirs'),
                      ('сохранить оба (.srnew)', 'both')],
    'conflict_binary': [('оставить мой', 'mine'), ('взять новый', 'theirs'),
                        ('сохранить оба (.srnew)', 'both')],
    'conflict_deleted': [('оставить мой', 'keep'), ('удалить', 'delete')],
}


class MergePreviewDialog:
    """Предпросмотр обновления мода: дерево всех действий + поштучный выбор по
    каждому конфликту. По «Применить» отдаёт decisions в on_apply."""

    def __init__(self, parent, theme, plan, on_apply, on_cancel=None):
        self.plan = plan
        self.on_apply = on_apply
        self.on_cancel = on_cancel
        self.choice = {}        # relpath -> tk.StringVar (подпись решения)
        self.opt_lookup = {}    # relpath -> {подпись: код}
        d = self.dlg = tk.Toplevel(parent)
        d.title('Обновление — предпросмотр'); d.transient(parent); d.grab_set()
        d.geometry('760x560')
        d.protocol('WM_DELETE_WINDOW', self._cancel)

        s = plan['summary']
        head = (f"Мод: {plan.get('id', '?')}    версия {plan.get('version_old') or '—'} "
                f"→ {plan.get('version_new') or '—'}\n"
                f"авто-слить: {s.get('automerge', 0)}   обновить: {s.get('update', 0)}   "
                f"добавить: {s.get('add', 0)}   удалить: {s.get('deleted_clean', 0)}   "
                f"ваших правок сохранится: {s.get('player_only', 0)}   "
                f"конфликтов: {s.get('conflicts', 0)}")
        ttk.Label(d, text=head, justify=tk.LEFT).pack(anchor=tk.W, padx=12, pady=(12, 6))

        # дерево всех действий, сгруппированных по статусу
        tw = ttk.Frame(d); tw.pack(fill=tk.BOTH, expand=True, padx=12)
        tree = ttk.Treeview(tw, columns=('act',), show='tree headings', height=12)
        tree.heading('#0', text='Файл'); tree.column('#0', width=440, anchor=tk.W)
        tree.heading('act', text='Действие'); tree.column('act', width=260, anchor=tk.W)
        sb = ttk.Scrollbar(tw, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); sb.pack(side=tk.RIGHT, fill=tk.Y)
        by_status = {}
        for r in plan['actions']:
            by_status.setdefault(r['status'], []).append(r)
        order = ['conflict_text', 'conflict_binary', 'conflict_deleted', 'automerge',
                 'update', 'add', 'deleted_clean', 'player_only', 'unchanged']
        for st in order:
            rows = by_status.get(st)
            if not rows:
                continue
            gid = tree.insert('', tk.END, text=f'{STATUS_LABELS.get(st, st)}  ({len(rows)})',
                              open=st.startswith('conflict'))
            for r in rows:
                tree.insert(gid, tk.END, text=core.install_relpath(r['relpath']))

        # панель поштучного решения конфликтов
        conflicts = [r for r in plan['actions'] if r['status'] in CONFLICT_OPTIONS]
        cf = ttk.LabelFrame(d, text='Конфликты — выберите по каждому', padding=6)
        cf.pack(fill=tk.BOTH, expand=False, padx=12, pady=(8, 4))
        if not conflicts:
            ttk.Label(cf, text='Конфликтов нет — всё применится автоматически.').pack(anchor=tk.W)
        else:
            canvas = tk.Canvas(cf, height=140, highlightthickness=0,
                               bg=theme.get('panel', '#ffffff'))
            inner = ttk.Frame(canvas)
            csb = ttk.Scrollbar(cf, orient=tk.VERTICAL, command=canvas.yview)
            canvas.configure(yscrollcommand=csb.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); csb.pack(side=tk.RIGHT, fill=tk.Y)
            canvas.create_window((0, 0), window=inner, anchor='nw')
            inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
            for r in conflicts:
                rp = r['relpath']
                opts = CONFLICT_OPTIONS[r['status']]
                self.opt_lookup[rp] = {lbl: code for lbl, code in opts}
                row = ttk.Frame(inner); row.pack(fill=tk.X, pady=2)
                default = core.DECISION_DEFAULTS.get(r['status'])
                def_lbl = next((lbl for lbl, code in opts if code == default), opts[0][0])
                var = tk.StringVar(value=def_lbl)
                self.choice[rp] = var
                ttk.Combobox(row, textvariable=var, state='readonly', width=22,
                             values=[lbl for lbl, _ in opts]).pack(side=tk.LEFT, padx=(0, 8))
                ttk.Label(row, text=core.install_relpath(rp)).pack(side=tk.LEFT)

        bf = ttk.Frame(d); bf.pack(side=tk.BOTTOM, pady=10)
        ttk.Button(bf, text='Применить', style='Accent.TButton',
                   command=self._apply).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text='Отмена', command=self._cancel).pack(side=tk.LEFT)

    def _apply(self):
        decisions = {}
        for rp, var in self.choice.items():
            decisions[rp] = self.opt_lookup[rp].get(var.get())
        self.dlg.destroy()
        self.on_apply(decisions)

    def _cancel(self):
        self.dlg.destroy()
        if self.on_cancel:
            self.on_cancel()


def main():
    if sys.version_info < (3, 7):
        print('Нужен Python 3.7+'); sys.exit(1)
    Launcher().run()


if __name__ == '__main__':
    main()
