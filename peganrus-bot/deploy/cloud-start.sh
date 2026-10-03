#!/usr/bin/env bash
# Per-boot startup: PostgreSQL for local development.
# The Telegram bots are not started here. Polling needs real tokens and must not
# share a production bot token with another running process.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [ -x /opt/peganrus-venv/bin/python ] && [ ! -e "$ROOT/.venv" ]; then
  ln -sfn /opt/peganrus-venv "$ROOT/.venv"
fi

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

if [ ! -f "$ROOT/.env" ]; then
  umask 077
  cat > "$ROOT/.env" <<EOF
BOT_TOKEN=${BOT_TOKEN:-local-dev-placeholder}
ADMIN_BOT_TOKEN=${ADMIN_BOT_TOKEN:-local-dev-placeholder}
ADMIN_TELEGRAM_ID=${ADMIN_TELEGRAM_ID:-0}
DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-local-dev-placeholder}
DEEPSEEK_MODEL=${DEEPSEEK_MODEL:-deepseek-chat}
DATABASE_URL=postgresql+asyncpg://peganrus:peganrus@localhost:5432/peganrus_db
AUTO_MIGRATE=true
EOF
fi

echo "PostgreSQL ready for peganrus-bot"
