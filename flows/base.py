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
import re

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
            if(sp.selectedOptions[0])st.provincia=sp.selectedOptions[0].textContent.trim();
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
            var fs=document.querySelectorAll('span.field-label');
            for(var j=0;j<fs.length;j++){
                if((fs[j].textContent||'').trim()==='Provincia'){
                    var sib=fs[j].nextElementSibling;
                    if(sib)st.provincia=(sib.textContent||'').trim();
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

    # ---- pipeline condivisa ----
    def poll_once(self, manual: bool = False) -> str:
        """Esegue un ciclo di controllo. Ritorna un messaggio di esito (o '').

        manual=True se il giro è stato richiesto esplicitamente dall'utente
        (/poll): solo in quel caso viene inviata la notifica di fine-giro
        "nessuna disponibilità" (nei tick automatici sarebbe invasiva).
        """
        if not self._is_autenticato():
            msg = "Sessione non autenticata o scaduta (redirect a login)"
            log.warning("[%s] %s", self.mid, msg)
            if manual:
                self.bot.notify(f"⚠️ Controllo non riuscito per {self.mid}: {msg}")
            raise RuntimeError(msg)

        try:
            self.load_page()
            if not self._is_autenticato():
                msg = "Sessione scaduta durante il caricamento della pagina"
                log.warning("[%s] %s", self.mid, msg)
                if manual:
                    self.bot.notify(f"⚠️ Controllo non riuscito per {self.mid}: {msg}")
                raise RuntimeError(msg)

            slots = self.extract_slots()
        except Exception as e:  # noqa: BLE001
            log.error("[%s] load/extract errato: %s", self.mid, e)
            if manual:
                self.bot.notify(f"⚠️ Controllo non riuscito per {self.mid}: {e}")
            raise

        # filtra per evitare di riproporre slot già visti
        seen = self.store.seen_slots(self.mid)
        fresh = [s for s in slots if s.key not in seen]

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

    def _notifica_poll_vuoto(self, slots):
        """Notifica a fine poll quando non ci sono disponibilità nel range."""
        prov = ""
        if slots:
            # ci sono slot MA non nel range: mostra il primo fuori range
            s0 = slots[0]
            prov = (s0.extra or {}).get("provincia", "")
            dal = self.criteri.get("data_dal")
            al = self.criteri.get("data_a")
            range_txt = f"da {dal} a {al}" if dal or al else "range"
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
