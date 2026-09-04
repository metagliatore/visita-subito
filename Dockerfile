# Fase finale: immagine magra con Chrome headless.
# Usa l'immagine ufficiale Chrome per evitare di spedire il .chrome (436MB).
FROM python:3.11-slim

# Chrome + dipendenze di sistema per il driver
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip xvfb \
    && curl -fsSL -o /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# In Docker ignoriamo il browser locale e usiamo quello di sistema
ENV CHROME_BIN="" \
    TG_TOKEN="" \
    TG_CHAT_ID="" \
    TG_CHAT_ID_SECONDARY=""

# volume per dati persistenti (cookie, stato, logs)
VOLUME ["/app/data"]

CMD ["python", "main.py", "--nobot"]
