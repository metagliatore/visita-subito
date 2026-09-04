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
from core.config import Config
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
            binary_path=br.get("binary_path", ""),
            headless=br.get("headless", True),
            window_size=tuple(br.get("window_size", [1280, 900])),
            timeouts=cfg.settings.get("timeouts", {}),
        ))

        s = cfg.settings.get("session", {})
        self.sess = SessionManager(self.browser, Path_like(s.get("cookie_files", "data/cookies")))
        # le notifiche di login (avvisi Telegram) passano dal bot
        self.sess.on_notify = lambda msg: self.bot.notify(msg)

        a = cfg.settings.get("approval", {})
        self.queue = ApprovalQueue(
            Path_like("data/approval.json"),
            timeout_seconds=a.get("timeout_seconds", 600),
            discard_on_timeout=a.get("discard_on_timeout", True),
        )
        self.store = Store(Path_like("data/state.json"))

        self._flows = []
        self._stop = threading.Event()
        self._force = threading.Event()
        self._build_flows()
        # --- retry login ---
        self.login_retries = 0
        self.login_bloccato = False
        try:
            self.max_login_retries = int(os.environ.get("MAX_LOGIN_RETRIES", "3"))
        except Exception:  # noqa: BLE001
            self.max_login_retries = 3

    def _build_flows(self):
        self._flows = []
        monitor_list = list(self.cfg.active_monitors) + list(self.store.get_monitors())
        for m in monitor_list:
            # se la ricetta è già stata prenotata, il monitor deve gestire
            # l'appuntamento esistente (spostamento) e non cercare nuova disponibilità
            if self.store.is_prenotato(m["id"]):
                cls = RescheduleFlow
            else:
                cls = FLOW_TYPES.get(m["type"], NewBookingFlow)
            self._flows.append(cls(m, self.browser, self.queue, self.bot, self.store))

    # ---- API per il bot ----
    def assicura_sessione_attiva(self) -> bool:
        """Assicura una sessione autenticata prima dei comandi che toccano il portale.

        Ritorna True se autenticato (o dopo nuovo login). Il bot avvisa su TG
        del re-login e dell'approvazione push.
        """
        import os
        try:
            driver = self.browser.start()
            driver.get(selectors.LOGIN_SPID["url_accedi"])
            time.sleep(3)
            if self.sess.is_autenticato(driver):
                return True
            user = os.environ.get("SIELTE_USERNAME", "")
            pwd = os.environ.get("SIELTE_PASSWORD", "")
            if user and pwd:
                self.bot.notify("🔄 Sessione scaduta: avvio re-login... approvala la push appena arriva!")
                self.sess.relogin_sielte(username=user, password=pwd)
                return True
            else:
                self.bot.notify("🔑 Sessione scaduta: completa il login SPID nella finestra.")
                self.sess.relogin_manual(selectors.LOGIN_SPID["url_accedi"])
                return True
        except Exception as e:  # noqa: BLE001
            log.warning("assicura_sessione_attiva: %s", e)
            return False

    def get_appuntamenti(self) -> list:
        """Legge gli appuntamenti esistenti dalla sezione 'I miei appuntamenti'."""
        import re
        import time
        from selenium.webdriver.common.by import By
        # verifica/rilogin se la sessione è caduta
        if not self.assicura_sessione_attiva():
            return []
        driver = self.browser.start()
        # naviga SEMPRE a prenotaonline/riservata (stato home della SPA)
        driver.get("https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/riservata")
        time.sleep(4)
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
        html = driver.page_source.replace(r'\"', '"').replace(r'\n', '\n')
        out = []
        # divide per card: ogni card contiene 'Codice prenotazione:' e 'Prestazioni:'
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"[\s\t]+", " ", text)
        out = []
        # formato reale card: "Codice prenotazione: XXXX = 1 --> vedi tutte le...chiudi Prestazioni: NOME = 1 --> ... Data e ora: DD/MM/YYYY - HH:MM"
        pat = re.compile(
            r"Codice prenotazione:?\s*([A-Z0-9\-]+)\s*=?\s*\d*\s*-->.*?"
            r"Prestazioni?\s*:?\s*(.*?)\s*=?\s*\d*\s*-->.*?"
            r"Data e ora:?\s*(\d{2}/\d{2}/\d{4})\s*[-–]?\s*(\d{2}:\d{2})",
            re.S)
        for m in pat.finditer(text):
            prestazione = m.group(2).strip()
            prestazione = re.sub(r"&gt;|&lt;|-->", "", prestazione)
            prestazione = re.sub(r"\s*vedi tutte le.*?chiudi(\s|$)", "", prestazione)
            prestazione = prestazione.strip(" -")
            out.append({
                "codice": m.group(1).strip(),
                "prestazione": prestazione[:80],
                "data_ora": f"{m.group(3)} - {m.group(4)}",
            })
        return out

    def _dump_pagina_per_debug(self, name, driver):
        """Salva un dump della pagina corrente per debug (solo se il file non esiste già)."""
        import datetime
        p = f"data/study/debug_{name}.html"
        try:
            open(p, "w", encoding="utf-8").write(driver.page_source)
        except Exception:  # noqa: BLE001
            pass

    def get_ricette(self) -> list:
        """Legge la pagina Ricette e ritorna le card classificate.

        Mostra sia le ricette attive/prenotabili sia quelle prenotate non
        ancora erogate (con link download).
        """
        import time
        from selenium.webdriver.common.by import By
        if not self.assicura_sessione_attiva():
            return []
        driver = self.browser.start()
        # naviga SEMPRE alla pagina ricette
        driver.get(selectors.RICETTE["url"])
        time.sleep(4)
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

    def aggiungi_monitor(self, tipo: str, descrizione: str, criteri: dict = None) -> str:
        """Aggiunge un monitor dinamico (nuova prenotazione o appuntamento).

        Ritorna l'id del monitor creato, oppure '' se fallisce.
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
                    "ricetta": descrizione, "criteri": crit}
        self.store.add_monitor(monitor)
        self._flows.append(FLOW_TYPES[tipo](monitor, self.browser, self.queue, self.bot, self.store))
        return mid

    def rimuovi_monitor(self, monitor_id: str) -> bool:
        """Rimuove un monitor dinamico."""
        self.store.remove_monitor(monitor_id)
        self._flows = [f for f in self._flows if f.mid != monitor_id]
        return True

    def monitors_attivi(self) -> list:
        """Ritorna i monitor dinamici attivi (dallo store) per lista/stop."""
        return self.store.get_monitors()

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
        # stato sessione
        if self.sess.session_valid:
            sess = "✅ attiva"
        elif self.sess.needs_login():
            sess = "⚠️ mai loggato (serve login)"
        else:
            sess = "⚠️ sessione non verificata"
        lines.append(f"🔐 Sessione SPID: {sess}")
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
            nome = self._nome_leggibile(mon or {"ricetta": f.monitor.get("ricetta", ""), "type": f.type})
            stato = self.store.get_action(f.mid)
            stato_txt = {"idle": "in attesa di novità", "done": "completato",
                         "prenotato": "prenotato", "pending": "in attesa conferma"}.get(stato, stato)
            lines.append(f"\n• {nome}")
            lines.append(f"  Tipo: {tipo_nome.get(f.type, f.type)}")
            lines.append(f"  Stato: {stato_txt}")
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
        if self.sess.needs_login():
            if self._avvia_login():
                self.login_retries = 0
            else:
                self._incrementa_retry()
        else:
            try:
                driver = self.browser.start()
                if self.sess.is_autenticato(driver):
                    log.info("Sessione già attiva: %s", driver.current_url[:60])
                    return driver
                driver.get(selectors.LOGIN_SPID["url_accedi"])
                time.sleep(4)
                self.sess.load_cookies(driver)
                if self.sess.is_autenticato(driver):
                    log.info("Sessione da cookie OK: %s", driver.current_url[:60])
                else:
                    log.info("Cookie non bastano -> login SielteID")
                    if self._avvia_login():
                        self.login_retries = 0
                    else:
                        self._incrementa_retry()
            except Exception as e:  # noqa: BLE001
                log.warning("ensure_session: %s -> login", e)
                if self._avvia_login():
                    self.login_retries = 0
                else:
                    self._incrementa_retry()
        return self.browser.driver

    def _incrementa_retry(self):
        self.login_retries += 1
        if self.login_retries >= self.max_login_retries:
            self.login_bloccato = True
            self.bot.notify(
                "⛔ Troppi tentativi di login falliti. Il bot resta in ascolto ma "
                "non riproverà il login in automatico.\nInvia un comando qualunque "
                "(es. /status) per riavviare il tentativo di login.")
            log.warning("MAX_LOGIN_RETRIES raggiunto (%s): login bloccato", self.max_login_retries)

    def risveglia(self) -> bool:
        """Riavvio login dopo blocco: resetta il contatore e prova subito."""
        self.login_bloccato = False
        self.login_retries = 0
        log.info("Risveglio da comando: riprovo il login")
        if self._avvia_login():
            self.login_retries = 0
        return True

    def _avvia_login(self) -> bool:
        """Avvia il login (SielteID o manuale). Ritorna True se autenticato."""
        import os
        user = os.environ.get("SIELTE_USERNAME", "")
        pwd = os.environ.get("SIELTE_PASSWORD", "")
        try:
            if user and pwd:
                self.bot.notify("🔑 Login SielteID in corso: approva la notifica push sull'app!")
                self.sess.relogin_sielte(username=user, password=pwd)
            else:
                self.bot.notify("🔑 Richiesto login SPID manuale: completa l'accesso nella finestra.")
                self.sess.relogin_manual(selectors.LOGIN_SPID["url_accedi"])
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("login fallito: %s", e)
            return False

    # ---- keep-alive loop ----
    def _keep_alive_loop(self):
        ka = self.cfg.settings.get("session", {}).get("keep_alive_seconds", 900)
        while not self._stop.wait(ka):
            try:
                if self.browser.driver is not None:
                    self.sess.keep_alive(self.browser.driver, selectors.LOGIN_SPID["url_accedi"])
            except Exception as e:  # noqa: BLE001
                log.warning("keep-alive loop: %s", e)

    # ---- polling loop ----
    def _poll_loop(self):
        # intervallo configurabile via env POLL_INTERVAL_SECONDS (default dagli YAML)
        iv = int(os.environ.get("POLL_INTERVAL_SECONDS",
                self.cfg.settings.get("scheduler", {}).get("poll_interval_seconds", 300)))
        log.info("Polling attivo: tick ogni %s secondi (%s)", iv, "; env POLL_INTERVAL_SECONDS" if "POLL_INTERVAL_SECONDS" in os.environ else "config")
        self._next_poll_time = time.time() + iv
        # serve per forzare il primo giro subito
        self._force.set()
        while not self._stop.is_set():
            self._force.wait(iv)
            self._force.clear()
            self._run_poll()
            self._next_poll_time = time.time() + iv

    def _run_poll(self):
        """Esegue un giro di controllo su tutti i monitor attivi.

        Ogni monitor può avere più province nei criteri: iteriamo le province
        dell'utente per ciascun flusso.
        """
        if self.sess.needs_login() or self.browser.driver is None:
            log.info("Sessione non pronta, salto polling")
            return
        for f in list(self._flows):
            log.info("Polling %s (%s)", f.mid, f.type)
            try:
                # per i flow new: esegue il giro di ricerca per le province
                # configurate; extract_slots con i criteri (province multiple)
                f.poll_once()
            except Exception as e:  # noqa: BLE001
                log.exception("Polling %s fallito: %s", f.mid, e)

    # ---- main ----
    def run(self, run_bot: bool = True):
        self.ensure_session()
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
