"""Classe base per i flussi di prenotazione.

Un Flusso incapsula: caricare la pagina, estrarre le disponibilità, filtrarle
con il matcher, chiedere conferma via Telegram e (se approvato) eseguire
l'azione reale sul portale tramite i selettori in core/selectors.py.

Sottoclassi da implementare:
    extract_slots()  -> list[Slot]  (parser DOM, da compilare con lo studio)
    execute()        -> azione reale sul portale (prenota / riprogramma)
"""
from __future__ import annotations

import logging

from core.browser import Browser
from core import selectors
from services.approval import ApprovalQueue
from services.matcher import best_match, match_slot
from services.telegram_bot import TelegramBot
from state.store import Store

log = logging.getLogger(__name__)


class Flow:
    type = "base"

    def __init__(self, monitor: dict, browser: Browser,
                 queue: ApprovalQueue, bot: TelegramBot, store: Store):
        self.monitor = monitor
        self.browser = browser
        self.queue = queue
        self.bot = bot
        self.store = store
        self.criteri = monitor.get("criteri", {})

    @property
    def mid(self) -> str:
        return self.monitor["id"]

    # ---- da implementare nelle sottoclassi ----
    def load_page(self) -> None:
        """Naviga e predisponi la pagina col browser."""
        raise NotImplementedError

    def extract_slots(self) -> list:
        """Estrai la lista di Slot disponibili dalla pagina corrente."""
        raise NotImplementedError

    def execute(self, slot) -> None:
        """Esegue la prenotazione/reprogrammazione reale sul portale."""
        raise NotImplementedError

    @staticmethod
    def _data_dal_effettiva(criteri: dict) -> str:
        """Ritorna la data 'a partire da' (GG/MM/AAAA) corretta da usare.

        - Se assente -> oggi
        - Se è nel passato (es. scelta giorni fa) -> oggi (il portale rifiuta
          date antecedenti l'odierna e questo bloccherebbe la ricerca)
        """
        import datetime as _dt
        oggi = _dt.date.today()
        raw = (criteri.get("data_dal") or "").strip()
        if raw:
            try:
                d = _dt.datetime.strptime(raw, "%d/%m/%Y").date()
                if d < oggi:
                    return oggi.strftime("%d/%m/%Y")
                return d.strftime("%d/%m/%Y")
            except Exception:  # noqa: BLE001
                pass
        return oggi.strftime("%d/%m/%Y")

    def azione_extra(self, slot) -> str:
        """Riga extra opzionale mostrata all'utente in fase di conferma.

        Default: nessuna. I sottotipi (es. reschedule) possono restituire un
        messaggio aggiuntivo (es. 'Vuoi anticipare o posticipare?').
        """
        return ""

    def _cambia_provincia_e_ricerca(self, provincia: str) -> bool:
        """Dai risultati attuali: cambia provincia e rilanci ricerca.

        Gestisce 2 casi:
          - siamo sui risultati -> 'Modifica ricerca' (modale) -> Aggiorna ricerca
          - siamo tornati sul form Dove/Quando (0 disponib) -> cambia #provincia
            e rilancia doveQuandoCtrl.ricercaDisponibilita direttamente.
        Aggiorna self._provincia_corrente.
        """
        import time
        from selenium.webdriver.common.by import By
        d = self.driver
        # 1) chiudi eventuali modali che coprono l'area
        try:
            d.execute_script(
                "var els=Array.from(document.querySelectorAll("
                "'button[ng-click*=messaggiCtrl],button[data-dismiss=modal]'));"
                "var txt=Array.from(document.querySelectorAll('button')).filter(function(b){"
                "return /chiudi/i.test(b.textContent||'')});"
                "txt.forEach(function(b){els.push(b)});"
                "els.forEach(function(b){try{b.click()}catch(e){}});")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
        # 2) verifica dove siamo:
        if d.find_elements(By.ID, "provincia"):
            # siamo sul FORM Dove/Quando: cambia provincia e rilancia ricerca
            d.execute_script(
                "var sel=document.getElementById('provincia');if(sel){"
                "var o=Array.from(sel.options).find(function(x){return x.textContent===arguments[0]});"
                "if(o){sel.value=o.value;angular.element(sel).triggerHandler('change');}}return true;",
                provincia)
            time.sleep(1)
            d.execute_script(
                "var c=document.getElementById('consensoPrenotazione');if(c&&!c.checked){c.click();}"
                "if(c){angular.element(c).triggerHandler('change');}")
            b = self._find(selectors.PRENOTAONLINE["btn_ricerca_disponibilita"])
            if b is not None and not b.get_attribute("disabled"):
                self._click_el(b)
                time.sleep(6)
                self._provincia_corrente = provincia
                return True
            log.warning("ricerca (form) disabilitata per %s", provincia)
            return False
        # 3) siamo sui risultati: apri modale 'Modifica ricerca'
        b = self._find(selectors.PRENOTAONLINE["btn_modifica_ricerca"])
        if b is None:
            log.warning("pulsante 'Modifica ricerca' non trovato")
            return False
        self._click_el(b)
        time.sleep(2)
        # cambia provincia nella modale
        d.execute_script(
            "var sel=document.getElementById('provincia');if(sel){"
            "var o=Array.from(sel.options).find(function(x){return x.textContent===arguments[0]});"
            "if(o){sel.value=o.value;angular.element(sel).triggerHandler('change');}}return true;",
            provincia)
        time.sleep(1)
        # Aggiorna ricerca
        b = self._find(selectors.PRENOTAONLINE["btn_aggiorna_ricerca"])
        if b is not None and not b.get_attribute("disabled"):
            self._click_el(b)
            time.sleep(6)
            try:
                d.execute_script(
                    "var el=document.querySelector('button[ng-click*=messaggiCtrl]');if(el)el.click();")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
            self._provincia_corrente = provincia
            return True
        log.warning("'Aggiorna ricerca' non disponibile per %s", provincia)
        return False

    # ---- pipeline condivisa ----
    def poll_once(self) -> str:
        """Esegue un ciclo di controllo. Ritorna un messaggio di esito (o '')."""
        try:
            self.load_page()
            slots = self.extract_slots()
        except Exception as e:  # noqa: BLE001
            log.error("[%s] load/extract errato: %s", self.mid, e)
            return ""

        # filtra per evitare di riproporre slot già visti
        seen = self.store.seen_slots(self.mid)
        fresh = [s for s in slots if s.key not in seen]

        match = best_match(fresh, self.criteri)
        if not match:
            # aggiorna gli slot visti comunque (per i nuovi che arriveranno)
            self.store.record_slots(self.mid, [s.key for s in slots])
            log.info("[%s] nessun match nuovo (%d disponibili)", self.mid, len(slots))
            return ""

        # propongo all'utente
        self.store.record_slots(self.mid, [s.key for s in slots])
        return self._request_approval(match)

    def _request_approval(self, slot) -> str:
        req_id = self.queue.create(self.mid, {"slot": slot.__dict__, "type": self.type})
        # contesto: provincia (se nota) e range temporale del monitor
        prov = (slot.extra or {}).get("provincia", "")
        range_txt = ""
        dal = self.criteri.get("data_dal")
        al = self.criteri.get("data_a")
        if dal or al:
            range_txt = f"\n📅 Range richiesto: {dal or 'da oggi'} → {al or 'senza limite'}"
        msg = (
            f"🩺 Trovata disponibilità ({self.mid})\n"
            f"📅 {slot.date_str} ore {slot.time_str}{f' · {prov}' if prov else ''}{range_txt}\n"
            f"{self.criteri.get('note', '')}"
            f"\n\nVuoi procedere?"
        )
        # pulsanti inline: Approva/Rifiuta; per reschedule anche Anticipa/Posticipa
        righe = [[("✅ Approva", f"decide:approve:{req_id}"),
                  ("❌ Rifiuta", f"decide:deny:{req_id}")]]
        if self.type == "reschedule":
            righe = [
                [("↩️ Anticipa", f"decide:anticipa:{req_id}"),
                 ("↪️ Posticipa", f"decide:posticipa:{req_id}")],
                [("✅ Conferma", f"decide:approve:{req_id}"),
                 ("❌ Rifiuta", f"decide:deny:{req_id}")]
            ]
        self.bot.notify_buttons(msg, righe)
        # attendo la decisione (bloccante, con timeout)
        decision = self.queue.wait_decision(req_id)
        if decision.get("cancelled") or not decision.get("decision"):
            log.info("[%s] approvazione negata/scaduta", self.mid)
            return f"Disponibilità {slot} non confermata."

        # approvata -> esegui l'azione reale
        try:
            self._ultima_azione = (decision.get("extra") or {}).get("azione", "approve")
            self.execute(slot)
            self.store.mark_action(self.mid, "done", str(slot))
            self.bot.notify(f"✅ Appuntamento {slot.date_str} ore {slot.time_str} prenotato.")
            return "ok"
        except Exception as e:  # noqa: BLE001
            log.exception("[%s] esecuzione fallita", self.mid)
            self.bot.notify(f"❌ Errore durante la prenotazione: {e}")
            return "errore"
