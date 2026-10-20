"""Flow B — Rescheduling / updating (earlier/later) an already booked
appointment.

Differences from Flow A:
  - Starts from the list of EXISTING appointments (not from a prescription)
  - For each appointment, there is an advance/postpone action
  - System asks for EXTRA confirmation (choose between 'anticipa' and
    'posticipa') added to standard booking confirmation

Contract with Flow.poll_once():
  - load_page()    opens existing appointment and rescheduling view
  - extract_slots() reads available slots for rescheduling
  - execute(slot)  selects + confirms (including extra system confirmations)
"""
from __future__ import annotations

import logging
import os
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
        self.po = selectors.PRENOTAONLINE  # reuse availability list selectors
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
        """Open existing appointment and rescheduling window.

        Mapped real workflow:
          1. Gestisci Prenotazioni menu -> 'I miei appuntamenti'
          2. Find appointment card (match by service/prescription)
          3. Click 'Dettaglio' -> Appointment detail modal
          4. Click 'Anticipa/Posticipa' (riprenota)
          5. 'Completa dati' modal (CONTROL/FOLLOW-UP): radio No -> Confirm
          6. Dove/Quando step with province/date -> search availability
        """
        d = self.driver
        time.sleep(1)
        # 0) Clean up leftover tabs and navigate to prenotaonline SPA
        self._riduci_tab_a_una()
        d.get("https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/riservata")
        time.sleep(4)
        if not self._is_autenticato():
            raise RuntimeError("Sessione scaduta o non valida: reindirizzato a pagina di login")

        # 1) Open 'Gestisci Prenotazioni' (I miei appuntamenti)
        # Click via JS script (more reliable for Angular), with selector fallback
        opened = False
        try:
            opened = d.execute_script("""
                var a = document.querySelector("a[ng-click*='clickGestisciAppuntamenti']") ||
                        document.querySelector("a[ng-click*='clickGestisci']");
                if (a) { a.click(); return true; }
                return false;
            """)
        except Exception:
            pass
        if not opened:
            if not self._click_by_or(self.sel["menu_gestisci"], "menu gestisci appuntamenti") \
               and not self._click_by_or(self.sel["menu_gestisci_alt"], "menu gestisci (alt)"):
                log.warning("flow B: menu Gestisci non trovato")
                if not self._is_autenticato():
                    raise RuntimeError("Sessione scaduta: menu Gestisci non raggiungibile (redirect login)")

        time.sleep(1)
        # Ensure modal closure and switch to 'I miei appuntamenti' tab
        try:
            d.execute_script("""
                var b = document.querySelector("button[ng-click*='messaggiCtrl']");
                if (b) b.click();
                var tab = document.querySelector("a[ng-click*='typeTabAppuntamenti']") ||
                          document.querySelector("a[aria_controls='i_tuoi_appuntamenti']");
                if (tab && !tab.closest('li.active')) tab.click();
            """)
        except Exception:
            pass

        # 2) Dynamic wait for appointment cards (default 30 seconds, configurable via env)
        timeout_attesa = int(os.environ.get("RESCHEDULE_WAIT_TIMEOUT", "30"))
        dettagli = []
        for _ in range(max(1, timeout_attesa)):
            if not self._is_autenticato():
                raise RuntimeError("Sessione scaduta: redirect su login durante il caricamento appuntamenti")
            try:
                d.execute_script("var b=document.querySelector('button[ng-click*=messaggiCtrl]');if(b)b.click();")
            except Exception:
                pass
            dettagli = self._find(self.sel["btn_dettaglio"], mult=True)
            if dettagli:
                break
            time.sleep(1)

        if not dettagli:
            # Save page dump for debugging
            dump = self._salva_debug_dump("reschedule_cards_not_found")
            dump_msg = f" ({dump['message']})" if dump else ""
            if not self._is_autenticato():
                raise RuntimeError(f"Sessione scaduta: redirect su login durante il caricamento appuntamenti{dump_msg}")
            log.warning("flow B: nessuna card appuntamento trovata dopo attesa")
            raise RuntimeError(f"Nessuna card appuntamento trovata per {self._appuntamento}{dump_msg}")

        # Open monitor detail: match by NRE, code, current date, or keywords
        target = None
        ric = (self.monitor.get("ricetta") or "").strip()
        nre = (self.monitor.get("nre") or "").strip()
        cod = (self.monitor.get("codice_appuntamento") or "").strip()
        data_att = (self.monitor.get("data_attuale") or "").strip()
        search_terms = [t.lower() for t in [ric, nre, cod, self._appuntamento] if t]

        import re as _re
        nre_clean = _re.sub(r"[^a-zA-Z0-9]", "", nre).lower() if nre else ""
        cod_clean = _re.sub(r"[^a-zA-Z0-9]", "", cod).lower() if cod else ""

        if search_terms or data_att:
            for b in dettagli:
                try:
                    card = b.find_element(By.XPATH, "ancestor::*[contains(@class,'appuntamento') or contains(@class,'app-unificato')][1]")
                    txt = (card.text or "").lower()
                    txt_clean = _re.sub(r"[^a-zA-Z0-9]", "", txt)

                    # 1) Deterministic match by NRE or appointment code (clean of dashes)
                    if nre_clean and nre_clean in txt_clean:
                        target = b
                        log.info("[%s] Match card appuntamento per NRE (%s)", self.mid, nre)
                        break
                    if cod_clean and cod_clean in txt_clean:
                        target = b
                        log.info("[%s] Match card appuntamento per codice (%s)", self.mid, cod)
                        break

                    # 2) Exact match by full term
                    if any(st in txt for st in search_terms):
                        target = b
                        log.info("[%s] Match card appuntamento per termine completo", self.mid)
                        break

                    # 3) Match by significant keywords of the service
                    if ric:
                        parole = [w for w in _re.findall(r"\b[a-zA-Z]{4,}\b", ric.lower())
                                  if w not in ("senza", "con", "della", "delle", "degli", "visita", "prima")]
                        if parole and any(w in txt for w in parole):
                            target = b
                            log.info("[%s] Match card appuntamento per parole chiave (%s)", self.mid, parole)
                            break

                    # 4) Match by currently booked date
                    if data_att:
                        m_dt = _re.search(r"(\d{2}/\d{2}/\d{4})", data_att)
                        if m_dt and m_dt.group(1) in txt:
                            target = b
                            log.info("[%s] Match card appuntamento per data attuale (%s)", self.mid, m_dt.group(1))
                            break
                except Exception:
                    continue

        if target is None:
            if len(dettagli) == 1 and not search_terms:
                target = dettagli[0]
            else:
                dump = self._salva_debug_dump("reschedule_match_card_not_found")
                dump_msg = f" ({dump['message']})" if dump else ""
                log.error("[%s] Nessuna card appuntamento corrisponde ai criteri del monitor (ricetta='%s', nre='%s', cod='%s', data='%s')",
                          self.mid, ric, nre, cod, data_att)
                raise RuntimeError(f"Nessuna card appuntamento corrisponde ai criteri del monitor {self.mid}{dump_msg}")

        self._click_el(target)
        time.sleep(2)
        # Check if appointment is autonomously manageable
        if self._find(self.sel["non_gestibile"]):
            log.warning("flow B: appuntamento NON gestibile in autonomia")
        # 3) Click Anticipa/Posticipa (riprenota) with explicit wait for modal to open
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        riprenota_btn = None
        for by, val in self.sel["modal_dettaglio"]["btn_riprenota"]:
            try:
                riprenota_btn = WebDriverWait(d, 8).until(
                    EC.element_to_be_clickable((self._by(by), val)))
                if riprenota_btn:
                    break
            except Exception:
                continue
        if riprenota_btn:
            self._click_el(riprenota_btn)
        else:
            log.warning("flow B: bottone Anticipa/Posticipa non cliccabile")
            raise RuntimeError("Modale Dettaglio: bottone Anticipa/Posticipa non cliccabile")
        time.sleep(2)
        # 4) 'Completa dati' modal: radio No then Confirm
        self._gestisci_completa_dati(no=True)
        time.sleep(1)
        # 5) Fill Dove/Quando (province from criteria) and search availability
        try:
            WebDriverWait(d, 15).until(
                lambda drv: drv.find_elements(By.ID, "provincia") and drv.find_elements(By.ID, "quando"))
        except Exception as e:  # noqa: BLE001
            log.warning("flow B: form Dove/Quando non pronto in 15s: %s", e)
            if not self._is_autenticato():
                raise RuntimeError("Sessione scaduta durante il caricamento del form Dove/Quando")
            raise RuntimeError(f"Form Dove/Quando non caricato: {e}")
        first_prov = (self.criteri.get("province") or [""])[0]
        self._provincia_corrente = first_prov
        self._compila_dove_quando_e_cerca(first_prov)

    def _click_by_or(self, sel_list, label="") -> bool:
        el = self._find(sel_list)
        if el is None:
            log.debug("flow B: elemento non trovato: %s", label)
            return False
        self._click_el(el)
        return True

    def _gestisci_completa_dati(self, no=True):
        """'Completa dati' modal: radio No/Yes then Confirm (with WebDriverWait)."""
        d = self.driver
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        # If already on Dove/Quando form, modal is not present
        if d.find_elements(By.ID, "provincia"):
            return
        sel_radio = self.po["modal_completa_dati"]["radio_no" if no else "radio_si"]
        r = self._find(sel_radio)
        if r is not None:
            self._click_el(r)
        # Wait until Confirm button is clickable and click it
        for by, val in self.po["modal_completa_dati"]["btn_conferma"]:
            try:
                el = WebDriverWait(d, 5).until(EC.element_to_be_clickable((self._by(by), val)))
                el.click()
                return
            except Exception:  # noqa: BLE001
                continue
        if not d.find_elements(By.ID, "provincia"):
            log.warning("flow B: bottone conferma completa-dati non trovato")

    def _chiudi_modale_info(self):
        """Close optional info modal (messaggiCtrl)."""
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
        """Fill in Dove/Quando for ONE province (defaults to first configured),
        then search availability.

        Note: does NOT overwrite email/phone if already populated by profile.
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
        # Contact details: do NOT overwrite defaults (already populated email/phone).
        # If field is empty and no value is configured in criteria,
        # leave empty: do NOT insert placeholder, portal will use default.
        tel = d.find_elements(By.ID, "telefono")
        if tel and not tel[0].get_attribute("value") and self.criteri.get("telefono"):
            d.execute_script("var el=document.getElementById('telefono');angular.element(el).val(arguments[0]).triggerHandler('input');return true;", self.criteri["telefono"])
        em = d.find_elements(By.ID, "email")
        if em and not em[0].get_attribute("value") and self.criteri.get("email"):
            d.execute_script("var el=document.getElementById('email');angular.element(el).val(arguments[0]).triggerHandler('input');return true;", self.criteri["email"])
        # Consent (required for validation): real click if not checked yet
        d.execute_script(
            "var c=document.getElementById('consensoPrenotazione');if(c&&!c.checked){c.click();}return true;")
        # Force Angular update of consent if needed
        d.execute_script(
            "var c=document.getElementById('consensoPrenotazione');if(c){angular.element(c).triggerHandler('change');}return true;")
        time.sleep(1)
        # Availability search: try multiple buttons (ricercaDisponibilita, conferma, aggiorna)
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
            raise RuntimeError("Form Dove/Quando: nessun bottone ricerca/conferma abilitato")
        # WAIT for stable outcome (results view or absence modal) - NOT leftover text
        st = self._attendi_esito(prov, timeout_s=60)
        if st.get("stato") not in ("risultati", "no_risultati"):
            log.warning("reschedule: ricerca per %s non completata (stato %s)", prov, st.get("stato"))
            self._salva_debug_dump(f"reschedule_zero_slots_{prov}")

    # ---------------- extract_slots (all provinces or single) --------------
    def extract_slots_current_province(self) -> list[Slot]:
        """Extract slots for the single province currently displayed on screen."""
        return self._estrai_slots_pagina_corrente()

    def extract_slots(self) -> list[Slot]:
        """Extract slots for ALL configured provinces (aggregating results).

        The first province has already been searched in load_page; for remaining ones, uses
        'Modifica ricerca' -> switch province -> 'Aggiorna ricerca'.
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
        """Extract slots from the CURRENT results view.

        The province is NOT taken from self._provincia_corrente (Python variable
        subject to race conditions): it is read from the results HEADER
        displayed by the portal at this moment.
        """
        prov_attesa = getattr(self, "_provincia_corrente", "") or (self.criteri.get("province") or [""])[0]
        st = self._attendi_esito(prov_attesa, timeout_s=60)
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
        """Select slot for the RIGHT province and confirm rescheduling.

        - action = self._ultima_azione ('anticipa' | 'posticipa' | 'approve')
        - navigate UI to slot's province (if needed), then exact match date/time
          (NEVER blind fallback: avoids clicking a slot in another province)
        - after confirmation, extract and send location + notes (+ ICS)
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
        # 2) Booking confirmation modal: reading checkbox + confirm
        chk = self._find(self.po["modal_conferma_prenota"]["checkbox_lettura"])
        if chk is not None and not chk.is_selected():
            self._click_el(chk)
            time.sleep(1)

        from services.appuntamento import parse_conferma
        info = parse_conferma(self.driver.page_source)
        info.data_ora = f"{slot.date_str} - {slot.time_str}" or info.data_ora
        if not info.prestazione:
            info.prestazione = self.monitor.get("ricetta", self._appuntamento)

        conf = self._find(self.po["modal_conferma_prenota"]["btn_conferma"])
        if conf is not None and not conf.get_attribute("disabled"):
            self._click_el(conf)
        elif conf is not None:
            raise RuntimeError("Bottone 'Conferma' disabilitato (lettura non presa visione)")
        else:
            raise RuntimeError("Bottone 'Conferma' non trovato nella modale di conferma")

        # 3) Rigorous portal confirmation check
        ok_conf, cod, err_conf = self._attendi_conferma_prenotazione(timeout_s=30)
        if not ok_conf:
            raise RuntimeError(f"Riprogrammazione non riuscita dal portale: {err_conf}")
        info.codice = cod or info.codice or ""

        # 4) Final verification check in 'I miei appuntamenti'
        log.info("[%s] Controllo finale di riscontro in 'I miei appuntamenti'...", self.mid)
        riscontro_ok = self._verifica_riscontro_miei_appuntamenti(slot, codice=info.codice)
        if not riscontro_ok:
            raise RuntimeError(
                f"Riscontro fallito: l'appuntamento riprogrammato {slot.date_str} {slot.time_str} (cod: {info.codice or 'n/d'}) "
                f"non risulta presente nella sezione 'I miei appuntamenti'"
            )

        # 5) Send summary + ICS + persist in store
        self._invia_riepilogo_dopo_modifica(slot, info=info)
        log.info("flow B: riprogrammazione (%s) confermata %s", azione, slot)

    def _trova_bottone_slot(self, slot):
        """Find 'Verifica e conferma' button for the exact slot (date+time).

        If the slot has a province and the UI does not display it, switch province
        (Modifica ricerca -> province -> Aggiorna) and retry.
        """
        import time
        prov = (slot.extra or {}).get("provincia", "")
        # Base match
        target = self._match_verifica_sulla_pagina(slot)
        if target is not None:
            return target
        # If slot specifies a province and UI is on another, switch province
        if prov:
            log.info("flow B: cambio UI sulla provincia %s (slot proposto)", prov)
            if self._cambia_provincia_e_ricerca(prov).get("stato") == "risultati":
                target = self._match_verifica_sulla_pagina(slot)
        return target

    def _match_verifica_sulla_pagina(self, slot):
        """Exact date+time match on current 'Verifica e conferma' buttons."""
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

    def _invia_riepilogo_dopo_modifica(self, slot, info=None):
        """Extract and send location + notes of confirmed appointment (+ ICS)."""
        from services.appuntamento import parse_conferma, parse_successo
        from services.calendar import write_ics
        if info is None:
            info = parse_conferma(self.driver.page_source)
            info.data_ora = f"{slot.date_str} - {slot.time_str}" or info.data_ora
            if not info.prestazione:
                info.prestazione = self.monitor.get("ricetta", "")
            info.codice = parse_successo(self.driver.page_source) or ""
        # Send summary with location and notes
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
        # Store updated appointment in store
        try:
            self.store.record_prenotazione(
                self.mid, info.codice or "",
                f"{slot.date_str} {slot.time_str}",
                info.__dict__ if hasattr(info, "__dict__") else {},
            )
            log.info("[%s] riprogrammazione registrata nello store (cod %s)", self.mid, info.codice)
        except Exception as ex:  # noqa: BLE001
            log.warning("salvataggio riprogrammazione nello store: %s", ex)
