#!/bin/bash
set -e

# Avvia Xvfb (display virtuale) in background per permettere a Chrome di girare
# anche se la sessione richiede una finestra visibile (es. durante il login)
Xvfb :99 -screen 0 1280x1024x24 -ac &
export DISPLAY=:99

# Breve attesa per l'avvio del server X
sleep 0.5

# Sostituisce il processo con il comando specificato (es. python main.py)
exec "$@"
