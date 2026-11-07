#!/bin/sh
# Existing config, database and named volume are retained.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
command -v docker >/dev/null 2>&1 || { echo 'Install Docker / Container Manager on the NAS first.'; exit 1; }
docker compose version >/dev/null 2>&1 || { echo 'Docker Compose v2 is required.'; exit 1; }
export VION_BIND_ADDRESS=0.0.0.0
docker compose up -d --build
echo 'Open http://NAS-IP:8790/setup in your browser.'
echo 'Browser administration key (keep private):'
docker compose exec -T vion-collector python /app/collector.py --data /data --config /data/config.json --show-setup-key
echo 'Select source, enter API keys, save; then connect Vion using the device key shown in the browser.'
