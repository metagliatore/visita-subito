# 🏥 Monitor Appuntamenti Fascicolo Sanitario (Regione Lombardia)

Bot automatizzato che controlla periodicamente le disponibilità per visite ed esami medici specialistici sul portale del Fascicolo Sanitario Elettronico e invia notifiche interattive via **Telegram** per approvare o rifiutare le date trovate.

> [!IMPORTANT]
> **Compatibilità e limitazioni attuali**:
> - **Portale regionale**: al momento questo progetto supporta **esclusivamente il portale del Fascicolo Sanitario di Regione Lombardia** (`https://www.fascicolosanitario.regione.lombardia.it`).
> - **Provider SPID**: è attualmente implementato e supportato **un solo provider SPID, ovvero SielteID** (il provider personale utilizzato dall'autore), con approvazione push via app mobile. Gli altri provider (PosteID, Lepida, Aruba, TIM, Namirial, ecc.) o l'accesso CIE non sono al momento implementati nel flusso di login automatico.

---

## 🌟 Funzionalità principali

- **Monitoraggio nuove prenotazioni (Flow A)**: ricerca disponibilità a partire da ricette dematerializzate (NRE) prescritte e non ancora prenotate.
- **Anticipo / spostamento appuntamenti (Flow B)**: monitora visite già fissate per trovare date precedenti (anticipo) o più comode all'interno di un intervallo temporale desiderato.
- **Wizard interattivo su Telegram**: creazione guidata dei monitor direttamente dalla chat con selezione di ricetta/appuntamento, scelta multi-provincia e calendario inline per l'intervallo date.
- **Gestione sessione SPID**: keep-alive automatico della sessione e rinnovo intelligente tramite SielteID con notifica push sul telefono.
- **Richiesta di approvazione a 2 vie**: quando viene trovato uno slot che soddisfa i criteri, il bot invia un messaggio con pulsanti inline (*Conferma* / *Rifiuta*) con timeout configurabile prima di bloccare la prenotazione.
- **Completamente Dockerizzato**: pronto per l'esecuzione 24/7 in background con server X virtuale (`Xvfb`) e volumi persistenti per cookie e stato.

---

## 🏗️ Architettura

```text
┌──────────────────────┐   richieste JSON    ┌───────────────────────────────────────────────┐
│     Bot Telegram     │ ◄─────────────────► │        Controller (poller + scheduler)        │
│ (interfaccia utente) │   approval.json     │  • SessionManager (cookie SPID + keep-alive)  │
└──────────────────────┘                     │  • Gestione flussi (NewBooking / Reschedule)  │
           ▲                                 └───────────────────────┬───────────────────────┘
           │ notifiche                                               │ Selenium WebDriver
           │                                                ┌────────▼────────┐
           └────────────────────────────────────────────────┤ Google Chrome   │
                                                            │ (Xvfb / Headless│
                                                            └─────────────────┘
```

---

## 🚀 Avvio rapido con Docker (Consigliato)

Il modo più semplice e affidabile per eseguire il monitor è tramite **Docker Compose**.

### 1. Prerequisiti
- **Docker** e **Docker Compose** installati (su Linux, Windows con WSL2 o macOS).
- Credenziali SPID **SielteID** (l'unico provider attualmente supportato) con l'app mobile Sielte installata sullo smartphone per autorizzare le notifiche push.

### 2. Configurazione del Bot Telegram
Prima di avviare l'applicazione, è necessario creare il proprio bot Telegram personale:

1. **Creare il bot con BotFather**:
   - Apri Telegram e cerca [@BotFather](https://t.me/BotFather) (l'account ufficiale con la spunta blu).
   - Invia il comando `/newbot`.
   - Inserisci un nome a piacere per il tuo bot (es. `Mio Monitor Sanitario`).
   - Scegli un *username* univoco che termini con `bot` (es. `miomonitor_sanitario_bot`).
   - BotFather ti risponderà fornendoti l'**API Token** (una stringa del tipo `123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`). Questo valore corrisponde a `TG_TOKEN`.

2. **Recuperare il proprio Chat ID**:
   - Cerca su Telegram il bot [@userinfobot](https://t.me/userinfobot) (oppure [@raw_data_bot](https://t.me/raw_data_bot)).
   - Premi `/start`: il bot risponderà mostrandoti il tuo `Id` numerico personale (es. `123456789`). Questo valore corrisponde a `TG_CHAT_ID`.
   - *(Opzionale)* Se vuoi ricevere le notifiche anche su un gruppo Telegram, aggiungi il bot al gruppo e inserisci l'ID del gruppo (che comincia solitamente con un segno meno, es. `-987654321`) in `TG_CHAT_ID_SECONDARY`.

3. **Inizializzare la chat con il bot**:
   - **Passo fondamentale**: Apri la chat con il tuo bot appena creato e clicca su **AVVIA** (`/start`). Telegram impedisce ai bot di inviare messaggi agli utenti che non hanno preventivamente avviato la conversazione.

### 3. Configurazione `.env`
Copia il file di esempio ed inserisci le tue credenziali:

```bash
cp .env.example .env
```

Compila i campi nel file `.env`:
```env
# Telegram
TG_TOKEN=123456789:ABCdefGHIjklMNOpqrSTUvwxYZ
TG_CHAT_ID=123456789
TG_CHAT_ID_SECONDARY=

# SPID (SielteID)
SIELTE_USERNAME=il_tuo_username
SIELTE_PASSWORD=la_tua_password
SIELTE_OTP_MODE=notifica

# Scheduler & timeouts
POLL_INTERVAL_SECONDS=300
MAX_LOGIN_RETRIES=3
APPROVAL_TIMEOUT_SECONDS=600
```

### 4. Avvio del container
Avvia il container in background:

```bash
docker compose up -d
```

### 5. Primo Accesso SPID
Alla prima esecuzione:
1. Il container si avvia e naviga automaticamente verso il portale di autenticazione SielteID.
2. Riceverai un messaggio su Telegram: `📲 Sto per inviare la notifica SielteID: prepara il telefono e approvala appena arriva!`.
3. Apri l'app **SielteID** sul tuo smartphone e approva la richiesta push entro 3 minuti.
4. I cookie di sessione verranno salvati automaticamente nel volume persistente `./data/cookies/cookies.pkl`. Da questo momento in poi il monitor rimarrà attivo autonomamente.

### 6. Gestione del container
- **Visualizzare i log in tempo reale**:
  ```bash
  docker compose logs -f
  ```
- **Verificare lo stato del container**:
  ```bash
  docker compose ps
  ```
- **Fermare il container**:
  ```bash
  docker compose down
  ```
- **Riavviare il container**:
  ```bash
  docker compose restart
  ```

---

## 🤖 Comandi Telegram

Una volta avviato, tutte le operazioni si gestiscono comodamente dalla chat con il bot:

| Comando | Descrizione |
| :--- | :--- |
| `/start` | Messaggio di benvenuto e scorciatoie principali. |
| `/monitora` | **Wizard guidato**: seleziona se monitorare una nuova ricetta o un appuntamento esistente, scegli le province di ricerca e imposta l'intervallo temporale tramite calendario interattivo. |
| `/stop` | Mostra i monitor attivi e permette di interromperne uno con un click. |
| `/status` | Visualizza lo stato di tutti i monitor attivi, lo stato della sessione SPID e il tempo rimanente al prossimo controllo. |
| `/poll` | Forza un giro di verifica immediato delle disponibilità senza attendere l'intervallo schedulato. |
| `/ricette` | Mostra le ricette dematerializzate più recenti presenti sul fascicolo (con stato e pulsante per scaricare il PDF promemoria). |
| `/appuntamenti` | Mostra l'elenco degli appuntamenti già prenotati con data, struttura sanitaria, indirizzo e link rapido a Google Maps. |
| `/help` | Elenco di riepilogo di tutti i comandi disponibili. |

---

## 💻 Esecuzione in locale (Senza Docker)

Se preferisci eseguire l'applicazione direttamente sul sistema operativo:

### 1. Creazione ambiente virtuale
```bash
python3 -m venv .venv
source .venv/bin/activate  # Su Linux/macOS
# oppure su Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configurazione binario Chrome
Assicurati di avere Google Chrome installato. Puoi specificare il percorso del binario in `.env` o in `config/settings.yaml` (lasciandolo vuoto, Selenium Manager rileverà automaticamente il browser di sistema).

### 3. Avvio
```bash
python main.py
```
*(Opzionale: aggiungi il flag `--nobot` se desideri eseguire solo il poller di background senza avviare il listener Telegram nello stesso processo).*

---

## 📂 Struttura del Progetto

```text
├── config/
│   ├── settings.yaml     # Parametri globali (browser, scheduler, logging, telegram)
│   └── monitors.yaml     # Monitor statici iniziali (opzionali)
├── core/
│   ├── browser.py        # Wrapper Selenium Chrome (gestione headless/display)
│   ├── config.py         # Caricamento e merge configurazioni YAML + env
│   ├── selectors.py      # Mappatura selettori CSS/XPath del portale regionale
│   └── session.py        # Gestione sessione SPID, salvataggio cookie, login Sielte
├── flows/
│   ├── base.py           # Classe base per i flussi di ricerca
│   ├── new_booking.py    # Flusso A: prenotazione nuova ricetta
│   └── reschedule.py     # Flusso B: anticipo/spostamento appuntamento
├── services/
│   ├── approval.py       # Gestione coda richieste di conferma (approval.json)
│   ├── ricette.py        # Parser e classificazione ricette (specialistica/laboratorio)
│   ├── scheduler.py      # Orchestratore keep-alive e ciclo di polling
│   └── telegram_bot.py   # Bot Telegram asincrono e wizard interattivi
├── data/                 # Directory persistente (cookie, state.json, log)
├── entrypoint.sh         # Script di avvio container con server X virtuale (Xvfb)
├── Dockerfile            # Immagine Docker Python 3.11 con Google Chrome
└── docker-compose.yml    # Definizione del servizio containerizzato
```

---

## ⚠️ Disclaimer

Questo software è un progetto personale realizzato a scopo di studio e automazione privata. Non è in alcun modo affiliato, sponsorizzato o approvato da Regione Lombardia, ARIA S.p.A. o AgID. L'utilizzo del software è sotto la responsabilità esclusiva dell'utente, nel rispetto dei termini di servizio del portale e delle normative vigenti.
