"""Orchestratore: tiene viva la sessione e fa polling dei flussi.

Può girare come 2 processi (bot e poller) o, in modalità semplificata, in un
singolo processo con il bot in un thread.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from core.browser import Browser, BrowserSettings
from core.config import Config, env_token
from core import selectors
from core.session import SessionManager
from flows.base import Flow
from flows.new_booking import NewBookingFlow
from flows.reschedule import RescheduleFlow
from services.approval import ApprovalQueue
from services.ricette import parse_ricette
from services.telegram_bot import TelegramBot
from state.store import Store

log = logging.getLogger(__name__)

FLOW_TYPES = {"new": NewBookingFlow, "reschedule": RescheduleFlow}


class Controller:
    def __init__(self, cfg: Config, bot: TelegramBot):
        self.cfg = cfg
        self.bot = bot
        bot.controller = self

        br = cfg.settings.get("browser", {})
        self.browser = Browser(BrowserSettings(
            binary_path=env_token("CHROME_BIN", br.get("binary_path", "")),
            headless=br.get("headless", True),
            window_size=tuple(br.get("window_size", [1280, 900])),
            timeouts=cfg.settings.get("timeouts", {}),
        ))

        s = cfg.settings.get("session", {})
        self.sess = SessionManager(self.browser, Path_like(s.get("cookie_files", "data/cookies")))
        # le notifiche di login (avvisi Telegram) passano dal bot
        self.sess.on_notify = lambda msg: self.bot.notify(msg)

        a = cfg.settings.get("approval", {})
        # timeout di attesa decisione su TG: override via env APPROVAL_TIMEOUT_SECONDS
        import os as _os
        try:
            timeout_seconds = int(_os.environ.get(
                "APPROVAL_TIMEOUT_SECONDS", a.get("timeout_seconds", 600)))
        except Exception:  # noqa: BLE001
            timeout_seconds = a.get("timeout_seconds", 600)
        self.queue = ApprovalQueue(
            Path_like("data/approval.json"),
            timeout_seconds=timeout_seconds,
            discard_on_timeout=a.get("discard_on_timeout", True),
        )
        self.store = Store(Path_like("data/state.json"))

        self._flows = []
        self._stop = threading.Event()
        self._force = threading.Event()
        self._login_lock = threading.Lock()
        from core.auth import AuthConfig, get_auth_provider
        self.auth_config = AuthConfig.from_settings_and_env(cfg.settings)
        self.auth_provider = get_auth_provider(self.auth_config)
        self._build_flows()
        # --- retry login ---
        self.login_retries = 0
        self.login_bloccato = False
        self.login_in_corso = False
        try:
            self.max_login_retries = int(os.environ.get("MAX_LOGIN_RETRIES", "3"))
        except Exception:  # noqa: BLE001
            self.max_login_retries = 3

    def _build_flows(self):
        self._flows = []
        # Deduplica: i monitor nello store dinamico hanno priorità rispetto a config statico
        monitors_dict = {m["id"]: m for m in self.cfg.active_monitors if m.get("enabled", True)}
        for m in self.store.get_monitors():
            monitors_dict[m["id"]] = m

        for m in monitors_dict.values():
            if self.store.is_disabled(m["id"]):
                continue
            # se la ricetta è già stata prenotata o è di tipo reschedule, usa RescheduleFlow
            if self.store.is_prenotato(m["id"]) or m.get("type") == "reschedule":
                cls = RescheduleFlow
            else:
                cls = FLOW_TYPES.get(m.get("type", "new"), NewBookingFlow)
            self._flows.append(cls(m, self.browser, self.queue, self.bot, self.store))

    # ---- API per il bot ----
    def assicura_sessione_attiva(self) -> bool:
        """Assicura una sessione autenticata prima dei comandi che toccano il portale.

        Ritorna True se autenticato (o dopo nuovo login).
        """
        driver = self.ensure_session()
        return driver is not None and self.sess.session_valid

    def get_appuntamenti(self) -> list | None:
        """Legge gli appuntamenti esistenti dalla sezione 'I miei appuntamenti'.

        Ritorna una lista di dict con tutti i dati esposti dalla card:
          codice, prestazione, data_ora, azienda, presentarsi_in, comune
        (i campi mancanti nella card non vengono inseriti), oppure None se fallisce il login.
        """
        import re
        import time
        from selenium.webdriver.common.by import By
        if not self.assicura_sessione_attiva():
            return None
        driver = self.browser.start()
        # naviga SEMPRE a prenotaonline/riservata (stato home della SPA)
        driver.get("https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/riservata")
        time.sleep(4)
        if not self.sess.is_autenticato(driver):
            log.warning("get_appuntamenti: sessione non valida o reindirizzata al login")
            self.sess.session_valid = False
            return None
        # clicca Gestisci Prenotazioni -> 'I miei appuntamenti'
        try:
            el = driver.find_element(By.CSS_SELECTOR, "a[ng-click*='clickGestisciAppuntamenti']")
            driver.execute_script("arguments[0].click();", el)
            time.sleep(4)
        except Exception as e:  # noqa: BLE001
            log.warning("get_appuntamenti: click Gestisci: %s", e)
        # chiudi eventuale modale info
        try:
            driver.execute_script("var b=document.querySelector('button[ng-click*=messaggiCtrl]');if(b)b.click();")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
        self._dump_pagina_per_debug("appuntamenti", driver)
        html = driver.page_source

        CARD_START = re.compile(r"<li[^>]*app-unificato[^>]*>", re.I)
        FIELD_PAIR = re.compile(
            r"<div[^>]*appuntamento-prenotato-field-title[^>]*>\s*<span>([^<]*)</span>\s*</div>\s*"
            r"<div[^>]*appuntamento-prenotato-field-value[^>]*>\s*<span[^>]*>([^<]*)</span>",
            re.S)
        LI_VAL = re.compile(r"<li[^>]*>([^<]+)</li>", re.I)

        def _valori(card, attr):
            m = re.search(r"<ul[^>]*valori=\"%s\"[^>]*>(.*?)</ul>" % re.escape(attr),
                          card, re.S)
            if not m:
                return []
            out = []
            for lm in LI_VAL.finditer(m.group(1)):
                v = lm.group(1).strip()
                if not v:
                    continue
                low = v.lower()
                if "vedi tutte" in low or "chiudi" in low:
                    continue
                out.append(v)
            return out

        def _campi(card):
            campi = {}
            for m in FIELD_PAIR.finditer(card):
                label = re.sub(r"[\s:]+$", "", m.group(1)).strip()
                val = m.group(2).strip()
                if label and val:
                    campi[label] = val
            return campi

        starts = [m.start() for m in CARD_START.finditer(html)]
        out = []
        for idx, s in enumerate(starts):
            e = starts[idx + 1] if idx + 1 < len(starts) else min(len(html), s + 80000)
            card = html[s:e]
            campi = _campi(card)
            pres = _valori(card, "appuntamentoUnificatoCtrl.prestazioni")
            cod = _valori(card, "appuntamentoUnificatoCtrl.codici_prenotazione")
            prestazione = ", ".join(pres)[:80]
            entry = {
                "prestazione": prestazione,
                "data_ora": campi.get("Data e ora", ""),
                "codice": cod[0] if cod else "",
                "azienda": campi.get("Azienda", ""),
                "presentarsi_in": campi.get("Presentarsi in", ""),
                "comune": campi.get("Comune", ""),
                "indirizzo": campi.get("Indirizzo", ""),
                "cap": campi.get("CAP", ""),
            }
            if not (entry["codice"] or entry["data_ora"] or prestazione):
                continue  # card vuota/placeholder non renderizzata
            out.append(entry)
        return out

    def _dump_pagina_per_debug(self, name, driver):
        """Salva un dump della pagina corrente per debug (solo se il file non esiste già)."""
        import datetime
        p = f"data/study/debug_{name}.html"
        try:
            open(p, "w", encoding="utf-8").write(driver.page_source)
        except Exception:  # noqa: BLE001
            pass

    def get_ricette(self) -> list | None:
        """Legge la pagina Ricette e ritorna le card classificate.

        Mostra sia le ricette attive/prenotabili sia quelle prenotate non
        ancora erogate (con link download), oppure None se fallisce il login.
        """
        import time
        from selenium.webdriver.common.by import By
        if not self.assicura_sessione_attiva():
            return None
        driver = self.browser.start()
        # naviga SEMPRE alla pagina ricette
        driver.get(selectors.RICETTE["url"])
        time.sleep(4)
        if not self.sess.is_autenticato(driver):
            log.warning("get_ricette: sessione non valida o reindirizzata al login")
            self.sess.session_valid = False
            return None
        # ELIMINA I FILTRI: così compaiono TUTTE le ricette (non solo vecchie/parziali)
        try:
            driver.execute_script(
                "var l=document.getElementById('eliminaFiltriLink');"
                "if(l){l.click();return true}return false;", )
            # fallback: chiama la funzione JS se il link non è un id
            driver.execute_script("try{eliminaFiltri()}catch(e){}")
            time.sleep(3)
        except Exception as e:  # noqa: BLE001
            log.warning("elimina filtri ricette: %s", e)
        try:
            with open("data/study/ricette_live.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
        except Exception:  # noqa: BLE001
            pass
        html = driver.page_source
        return parse_ricette(html)

    def decide_request(self, req_id: str, approved: bool, extra: dict = None):
        self.queue.decide(req_id, approved, extra)

    def force_poll(self):
        """Poll forzato richiesto dall'utente (/poll): oltre a svegliare il
        loop, marca il giro come MANUALE (le notifiche di fine-giro 'nessuna
        disponibilità' arrivano solo in questo caso, non nei tick automatici)."""
        self._poll_manual = True
        self._force.set()

    # ---- API di configurazione (per il bot) ----
    def lista_province(self) -> list:
        """Le province selezionabili (dal dropdown del portale)."""
        return selectors.PRENOTAONLINE["province"]

    def set_provincia_default(self, provincia: str) -> bool:
        """Imposta la provincia di default per i nuovi monitor (salva nello store)."""
        if provincia not in self.lista_province():
            return False
        self.store.set_preferenza("provincia_default", provincia)
        return True

    def set_data_dal_default(self, data_dal: str) -> bool:
        """Imposta la data minima 'a partire da' di default."""
        import re
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", data_dal or ""):
            return False
        self.store.set_preferenza("data_dal", data_dal)
        return True

    def aggiungi_monitor(self, tipo: str, descrizione: str, nre: str = "", criteri: dict = None) -> str:
        """Aggiunge un monitor dinamico (nuova prenotazione o appuntamento).

        Ritorna l'id del monitor creato, oppure '' se fallisce.
        `nre` e' il codice ricetta: serve al flow per individuare l'unica
        card corretta (il solo match testuale puo' fallire se la descrizione
        non e' esaustiva).
        """
        if tipo not in FLOW_TYPES:
            return ""
        crit = dict(self.criteri_default())
        crit.update(criteri or {})
        # ID leggibile: tipo + parola chiave ricetta + progressivo
        import re as _re
        parola = _re.sub(r"[^A-Za-z0-9]+", "-", (descrizione or "").strip())[:28].strip("-")
        if not parola:
            parola = tipo
        prog = len(self.store.get_monitors()) + 1
        mid = f"{tipo}-{parola}-{prog}"
        monitor = {"id": mid, "type": tipo, "enabled": True,
                    "ricetta": descrizione, "nre": nre or "", "criteri": crit}
        self.store.add_monitor(monitor)
        self._flows.append(FLOW_TYPES[tipo](monitor, self.browser, self.queue, self.bot, self.store))
        return mid

    def rimuovi_monitor(self, monitor_id: str) -> bool:
        """Rimuove un monitor (dinamico o statico) arrestando i flussi attivi."""
        self.store.remove_monitor(monitor_id)
        self._flows = [f for f in self._flows if f.mid != monitor_id]
        return True

    def converti_in_reschedule(self, monitor_id: str) -> bool:
        """Converte un monitor da prima visita a spostamento o aggiorna la data di riferimento."""
        pren = self.store.get_prenotazione(monitor_id)
        codice = pren.get("codice") or ""
        data_ora = pren.get("data_ora") or ""

        # Trova il monitor configurato
        mon = next((m for m in self.store.get_monitors() if m.get("id") == monitor_id), None)
        if mon is None:
            mon = next((m for m in self.cfg.active_monitors if m.get("id") == monitor_id), None)
            if mon is not None:
                mon = dict(mon)

        if mon is None:
            info = pren.get("info", {})
            mon = {
                "id": monitor_id,
                "enabled": True,
                "ricetta": info.get("prestazione", monitor_id),
                "criteri": self.criteri_default(),
            }

        # Aggiorna a tipo reschedule
        mon["type"] = "reschedule"
        if codice:
            mon["codice_appuntamento"] = codice
        if data_ora:
            mon["data_attuale"] = data_ora
            # Se la visita è fissata per una certa data, imposta data_a alla data prenotata
            # per cercare esclusivamente date migliorative (anticipo)
            try:
                import re as _re
                from datetime import datetime as _dt
                m = _re.search(r"(\d{2}/\d{2}/\d{4})", data_ora)
                if m:
                    data_solo = m.group(1)
                    crit = mon.setdefault("criteri", {})
                    d_target = _dt.strptime(data_solo, "%d/%m/%Y").date()
                    if crit.get("data_a"):
                        try:
                            d_curr = _dt.strptime(crit["data_a"], "%d/%m/%Y").date()
                            if d_curr > d_target:
                                crit["data_a"] = data_solo
                        except Exception:
                            crit["data_a"] = data_solo
                    else:
                        crit["data_a"] = data_solo
            except Exception:  # noqa: BLE001
                pass

        # Salva nello store
        self.store.add_monitor(mon)

        # Ripristina action_state a idle per consentire le future ricerche
        self.store.mark_action(monitor_id, "idle", f"convertito a reschedule (data {data_ora})")

        # Ricrea il flusso corrispondente in self._flows
        self._flows = [f for f in self._flows if f.mid != monitor_id]
        self._flows.append(RescheduleFlow(mon, self.browser, self.queue, self.bot, self.store))
        log.info("[%s] Monitor convertito a RescheduleFlow (data_attuale=%s)", monitor_id, data_ora)
        return True

    def monitors_attivi(self) -> list:
        """Ritorna i monitor attivi (dallo store) non disabilitati per lista/stop."""
        return [m for m in self.store.get_monitors() if not self.store.is_disabled(m.get("id", ""))]

    def criteri_default(self) -> dict:
        prefs = self.store.get_preferenze()
        return {
            "province": [prefs.get("provincia_default", "MILANO CITTA'")],
            "data_dal": prefs.get("data_dal", ""),
            "data_a": prefs.get("data_a", ""),
            "giorni": [], "escludi_giorni": [], "fascia": "",
        }


    @staticmethod
    def _nome_leggibile(monitor) -> str:
        """Nome comprensibile: usa il campo ricetta/descrizione e il tipo."""
        import re as _re
        # estrai la descrizione (pulita)
        desc = ""
        for key in ("ricetta", "descrizione"):
            v = (monitor.get(key) or "").strip() if isinstance(monitor, dict) else str(getattr(monitor, key, "") or "").strip()
            if v:
                desc = v
                break
        # salta suffissi tipo "= 1 --> vedi" (resti del portale)
        desc = _re.sub(r"=\s*\d+.*", "", desc)          # toglie "= 1 --> vedi..." (tutto dopo =)
        desc = _re.sub(r"---*|&gt;|&lt;|-->", " ", desc)  # pulisce frecce/entity
        desc = _re.sub(r"\s*chiudi.*", "", desc, flags=_re.IGNORECASE)
        desc = _re.sub(r"\s*vedi tutte le.*", "", desc, flags=_re.IGNORECASE)
        desc = _re.sub(r"\s+", " ", desc).strip(" -")
        parola = _re.sub(r"[^A-Za-z0-9]+", "-", desc)[:30].strip("-")
        tipo = "spostamento" if (isinstance(monitor, dict) and monitor.get("type") == "reschedule") or (not isinstance(monitor, dict) and getattr(monitor, "type", "") == "reschedule") else "prenotazione"
        return f"{parola or 'monitor'} [{tipo}]"

    def status_text(self) -> str:
        tipo_nome = {"new": "nuova prenotazione", "reschedule": "appuntamento esistente"}
        lines = [f"🤖 Monitor attivi: {len(self._flows)}"]
        # modalità autenticazione
        auth_desc = getattr(self.auth_config, "describe", lambda: "SPID")() if hasattr(self, "auth_config") else "SPID"
        lines.append(f"🔑 Autenticazione: {auth_desc}")
        # stato sessione
        if self.login_bloccato:
            sess = "⛔ Bloccata (raggiunti max tentativi falliti. Invia /poll o un comando per sbloccare)"
        elif getattr(self, "login_in_corso", False):
            sess = f"🔄 Login in corso ({auth_desc}) - segui le istruzioni"
        elif self.sess.is_expired(self.cfg.settings.get("session", {}).get("max_idle_seconds", 21600)):
            sess = "⚠️ Scaduta per inattività (sarà rinnovata al prossimo controllo)"
        elif self.sess.session_valid:
            sess = "✅ Attiva e autenticata"
        elif self.sess.needs_login():
            sess = "⚠️ Mai loggato (serve primo login)"
        else:
            sess = "❌ Non attiva / fallita (invia /poll per riautenticare)"
        lines.append(f"🔐 Stato sessione: {sess}")
        # prossimo controllo programmato
        if hasattr(self, "_next_poll_time"):
            rimanenti = int(self._next_poll_time - time.time())
            if rimanenti < 0:
                rimanenti = 0
            lines.append(f"⏱ Prossimo controllo tra: {rimanenti} s")
        else:
            lines.append("⏱ Prossimo controllo: non avviato")
        if not self._flows:
            lines.append("\nNessun monitor configurato. Usa /monitora per crearne uno.")
        for f in self._flows:
            # trova il monitor corrispondente per il nome leggibile
            mon = next((m for m in self.store.get_monitors() if m.get("id") == f.mid), None)
            # unifica i dati: i monitor dinamici sono dict; quelli statici
            # vengono già passati come dict di ricetto/type/criteri
            if mon is None:
                mon = {"ricetta": f.monitor.get("ricetta", ""), "criteri": f.monitor.get("criteri", {}), "type": f.type}
            nome = self._nome_leggibile(mon)
            stato = self.store.get_action(f.mid)
            stato_txt = {
                "idle": "in attesa di novità",
                "done": "completato (prenotato)",
                "done_pending_choice": "prenotato (in attesa scelta: ferma o continua)",
                "prenotato": "prenotato",
                "pending": "in attesa conferma",
            }.get(stato, stato)
            crit = mon.get("criteri", {}) if isinstance(mon, dict) else {}
            riga = f"\n• {nome}"
            # ricetta reale (la riga "nome" potrebbe essere generica, es. 'monitor')
            ricetta = (mon.get("ricetta") or "").strip() if isinstance(mon, dict) else ""
            # pulisce i resti del portale dal testo della ricetta
            import re as _re
            ricetta = _re.sub(r"=\s*\d+.*", "", ricetta)
            ricetta = _re.sub(r"\s+chiudi.*", "", ricetta, flags=_re.IGNORECASE)
            ricetta = _re.sub(r"\s+vedi tutte le.*", "", ricetta, flags=_re.IGNORECASE)
            if ricetta:
                riga += f"\n  🩺 Ricetta: {ricetta}"
            nre = (mon.get("nre") or "").strip() if isinstance(mon, dict) else ""
            if nre:
                riga += f"\n  🔖 NRE: {nre}"
            riga += f"\n  Tipo: {tipo_nome.get(f.type, f.type)}"
            province = crit.get("province")
            if province:
                if isinstance(province, (list, tuple)):
                    riga += f"\n  📍 Province: {', '.join(p for p in province if p)}"
                else:
                    riga += f"\n  📍 Provincia: {province}"
            dal = crit.get("data_dal") or ""
            al = crit.get("data_a") or ""
            if dal or al:
                riga += f"\n  📅 Range: da {dal or 'oggi'} a {al or 'senza limite'}"
            riga += f"\n  Stato: {stato_txt}"
            lines.append(riga)
        return "\n".join(lines)

    # ---- bootstrap sessione ----
    def ensure_session(self):
        """Assicura una sessione autenticata.

        Se i cookie non bastano, fa il login SielteID con un numero MASSIMO di
        tentativi (MAX_LOGIN_RETRIES, default 3). Se supera i retry ferma i
        tentativi e resta in ascolto (un comando Telegram lo risveglia).
        """
        if self.login_bloccato:
            log.info("Login bloccato (max retry raggiunto): serve un comando per risvegliare")
            return None
        with self._login_lock:
            if self.login_bloccato:
                return None
            try:
                driver = self.browser.start()
                max_idle = self.cfg.settings.get("session", {}).get("max_idle_seconds", 21600)
                if self.sess.is_expired(max_idle):
                    log.info("Sessione scaduta per inattività (max_idle=%ds) -> invalido sessione", max_idle)
                    self.sess.session_valid = False

                if self.sess.session_valid and self.sess.is_autenticato(driver):
                    log.info("Sessione già attiva: %s", driver.current_url[:60])
                    self.login_retries = 0
                    return driver

                if not self.sess.needs_login() and not self.sess.is_expired(max_idle):
                    driver.get("https://www.fascicolosanitario.regione.lombardia.it")
                    time.sleep(2)
                    self.sess.load_cookies(driver)
                    driver.get("https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/riservata")
                    time.sleep(3)
                    if self.sess.is_autenticato(driver):
                        log.info("Sessione da cookie OK: %s", driver.current_url[:60])
                        self.sess.session_valid = True
                        self.login_retries = 0
                        return driver
                    else:
                        log.info("Cookie scaduti o non sufficienti -> avvio login")
                else:
                    log.info("Nessuna sessione valida salvata o cookie scaduti -> avvio login")

                self.sess.session_valid = False
                if self._avvia_login():
                    self.login_retries = 0
                    self.sess.session_valid = True
                    return self.browser.driver
                else:
                    self.sess.session_valid = False
                    self._incrementa_retry()
                    return None
            except Exception as e:  # noqa: BLE001
                log.warning("ensure_session: %s -> login", e)
                self.sess.session_valid = False
                if self._avvia_login():
                    self.login_retries = 0
                    self.sess.session_valid = True
                    return self.browser.driver
                else:
                    self.sess.session_valid = False
                    self._incrementa_retry()
                    return None

    def _incrementa_retry(self):
        self.login_retries += 1
        if self.login_retries >= self.max_login_retries:
            self.login_bloccato = True
            self.bot.notify(
                "⛔ Troppi tentativi di login falliti. Il bot resta in ascolto ma "
                "non riproverà il login in automatico.\nInvia un comando qualunque "
                "(es. /status o /poll) per riprovare il login.")
            log.warning("MAX_LOGIN_RETRIES raggiunto (%s): login bloccato", self.max_login_retries)

    def risveglia(self) -> bool:
        """Riavvio login dopo blocco: resetta il contatore e prova subito."""
        self.login_bloccato = False
        self.login_retries = 0
        log.info("Risveglio da comando: riprovo il login")
        return self._avvia_login()

    def _avvia_login(self) -> bool:
        """Avvia il login secondo la modalità configurata (SPID Sielte, altri SPID, CIE, Manuale)."""
        self.login_in_corso = True
        try:
            self.auth_provider.login(
                session_manager=self.sess,
                browser=self.browser,
                notify_cb=lambda msg: self.bot.notify(msg),
            )
            self.login_in_corso = False
            self.sess.session_valid = True
            self.bot.notify("✅ Login completato con successo! Sessione attiva.")
            return True
        except Exception as e:  # noqa: BLE001
            self.login_in_corso = False
            self.sess.session_valid = False
            log.warning("login fallito: %s", e)
            desc = getattr(self.auth_config, "describe", lambda: "Autenticazione")()
            self.bot.notify(f"❌ Login fallito ({desc}): {e}")
            return False

    # ---- keep-alive loop ----
    def _keep_alive_loop(self):
        ka = self.cfg.settings.get("session", {}).get("keep_alive_seconds", 900)
        while not self._stop.wait(ka):
            try:
                if self.browser.driver is not None and self.sess.session_valid:
                    ok = self.sess.keep_alive(self.browser.driver, selectors.LOGIN_SPID["url_accedi"])
                    if not ok:
                        log.warning("Keep-alive fallito: marco sessione come non valida")
                        self.sess.session_valid = False
            except Exception as e:  # noqa: BLE001
                log.warning("keep-alive loop: %s", e)
                self.sess.session_valid = False

    # ---- polling loop ----
    def _poll_loop(self):
        # intervallo configurabile via env POLL_INTERVAL_SECONDS (default dagli YAML)
        iv = int(os.environ.get("POLL_INTERVAL_SECONDS",
                self.cfg.settings.get("scheduler", {}).get("poll_interval_seconds", 300)))
        log.info("Polling attivo: tick ogni %s secondi (%s)", iv, "; env POLL_INTERVAL_SECONDS" if "POLL_INTERVAL_SECONDS" in os.environ else "config")
        self._next_poll_time = time.time() + iv
        self._poll_manual = False
        # serve per forzare il primo giro subito (bootstrap, NON manuale)
        self._force.set()
        while not self._stop.is_set():
            # attesa con polling ogni 1s per reagire subito a /poll
            atteso = 0.0
            while not self._force.is_set():
                if self._stop.is_set():
                    return
                time.sleep(1)
                atteso += 1
                if atteso >= iv:
                    break
            self._force.clear()
            manual = self._poll_manual
            self._poll_manual = False
            self._run_poll(manual=manual)
            self._next_poll_time = time.time() + iv

    def _run_poll(self, manual: bool = False):
        """Esegue un giro di controllo su tutti i monitor attivi.

        manual=True se il giro è stato richiesto esplicitamente con /poll:
        solo in quel caso i flow inviano le notifiche di fine-giro "nessuna
        disponibilità".

        Se /poll è arrivato durante il giro (self._force settato), interrompe
        i flow rimanenti: il loop ricomincia subito (l'utente ha chiesto un
        nuovo controllo).
        """
        max_idle = self.cfg.settings.get("session", {}).get("max_idle_seconds", 21600)
        sessione_scaduta = self.sess.is_expired(max_idle)
        driver_ok = self.browser.driver is not None and self.sess.is_autenticato(self.browser.driver)

        if sessione_scaduta or not self.sess.session_valid or not driver_ok:
            self.sess.session_valid = False
            auth_desc = getattr(self.auth_config, "describe", lambda: "SPID")() if hasattr(self, "auth_config") else "SPID"
            if manual:
                self.bot.notify(f"🔍 Sessione non attiva o scaduta ({auth_desc}). Avvio rinnovo sessione...")
                if not self.assicura_sessione_attiva():
                    self.bot.notify(f"❌ Polling interrotto: impossibile autenticare la sessione ({auth_desc}).")
                    return
            else:
                log.info("Sessione scaduta o non pronta (%s), provo ensure_session", auth_desc)
                if not self.ensure_session():
                    log.info("Sessione ancora non pronta, salto polling automatico")
                    return

        for f in list(self._flows):
            # interruzione: /poll richiesto durante il giro -> esci subito
            if self._force.is_set():
                log.info("Polling interrotto a metà: /poll richiesto dall'utente")
                break
            log.info("Polling %s (%s)", f.mid, f.type)
            try:
                f.poll_once(manual=manual)
            except Exception as e:  # noqa: BLE001
                log.exception("Polling %s fallito: %s", f.mid, e)
                err_str = str(e).lower()
                if "login" in err_str or "autentica" in err_str or "sessione" in err_str:
                    self.sess.session_valid = False
            # ricontrolla anche dopo un flow lungo (il /poll può essere arrivato)
            if self._force.is_set():
                log.info("Polling interrotto:a fine %s: /poll richiesto dall'utente", f.mid)
                break

    # ---- main ----
    def run(self, run_bot: bool = True):
        threads = [
            threading.Thread(target=self._keep_alive_loop, daemon=True),
            threading.Thread(target=self._poll_loop, daemon=True),
        ]
        for t in threads:
            t.start()
        if run_bot:
            self.bot.run()  # bloccante
        else:
            log.info("Modalità poller-only (avvia il bot in un altro processo)")
            try:
                while not self._stop.wait(1):
                    pass
            except KeyboardInterrupt:
                pass


def Path_like(p):
    from pathlib import Path
    return Path(p)
