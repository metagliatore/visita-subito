"""Flow A — New booking (specialist visit) in PrenotaOnline.

Mapped real workflow (Selenium):
  1. From the Prescriptions tab open "Prenota" for prescription -> new `prenotaonline` tab
  2. Button `ricettaCtrl.prenotaAppuntamento(ricetta)`
  3. "Completa dati" modal (CONTROL/FOLLOW-UP): radio No -> confirm
  4. Info modal -> close
  5. Dove/Quando: province (from criteria), date, contact info, consent -> search
  6. Availability list -> extract slots (date/time, company, municipality, facility)
  7. On match -> Telegram confirmation (in base.py) -> verify/confirm

Contract with Flow.poll_once():
  - load_page()    executes the ENTIRE search sequence (from Prescriptions tab up to
                   the results page for the requested province)
  - extract_slots() reads resulting slots
  - execute(slot)  selects the slot and confirms it

Note: Angular (ng-model) fields must be populated with real events (angular
trigger / real clicks), otherwise $invalid prevents search.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from core import selectors
from flows.base import Flow
from services.appuntamento import parse_conferma, parse_successo
from services.calendar import write_ics
from services.matcher import Slot

log = logging.getLogger(__name__)


class NewBookingFlow(Flow):
    type = "new"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sel = selectors.PRENOTAONLINE
        self.ric = selectors.RICETTE
        self._ricetta = self.monitor.get("ricetta", "")
        self._nre = (self.monitor.get("nre") or "").strip()
        self.criteri = self.monitor.get("criteri", {})
        # state to avoid restarting search if already on results
        self._ricerca_fatta = False

    # ================= helpers =================
    @property
    def driver(self):
        return self.browser.driver

    def _by(self, key):
        return {"id": By.ID, "name": By.NAME, "css": By.CSS_SELECTOR,
                "xpath": By.XPATH, "class": By.CLASS_NAME, "tag": By.TAG_NAME}[key]

    def _find(self, sel_list, mult=False, timeout=5):
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

    def _click_by(self, sel_list, label=""):
        el = self._find(sel_list)
        if el is None:
            log.debug("elemento non trovato: %s (%s)", label, sel_list)
            return False
        self._click_el(el)
        log.info("click: %s", label or sel_list)
        return True

    def _set_ng(self, element_id, value):
        """Set an Angular field (by id) with real input+change events.

        Robust: if element does not exist yet (form still rendering),
        returns None WITHOUT raising (angular.element(null) throws
        'Cannot read properties of undefined (reading triggerHandler)').
        """
        import json
        try:
            return self.driver.execute_script(
                "var el=document.getElementById('%s');if(!el)return null;var s=angular.element(el);"
                "s.val(%s).triggerHandler('input').triggerHandler('change');"
                "return el.value;" % (element_id, json.dumps(value)))
        except Exception as e:  # noqa: BLE001
            log.warning("_set_ng(%s): %s", element_id, e)
            return None

    # ================= prescription selection (from Prescriptions tab) =================
    def _riduci_tab_a_una(self) -> None:
        """Close ALL tabs except current.

        Each monitoring cycle opens a new tab (the portal opens 'Prenota' clicks
        in target=_blank): without cleanup, tabs accumulate on every poll and RAM balloons.
        """
        d = self.driver
        try:
            hs = list(d.window_handles or [])
            if len(hs) <= 1:
                return
            current = d.current_window_handle
            for h in hs:
                if h == current:
                    continue
                try:
                    d.switch_to.window(h)
                    d.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                d.switch_to.window(current)
            except Exception:  # noqa: BLE001
                if d.window_handles:
                    d.switch_to.window(d.window_handles[-1])
        except Exception as e:  # noqa: BLE001
            log.warning("pulizia tab: %s", e)

    def _applica_filtro_specialistiche(self):
        """Apply 'Specialistiche' + 'Prescritte' filters to show specialist visits.
        The Prescriptions page defaults to showing medications; submitting filter is required.
        """
        d = self.driver
        try:
            res = d.execute_script(
                "var s=document.querySelector('#specialisticheID');if(s)s.checked=true;"
                "var p=document.querySelector('#prescritteID');if(p)p.checked=true;"
                "var p2=document.querySelector('#accettatePrenotateID');if(p2)p2.checked=true;"
                "var f=document.querySelector('#formFilterPrescrizioni');"
                "if(f){f.submit();return 'submitted'}else{return 'noform'}")
            log.info("filtro specialistiche: %s", res)
            time.sleep(3)
        except Exception as e:  # noqa: BLE001
            log.warning("filtro specialistiche: %s", e)

    def _apri_ricetta_e_prenota(self) -> bool:
        """From Prescriptions tab, click 'Prenota' on the card matching the prescription.

        Returns True if we are on the ready prenotaonline tab (Dove/Quando).
        """
        d = self.driver
        # Ensure we are on the Prescriptions page
        if "/web/areaprivata/ricette" not in d.current_url:
            d.get(selectors.RICETTE["url"])
            time.sleep(3)
        # Apply specialist filter to make visits visible
        self._applica_filtro_specialistiche()
        # 1) Deterministic match by normalized NRE (ignores dashes, spaces, case)
        done = False
        if self._nre:
            import re as _re
            nre_clean = _re.sub(r"[^a-zA-Z0-9]", "", self._nre).lower()
            done = d.execute_script(
                "var clean = function(s) { return String(s || '').replace(/[^a-zA-Z0-9]/g, '').toLowerCase(); };"
                "var q = arguments[0];"
                "var links = Array.from(document.querySelectorAll('a.cambia-visibilita[href*=prenotaonline]'));"
                "var a = links.find(function(l) {"
                "    var c = l.closest('.prescrizioni-row');"
                "    if (!c) return false;"
                "    var cid = clean(c.getAttribute('id'));"
                "    if (cid && cid === q) return true;"
                "    var codSpan = c.querySelector('#codiceRicetta');"
                "    if (codSpan && clean(codSpan.textContent) === q) return true;"
                "    return clean(c.textContent).indexOf(q) > -1;"
                "});"
                "if (!a) return false; a.click(); return true;",
                nre_clean)
            if done:
                log.info("[%s] Ricetta aperta tramite NRE normalizzato (%s)", self.mid, self._nre)

        # 2) Match by service description text (exact and case-insensitive)
        if not done and self._ricetta:
            done = d.execute_script(
                "var q = String(arguments[0]).toLowerCase();"
                "var links = Array.from(document.querySelectorAll('a.cambia-visibilita[href*=prenotaonline]'));"
                "var a = links.find(function(l) {"
                "    var c = l.closest('.prescrizioni-row') || l.parentElement;"
                "    return c && c.textContent.toLowerCase().indexOf(q) > -1;"
                "});"
                "if (!a) return false; a.click(); return true;",
                self._ricetta)

        # 3) Match by significant keywords of the service
        if not done and self._ricetta:
            import re as _re
            parole = [w.lower() for w in _re.findall(r"\b[a-zA-Z]{4,}\b", self._ricetta)
                      if w.lower() not in ("senza", "con", "della", "delle", "degli", "visita", "prima")]
            if parole:
                done = d.execute_script(
                    "var words = arguments[0];"
                    "var links = Array.from(document.querySelectorAll('a.cambia-visibilita[href*=prenotaonline]'));"
                    "var a = links.find(function(l) {"
                    "    var c = l.closest('.prescrizioni-row') || l.parentElement;"
                    "    if (!c) return false; var txt = c.textContent.toLowerCase();"
                    "    return words.some(function(w) { return txt.indexOf(w) > -1; });"
                    "});"
                    "if (!a) return false; a.click(); return true;",
                    parole)
                if done:
                    log.info("[%s] Ricetta aperta tramite parole chiave (%s)", self.mid, parole)
        if not done:
            log.warning("ricetta '%s' non trovata sulla pagina Ricette", self._ricetta)
            # Diagnostics: log available cards for debugging
            try:
                cards = d.execute_script(
                    "return Array.from(document.querySelectorAll('a.cambia-visibilita[href*=prenotaonline]'))"
                    ".map(function(a){var c=a.closest('.prescrizioni-row');"
                    "return (c?c.getAttribute('id'):'nocard')+' :: '+((c?c.textContent:'')||'').replace(/\\s+/g,' ').trim().slice(0,120)});")
                log.info("card prenotabili trovate (%d): %s", len(cards or []), (cards or [])[:8])
            except Exception as ex:  # noqa: BLE001
                log.debug("diagnostica card: %s", ex)
            return False
        # Booking opens in a new tab: close leftover tabs from previous cycle and switch to latest
        time.sleep(3)
        hs = list(d.window_handles or [])
        ultima = hs[-1] if hs else d.current_window_handle
        current = d.current_window_handle
        for h in hs:  # Close ALL except the new one (avoids RAM accumulation)
            if h == ultima:
                continue
            try:
                d.switch_to.window(h)
                d.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            d.switch_to.window(ultima)
        except Exception:  # noqa: BLE001
            d.switch_to.window(d.window_handles[-1])
        return True

    def _attiva_prenotazione(self) -> None:
        """Click 'Prenota' (prenotaAppuntamento) + handle modals up to Dove/Quando."""
        if not self._click_by(self.sel["btn_prenota_ricetta"], "prenotaAppuntamento"):
            return
        time.sleep(2)
        self._gestisci_modale_completa(no=True)
        time.sleep(1)
        self._chiudi_modale_info()
        time.sleep(1)

    def _gestisci_modale_completa(self, no=True) -> None:
        """'Completa dati' modal: radio (No) then Confirm, handling auto-advance.

        Clicking 'No' radio may advance automatically; if Confirm button
        is still present (and enabled), click it.
        """
        # 1) radio
        radio = self._find(self.sel["modal_completa_dati"]["radio_no" if no else "radio_si"])
        if radio is not None:
            self._click_el(radio)
            time.sleep(1.2)
        # 2) confirm (if still present and enabled)
        conf = self._find(self.sel["modal_completa_dati"]["btn_conferma"])
        if conf is not None and not conf.get_attribute("disabled"):
            self._click_el(conf)
        log.info("modale completa dati gestita (%s)", "No" if no else "Si")

    def _chiudi_modale_info(self) -> None:
        self._click_by(self.sel["modal_info_chiudi"], "chiudi modale info")

    # ================= Dove/Quando =================
    def _attendi_form_dove_quando(self, timeout_s=20) -> bool:
        """Wait until Dove/Quando form is rendered (province select +
        date field). Right after the 'Completa dati' modal, the new view
        takes a few seconds to appear: without this wait, JS scripts fail on null elements.
        """
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        try:
            WebDriverWait(self.driver, timeout_s).until(
                EC.presence_of_element_located((By.ID, "provincia")))
            WebDriverWait(self.driver, timeout_s).until(
                lambda d: d.find_elements(By.ID, "quando"))
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("form Dove/Quando non pronto in %ss: %s", timeout_s, e)
            return False

    def _compila_dove_quando(self):
        """Fill in province (rotating among configured), minimum date,
        contact details, and consent.

        Note: does NOT overwrite phone/email if already present (portal
        defaults associated with user profile).
        """
        # Wait for form before filling (view not immediately ready after modal)
        if not self._attendi_form_dove_quando():
            log.warning("dove/quando: form non pronto, provo comunque")
        province = self.criteri.get("province") or selectors.PRENOTAONLINE["province"]
        prov = province[0] if province else ""
        self._provincia_corrente = prov
        if prov:
            self._set_provincia(prov)
        d_dal = self._data_dal_effettiva(self.criteri)
        self._set_ng("quando", d_dal)
        # Contact details: set ONLY if empty (respecting profile defaults)
        tel = self.driver.find_elements(By.ID, "telefono")
        if tel and not tel[0].get_attribute("value"):
            self._set_ng("telefono", self.criteri.get("telefono", ""))
        em = self.driver.find_elements(By.ID, "email")
        if em and not em[0].get_attribute("value"):
            # Do NOT use placeholder: leave empty if no default
            if self.criteri.get("email"):
                self._set_ng("email", self.criteri["email"])
        self._click_consenso()

    def _click_consenso(self) -> None:
        """Click consent checkbox only if not already selected (with Angular events)."""
        self.driver.execute_script(
            "var c=document.getElementById('consensoPrenotazione');if(c&&!c.checked){c.click();}"
            "return true;")
        time.sleep(0.3)

    def _ricerca(self) -> None:
        """Click availability search button (if enabled)."""
        btn = self._find(self.sel["btn_ricerca_disponibilita"])
        if btn is not None and not btn.get_attribute("disabled"):
            self._click_el(btn)
            log.info("ricerca disponibilità avviata")
        else:
            log.warning("bottone ricerca disabilitato (form invalido?)")

    # ================= load_page (Flow contract) =================
    def load_page(self) -> None:
        """Execute entire search workflow: Prescriptions -> prenota -> Dove/Quando -> results."""
        if self._ricerca_fatta:
            return
        # Preventive cleanup: close leftover tabs from previous cycles
        self._riduci_tab_a_una()
        if not self._apri_ricetta_e_prenota():
            log.warning("load_page: ricetta non aperta, interrompo")
            if not self._is_autenticato():
                raise RuntimeError("Sessione scaduta o non valida durante l'apertura della ricetta")
            raise RuntimeError(f"Ricetta '{self._ricetta}' non trovata o non prenotabile")
        self._attiva_prenotazione()
        self._compila_dove_quando()
        self._ricerca()
        # Wait for stable outcome (results or no availability) for the first province
        st = self._attendi_esito(self._provincia_corrente, timeout_s=60)
        if st.get("stato") not in ("risultati", "no_risultati"):
            log.warning("new: ricerca %s non completata (%s)", self._provincia_corrente, st.get("stato"))
        # Reset: next cycle (new rotating province) will rerun search
        self._ricerca_fatta = False

    # ================= extract_slots (all provinces or single) =================
    def extract_slots_current_province(self) -> list[Slot]:
        """Extract slots for the single province currently displayed on screen."""
        return self._estrai_slots_corrente()

    def extract_slots(self) -> list[Slot]:
        """Extract slots from availability list.

        The first province has already been searched in load_page; for remaining
        configured provinces, uses 'Modifica ricerca' -> province -> 'Aggiorna ricerca'.
        """
        slots = self._estrai_slots_corrente()
        province = list(self.criteri.get("province") or [])
        for prov in province[1:]:
            esito = self._cambia_provincia_e_ricerca(prov)
            if esito.get("stato") in ("risultati", "no_risultati"):
                self._provincia_corrente = esito.get("provincia") or prov
                slots.extend(self._estrai_slots_corrente())
            else:
                log.warning("new: provincia %s ricerca non completata -> skip", prov)
        return slots

    def _estrai_slots_corrente(self) -> list[Slot]:
        """Extract slots from the CURRENT results view.

        The province is read from the results HEADER (source of truth),
        not from self._provincia_corrente (Python variable subject to race).
        """
        prov_attesa = getattr(self, "_provincia_corrente", "") or (self.criteri.get("province") or [""])[0]
        st = self._attendi_esito(prov_attesa, timeout_s=60)
        stato = st.get("stato")
        if stato == "no_risultati":
            log.info("new: 0 disponibilita per %s (esito valido)", prov_attesa)
            return []
        if stato != "risultati":
            log.warning("new: vista non pronta (%s) per %s", stato, prov_attesa)
            return []
        prov_reale = st.get("provincia") or prov_attesa
        if prov_attesa and prov_reale.upper() != prov_attesa.upper():
            log.warning("new: vista su %s, attesa %s -> estrazione saltata (no stale)",
                        prov_reale, prov_attesa)
            return []
        self._chiudi_modali_residui()
        time.sleep(0.5)
        blocks = self._find(self.sel["slot_disponibilita"], mult=True)
        slots = []
        for b in blocks or []:
            try:
                txt = b.text
            except Exception:  # noqa: BLE001
                continue
            m = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}:\d{2})", txt)
            if not m:
                continue
            dt_ok = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d/%m/%Y %H:%M")

            def _field(label, stop_label=None):
                mm = re.search(re.escape(label) + r"\s*\n?\s*([^\n]+)", txt)
                if not mm:
                    return ""
                v = mm.group(1).strip()
                if stop_label and stop_label.lower() in v.lower():
                    v = v.split(stop_label)[0].strip()
                return v

            slots.append(Slot(
                datetime=dt_ok,
                extra={
                    "azienda": _field("Azienda", "Comune").strip(" -"),
                    "comune": _field("Comune", "Presentarsi").strip(" -"),
                    "sede": _field("Presentarsi in").split("-->")[0].strip(" -"),
                    "provincia": prov_reale,
                },
            ))
        if not slots:
            log.info("new: 0 blocchi in vista risultati (prov %s)", prov_reale)
        return slots

    # ================= execute (booking) =================
    def _data_dir(self):
        from pathlib import Path
        return Path(__file__).resolve().parent.parent / "data"

    def _invia_riepilogo(self, info):
        """Send appointment summary to Telegram (HTML format)."""
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
            lines.append("\n<b>Note:</b>")
            lines.append("\n".join(f"· {e(n)}" for n in info.note))
        self.bot.notify("\n".join(lines))

    def execute(self, slot) -> None:
        """Select slot (Verifica e conferma) and complete booking.

        1. click 'Verifica e conferma' on the target slot
        2. in 'Vuoi confermare' modal: check 'Confermo lettura'
        3. click Conferma
        """
        import time
        prov = (slot.extra or {}).get("provincia", "")
        # 1) 'Verifica e conferma' on matching slot (date/time)
        found = False
        for btn in self._find(self.sel["btn_verifica_conferma"], mult=True) or []:
            # Find slot container and verify it contains date/time
            try:
                container = btn.find_element(By.XPATH, "ancestor::*[contains(@class,'appuntamento')][1]")
            except Exception:  # noqa: BLE001
                container = None
            txt = (container.text if container is not None else "")
            if (
                (slot.date_str.replace("/", "/") in txt if container else False)
                and slot.time_str in txt
            ):
                self._click_el(btn)
                found = True
                break
        if not found:
            # If slot has province and UI does not display it, switch province
            if prov and self._cambia_provincia_e_ricerca(prov).get("stato") == "risultati":
                for btn in self._find(self.sel["btn_verifica_conferma"], mult=True) or []:
                    try:
                        container = btn.find_element(By.XPATH, "ancestor::*[contains(@class,'appuntamento')][1]")
                    except Exception:  # noqa: BLE001
                        container = None
                    txt = (container.text if container is not None else "")
                    if slot.date_str in txt and slot.time_str in txt:
                        self._click_el(btn)
                        found = True
                        break
        if not found:
            raise RuntimeError(
                f"Slot {slot.date_str} {slot.time_str} non trovato (fallback disabilitato)")
        time.sleep(2)
        # 2) Check 'Confermo lettura'
        chk = self._find(self.sel["modal_conferma_prenota"]["checkbox_lettura"])
        if chk is not None and not chk.is_selected():
            self._click_el(chk)
            time.sleep(1)
        # Extract info from modal (for Telegram + .ics)
        info = parse_conferma(self.driver.page_source)
        info.data_ora = f"{slot.date_str} - {slot.time_str}" or info.data_ora
        if not info.prestazione:
            info.prestazione = self._ricetta
        # 3) Confirm
        conf = self._find(self.sel["modal_conferma_prenota"]["btn_conferma"])
        if conf is not None and not conf.get_attribute("disabled"):
            self._click_el(conf)
        elif conf is not None:
            raise RuntimeError("Bottone 'Conferma' disabilitato (lettura non presa visione)")
        else:
            raise RuntimeError("Bottone 'Conferma' non trovato nella modale di conferma")

        # 4) Rigorous portal confirmation check
        ok_conf, cod, err_conf = self._attendi_conferma_prenotazione(timeout_s=30)
        if not ok_conf:
            log.warning(
                "[%s] Schermata di conferma portale non rilevata (%s), tento riscontro in 'I miei appuntamenti' come fallback...",
                self.mid, err_conf,
            )
            riscontro_ok = self._verifica_riscontro_miei_appuntamenti(slot, codice=cod)
            if not riscontro_ok:
                raise RuntimeError(f"Conferma prenotazione non riuscita: {err_conf}")
            log.info("[%s] Riscontro positivo in 'I miei appuntamenti' (recuperato da fallback), procedo.", self.mid)
        else:
            info.codice = cod or info.codice or ""
            # 5) Verification check in 'I miei appuntamenti'
            log.info("[%s] Controllo finale di riscontro in 'I miei appuntamenti'...", self.mid)
            riscontro_ok = self._verifica_riscontro_miei_appuntamenti(slot, codice=info.codice)
            if not riscontro_ok:
                log.warning(
                    "[%s] Riscontro in 'I miei appuntamenti' non immediato dopo la conferma della prenotazione. "
                    "La conferma a video è comunque avvenuta con successo.", self.mid,
                )
                self.bot.notify(
                    f"⚠️ <i>Nota per {self.mid}:</i> la prenotazione è stata confermata con successo dal portale, "
                    f"ma l'aggiornamento nella sezione 'I miei appuntamenti' potrebbe richiedere qualche minuto "
                    f"per sincronizzarsi completamente a video."
                )

        # 6) Successfully verified booking -> persistence and notifications
        try:
            self.store.record_prenotazione(
                self.mid, info.codice or "",
                f"{slot.date_str} {slot.time_str}",
                info.__dict__ if hasattr(info, "__dict__") else {},
            )
            log.info("[%s] prenotazione registrata nello store (cod %s)", self.mid, info.codice)
        except Exception as e:  # noqa: BLE001
            log.warning("salvataggio prenotazione nello store: %s", e)
        # --- send summary to Telegram ---
        self._invia_riepilogo(info)
        # --- generate and send calendar event .ics ---
        try:
            ics_path = write_ics(info, self._data_dir() / "ics")
            cap = f"📅 Evento calendario appuntamento {info.data_ora}"
            self.bot.send_file(str(ics_path), caption=cap)
        except Exception as e:  # noqa: BLE001
            log.exception("generazione/invio .ics fallita: %s", e)
        log.info("prenotazione confermata per %s %s (cod %s)",
                 slot.date_str, slot.time_str, cod)
