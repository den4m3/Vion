#!/bin/sh
# Bind to loopback; never publish the management service on the public internet.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
command -v docker >/dev/null 2>&1 || { echo 'Install Docker on your server first.'; exit 1; }
docker compose version >/dev/null 2>&1 || { echo 'Docker Compose v2 is required.'; exit 1; }
export VION_BIND_ADDRESS=127.0.0.1
docker compose up -d --build
echo 'On your computer open an SSH tunnel: ssh -L 8790:127.0.0.1:8790 USER@SERVER'
echo 'Then open http://127.0.0.1:8790/setup in your browser.'
echo 'Browser administration key (keep private):'
docker compose exec -T vion-collector python /app/collector.py --data /data --config /data/config.json --show-setup-key
echo 'For TVs outside this server use your HTTPS reverse proxy or private VPN; no public port is opened by this installer.'
echo 'Repeat upgrades with this script to retain the loopback binding.'
