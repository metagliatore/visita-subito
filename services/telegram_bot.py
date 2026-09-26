"""Bot Telegram: notifiche + flusso di conferma prenotazioni.

Comandi base:
  /status   -> stato dei monitor e della sessione
  /poll     -> forza il polling adesso
  /relogin  -> chiede di avviare il re-login manuale nel prompt di chi esegue
  /approve <id> / /deny <id> -> conferma/rifiuta una richiesta pendente
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

log = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 Comandi disponibili:\n"
    "\n🔎 <b>Operazioni</b>\n"
    "/start - scegli su cosa lavorare\n"
    "/monitora - crea un monitor (con wizard guidato: scelta, province, date)\n"
    "/stop - lista dei monitor e scelta di quello da fermare\n"
    "/status - stato monitor e sessione\n"
    "/poll - forza il controllo disponibilità\n"
    "\n📋 <b>Informazioni</b>\n"
    "/ricette - elenca le ricette da prenotare\n"
    "/appuntamenti - elenca gli appuntamenti esistenti\n"
    "\n❓ /help - questo messaggio"
)


CATEGORIA_LABEL = {
    "specialistica": "🩺 specialistica (automatizzabile)",
    "laboratorio": "🧪 analisi/laboratorio (NON prenotabile in automatico)",
    "farmaceutica": "💊 farmaceutica (non prenotabile)",
}


class TelegramBot:
    def __init__(self, token: str, chat_id: str, chat_id_secondary: str = ""):
        self.token = token
        self.chat_ids = [c for c in (chat_id, chat_id_secondary) if c]
        self.app = None
        self.controller = None  # collegato da main per /poll /status /relogin
        # stato wizard monitoraggio per chat
        self._wizard = {}  # chat_id -> snapshot per il wizard /monitora

    # ---------- invio (usato dal poller/flussi) ----------
    async def _send(self, text: str) -> None:
        if not (self.token and self.chat_ids):
            log.warning("TG non configurato, notifica non inviata: %s", text)
            return
        app = Application.builder().token(self.token).build()
        async with app:
            for cid in self.chat_ids:
                try:
                    await app.bot.send_message(chat_id=cid, text=text,
                                               parse_mode=ParseMode.HTML)
                except Exception as e:  # noqa: BLE001
                    log.error("invio TG a %s fallito: %s", cid, e)

    def notify(self, text: str) -> None:
        """Callback sincrona da flussi/poller (crea un loop ad-hoc)."""
        try:
            asyncio.run(self._send(text))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._send(text))
            loop.close()
        except Exception as e:  # noqa: BLE001
            log.error("notify TG fallita: %s", e)

    # ---------- invio con pulsanti inline (da flussi/poller) ----------
    async def _send_buttons(self, text: str, buttons: list) -> list:
        """Invia testo + pulsanti inline; ritorna [(chat_id, message_id), ...]
        cosi' il chiamante puo' editare il messaggio in seguito (es. scadenza)."""
        if not (self.token and self.chat_ids):
            log.warning("TG non configurato, notifica con pulsanti non inviata")
            return []
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(lbl, callback_data=cb) for lbl, cb in row
        ] for row in buttons])
        sent = []
        app = Application.builder().token(self.token).build()
        async with app:
            for cid in self.chat_ids:
                try:
                    m = await app.bot.send_message(chat_id=cid, text=text,
                                                   reply_markup=kb, parse_mode=ParseMode.HTML)
                    sent.append((cid, m.message_id))
                except Exception as e:  # noqa: BLE001
                    log.error("invio TG con pulsanti a %s fallito: %s", cid, e)
        return sent

    def notify_buttons(self, text: str, buttons: list) -> list:
        """Invia messaggio con pulsanti inline (sincrono).

        Ritorna la lista [(chat_id, message_id)] dei messaggi inviati
        (per eventuali edit successivi, es. marcare la proposta scaduta).
        """
        try:
            return asyncio.run(self._send_buttons(text, buttons))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._send_buttons(text, buttons))
            finally:
                loop.close()
        except Exception as e:  # noqa: BLE001
            log.error("notify_buttons TG fallita: %s", e)
            return []

    # ---------- edit messaggi inviati (es. scadenza proposta) ----------
    async def _edit_message(self, text: str, destinations: list) -> None:
        """Sostituisce il testo (e rimuove i pulsanti) dei messaggi inviati."""
        if not (self.token and destinations):
            return
        app = Application.builder().token(self.token).build()
        async with app:
            for cid, mid in destinations:
                try:
                    await app.bot.edit_message_text(
                        chat_id=cid, message_id=mid, text=text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([]))
                except Exception as e:  # noqa: BLE001
                    log.error("edit TG %s/%s fallito: %s", cid, mid, e)

    def edit_message(self, text: str, destinations: list) -> None:
        """Sincrono: edita i messaggi (usato da flussi/poller)."""
        try:
            asyncio.run(self._edit_message(text, destinations))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._edit_message(text, destinations))
            finally:
                loop.close()
        except Exception as e:  # noqa: BLE001
            log.error("edit_message TG fallita: %s", e)

    # ---------- invio file (es. .ics) ----------
    async def _send_file(self, path: str, caption: str = "") -> None:
        if not (self.token and self.chat_ids):
            log.warning("TG non configurato, file non inviato: %s", path)
            return
        app = Application.builder().token(self.token).build()
        async with app:
            for cid in self.chat_ids:
                try:
                    with open(path, "rb") as f:
                        await app.bot.send_document(chat_id=cid, document=f, filename=path.split("/")[-1], caption=caption, parse_mode=ParseMode.HTML)
                except Exception as e:  # noqa: BLE001
                    log.error("invio file TG a %s fallito: %s", cid, e)

    def send_file(self, path: str, caption: str = "") -> None:
        """Invia un documento (es. event .ics) da un flusso/poller."""
        try:
            asyncio.run(self._send_file(path, caption))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._send_file(path, caption))
            loop.close()
        except Exception as e:  # noqa: BLE001
            log.error("send_file TG fallita: %s", e)

    # ---------- helper autorizzazione ----------
    def _autorizzato(self, update: Update) -> bool:
        """Solo i chat_id autorizzati (config) possono usare il bot."""
        chat = update.effective_chat
        if chat is None:
            return False
        # confronto robusto: accetta sia int che str (chat.id è int, config str)
        return str(chat.id) in {str(c) for c in self.chat_ids}

    @staticmethod
    def _esc(s: str) -> str:
        """Escape HTML dei testi dinamici (per parse_mode=HTML)."""
        return (str(s or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))

    def _risveglia_se_needed(self, update) -> bool:
        """Se il login è bloccato, un qualunque comando lo risveglia e riprova.

        Ritorna True se abbiamo risvegliato (l'handler avvisa e avvia il login).
        """
        if self.controller is None:
            return False
        if self.controller.login_bloccato:
            import threading
            auth_desc = getattr(self.controller.auth_config, "describe", lambda: "Autenticazione")() if hasattr(self.controller, "auth_config") else "Autenticazione"
            self.notify(f"🔄 Tentativi di login sbloccati! Avvio un nuovo tentativo di accesso ({auth_desc})... controlla il dispositivo per approvare!")
            threading.Thread(target=self.controller.risveglia, daemon=True).start()
            return True
        return False

    # ---------- handler ----------
    async def _h_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            await update.message.reply_text("Bot non disponibile.")
            return
        if self._risveglia_se_needed(update):
            return
        await update.message.reply_text(
            "👋 Benvenuto! Scegli su cosa vuoi lavorare:\n\n"
            "• /ricette — su una RICETTA da prenotare (nuove disponibilità)\n"
            "• /appuntamenti — su un APPUNTAMENTO già preso (anticipa/posticipa)\n\n"
            "Oppure /help per l'elenco completo.")

    # ---------- calendario inline ----------
    @staticmethod
    def _cal_keyboard(anno, mese, sel=None):
        """Costruisce un Mini-calendario inline (griglia giorni + frecce mese).

        Callback: cal:prev, cal:next, cal:day:YYYY-MM-DD
        """
        import calendar as _cal
        from datetime import date
        bottoni = []
        # intestazione mese/anno con frecce
        bottoni.append([
            InlineKeyboardButton("‹", callback_data="cal:prev"),
            InlineKeyboardButton(f"{_cal.month_name[mese]} {anno}",
                                 callback_data="cal:noop"),
            InlineKeyboardButton("›", callback_data="cal:next"),
        ])
        # giorni settimana
        sett = ["Lu", "Ma", "Me", "Gi", "Ve", "Sa", "Do"]
        bottoni.append([InlineKeyboardButton(g, callback_data="cal:noop") for g in sett])
        # griglia giorni
        prima, num = _cal.monthrange(anno, mese)
        # lunedì primo giorno (calendar.MONDAY=0 -> lundi index 0)
        riga = []
        # sfalsamento: python calendar.monthrange da lun=0 a dom=6
        for _ in range(prima):
            riga.append(InlineKeyboardButton(" ", callback_data="cal:noop"))
        for giorno in range(1, num + 1):
            d = date(anno, mese, giorno)
            dstr = d.strftime("%Y-%m-%d")
            lbl = f"📍 {giorno}" if sel == dstr else str(giorno)
            riga.append(InlineKeyboardButton(lbl, callback_data=f"cal:day:{dstr}"))
            if len(riga) == 7:
                bottoni.append(riga); riga = []
        if riga:
            bottoni.append(riga)
        return InlineKeyboardMarkup(bottoni)

    def _mostra_calendario(self, chat_id, anno=None, mese=None, sel=None):
        """Ritorna il messaggio + markup calendario (lo stato con date correnti)."""
        import datetime as _dt
        wiz = self._wizard.get(chat_id, {})
        now = _dt.date.today()
        anno = anno or wiz.get("cal_anno", now.year)
        mese = mese or wiz.get("cal_mese", now.month)
        wiz["cal_anno"] = anno; wiz["cal_mese"] = mese
        self._wizard[chat_id] = wiz
        testo = "📅 Seleziona la data (calendario):"
        if sel:
            testo = f"📅 Data selezionata: <b>{sel}</b>\nSeleziona la data:"
        return testo, self._cal_keyboard(anno, mese, sel)

    async def _h_cal(self, update, chat_id):
        """Gestisce i callback del calendario (prev/next/day)."""
        import datetime as _dt
        import calendar as _cal
        q = update.callback_query
        data = q.data or ""
        wiz = self._wizard.get(chat_id, {})
        anno = wiz.get("cal_anno") or _dt.date.today().year
        mese = wiz.get("cal_mese") or _dt.date.today().month
        if data == "cal:prev":
            mese -= 1
            if mese == 0: mese = 12; anno -= 1
        elif data == "cal:next":
            mese += 1
            if mese == 13: mese = 1; anno += 1
        elif data.startswith("cal:day:"):
            dstr = data.split(":", 2)[2]  # YYYY-MM-DD
            giorno = _dt.datetime.strptime(dstr, "%Y-%m-%d").date()
            # salva nel wizard e prosegue
            await self._data_selezionata(update, chat_id, giorno)
            return
        elif data == "cal:noop":
            await q.answer()
            return
        testo, kb = self._mostra_calendario(chat_id, anno, mese)
        try:
            await q.edit_message_text(testo, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception as e:  # noqa: BLE001
            log.warning("cal agg: %s", e)

    async def _data_selezionata(self, update, chat_id, giorno):
        """Dopo il giorno nel calendario, in base allo step aggiorna il flusso."""
        import datetime as _dt
        q = update.callback_query
        wiz = self._wizard.get(chat_id, {})
        step = wiz.get("step")
        if step == "range_da":
            wiz["data_dal"] = giorno.strftime("%d/%m/%Y")
            wiz["step"] = "range_a"
            self._wizard[chat_id] = wiz
            await q.edit_message_text(
                f"✅ Da: <b>{giorno.strftime('%d/%m/%Y')}</b>\n\nOra seleziona la <b>data fino a cui</b> cercare:",
                reply_markup=self._cal_keyboard(giorno.year, giorno.month, sel=giorno.strftime("%Y-%m-%d")),
                parse_mode=ParseMode.HTML)
        elif step == "range_a":
            wiz["data_a"] = giorno.strftime("%d/%m/%Y")
            self._wizard[chat_id] = wiz
            await self._crea_monitor_finale(update, chat_id, wiz)

    async def _crea_monitor_finale(self, update, chat_id, wiz):
        """Crea il monitor con range e conferma."""
        target = wiz.get("target", {})
        tipo = wiz.get("tipo", "new")
        prov_list = wiz.get("province", [])
        data_dal = wiz.get("data_dal", "")
        data_a = wiz.get("data_a", "")
        # le target-ricetta hanno 'prestazioni' (lista) e 'codice' (NRE);
        # gli appuntamenti hanno 'prestazione' (stringa). Prima il nome della
        # prestazione, poi gli altri campi, e solo alla fine il fallback.
        prestazioni = target.get("prestazioni") or []
        if prestazioni:
            desc = ", ".join(p for p in prestazioni if p)
        else:
            desc = target.get("prestazione") or target.get("descrizione") or ""
        nre = target.get("codice") or ""
        if not desc and not nre:
            # mai piu' placeholder 'monitor': senza nome prestazione (ricetto) e
            # senza NRE il flow non saprebbe quale card aprire
            self._wizard.pop(chat_id, None)
            await q.edit_message_text(
                "⚠️ Non riesco a ricavare la prestazione da monitorare dall'elenco. "
                "Riprova con /monitora e scegli la ricetta dall'elenco.")
            return
        mid = self.controller.aggiungi_monitor(
            tipo, desc, nre=nre,
            criteri={"province": prov_list, "data_dal": data_dal, "data_a": data_a})
        self._wizard.pop(chat_id, None)
        q = update.callback_query
        if mid:
            prov_txt = ", ".join(prov_list) if prov_list else "default"
            nre_txt = f"\n🔖 NRE: {self._esc(nre)}" if nre else ""
            await q.edit_message_text(
                f"✅ Monitor creato!\n🎯 {self._esc(desc)}{nre_txt}\n🏙 {self._esc(prov_txt)}\n"
                f"📅 Range: {self._esc(data_dal)} → {self._esc(data_a)}\n"
                f"🆔 {self._esc(mid)}\n\n"
                f"Ti notificherò solo se trovo disponibilità nel range.")
        else:
            await q.edit_message_text("Errore creazione monitor.")

    async def _h_monitora(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        if self._risveglia_se_needed(update):
            return
        if self.controller is None:
            await update.message.reply_text("Controller non inizializzato.")
            return
        await update.message.reply_text(
            "📌 Su cosa vuoi monitorare?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📅 Appuntamenti già presi", callback_data="mon:app"),
                InlineKeyboardButton("📋 Ricette da prenotare", callback_data="mon:ric"),
            ]]))

    async def _h_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Gestisce i click sui pulsanti (wizard di monitoraggio)."""
        if not self._autorizzato(update):
            await update.answer("Non autorizzato")
            return
        q = update.callback_query
        data = q.data or ""
        chat_id = update.effective_chat.id
        try:
            # download ricetta dal comando /ricette
            if data.startswith("ric:dl:"):
                idx = int(data.split(":", 2)[2])
                wiz = self._wizard.get(chat_id, {})
                ric = wiz.get("ricette", [])
                if idx < len(ric) and ric[idx].get("link_download"):
                    await self._scarica_ricetta(update, ric[idx])
                else:
                    await q.answer("Ricetta non trovata")
                return
            if data == "ric:done":
                self._wizard.pop(chat_id, None)
                await q.edit_message_text("Ok 👍")
                return
            # decisione su proposta (approve/deny/anticipa/posticipa via pulsanti)
            if data.startswith("decide:"):
                parti = data.split(":")
                azione = parti[1]
                req_id = ":".join(parti[2:])
                approved = azione in ("approve", "anticipa", "posticipa")
                if self.controller:
                    self.controller.decide_request(req_id, approved, extra={"azione": azione})
                    await q.edit_message_text(
                        {"approve": "✅ Approvato: procedo con la prenotazione.",
                         "deny": "❌ Richiesta rifiutata.",
                         "anticipa": "↩️ Anticipo dell'appuntamento in corso...",
                         "posticipa": "↪️ Posticipo dell'appuntamento in corso...",
                         }.get(azione, "Azione registrata."))
                else:
                    await q.answer("Controller non inizializzato")
                return
            # scelta post-prenotazione: stop oppure continua (converte in reschedule)
            if data.startswith("postbook:"):
                parti = data.split(":", 2)
                scelta = parti[1]
                mid = parti[2]
                if not self.controller:
                    await q.answer("Controller non inizializzato.")
                    return
                if scelta == "stop":
                    self.controller.rimuovi_monitor(mid)
                    await q.edit_message_text(
                        f"🛑 <b>Monitoraggio interrotto per {self._esc(mid)}.</b>\n\n"
                        f"La prenotazione effettuata rimane confermata sul tuo Fascicolo Sanitario.",
                        parse_mode=ParseMode.HTML,
                    )
                    return
                elif scelta == "continue":
                    ok = self.controller.converti_in_reschedule(mid)
                    if ok:
                        pren = self.controller.store.get_prenotazione(mid)
                        data_ora = pren.get("data_ora") or "la data prenotata"
                        await q.edit_message_text(
                            f"🔄 <b>Monitoraggio aggiornato: ricerca anticipo attiva!</b>\n\n"
                            f"📌 Appuntamento attuale: <b>{self._esc(data_ora)}</b>\n"
                            f"Il bot continuerà a verificare automaticamente per provare ad anticipare la visita rispetto alla data prenotata.",
                            parse_mode=ParseMode.HTML,
                        )
                    else:
                        await q.edit_message_text(f"⚠️ Impossibile aggiornare il monitor {self._esc(mid)}.")
                    return
            # calendario inline (seleziona data)
            if (data or "").startswith("cal:"):
                await self._h_cal(update, chat_id)
                return
            # stop monitor dalla lista
            if data.startswith("stop:pick:"):
                mid = data.split(":", 2)[2]
                if self.controller.rimuovi_monitor(mid):
                    await q.edit_message_text(f"🛑 Monitor {mid} interrotto.")
                else:
                    await q.edit_message_text(f"Monitor {mid} non trovato.")
                return
            if data == "stop:annulla":
                await q.edit_message_text("Operazione annullata.")
                return
            if data == "mon:app":
                app = self.controller.get_appuntamenti()
                if app is None:
                    await q.edit_message_text("⚠️ Impossibile recuperare gli appuntamenti: sessione SPID scaduta o login fallito. Usa /status o /poll per riprovare.")
                    return
                if not app:
                    await q.edit_message_text("Nessun appuntamento trovato.")
                    return
                kb = [[InlineKeyboardButton(
                    f"{a.get('prestazione','')[:25]} · {a.get('data_ora','')}",
                    callback_data=f"mon:pick_app:{i}")] for i, a in enumerate(app)]
                self._wizard[chat_id] = {"step": "app_list", "appuntamenti": app}
                await q.edit_message_text("📅 Scegli l'appuntamento da monitorare:",
                                         reply_markup=InlineKeyboardMarkup(kb))
            elif data.startswith("mon:pick_app:"):
                idx = int(data.split(":")[2])
                wiz = self._wizard.get(chat_id, {})
                app = wiz.get("appuntamenti", [])
                if idx >= len(app):
                    await q.answer("Scelta non valida"); return
                target = app[idx]
                self._wizard[chat_id] = {"step": "provincia", "target": target,
                                         "tipo": "reschedule"}
                await self._chiedi_provincia(update, ctx, chat_id)
            elif data.startswith("mon:prov:") and data != "mon:prov:__ferma__":
                prov = data.split(":", 2)[2]
                wiz = self._wizard.get(chat_id, {})
                # toggle selezione multipla
                sel = wiz.setdefault("province", [])
                if prov in sel:
                    sel.remove(prov)
                else:
                    sel.append(prov)
                wiz["step"] = "provincia"
                self._wizard[chat_id] = wiz
                await self._chiedi_provincia(update, ctx, chat_id)
                # conferma il click (small feedback), non cambia il testo a parte il toggle
                return
            elif data == "mon:conferma":
                wiz = self._wizard.get(chat_id, {})
                if not wiz.get("province"):
                    await q.answer("Seleziona almeno una provincia")
                    return
                await self._conferma_monitor(update, ctx, chat_id, wiz)
            elif data == "mon:ric":
                ric = self.controller.get_ricette()
                if ric is None:
                    await q.edit_message_text("⚠️ Impossibile recuperare le ricette: sessione SPID scaduta o login fallito. Usa /status o /poll per riprovare.")
                    return
                if not ric:
                    await q.edit_message_text("Nessuna ricetta trovata.")
                    return
                kb = [[InlineKeyboardButton(
                    f"{', '.join(r.get('prestazioni', [])[:1])[:30]}",
                    callback_data=f"mon:pick_ric:{i}")] for i, r in enumerate(ric[:12])]
                self._wizard[chat_id] = {"step": "ric_list", "ricette": ric}
                await q.edit_message_text("📋 Scegli la ricetta da monitorare:",
                                         reply_markup=InlineKeyboardMarkup(kb))
            elif data.startswith("mon:pick_ric:"):
                idx = int(data.split(":")[2])
                wiz = self._wizard.get(chat_id, {})
                ric = wiz.get("ricette", [])
                if idx >= len(ric):
                    await q.answer("Scelta non valida"); return
                target = ric[idx]
                self._wizard[chat_id] = {"step": "provincia", "target": target,
                                        "tipo": "new"}
                await self._chiedi_provincia(update, ctx, chat_id)
            elif data == "mon:annulla":
                self._wizard.pop(chat_id, None)
                await q.edit_message_text("Monitoraggio annullato.")
            else:
                await q.answer("Scelta non riconosciuta")
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            try:
                await q.edit_message_text(f"Errore: {e}")
            except Exception:  # noqa: BLE001
                pass

    async def _chiedi_provincia(self, update, ctx, chat_id):
        """Mostra la scelta MULTIPLA delle province (toggle col click).
        Lo stesso messaggio si aggiorna a ogni click; poi 'Conferma'.
        """
        wiz = self._wizard.get(chat_id, {})
        sel = wiz.get("province", [])
        prov = self.controller.lista_province()
        kb = [[InlineKeyboardButton(
            f"{'✅' if p in sel else '⬜'} {p}",
            callback_data=f"mon:prov:{p}")] for p in prov]
        kb.insert(0, [InlineKeyboardButton("✅ Conferma", callback_data="mon:conferma")])
        kb.append([InlineKeyboardButton("❌ Annulla", callback_data="mon:annulla")])
        testo = ("🏙 Seleziona le province da controllare (multiple):\n"
                 + "\n".join(f"{'✅' if p in sel else '⬜'} {p}" for p in prov)
                 + "\n\n👉 Clicca le province per selezionarle, poi <b>Conferma</b>.")
        try:
            await update.callback_query.edit_message_text(
                testo, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:  # noqa: BLE001
            import traceback; traceback.print_exc()
            await update.callback_query.answer(f"err {e}")

    async def _conferma_monitor(self, update, ctx, chat_id, wiz):
        """Dopo le province: mostra il CALENDARIO per scegliere la data 'Da'."""
        import datetime as _dt
        wiz["step"] = "range_da"
        self._wizard[chat_id] = wiz
        now = _dt.date.today()
        testo, kb = self._mostra_calendario(chat_id, now.year, now.month)
        await update.callback_query.edit_message_text(
            "📅 Scegli la <b>data da cui</b> cercare:",
            reply_markup=kb, parse_mode=ParseMode.HTML)

    async def _gestisci_range_da(self, update, chat_id, testo):
        """(Deprecato: range via calendario)"""
        pass

    async def _gestisci_range_a(self, update, chat_id, testo):
        """(Deprecato: range via calendario)"""
        pass

    async def _h_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        if self._risveglia_se_needed(update):
            return
        if self.controller is None:
            await update.message.reply_text("Controller non inizializzato.")
            return
        # con id esplicito: rimuove subito
        if ctx.args:
            mid = ctx.args[0]
            if self.controller.rimuovi_monitor(mid):
                await update.message.reply_text(f"🛑 Monitor {mid} interrotto.")
            else:
                await update.message.reply_text(f"Monitor {mid} non trovato.")
            return
        # senza argomenti: mostra la lista con pulsanti
        monitor = self.controller.monitors_attivi()
        if not monitor:
            await update.message.reply_text("Nessun monitor attivo.")
            return
        kb = [[InlineKeyboardButton(
            f"{m.get('ricetta','')[:28]} [{m.get('type','')}]",
            callback_data=f"stop:pick:{m['id']}")] for m in monitor]
        kb.append([InlineKeyboardButton("❌ Annulla", callback_data="stop:annulla")])
        await update.message.reply_text(
            "🛑 Scegli un monitor da interrompere:",
            reply_markup=InlineKeyboardMarkup(kb))

    async def _h_appuntamenti(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        if self._risveglia_se_needed(update):
            return
        if self.controller is None:
            await update.message.reply_text("Controller non inizializzato.")
            return
        await update.message.reply_text("⏳ Recupero gli appuntamenti dal portale...")
        try:
            appuntamenti = self.controller.get_appuntamenti()
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"Errore lettura appuntamenti: {e}")
            return
        if appuntamenti is None:
            await update.message.reply_text("⚠️ Impossibile recuperare gli appuntamenti: sessione SPID scaduta o login fallito. Verifica con /status o riprova con /poll.")
            return
        if not appuntamenti:
            await update.message.reply_text("Nessun appuntamento trovato.")
            return
        lines = [f"📋 {len(appuntamenti)} appuntamenti trovati:"]
        for i, a in enumerate(appuntamenti):
            lines.append(f"\n{i + 1}) {self._esc(a.get('prestazione',''))}")
            if a.get("data_ora"):
                lines.append(f"   🗓 {self._esc(a.get('data_ora',''))}")
            azienda = a.get("azienda","").strip()
            presi = a.get("presentarsi_in","").strip()
            indirizzo = a.get("indirizzo","").strip()
            cap = a.get("cap","").strip()
            comune = (a.get("comune","") or "").strip()
            if azienda:
                lines.append(f"   🏥 Azienda: {self._esc(azienda)}")
            # luogo: presidio (se != azienda), indirizzo, comune
            luogo = []
            if presi:
                luogo.append(self._esc(presi))
            elif azienda:
                luogo.append(self._esc(azienda))
            if indirizzo:
                luogo.append(self._esc(indirizzo))
            if comune:
                luogo.append(f"({self._esc(comune)}{f', {self._esc(cap)}' if cap else ''})")
            if luogo:
                lines.append(f"   📍 {' · '.join(luogo)}")
                # link Google Maps (query = struttura/azienda + comune)
                qparts = [p for p in (presi or azienda, indirizzo, comune) if p]
                if qparts:
                    import urllib.parse as _up
                    q = _up.quote(" ".join(qparts))
                    maps_url = f"https://www.google.com/maps/search/?api=1&query={q}"
                    lines.append(f'   🗺 <a href="{maps_url.replace("&", "&amp;")}">Apri in Google Maps</a>')
            if a.get("codice"):
                lines.append(f"   🎫 {self._esc(a.get('codice',''))}")
        msg = "\n".join(lines)
        for chunk in (msg[i:i + 3800] for i in range(0, len(msg), 3800)):
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

    async def _h_ricette(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        if self._risveglia_se_needed(update):
            return
        if self.controller is None:
            await update.message.reply_text("Controller non inizializzato.")
            return
        await update.message.reply_text("⏳ Recupero le ricette dal portale...")
        try:
            ricette = self.controller.get_ricette()  # list[dict] | None
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"Errore lettura ricette: {e}")
            return
        if ricette is None:
            await update.message.reply_text("⚠️ Impossibile recuperare le ricette: sessione SPID scaduta o login fallito. Verifica con /status o riprova con /poll.")
            return
        if not ricette:
            await update.message.reply_text("Nessuna ricetta trovata.")
            return
        # limita alle prime 3 più recenti (il parser ordina per data desc)
        ricette = ricette[:3]
        lines = [f"📋 Le {len(ricette)} ricette più recenti:\n"]
        for i, r in enumerate(ricette):
            prest = ", ".join(r["prestazioni"])
            if len(prest) > 45:
                prest = prest[:42] + "…"
            stato = (r.get("stato") or "").strip()
            if stato.lower().startswith("prescritta"):
                stato_icona = "🅿️ Da prenotare"
            elif "accettata" in stato.lower() or "prenotata" in stato.lower():
                stato_icona = "✅ Già prenotata"
            elif "erogata" in stato.lower():
                stato_icona = "⚪ Erogata"
            else:
                stato_icona = stato or "—"
            data = (r.get("data_ricetta") or "")[-5:]  # MM-DD
            if data and data != "MM-DD":
                data_txt = f" · 📅 {data}"
            else:
                data_txt = ""
            cat_icona = {"specialistica": "🩺", "laboratorio": "🧪", "farmaceutica": "💊"}.get(r["categoria"], "📄")
            scarica = " ⬇️" if r.get("link_download") else ""
            lines.append(f"{i+1}) {cat_icona} <b>{self._esc(prest)}</b>")
            lines.append(f"   {stato_icona}{data_txt} · {self._esc(r['codice'])}{scarica}")
        # invia a pezzi se troppo lungo
        msg = "\n".join(lines)
        for chunk in (msg[i:i + 3800] for i in range(0, len(msg), 3800)):
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
        # pulsanti download per le ricette con link
        self._wizard[update.effective_chat.id] = {"ricette": ricette}
        scaricabili = [(i, r) for i, r in enumerate(ricette) if r.get("link_download")]
        if scaricabili:
            kb = [[InlineKeyboardButton(
                f"⬇️ Scarica {r.get('codice','')}", callback_data=f"ric:dl:{i}")]
                  for i, r in scaricabili]
            kb.append([InlineKeyboardButton("❌ Chiudi", callback_data="ric:done")])
            await update.message.reply_text(
                "🗂 Puoi scaricare le ricette prenotate:",
                reply_markup=InlineKeyboardMarkup(kb))

    async def _h_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        if self._risveglia_se_needed(update):
            return
        if self.controller:
            text = self.controller.status_text()
        else:
            text = "Controller non inizializzato."
        await update.message.reply_text(text)

    async def _h_poll(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        if self._risveglia_se_needed(update):
            return
        await update.message.reply_text("Avvio polling forzato...")
        if self.controller:
            self.controller.force_poll()

    async def _h_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        if self._risveglia_se_needed(update):
            return
        await update.message.reply_text(HELP_TEXT)

    async def _h_echo_chat(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Gestisce i messaggi testuali generici (info chat)."""
        if not self._autorizzato(update):
            return
        chat = update.effective_chat
        await update.message.reply_text(
            f"🤖 Bot attivo!\nChat ID: `{chat.id}`\nTipo: {chat.type}"
            "\nUsa /help per i comandi.")

    async def _scarica_ricetta(self, update, ricetta):
        """Avvia il download della ricetta (nel browser del bot)."""
        q = update.callback_query
        link = (ricetta.get("link_download") or "")
        if not link:
            await q.answer("Nessun link download")
            return
        # estrai l'URL dalla javascript:addCurrentPage('URL')
        import re as _re
        m = _re.search(r"'([^']+)'", link)
        url = m.group(1) if m else link
        url = url.replace("&amp;", "&")
        try:
            driver = self.controller.browser.start()
            await q.edit_message_text("⬇️ Genero il file della ricetta...")
            driver.get(url)
            # attende e verifica che il download sia partito (il file va nella dir download)
            import time as _time
            _time.sleep(6)
            await q.edit_message_text("✅ Download della ricetta avviato.\n"
                                      f"📄 {self._esc(ricetta.get('codice',''))} · "
                                      f"{self._esc(', '.join(ricetta.get('prestazioni', [])[:1]))}")
        except Exception as e:  # noqa: BLE001
            await q.answer(f"Errore download: {e}")

    # ---------- run ----------
    def run(self) -> None:
        """Avvia il polling del bot (blocca; va in un processo/thread separato)."""
        from telegram import BotCommand

        async def _post_init(application: Application) -> None:
            commands = [
                BotCommand("start", "Panoramica e benvenuto"),
                BotCommand("monitora", "Crea un nuovo monitor (wizard guidato)"),
                BotCommand("status", "Stato dei monitor e sessione SPID"),
                BotCommand("poll", "Forza controllo disponibilità adesso"),
                BotCommand("ricette", "Elenco delle ricette dematerializzate"),
                BotCommand("appuntamenti", "Elenco degli appuntamenti già presi"),
                BotCommand("stop", "Interrompi un monitor attivo"),
                BotCommand("help", "Guida ai comandi"),
            ]
            try:
                await application.bot.set_my_commands(commands)
                log.info("Comandi del bot registrati automaticamente su Telegram")
            except Exception as e:  # noqa: BLE001
                log.warning("Impossibile registrare i comandi su Telegram: %s", e)

        self.app = Application.builder().token(self.token).post_init(_post_init).build()
        self.app.add_handler(CommandHandler("start", self._h_start))
        self.app.add_handler(CommandHandler("status", self._h_status))
        self.app.add_handler(CommandHandler("ricette", self._h_ricette))
        self.app.add_handler(CommandHandler("appuntamenti", self._h_appuntamenti))
        self.app.add_handler(CommandHandler("poll", self._h_poll))
        self.app.add_handler(CommandHandler("monitora", self._h_monitora))
        self.app.add_handler(CommandHandler("stop", self._h_stop))
        self.app.add_handler(CommandHandler("help", self._h_help))
        # pulsanti inline (wizard /monitora e risposte)
        self.app.add_handler(CallbackQueryHandler(self._h_callback))
        # qualsiasi messaggio non-comando -> info chat (per setup / comandi)
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._h_echo_chat))
        log.info("Bot Telegram avviato")
        self.app.run_polling()
