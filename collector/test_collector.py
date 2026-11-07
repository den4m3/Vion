import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import collector as c


def film(name='Фильм', year='2026', rank='WEB-DL 1080p'):
    return c.item_from_title(f'{name} ({year}) {rank}', '2026-01-01', 'rutor')


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config.json'
        c.atomic(self.config, dict(source='rutor', pages_per_update=2))
        self.engine = c.Collector(self.root / 'data', self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_quality_rejects_bad_sources(self):
        for q in ('CAMRip 1080p', 'HDTS 1080p', 'WEBRip 1080p', 'BDRip 720p'):
            self.assertEqual(c.quality(q), 0)
        self.assertEqual(c.quality('UHD BDRip 1080p HEVC'), 3)
        self.assertEqual(c.quality('UHD BDRemux 2160p HDR'), 10)

    def test_series_and_missing_dates_rejected(self):
        self.assertIsNone(film('Сериал S01E01'))
        self.assertIsNone(c.item_from_title('Фильм (2026) WEB-DL 1080p', '', 'rutor'))

    def test_merge_ranks_and_keep_remakes(self):
        result = c.merge([], [film(), film(rank='BDRemux 1080p'), film(year='2009')], 'rutor')
        self.assertEqual(len(result['items']), 2)
        row = next(x for x in result['items'] if x['releaseInfo'] == '2026')
        self.assertEqual(row['availableQuality'], [1, 4])
        self.assertRegex(row['id'], r'^release:[0-9a-f]{24}$')

    def test_external_ids_bridge_names(self):
        a, b = film('Русское имя'), film('English name')
        a['imdbid'] = b['imdbid'] = 'tt1234567'
        self.assertEqual(len(c.merge([], [a, b], 'rutor')['items']), 1)

    def test_conflicting_ids_never_merge(self):
        a, b = film(), film()
        a['imdbid'], b['imdbid'] = 'tt1', 'tt2'
        self.assertEqual(len(c.merge([], [a, b], 'rutor')['items']), 2)

    def test_ambiguous_title_does_not_join_conflicting_films(self):
        a, b, unknown = film(), film(), film(rank='BDRemux 1080p')
        a['imdbid'], b['imdbid'] = 'tt1', 'tt2'
        result = c.merge([], [a, b, unknown], 'rutor')['items']
        self.assertTrue(any(x.get('imdbid') == 'tt1' for x in result))
        self.assertTrue(any(x.get('imdbid') == 'tt2' for x in result))
        self.assertEqual(len({x['id'] for x in result}), len(result))

    def test_listing_dates_and_no_download_links(self):
        body = '''<div id="index"><table><tr class="gai"><td>1 янв 26</td>
        <td><a href="magnet:secret">download</a><a href="/torrent/123/film">Фильм (2026) WEB-DL 1080p</a></td>
        </tr></table></div>'''
        rows = c.listing(body)
        self.assertEqual(rows[0]['releaseDate'], '2026-01-01')
        self.assertNotIn('magnet', json.dumps(rows))
        self.assertNotIn('sourceUrl', rows[0])

    def test_invalid_listing_does_not_mean_empty_success(self):
        with self.assertRaises(ValueError):
            c.listing('<html>Blocked</html>')

    def test_update_is_atomic_and_error_keeps_old(self):
        with patch.object(c, 'collect', return_value=[film()]):
            self.assertTrue(self.engine.update())
        before = self.engine.snapshot()
        with patch.object(c, 'collect', side_effect=TimeoutError('key=private-secret')), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(self.engine.update())
        self.assertNotIn('private-secret', out.getvalue())
        self.assertEqual(before, self.engine.snapshot())

    def test_empty_update_preserves_catalog_and_reports_error(self):
        with patch.object(c, 'collect', return_value=[film()]):
            self.engine.update()
        before = self.engine.snapshot()
        with patch.object(c, 'collect', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(self.engine.update())
        self.assertEqual(self.engine.error, 'no_qualifying_releases')
        self.assertEqual(before, self.engine.snapshot())

    def test_switch_sources_separate_snapshots(self):
        with patch.object(c, 'collect', return_value=[film()]):
            self.engine.update()
        before = self.engine.snapshot()
        c.atomic(self.config, dict(source='rutracker'))
        self.assertIsNone(self.engine.snapshot())
        with patch.object(c, 'collect', return_value=[film('Другой')]):
            self.engine.update()
        self.assertEqual(self.engine.snapshot()['source'], 'rutracker')
        self.assertEqual(self.engine.snapshot('rutor'), before)

    def test_backfill_resumes_without_repeating_first_page(self):
        pages = []
        def batch(config, page):
            pages.append(page)
            return [film()]
        with patch.object(c, 'collect', side_effect=batch):
            self.engine.update()
            self.engine.update()
        self.assertEqual(pages, [1, 2, 1, 3])

    def test_config_allows_only_one_source(self):
        c.atomic(self.config, dict(source=['rutor', 'rutracker']))
        with self.assertRaises(ValueError):
            c.config_at(self.config)

    def test_bounds_and_source_validation(self):
        for cfg in (dict(source='unknown'), dict(interval_minutes=0), dict(pages_per_update=11)):
            c.atomic(self.config, cfg)
            with self.assertRaises(ValueError):
                c.config_at(self.config)

    def test_key_stable_across_restart(self):
        self.assertGreaterEqual(len(self.engine.key), 32)
        self.assertEqual(self.engine.key, c.Collector(self.root / 'data', self.config).key)

    def test_http_authorization_readonly_and_not_ready(self):
        server = c.make_server(self.engine, '127.0.0.1', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/health') as response:
                self.assertEqual(json.load(response)['schema'], 1)
            with self.assertRaises(HTTPError) as denied:
                urlopen(base + '/v1/releases')
            self.assertEqual(denied.exception.code, 401)
            headers = {'Authorization': 'Bearer ' + self.engine.key}
            with self.assertRaises(HTTPError) as not_ready:
                urlopen(Request(base + '/v1/releases', headers=headers))
            self.assertEqual(not_ready.exception.code, 503)
            with patch.object(c, 'collect', return_value=[film()]):
                self.engine.update()
            with urlopen(Request(base + '/v1/releases', headers=headers)) as response:
                snapshot = json.load(response)
            self.assertEqual(snapshot['schema'], 1)
            self.assertEqual(len(snapshot['items']), 1)
            self.assertNotIn(self.engine.key, json.dumps(snapshot))
            with self.assertRaises(HTTPError) as files:
                urlopen(Request(base + '/data/access-key.txt', headers=headers))
            self.assertEqual(files.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_jackett_missing_key_actionable_error(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'jackett_key_missing'):
                c.collect(dict(source='rutracker', jackett_url='http://127.0.0.1:9117'))

    def test_jackett_normalizes_without_playback_links(self):
        response = dict(Results=[dict(Title='Фильм (2026) WEB-DL 1080p', PublishDate='2026-01-01T12:00:00Z',
                                      Imdb=1234567, MagnetUri='secret', Link='secret')])
        with patch.object(c, 'fetch', return_value=json.dumps(response)):
            rows = c.collect(dict(source='rutracker', jackett_url='http://127.0.0.1:9117', jackett_key='fixture'))
        self.assertEqual(rows[0]['imdbid'], 'tt1234567')
        self.assertNotIn('secret', json.dumps(rows))


if __name__ == '__main__':
    unittest.main()
