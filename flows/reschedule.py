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
from services.matcher import Slot

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
        # attende esito stabile (risultati o assenza) per la prima provincia
        st = self._attendi_esito(first_prov, timeout_s=30)
        if st.get("stato") not in ("risultati", "no_risultati"):
            log.warning("flow B: prima provincia %s non pronta (%s)", first_prov, st.get("stato"))
        # NB: NON chiudiamo qui la modale di ASSENZA: la gestisce extract_slots
        # (altrimenti perderemmo lo stato no_risultati della prima provincia)

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
                "var target=arguments[0];"
                "var sel=document.getElementById('provincia');if(sel){"
                "var o=Array.from(sel.options).filter(function(x){return x.textContent.trim()===target});"
                "if(o.length){sel.value=o[0].value;angular.element(sel).triggerHandler('change');}}return true;",
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
        # ATTESA esito stabile (vista risultati o modale assenza) - NON testi residui
        st = self._attendi_esito(prov, timeout_s=30)
        if st.get("stato") not in ("risultati", "no_risultati"):
            log.warning("reschedule: ricerca per %s non completata (stato %s)", prov, st.get("stato"))
            try:
                from pathlib import Path
                Path("data/study/zero_slots.html").write_text(self.driver.page_source, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass

    # ---------------- extract_slots (tutte le province) --------------
    def extract_slots(self) -> list[Slot]:
        """Estrae gli slot per TUTTI le province configurate (aggregando i risultati).

        La prima provincia è già stata cercata in load_page; per le restanti usa
        'Modifica ricerca' -> cambia provincia -> 'Aggiorna ricerca'.
        """
        slots = self._estrai_slots_pagina_corrente()
        province = list(self.criteri.get("province") or [])
        for prov in province[1:]:
            esito = self._cambia_provincia_e_ricerca(prov)
            if esito.get("stato") in ("risultati", "no_risultati"):
                self._provincia_corrente = esito.get("provincia") or prov
                slots.extend(self._estrai_slots_pagina_corrente())
            else:
                log.warning("reschedule: provincia %s ricerca non completata -> skip", prov)
        return slots

    def _estrai_slots_pagina_corrente(self) -> list[Slot]:
        """Estrae gli slot dalla vista risultati CORRENTE.

        La provincia NON e' presa da self._provincia_corrente (variabile
        Python che puo' restare indietro per la race): viene letta dalla
        TESTATA dei risultati esposta dal portale in questo momento.
        """
        prov_attesa = getattr(self, "_provincia_corrente", "") or (self.criteri.get("province") or [""])[0]
        st = self._attendi_esito(prov_attesa, timeout_s=20)
        stato = st.get("stato")
        if stato == "no_risultati":
            log.info("reschedule: 0 disponibilita per %s (esito valido)", prov_attesa)
            return []
        if stato != "risultati":
            log.warning("reschedule: vista non pronta (%s) per %s", stato, prov_attesa)
            return []
        prov_reale = st.get("provincia") or prov_attesa
        if prov_attesa and prov_reale.upper() != prov_attesa.upper():
            log.warning("reschedule: vista su %s, attesa %s -> salto estrazione (no stale)",
                        prov_reale, prov_attesa)
            return []
        self._chiudi_modali_residui()
        time.sleep(0.5)
        blocks = self._find(self.po["slot_disponibilita"], mult=True)
        log.info("reschedule: blocks slot trovati = %d (prov %s)", len(blocks or []), prov_reale)
        slots = []
        for b in blocks or []:
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
                    "provincia": prov_reale,
                },
            ))
        if not slots:
            log.info("reschedule: 0 blocchi in vista risultati (prov %s)", prov_reale)
        return slots

    # ---------------- execute ----------------
    def azione_extra(self, slot) -> str:
        return ("\n🗓 Vuoi ANTICIPARE o POSTICIPARE l'appuntamento? "
                "/anticipa oppure /posticipa (attaccato alla conferma).")

    def execute(self, slot) -> None:
        """Seleziona lo slot della provincia GIUSTA e conferma la riprogrammazione.

        - azione = self._ultima_azione ('anticipa' | 'posticipa' | 'approve')
        - porta la UI sulla provincia dello slot (se serve), poi match esatto
          data/ora (MAI fallback cieco: eviterebbe di cliccare uno slot di un'al
         provincia diversa)
        - dopo la conferma estrai e invia luogo+note (+ICS)
        """
        import time
        azione = getattr(self, "_ultima_azione", "approve")
        log.info("flow B: azione richiesta = %s", azione)
        target = self._trova_bottone_slot(slot)
        if target is None:
            raise RuntimeError(
                f"Slot {slot.date_str} {slot.time_str} non trovato sulla provincia della UI")
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
        # 3) estrai info dalla schermata/modale e invia riepilogo + ICS
        self._invia_riepilogo_dopo_modifica(slot)
        log.info("flow B: riprogrammazione (%s) confermata %s", azione, slot)

    def _trova_bottone_slot(self, slot):
        """Trova il pulsante 'Verifica e conferma' dello slot esatto (data+ora).

        Se lo slot ha una provincia e la UI non la mostra, cambia provincia
        (Modifica ricerca -> provincia -> Aggiorna) e ritenta.
        """
        import time
        prov = (slot.extra or {}).get("provincia", "")
        # match base
        target = self._match_verifica_sulla_pagina(slot)
        if target is not None:
            return target
        # se lo slot indica una provincia e la UI è su un'altra, cambia provincia
        if prov:
            log.info("flow B: cambio UI sulla provincia %s (slot proposto)", prov)
            if self._cambia_provincia_e_ricerca(prov).get("stato") == "risultati":
                target = self._match_verifica_sulla_pagina(slot)
        return target

    def _match_verifica_sulla_pagina(self, slot):
        """Match esatto data+ora sul pulsante 'Verifica e conferma' corrente."""
        bs = self._find(self.po["btn_verifica_conferma"], mult=True)
        for b in bs or []:
            try:
                cont = b.find_element(By.XPATH, "ancestor::*[contains(@class,'appuntamento')][1]")
                txt = cont.text if cont else ""
            except Exception:  # noqa: BLE001
                txt = ""
            if slot.date_str in txt and (slot.time_str in txt):
                return b
        return None

    def _invia_riepilogo_dopo_modifica(self, slot):
        """Estrae e invia luogo+note dell'appuntamento confermato (+ ICS)."""
        from services.appuntamento import parse_conferma, parse_successo
        from services.calendar import write_ics
        info = parse_conferma(self.driver.page_source)
        info.data_ora = f"{slot.date_str} - {slot.time_str}" or info.data_ora
        if not info.prestazione:
            info.prestazione = self.monitor.get("ricetta", "")
        info.codice = parse_successo(self.driver.page_source) or ""
        # invia riepilogo con luogo e note
        import html as _html
        e = _html.escape
        lines = [
            "📅 <b>Appuntamento confermato</b>",
            f"📍 Data e ora: {e(info.data_ora) or '-'}",
            f"🩺 Prestazione: {e(info.prestazione) or '-'}",
            f"🏥 Azienda: {e(info.azienda) or '-'}",
        ]
        if info.presentarsi_in:
            lines.append(f"🏠 Presentarsi in: {e(info.presentarsi_in)}")
        if info.indirizzo:
            lines.append(f"🧭 Indirizzo: {e(info.indirizzo)}")
        if info.codice:
            lines.append(f"🎫 Codice prenotazione: {e(info.codice)}")
        if info.note:
            lines.append("\n<b>Note esame:</b>")
            lines.append("\n".join(f"· {e(n)}" for n in info.note))
        self.bot.notify("\n".join(lines))
        # ICS
        try:
            from pathlib import Path
            ics = write_ics(info, Path("data/ics"))
            self.bot.send_file(str(ics), caption=f"📅 Evento calendario {info.data_ora}")
        except Exception as ex:  # noqa: BLE001
            log.warning("ICS reschedule: %s", ex)
        self.store.mark_action(self.mid, "done", str(slot))
