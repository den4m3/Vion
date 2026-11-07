"""Build public archives from an explicit allowlist; never include config/data."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

FILES = ['collector.py', 'config.example.json', 'README.md', 'test_collector.py',
         'Start-Collector.cmd', 'Show-Key.cmd', 'Dockerfile', 'compose.yaml',
         'vion-collector.service', 'Caddyfile.example', '.dockerignore',
         'THIRD-PARTY-NOTICES.txt', 'LICENSE-Python.txt']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path)
    parser.add_argument('--out', type=Path, default=Path('dist'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    args.out.mkdir(parents=True, exist_ok=True)
    for platform in (['Server', 'Windows-x64'] if args.exe else ['Server']):
        path = args.out / f'VionCollector-{platform}.zip'
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name in FILES:
                archive.write(root / name, name)
            if platform == 'Windows-x64':
                archive.write(args.exe, 'VionCollector.exe')
    lines = []
    for name in ['VionCollector-Server.zip'] + (['VionCollector-Windows-x64.zip'] if args.exe else []):
        lines.append(hashlib.sha256((args.out / name).read_bytes()).hexdigest() + '  ' + name)
    (args.out / 'SHA256SUMS.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('Public archives prepared from allowlisted files; no config.json or data included.')

if __name__ == '__main__':
    main()
