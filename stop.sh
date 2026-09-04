#!/usr/bin/env bash
# Termina in modo pulito i processi del progetto (bot, session server, test,
# chromedriver e i Chrome del progetto) per liberare la RAM.
set -u

echo "🔍 Cerco processi del progetto..."

# 1) Python del progetto (venv)
PIDS=$(pgrep -f "\.venv/bin/python.*(main.py|session_server.py|test_resched.py|test_e2e.py)" 2>/dev/null || true)
if [ -n "$PIDS" ]; then
  echo "  fermo python: $PIDS"
  kill $PIDS 2>/dev/null || true
  sleep 2
  for p in $PIDS; do kill -9 "$p" 2>/dev/null || true; done
else
  echo "  nessun processo python del progetto"
fi

# 2) chromedriver + chrome del progetto
if pgrep -f "chromedriver" >/dev/null 2>&1; then
  echo "  fermo chromedriver..."
  pkill -9 -f "chromedriver" 2>/dev/null || true
fi
# chrome del progetto = lanciato dal binario .chrome/ o con user-data-dir temp di selenium
if pgrep -f "\.chrome/opt/google/chrome/chrome" >/dev/null 2>&1; then
  echo "  fermo chrome del progetto..."
  pkill -9 -f "\.chrome/opt/google/chrome/chrome" 2>/dev/null || true
fi
if pgrep -f "user-data-dir=/tmp/org.chromium" >/dev/null 2>&1; then
  pkill -9 -f "user-data-dir=/tmp/org.chromium" 2>/dev/null || true
fi

sleep 1
REMANENTE=$(pgrep -f "\.venv/bin/python.*(main.py|session_server.py|test_resched.py|test_e2e.py)" 2>/dev/null || true)
if [ -n "$REMANENTE" ]; then
  echo "⚠️  ancora attivi: $REMANENTE"
else
  echo "✅ tutto terminato (RAM liberata)."
fi