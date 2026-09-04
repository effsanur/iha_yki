#!/usr/bin/env bash
# Savaşan İHA YKİ başlatıcı (Ubuntu 22.04)
# Kullanım: ./baslat.sh [--sim] [--kaynak mavlink|rosbridge|otomatik]
set -e
cd "$(dirname "$0")"

if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

exec python3 -m yki.main "$@"
