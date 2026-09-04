# Arcipelago — Monitor Appuntamenti Fascicolo Sanitario (Lombardia)

Bot che controlla automaticamente le disponibilità per visite sul Fascicolo
Sanitario Regione Lombardia e notifica/chiede conferma via Telegram.

## Architettura

```
┌──────────────┐  file JSON   ┌──────────────────────────────────────────┐
│  Bot Telegram │◄────────────►│  Controller (poller + keep-alive)         │
│  (asincrono)  │  approval    │    • SessionManager (cookie SPID)          │
└──────────────┘               │    • Scheduler (polling)                  │
        ▲                      │    • Flows (NewBooking / Reschedule)       │
        │ notifiche            └───────────────┬──────────────────────────┘
                                               │ Selenium
                                      ┌────────▼────────┐
                                      │ Chrome headless │
                                      └─────────────────┘
```

Due processi separati comunicano via file JSON (`approval.json`):
- **Poller**: esegue i flussi; quando trova uno slot che matcha i criteri,
  crea una richiesta pendente e **aspetta** la decisione dell'utente su TG.
- **Bot**: ascolta i comandi (`/approve <id>`, `/deny <id>`, `/status`, ...).

### Flussi supportati
- **`new` (Flow A)** — prenota una nuova visita da ricetta in PrenotaOnline:
  apri ricetta -> bottone `prenotaAppuntamento` -> modale completa dati (radio
  CONTROLLO/FOLLOW-UP) -> modale info -> Dove/Quando (provincia, data, recapiti,
  consenso) -> `ricercaDisponibilita` -> lista disponibilità (match criteri)
  -> conferma TG -> verifica e conferma.
- **`reschedule` (Flow B)** — anticipa/posticipa una visita esistente: ha un
  passo di conferma EXTRA del sistema (specifico di questo flusso).

### Sessione SPID
Il login è manuale (OTP non automatizzabile): si esegue UNA volta in finestra
visibile. I cookie vengono salvati su `data/cookies/` e mantenuti freschi da un
keep-alive periodico. Alla scadenza il bot avvisa di rifare il login.

## Setup

```bash
# 1. env virtuale (già fatto in questo repo)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # selenum, python-telegram-bot, httpx, PyYAML

# 2. configurazione Telegram (bot creato con @BotFather) in config/settings.yaml
#    oppure via env: TG_TOKEN, TG_CHAT_ID, TG_CHAT_ID_SECONDARY

# 3. avvio
python main.py            # poller + bot
python main.py --nobot    # solo poller (avvia bot in altro processo)
```

## Stato attuale / prossimi passi

- [x] Ambiente: venv + Chrome headless + Selenium (driver gestito da Selenium Manager)
- [x] Core: Browser, SessionManager, selettori
- [x] Servizi: matcher, state store, approvazione, bot Telegram, scheduler
- [x] Flussi: NewBooking e Reschedule
- [x] **Flusso reale mappato (sessione Selenium)**: login SielteID completo +
      prenotazione visita end-to-end (modali, Dove/Quando, lista disponibilità)
- [x] Esclusioni: ricette farmaceutiche e analisi/laboratorio NON automatizzate
      (`services/ricette.py` + comando `/ricette`)
- [x] Province, giorni settimanali e fascia oraria mappati dalla modale
      "Modifica ricerca" (`core/selectors.py`)
- [ ] **Continuare studio**: schermo "nessuna disponibilità" e varie province
- [ ] **Gestione via Telegram**: `/ricette`, scegli provincia/giorni/fascia,
      `/monitora <idx>`, `/stop <idx>` per creare/rimuovere monitor dinamici
- [ ] Test end-to-end con login + una prenotazione reale (fermarsi prima del click finale)
- [ ] Dockerfile + docker-compose (fase finale)

## Mappatura portale (riepilogo sessione di studio)

### Login SielteID
1. `idpcwrapper` -> SielteID (indice 10) -> loginform (`#username`,`#password`,`#autorizza`)
2. scelta metodo: `useNotify()` (push app) o `useAPP()` (OTP)
3. consenso dati -> bottone Autorizza
4. -> area privata

### Prenotazione visita (Flow A)
1. Ricette -> "Prenota" sulla card -> nuova scheda `prenotaonline/riservata`
2. `ricettaCtrl.prenotaAppuntamento()`
3. Modale completa dati: radio `controllo` (No) -> `prenotaCompletaDatiCtrl.conferma()`
4. Modale info -> `messaggiCtrl.close()`
5. Dove/Quando: `#provincia`,`#quando`,`#telefono`,`#email`,`#consensoPrenotazione`
   -> `doveQuandoCtrl.ricercaDisponibilita()`
6. Lista disponibilità: slot `[ng-repeat='disponibilita in disponibilitaCtrl.valori']`
   con Data/ora `DD/MM/YYYY - HH:MM`, Azienda, Comune, Presentarsi in.
   Pulsanti: "Verifica e conferma", "Ricerca altre date/orari", "Modifica ricerca"
   (-> modale) -> "Aggiorna ricerca" (`doveQuandoModalCtrl.aggiorna()`)
   Messaggio assenza: "Non sono state trovate disponibilità".

### Criteri configurabili (modale "Modifica ricerca")
- Provincia: `#provincia`
- Data: `#quando`
- Giorni settimanali: `#tutti`,`#lun`..`#dom` (`vincoliTemporali.*`)
- Fascia oraria: `#tutte`,`#mattina`,`#pomeriggio`
- Recapiti: `#telefono`,`#email`,`#consensoPrenotazione`

### Province disponibili
BERGAMO, BRESCIA, COMO, CREMONA, LECCO, LODI, MANTOVA, MILANO CITTA',
MILANO PROVINCIA, MONZA E DELLA BRIANZA, PAVIA, SONDRIO, VARESE

### Nota Angular
I campi del form sono `ng-model`: vanno valorizzati con eventi reali
(`angular.element(...).triggerHandler('input'/'change')` e click veri sui
checkbox, in particolare il consenso `#consensoPrenotazione`) perché la
validazione `$invalid` tiene disabilitato il bottone di ricerca.

## Docker (fase finale)

`Dockerfile` incluso. Costruisce un'immagine magra con Chrome; config da env.

```bash
docker compose up --build
```
