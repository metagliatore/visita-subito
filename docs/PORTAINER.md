# 🚢 Guida al Deploy con Portainer

Questa guida descrive come installare ed eseguire il **Monitor Fascicolo Sanitario** come **Stack** all'interno di [Portainer](https://www.portainer.io/).

Per garantire la massima compatibilità con l'interfaccia di Portainer (evitando l'errore `open .env: no such file or directory` tipico dei compose che fanno riferimento a file `.env` locali), il progetto fornisce un file dedicato: **[`docker-compose.portainer.yml`](../docker-compose.portainer.yml)**.

---

## 📋 Caratteristiche dello Stack per Portainer

1. **Nessuna dipendenza da file `.env` locali**: tutte le variabili vengono dichiarate nel compose e caricate direttamente dalla sezione **Environment variables** dell'interfaccia grafica di Portainer.
2. **Volumi persistenti Docker (Named Volumes)**: i dati della sessione (cookie SPID, file di stato `state.json`, log applicativi) vengono salvati sul volume `fascicolo_data`, persistendo a riavvii, ricreazioni e aggiornamenti dell'immagine.
3. **Flessibilità dei volumi**: se preferisci mappare cartelle specifiche dell'host (bind mount), puoi sovrascrivere i path impostando le variabili `DATA_PATH` e `CONFIG_PATH`.

---

## 🚀 Metodi di Installazione

Scegli il metodo più adatto al tuo ambiente Portainer:

### Metodo 1: Deploy tramite Repository Git (Consigliato)

Questo metodo è ideale se il repository è ospitato su GitHub/GitLab: Portainer clonerà il codice, compilerà l'immagine con Google Chrome ed eseguirà il container, con la possibilità di abilitare gli aggiornamenti automatici (GitOps).

1. Accedi alla dashboard di Portainer.
2. Nel menu laterale, seleziona l'ambiente (es. **local**) e clicca su **Stacks** -> **Add stack**.
3. Assegna un nome allo stack (es. `fascicolo-monitor`).
4. Come metodo di creazione seleziona **Repository**.
5. Compila i campi:
   - **Repository URL**: L'URL del repository Git (es. `https://github.com/tuo-utente/prenotazioni-fascicolo-sanitario-lombardia`).
   - **Repository reference**: `refs/heads/main` (o il branch desiderato).
   - **Compose path**: inserisci **`docker-compose.portainer.yml`**.
   - *(Opzionale)* Attiva **Automatic updates** (Webhook o Polling) per ricompilare ed eseguire automaticamente il container ad ogni nuovo commit.
6. Scorri verso il basso fino alla sezione **Environment variables**:
   - Clicca su **Advanced mode** per incollare in blocco le variabili d'ambiente (vedi la tabella sotto per il template).
7. Clicca su **Deploy the stack**.

---

### Metodo 2: Deploy tramite Web Editor

Se desideri incollare direttamente il file Compose nell'editor di Portainer:

1. In Portainer, vai su **Stacks** -> **Add stack**.
2. Assegna un nome allo stack (es. `fascicolo-monitor`).
3. Seleziona **Web editor**.
4. Apri e copia l'intero contenuto di **[`docker-compose.portainer.yml`](../docker-compose.portainer.yml)** e incollalo nell'area di testo.
5. Nella sezione **Environment variables**, definisci le variabili con le tue credenziali.
6. *Nota sull'immagine*: se esegui Portainer su un host in cui il codice sorgente è già presente localmente, Portainer userà il builder locale. Se invece Portainer è su un host remoto e non usi Git, compila prima l'immagine localmente o tramite CI/CD con tag `prenotazioni-fascicolo-sanitario-monitor:latest` (o specifica `IMAGE_NAME` nell'env).
7. Clicca su **Deploy the stack**.

---

## 🔑 Variabili d'Ambiente (Portainer Environment Variables)

Puoi copiare questo template direttamente nella modalità **Advanced mode** della sezione *Environment variables* di Portainer:

```env
# --- Telegram Bot ---
TG_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TG_CHAT_ID=123456789
TG_CHAT_ID_SECONDARY=

# --- Autenticazione (spid | cie | manual) ---
AUTH_METHOD=spid

# --- Configurazione SPID ---
# Provider supportati: sielte, poste, aruba, infocert, lepida, namirial, tim...
SPID_PROVIDER=sielte
SPID_USERNAME=il_tuo_username
SPID_PASSWORD=la_tua_password
SPID_OTP_MODE=notifica

# --- Configurazione CIE (opzionale se usi SPID) ---
# Modalità: app (CieID / QR) oppure smartcard (NFC)
CIE_MODE=app
CIE_USERNAME=
CIE_PASSWORD=

# --- Polling & Scheduler ---
POLL_INTERVAL_SECONDS=300
MAX_LOGIN_RETRIES=3
APPROVAL_TIMEOUT_SECONDS=600

# --- Volumi (opzionale: default su named volumes 'fascicolo_data') ---
# Imposta questi percorsi solo se desideri mappare directory assolute sull'host:
# DATA_PATH=/opt/fascicolo/data
# CONFIG_PATH=/opt/fascicolo/config
```

### Dettaglio Variabili

| Variabile | Obbligatoria | Default | Descrizione |
| :--- | :---: | :---: | :--- |
| `TG_TOKEN` | **Sì** | - | Token del bot Telegram generato da [@BotFather](https://t.me/BotFather). |
| `TG_CHAT_ID` | **Sì** | - | Il tuo Chat ID numerico personale (ricavabile da [@userinfobot](https://t.me/userinfobot)). |
| `TG_CHAT_ID_SECONDARY` | No | `""` | ID opzionale per inviare notifiche a un gruppo o canale di backup. |
| `AUTH_METHOD` | No | `spid` | Metodo di accesso principale: `spid`, `cie` o `manual`. |
| `SPID_PROVIDER` | No | `sielte` | Fornitore SPID accreditato: `sielte`, `poste`, `aruba`, `infocert`, `lepida`, `namirial`, ecc. |
| `SPID_USERNAME` | Sì* | `""` | Username o codice fiscale per l'accesso SPID (*se metodo `spid`). |
| `SPID_PASSWORD` | Sì* | `""` | Password dell'account SPID. |
| `SPID_OTP_MODE` | No | `notifica` | `notifica` per autorizzazione push su app (Sielte/Poste), oppure `otp`. |
| `CIE_MODE` | No | `app` | Per accesso CIE: `app` (app CieID) oppure `smartcard`. |
| `CIE_USERNAME` | No | `""` | Numero CIE o Codice Fiscale per accesso CIE Livello 2. |
| `CIE_PASSWORD` | No | `""` | Password della CIE per accesso Livello 2. |
| `POLL_INTERVAL_SECONDS` | No | `300` | Intervallo in secondi tra un controllo e l'altro (es. `300` = 5 minuti). |
| `APPROVAL_TIMEOUT_SECONDS` | No | `600` | Secondi di attesa per la conferma dell'utente su Telegram prima che lo slot scada. |
| `MAX_LOGIN_RETRIES` | No | `3` | Numero massimo di tentativi di login consecutivi falliti prima di mettere in pausa i tentativi. |
| `DATA_PATH` | No | `fascicolo_data` | Directory host per i dati persistenti. Lasciare vuoto per usare il volume Docker gestito. |

---

## 📱 Primo Avvio e Autenticazione (Push SPID)

1. Una volta cliccato su **Deploy the stack**, Portainer avvierà il container `fascicolo-monitor`.
2. Nella dashboard di Portainer, vai su **Containers**, clicca su `fascicolo-monitor` e seleziona **Logs**.
3. Nei log vedrai:
   ```text
   INFO services.scheduler: Sessione scaduta o non pronta (SPID (SielteID) [automatico]), provo ensure_session
   INFO services.telegram_bot: Bot Telegram avviato
   ```
4. Contemporaneamente riceverai un messaggio Telegram dal bot:
   > 📲 *Sto per inviare la notifica SielteID: prepara il telefono e approvala appena arriva!*
5. Apri l'app **SielteID** (o l'app del tuo provider SPID) sullo smartphone e autorizza la richiesta push.
6. Entro pochi secondi nei log di Portainer apparirà:
   ```text
   INFO core.session: Cookie salvati su /app/data/cookies/cookies.pkl
   INFO services.scheduler: Sessione da cookie OK
   ```
7. Da questo momento in poi, lo stack manterrà attiva la sessione in background e potrai controllare tutto tramite Telegram con i comandi `/status`, `/poll`, `/monitora`, ecc.

---

## 🛠️ Manutenzione e Aggiornamento dello Stack

- **Riavviare il container**: Da Portainer -> *Containers* -> seleziona `fascicolo-monitor` -> **Restart**. La sessione verrà ripristinata istantaneamente dal volume senza richiedere un nuovo login se i cookie non sono scaduti.
- **Aggiornare il codice (con metodo Git)**: Nella schermata dello Stack su Portainer, clicca sulla scheda **Editor**, scorri in basso e clicca su **Update the stack** (spuntando l'opzione *Re-pull image and redeploy* se applicabile).
