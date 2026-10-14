#!/bin/bash
# Установка сервиса на VPS: зависимости, systemd, запуск.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

python3 -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install -q -r "$ROOT/requirements.txt"

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "Создан .env - при необходимости впишите LLM_API_KEY"
fi
chmod 600 "$ROOT/.env"
mkdir -p "$ROOT/data"

cp "$ROOT/deploy/skytek-mc.service" /etc/systemd/system/skytek-mc.service
systemctl daemon-reload
systemctl enable --now skytek-mc.service
sleep 2
systemctl --no-pager status skytek-mc.service | head -8
curl -s -o /dev/null -w "http_status=%{http_code}\n" "http://127.0.0.1:${MC_PORT:-8095}/"
