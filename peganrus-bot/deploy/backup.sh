#!/usr/bin/env bash
# Ежедневная копия базы, хранится 14 дней.
set -euo pipefail
DIR=/var/backups/peganrus
mkdir -p "$DIR"
sudo -u postgres pg_dump -Fc peganrus_db >"$DIR/peganrus_$(date +%Y%m%d_%H%M).dump"
find "$DIR" -name 'peganrus_*.dump' -mtime +14 -delete
echo "$(date) backup ok"
