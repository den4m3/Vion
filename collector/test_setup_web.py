import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import collector as c


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.path = root / 'config.json'
        c.atomic(self.path, dict(source='rutor', tmdb_key='private-api-example', custom_setting='retained'))
        self.engine = c.Collector(root / 'data', self.path)
        self.server = c.make_server(self.engine, '127.0.0.1', 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.admin = (self.engine.data / 'setup-key.txt').read_text()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        self.tmp.cleanup()

    def request(self, path, payload=None, key=None, headers=None):
        heads = {'Authorization': 'Bearer ' + (key if key is not None else self.admin)}
        if payload is not None:
            heads['Content-Type'] = 'application/json'
        heads.update(headers or {})
        req = Request(self.base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=heads)
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def test_public_page_no_credentials_and_security_headers(self):
        status, body, headers = self.request('/setup', key='')
        self.assertEqual(status, 200)
        self.assertIn('Vion Collector', body.decode())
        self.assertNotIn(self.admin.encode(), body)
        self.assertNotIn(self.engine.key.encode(), body)
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_device_key_cannot_administer(self):
        self.assertNotEqual(self.admin, self.engine.key)
        self.assertEqual(self.request('/admin/state', key=self.engine.key)[0], 401)
        self.assertEqual(self.request('/v1/releases', key=self.admin)[0], 401)

    def test_state_never_returns_api_keys(self):
        status, body, _ = self.request('/admin/state')
        self.assertEqual(status, 200)
        self.assertNotIn(b'private-api-example', body)
        self.assertTrue(json.loads(body)['saved']['tmdb_key'])

    def test_save_preserves_secrets_unknown_settings_and_database(self):
        db = self.engine.data / 'releases-rutor.json'
        c.atomic(db, dict(items=[dict(id='retained')]))
        before = db.read_bytes()
        status, _, _ = self.request('/admin/config', dict(source='rutor', tmdb_key='', interval_minutes=30))
        self.assertEqual(status, 200)
        cfg = c.config_at(self.path)
        self.assertEqual(cfg['tmdb_key'], 'private-api-example')
        self.assertEqual(cfg['custom_setting'], 'retained')
        self.assertEqual(cfg['interval_minutes'], 30)
        self.assertEqual(db.read_bytes(), before)
        self.assertTrue(self.engine.refresh_requested.is_set())

    def test_invalid_config_is_atomic(self):
        before = self.path.read_bytes()
        for data in (dict(interval_minutes=1), dict(source='unknown'), dict(source='rutracker'), dict(poster_cache_mb=True), dict(tmdb_key=['wrong']), dict(arbitrary='not-allowed')):
            self.assertEqual(self.request('/admin/config', data)[0], 400)
            self.assertEqual(self.path.read_bytes(), before)

    def test_foreign_origin_and_non_json_rejected(self):
        self.assertEqual(self.request('/admin/config', dict(source='rutor'), headers={'Origin': 'https://unrelated.invalid'})[0], 403)
        self.assertEqual(self.request('/admin/config', dict(source='rutor'), headers={'Content-Type': 'text/plain'})[0], 400)

    def test_key_persists_after_server_recreation(self):
        from setup_web import Setup
        self.assertEqual(Setup(self.engine).key, self.admin)

    def test_authenticated_source_change_and_old_index_retained(self):
        db = self.engine.data / 'releases-rutor.json'
        c.atomic(db, dict(items=[dict(id='old')]))
        status, _, _ = self.request('/admin/config', dict(source='rutracker', jackett_url='http://127.0.0.1:9117', jackett_key='test-jackett'))
        self.assertEqual(status, 200)
        self.assertEqual(c.config_at(self.path)['source'], 'rutracker')
        self.assertEqual(json.loads(db.read_text())['items'][0]['id'], 'old')

    def test_login_attempts_bounded(self):
        for _ in range(10):
            self.assertEqual(self.request('/admin/state', key='wrong')[0], 401)
        self.assertEqual(self.request('/admin/state', key='wrong')[0], 429)


if __name__ == '__main__':
    unittest.main()
