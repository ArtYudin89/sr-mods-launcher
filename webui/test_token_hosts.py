"""Уровень-1: GitHub-токен игрока уходит только на адреса GitHub.

Адреса для запросов приходят и из данных — chunk_index_url в описании мода (Hugging
Face), индекс частей форка, ссылка на мод или архив от другого человека. До 0.28.0
лаунчер прикладывал токен заголовком Authorization к любому такому адресу (аудит
15.10.2026, находки L10 и L6). Здесь локальный сервер записывает заголовки настоящих
запросов: ни один запрос не на GitHub не должен нести Authorization.

Запуск: python webui/test_token_hosts.py
"""
import json
import os
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import launcher_core as core

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PASS = []; FAIL = []
TOKEN = 'DUMMY-TOKEN-FOR-TEST'


def check(name, expect, actual):
    ok = expect == actual
    (PASS if ok else FAIL).append(name)
    mark = 'OK ' if ok else 'FAIL'
    print(f'[{mark}] {name}\n      ожидание: {expect!r}\n      факт:     {actual!r}')


# ── Локальный сервер: записывает заголовки, отдаёт JSON или zip ──────────────────
SEEN = []


def _zip_bytes():
    p = Path(tempfile.mkdtemp(prefix='tok_zip_')) / 'm.zip'
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('Mods/Cat/Mod/ModuleInfo.txt', b'Name=Mod')
    return p.read_bytes()


ZIP = _zip_bytes()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN.append((self.path, self.headers.get('Authorization')))
        if self.path.endswith('.zip'):
            body, ctype = ZIP, 'application/zip'
        elif self.path.endswith('asset_index.json'):
            body, ctype = json.dumps({'blobs': {}, 'chunks': {}}).encode(), 'application/json'
        else:
            body = json.dumps({'id': 'Cat/Mod', 'files': {'code': {}, 'assets': {}}}).encode()
            ctype = 'application/json'
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = HTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{srv.server_address[1]}'
tmp = Path(tempfile.mkdtemp(prefix='tok_'))


def auth_of(path_part):
    hits = [a for p, a in SEEN if path_part in p]
    return hits[-1] if hits else 'NO REQUEST'


try:
    core.load_chunk_index(desc={'chunk_index_url': f'{BASE}/asset_index.json'}, token=TOKEN)
    check('индекс частей по адресу из описания мода — без токена', None,
          auth_of('asset_index.json'))
    core.descriptor_for({'url': f'{BASE}/fork/Cat/Mod.json'}, token=TOKEN)
    check('мод-форк по ссылке — без токена', None, auth_of('/fork/Cat/Mod.json'))
    core.download_url(f'{BASE}/chunk.zip', TOKEN, tmp / 'c.zip')
    check('часть с чужого сервера — без токена', None, auth_of('/chunk.zip'))
    mods = tmp / 'Game' / 'Mods'
    mods.mkdir(parents=True)
    core.install_zip(f'{BASE}/archive.zip', mods, TOKEN, log=lambda *_: None)
    check('архив по ссылке — без токена', None, auth_of('/archive.zip'))
    check('архив по ссылке при этом установлен', True,
          (mods / 'Cat' / 'Mod' / 'ModuleInfo.txt').is_file())
finally:
    srv.shutdown()

# ── Какие адреса получают токен ──────────────────────────────────────────────────
for url, expect in (
        ('https://api.github.com/repos/o/r/contents/x', TOKEN),
        ('https://github.com/o/r/releases/download/v1/SRModsLauncher.exe', TOKEN),
        ('https://raw.githubusercontent.com/o/r/master/x.json', TOKEN),
        ('https://API.GitHub.com/repos/o/r', TOKEN),
        ('https://huggingface.co/datasets/Artyudin/sr-mods-assets/resolve/main/asset_index.json',
         None),
        ('http://api.github.com/repos/o/r', None),                 # без TLS — нет
        ('https://api.github.com.evil.example/x', None),
        ('https://evil.example/api.github.com/x', None),
        ('https://user@evil.example/x', None),
        ('not a url', None)):
    check(f'токен для {url}', expect, core._token_for(url, TOKEN))
check('пустой токен — None и для GitHub', None, core._token_for('https://api.github.com/x', ''))


# ── На GitHub токен по-прежнему уходит (приватный форк, релизы) ──────────────────
class _Resp:
    status_code = 200
    headers = {'content-length': '0'}

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        return iter(())

    def json(self):
        return {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


sent = {}


def _rec(url, headers=None, **kw):
    sent[url] = (headers or {}).get('Authorization')
    return _Resp()


with patch.object(core._SESSION, 'get', side_effect=_rec), \
        patch.object(core.requests, 'get', side_effect=_rec):
    gh = 'https://github.com/o/r/releases/download/v1/SRModsLauncher.exe'
    hf = 'https://huggingface.co/datasets/x/resolve/main/c.zip'
    core.download_url(gh, TOKEN, tmp / 'a.exe')
    core.download_url(hf, TOKEN, tmp / 'b.zip')
    core.gh_json('https://api.github.com/repos/o/r/releases', TOKEN)
    core.download_asset({'url': 'https://api.github.com/repos/o/r/releases/assets/1'},
                        TOKEN, tmp / 'c.bin')
    check('релиз на github.com — с токеном', 'Bearer ' + TOKEN, sent.get(gh))
    check('часть на Hugging Face — без токена', None, sent.get(hf))
    check('API GitHub — с токеном', 'Bearer ' + TOKEN,
          sent.get('https://api.github.com/repos/o/r/releases'))
    check('ассет релиза через API — с токеном', 'Bearer ' + TOKEN,
          sent.get('https://api.github.com/repos/o/r/releases/assets/1'))

print(f'\n===== ИТОГ: PASS={len(PASS)}  FAIL={len(FAIL)} =====')
if FAIL:
    print('ПРОВАЛЫ:', FAIL)
    sys.exit(1)
