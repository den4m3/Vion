import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import zlib
import artwork as a
import collector as c

def png(width=100, height=150):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress((b'\0' + b'\x25\x80\xa0' * width) * height)) + chunk(b'IEND', b'')

def film(): return c.item_from_title('Дочь / Daughter (2025) WEB-DL 1080p', '2026-01-01', 'rutor')

class ArtworkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.config = {'poster_provider': 'tmdb', 'tmdb_key': 'private-api-fixture-key', 'poster_width': 342}
        self.cache = a.ArtworkCache(self.root, 'test-access-key-' + 'x' * 32)
    def tearDown(self): self.tmp.cleanup()

    def test_bad_images_rejected(self):
        for body in (b'<html>Blocked</html>', b'<svg/>', png(200, 100), png(5000, 100)):
            with self.assertRaises(ValueError): a.image_dimensions(body)
        self.assertEqual(a.image_dimensions(png()), (100, 150))

    def test_signed_path_and_traversal(self):
        digest = self.cache.store(png()); route = self.cache.route(digest)
        self.assertEqual(self.cache.read(route), png())
        self.assertIsNone(self.cache.read(route[:-1] + ('1' if route[-1] != '1' else '2')))
        self.assertIsNone(self.cache.read('/v1/images/../../access-key.txt'))
        self.assertNotIn('test-access-key', route)

    def test_disk_cache_survives_restart_without_network(self):
        with patch.object(a, 'resolve_tmdb', return_value={'url': 'https://image.tmdb.org/t/p/w342/one.jpg', 'tmdbid': '1', 'provider': 'tmdb'}), patch.object(a, 'download_image', return_value=png()) as fetch:
            first = self.cache.enrich(film(), self.config)
            restarted = a.ArtworkCache(self.root, 'test-access-key-' + 'x' * 32)
            second = restarted.enrich(film(), self.config)
        self.assertEqual(first, second); self.assertEqual(fetch.call_count, 1)
        self.assertNotIn(self.config['tmdb_key'], self.cache.manifest.read_text())

    def test_no_exact_match_is_negative_cached(self):
        with patch.object(a, 'resolve_tmdb', return_value=None) as resolve:
            self.assertIsNone(self.cache.enrich(film(), self.config))
            self.assertIsNone(self.cache.enrich(film(), self.config))
            self.assertFalse(self.cache.due(film(), self.config))
        self.assertEqual(resolve.call_count, 1)

    def test_transient_error_gets_bounded_retry(self):
        with patch.object(a, 'resolve_tmdb', side_effect=TimeoutError): self.cache.enrich(film(), self.config)
        record = next(iter(self.cache.index.values()))
        self.assertLess(record['retry_at'] - record['checked'], 301)

    def test_auto_provider_fallback(self):
        config = dict(self.config, poster_provider='auto', poiskkino_key='private-poisk-fixture')
        with patch.object(a, 'resolve_tmdb', side_effect=TimeoutError), patch.object(a, 'resolve_poiskkino', return_value={'url': 'https://avatars.mds.yandex.net/one.jpg', 'kpid': '2', 'provider': 'poiskkino'}), patch.object(a, 'download_image', return_value=png()):
            self.assertEqual(self.cache.enrich(film(), config)['kpid'], '2')

    def test_auto_falls_back_when_first_image_download_fails(self):
        config = dict(self.config, poster_provider='auto', poiskkino_key='private-poisk-fixture')
        with patch.object(a, 'resolve_tmdb', return_value={'url':'https://image.tmdb.org/blocked.jpg','tmdbid':'1','provider':'tmdb'}), patch.object(a, 'resolve_poiskkino', return_value={'url':'https://avatars.mds.yandex.net/good.jpg','kpid':'2','provider':'poiskkino'}), patch.object(a, 'download_image', side_effect=[TimeoutError(), png()]):
            result = self.cache.enrich(film(), config)
        self.assertIsNotNone(result)
        self.assertEqual(result['kpid'], '2')

    def test_valid_but_wrong_manifest_shape_is_recovered(self):
        for value in ([], {'bad': None}, {'bad': {'file': '../bad', 'retry_at': 'invalid'}}):
            self.cache.manifest.write_text(json.dumps(value), encoding='utf-8')
            restarted = a.ArtworkCache(self.root, 'test-access-key-' + 'x' * 32)
            self.assertTrue(restarted.due(film(), self.config))
            self.assertIsNone(restarted.cached(film(), self.config))

    def test_quality_change_invalidates_lookup(self):
        self.assertNotEqual(self.cache.lookup(film(), self.config), self.cache.lookup(film(), dict(self.config, poster_width=500)))

    def test_cache_limit_removes_oldest(self):
        digest = self.cache.store(png())
        self.cache.trim(0)
        self.assertFalse(self.cache.has(self.cache.route(digest)))

    def test_remakes_and_ambiguous_titles_never_guessed(self):
        movie = film()
        remake = dict(id=1, title='Дочь', release_date='2009-01-01', poster_path='/wrong.jpg')
        match = dict(id=2, title='Дочь', release_date='2025-01-01', poster_path='/correct.jpg')
        with patch.object(a, 'request_json', return_value={'results': [remake, match]}):
            self.assertTrue(a.resolve_tmdb(movie, 'token', 342)['url'].endswith('/correct.jpg'))
        with patch.object(a, 'request_json', return_value={'results': [match, dict(match, id=3)]}):
            self.assertIsNone(a.resolve_tmdb(movie, 'token', 342))

    def test_poster_metadata_contains_available_rating_and_genres(self):
        row = dict(id=2, title='Дочь', release_date='2025-01-01', poster_path='/correct.jpg', vote_average=7.3, genre_ids=[18,35], overview='Plot')
        with patch.object(a, 'request_json', return_value={'results': [row]}):
            result = a.resolve_tmdb(film(), 'token', 342)
        self.assertEqual(result['rating'], 7.3)
        self.assertEqual(result['genres'], 'драма, комедия')
        self.assertEqual(result['description'], 'Plot')

    def test_external_imdb_id_matching_checks_year(self):
        movie = dict(film(), imdbid='tt1234567')
        with patch.object(a, 'request_json', return_value={'movie_results': [dict(id=1, title='Other name', release_date='2009-01-01', poster_path='/wrong.jpg')]}):
            self.assertIsNone(a.resolve_tmdb(movie, 'token', 342))

    def test_private_or_arbitrary_image_hosts_rejected(self):
        for url in ('http://image.tmdb.org/one.jpg', 'https://127.0.0.1/one.jpg', 'https://untrusted.invalid/one.jpg', 'https://image.tmdb.org@evil.invalid/one.jpg'):
            with self.assertRaises(ValueError): a.image_url(url)
        with patch.object(a.socket, 'getaddrinfo', return_value=[(0,0,0,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError): a.image_url('https://image.tmdb.org/one.jpg')

    def test_collector_publishes_cached_image_without_upstream_access(self):
        config_path = self.root / 'config.json'
        c.atomic(config_path, dict(self.config, source='rutor', pages_per_update=1))
        collector = c.Collector(self.root / 'data', config_path)
        with patch.object(c, 'collect', return_value=[film()]): self.assertTrue(collector.update())
        before = collector.snapshot()
        with patch.object(a, 'resolve_tmdb', return_value={'url':'https://image.tmdb.org/t/p/w342/one.jpg','provider':'tmdb','tmdbid':'1'}), patch.object(a, 'download_image', return_value=png()):
            self.assertEqual(collector.artwork_batch(), 1)
        snapshot = collector.snapshot(); route = snapshot['items'][0]['collectorPoster']
        self.assertEqual(before['items'][0]['id'], snapshot['items'][0]['id'])
        server = c.make_server(collector, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with patch.object(a, 'download_image', side_effect=AssertionError('GET must not contact upstream')):
                with urlopen(base + route) as response:
                    self.assertEqual(response.headers.get_content_type(), 'image/png')
                    self.assertEqual(response.read(), png())
            request = Request(base + '/v1/releases', headers={'Authorization':'Bearer ' + collector.key})
            with urlopen(request) as response: public = json.load(response)
            self.assertEqual(public['items'][0]['collectorPoster'], route)
            self.assertNotIn('_topic', public['items'][0])
            with self.assertRaises(HTTPError) as err: urlopen(base + route[:-1] + ('1' if route[-1] != '1' else '2'))
            self.assertEqual(err.exception.code, 404)
        finally: server.shutdown(); server.server_close(); thread.join()

if __name__ == '__main__': unittest.main()
