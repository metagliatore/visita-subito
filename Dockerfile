# Fase finale: immagine magra con Chrome headless.
# Usa l'immagine ufficiale Chrome per evitare di spedire il .chrome (436MB).
FROM python:3.11-slim

# Chrome + dipendenze di sistema per il driver e virtual display (Xvfb)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip xvfb xauth \
    && curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# In Docker usiamo il binario di sistema e output non bufferizzato
ENV PYTHONUNBUFFERED=1 \
    CHROME_BIN=/usr/bin/google-chrome

RUN chmod +x /app/entrypoint.sh

# volume per dati persistenti (cookie, stato, logs)
VOLUME ["/app/data"]

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "main.py"]
