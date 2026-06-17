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
            'last_profile': 'default', 'profiles': ['default'], 'github_token': ''})
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
        self._save_config()

    def _token(self):
        return self.token_var.get().strip() or os.environ.get('GH_TOKEN', '')

    def _mods_dir(self):
        gp = self.game_path_var.get()
        base = Path(gp).parent if gp and Path(gp).exists() else HERE
        d = base / 'Mods'
        d.mkdir(parents=True, exist_ok=True)
        return d

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
               foreground=[('selected', t['accent_fg'])])
        st.configure('TProgressbar', background=t['accent'], troughcolor=t['tree_bg'])

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
        if self.banner_img is None:
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
        ttk.Label(r1, text='Игра:', background=self.theme['panel']).pack(side=tk.LEFT)
        self.game_path_var = tk.StringVar(value=self.profile.get('game_path', ''))
        ttk.Entry(r1, textvariable=self.game_path_var).pack(side=tk.LEFT, fill=tk.X,
                                                            expand=True, padx=6)
        ttk.Button(r1, text='Обзор', command=self._browse_game).pack(side=tk.LEFT)

        r2 = ttk.Frame(box, style='Panel.TFrame'); r2.pack(fill=tk.X, pady=(6, 2))
        ttk.Button(r2, text='▶  Запустить игру', style='Accent.TButton',
                   command=self._launch).pack(side=tk.LEFT)
        ttk.Button(r2, text='📂 Папка модов', command=self._open_mods).pack(side=tk.LEFT, padx=8)
        ttk.Label(r2, text='GitHub token (для приватных/агрегатора):',
                  background=self.theme['panel']).pack(side=tk.LEFT, padx=(14, 4))
        self.token_var = tk.StringVar(value=self.config.get('github_token', ''))
        ttk.Entry(r2, textvariable=self.token_var, width=24, show='•').pack(side=tk.LEFT)

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

        cols = ('name', 'type', 'status', 'updated', 'downloaded')
        self.tree = ttk.Treeview(left, columns=cols, show='headings', height=16)
        for c, txt, w in [('name', 'Название', 230), ('type', 'Тип', 70),
                          ('status', 'Статус', 70), ('updated', 'Обновлён', 130),
                          ('downloaded', 'Скачан', 130)]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor=tk.W if c == 'name' else tk.CENTER)
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

    # ---------- list ----------
    def _refresh_list(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for idx, m in enumerate(self.profile.get('mods', [])):
            dl = m.get('last_downloaded')
            self.tree.insert('', tk.END, iid=str(idx), values=(
                m.get('name', '?'),
                m.get('type', 'zip'),
                '✅' if dl else '⏳',
                fmt_date(m.get('last_updated')),
                fmt_date(dl) if dl else 'никогда',
            ))

    def _selected(self):
        s = self.tree.selection()
        return int(s[0]) if s else None

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
        AddModDialog(self.root, self.theme, self._on_mod_added)

    def _on_mod_added(self, mod):
        self.profile.setdefault('mods', []).append(mod)
        self._save_profile()
        self._refresh_list()
        self.log(f'Добавлен мод: {mod["name"]}')

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
            self.tree.selection_set(str(i - 1))

    def _move_down(self):
        i = self._selected()
        if i is not None and i < len(self.profile['mods']) - 1:
            m = self.profile['mods']
            m[i + 1], m[i] = m[i], m[i + 1]
            self._save_profile(); self._refresh_list()
            self.tree.selection_set(str(i + 1))

    def _refresh_remote(self):
        if self.busy:
            return
        threading.Thread(target=self._refresh_remote_worker, daemon=True).start()

    def _refresh_remote_worker(self):
        tok = self._token()
        for i, m in enumerate(self.profile.get('mods', [])):
            try:
                if m.get('type') == 'unit':
                    up = core.unit_remote_updated(m['repo'], m['camp'], tok)
                else:
                    up = core.resolve_zip(m['url'], tok).get('updated')
                if up:
                    m['last_updated'] = up
            except Exception as e:
                self.log(f'{m["name"]}: ошибка обновления ({e})')
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
            for i in targets:
                m = self.profile['mods'][i]
                self.log(f'=== {m["name"]} ===')
                try:
                    if m.get('type') == 'unit':
                        st = core.reconstruct_unit(m['repo'], m['camp'], m['unit'],
                                                   mods_dir, tok, self._progress, self.log,
                                                   tmp_dir=HERE)
                        m['last_updated'] = st.get('updated') or m.get('last_updated')
                    else:
                        up = core.install_zip(m['url'], mods_dir, tok, self._progress,
                                              self.log, tmp_dir=HERE)
                        m['last_updated'] = up or m.get('last_updated')
                    m['last_downloaded'] = datetime.now().isoformat()
                    self._post(self._save_profile)
                    self._post(self._refresh_list)
                except Exception as e:
                    self.log(f'ОШИБКА {m["name"]}: {e}')
        finally:
            self.busy = False
            self._post(self.progress_var.set, 0)
            self.log('Готово')

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
        p = filedialog.askopenfilename(title='exe игры',
                                       filetypes=[('exe', '*.exe'), ('Все', '*.*')])
        if p:
            self.game_path_var.set(p)

    def _launch(self):
        gp = self.game_path_var.get()
        if not gp or not Path(gp).exists():
            messagebox.showerror('Ошибка', 'Укажите корректный путь к игре')
            return
        try:
            subprocess.Popen([os.path.basename(gp)], cwd=os.path.dirname(gp), shell=True)
            self.log(f'Запуск: {os.path.basename(gp)}')
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
    """Диалог добавления мода: тип zip (URL) или unit (агрегатор)."""
    def __init__(self, parent, theme, on_ok):
        self.on_ok = on_ok
        self.dlg = d = tk.Toplevel(parent)
        d.title('Добавить мод'); d.transient(parent); d.grab_set(); d.geometry('520x300')
        self.type_var = tk.StringVar(value='unit')
        tf = ttk.Frame(d); tf.pack(fill=tk.X, padx=14, pady=(14, 4))
        ttk.Label(tf, text='Тип:').pack(side=tk.LEFT)
        ttk.Radiobutton(tf, text='Юнит агрегатора', variable=self.type_var,
                        value='unit', command=self._switch).pack(side=tk.LEFT, padx=8)
        ttk.Radiobutton(tf, text='Generic ZIP', variable=self.type_var,
                        value='zip', command=self._switch).pack(side=tk.LEFT)
        self.body = ttk.Frame(d); self.body.pack(fill=tk.BOTH, expand=True, padx=14, pady=6)
        self.vars = {k: tk.StringVar() for k in
                     ('name', 'url', 'repo', 'camp', 'unit')}
        self.vars['repo'].set('ArtYudin89/sr-mods-aggregator')
        bf = ttk.Frame(d); bf.pack(pady=10)
        ttk.Button(bf, text='Добавить', style='Accent.TButton',
                   command=self._ok).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text='Отмена', command=d.destroy).pack(side=tk.LEFT)
        self._switch()

    def _row(self, label, key):
        r = ttk.Frame(self.body); r.pack(fill=tk.X, pady=4)
        ttk.Label(r, text=label, width=16).pack(side=tk.LEFT)
        ttk.Entry(r, textvariable=self.vars[key]).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _switch(self):
        for w in self.body.winfo_children():
            w.destroy()
        self._row('Название:', 'name')
        if self.type_var.get() == 'zip':
            self._row('Ссылка Release:', 'url')
        else:
            self._row('Репозиторий:', 'repo')
            self._row('Лагерь (camp):', 'camp')
            self._row('Юнит (unit):', 'unit')

    def _ok(self):
        t = self.type_var.get()
        v = {k: self.vars[k].get().strip() for k in self.vars}
        if not v['name']:
            messagebox.showerror('Ошибка', 'Укажите название'); return
        if t == 'zip':
            if not v['url']:
                messagebox.showerror('Ошибка', 'Укажите ссылку'); return
            mod = {'type': 'zip', 'name': v['name'], 'url': v['url'],
                   'last_downloaded': None, 'last_updated': None}
        else:
            if not (v['repo'] and v['camp'] and v['unit']):
                messagebox.showerror('Ошибка', 'Заполните repo/camp/unit'); return
            mod = {'type': 'unit', 'name': v['name'], 'repo': v['repo'],
                   'camp': v['camp'], 'unit': v['unit'],
                   'last_downloaded': None, 'last_updated': None}
        self.dlg.destroy()
        self.on_ok(mod)


def main():
    if sys.version_info < (3, 7):
        print('Нужен Python 3.7+'); sys.exit(1)
    Launcher().run()


if __name__ == '__main__':
    main()
