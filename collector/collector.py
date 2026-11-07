#!/usr/bin/env python3
"""Vion metadata collector by tomeeli. Python 3.10+; optional mDNS via python-zeroconf.

Only public film metadata is retained. No magnet, torrent or playback URLs.
"""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import re
import secrets
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener
from artwork import ArtworkCache, IMAGE_HOSTS

VERSION = '0.3.0'
SOURCES = ('rutor', 'rutracker', 'kinozal', 'nnm-club', 'selezen')
LIMIT = 8 * 1024 * 1024
LABELS = ['', 'WEB-DL 1080p', 'BDRip 1080p', 'BDRip-HEVC 1080p',
          'BDRemux 1080p', 'WEB-DL 2160p SDR', 'WEB-DL 2160p HDR',
          'UHD BDRip SDR', 'UHD BDRip HDR', 'UHD BDRemux SDR', 'UHD BDRemux HDR']


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def atomic(path, value):
    path = Path(path)
    data = encode(value)
    if len(data) > LIMIT:
        raise ValueError('snapshot_too_large')
    tmp = path.with_suffix('.tmp')
    with tmp.open('wb') as out:
        os.chmod(tmp, 0o600)
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)


def plain(value):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', value))).strip()


def valid_date(value):
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return ''


def quality(title):
    s = title.upper().replace('–', '-').replace('—', '-')
    if re.search(r'(?<![A-Z])(?:CAMRIP|HDCAM|HDTS|HDTC|TELESYNC|TS|TC|DVDSCR|SCR)(?![A-Z])', s):
        return 0
    uhd = bool(re.search(r'(?<!\d)2160[PI]?(?!\d)', s))
    explicit = re.search(r'(?<!\d)(?:480|576|720|1080|1440|2160|4320)[PI]?(?!\d)', s)
    uhd |= not explicit and bool(re.search(r'\b(?:4K|UHD)\b', s))
    if not uhd and not re.search(r'(?<!\d)1080[PI]?(?!\d)', s):
        return 0
    hdr = bool(re.search(r'\bHDR(?:10\+?)?\b|DOLBY\s*VISION|\bDV\b', s))
    if re.search(r'\bBD[ -]?REMUX\b', s):
        return (10 if hdr else 9) if uhd else 4
    if re.search(r'\bBD[ -]?RIP(?:\b|[-_])', s):
        return (8 if hdr else 7) if uhd else (3 if re.search(r'HEVC|H[.]?265|X265', s) else 2)
    if re.search(r'\bWEB[ -]?DL\b(?!RIP)', s):
        return (6 if hdr else 5) if uhd else 1
    return 0


def item_from_title(title, published, source):
    title = plain(title)[:1000]
    published = valid_date(published)
    rank = quality(title)
    year = re.search(r'[\[(]((?:18|19|20|21)\d{2})[\]), ]', title)
    if not rank or not year or not published or published > date.today().isoformat():
        return None
    if re.search(r'сезон|сери[яий]|S\d{1,2}E\d', title, re.I):
        return None
    names = title[:year.start()].strip().split(' / ', 1)
    if not names[0]:
        return None
    return dict(id='', type='movie', name=names[0][:500], original=names[1][:500] if len(names) > 1 else '',
                releaseInfo=year[1], releaseDate=published, releaseSource=source,
                poster='', description='', availableQuality=[rank], qualitySummary=LABELS[rank])


def title_key(value):
    return re.sub(r'[^\w]+', ' ', value.casefold().replace('ё', 'е').replace('_', ' ')).strip()


def identities(item):
    result = []
    for key in ('kpid', 'imdbid', 'tmdbid'):
        if item.get(key):
            result.append(key + ':' + str(item[key]))
    year = str(item.get('releaseInfo', ''))
    if re.fullmatch(r'\d{4}', year):
        for key in ('original', 'name'):
            if item.get(key):
                result.append('title:' + title_key(item[key]) + '|' + year)
    return result


def conflicts(a, b):
    return any(a.get(k) and b.get(k) and a[k] != b[k] for k in ('kpid', 'imdbid', 'tmdbid'))


def merge(existing, incoming, source):
    # Index aliases once; a large saved catalog must not require pairwise comparisons.
    films = []; active = {}; aliases = {}; positions = {}; used_ids = set()
    def index(row, keys):
        for key in keys: aliases.setdefault(key, {})[id(row)] = row
    for raw in existing + incoming:
        item = dict(raw); keys = set(identities(item))
        if not keys: continue
        candidates = {}
        for key in keys: candidates.update(aliases.get(key, {}))
        matches = sorted((row for identity, row in candidates.items() if identity in active and not conflicts(row, item)), key=lambda row: positions[id(row)])
        if any(conflicts(left, right) for i, left in enumerate(matches) for right in matches[i+1:]):
            strong = {key for key in keys if not key.startswith('title:')}
            matches = [row for row in matches if strong.intersection(identities(row))]
            if any(conflicts(left, right) for i, left in enumerate(matches) for right in matches[i+1:]): matches = []
        if not matches:
            item['id'] = item.get('id') or 'release:' + hashlib.sha256(next(iter(identities(item))).encode()).hexdigest()[:24]
            if item['id'] in used_ids: item['id'] = 'release:' + hashlib.sha256(encode(sorted(keys))).hexdigest()[:24]
            films.append(item); active[id(item)] = item; positions[id(item)] = len(films); used_ids.add(item['id']); index(item, keys)
            continue
        target = matches[0]
        for other in matches[1:] + [item]:
            keys.update(identities(other))
            for key, value in other.items():
                if not target.get(key) and value: target[key] = value
            target['availableQuality'] = sorted(set(target['availableQuality'] + other['availableQuality']))
            target['qualitySummary'] = ' · '.join(LABELS[x] for x in target['availableQuality'])
            target['releaseDate'] = min(target['releaseDate'], other['releaseDate'])
        for other in matches[1:]:
            active.pop(id(other), None); used_ids.discard(other['id'])
        index(target, keys | set(identities(target)))
    films = [row for row in films if id(row) in active]
    films.sort(key=lambda row: (row['releaseDate'], row['id']), reverse=True)
    return dict(schema=1, updated=int(time.time() * 1000), source=source, items=films[:8000], historyComplete=False, historyFrom='')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not forward a Jackett API key to a different host.


def fetch(url, maximum=2 * 1024 * 1024):
    req = Request(url, headers={'User-Agent': 'Vion-Collector/' + VERSION, 'Accept': 'application/json,text/html'})
    with build_opener(NoRedirect).open(req, timeout=15) as response:
        body = response.read(maximum + 1)
        if len(body) > maximum:
            raise ValueError('upstream_too_large')
        charset = response.headers.get_content_charset() or 'utf-8'
        if charset.lower() not in ('utf-8', 'windows-1251', 'cp1251'):
            raise ValueError('unsupported_upstream_charset')
        return body.decode(charset, errors='strict')


def listing(body, source='rutor'):
    start = re.search(r'<div\b[^>]*\bid=[\x22\x27]index[\x22\x27][^>]*>', body, re.I)
    if not start or '</table>' not in body[start.end():].lower():
        raise ValueError('unrecognized_rutor_listing')
    table = re.split(r'</table>', body[start.end():], maxsplit=1, flags=re.I)[0]
    items = []
    months = {'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4, 'май': 5, 'июн': 6,
              'июл': 7, 'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12}
    for row in re.findall(r'<tr\b[^>]*class=[\x22\x27](?:gai|tum)[\x22\x27][^>]*>(.*?)</tr\s*>', table, re.I | re.S):
        cell = re.search(r'<td\b[^>]*>(.*?)</td>', row, re.I | re.S)
        if not cell:
            raise ValueError('missing_listing_date')
        value = plain(cell[1]).lower()
        found = re.search(r'(\d{1,2})\s+([а-я]+)\s+(\d{2,4})', value)
        try:
            if found:
                year = int(found[3]); year += 2000 if year < 100 else 0
                published = date(year, months[found[2][:3]], int(found[1])).isoformat()
            else:
                published = datetime.strptime(value, '%d.%m.%Y').date().isoformat()
        except (ValueError, KeyError):
            raise ValueError('unrecognized_listing_date') from None
        for href, title in re.findall(r'<a\b[^>]*href=[\x22\x27]([^\x22\x27]+)[\x22\x27][^>]*>(.*?)</a>', row, re.I | re.S):
            if re.fullmatch(r'/torrent/[1-9]\d*(?:/[^?#]*)?', html.unescape(href)):
                item = item_from_title(title, published, source)
                if item:
                    item['_topic'] = html.unescape(href)
                    items.append(item)
                break
    return items


def url_base(value, private_http=False):
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('invalid_upstream_address')
    if not private_http and parsed.scheme != 'https':
        raise ValueError('upstream_requires_https')
    return value.rstrip('/')


def collect(config, page=1):
    source = config['source']
    if source == 'rutor':
        base = url_base(config.get('rutor_url', 'https://rutor.info'))
        rows = []
        for category in (1, 5):
            films = listing(fetch(f'{base}/browse/{page - 1}/{category}/0/0'))
            for item in films: item['_rutor_base'] = base
            rows += films
        return rows
    base = url_base(config.get('jackett_url', ''), private_http=True)
    secret = os.environ.get('VION_JACKETT_KEY') or config.get('jackett_key', '')
    if not secret:
        raise ValueError('jackett_key_missing')
    indexer = config.get('indexer', '') or source
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', indexer):
        raise ValueError('invalid_indexer')
    params = urlencode(dict(apikey=secret, Query='', Category=2000, Limit=100))
    response = json.loads(fetch(f'{base}/api/v2.0/indexers/{indexer}/results?{params}'))
    if response.get('Error') or not isinstance(response.get('Results'), list):
        raise ValueError('jackett_feed_unavailable')
    rows = []
    for raw in response['Results'][:1000]:
        item = item_from_title(raw.get('Title', ''), raw.get('PublishDate', ''), source)
        if item:
            imdb = str(raw.get('Imdb', ''))
            if imdb.isdigit():
                imdb = 'tt' + imdb
            if re.fullmatch(r'tt\d+', imdb):
                item['imdbid'] = imdb
            rows.append(item)
    return rows


def config_at(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if config.get('source', 'rutor') not in SOURCES:
        raise ValueError('invalid_source')
    config.setdefault('source', 'rutor')
    interval = config.get('interval_minutes', 120)
    if type(interval) is not int or not 15 <= interval <= 1440:
        raise ValueError('invalid_interval')
    config['interval_minutes'] = interval
    pages = config.get('pages_per_update', 2)
    if type(pages) is not int or not 1 <= pages <= 10:
        raise ValueError('invalid_page_budget')
    if config.get('poster_provider', 'auto') not in ('auto', 'tmdb', 'poiskkino', 'off'):
        raise ValueError('invalid_poster_provider')
    if config.get('poster_width', 342) not in (185, 342, 500):
        raise ValueError('invalid_poster_width')
    size = config.get('poster_cache_mb', 512)
    if type(size) is not int or not 32 <= size <= 8192:
        raise ValueError('invalid_poster_cache_limit')
    return config


class Collector:
    def __init__(self, data, config_path):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.config_path = Path(config_path)
        self.lock = threading.RLock()
        self.update_lock = threading.Lock()
        self.error = ''
        self.artwork_ready = threading.Event()
        self.refresh_requested = threading.Event()
        keyfile = self.data / 'access-key.txt'
        try:
            with keyfile.open('x', encoding='ascii') as out:
                os.chmod(keyfile, 0o600)
                out.write(secrets.token_urlsafe(32))
        except FileExistsError:
            pass
        self.key = keyfile.read_text(encoding='ascii').strip()
        if not re.fullmatch(r'[!-~]{32,512}', self.key):
            raise ValueError('invalid_access_key')
        self.artwork = ArtworkCache(self.data, self.key)

    def snapshot(self, source=None):
        source = source or config_at(self.config_path)['source']
        path = self.data / ('releases-' + source + '.json')
        with self.lock:
            if not path.exists():
                return None
            if path.stat().st_size > LIMIT:
                raise ValueError('snapshot_too_large')
            return json.loads(path.read_text(encoding='utf-8'))

    def update(self):
        if not self.update_lock.acquire(blocking=False):
            return False
        try:
            config = config_at(self.config_path)
            incoming = []
            cursor_path = self.data / ('cursor-' + config['source'] + '.json')
            cursor = 2
            if cursor_path.exists():
                cursor = max(2, min(201, json.loads(cursor_path.read_text())['page']))
            pages = [1]
            if config['source'] == 'rutor':
                pages += list(range(cursor, min(201, cursor + config['pages_per_update'] - 1)))
            oldest = ''
            for page in pages:
                incoming += collect(config, page)
            if not incoming:
                raise ValueError('no_qualifying_releases')
            with self.lock:
                old = self.snapshot(config['source'])
                result = merge(old['items'] if old else [], incoming, config['source'])
                atomic(self.data / ('releases-' + config['source'] + '.json'), result)
                # Save after the catalog: a crash may repeat a page, never skip it.
                if config['source'] == 'rutor' and len(pages) > 1:
                    atomic(cursor_path, dict(page=pages[-1] + 1))
            self.error = ''
            self.artwork_ready.set()
            return True
        except Exception as error:
            # Never log raw HTTP errors: they can contain an API key in the URL.
            safe = str(error) if isinstance(error, ValueError) and re.fullmatch(r'[a-z_]+', str(error)) else type(error).__name__
            self.error = safe
            print('Update failed:', safe, '— saved catalog retained', flush=True)
            return False
        finally:
            self.update_lock.release()

    def artwork_batch(self, maximum=12):
        config = config_at(self.config_path)
        if config.get('poster_provider') == 'off': return 0
        snapshot = self.snapshot(config['source'])
        if not snapshot: return 0
        changed = attempted = 0
        for item in snapshot['items']:
            # Metadata GETs never perform this work; poster downloads have their own worker.
            ready = self.artwork.cached(item, config)
            if ready and item.get('collectorPoster') == ready['collectorPoster']: continue
            if not ready and not self.artwork.due(item, config): continue
            attempted += 1
            if not ready and not item.get('_poster_hint') and item.get('_topic') and item.get('_rutor_base'):
                try:
                    topic = fetch(url_base(item['_rutor_base']) + item['_topic'])
                    start = re.search(r'<table\b[^>]*\bid=[\x22\x27]details[\x22\x27][^>]*>', topic, re.I)
                    if start:
                        body = re.split(r'</table>', topic[start.end():], maxsplit=1, flags=re.I)[0]
                        body = re.split('скриншот', body, maxsplit=1, flags=re.I)[0]
                        for value in re.findall(r'<img\b[^>]*\bsrc=[\x22\x27]([^\x22\x27]+)', body, re.I):
                            value = html.unescape(value)
                            if urlsplit(value).scheme == 'https' and urlsplit(value).hostname in IMAGE_HOSTS:
                                item['_poster_hint'] = value; break
                except Exception: pass
            art = ready or self.artwork.enrich(item, config)
            if art:
                with self.lock:
                    current = self.snapshot(config['source'])
                    for row in current['items']:
                        if row['id'] == item['id']: row.update(art); break
                    current['updated'] = int(time.time() * 1000)
                    atomic(self.data / ('releases-' + config['source'] + '.json'), current)
                changed += 1
            if attempted >= maximum: break
        self.artwork.trim(config.get('poster_cache_mb', 512))
        if attempted:
            print(f'Posters: checked={attempted}, added={changed}; existing images retained', flush=True)
        return attempted


def make_server(collector, host, port):
    from setup_web import Setup
    setup = Setup(collector)
    class Handler(BaseHTTPRequestHandler):
        server_version = 'VionCollector/' + VERSION
        sys_version = ''

        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def answer(self, status, value):
            body = encode(value)
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if setup.handle(self):
                return
            if self.path == '/health':
                return self.answer(200, dict(service='vion-collector', version=VERSION, schema=1))
            if self.path.startswith('/v1/images/'):
                body = collector.artwork.read(self.path)
                if body is None: return self.answer(404, dict(error='image_not_found'))
                self.send_response(200)
                self.send_header('Content-Type', 'image/png' if body.startswith(b'\x89PNG') else 'image/webp' if body.startswith(b'RIFF') else 'image/jpeg')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'private, max-age=604800, immutable')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers(); self.wfile.write(body); return
            supplied = self.headers.get('Authorization', '').encode('utf-8')
            expected = ('Bearer ' + collector.key).encode('ascii')
            if not hmac.compare_digest(supplied, expected):
                return self.answer(401, dict(error='unauthorized'))
            if self.path != '/v1/releases':
                return self.answer(404, dict(error='not_found'))
            try:
                snapshot = collector.snapshot()
                if snapshot:
                    for row in snapshot['items']:
                        for field in list(row):
                            if field.startswith('_'): del row[field]
                        if row.get('collectorPoster') and not collector.artwork.has(row['collectorPoster']):
                            del row['collectorPoster']
                return self.answer(200, snapshot) if snapshot else self.answer(503, dict(error='catalog_not_ready'))
            except Exception:
                return self.answer(503, dict(error='catalog_unavailable'))
        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ValueError()
                self.setup_body = self.rfile.read(length)
                if len(self.setup_body) != length:
                    raise ValueError()
            except (ValueError, OSError):
                return self.answer(400, dict(message='Недопустимый размер запроса.'))
            if not setup.handle(self):
                self.answer(404, dict(error='not_found'))

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        def __init__(self, *args):
            self.slots = threading.BoundedSemaphore(8)
            super().__init__(*args)
        def process_request(self, request, address):
            if not self.slots.acquire(blocking=False):
                request.close()
                return
            try:
                super().process_request(request, address)
            except BaseException:
                self.slots.release()
                raise
        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.slots.release()
    return Server((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description='Vion Collector by tomeeli')
    parser.add_argument('--data', default='data')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--show-key', action='store_true')
    parser.add_argument('--show-setup-key', action='store_true', help='Show the separate browser administration key')
    parser.add_argument('--discover', action='store_true', help='Find Vion devices on the local network and exit')
    parser.add_argument('--control-stdin', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--no-discovery', action='store_true', help='Disable LAN announcements and discovery')
    args = parser.parse_args()
    if args.discover:
        from discovery import discover
        try:
            rows = discover()
        except ImportError:
            print('LAN discovery requires: python -m pip install -r requirements.txt')
            return 1
        except Exception:
            print('LAN discovery unavailable. Check multicast and firewall; manual addresses still work.')
            return 1
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        if not rows:
            print('No Vion devices found. Open Vion on the same LAN; allow mDNS UDP 5353.')
        return 0
    if not Path(args.config).exists():
        atomic(Path(args.config), dict(source='rutor', interval_minutes=120, pages_per_update=2))
    config_at(args.config)
    collector = Collector(args.data, args.config)
    if args.show_setup_key:
        from setup_web import Setup
        print(Setup(collector).key)
        return 0
    if args.show_key:
        print(collector.key)
        return 0
    if args.once:
        ok = collector.update()
        if ok: collector.artwork_batch()
        return 0 if ok else 1
    stop = threading.Event()
    def worker():
        while not stop.is_set():
            collector.update()
            try:
                delay = config_at(args.config)['interval_minutes'] * 60
            except Exception:
                delay = 900
            collector.refresh_requested.wait(delay)
            collector.refresh_requested.clear()
    server = make_server(collector, args.host, args.port)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    def artwork_worker():
        while not stop.is_set():
            try: attempted = collector.artwork_batch()
            except Exception:
                attempted = 0
                print('Poster update delayed; saved images retained', flush=True)
            if not attempted:
                collector.artwork_ready.wait(300)
                collector.artwork_ready.clear()
            stop.wait(.25)
    threading.Thread(target=artwork_worker, daemon=True, name='VionPosters').start()
    def shutdown(*_):
        stop.set()
        collector.refresh_requested.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    if args.control_stdin:
        def control():
            import sys
            for line in sys.stdin:
                if line.strip() == 'stop':
                    shutdown()
                    return
            shutdown()  # GUI disappeared: do not leave an orphan worker.
        threading.Thread(target=control, daemon=True, name='VionControl').start()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    discovery = None
    if not args.no_discovery:
        try:
            from discovery import Discovery
            def found(item):
                destination = item.get('url', ', '.join(item['addresses']))
                print(f"Vion found: {item['name']} [{item['kind']}] — {destination}", flush=True)
            discovery = Discovery(server.server_port, args.host, notify=found).start()
        except ImportError:
            print('LAN discovery unavailable: install requirements.txt; manual addresses still work.', flush=True)
        except Exception:
            print('LAN discovery unavailable; manual addresses still work.', flush=True)
    print(f'Vion Collector {VERSION} by tomeeli — {args.host}:{server.server_port}', flush=True)
    print('Browser setup: open /setup on this server. Show the admin key with --show-setup-key (or Show-Setup-Key.cmd).', flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        stop.set()
        if discovery:
            discovery.close()
        server.server_close()
        thread.join(timeout=2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
