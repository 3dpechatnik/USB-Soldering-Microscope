#!/usr/bin/env bash
# Idempotent Cloud Agent install for peganrus-bot.
# System packages, virtualenv, local Postgres role/database, and a gitignored .env.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

sudo apt-get update -y
sudo apt-get install -y postgresql postgresql-contrib python3-venv python3-dev build-essential

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

sudo service postgresql start
for _ in $(seq 1 30); do
  if pg_isready -q; then
    break
  fi
  sleep 1
done
pg_isready -q

sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'peganrus') THEN
    CREATE ROLE peganrus LOGIN PASSWORD 'peganrus';
  ELSE
    ALTER ROLE peganrus WITH LOGIN PASSWORD 'peganrus';
  END IF;
END
$$;
SQL

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='peganrus_db'" | grep -q 1; then
  sudo -u postgres createdb -O peganrus peganrus_db
fi

if [ ! -f .env ]; then
  umask 077
  cat > .env <<EOF
BOT_TOKEN=${BOT_TOKEN:-local-dev-placeholder}
ADMIN_BOT_TOKEN=${ADMIN_BOT_TOKEN:-local-dev-placeholder}
ADMIN_TELEGRAM_ID=${ADMIN_TELEGRAM_ID:-0}
DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-local-dev-placeholder}
DEEPSEEK_MODEL=${DEEPSEEK_MODEL:-deepseek-chat}
DATABASE_URL=postgresql+asyncpg://peganrus:peganrus@localhost:5432/peganrus_db
AUTO_MIGRATE=true
EOF
fi

echo "peganrus-bot install complete"
