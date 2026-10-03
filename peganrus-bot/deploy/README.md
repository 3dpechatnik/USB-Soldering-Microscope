# Перенос и обновление бота на сервере (Ubuntu)

Установка (один раз): скопировать папку проекта в `/opt/peganrus-bot` (вместе с файлом `.env`) и выполнить
`bash deploy/setup_server.sh`. Скрипт ставит PostgreSQL, создаёт базу и пользователя, виртуальное окружение,
службу `peganrus`, ежедневный бэкап базы и включает firewall (открыт только SSH).

Команды на сервере:
- `systemctl restart peganrus` — перезапуск бота
- `journalctl -u peganrus -f` — логи
- `peganrus-backup` — сделать копию базы сейчас (лежат в `/var/backups/peganrus`, хранятся 14 дней, ежедневно в 04:30)
- Восстановление: `sudo -u postgres pg_restore --no-owner --role=peganrus -d peganrus_db --clean --if-exists ФАЙЛ.dump`

Важно: один токен бота нельзя запускать одновременно в двух местах (будут конфликты опроса Telegram).
IPv6 на сервере отключён для исходящих (в `/etc/gai.conf`), т.к. по IPv6 Telegram не отвечает.
