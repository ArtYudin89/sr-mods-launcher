"""Уровень-1: запросы на Hugging Face не просят сжатие br.

В exe (urllib3 2.6.1 + brotli 1.2.0) ответ HF с Content-Encoding: br то распаковывается, то
падает (can_accept_more_data() is False); без br в Accept-Encoding HF отдаёт gzip — и всегда
успех (проверка v0.28.0 15.10.2026). Сам requests просит br, как только в сборке есть brotli,
поэтому лаунчер пишет заголовок явно. Здесь локальный сервер записывает заголовки настоящих
запросов (индекс частей и часть), а адрес сервера считается адресом HF.

Запуск: python webui/test_hf_encoding.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import launcher_core as core
import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PASS = []; FAIL = []


def check(name, expect, actual):
    ok = expect == actual
    (PASS if ok else FAIL).append(name)
    mark = 'OK ' if ok else 'FAIL'
    print(f'[{mark}] {name}\n      ожидание: {expect!r}\n      факт:     {actual!r}')


# ── Какие адреса — Hugging Face ──────────────────────────────────────────────────
for url, expect in (
        ('https://huggingface.co/datasets/Artyudin/sr-mods-assets/resolve/main/asset_index.json',
         True),
        ('https://HuggingFace.co/datasets/x/resolve/main/c.zip', True),
        ('https://cdn-lfs.huggingface.co/repos/x/y', True),
        ('https://cas-bridge.xethub.hf.co/xet-bridge-us/x', True),
        ('https://hf.co/datasets/x', True),
        ('https://raw.githubusercontent.com/o/r/master/state/asset_index.json', False),
        ('https://github.com/o/r/releases/download/v1/SRModsLauncher.exe', False),
        ('https://huggingface.co.evil.example/x', False),
        ('https://evilhuggingface.co/x', False),
        ('https://evil.example/huggingface.co/x', False),
        ('not a url', False)):
    check(f'Hugging Face: {url}', expect, core._is_hf(url))


# ── Локальный сервер: записывает Accept-Encoding ─────────────────────────────────
SEEN = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        SEEN[self.path] = self.headers.get('Accept-Encoding')
        body = json.dumps({'blobs': {}, 'chunks': {}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = HTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f'http://127.0.0.1:{srv.server_address[1]}'
HF = BASE + '/hf'
tmp = Path(tempfile.mkdtemp(prefix='hfenc_'))

try:
    with patch.object(core, '_is_hf', side_effect=lambda url: url.startswith(HF)):
        core.load_chunk_index(desc={'chunk_index_url': HF + '/asset_index.json'})
        check('индекс частей с HF — gzip, deflate', 'gzip, deflate',
              SEEN.get('/hf/asset_index.json'))
        core.download_url(HF + '/chunk.zip', 'DUMMY', tmp / 'c.zip')
        check('часть с HF — gzip, deflate', 'gzip, deflate', SEEN.get('/hf/chunk.zip'))
        core.download_url(BASE + '/other.zip', None, tmp / 'o.zip')
    default = requests.utils.default_headers()['Accept-Encoding']
    check('другой адрес — заголовок requests по умолчанию', default, SEEN.get('/other.zip'))
    print(f'      (по умолчанию requests здесь просит: {default})')
finally:
    srv.shutdown()

print(f'\n===== ИТОГ: PASS={len(PASS)}  FAIL={len(FAIL)} =====')
if FAIL:
    print('ПРОВАЛЫ:', FAIL)
    sys.exit(1)
