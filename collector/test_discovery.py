import unittest
from types import SimpleNamespace
from unittest.mock import patch
import discovery as d


def info(kind='sync', addresses=None, port=8787, props=None):
    return SimpleNamespace(name='Vion TV.' + d.TYPES[kind], port=port,
        properties={b'protocol': b'1', b'kind': kind.encode(), **(props or {})},
        parsed_addresses=lambda: addresses or ['192.168.1.7'])


class DiscoveryTests(unittest.TestCase):
    def test_only_private_ipv4(self):
        for ip in ('192.168.1.7', '10.0.0.2', '172.31.2.4', '169.254.1.4'):
            self.assertTrue(d.private_ipv4(ip))
        for ip in ('127.0.0.1', '0.0.0.0', '8.8.8.8', '172.32.0.1', '::1', '224.0.0.251', 'bad'):
            self.assertFalse(d.private_ipv4(ip))

    def test_record_uses_resolved_host_not_txt_url(self):
        result = d.record(info(props={b'url': b'http://evil.invalid', b'key': b'secret'}), 'sync')
        self.assertEqual(result['url'], 'http://192.168.1.7:8787')
        self.assertNotIn('secret', repr(result))
        self.assertNotIn('evil', repr(result))

    def test_reject_protocol_kind_and_invalid_port(self):
        self.assertIsNone(d.record(info(props={b'protocol': b'2'}), 'sync'))
        self.assertIsNone(d.record(info(), 'collector'))
        self.assertIsNone(d.record(info(port=0), 'sync'))
        self.assertIsNone(d.record(info(port=65536), 'sync'))
        self.assertIsNone(d.record(info(addresses=['8.8.8.8']), 'sync'))

    def test_device_presence_is_not_a_control_url(self):
        result = d.record(info('device'), 'device')
        self.assertNotIn('url', result)
        self.assertEqual(result['kind'], 'device')

    def test_record_removes_terminal_controls(self):
        value = info()
        value.name = '\x1b[31mVion\nTV.' + d.TYPES['sync']
        result = d.record(value, 'sync')
        self.assertNotIn('\x1b', result['name'])
        self.assertNotIn('\n', result['name'])

    def test_queue_is_bounded_and_removal_clears_cache(self):
        service = d.Discovery()
        for n in range(1000):
            service.add_service(None, d.TYPES['sync'], f'Vion {n}')
        self.assertEqual(len(service.pending), 64)
        key = next(iter(service.pending))
        service.entries[key] = {'name': 'TV', 'kind': 'sync'}
        service.remove_service(None, *key)
        self.assertNotIn(key, service.pending)
        self.assertNotIn(key, service.entries)
        service.close()

    def test_no_public_advertising_for_loopback_bind(self):
        with patch('socket.getaddrinfo', return_value=[(0, 0, 0, '', ('192.168.1.5', 0)), (0, 0, 0, '', ('8.8.8.8', 0))]):
            self.assertEqual(d.local_addresses(), ['192.168.1.5'])

    def test_start_and_stop_mdns(self):
        try:
            import zeroconf
        except ImportError:
            self.skipTest('Install requirements.txt for real mDNS test')
        # Actual local sockets; no HTTP requests and no subnet probing.
        service = d.Discovery().start()
        try:
            self.assertIsNotNone(service.browser)
            self.assertIsNone(service.advertisement)
        finally:
            service.close()
        self.assertFalse(service.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
