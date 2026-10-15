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
import os
import re
import time

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
        self.criteri = dict(monitor.get("criteri", {}))
        if monitor.get("data_attuale") and not self.criteri.get("data_attuale"):
            self.criteri["data_attuale"] = monitor["data_attuale"]

        # Strategia di ricerca multi-provincia:
        # - "first_match": rapida/immediata, si ferma al primo match valido, mantiene la pagina e prenota subito
        # - "best_match": globale, scansiona tutte le province e sceglie la data migliore
        strat = (
            self.criteri.get("search_strategy")
            or os.environ.get("SEARCH_STRATEGY", "")
        ).strip().lower()
        if not strat:
            try:
                from core.config import Config
                cfg = Config.load()
                strat = str(cfg.settings.get("scheduler", {}).get("search_strategy", "first_match")).strip().lower()
            except Exception:
                strat = "first_match"
        if strat in ("best", "best_match", "globale", "all", "all_provinces"):
            self.search_strategy = "best_match"
        else:
            self.search_strategy = "first_match"

    @property
    def is_first_match(self) -> bool:
        return self.search_strategy == "first_match"

    @property
    def mid(self) -> str:
        return self.monitor["id"]

    @property
    def driver(self):
        return getattr(self.browser, "driver", None)

    def _is_autenticato(self) -> bool:
        """True se il browser corrente è autenticato e non su pagine di login/IdPC."""
        d = getattr(self.browser, "driver", None)
        if d is None:
            return False
        try:
            url = (d.current_url or "").lower()
            if not url or "about:blank" in url:
                return False
            if any(k in url for k in ["idpcwrapper", "identity.", "login", "/sso"]):
                return False
            return ("/web/areaprivata/" in url) or ("/prenotaonline/" in url)
        except Exception:
            return False

    # ---- da implementare nelle sottoclassi ----
    def load_page(self) -> None:
        """Naviga e predisponi la pagina col browser."""
        raise NotImplementedError

    def extract_slots(self) -> list:
        """Estrai la lista di Slot disponibili dalla pagina corrente."""
        raise NotImplementedError

    def extract_slots_current_province(self) -> list:
        """Estrae gli slot della sola provincia attualmente a video.
        
        Nelle sottoclassi implementa l'estrazione veloce dalla schermata corrente
        senza iterare sulle province successive. Se non sovrascritta, fa fallback su extract_slots().
        """
        return self.extract_slots()

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
        val = criteri.get("data_dal")
        if val:
            if hasattr(val, "strftime"):
                d = val.date() if hasattr(val, "date") else val
                if d < oggi:
                    return oggi.strftime("%d/%m/%Y")
                return d.strftime("%d/%m/%Y")
            raw = str(val).strip()
            for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
                try:
                    d = _dt.datetime.strptime(raw[:10], fmt).date()
                    if d < oggi:
                        return oggi.strftime("%d/%m/%Y")
                    return d.strftime("%d/%m/%Y")
                except Exception:  # noqa: BLE001
                    continue
        return oggi.strftime("%d/%m/%Y")

    def azione_extra(self, slot) -> str:
        """Riga extra opzionale mostrata all'utente in fase di conferma.

        Default: nessuna. I sottotipi (es. reschedule) possono restituire un
        messaggio aggiuntivo (es. 'Vuoi anticipare o posticipare?').
        """
        return ""

    def _riduci_tab_a_una(self) -> None:
        """Chiude TUTTE le tab tranne quella corrente.

        Ogni ciclo o click su link target=_blank può aprire tab residue:
        senza pulizia i tab si accumulano a ogni poll e la RAM cresce.
        """
        d = getattr(self, "driver", None) or getattr(self.browser, "driver", None)
        if d is None:
            return
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

# ---------------- helper stato pagina (robusto, via JS) ----------------
    def _stato_vista(self) -> dict:
        """Stato REALE della pagina corrente, via JS (il test del testo del DOM
        e' inaffidabile: i messaggi "non ci sono disponibilita" restano nel DOM
        anche dopo aver chiuso la modale, e un campo nascosto #provincia puo'
        confondere getElementById).

        Ritorna dict:
          stato: 'loading' | 'risultati' | 'no_risultati' | 'modale_ricerca' | 'form' | 'altro'
          provincia: la provincia che la pagina ESPONE davvero:
                     - 'risultati'     -> testata risultati (span.field-label)
                     - 'form'/'modale' -> select visibile
          loading: True se c'e' uno spinner di caricamento attivo
        """
        js = r"""
        function vis(el){if(!el)return false;var s=getComputedStyle(el);return s&&s.display!=='none'&&s.visibility!=='hidden';}
        var st={stato:'altro',provincia:'',loading:false},mods=document.querySelectorAll('.modal'),i,m;
        // 1) modale 'Modifica ricerca' aperta (contiene select provincia)
        for(i=0;i<mods.length;i++){if(vis(mods[i])){m=mods[i];break;}}
        if(m&&m.querySelector('#provincia')){
            var sp=m.querySelector('#provincia');
            st.stato='modale_ricerca';
            if(sp.selectedOptions&&sp.selectedOptions[0]) st.provincia=sp.selectedOptions[0].textContent.trim();
            else if(sp.selectedIndex>=0&&sp.options[sp.selectedIndex]) st.provincia=sp.options[sp.selectedIndex].textContent.trim();
            return st;
        }
        // 2) vista risultati: bottone 'Modifica ricerca' + slot renderizzati
        //    (o messaggio assenza nel body). NB: va VERIFICATO PRIMA dello
        //    spinner: il portale lascia un overlay 'caricamento in corso...'
        //    visibile anche a risultati già renderizzati, e mettere lo spinner
        //    per primo bloccherebbe il flow in 'loading' per sempre.
        var hasBtn=document.querySelector('button[ng-click*="prenotaDisponibilitaCtrl.modificaCriteri"]');
        var slotsInDom=document.querySelectorAll('li.appuntamento').length;
        var bodyNoDisp=/non sono state trovate|nessuna disponib|non ci sono disponib/i.test(document.body.textContent||'');
        if(hasBtn&&(slotsInDom>0||bodyNoDisp)){
            st.stato='risultati';
            var fs=document.querySelectorAll('span.field-label, label, .field-label, dt');
            for(var j=0;j<fs.length;j++){
                var labelText = (fs[j].textContent||'').replace(/[\s:]+$/,'').trim();
                if(/^provincia$/i.test(labelText)){
                    var sib=fs[j].nextElementSibling || (fs[j].parentElement?fs[j].parentElement.querySelector('.field-value, dd, span:not(.field-label)'):null);
                    if(sib) st.provincia=(sib.textContent||'').trim();
                    break;
                }
            }
            return st;
        }
        // 3) modale 'Attenzione: non ci sono disponibilita' (0 disponibilita)
        for(i=0;i<mods.length;i++){
            if(!vis(mods[i]))continue;
            var t=mods[i].textContent||'';
            if(/non ci sono disponib/ig.test(t)||/nessuna disponib/ig.test(t)||/non sono state trovate/ig.test(t)){
                st.stato='no_risultati';
                return st;
            }
        }
        // 4) form Dove/Quando visibile
        var p=document.getElementById('provincia');
        if(p&&vis(p)){
            st.stato='form';
            if(p.selectedOptions[0])st.provincia=p.selectedOptions[0].textContent.trim();
            return st;
        }
        // 5) spinner 'caricamento in corso...' SOLO se non c'è altro stato reale
        var sp=Array.from(document.querySelectorAll('.spinner-container,[class*="spinner"],[class*="loader"]'));
        for(i=0;i<sp.length;i++){
            if(vis(sp[i])&&/caricamento/i.test(sp[i].textContent||'')){
                st.stato='loading'; st.loading=true; return st;
            }
        }
        return st;
        """
        try:
            r = self.driver.execute_script(js)
        except Exception:  # noqa: BLE001
            r = {}
        if not isinstance(r, dict):
            r = {}
        r.setdefault("stato", "altro")
        r.setdefault("provincia", "")
        r.setdefault("loading", False)
        return r

    def _chiudi_modali_residui(self) -> None:
        """Chiude eventuali modali residue (Attenzione/errore/info) che coprono
        l'area di lavoro. NON tocca la modale di conferma prenotazione.
        """
        try:
            self.driver.execute_script(
                "var els=Array.from(document.querySelectorAll("
                "'button[data-dismiss=modal],button[ng-click*=messaggiCtrl]'));"
                "var txt=Array.from(document.querySelectorAll('button')).filter(function(b){"
                "return /chiudi|attenzione/i.test((b.textContent||'').trim())});"
                "els.concat(txt).forEach(function(b){try{b.click()}catch(e){}});")
        except Exception:  # noqa: BLE001
            pass

    def _set_provincia(self, provincia: str) -> bool:
        """Imposta `provincia` nella select VISIBILE (form o modale ricerca).

        NB: il valore cercato va catturato in una variabile LOCALE: usarlo come
        `arguments[0]` DENTRO la callback del `.find` confronta con `x` stesso
        (sempre True) e selezionerebbe sempre la prima opzione.
        """
        js = (
            "var target=arguments[0],i;"
            "var mods=Array.from(document.querySelectorAll('.modal'));"
            "var m=null;for(i=0;i<mods.length;i++){var ms=getComputedStyle(mods[i]);"
            "if(ms&&ms.display!=='none'&&ms.visibility!=='hidden'){m=mods[i];break;}}"
            "var a=document.querySelectorAll('#provincia'),sel=null;"
            "if(m&&m.querySelector('#provincia')){sel=m.querySelector('#provincia');}"
            "else{var vis=[];for(i=0;i<a.length;i++){var s=getComputedStyle(a[i]);"
            "if(s&&s.display!=='none'&&s.visibility!=='hidden')vis.push(a[i]);}"
            "sel=vis.length?vis[vis.length-1]:(a.length?a[a.length-1]:null);}"
            "if(!sel)return 'noselect';"
            "var opts=Array.from(sel.options).filter(function(o){return o.textContent.trim()===target});"
            "if(!opts.length)return 'nooption';"
            "sel.value=opts[0].value;angular.element(sel).triggerHandler('change');"
            "return 'ok';"
        )
        try:
            r = str(self.driver.execute_script(js, provincia) or "")
        except Exception as e:  # noqa: BLE001
            log.warning("_set_provincia(%s): eccezione %s", provincia, e)
            return False
        if r != "ok":
            log.warning("_set_provincia(%s): %s", provincia, r)
            return False
        return True

    def _attendi_esito(self, provincia: str = "", timeout_s: int = 60,
                      max_load_s: int = 300) -> dict:
        """Attende che la ricerca sia arrivata a un esito STABILE:

          - 'risultati'    -> vista risultati attiva (header provincia == attesa)
          - 'no_risultati' -> modale assenza disponibilita visibile (0 valido)
          - 'loading'      -> spinner 'caricamento in corso...' attivo

        IMPORTANTE: il timeout NON scatta mentre la pagina e' in caricamento
        (spinner visibile), perche' il portale a volte impiega molto tempo e un
        timeout fisso farebbe perdere l'esito. Scatta solo quando la pagina e'
        FERMA (nessuno spinner e nessun esito) da almeno `timeout_s` secondi.
        Come guardia anti-blocco, se il caricamento supera `max_load_s` totali
        viene loggato e si ritorna comunque.
        """
        import time
        t0 = time.time()
        ultimo = {"stato": "altro", "provincia": "", "loading": False}
        idle_da = None  # da quando la pagina non mostra ne' spinner ne' esito
        while True:
            st = self._stato_vista()
            ultimo = st
            s = st.get("stato", "altro")
            now = time.time()
            if s == "no_risultati":
                return st
            if s == "risultati" and (
                    not provincia or (st.get("provincia") or "").upper() == provincia.upper()):
                return st
            if s == "loading":
                idle_da = None
                if now - t0 > max_load_s:
                    log.warning("_attendi_esito: caricamento oltre %ss per %s (stato %s)",
                                max_load_s, provincia or "-", s)
                    return st
            else:
                if idle_da is None:
                    idle_da = now
                if now - idle_da > timeout_s:
                    log.warning("_attendi_esito: timeout %ss (stato %s) per %s",
                                timeout_s, s, provincia or "-")
                    return st
            time.sleep(1.0)

    def _attendi_risultati(self, provincia: str, timeout_s: int = 60) -> bool:
        """Compat: True se esito stabile (o assenza)."""
        st = self._attendi_esito(provincia, timeout_s=timeout_s)
        return st.get("stato") in ("risultati", "no_risultati")

    def _cambia_provincia_e_ricerca(self, provincia: str, _retry: int = 0) -> dict:
        """Cambia provincia e rilancia la ricerca, ritornando l'esito
        (vedi _stato_vista / _attendi_esito):
        - form Dove/Quando (o dopo 0 disponibilità chiudendo la modale)
          -> set provincia + consenso + ricercaDisponibilita
        - vista risultati -> modale 'Modifica ricerca' -> provincia -> Aggiorna
        Aggiorna self._provincia_corrente.
        """
        import time
        d = self.driver
        self._chiudi_modali_residui()
        time.sleep(1)
        st = self._stato_vista()
        s = st.get("stato", "altro")
        if s in ("form", "modale_ricerca"):
            if not self._set_provincia(provincia):
                log.warning("provincia %s non nel select", provincia)
                return {"stato": s, "provincia": st.get("provincia", "")}
            time.sleep(1)
            try:
                d.execute_script(
                    "var c=document.getElementById('consensoPrenotazione');"
                    "if(c&&!c.checked){c.click();angular.element(c).triggerHandler('change');}")
            except Exception:  # noqa: BLE001
                pass
            b = (self._find(selectors.PRENOTAONLINE["btn_ricerca_disponibilita"])
                 or self._find([("css", "button[ng-click*='doveQuandoModalCtrl.aggiorna']")]))
            if b is not None and not b.get_attribute("disabled"):
                self._click_el(b)
                self._provincia_corrente = provincia
                return self._attendi_esito(provincia)
            log.warning("ricerca per %s: bottone disabilitato/non trovato", provincia)
            return {"stato": s, "provincia": provincia}
        if s == "risultati":
            b = self._find(selectors.PRENOTAONLINE["btn_modifica_ricerca"])
            if b is None:
                log.warning("'Modifica ricerca' non trovato")
                return {"stato": s, "provincia": st.get("provincia", "")}
            self._click_el(b)
            # aspetta che la MODALE sia davvero aperta (select al suo interno)
            t0 = time.time()
            while time.time() - t0 < 10:
                sm = self._stato_vista()
                if sm.get("stato") == "modale_ricerca":
                    break
                time.sleep(1)
            else:
                log.warning("modale 'Modifica ricerca' non comparsa per %s (stato %s)",
                            provincia, sm.get("stato"))
            if not self._set_provincia(provincia):
                log.warning("cambio provincia %s nella modale fallito", provincia)
                return {"stato": "modale_ricerca", "provincia": st.get("provincia", "")}
            b2 = self._find(selectors.PRENOTAONLINE["btn_aggiorna_ricerca"])
            if b2 is not None and not b2.get_attribute("disabled"):
                self._click_el(b2)
                self._provincia_corrente = provincia
                # Breve attesa per consentire la chiusura della modale e l'avvio della transizione DOM
                time.sleep(1.0)
                esito = self._attendi_esito(provincia, timeout_s=60)
                # se la testata NON è cambiata alla provincia attesa, riprova una volta
                if (esito.get("stato") == "risultati"
                        and (esito.get("provincia") or "").upper() != provincia.upper()
                        and _retry < 2):
                    log.warning("testata still %s dopo 'Aggiorna' (%s attesa): retry %s",
                                esito.get("provincia"), provincia, _retry + 1)
                    self._chiudi_modali_residui()
                    time.sleep(1)
                    return self._cambia_provincia_e_ricerca(provincia, _retry=_retry + 1)
                return esito
            log.warning("'Aggiorna ricerca' non disponibile per %s", provincia)
            return {"stato": "modale_ricerca", "provincia": provincia}
        if s == "no_risultati":
            # chiudi la modale di assenza (torna al form) e ri-handler
            if _retry >= 2:
                log.warning("cambio %s: modale di assenza non si chiude (retry esauriti)", provincia)
                return {"stato": s, "provincia": st.get("provincia", "")}
            self._chiudi_modali_residui()
            time.sleep(1)
            return self._cambia_provincia_e_ricerca(provincia, _retry=_retry + 1)
        log.warning("cambio provincia %s: stato inatteso %s", provincia, s)
        return {"stato": s, "provincia": st.get("provincia", "")}

    def _salva_debug_dump(self, name: str, invia_telegram: bool = False) -> dict | None:
        """Salva dump HTML e screenshot della pagina corrente per diagnosi.

        Se invia_telegram=True (oppure se il tag indica un errore critico di prenotazione),
        invia automaticamente lo screenshot PNG sul canale Telegram per visibilità immediata.
        """
        from core.debug import salva_debug_dump
        d = getattr(self, "driver", None) or getattr(self.browser, "driver", None)
        dump = salva_debug_dump(d, tag=name, prefix=self.mid)
        if dump and (invia_telegram or "errore_prenotazione" in name or "conferma_errore" in name):
            png_p = dump.get("png_path")
            if png_p and getattr(self, "bot", None) is not None:
                try:
                    caption = f"📸 <b>Screenshot di debug</b> ({self.mid})\n🏷 Riferimento: <code>{dump.get('ref', '')}</code>"
                    if hasattr(self.bot, "send_photo"):
                        self.bot.send_photo(str(png_p), caption=caption)
                    elif hasattr(self.bot, "send_file"):
                        self.bot.send_file(str(png_p), caption=caption)
                except Exception as e_tg:  # noqa: BLE001
                    log.warning("[%s] Invio screenshot Telegram fallito: %s", self.mid, e_tg)
        return dump

    def _attendi_conferma_prenotazione(self, timeout_s: int = 30) -> tuple[bool, str, str]:
        """Attende attivamente la conferma della prenotazione dopo il click su 'Conferma'.

        Ritorna una tupla: (successo: bool, codice_prenotazione: str, messaggio_errore: str).
        - Riconosce schermata di successo (appuntamentoConfermatoCtrl, success-feedback-title,
          testo 'Prenotazione effettuata con successo', 'Appuntamento prenotato', codice prenotazione).
        - Riconosce modali o alert di errore (es. slot scaduto / non più disponibile, errore di rete, ecc.).
        """
        import time
        from services.appuntamento import parse_successo

        d = getattr(self, "driver", None)
        if d is None:
            return False, "", "Browser driver non disponibile"

        for sec in range(max(1, timeout_s)):
            if not self._is_autenticato():
                return False, "", "Sessione scaduta o reindirizzato a login durante la conferma"

            # 1. Controlla modali o alert di errore dal portale
            err_msg = ""
            try:
                err_msg = d.execute_script("""
                    // Modali aperte/visibili
                    var modals = document.querySelectorAll(".modal.in, .modal.show, div[uib-modal-window]");
                    for (var i = 0; i < modals.length; i++) {
                        var m = modals[i];
                        if (m.offsetWidth > 0 || m.offsetHeight > 0 || window.getComputedStyle(m).display !== 'none') {
                            var body = m.querySelector(".modal-body, .modal-content") || m;
                            var t = (body.textContent || "").replace(/\\s+/g, ' ').trim();
                            var low = t.toLowerCase();
                            if (low.includes("non più disponibile") ||
                                low.includes("non e' piu' disponibile") ||
                                low.includes("non andata a buon fine") ||
                                low.includes("si è verificato un errore") ||
                                low.includes("si e' verificato un errore") ||
                                low.includes("si è verificato un problema") ||
                                low.includes("si e' verificato un problema") ||
                                low.includes("impossibile procedere") ||
                                low.includes("impossibile confermare") ||
                                (low.includes("attenzione") && (low.includes("errore") || low.includes("disponibil"))) ||
                                low.includes("errore durante")) {
                                return t;
                            }
                        }
                    }
                    // Alert visibili
                    var alerts = document.querySelectorAll(".alert-danger, #initError");
                    for (var j = 0; j < alerts.length; j++) {
                        var a = alerts[j];
                        if (a.offsetWidth > 0 && a.offsetHeight > 0 && window.getComputedStyle(a).display !== 'none') {
                            var at = (a.textContent || "").replace(/\\s+/g, ' ').trim();
                            if (at) return at;
                        }
                    }
                    return "";
                """) or ""
            except Exception:
                pass

            if isinstance(err_msg, str) and err_msg.strip():
                log.error("[%s] Errore riscontrato dal portale durante la conferma: %s", self.mid, err_msg)
                self._salva_debug_dump("debug_conferma_errore")
                return False, "", err_msg.strip()

            # 2. Controlla schermata di successo
            html = ""
            try:
                html = d.page_source or ""
            except Exception:
                pass

            cod = parse_successo(html)
            has_success_el = False
            try:
                has_success_el = bool(d.execute_script("""
                    return !!(
                        document.querySelector("[ng-controller*='appuntamentoConfermatoCtrl']") ||
                        document.querySelector(".success-feedback-title") ||
                        document.querySelector("#conferma-appuntamenti") ||
                        document.querySelector("appuntamento-confermato")
                    );
                """))
            except Exception:
                pass

            if cod or has_success_el or ("Prenotazione effettuata con successo" in html) or ("Appuntamento prenotato" in html and "appuntamentoConfermatoCtrl" in html):
                log.info("[%s] Prenotazione confermata a video! Codice: %s (in %ds)", self.mid, cod or "(non visibile)", sec)
                return True, cod or "", ""

            time.sleep(1)

        log.warning("[%s] Timeout attesa conferma prenotazione dopo %ds", self.mid, timeout_s)
        dump = self._salva_debug_dump("debug_conferma_timeout")
        dump_msg = f" ({dump['message']})" if dump else ""
        return False, "", f"Timeout dopo {timeout_s}s: nessuna schermata di conferma o codice prenotazione apparso{dump_msg}"

    def _verifica_riscontro_miei_appuntamenti(self, slot, codice: str = "", timeout_s: int = 30) -> bool:
        """Verifica nella sezione 'I miei appuntamenti' che la prenotazione esista realmente."""
        d = getattr(self, "driver", None)
        if d is None:
            return False

        log.info("[%s] Inizio riscontro su 'I miei appuntamenti' per slot %s %s (codice: %s)",
                 self.mid, slot.date_str, slot.time_str, codice or "n/d")
        try:
            self._riduci_tab_a_una()
            d.get("https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/riservata")
            time.sleep(3)
            if not self._is_autenticato():
                log.warning("[%s] Sessione non autenticata durante riscontro", self.mid)
                return False

            # Clicca 'Gestisci Prenotazioni'
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
                self._click_by_or(
                    selectors.RESCHEDULE.get("menu_gestisci", []),
                    "menu gestisci appuntamenti"
                )

            time.sleep(1)
            # Chiudi modali residui e switch al tab 'I miei appuntamenti'
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

            # Prepara termini di ricerca
            ric = (self.monitor.get("ricetta") or "").strip().lower()
            nre = (self.monitor.get("nre") or "").strip().lower()
            cod = codice.strip().lower() if codice else ""
            target_data = slot.date_str.strip()  # es. "04/06/2027"

            # Polling attivo per le card
            for sec in range(max(1, timeout_s)):
                if not self._is_autenticato():
                    log.warning("[%s] Sessione persa durante polling riscontro", self.mid)
                    return False
                try:
                    d.execute_script("var b=document.querySelector('button[ng-click*=messaggiCtrl]');if(b)b.click();")
                except Exception:
                    pass

                # Estrai il testo delle card presenti
                cards_data = []
                try:
                    cards_data = d.execute_script("""
                        var cards = document.querySelectorAll(
                            "li[app-unificato], .app-unificato, .appuntamento, [ng-repeat*='listaRicetteAppuntamentiCtrl']"
                        );
                        return Array.from(cards).map(function(c) {
                            return (c.textContent || '').replace(/\\s+/g, ' ').trim();
                        });
                    """) or []
                except Exception:
                    pass

                page_text = ""
                try:
                    page_text = d.page_source.lower()
                except Exception:
                    pass

                # Match per codice prenotazione (se disponibile)
                if cod and (cod in page_text or any(cod in c.lower() for c in cards_data)):
                    log.info("[%s] Riscontro OK: codice %s trovato tra gli appuntamenti (dopo %ds)",
                             self.mid, cod, sec)
                    return True

                # Match per data slot e prestazione/ricetta
                for card_txt in cards_data:
                    c_low = card_txt.lower()
                    if target_data in card_txt:
                        if nre and nre in c_low:
                            log.info("[%s] Riscontro OK: data %s e NRE %s trovati (dopo %ds)",
                                     self.mid, target_data, nre, sec)
                            return True
                        if ric:
                            import re as _re
                            parole_ricetta = [w for w in _re.findall(r"\b[a-zA-Z]{4,}\b", ric)
                                              if w not in ("senza", "con", "della", "delle", "degli", "visita")]
                            if any(w in c_low for w in parole_ricetta):
                                log.info("[%s] Riscontro OK: data %s e prestazione matching trovati (dopo %ds)",
                                         self.mid, target_data, sec)
                                return True
                        # Se non c'è ricetta specifica o se la data matcha la card
                        log.info("[%s] Riscontro OK: data %s trovata tra le card (dopo %ds)",
                                 self.mid, target_data, sec)
                        return True

                time.sleep(1)

            # Timeout riscontro
            log.warning("[%s] Riscontro fallito dopo %ds: appuntamento per %s non trovato in 'I miei appuntamenti'",
                        self.mid, timeout_s, target_data)
            self._salva_debug_dump("debug_riscontro_non_trovato")
            return False
        except Exception as e:
            log.warning("[%s] Errore durante la verifica riscontro appuntamenti: %s", self.mid, e)
            self._salva_debug_dump("debug_riscontro_errore")
            return False

    # ---- pipeline condivisa ----
    def poll_once(self, manual: bool = False) -> str:
        """Esegue un ciclo di controllo. Ritorna un messaggio di esito (o '').

        manual=True se il giro è stato richiesto esplicitamente dall'utente
        (/poll): solo in quel caso viene inviata la notifica di fine-giro
        "nessuna disponibilità" (nei tick automatici sarebbe invasiva).
        """
        # Se la visita è già stata prenotata:
        action_state = self.store.get_action(self.mid)
        # 1) Per monitor di tipo 'reschedule': riarma sempre a 'idle' per continuare a cercare anticipi
        if self.type == "reschedule" and action_state in ("done", "done_pending_choice"):
            log.info(
                "[%s] Monitor reschedule attivo con action_state=%s: ripristino a idle per proseguire il monitoraggio anticipo",
                self.mid,
                action_state,
            )
            self.store.mark_action(self.mid, "idle", "riarmo monitor reschedule")
            action_state = "idle"

        # 2) Per monitor 'new' in attesa scelta post-prenotazione:
        # se l'utente richiede esplicitamente il controllo (/poll manuale), promuove automaticamente a reschedule
        if self.type == "new" and action_state == "done_pending_choice" and manual:
            log.info("[%s] /poll manuale su monitor done_pending_choice: promozione automatica a ricerca anticipo", self.mid)
            pren = self.store.get_prenotazione(self.mid)
            d_str = pren.get("data_ora") or self.monitor.get("data_attuale") or ""
            data_info = f" ({d_str})" if d_str else ""
            if hasattr(self.bot, "notify"):
                self.bot.notify(
                    f"🔄 <b>{self.mid}</b> risulta già prenotata{data_info}.\n"
                    f"Poiché hai richiesto il controllo con /poll, avvio la ricerca per <b>anticipare</b> la visita."
                )
            self.type = "reschedule"
            self.monitor["type"] = "reschedule"
            if d_str:
                self.monitor["data_attuale"] = d_str
                self.criteri["data_attuale"] = d_str
            self.store.mark_action(self.mid, "idle", "promosso a reschedule da /poll manuale")
            action_state = "idle"

        if action_state in ("done", "done_pending_choice"):
            log.info("[%s] Visita già prenotata (action_state=%s): salto il controllo", self.mid, action_state)
            if manual:
                pren = self.store.get_prenotazione(self.mid)
                d_str = (
                    pren.get("data_ora")
                    or self.monitor.get("data_attuale")
                    or (self.criteri or {}).get("data_a")
                    or ""
                )
                data_info = f" ({d_str})" if d_str else ""
                if action_state == "done_pending_choice":
                    msg = (
                        f"ℹ️ {self.mid} risulta già prenotata{data_info}.\n\n"
                        f"Cosa desideri fare con il monitoraggio per questa visita?\n\n"
                        f"• 🛑 <b>Ferma monitoraggio</b>: interrompe i controlli (l'appuntamento confermato rimane attivo).\n"
                        f"• 🔄 <b>Continua a monitorare (prova ad anticipare)</b>: continua la ricerca automatica cercando esclusivamente date migliori."
                    )
                    righe = [[
                        ("🛑 Ferma monitoraggio", f"postbook:stop:{self.mid}"),
                        ("🔄 Continua a monitorare (prova ad anticipare)", f"postbook:continue:{self.mid}"),
                    ]]
                    if hasattr(self.bot, "notify_buttons"):
                        self.bot.notify_buttons(msg, righe)
                    else:
                        self.bot.notify(msg)
                else:
                    self.bot.notify(
                        f"ℹ️ {self.mid} risulta già prenotata{data_info} e il monitoraggio è completato. "
                        f"Usa /monitora per avviare una nuova ricerca se desideri."
                    )
            return ""

        if not self._is_autenticato():
            msg = "Sessione non autenticata o scaduta (redirect a login)"
            log.warning("[%s] %s", self.mid, msg)
            dump = self._salva_debug_dump("sessione_scaduta")
            dump_msg = f"\n\n{dump['message']}" if dump else ""
            if manual:
                self.bot.notify(f"⚠️ Controllo non riuscito per {self.mid}: {msg}{dump_msg}")
            raise RuntimeError(msg)

        try:
            self.load_page()
            if not self._is_autenticato():
                msg = "Sessione scaduta durante il caricamento della pagina"
                log.warning("[%s] %s", self.mid, msg)
                dump = self._salva_debug_dump("sessione_scaduta_load")
                dump_msg = f"\n\n{dump['message']}" if dump else ""
                if manual:
                    self.bot.notify(f"⚠️ Controllo non riuscito per {self.mid}: {msg}{dump_msg}")
                raise RuntimeError(msg)

            province = list(self.criteri.get("province") or [])
            if self.search_strategy == "first_match" and len(province) > 1:
                return self._cerca_first_match(manual=manual)
            else:
                return self._cerca_best_match(manual=manual)
        except Exception as e:  # noqa: BLE001
            log.error("[%s] load/extract errato: %s", self.mid, e)
            dump = self._salva_debug_dump("errore_ricerca")
            dump_msg = f"\n\n{dump['message']}" if dump else ""
            if manual:
                self.bot.notify(f"⚠️ Controllo non riuscito per {self.mid}: {e}{dump_msg}")
            raise

    def _cerca_best_match(self, manual: bool = False) -> str:
        """Modalità 'best_match': scansiona tutte le province, estrae tutti gli slot
        e propone il migliore in assoluto.
        """
        slots = self.extract_slots()
        seen = self.store.seen_slots(self.mid)
        bl_keys = self.store.get_blacklist_keys(self.mid) if hasattr(self.store, "get_blacklist_keys") else set()
        fresh = [s for s in slots if s.key not in seen and s.key not in bl_keys]

        match = best_match(fresh, self.criteri)
        if not match:
            # aggiorna gli slot visti comunque (per i nuovi che arriveranno)
            self.store.record_slots(self.mid, [s.key for s in slots])
            log.info("[%s] nessun match nuovo (%d disponibili)", self.mid, len(slots))
            # messaggio di fine giro SOLO se richiesto manualmente (/poll)
            if manual and self.criteri.get("notifica_vuoto", True):
                self._notifica_poll_vuoto(slots)
            return ""

        # propongo all'utente
        self.store.record_slots(self.mid, [s.key for s in slots])
        return self._request_approval(match)

    def _cerca_first_match(self, manual: bool = False) -> str:
        """Modalità 'first_match': esamina le province in ordine sequenziale.

        Non appena trova una disponibilità valida nei criteri:
        - si ferma subito senza esaminare le province successive;
        - lascia il browser fermo sulla schermata dei risultati della provincia trovata;
        - chiede l'approvazione all'utente;
        - all'approvazione, prenota direttamente sulla schermata già caricata,
          azzerando i tempi di switch e minimizzando le race condition.
        """
        province = list(self.criteri.get("province") or [])
        seen = self.store.seen_slots(self.mid)
        bl_keys = self.store.get_blacklist_keys(self.mid) if hasattr(self.store, "get_blacklist_keys") else set()
        all_slots = []

        for i, prov in enumerate(province):
            if i > 0:
                log.info("[%s] first_match: passo alla provincia successiva: %s", self.mid, prov)
                esito = self._cambia_provincia_e_ricerca(prov)
                if esito.get("stato") not in ("risultati", "no_risultati"):
                    log.warning("[%s] first_match: ricerca per %s non completata -> skip", self.mid, prov)
                    continue
                self._provincia_corrente = esito.get("provincia") or prov

            slots = self.extract_slots_current_province()
            all_slots.extend(slots)
            fresh = [s for s in slots if s.key not in seen and s.key not in bl_keys]
            match = best_match(fresh, self.criteri)

            if match:
                log.info(
                    "[%s] first_match: trovato slot valido in '%s' (%s ore %s). "
                    "Stop ricerca altre province, browser fermo sulla pagina.",
                    self.mid, prov, match.date_str, match.time_str
                )
                self.store.record_slots(self.mid, [s.key for s in slots])
                return self._request_approval(match)
            else:
                self.store.record_slots(self.mid, [s.key for s in slots])

        # Nessuna provincia contiene slot che soddisfano i criteri
        log.info("[%s] first_match: nessun match nuovo in tutte le province (%d disponibili)", self.mid, len(all_slots))
        if manual and self.criteri.get("notifica_vuoto", True):
            self._notifica_poll_vuoto(all_slots)
        return ""

    def _notifica_poll_vuoto(self, slots):
        """Notifica a fine poll quando non ci sono disponibilità nel range."""
        prov = ""
        if slots:
            # ci sono slot MA non nel range: mostra il primo fuori range
            s0 = slots[0]
            prov = (s0.extra or {}).get("provincia", "")
            dal = (self.criteri.get("data_dal") or "").strip()
            al = (self.criteri.get("data_a") or "").strip()
            if dal and al:
                range_txt = f"da {dal} a {al}"
            elif dal:
                range_txt = f"a partire da {dal}"
            elif al:
                range_txt = f"fino a {al}"
            else:
                range_txt = "richiesto"
            self.bot.notify(
                f"🔍 Controllo completato: nessuna disponibilità nel range {range_txt} "
                f"per {self.mid}."
            )
        else:
            self.bot.notify(
                f"🔍 Controllo completato: nessuna disponibilità trovata per {self.mid}."
            )

    def _request_approval(self, slot) -> str:
        req_id = self.queue.create(self.mid, {"slot": slot.__dict__, "type": self.type})
        import html as _html
        import urllib.parse as _up
        esc = lambda s: _html.escape(str(s or ""))
        extra = slot.extra or {}
        # contesto: provincia (se nota) e range temporale del monitor
        prov = extra.get("provincia", "")
        azienda = extra.get("azienda", "")
        sede = extra.get("sede", "")
        comune = extra.get("comune", "")
        range_txt = ""
        dal = self.criteri.get("data_dal")
        al = self.criteri.get("data_a")
        if dal or al:
            range_txt = f"\n📅 Range richiesto: {dal or 'da oggi'} → {al or 'senza limite'}"
        msg = (
            f"🩺 Trovata disponibilità ({self.mid})\n"
            f"📅 {slot.date_str} ore {slot.time_str}{f' · {prov}' if prov else ''}{range_txt}"
        )
        if azienda:
            msg += f"\n🏥 {esc(azienda)}"
        # luogo: sede (o azienda) + comune, con link Google Maps
        qparts = [p for p in (sede or azienda, comune) if p.strip()]
        if qparts:
            q = _up.quote(" ".join(p.strip() for p in qparts))
            maps_url = f"https://www.google.com/maps/search/?api=1&query={q}"
            luogo = []
            if sede and sede != azienda:
                luogo.append(esc(sede))
            elif azienda and not sede:
                luogo.append(esc(azienda))
            if comune:
                luogo.append(f"({esc(comune)})")
            msg += f"\n📍 {' · '.join(luogo)}"
            msg += f'\n🗺 <a href="{maps_url.replace("&", "&amp;")}">Apri in Google Maps</a>'
        msg += f"\n{esc(self.criteri.get('note', ''))}"
        msg += f"\n\nVuoi procedere?"
        # pulsanti inline: Approva/Rifiuta (per reschedule la scelta anticipa/
        # posticipa è IMPLICITA nella data scelta, non serve chiederla)
        righe = [[("✅ Approva", f"decide:approve:{req_id}"),
                  ("❌ Rifiuta", f"decide:deny:{req_id}")]]
        inviati = self.bot.notify_buttons(msg, righe)
        # attendo la decisione (bloccante, con timeout)
        decision = self.queue.wait_decision(req_id)
        scaduta = decision.get("cancelled") and decision.get("reason") == "timeout"
        if decision.get("cancelled") or not decision.get("decision"):
            log.info("[%s] approvazione negata/scaduta", self.mid)
            # proposta scaduta per timeout: aggiorna il messaggio su Telegram
            # (mantiene i dettagli e toglie i pulsanti, segnalando la scadenza)
            if scaduta and inviati:
                testo = f"{msg}\n\n⏳ Proposta scaduta: il tempo per rispondere è terminato, la disponibilità potrebbe non essere più valida."
                self.bot.edit_message(testo, inviati)
            else:
                # Rifiuto esplicito dell'utente: aggiungi lo slot alla blacklist
                if hasattr(self.store, "add_blacklist_entry"):
                    nre = (self.monitor.get("nre") or "").strip()
                    data_ricetta = (self.monitor.get("data_ricetta") or "").strip()
                    self.store.add_blacklist_entry(self.mid, slot, nre=nre, data_ricetta=data_ricetta)
                    log.info("[%s] Aggiunto slot %s alla blacklist", self.mid, getattr(slot, "key", str(slot)))
            return f"Disponibilità {slot} non confermata."

        # approvata -> esegui l'azione reale
        try:
            self._ultima_azione = (decision.get("extra") or {}).get("azione", "approve")
            self.execute(slot)
            self.store.mark_action(self.mid, "done_pending_choice", str(slot))
            self.bot.notify(f"✅ Appuntamento {slot.date_str} ore {slot.time_str} prenotato.")
            self._chiedi_post_prenotazione(slot)
            return "ok"
        except Exception as e:  # noqa: BLE001
            log.exception("[%s] esecuzione fallita", self.mid)
            dump = self._salva_debug_dump("errore_prenotazione", invia_telegram=True)
            dump_msg = f"\n\n{dump['message']}" if dump else ""
            self.store.mark_action(self.mid, "idle", f"fallita prenotazione: {e}")
            self.bot.notify(
                f"❌ <b>Prenotazione non riuscita</b> per <b>{self.mid}</b>:\n{e}{dump_msg}\n\n"
                f"ℹ️ Il monitoraggio rimane attivo e continuerà a cercare nuove date disponibili."
            )
            return "errore"

    def _chiedi_post_prenotazione(self, slot) -> None:
        """Invia all'utente la richiesta con bottoni per decidere se fermare o proseguire il monitoraggio."""
        nome = self.monitor.get("ricetta") or self.mid
        msg = (
            f"🎯 <b>Prenotazione completata!</b> ({nome})\n"
            f"📅 Data confermata: <b>{slot.date_str} ore {slot.time_str}</b>\n\n"
            f"Cosa desideri fare con il monitoraggio per questa visita?\n\n"
            f"• 🛑 <b>Ferma monitoraggio</b>: interrompe i controlli (l'appuntamento confermato rimane attivo).\n"
            f"• 🔄 <b>Continua a monitorare (prova ad anticipare)</b>: continua la ricerca automatica cercando esclusivamente date migliori "
            f"(anticipo prima del <b>{slot.date_str}</b> rispetto a quella appena fissata)."
        )
        righe = [[
            ("🛑 Ferma monitoraggio", f"postbook:stop:{self.mid}"),
            ("🔄 Continua a monitorare (prova ad anticipare)", f"postbook:continue:{self.mid}"),
        ]]
        try:
            if hasattr(self.bot, "notify_buttons"):
                self.bot.notify_buttons(msg, righe)
            else:
                self.bot.notify(msg)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] invio richiesta post-prenotazione fallito: %s", self.mid, e)
