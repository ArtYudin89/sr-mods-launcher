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
            'repo': 'ArtYudin89/sr-mods-aggregator'})
        PROFILES_DIR.mkdir(exist_ok=True)
        self.current_profile = self.config.get('last_profile', 'default')
        self.profile = self._load_profile(self.current_profile)
        self.busy = False
        self.progress_var = tk.DoubleVar(value=0)
        self._apply_theme()
        self._build_ui()
        self._refresh_list()

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
        bf = ttk.Frame(hdr, style='TFrame'); bf.pack(side=tk.RIGHT)
        for txt, cmd in [('＋', self._add_mod), ('－', self._remove_mod),
                         ('↑', self._move_up), ('↓', self._move_down),
                         ('⟳', self._refresh_remote)]:
            ttk.Button(bf, text=txt, width=3, command=cmd).pack(side=tk.LEFT, padx=1)

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

        right = ttk.Frame(main, style='TFrame'); right.pack(side=tk.RIGHT, fill=tk.BOTH)
        act = ttk.LabelFrame(right, text='Действия', padding=10); act.pack(fill=tk.X)
        ttk.Button(act, text='⬇  Установить выбранный', style='Accent.TButton',
                   command=lambda: self._install(False)).pack(fill=tk.X, pady=2)
        ttk.Button(act, text='⬇  Установить все',
                   command=lambda: self._install(True)).pack(fill=tk.X, pady=2)
        ttk.Button(act, text='🧩 Установить набор (с зависим.)', style='Accent.TButton',
                   command=lambda: self._install(True)).pack(fill=tk.X, pady=2)
        ttk.Button(act, text='🛡 Проверить совместимость',
                   command=self._check_compat).pack(fill=tk.X, pady=2)
        ttk.Button(act, text='🗑  Очистить Mods', command=self._clear_mods).pack(fill=tk.X, pady=2)
        ttk.Label(right, text='Прогресс:').pack(anchor=tk.W, pady=(10, 0))
        ttk.Progressbar(right, variable=self.progress_var, maximum=100).pack(fill=tk.X)
        lf = ttk.LabelFrame(right, text='Лог', padding=6); lf.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
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
        self._post(self.progress_var.set, (done / total * 100) if total else 0)

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

    def _camp_of(self, mid):
        cat = getattr(self, '_catalog_cache', None) or {}
        ent = cat.get(mid)
        if ent and ent.get('variants'):
            src = ent.get('default_source') or ent['variants'][0]['source']
            return src.split('/')[0]
        return mid.split('/')[0] if '/' in mid else 'прочее'   # фолбэк: категория

    def _refresh_list(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
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
                items.append((m.get('camp', 'прочее'), m.get('unit'), 'мод',
                              mid.split('/')[-1], st_of(m, on), date if on else '', iid))
            elif typ == 'unit':
                on = bool(m.get('last_downloaded'))
                items.append((m.get('camp', 'прочее'), m.get('unit'), 'пак',
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
        camp_upd = getattr(self, '_camp_updated', {})
        for mid, ts in sorted(disk.items()):     # на диске, но не в наборе
            if mid in seen:
                continue
            camp = self._camp_of(mid)
            cu = camp_upd.get(camp)
            stt = '⬆ обновление?' if (cu and ts and ts < cu) else '✅ установлен'
            items.append((camp, None, 'мод', mid.split('/')[-1], stt, ts, None))

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

        for camp, pack, kind, label, status, date, iid in sorted(items, key=lambda x: (
                x[0], x[1] or '', x[3])):
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
                cu = {}                       # дата апдейта лагеря в репо (для дисковых модов)
                for c in ('redux', 'universe', 'shared'):
                    try:
                        cu[c] = core.unit_remote_updated(self._repo(), c, self._token())
                    except Exception:
                        pass
                self._camp_updated = cu
                self._cat_loading = False
                if self._catalog_cache or cu:
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
        """Проверка совместимости набора (Фаза 2): база/фиксы/сейвы/конфликты модов."""
        if self.busy:
            return
        units = [f'{m["camp"]}/{m["unit"]}' for m in self.profile.get('mods', [])
                 if m.get('type') == 'unit' and m.get('camp') and m.get('unit')]
        if not units:
            messagebox.showinfo('Совместимость',
                                'В наборе нет юнитов-паков для проверки.')
            return
        self.log('Проверка совместимости…')
        threading.Thread(target=self._check_compat_worker, args=(units,),
                         daemon=True).start()

    def _check_compat_worker(self, units):
        tok = self._token()
        try:
            packs = self._get_packs(tok)
            if not packs:
                self._post(messagebox.showwarning, 'Совместимость',
                           'Не удалось загрузить packs.json из репозитория.')
                return
            info = core.check_pack_compatibility(
                units, packs, installed_base=self.profile.get('installed_base'))
        except Exception as e:
            self._post(messagebox.showerror, 'Совместимость', f'Ошибка: {e}')
            return

        problems, notes = [], []
        bnames = [packs[b]['name'] for b in info['bases']]
        if info['base_conflict']:
            problems.append('⛔ В наборе НЕСКОЛЬКО базовых паков: ' + ', '.join(bnames) +
                            '.\n   Нельзя смешивать базы — оставь ровно одну.')
        elif info['missing_base']:
            problems.append('⚠ В наборе НЕТ базового пака (с Rangers.exe).\n'
                            '   Модам/фиксам нужна база — добавь одну (redux/original/universe…).')
        elif bnames:
            notes.append('✅ Базовый пак: ' + bnames[0])
        for fix, parent in info['fix_orphans']:
            problems.append(f'⚠ Фикс «{packs[fix]["name"]}» требует родительский пак '
                            f'«{parent}», которого нет в наборе.')
        if info['save_warning']:
            problems.append('💾 ' + info['save_warning'])
        if info['mandatory']:
            mand = ', '.join(packs[u]['name'] for u in info['mandatory'])
            notes.append('❗ Базовые/фикс-паки (обновление обязательно при выходе апдейта):\n   '
                         + mand)
        notes.append('ℹ Смена базового пака делает несовместимыми сейвы текущей партии '
                     '(новую партию начать можно).')

        title = 'Совместимость: проблемы' if problems else 'Совместимость: ок'
        body = ''
        if problems:
            body += 'НАЙДЕНЫ ВОПРОСЫ:\n\n' + '\n\n'.join(problems) + '\n\n'
        body += '— — —\n' + '\n'.join(notes)
        self.log('Совместимость: ' + ('проблемы' if problems else 'ок'))
        show = messagebox.showwarning if problems else messagebox.showinfo
        self._post(show, title, body)

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
        if self.busy:
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
        self.log('Информация об обновлениях получена')

    # ---------- install ----------
    def _install(self, all_mods):
        if self.busy:
            messagebox.showwarning('Занято', 'Дождитесь окончания операции')
            return
        if all_mods:
            targets = list(range(len(self.profile.get('mods', []))))
        else:
            i = self._selected()
            if i is None:
                messagebox.showwarning('Выбор', 'Выберите мод')
                return
            targets = [i]
        if not targets:
            return
        self.busy = True
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
                                                      tmp_dir=HERE)
                                if p.get('tier') == 'base':
                                    self.profile['installed_base'] = p['name']
                            except Exception as e:
                                self.log(f'ОШИБКА {p["name"]}: {e}')
                        m['last_downloaded'] = datetime.now().isoformat()
                        self._post(self._save_profile); self._post(self._refresh_list)
                        continue
                    if m.get('type') == 'unit':
                        st = core.reconstruct_unit(m['repo'], m['camp'], m['unit'],
                                                   mods_dir, tok, self._progress, self.log,
                                                   tmp_dir=HERE, mod=m.get('mod') or None)
                        m['last_updated'] = st.get('updated') or m.get('last_updated')
                    else:
                        up = core.install_zip(m['url'], mods_dir, tok, self._progress,
                                              self.log, tmp_dir=HERE)
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
                except Exception as e:
                    self.log(f'ОШИБКА {m["name"]}: {e}')
            if desc_idx:
                self._install_desc_set(desc_idx, mods_dir, tok)
        finally:
            self.busy = False
            self._post(self.progress_var.set, 0)
            self.log('Готово')

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
                                       log=self.log, tmp_dir=HERE)
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

    def _clear_mods(self):
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

    def _on_close(self):
        self.config['last_profile'] = self.current_profile
        self._save_profile()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


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


class AddModDialog:
    """Добавление по выбору из списков: Лагерь целиком / Пак / Конкретный мод / По ссылке (форк).
    Данные тянутся из packs.json (лагеря, паки) и каталога (моды) репозитория."""
    def __init__(self, parent, theme, on_ok, token='', repo='ArtYudin89/sr-mods-aggregator'):
        self.on_ok = on_ok
        self.token = token
        self.repo = repo
        self.cp = {}             # {camp: [pack dict]}
        self.pack_map = {}       # label -> pack dict (для текущего лагеря)
        self.mod_map = {}        # label -> mod_id (для текущего пака)
        self.camp_combo = self.pack_combo = self.mod_combo = None
        self.dlg = d = tk.Toplevel(parent)
        d.title('Добавить'); d.transient(parent); d.grab_set(); d.geometry('560x320')

        self.level = tk.StringVar(value='mod')
        tf = ttk.Frame(d); tf.pack(fill=tk.X, padx=14, pady=(14, 4))
        ttk.Label(tf, text='Что добавить:').pack(side=tk.LEFT)
        for txt, val in [('Лагерь целиком', 'camp'), ('Пак', 'pack'),
                         ('Мод', 'mod'), ('По ссылке (форк)', 'fork')]:
            ttk.Radiobutton(tf, text=txt, variable=self.level, value=val,
                            command=self._switch).pack(side=tk.LEFT, padx=6)

        self.body = ttk.Frame(d); self.body.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)
        self.camp_var = tk.StringVar(); self.pack_var = tk.StringVar()
        self.mod_var = tk.StringVar(); self.url_var = tk.StringVar()

        self.status = ttk.Label(d, text='Загрузка списка паков…')
        self.status.pack(anchor=tk.W, padx=14)
        bf = ttk.Frame(d); bf.pack(pady=10)
        ttk.Button(bf, text='Добавить', style='Accent.TButton',
                   command=self._ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text='Отмена', command=self.dlg.destroy).pack(side=tk.LEFT)
        self._switch()
        threading.Thread(target=self._load_packs, daemon=True).start()

    # --- данные ---
    def _load_packs(self):
        try:
            packs = core.load_packs('state/packs.json', self.repo, self.token)
            cp = core.camp_packs(packs)
        except Exception as e:
            self.dlg.after(0, lambda: self.status.config(text=f'Ошибка загрузки паков: {e}'))
            return
        def apply():
            self.cp = cp
            self.status.config(text=f'Лагерей: {len(cp)} · паков: {sum(len(v) for v in cp.values())}')
            if self.camp_combo is not None and self.camp_combo.winfo_exists():
                self.camp_combo['values'] = sorted(cp)
        self.dlg.after(0, apply)

    def _on_camp(self, _e=None):
        camp = self.camp_var.get()
        packs = self.cp.get(camp, [])
        self.pack_map = {f"{p['name']}  [{p['unit']}]": p for p in packs}
        self.pack_var.set(''); self.mod_var.set('')
        if self.pack_combo is not None and self.pack_combo.winfo_exists():
            self.pack_combo['values'] = list(self.pack_map)
        if self.mod_combo is not None and self.mod_combo.winfo_exists():
            self.mod_combo['values'] = []

    def _on_pack(self, _e=None):
        if self.level.get() != 'mod':
            return
        p = self.pack_map.get(self.pack_var.get())
        if not p:
            return
        self.status.config(text='Загрузка модов пака…')

        def work():
            try:
                mods = core.list_unit_mods(self.repo, p['camp'], p['unit'], self.token)
            except Exception as e:
                self.dlg.after(0, lambda: self.status.config(text=f'Ошибка: {e}'))
                return
            def apply():
                self.mod_map = {m: m for m in mods}
                if self.mod_combo is not None and self.mod_combo.winfo_exists():
                    self.mod_combo['values'] = mods
                self.status.config(text=f'Модов в паке: {len(mods)}')
            self.dlg.after(0, apply)
        threading.Thread(target=work, daemon=True).start()

    # --- разметка ---
    def _combo(self, label, var, on_pick=None):
        r = ttk.Frame(self.body); r.pack(fill=tk.X, pady=5)
        ttk.Label(r, text=label, width=14).pack(side=tk.LEFT)
        cb = ttk.Combobox(r, textvariable=var, state='readonly')
        cb.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if on_pick:
            cb.bind('<<ComboboxSelected>>', on_pick)
        return cb

    def _switch(self):
        for w in self.body.winfo_children():
            w.destroy()
        self.camp_combo = self.pack_combo = self.mod_combo = None
        lv = self.level.get()
        if lv == 'fork':
            r = ttk.Frame(self.body); r.pack(fill=tk.X, pady=5)
            ttk.Label(r, text='URL дескриптора:', width=14).pack(side=tk.LEFT)
            ttk.Entry(r, textvariable=self.url_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Label(self.body, text='Прямая ссылка на <id>.json форка мода.').pack(anchor=tk.W)
            return
        self.camp_combo = self._combo('Лагерь:', self.camp_var, self._on_camp)
        self.camp_combo['values'] = sorted(self.cp)
        if lv in ('pack', 'mod'):
            self.pack_combo = self._combo('Пак:', self.pack_var, self._on_pack)
        if lv == 'mod':
            self.mod_combo = self._combo('Мод:', self.mod_var)

    def _ok(self):
        lv = self.level.get()
        if lv == 'fork':
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
        if lv == 'camp':
            mod = {'type': 'camp', 'camp': camp, 'repo': self.repo,
                   'name': f'{camp} — весь лагерь',
                   'last_downloaded': None, 'last_updated': None}
        else:
            p = self.pack_map.get(self.pack_var.get())
            if not p:
                messagebox.showerror('Ошибка', 'Выберите пак'); return
            mod = {'type': 'unit', 'repo': self.repo, 'camp': p['camp'], 'unit': p['unit'],
                   'name': p['name'], 'mod': '',
                   'last_downloaded': None, 'last_updated': None}
            if lv == 'mod':
                mid = self.mod_var.get().strip()
                if not mid:
                    messagebox.showerror('Ошибка', 'Выберите мод'); return
                mod['mod'] = mid
                mod['name'] = mid
        self.dlg.destroy()
        self.on_ok(mod)


def main():
    if sys.version_info < (3, 7):
        print('Нужен Python 3.7+'); sys.exit(1)
    Launcher().run()


if __name__ == '__main__':
    main()
