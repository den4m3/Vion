"""Exact film matching and persistent, bounded poster cache for Vion Collector."""
import hashlib
import hmac
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import struct
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit, urljoin
from urllib.request import Request, build_opener, HTTPRedirectHandler

MAX_IMAGE = 8 * 1024 * 1024
IMAGE_HOSTS = ('image.tmdb.org', 'avatars.mds.yandex.net', 'avatars.yandex.net',
               'st.kp.yandex.net', 'kinopoiskapiunofficial.tech')
ROUTE = re.compile(r'/v1/images/([a-f0-9]{64})-([a-f0-9]{32})\Z')
GENRES = {28:'боевик',12:'приключения',16:'мультфильм',35:'комедия',80:'криминал',99:'документальный',18:'драма',10751:'семейный',14:'фэнтези',36:'история',27:'ужасы',10402:'музыка',9648:'детектив',10749:'мелодрама',878:'фантастика',53:'триллер',10752:'военный',37:'вестерн'}

def metadata(row, tmdb=False):
    result = {}
    rating = row.get('vote_average') if tmdb else (row.get('rating') or {}).get('kp')
    if type(rating) in (int, float) and 0 < rating <= 10: result['rating'] = rating
    description = row.get('overview' if tmdb else 'description')
    if isinstance(description, str): result['description'] = description[:12000]
    names = [g['name'] for g in (row.get('genres') or []) if isinstance(g,dict) and isinstance(g.get('name'),str)]
    if not names and tmdb: names = [GENRES[g] for g in (row.get('genre_ids') or []) if g in GENRES]
    if names: result['genres'] = ', '.join(names)[:1000]
    return result

def key_title(value):
    return re.sub(r'[^\w]+', '', str(value).casefold().replace('ё', 'е').replace('_', ''))

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args): return None

def request_json(url, headers):
    with build_opener(NoRedirect).open(Request(url, headers=headers), timeout=8) as response:
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024: raise ValueError('metadata_too_large')
        return json.loads(body)

def image_url(value):
    u = urlsplit(value)
    if u.scheme != 'https' or u.hostname not in IMAGE_HOSTS or u.username or u.password or u.port not in (None, 443) or u.fragment:
        raise ValueError('untrusted_image_address')
    for entry in socket.getaddrinfo(u.hostname, 443, type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(entry[4][0]).is_global: raise ValueError('nonpublic_image_address')
    return value

def image_dimensions(body):
    if body.startswith(b'\x89PNG\r\n\x1a\n') and len(body) >= 33 and body[12:16] == b'IHDR':
        width, height = struct.unpack('>II', body[16:24])
    elif body.startswith(b'\xff\xd8') and body.endswith(b'\xff\xd9'):
        pos = 2; width = height = 0
        while pos + 4 <= len(body):
            if body[pos] != 255: break
            while pos < len(body) and body[pos] == 255: pos += 1
            if pos >= len(body): break
            marker = body[pos]; pos += 1
            if marker in (0xD8, 0xD9): continue
            if pos + 2 > len(body): break
            length = int.from_bytes(body[pos:pos+2], 'big')
            if length < 2 or pos + length > len(body): break
            if marker in (0xC0, 0xC1, 0xC2) and length >= 8:
                height, width = struct.unpack('>HH', body[pos+3:pos+7]); break
            if marker == 0xDA: break
            pos += length
    elif len(body) >= 30 and body[:4] == b'RIFF' and body[8:12] == b'WEBP' and int.from_bytes(body[4:8], 'little') + 8 == len(body):
        kind = body[12:16]
        if kind == b'VP8X': width, height = int.from_bytes(body[24:27], 'little') + 1, int.from_bytes(body[27:30], 'little') + 1
        elif kind == b'VP8 ' and body[23:26] == b'\x9d\x01\x2a': width, height = int.from_bytes(body[26:28], 'little') & 16383, int.from_bytes(body[28:30], 'little') & 16383
        elif kind == b'VP8L' and body[20] == 47:
            bits = int.from_bytes(body[21:25], 'little'); width, height = (bits & 16383) + 1, ((bits >> 14) & 16383) + 1
        else: raise ValueError('unsupported_webp_header')
    else: raise ValueError('unsupported_image_format')
    if not 40 <= width <= 4096 or not 60 <= height <= 8192 or width * height > 16_000_000:
        raise ValueError('invalid_image_dimensions')
    if width >= height: raise ValueError('not_a_vertical_poster')
    return width, height

def download_image(url):
    for attempt in range(3):
        image_url(url)
        try:
            with build_opener(NoRedirect).open(Request(url, headers={'User-Agent': 'Vion-Collector/0.2', 'Accept': 'image/jpeg,image/png'}), timeout=8) as response:
                content_type = response.headers.get_content_type()
                if content_type not in ('image/jpeg', 'image/png', 'image/webp', 'application/octet-stream'):
                    raise ValueError('invalid_image_type')
                body = response.read(MAX_IMAGE + 1)
                if len(body) > MAX_IMAGE: raise ValueError('image_too_large')
                image_dimensions(body)
                return body
        except HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308) or attempt == 2: raise
            url = urljoin(url, error.headers.get('Location', ''))
    raise ValueError('too_many_image_redirects')

def exact(rows, item, fields, year_field):
    aliases = {key_title(item.get('name', '')), key_title(item.get('original', ''))} - {''}
    candidates = []
    for row in rows:
        year = str(row.get(year_field, ''))[:4]
        if year != str(item.get('releaseInfo', '')): continue
        if aliases.intersection(key_title(row.get(field, '')) for field in fields): candidates.append(row)
    unique = {str(row.get('id', row.get('filmId', ''))): row for row in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None

def resolve_tmdb(item, key, width):
    headers = {'Accept': 'application/json'}
    if not re.fullmatch(r'[a-fA-F0-9]{32}', key): headers['Authorization'] = 'Bearer ' + key
    def get(path, **query):
        query['language'] = 'ru-RU'
        if 'Authorization' not in headers: query['api_key'] = key
        return request_json('https://api.themoviedb.org/3/' + path + '?' + urlencode(query), headers)
    selected = None
    if str(item.get('tmdbid', '')).isdigit():
        selected = get('movie/' + str(item['tmdbid']))
    elif re.fullmatch(r'tt\d+', item.get('imdbid', '')):
        rows = get('find/' + item['imdbid'], external_source='imdb_id').get('movie_results', [])
        selected = rows[0] if len(rows) == 1 else None
    else:
        for title in dict.fromkeys([item.get('name', ''), item.get('original', '')]):
            if not title: continue
            selected = exact(get('search/movie', query=title, primary_release_year=item['releaseInfo'], include_adult='false').get('results', []), item, ('title', 'original_title'), 'release_date')
            if selected: break
    if not selected or str(selected.get('release_date', ''))[:4] != str(item.get('releaseInfo', '')): return None
    path = selected.get('poster_path') or ''
    if not re.fullmatch(r'/[A-Za-z0-9._-]+\.(?:jpg|jpeg|png)', path): return None
    return dict(metadata(selected, True), url=f'https://image.tmdb.org/t/p/w{width}' + path, tmdbid=str(selected['id']), provider='tmdb')

def resolve_poiskkino(item, key, width):
    base = 'https://api.poiskkino.dev/v1.5/movie'
    headers = {'X-API-KEY': key}
    if str(item.get('kpid', '')).isdigit(): selected = request_json(base + '/' + str(item['kpid']), headers)
    elif item.get('imdbid') or item.get('tmdbid'):
        field = 'externalId.imdb' if item.get('imdbid') else 'externalId.tmdb'
        value = item.get('imdbid') or item.get('tmdbid')
        rows = request_json(base + '?' + urlencode({field: value, 'limit': 10}), headers).get('docs', [])
        selected = rows[0] if len(rows) == 1 else None
    else:
        rows = request_json(base + '/search?' + urlencode({'query': item['name'], 'limit': 10}), headers).get('docs', [])
        selected = exact(rows, item, ('name', 'alternativeName', 'enName'), 'year')
    if not selected or selected.get('isSeries') or str(selected.get('year', '')) != str(item.get('releaseInfo', '')): return None
    external = selected.get('externalId') or {}
    for ours, theirs in [('imdbid', 'imdb'), ('tmdbid', 'tmdb')]:
        if item.get(ours) and external.get(theirs) and str(item[ours]) != str(external[theirs]): return None
    poster = selected.get('poster') or {}; url = poster.get('url') or poster.get('previewUrl')
    if not url: return None
    result = dict(metadata(selected), url=url, kpid=str(selected['id']), provider='poiskkino')
    for ours, theirs in [('tmdbid', 'tmdb'), ('imdbid', 'imdb')]:
        if external.get(theirs): result[ours] = str(external[theirs])
    return result

class ArtworkCache:
    def __init__(self, data, access_key):
        self.root = Path(data) / 'posters'; self.root.mkdir(exist_ok=True)
        self.manifest = Path(data) / 'artwork-index.json'
        self.secret = access_key.encode(); self.lock = threading.RLock()
        self.index = {}
        if self.manifest.exists():
            try: self.index = json.loads(self.manifest.read_text(encoding='utf-8'))
            except (ValueError, OSError): pass

    def route(self, digest):
        signature = hmac.new(self.secret, ('poster:' + digest).encode(), hashlib.sha256).hexdigest()[:32]
        return '/v1/images/' + digest + '-' + signature

    def read(self, route):
        match = ROUTE.fullmatch(route)
        if not match or not hmac.compare_digest(self.route(match[1]), route): return None
        path = self.root / (match[1] + '.img')
        try:
            if path.stat().st_size > MAX_IMAGE: return None
            body = path.read_bytes(); image_dimensions(body)
            os.utime(path, None)
            return body
        except (OSError, ValueError): return None

    def has(self, route):
        match = ROUTE.fullmatch(route or '')
        return bool(match and hmac.compare_digest(self.route(match[1]), route) and (self.root / (match[1] + '.img')).is_file())

    def store(self, body):
        image_dimensions(body)
        digest = hashlib.sha256(body).hexdigest(); path = self.root / (digest + '.img')
        if not path.exists():
            tmp = path.with_suffix('.tmp')
            with tmp.open('wb') as out: out.write(body); out.flush(); os.fsync(out.fileno())
            os.replace(tmp, path)
        return digest

    def save(self):
        # Never persist catalog API credentials or signed bearer secrets in this index.
        temp = self.manifest.with_suffix('.tmp')
        temp.write_text(json.dumps(self.index, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, self.manifest)

    def lookup(self, item, config):
        provider = config.get('poster_provider', 'auto'); width = config.get('poster_width', 342)
        tmdb = os.environ.get('VION_TMDB_KEY') or config.get('tmdb_key', '')
        poisk = os.environ.get('VION_POISKKINO_KEY') or config.get('poiskkino_key', '')
        scope = hashlib.sha256((provider + '|' + str(width) + '|' + tmdb + '|' + poisk).encode()).hexdigest()[:16]
        identity = item.get('id') or 'title:' + key_title(item['name']) + '|' + str(item.get('releaseInfo', ''))
        return scope + '|' + identity

    def due(self, item, config):
        with self.lock:
            old = self.index.get(self.lookup(item, config), {})
            digest = old.get('file', '')
            if re.fullmatch(r'[a-f0-9]{64}', digest) and (self.root / (digest + '.img')).exists(): return False
            return time.time() >= old.get('retry_at', 0)

    def cached(self, item, config):
        with self.lock:
            old = self.index.get(self.lookup(item, config), {})
            digest = old.get('file', '')
            if re.fullmatch(r'[a-f0-9]{64}', digest) and (self.root / (digest + '.img')).is_file():
                return dict(old.get('ids', {}), collectorPoster=self.route(digest))
        return None

    def enrich(self, item, config):
        width = config.get('poster_width', 342)
        provider = config.get('poster_provider', 'auto')
        tmdb = os.environ.get('VION_TMDB_KEY') or config.get('tmdb_key', '')
        poisk = os.environ.get('VION_POISKKINO_KEY') or config.get('poiskkino_key', '')
        lookup = self.lookup(item, config)
        with self.lock:
            old = self.index.get(lookup, {})
            digest = old.get('file', '')
            if re.fullmatch(r'[a-f0-9]{64}', digest) and (self.root / (digest + '.img')).exists():
                return dict(old.get('ids', {}), collectorPoster=self.route(digest))
            if time.time() < old.get('retry_at', 0): return None
        selected = None
        try:
            failed = False
            for name, secret, resolver in [('tmdb', tmdb, resolve_tmdb), ('poiskkino', poisk, resolve_poiskkino)]:
                if not selected and provider in ('auto', name) and secret:
                    try: selected = resolver(item, secret, width)
                    except Exception: failed = True
            if not selected:
                # Rutor topic posters are allowed only from known image CDNs.
                selected = {'url': item['_poster_hint'], 'provider': 'rutor'} if item.get('_poster_hint') else None
            if selected:
                digest = self.store(download_image(selected['url']))
                ids = {key: selected[key] for key in ('tmdbid', 'imdbid', 'kpid', 'rating', 'genres', 'description') if selected.get(key)}
                record = dict(file=digest, ids=ids, checked=time.time(), retry_at=time.time() + 21600, provider=selected['provider'])
            else: record = dict(checked=time.time(), retry_at=time.time() + (300 if failed else 21600))
        except Exception:
            record = dict(checked=time.time(), retry_at=time.time() + 300)
        with self.lock:
            self.index[lookup] = record
            if len(self.index) > 16000:
                self.index = dict(sorted(self.index.items(), key=lambda pair: pair[1].get('checked', 0), reverse=True)[:12000])
            self.save()
        return dict(record['ids'], collectorPoster=self.route(record['file'])) if record.get('file') else None

    def trim(self, limit_mb):
        with self.lock:
            files = sorted(self.root.glob('*.img'), key=lambda path: path.stat().st_mtime)
            total = sum(path.stat().st_size for path in files)
            for path in files:
                if total <= limit_mb * 1024 * 1024: break
                total -= path.stat().st_size; path.unlink(missing_ok=True)
