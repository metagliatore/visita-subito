"""Flow A — Nuova prenotazione (visita specialistica) in PrenotaOnline.

Flusso reale mappato (Selenium):
  1. dalla pagina Ricette apri "Prenota" della ricetta -> nuova scheda `prenotaonline`
  2. bottone `ricettaCtrl.prenotaAppuntamento(ricetta)`
  3. modale "Completa dati" (CONTROLLO/FOLLOW-UP): radio No -> conferma
  4. modale informativa -> chiudi
  5. Dove/Quando: provincia (da criteri), data, recapiti, consenso -> ricerca
  6. lista disponibilità -> estrai slot (data/ora, azienda, comune, sede)
  7. su match -> conferma Telegram (in base.py) -> verifica/conferma

Il contratto con Flow.poll_once():
  - load_page()    esegue TUTTA la sequenza di ricerca (dal tab Ricette fino
                   alla pagina risultati della provincia richiesta)
  - extract_slots() legge gli slot risultati
  - execute(slot)  seleziona lo slot e lo conferma (da chiarire col dettaglio)

NB: campi Angular (ng-model) vanno valorizzati con eventi reali (angular
trigger / click veri), altrimenti $invalid blocca la ricerca.
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
        # stato per non riavviare la ricerca se già sui risultati
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
        """Imposta un campo Angular (per id) con evento reale input+change.

        Robusto: se l'elemento non esiste ancora (form in renderizzazione)
        ritorna None SENZA sollevare (angular.element(null) lancia
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

    # ================= selezione ricetta (dal tab Ricette) =================
    def _applica_filtro_specialistiche(self):
        """Applica il filtro 'Specialistiche' + 'Prescritte' per mostrare le visite.
        La pagina Ricette di default mostra i farmaci; serve il submit del filtro.
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
        """Dal tab Ricette click 'Prenota' della card che matcha la ricetta.

        Ritorna True se siamo sulla scheda prenotaonline pronta (Dove/Quando).
        """
        d = self.driver
        # assicura di essere sulla pagina Ricette
        if "/web/areaprivata/ricette" not in d.current_url:
            d.get(selectors.RICETTE["url"])
            time.sleep(3)
        # applica il filtro specialistiche per rendere visibili le visite
        self._applica_filtro_specialistiche()
        # 1) match deterministico per id=NRE (univoco, non ambiguo)
        done = False
        if self._nre:
            done = d.execute_script(
                "var q=String(arguments[0]);"
                "var links=Array.from(document.querySelectorAll('a.cambia-visibilita[href*=prenotaonline]'));"
                "var a=links.find(function(l){var c=l.closest('.prescrizioni-row');"
                "return c && (c.getAttribute('id')||'')===q;});"
                "if(!a) return false; a.click(); return true;",
                self._nre)
        # 2) match per testo prestazione
        if not done:
            # ricerca la card: match per testo prestazione
            done = d.execute_script(
                "var q=String(arguments[0]);"
                "var links=Array.from(document.querySelectorAll('a.cambia-visibilita[href*=prenotaonline]'));"
                "var a=links.find(function(l){var c=l.closest('.prescrizioni-row');"
                "return c && ((c.textContent.indexOf(q)>-1) || "
                "(c.getAttribute('id')||'')===q);});"
                "if(!a) return false; a.click(); return true;",
                self._ricetta)
        if not done:
            # second tentativo: caso-insensitive e più permissivo (anche senza .prescrizioni-row)
            log.warning("ricetta '%s' non trovata al primo tentativo; riprovo...", self._ricetta)
            time.sleep(1)
            done = d.execute_script(
                "var q=String(arguments[0]).toLowerCase();"
                "var links=Array.from(document.querySelectorAll('a.cambia-visibilita[href*=prenotaonline]'));"
                "var a=links.find(function(l){var c=l.closest('.prescrizioni-row')||l.parentElement;"
                "return c && c.textContent.toLowerCase().indexOf(q)>-1;});"
                "if(!a) return false; a.click(); return true;",
                self._ricetta)
        if not done:
            log.warning("ricetta '%s' non trovata sulla pagina Ricette", self._ricetta)
            # diagnostica: stampa le card presenti per il debug
            try:
                cards = d.execute_script(
                    "return Array.from(document.querySelectorAll('a.cambia-visibilita[href*=prenotaonline]'))"
                    ".map(function(a){var c=a.closest('.prescrizioni-row');"
                    "return (c?c.getAttribute('id'):'nocard')+' :: '+((c?c.textContent:'')||'').replace(/\\s+/g,' ').trim().slice(0,120)});")
                log.info("card prenotabili trovate (%d): %s", len(cards or []), (cards or [])[:8])
            except Exception as ex:  # noqa: BLE001
                log.debug("diagnostica card: %s", ex)
            return False
        # la prenotazione si apre in una nuova tab: passa all'ultima
        time.sleep(3)
        d.switch_to.window(d.window_handles[-1])
        return True

    def _attiva_prenotazione(self) -> None:
        """Click 'Prenota' (prenotaAppuntamento) + gestione modali fino a Dove/Quando."""
        if not self._click_by(self.sel["btn_prenota_ricetta"], "prenotaAppuntamento"):
            return
        time.sleep(2)
        self._gestisci_modale_completa(no=True)
        time.sleep(1)
        self._chiudi_modale_info()
        time.sleep(1)

    def _gestisci_modale_completa(self, no=True) -> None:
        """Modale 'Completa dati': radio (No) poi Conferma, gestendo l'auto-avvio.

        Il click sul radio 'No' può far avanzare da solo; se il bottone Conferma
        è ancora presente (e abilitato) lo clicchiamo.
        """
        # 1) radio
        radio = self._find(self.sel["modal_completa_dati"]["radio_no" if no else "radio_si"])
        if radio is not None:
            self._click_el(radio)
            time.sleep(1.2)
        # 2) conferma (se ancora presente e abilitato)
        conf = self._find(self.sel["modal_completa_dati"]["btn_conferma"])
        if conf is not None and not conf.get_attribute("disabled"):
            self._click_el(conf)
        log.info("modale completa dati gestita (%s)", "No" if no else "Si")

    def _chiudi_modale_info(self) -> None:
        self._click_by(self.sel["modal_info_chiudi"], "chiudi modale info")

    # ================= Dove/Quando =================
    def _attendi_form_dove_quando(self, timeout_s=20) -> bool:
        """Attende che il form Dove/Quando sia renderizzato (select provincia +
        campo data). Subito dopo la modale 'Completa dati' la nuova vista
        impiega qualche secondo ad apparire: senza questa attesa i JS di
        compilazione girano su elementi null e falliscono.
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
        """Compila provincia (a rotazione tra quelle configurate), data minima,
        recapiti e consenso.

        NB: NON sovrascrive telefono/email se già valorizzati (default del
        portale associati al profilo).
        """
        # attende il form prima di compilare (vista non pronta subito dopo la modale)
        if not self._attendi_form_dove_quando():
            log.warning("dove/quando: form non pronto, provo comunque")
        province = self.criteri.get("province") or selectors.PRENOTAONLINE["province"]
        prov = province[0] if province else ""
        self._provincia_corrente = prov
        if prov:
            self._set_provincia(prov)
        d_dal = self._data_dal_effettiva(self.criteri)
        self._set_ng("quando", d_dal)
        # recapiti: imposta SOLO se vuoti (rispettando i default del profilo)
        tel = self.driver.find_elements(By.ID, "telefono")
        if tel and not tel[0].get_attribute("value"):
            self._set_ng("telefono", self.criteri.get("telefono", ""))
        em = self.driver.find_elements(By.ID, "email")
        if em and not em[0].get_attribute("value"):
            # NON mettere placeholder: lascia vuoto se nessun default
            if self.criteri.get("email"):
                self._set_ng("email", self.criteri["email"])
        self._click_consenso()

    def _click_consenso(self) -> None:
        """Clicca il checkbox consenso solo se non ancora selezionato (con eventi Angular)."""
        self.driver.execute_script(
            "var c=document.getElementById('consensoPrenotazione');if(c&&!c.checked){c.click();}"
            "return true;")
        time.sleep(0.3)

    def _ricerca(self) -> None:
        """Clicca il bottone di ricerca disponibilità (se abilitato)."""
        btn = self._find(self.sel["btn_ricerca_disponibilita"])
        if btn is not None and not btn.get_attribute("disabled"):
            self._click_el(btn)
            log.info("ricerca disponibilità avviata")
        else:
            log.warning("bottone ricerca disabilitato (form invalido?)")

    # ================= load_page (contratto Flow) =================
    def load_page(self) -> None:
        """Esegue l'intera ricerca: Ricette -> prenota -> Dove/Quando -> risultati."""
        if self._ricerca_fatta:
            return
        if not self._apri_ricetta_e_prenota():
            log.warning("load_page: ricetta non aperta, interrompo")
            return
        self._attiva_prenotazione()
        self._compila_dove_quando()
        self._ricerca()
        # attesa esito stabile (risultati o assenza) per la prima provincia
        st = self._attendi_esito(self._provincia_corrente, timeout_s=60)
        if st.get("stato") not in ("risultati", "no_risultati"):
            log.warning("new: ricerca %s non completata (%s)", self._provincia_corrente, st.get("stato"))
        # reset: il prossimo giro (nuova provincia in rotazione) rifarà la ricerca
        self._ricerca_fatta = False

    # ================= extract_slots (tutte le province) =================
    def extract_slots(self) -> list[Slot]:
        """Estrae gli slot dalla lista disponibilità.

        La prima provincia è già stata cercata in load_page; per le restanti
        configurate usa 'Modifica ricerca' -> provincia -> 'Aggiorna ricerca'.
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
        """Estrae gli slot dalla vista risultati CORRENTE.

        La provincia viene letta dalla TESTATA dei risultati (fonte vera),
        non da self._provincia_corrente (variabile Python soggetta alla race).
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

    # ================= execute (prenotazione) =================
    def _data_dir(self):
        from pathlib import Path
        return Path(__file__).resolve().parent.parent / "data"

    def _invia_riepilogo(self, info):
        """Invia il riepilogo dell'appuntamento estratto su Telegram (formato HTML)."""
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
        """Seleziona lo slot (Verifica e conferma) e completa la prenotazione.

        1. click 'Verifica e conferma' sullo slot target
        2. nella modale 'Vuoi confermare': spunta 'Confermo lettura'
        3. click Conferma
        """
        import time
        prov = (slot.extra or {}).get("provincia", "")
        # 1) 'Verifica e conferma' sullo slot che matcha (data/ora)
        found = False
        for btn in self._find(self.sel["btn_verifica_conferma"], mult=True) or []:
            # trova il contenitore dello slot e controlla che contenga data/ora
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
            # se lo slot ha provincia e la UI non la mostra, cambia provincia
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
        # 2) spunta 'Confermo lettura'
        chk = self._find(self.sel["modal_conferma_prenota"]["checkbox_lettura"])
        if chk is not None and not chk.is_selected():
            self._click_el(chk)
            time.sleep(1)
        # estrai le info dalla modale (per Telegram + .ics)
        info = parse_conferma(self.driver.page_source)
        info.data_ora = f"{slot.date_str} - {slot.time_str}" or info.data_ora
        if not info.prestazione:
            info.prestazione = self._ricetta
        # 3) Conferma
        conf = self._find(self.sel["modal_conferma_prenota"]["btn_conferma"])
        if conf is not None and not conf.get_attribute("disabled"):
            self._click_el(conf)
        elif conf is not None:
            raise RuntimeError("Bottone 'Conferma' disabilitato (lettura non presa visione)")
        time.sleep(3)
        # codice prenotazione dalla schermata di successo
        cod = parse_successo(self.driver.page_source)
        info.codice = cod or ""
        # --- memorizza la prenotazione nello store (ricetta ora è un appuntamento) ---
        try:
            self.store.record_prenotazione(
                self.mid, info.codice or "",
                f"{slot.date_str} {slot.time_str}",
                info.__dict__ if hasattr(info, "__dict__") else {},
            )
            log.info("[%s] prenotazione registrata nello store (cod %s)", self.mid, info.codice)
        except Exception as e:  # noqa: BLE001
            log.warning("salvataggio prenotazione nello store: %s", e)
        # --- invia riepilogo su Telegram ---
        self._invia_riepilogo(info)
        # --- genera e invia evento calendario .ics ---
        try:
            ics_path = write_ics(info, self._data_dir() / "ics")
            cap = f"📅 Evento calendario appuntamento {info.data_ora}"
            self.bot.send_file(str(ics_path), caption=cap)
        except Exception as e:  # noqa: BLE001
            log.exception("generazione/invio .ics fallita: %s", e)
        log.info("prenotazione confermata per %s %s (cod %s)",
                 slot.date_str, slot.time_str, cod)
