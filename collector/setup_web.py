"""Browser setup, independent admin key; no third-party assets or telemetry."""
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

PAGE = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Настройка Vion Collector</title><link rel="stylesheet" href="/setup.css">
<main><h1>Vion Collector <small>by tomeeli</small></h1><p>Свой каталог и обложки — на вашем устройстве.</p>
<section id="login"><h2>1. Откройте управление</h2><p>Введите ключ управления из окна сборщика или команды «Показать ключ управления». Это отдельный ключ, не ключ подключения телевизора.</p><label>Ключ управления<input id="admin" type="password" autocomplete="off"></label><button id="enter">Продолжить</button></section>
<section id="settings" hidden><h2>2. Выберите источник и обложки</h2>
<label>Источник релизов<select id="source"><option value="rutor">Rutor — без Jackett</option><option value="rutracker">RuTracker через Jackett</option><option value="kinozal">Kinozal через Jackett</option><option value="nnm-club">NNM-Club через Jackett</option><option value="selezen">seleZen через Jackett</option></select></label>
<div id="jackett"><label>Адрес вашего Jackett<input id="jackett_url" placeholder="http://адрес:9117"></label><label>Идентификатор индексатора<input id="indexer" placeholder="Оставьте пустым для стандартного имени"></label><label>Ключ Jackett<input id="jackett_key" type="password" autocomplete="off"></label></div>
<label>Обложки<select id="poster_provider"><option value="auto">Автоматически: TMDB / ПоискКино</option><option value="tmdb">TMDB</option><option value="poiskkino">ПоискКино</option><option value="off">Без подбора из каталогов</option></select></label>
<label>Ключ TMDB<input id="tmdb_key" type="password" autocomplete="off"></label><label>Ключ ПоискКино<input id="poiskkino_key" type="password" autocomplete="off"></label>
<p>Пустое поле сохраняет прежний ключ. Сохранённые ключи не показываются. Для Rutor обложки также подбираются со страницы релиза. Доступность TMDB зависит от вашей сети.</p>
<details><summary>Дополнительные параметры</summary><label>Обновление раз в … минут<input id="interval_minutes" type="number" min="15" max="1440"></label><label>Кэш обложек, МБ<input id="poster_cache_mb" type="number" min="32" max="8192"></label></details>
<button id="save">Сохранить и обновить</button><h2>3. Подключите Vion</h2><p>В Vion выберите внешний сборщик → «Найти сборщик в локальной сети». Выберите устройство и вставьте ключ ниже. Если поиск недоступен, укажите адрес этого сервера с портом сборщика вручную.</p>
<label>Ключ подключения Vion<input id="device_key" type="password" readonly></label><button id="copy">Скопировать ключ подключения</button><button id="reveal">Показать / скрыть</button>
<p id="state"></p><button id="refresh">Обновить состояние</button><button id="leave">Закрыть управление</button></section><p id="message" role="status"></p>
<footer>Ключи и данные остаются у вас. В интернете открывайте управление только через HTTPS или свою VPN. Не публикуйте ключи и файлы настроек.</footer></main><script src="/setup.js"></script></html>'''

CSS = '''body{margin:0;background:#152231;color:#e7edf6;font:17px/1.5 system-ui}main{max-width:760px;margin:auto;padding:28px}section{background:#203348;border-radius:18px;padding:24px;margin:24px 0}h1{color:#69d7eb}small{font-size:15px}label{display:block;margin:16px 0}input,select{display:block;box-sizing:border-box;width:100%;padding:12px;background:#102034;color:white;border:1px solid #54728d;border-radius:8px;font:inherit}button{padding:12px 18px;margin:10px 10px 10px 0;border:0;border-radius:10px;background:#6ed9e9;color:#102034;font:inherit;cursor:pointer}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #ba9aff}footer,small{color:#b6c8d9}[hidden]{display:none!important}#message{min-height:26px}'''

JS = '''let token='';const el=id=>document.getElementById(id);const textFields=['source','poster_provider','jackett_url','indexer'];const secrets=['tmdb_key','poiskkino_key','jackett_key'];
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,cache:'no-store',credentials:'omit'});let data=await r.json();if(!r.ok)throw Error(r.status===401?'Неверный ключ управления.':data.message||'Запрос не выполнен.');return data}
function source(){el('jackett').hidden=el('source').value==='rutor'}el('source').onchange=source;
async function load(){const s=await api('/admin/state');for(const k of textFields)el(k).value=s.config[k]||'';for(const k of ['interval_minutes','poster_cache_mb'])el(k).value=s.config[k];for(const k of secrets){el(k).value='';el(k).placeholder=s.saved[k]?'Ключ уже сохранён':'Ключ не задан'}el('device_key').value=s.device_key;el('state').textContent=s.busy?'Идёт обновление. Предыдущая база сохранена.':(s.error?'Последнее обновление не удалось. Проверьте источник и доступ к сети.':'Карточек в выбранном источнике: '+s.count+'. Обложки загружаются в фоне.');source()}
function run(fn){return async()=>{try{el('message').textContent='';await fn()}catch(e){el('message').textContent=e.message}}}
el('enter').onclick=run(async()=>{token=el('admin').value.trim();await load();el('admin').value='';el('login').hidden=true;el('settings').hidden=false});
el('save').onclick=run(async()=>{const b={};for(const k of [...textFields,...secrets])b[k]=el(k).value.trim();for(const k of ['interval_minutes','poster_cache_mb'])b[k]=Number(el(k).value);await api('/admin/config',b);await load();el('message').textContent='Сохранено. Обновление запрошено; первый сбор и подбор обложек займут время.'});
el('refresh').onclick=run(load);el('reveal').onclick=()=>{el('device_key').type=el('device_key').type==='password'?'text':'password'};
el('copy').onclick=run(async()=>{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(el('device_key').value);el('message').textContent='Ключ скопирован.'}else{el('device_key').type='text';el('device_key').select();el('message').textContent='Нажмите Ctrl+C или выберите «Копировать». После подключения скройте ключ.'}});
el('leave').onclick=()=>{token='';el('device_key').value='';for(const k of secrets)el(k).value='';el('settings').hidden=true;el('login').hidden=false;el('message').textContent='Управление закрыто.'};'''


class Setup:
    def __init__(self, collector):
        self.collector = collector
        path = collector.data / 'setup-key.txt'
        try:
            with path.open('x', encoding='ascii') as out:
                os.chmod(path, 0o600)
                out.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
        self.key = path.read_text(encoding='ascii').strip()
        if not 32 <= len(self.key) <= 512 or not self.key.isascii():
            raise ValueError('invalid_setup_key')
        self.lock = threading.Lock()
        self.failures = []

    def handle(self, h):
        if h.path in ('/', '/setup', '/setup.js', '/setup.css') and h.command == 'GET':
            value, mime = (JS, 'text/javascript') if h.path.endswith('.js') else (CSS, 'text/css') if h.path.endswith('.css') else (PAGE, 'text/html')
            data = value.encode('utf-8')
            h.send_response(200)
            h.send_header('Content-Type', mime + '; charset=utf-8')
            h.send_header('Content-Length', str(len(data)))
            h.send_header('Cache-Control', 'no-store')
            h.send_header('X-Content-Type-Options', 'nosniff')
            h.send_header('Referrer-Policy', 'no-referrer')
            h.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            h.end_headers()
            h.wfile.write(data)
            return True
        if not h.path.startswith('/admin/'):
            return False
        # Custom Authorization + JSON requests, no cookies/CORS; foreign origins cannot mutate state.
        origin = h.headers.get('Origin')
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ('http', 'https') or parsed.netloc.lower() != h.headers.get('Host', '').lower():
                h.answer(403, dict(message='Запрос с другого сайта запрещён.'))
                return True
        with self.lock:
            now = time.monotonic()
            self.failures = [t for t in self.failures if now - t < 60]
            if len(self.failures) >= 10:
                h.answer(429, dict(message='Подождите минуту перед повторным входом.'))
                return True
            if not hmac.compare_digest(h.headers.get('Authorization', '').encode(), ('Bearer ' + self.key).encode()):
                self.failures.append(now)
                h.answer(401, dict(error='unauthorized'))
                return True
        c = self.collector
        if h.path == '/admin/state' and h.command == 'GET':
            from collector import config_at
            try:
                cfg = config_at(c.config_path)
                snap = c.snapshot()
                visible = {k: cfg.get(k, '') for k in ('source', 'poster_provider', 'jackett_url', 'indexer')}
                visible.update(interval_minutes=cfg.get('interval_minutes', 120), poster_cache_mb=cfg.get('poster_cache_mb', 512))
                visible['poster_provider'] = cfg.get('poster_provider', 'auto')
                h.answer(200, dict(config=visible, saved={k: bool(cfg.get(k)) for k in ('tmdb_key', 'poiskkino_key', 'jackett_key')}, device_key=c.key, count=len(snap['items']) if snap else 0, busy=c.update_lock.locked(), error=bool(c.error)))
            except Exception:
                h.answer(409, dict(message='Не удалось прочитать настройки. Файлы не изменены.'))
            return True
        if h.path == '/admin/config' and h.command == 'POST':
            try:
                if h.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError()
                length = int(h.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ValueError()
                patch = json.loads(h.setup_body)
                self.save(patch)
            except (ValueError, TypeError, OSError):
                h.answer(400, dict(message='Проверьте источник, адрес Jackett и диапазоны параметров. Настройки не изменены.'))
                return True
            c.refresh_requested.set()
            h.answer(200, dict(saved=True))
            return True
        h.answer(404, dict(error='not_found'))
        return True

    def save(self, patch):
        from collector import atomic, config_at, SOURCES, url_base
        text = ('source', 'poster_provider', 'jackett_url', 'indexer', 'tmdb_key', 'poiskkino_key', 'jackett_key')
        numbers = ('interval_minutes', 'poster_cache_mb')
        if not isinstance(patch, dict) or set(patch) - set(text + numbers):
            raise ValueError()
        with self.lock:
            cfg = config_at(self.collector.config_path)
            for k, v in patch.items():
                if k in text and (not isinstance(v, str) or len(v) > 2048 or any(ord(ch) < 32 for ch in v)):
                    raise ValueError()
                if k in numbers and type(v) is not int:
                    raise ValueError()
                if k.endswith('_key') and not v:
                    continue
                cfg[k] = v
            if cfg['source'] not in SOURCES or cfg.get('poster_provider', 'auto') not in ('auto', 'tmdb', 'poiskkino', 'off'):
                raise ValueError()
            if not 15 <= cfg.get('interval_minutes', 120) <= 1440 or not 32 <= cfg.get('poster_cache_mb', 512) <= 8192:
                raise ValueError()
            if cfg.get('jackett_url'):
                url_base(cfg['jackett_url'], private_http=True)
            if cfg['source'] != 'rutor' and (not cfg.get('jackett_url') or not cfg.get('jackett_key')):
                raise ValueError()
            atomic(self.collector.config_path, cfg)
