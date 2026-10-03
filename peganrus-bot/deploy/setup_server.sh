#!/usr/bin/env bash
# Установка бота на чистый Ubuntu (запускать от root). Идемпотентно: можно запускать повторно.
# Код должен лежать в /opt/peganrus-bot, файл настроек .env — рядом с кодом.
set -euo pipefail

APP_DIR=/opt/peganrus-bot
APP_USER=peganrus
DB_NAME=peganrus_db
DB_USER=peganrus
BACKUP_DIR=/var/backups/peganrus

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y postgresql python3-venv python3-dev build-essential ufw

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"

DB_PASS_FILE=/root/.peganrus_db_pass
if [ ! -f "$DB_PASS_FILE" ]; then
  tr -dc 'A-Za-z0-9' </dev/urandom | head -c 28 >"$DB_PASS_FILE"
  chmod 600 "$DB_PASS_FILE"
fi
DB_PASS=$(cat "$DB_PASS_FILE")

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASS'"
else
  sudo -u postgres psql -c "ALTER ROLE $DB_USER PASSWORD '$DB_PASS'"
fi
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 \
  || sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"

cd "$APP_DIR"
sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+asyncpg://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME|" .env
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
chmod 600 .env

sudo -u "$APP_USER" python3 -m venv .venv
sudo -u "$APP_USER" .venv/bin/pip install --quiet --upgrade pip
sudo -u "$APP_USER" .venv/bin/pip install --quiet -r requirements.txt

cp deploy/peganrus.service /etc/systemd/system/peganrus.service
install -m 755 deploy/backup.sh /usr/local/bin/peganrus-backup
mkdir -p "$BACKUP_DIR"
cat >/etc/cron.d/peganrus-backup <<CRON
30 4 * * * root /usr/local/bin/peganrus-backup >> /var/log/peganrus-backup.log 2>&1
CRON

grep -q "^precedence ::ffff:0:0/96  100" /etc/gai.conf || echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf

ufw allow OpenSSH
ufw --force enable

systemctl daemon-reload
systemctl enable peganrus
echo "Готово. Запуск: systemctl restart peganrus; логи: journalctl -u peganrus -f"
