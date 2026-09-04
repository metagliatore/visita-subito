#!/usr/bin/env bash
# Avvia il bot assicurandosi di NON lasciare processi residui (RAM pulita).
set -u
cd "$(dirname "$0")"

echo "🛑 Stop processo precedente (se presente)..."
./stop.sh

echo "🚀 Avvio main.py (headless secondo config)..."
mkdir -p data/study
nohup ./.venv/bin/python main.py > data/study/bot_latest.log 2>&1 &
echo "PID: $!"
sleep 5
echo "Log: data/study/bot_latest.log"