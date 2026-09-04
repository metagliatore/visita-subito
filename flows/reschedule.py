"""Flow B — Aggiornamento/Riprogrammazione (anticipa/posticipa) di una visita
già prenotata.

Differenze col Flow A:
  - si parte dall'elenco degli appuntamenti ESISTENTI (non da una ricetta)
  - per ogni appuntamento c'è l'azione anticipa/posticipa
  - il sistema chiede una conferma EXTRA (scegliere tra 'anticipa' e
    'posticipa') che si aggiunge alla conferma standard della prenotazione

Questo file è predisposto strutturalmente; il dettaglio reale delle schermate
(come si apre l'appuntamento, l'elenco disponibilità per l'aggiornamento, le
conferme) verrà compilato dopo che l'utente mostra il flusso live.

Il contratto con Flow.poll_once():
  - load_page()    apre l'appuntamento esistente e la finestra di aggregamento
  - extract_slots() legge gli slot disponibili per anticipa/posticipa
  - execute(slot)  seleziona + conferma (incluse le conferme extra del sistema)
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from selenium.webdriver.common.by import By

from core import selectors
from flows.base import Flow
from services.matcher import Slot, nessuna_disponibilita

log = logging.getLogger(__name__)


class RescheduleFlow(Flow):
    type = "reschedule"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sel = selectors.RESCHEDULE
        self.po = selectors.PRENOTAONLINE  # riusa i selettori della lista disponibilità
        self._appuntamento = self.monitor.get("appuntamento", self.monitor.get("ricetta", ""))
        self.criteri = self.monitor.get("criteri", {})

    @property
    def driver(self):
        return self.browser.driver

    def _by(self, key):
        return {"id": By.ID, "name": By.NAME, "css": By.CSS_SELECTOR,
                "xpath": By.XPATH, "class": By.CLASS_NAME, "tag": By.TAG_NAME}[key]

    def _find(self, sel_list, mult=False):
        for by_str, value in sel_list:
            try:
                if mult:
                    return self.driver.find_elements(self._by(by_str), value)
                els = self.driver.find_elements(self._by(by_str), value)
                if els:
                    return els[0]
            except Exception:  # noqa: BLE001
                continue
        return [] if mult else None

    def _click_el(self, el, js_fallback=True):
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", el)
            el.click()
        except Exception:  # noqa: BLE001
            if js_fallback:
                self.driver.execute_script("arguments[0].click();", el)
            else:
                raise

    # ---------------- load_page ----------------
    def load_page(self) -> None:
        """Apre l'appuntamento esistente e la finestra di riprogrammazione.

        Flusso reale mappato:
          1. menu Gestisci Prenotazioni -> 'I miei appuntamenti'
          2. trova la card dell'appuntamento (match per prestazione/ricetta)
          3. click 'Dettaglio' -> modale Dettaglio appuntamento
          4. click 'Anticipa/Posticipa' (riprenota)
          5. modale 'Completa dati' (CONTROLLO/FOLLOW-UP): radio No -> Conferma
          6. (atteso) step Dove/Quando con provincia/data -> ricerca disponibilità
        """
        d = self.driver
        time.sleep(1)
        # 0) Naviga SEMPRE alla SPA prenotaonline (stato home, dove c'è il menu)
        if "/prenotaonline/" not in d.current_url:
            d.get("https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/riservata")
            time.sleep(5)
        else:
            # ricarica lo stato home per evitare viste intermedie
            d.get("https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/riservata")
            time.sleep(4)
        # chiudi eventuale modale info (truffe SMS)
        try:
            d.execute_script("var el=document.querySelector('button[ng-click*=messaggiCtrl]');if(el)el.click();")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
        # 1) apri 'Gestisci Prenotazioni' (I miei appuntamenti)
        if not self._click_by_or(self.sel["menu_gestisci"], "menu gestisci appuntamenti") \
           and not self._click_by_or(self.sel["menu_gestisci_alt"], "menu gestisci (alt)"):
            log.warning("flow B: menu Gestisci non trovato")
        time.sleep(3)
        # 2) trova il pulsante 'Dettaglio' (l'appuntamento è quello in lista)
        dettagli = self._find(self.sel["btn_dettaglio"], mult=True)
        if not dettagli:
            log.warning("flow B: nessuna card appuntamento trovata")
            return
        # apri il dettaglio del monitor: match per prestazione o primo disponibile
        target = dettagli[0]
        ric = self.monitor.get("ricetta", "").strip()
        if ric:
            for b in dettagli:
                try:
                    card = b.find_element(By.XPATH, "ancestor::*[contains(@class,'appuntamento')][1]")
                    if ric and ric.lower() in (card.text or "").lower():
                        target = b
                        break
                except Exception:  # noqa: BLE001
                    continue
        self._click_el(target)
        time.sleep(2)
        # controlla se l'appuntamento è gestibile in autonomia
        if self._find(self.sel["non_gestibile"]):
            log.warning("flow B: appuntamento NON gestibile in autonomia")
        # 3) click Anticipa/Posticipa (riprenota)
        self._click_by_or(self.sel["modal_dettaglio"]["btn_riprenota"], "Anticipa/Posticipa")
        time.sleep(2)
        # 4) modale completa-dati: radio No poi Conferma (con attesa cliccabile)
        self._gestisci_completa_dati(no=True)
        time.sleep(2)
        # 5) compila Dove/Quando (provincia da criteri) e cerca disponibilità
        first_prov = (self.criteri.get("province") or [""])[0]
        self._provincia_corrente = first_prov
        self._compila_dove_quando_e_cerca(first_prov)
        time.sleep(6)
        # chiudi eventuale modale info che copre i risultati
        self._chiudi_modale_info()
        time.sleep(2)

    def _click_by_or(self, sel_list, label="") -> bool:
        el = self._find(sel_list)
        if el is None:
            log.debug("flow B: elemento non trovato: %s", label)
            return False
        self._click_el(el)
        return True

    def _gestisci_completa_dati(self, no=True):
        """Modale 'Completa dati': radio No/Si poi Conferma (con WebDriverWait)."""
        d = self.driver
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        sel_radio = self.po["modal_completa_dati"]["radio_no" if no else "radio_si"]
        r = self._find(sel_radio)
        if r is not None:
            self._click_el(r)
        # attende che il bottone Conferma sia cliccabile e lo clicca
        for by, val in self.po["modal_completa_dati"]["btn_conferma"]:
            try:
                el = WebDriverWait(d, 8).until(EC.element_to_be_clickable((self._by(by), val)))
                el.click()
                return
            except Exception:  # noqa: BLE001
                continue
        log.warning("flow B: bottone conferma completa-dati non trovato")

    def _chiudi_modale_info(self):
        """Chiude eventuale modale informativa (messaggiCtrl)."""
        d = self.driver
        try:
            els = d.find_elements(By.CSS_SELECTOR,
                "button[ng-click*='messaggiCtrl.close']")
            clicked = False
            for e in els:
                try:
                    if e.is_displayed():
                        d.execute_script("arguments[0].click();", e)
                        clicked = True
                except Exception:  # noqa: BLE001
                    pass
            return clicked
        except Exception as e:  # noqa: BLE001
            log.debug("chiudi modale info: %s", e)
            return False

    def _compila_dove_quando_e_cerca(self, provincia=None):
        """Compila Dove/Quando per UNA provincia (default la prima configurata),
        poi ricerca disponibilità.

        NB: NON sovrascrive email/telefono se già valorizzati dal profilo.
        """
        d = self.driver
        province = self.criteri.get("province") or selectors.PRENOTAONLINE["province"]
        prov = provincia or (province[0] if province else "")
        if prov:
            d.execute_script(
                "var sel=document.getElementById('provincia');if(sel){"
                "var o=Array.from(sel.options).find(function(x){return x.textContent===arguments[0]});"
                "if(o){sel.value=o.value;angular.element(sel).triggerHandler('change');}}return true;",
                prov)
        d.execute_script(
            "var el=document.getElementById('quando');if(el){angular.element(el).val(arguments[0]).triggerHandler('input');}return true;",
            self._data_dal_effettiva(self.criteri))
        # recapiti: NON sovrascrivere i default (email/telefono già valorizzati).
        # Se il campo è vuoto e non c'è un valore configurato nei criteri,
        # lo lascia vuoto: NON inserire placeholder, il portale userà il default.
        tel = d.find_elements(By.ID, "telefono")
        if tel and not tel[0].get_attribute("value") and self.criteri.get("telefono"):
            d.execute_script("var el=document.getElementById('telefono');angular.element(el).val(arguments[0]).triggerHandler('input');return true;", self.criteri["telefono"])
        em = d.find_elements(By.ID, "email")
        if em and not em[0].get_attribute("value") and self.criteri.get("email"):
            d.execute_script("var el=document.getElementById('email');angular.element(el).val(arguments[0]).triggerHandler('input');return true;", self.criteri["email"])
        # consenso (necessario per validazione): click reale se non ancora checked
        d.execute_script(
            "var c=document.getElementById('consensoPrenotazione');if(c&&!c.checked){c.click();}return true;")
        # forzare anche l'update Angular del consenso se serve
        d.execute_script(
            "var c=document.getElementById('consensoPrenotazione');if(c){angular.element(c).triggerHandler('change');}return true;")
        time.sleep(1)
        # ricerca disponibilità: prova più bottoni (ricercaDisponibilita, conferma, aggiorna)
        avanzo = None
        for sel in [self.po["btn_ricerca_disponibilita"],
                    [("css", "button[ng-click*='doveQuandoCtrl.conferma']")],
                    [("css", "button[ng-click*='doveQuandoModalCtrl.aggiorna']")]]:
            b = self._find(sel)
            if b is not None and not b.get_attribute("disabled"):
                avanzo = b
                break
        if avanzo is not None:
            self._click_el(avanzo)
            log.info("avviata ricerca/spostamento (bottone: %s)", avanzo.get_attribute("ng-click") or "?")
        else:
            log.warning("nessun bottone ricerca/conferma abilitato (form invalido)")

    # ---------------- extract_slots (tutte le province) --------------
    def extract_slots(self) -> list[Slot]:
        """Estrae gli slot per TUTTI le province configurate (aggregando i risultati).

        La prima provincia è già stata cercata in load_page; per le restanti usa
        'Modifica ricerca' -> cambia provincia -> 'Aggiorna ricerca'.
        """
        slots = self._estrai_slots_pagina_corrente()
        province = list(self.criteri.get("province") or [])
        for prov in province[1:]:
            if not self._cambia_provincia_e_ricerca(prov):
                continue
            slots.extend(self._estrai_slots_pagina_corrente())
        return slots

    def _estrai_slots_pagina_corrente(self) -> list[Slot]:
        """Estrae gli slot dalla lista disponibilità della pagina corrente."""
        # provincia corrente (impostata da load_page / _cambia_provincia_e_ricerca)
        if not hasattr(self, "_provincia_corrente"):
            self._provincia_corrente = (self.criteri.get("province") or [""])[0]
        blocks = self._find(self.po["slot_disponibilita"], mult=True)
        slots = []
        for b in blocks:
            try:
                txt = b.text
            except Exception:  # noqa: BLE001
                continue
            m = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}:\d{2})", txt)
            if not m:
                continue
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d/%m/%Y %H:%M")

            def _field(label, stop_label=None):
                mm = re.search(re.escape(label) + r"\s*\n?\s*([^\n]+)", txt)
                if not mm:
                    return ""
                v = mm.group(1).strip()
                if stop_label and stop_label.lower() in v.lower():
                    v = v.split(stop_label)[0].strip()
                return v

            slots.append(Slot(
                datetime=dt,
                extra={
                    "azienda": _field("Azienda", "Comune").strip(" -"),
                    "comune": _field("Comune", "Presentarsi").strip(" -"),
                    "sede": _field("Presentarsi in").split("-->")[0].strip(" -"),
                    "provincia": self._provincia_corrente,
                },
            ))
        if not slots:
            if nessuna_disponibilita(self.driver.page_source):
                log.info("Spostamento: 0 disponibilità per questa ricerca (esito valido).")
        return slots

    def _cambia_provincia_e_ricerca_deprecato(self, provincia: str) -> bool:
        """(Deprecato: ora in base.Flow)"""
        return self._cambia_provincia_e_ricerca(provincia)

    # ---------------- execute ----------------
    def azione_extra(self, slot) -> str:
        return ("\n🗓 Vuoi ANTICIPARE o POSTICIPARE l'appuntamento? "
                "/anticipa oppure /posticipa (attaccato alla conferma).")

    def execute(self, slot) -> None:
        """Seleziona lo slot e conferma la riprogrammazione.

        - azione = self._ultima_azione ('anticipa' | 'posticipa' | 'approve')
        - riusa i passi del flusso di prenotazione (verifica e conferma,
          modale conferma con 'Confermo lettura')
        """
        import time
        azione = getattr(self, "_ultima_azione", "approve")
        log.info("flow B: azione richiesta = %s", azione)
        # 1) 'Verifica e conferma' sullo slot target
        bs = self._find(self.po["btn_verifica_conferma"], mult=True)
        target = None
        for b in bs or []:
            try:
                cont = b.find_element(By.XPATH, "ancestor::*[contains(@class,'appuntamento')][1]")
                txt = cont.text if cont else ""
            except Exception:  # noqa: BLE001
                txt = ""
            if slot.date_str in txt and (slot.time_str in txt):
                target = b
                break
        if target is None and bs:
            target = bs[0]
        if target is None:
            raise RuntimeError("Nessun slot/prestito da confermare")
        self._click_el(target)
        time.sleep(2)
        # 2) modale conferma prenotazione: checkbox lettura + conferma
        chk = self._find(self.po["modal_conferma_prenota"]["checkbox_lettura"])
        if chk is not None and not chk.is_selected():
            self._click_el(chk)
            time.sleep(1)
        conf = self._find(self.po["modal_conferma_prenota"]["btn_conferma"])
        if conf is not None and not conf.get_attribute("disabled"):
            self._click_el(conf)
        time.sleep(3)
        log.info("flow B: riprogrammazione (%s) confermata %s", azione, slot)
