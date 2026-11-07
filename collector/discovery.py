"""Vion DNS-SD discovery. Advertisements contain no access keys or API credentials."""
from __future__ import annotations
import ipaddress
import socket
import threading
import time
import uuid

TYPES = {'device': '_vion-device._udp.local.', 'web': '_vion-web._tcp.local.',
         'sync': '_vion-sync._tcp.local.', 'collector': '_vion-collector._tcp.local.'}


def private_ipv4(raw):
    try:
        ip = ipaddress.ip_address(raw)
        return ip.version == 4 and not ip.is_loopback and not ip.is_unspecified and (
            ip in ipaddress.ip_network('10.0.0.0/8') or
            ip in ipaddress.ip_network('172.16.0.0/12') or
            ip in ipaddress.ip_network('192.168.0.0/16') or ip.is_link_local)
    except ValueError:
        return False


def local_addresses():
    # No remote requests: enumerate local host addresses only.
    result = set()
    for name in {socket.gethostname(), socket.getfqdn()}:
        try:
            result.update(row[4][0] for row in socket.getaddrinfo(name, None, socket.AF_INET)
                          if private_ipv4(row[4][0]))
        except OSError:
            pass
    return sorted(result)


def record(info, kind):
    props = info.properties
    if props.get(b'protocol') != b'1' or props.get(b'kind') != kind.encode() or not 0 < info.port < 65536:
        return None
    addresses = sorted(set(ip for ip in info.parsed_addresses() if private_ipv4(ip)))
    if not addresses:
        return None
    name = info.name.removesuffix('.' + TYPES[kind])
    name = ''.join(c for c in name if c.isprintable())[:100]
    # Never promote TXT-supplied URLs, paths, credentials, or commands.
    result = {'name': name, 'kind': kind, 'addresses': addresses, 'port': info.port}
    if kind != 'device':
        result['url'] = f'http://{addresses[0]}:{info.port}'
    return result


class Discovery:
    """One background listener. Bounded name queue; resolve off the mDNS callback."""
    def __init__(self, port=None, host='127.0.0.1', notify=None):
        self.port, self.host, self.notify = port, host, notify
        self.zc = self.browser = self.advertisement = None
        self.lock = threading.Lock()
        self.pending, self.entries = {}, {}
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None

    def start(self):
        from zeroconf import Zeroconf, ServiceBrowser, ServiceInfo, IPVersion
        self.zc = Zeroconf(ip_version=IPVersion.V4Only)
        try:
            addresses = local_addresses() if self.host == '0.0.0.0' else ([self.host] if private_ipv4(self.host) else [])
            if self.port and addresses:
                token = uuid.uuid4().hex[:12]
                self.advertisement = ServiceInfo(
                    TYPES['collector'], f'Vion Collector {socket.gethostname()[:24]} {token}.' + TYPES['collector'],
                    addresses=[socket.inet_aton(ip) for ip in addresses], port=self.port,
                    properties={'protocol': '1', 'kind': 'collector', 'id': token},
                    server=f'vion-collector-{token}.local.')
                self.zc.register_service(self.advertisement, allow_name_change=True)
            self.thread = threading.Thread(target=self.run, daemon=True, name='VionDiscovery')
            self.thread.start()
            self.browser = ServiceBrowser(self.zc, [TYPES[k] for k in ('device', 'web', 'sync')], self)
            return self
        except BaseException:
            self.close()
            raise

    def add_service(self, zc, service_type, name):
        if service_type not in TYPES.values() or len(name) > 255:
            return
        with self.lock:
            if len(self.pending) < 64 and len(self.entries) < 64:
                self.pending[(service_type, name)] = True
        self.wake.set()

    update_service = add_service

    def remove_service(self, zc, service_type, name):
        with self.lock:
            self.pending.pop((service_type, name), None)
            self.entries.pop((service_type, name), None)

    def run(self):
        while not self.stop.is_set():
            self.wake.wait(.5)
            self.wake.clear()
            while not self.stop.is_set():
                with self.lock:
                    if not self.pending:
                        break
                    key = next(iter(self.pending))
                    del self.pending[key]
                try:
                    info = self.zc.get_service_info(*key, timeout=1000)
                    kind = next(k for k, value in TYPES.items() if value == key[0])
                    item = record(info, kind) if info else None
                    if item:
                        with self.lock:
                            changed = self.entries.get(key) != item
                            self.entries[key] = item
                        if changed and self.notify:
                            self.notify(item)
                except Exception:
                    continue

    def results(self):
        with self.lock:
            return sorted((dict(row) for row in self.entries.values()), key=lambda row: (row['name'], row['kind']))

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.browser:
            self.browser.cancel()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
        if self.zc:
            if self.advertisement:
                self.zc.unregister_service(self.advertisement)
            self.zc.close()
        self.zc = None


def discover(seconds=8):
    service = Discovery().start()
    try:
        time.sleep(max(0, min(30, seconds)))
        return service.results()
    finally:
        service.close()
