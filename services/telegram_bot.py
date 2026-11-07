"""Telegram Bot: notifications and booking confirmation flow.

Basic commands:
  /status   -> status of monitors and session
  /poll     -> force polling now
  /relogin  -> request manual re-login in terminal
  /approve <id> / /deny <id> -> approve/deny a pending request
"""
from __future__ import annotations

import asyncio
import logging
import threading

import httpx
import telegram.error
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from telegram.request import HTTPXRequest

log = logging.getLogger(__name__)

HELP_TEXT = (
    "🤖 Comandi disponibili:\n"
    "\n🔎 <b>Operazioni</b>\n"
    "/start - scegli su cosa lavorare\n"
    "/monitora - crea un monitor (con wizard guidato: scelta, province, date)\n"
    "/blacklist - consulta ed elimina disponibilità rifiutate per monitor attivo\n"
    "/stop - lista dei monitor e scelta di quello da fermare\n"
    "/status - stato monitor e sessione\n"
    "/poll - forza il controllo disponibilità\n"
    "/anticipa - cerca date precedenti per un appuntamento già prenotato\n"
    "/screenshot - cattura e invia screenshot attuale del portale\n"
    "/pause - metti in pausa o riprendi la ricerca (/resume per riprendere)\n"
    "\n📋 <b>Informazioni</b>\n"
    "/ricette - elenca le ricette da prenotare\n"
    "/appuntamenti - elenca gli appuntamenti esistenti e genera eventi calendario (.ics)\n"
    "/version - versione corrente e verifica aggiornamenti su GitHub\n"
    "/bug - segnala un problema o malfunzionamento\n"
    "/dona - supporta il progetto offrendo un caffè allo sviluppatore ☕\n"
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
        self.controller = None  # connected by main for /poll /status /relogin
        # monitor wizard state per chat
        self._wizard = {}  # chat_id -> snapshot for /monitora wizard
        self._appuntamenti_cache = {}  # chat_id -> list of appointment dicts from /appuntamenti
        # authentication interaction state (push fallback / OTP prompt)
        self._auth_event = None
        self._auth_choice = None
        self._otp_event = None
        self._otp_value = None
        self._pending_otp = False
        self._received_otp = None

    # ---------- Application builder & HTTP client resilience ----------
    def _create_application_builder(self):
        """Creates an ApplicationBuilder configured with hardened timeouts and connection pool."""
        req = HTTPXRequest(
            connection_pool_size=16,
            connect_timeout=15.0,
            read_timeout=20.0,
            write_timeout=20.0,
            pool_timeout=10.0,
        )
        builder = Application.builder().token(self.token)
        try:
            from unittest.mock import Mock
            if isinstance(builder, Mock):
                builder.request.return_value = builder
        except ImportError:
            pass
        return builder.request(req)

    async def _safe_edit_message_text(self, q, *args, max_retries: int = 3, retry_delay: float = 0.5, **kwargs):
        """Safely edit callback query message text with retries on transient network errors.

        Handles BadRequest('Message is not modified') silently and prevents leaking raw
        httpx.ConnectError / NetworkError strings to the chat.
        """
        for attempt in range(1, max_retries + 1):
            try:
                return await q.edit_message_text(*args, **kwargs)
            except telegram.error.BadRequest as e:
                if "Message is not modified" in str(e):
                    return None
                log.warning("Impossibile modificare il messaggio Telegram (BadRequest): %s", e)
                return None
            except (telegram.error.NetworkError, telegram.error.TimedOut, httpx.HTTPError, ConnectionError, OSError) as e:
                if attempt < max_retries:
                    log.warning(
                        "Errore di rete Telegram (%s) durante modifica messaggio (tentativo %d/%d). Riprovo...",
                        e, attempt, max_retries
                    )
                    await asyncio.sleep(retry_delay * attempt)
                else:
                    log.error(
                        "Errore di rete persistente durante modifica messaggio Telegram dopo %d tentativi: %s",
                        max_retries, e
                    )
                    return None
            except Exception as e:
                log.exception("Errore imprevisto durante modifica messaggio Telegram: %s", e)
                raise

    # ---------- sending (used by poller/flows) ----------
    async def _send(self, text: str) -> None:
        if not (self.token and self.chat_ids):
            log.warning("TG non configurato, notifica non inviata: %s", text)
            return
        app = self._create_application_builder().build()
        async with app:
            for cid in self.chat_ids:
                for attempt in range(1, 4):
                    try:
                        await app.bot.send_message(chat_id=cid, text=text,
                                                   parse_mode=ParseMode.HTML)
                        break
                    except (telegram.error.NetworkError, telegram.error.TimedOut, httpx.HTTPError, ConnectionError, OSError) as e:
                        if attempt < 3:
                            await asyncio.sleep(0.5 * attempt)
                        else:
                            log.error("invio TG a %s fallito per errore di rete: %s", cid, e)
                    except Exception as e:  # noqa: BLE001
                        log.error("invio TG a %s fallito: %s", cid, e)
                        break

    def notify(self, text: str) -> None:
        """Synchronous/asynchronous callback from flows/poller or handlers (handles active event loops)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            asyncio.create_task(self._send(text))
            return

        try:
            asyncio.run(self._send(text))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._send(text))
            finally:
                loop.close()
        except Exception as e:  # noqa: BLE001
            log.error("notify TG fallita: %s", e)

    # ---------- sending with inline buttons (from flows/poller) ----------
    async def _send_buttons(self, text: str, buttons: list) -> list:
        """Send text + inline buttons; returns [(chat_id, message_id), ...] so caller can edit message later (e.g. expiration)."""
        if not (self.token and self.chat_ids):
            log.warning("TG non configurato, notifica con pulsanti non inviata")
            return []
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(lbl, url=cb) if str(cb).startswith(("http://", "https://", "cieid://"))
            else InlineKeyboardButton(lbl, callback_data=cb) for lbl, cb in row
        ] for row in buttons])
        sent = []
        app = self._create_application_builder().build()
        async with app:
            for cid in self.chat_ids:
                for attempt in range(1, 4):
                    try:
                        m = await app.bot.send_message(chat_id=cid, text=text,
                                                       reply_markup=kb, parse_mode=ParseMode.HTML)
                        sent.append((cid, m.message_id))
                        break
                    except (telegram.error.NetworkError, telegram.error.TimedOut, httpx.HTTPError, ConnectionError, OSError) as e:
                        if attempt < 3:
                            await asyncio.sleep(0.5 * attempt)
                        else:
                            log.error("invio TG con pulsanti a %s fallito per errore di rete: %s", cid, e)
                    except Exception as e:  # noqa: BLE001
                        log.error("invio TG con pulsanti a %s fallito: %s", cid, e)
                        break
        return sent

    def notify_buttons(self, text: str, buttons: list) -> list:
        """Send message with inline buttons (synchronous).

        Returns list [(chat_id, message_id)] of sent messages
        (for subsequent edits, e.g. marking proposal expired).
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

    # ---------- edit sent messages (e.g. proposal expiration) ----------
    async def _edit_message(self, text: str, destinations: list) -> None:
        """Replace text (and remove buttons) of sent messages."""
        if not (self.token and destinations):
            return
        app = self._create_application_builder().build()
        async with app:
            for cid, mid in destinations:
                for attempt in range(1, 4):
                    try:
                        await app.bot.edit_message_text(
                            chat_id=cid, message_id=mid, text=text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup([]))
                        break
                    except (telegram.error.NetworkError, telegram.error.TimedOut, httpx.HTTPError, ConnectionError, OSError) as e:
                        if attempt < 3:
                            await asyncio.sleep(0.5 * attempt)
                        else:
                            log.error("edit TG %s/%s fallito per errore di rete: %s", cid, mid, e)
                    except Exception as e:  # noqa: BLE001
                        log.error("edit TG %s/%s fallito: %s", cid, mid, e)
                        break

    def edit_message(self, text: str, destinations: list) -> None:
        """Synchronous: edit messages (used by flows/poller)."""
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

    # ---------- sending files (e.g. .ics) ----------
    async def _send_file(self, path: str, caption: str = "") -> None:
        if not (self.token and self.chat_ids):
            log.warning("TG non configurato, file non inviato: %s", path)
            return
        app = self._create_application_builder().build()
        async with app:
            for cid in self.chat_ids:
                try:
                    with open(path, "rb") as f:
                        await app.bot.send_document(chat_id=cid, document=f, filename=path.split("/")[-1], caption=caption, parse_mode=ParseMode.HTML)
                except Exception as e:  # noqa: BLE001
                    log.error("invio file TG a %s fallito: %s", cid, e)

    def send_file(self, path: str, caption: str = "") -> None:
        """Send a document (e.g. .ics event) from a flow or poller."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            asyncio.create_task(self._send_file(path, caption))
            return

        try:
            asyncio.run(self._send_file(path, caption))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._send_file(path, caption))
            finally:
                loop.close()
        except Exception as e:  # noqa: BLE001
            log.error("send_file TG fallita: %s", e)

    # ---------- sending images / screenshots ----------
    async def _send_photo(self, path: str, caption: str = "", buttons: list | None = None) -> None:
        if not (self.token and self.chat_ids):
            log.warning("TG non configurato, foto non inviata: %s", path)
            return
        kb = None
        if buttons:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(lbl, url=cb) if str(cb).startswith(("http://", "https://", "cieid://"))
                else InlineKeyboardButton(lbl, callback_data=cb) for lbl, cb in row
            ] for row in buttons])
        app = self._create_application_builder().build()
        async with app:
            for cid in self.chat_ids:
                try:
                    with open(path, "rb") as f:
                        await app.bot.send_photo(chat_id=cid, photo=f, caption=caption,
                                                 parse_mode=ParseMode.HTML, reply_markup=kb)
                except Exception as e:  # noqa: BLE001
                    log.warning("invio photo TG a %s fallito (%s), provo send_document", cid, e)
                    try:
                        with open(path, "rb") as f:
                            await app.bot.send_document(chat_id=cid, document=f,
                                                        filename=path.split("/")[-1],
                                                        caption=caption, parse_mode=ParseMode.HTML,
                                                        reply_markup=kb)
                    except Exception as e2:  # noqa: BLE001
                        log.error("invio fallback file TG a %s fallito: %s", cid, e2)

    def send_photo(self, path: str, caption: str = "", buttons: list | None = None) -> None:
        """Send an image (e.g. PNG screenshot) from a flow or poller."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            asyncio.create_task(self._send_photo(path, caption, buttons=buttons))
            return

        try:
            asyncio.run(self._send_photo(path, caption, buttons=buttons))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._send_photo(path, caption, buttons=buttons))
            finally:
                loop.close()
        except Exception as e:  # noqa: BLE001
            log.error("send_photo TG fallita: %s", e)

    # ---------- authentication interactions (push fallback / otp / qr) ----------
    def ask_auth_fallback(self, prompt: str = "", timeout_s: int = 120, buttons: list | None = None) -> str | None:
        """Prompt user via Telegram inline buttons to resend push notification, display QR or enter OTP.

        Returns:
          - 'notify': user chose 'Invia notifica'
          - 'qr': user chose 'Invia QR Code'
          - 'otp': user chose 'Immetti codice OTP'
          - 'cancel' or None: cancelled or timed out
        """
        # If an OTP code was already received in chat in advance, consider it 'otp'
        if self._received_otp:
            return "otp"

        if not (self.token and self.chat_ids):
            log.warning("Telegram non configurato per ask_auth_fallback")
            return None

        # Reset state
        self._auth_choice = None
        self._auth_event = threading.Event()

        testo = (prompt or
                 "⚠️ <b>Accesso SPID SielteID</b>\n"
                 "La notifica push non è stata approvata o non è arrivata sul telefono.\n\n"
                 "Come vuoi procedere?")
        bottoni = buttons or [
            [("📲 Invia notifica", "auth:notify"), ("🔢 Immetti codice OTP", "auth:otp")],
            [("❌ Annulla login", "auth:cancel")],
        ]
        inviati = self.notify_buttons(testo, bottoni)

        ok = self._auth_event.wait(timeout_s)
        scelta = self._auth_choice
        self._auth_event = None

        if not ok:
            log.warning("ask_auth_fallback: timeout dopo %ds", timeout_s)
            if inviati:
                self.edit_message(f"{testo}\n\n⏳ <i>Tempo scaduto per la selezione.</i>", inviati)
            return None

        return scelta

    def ask_otp(self, prompt: str = "", timeout_s: int = 120) -> str | None:
        """Prompt user on Telegram to type their OTP code.

        Returns the OTP code string (cleaned of spaces/dashes) or None.
        """
        # If an OTP was already received in chat during fallback
        if self._received_otp:
            code = self._received_otp
            self._received_otp = None
            return code

        if not (self.token and self.chat_ids):
            log.warning("Telegram non configurato per ask_otp")
            return None

        self._otp_value = None
        self._otp_event = threading.Event()
        self._pending_otp = True

        testo = (prompt or
                 "🔢 <b>Immetti codice OTP</b>\n"
                 "Inserisci qui in chat il codice OTP generato dall'app MySielteID (o ricevuto via SMS):")
        bottoni = [[("❌ Annulla", "auth:cancel_otp")]]
        inviati = self.notify_buttons(testo, bottoni)

        ok = self._otp_event.wait(timeout_s)
        self._pending_otp = False
        code = self._otp_value
        self._otp_event = None

        if not ok:
            log.warning("ask_otp: timeout attesa codice dopo %ds", timeout_s)
            if inviati:
                self.edit_message(f"{testo}\n\n⏳ <i>Tempo scaduto per l'inserimento dell'OTP.</i>", inviati)
            return None

        return code

    # ---------- authorization helper ----------
    def _autorizzato(self, update: Update) -> bool:
        """Only authorized chat_ids (from config) can use the bot."""
        chat = update.effective_chat
        if chat is None:
            return False
        # Robust comparison: accept both int and str (chat.id is int, config is str)
        return str(chat.id) in {str(c) for c in self.chat_ids}

    @staticmethod
    def _esc(s: str) -> str:
        """HTML escape for dynamic texts (for parse_mode=HTML)."""
        return (str(s or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))

    def _risveglia_se_needed(self, update=None) -> bool:
        """If login is blocked, any command unblocks it and retries.

        Returns True if unblocked (the handler notifies and starts login).
        """
        if getattr(self, "controller", None) is None:
            return False
        if getattr(self.controller, "login_bloccato", False):
            import threading
            self.controller.login_bloccato = False
            self.controller.login_retries = 0
            auth_desc = getattr(self.controller.auth_config, "describe", lambda: "Autenticazione")() if hasattr(self.controller, "auth_config") else "Autenticazione"
            self.notify(f"🔄 Tentativi di login sbloccati! Avvio un nuovo tentativo di accesso ({auth_desc})... controlla il dispositivo per approvare!")
            threading.Thread(target=self.controller.risveglia, daemon=True).start()
            return True
        return False

    # ---------- handlers ----------
    async def _h_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            await update.message.reply_text("Bot non disponibile.")
            return
        self._risveglia_se_needed(update)
        from services.updater import APP_NAME, APP_VERSION, GITHUB_URL
        await update.message.reply_text(
            f"👋 <b>Benvenuto su {APP_NAME}!</b> (<code>v{APP_VERSION}</code>)\n"
            f"🔗 <a href=\"{GITHUB_URL}\">Progetto su GitHub</a>\n\n"
            "Scegli su cosa vuoi lavorare:\n"
            "• /ricette — su una RICETTA da prenotare (nuove disponibilità)\n"
            "• /appuntamenti — su un APPUNTAMENTO già preso (anticipa/posticipa)\n\n"
            "Oppure usa /version per verificare gli aggiornamenti o /help per l'elenco completo.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def _h_version(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show current version and check GitHub for latest releases."""
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        from services.updater import check_for_updates, format_telegram_welcome
        cfg = getattr(self.controller, "cfg", None) if hasattr(self, "controller") else None
        info = check_for_updates(force=True, cfg=cfg)
        testo, bottoni = format_telegram_welcome(info)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(lbl, url=cb) if str(cb).startswith(("http://", "https://"))
            else InlineKeyboardButton(lbl, callback_data=cb) for lbl, cb in row
        ] for row in bottoni])
        await update.message.reply_text(
            testo,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def _h_bug(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Guide user through opening a bug report on GitHub with pre-filled guidelines."""
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        from services.updater import format_issue_guide
        testo, bottoni = format_issue_guide()
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(lbl, url=cb) if str(cb).startswith(("http://", "https://"))
            else InlineKeyboardButton(lbl, callback_data=cb) for lbl, cb in row
        ] for row in bottoni])
        await update.message.reply_text(
            testo,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    async def _h_dona(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show donation and support information with interactive tier buttons."""
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        from services.donation import get_donation_config, build_donation_message, build_donation_buttons
        settings = getattr(self.controller, "settings", {}) if hasattr(self, "controller") else {}
        cfg = get_donation_config(settings)
        if not cfg["enabled"]:
            await update.message.reply_text(
                "ℹ️ <b>Nessun link di donazione o supporto configurato.</b>\n\n"
                "VisitaSubito è un progetto 100% open source e gratuito. Per abilitare le donazioni, "
                "configura la variabile <code>DONATION_URL</code> nel file <code>.env</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        testo = build_donation_message()
        bottoni = build_donation_buttons(cfg)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(lbl, url=cb) if str(cb).startswith(("http://", "https://"))
            else InlineKeyboardButton(lbl, callback_data=cb) for lbl, cb in row
        ] for row in bottoni])
        await update.message.reply_text(
            testo,
            reply_markup=kb,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

    # ---------- inline calendar ----------
    @staticmethod
    def _cal_keyboard(anno, mese, sel=None):
        """Build an inline mini-calendar (day grid + month arrows).

        Callbacks: cal:prev, cal:next, cal:day:YYYY-MM-DD
        """
        import calendar as _cal
        from datetime import date
        bottoni = []
        # Month/year header with navigation arrows
        bottoni.append([
            InlineKeyboardButton("‹", callback_data="cal:prev"),
            InlineKeyboardButton(f"{_cal.month_name[mese]} {anno}",
                                 callback_data="cal:noop"),
            InlineKeyboardButton("›", callback_data="cal:next"),
        ])
        # Days of week
        sett = ["Lu", "Ma", "Me", "Gi", "Ve", "Sa", "Do"]
        bottoni.append([InlineKeyboardButton(g, callback_data="cal:noop") for g in sett])
        # Day grid
        prima, num = _cal.monthrange(anno, mese)
        # Monday as first day (calendar.MONDAY=0 -> Monday index 0)
        riga = []
        # Offset: python calendar.monthrange gives Monday=0 to Sunday=6
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
        """Return message + calendar markup (state with current dates)."""
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
        """Handle calendar callbacks (prev/next/day)."""
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
            # save in wizard and proceed
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
        """After selecting a day in calendar, advance flow based on current step."""
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
            await self._chiedi_fascia(update, chat_id, wiz)

    async def _chiedi_fascia(self, update, chat_id, wiz):
        """Ask user to select preferred time slot (fascia oraria)."""
        wiz["step"] = "fascia"
        self._wizard[chat_id] = wiz
        kb = [
            [InlineKeyboardButton("☀️ Solo Mattina (07:00 - 13:00)", callback_data="mon:fascia:mattina")],
            [InlineKeyboardButton("🌤 Solo Pomeriggio (13:00 - 19:00)", callback_data="mon:fascia:pomeriggio")],
            [InlineKeyboardButton("🌙 Tardo Pom. / Sera (17:00 - 23:59)", callback_data="mon:fascia:sera")],
            [InlineKeyboardButton("🥪 Pausa Pranzo (12:00 - 15:00)", callback_data="mon:fascia:pranzo")],
            [InlineKeyboardButton("⏰ Qualsiasi orario (Salta)", callback_data="mon:fascia:qualsiasi")],
            [InlineKeyboardButton("❌ Annulla", callback_data="mon:annulla")],
        ]
        testo = (
            "⏰ <b>Preferenza fascia oraria:</b>\n"
            "Scegli la fascia oraria desiderata per le visite (oppure 'Qualsiasi orario' per accettare tutti gli orari):"
        )
        q = update.callback_query
        try:
            await q.edit_message_text(testo, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        except Exception as e:  # noqa: BLE001
            log.warning("chiedi_fascia: %s", e)

    async def _crea_monitor_finale(self, update, chat_id, wiz):
        """Create monitor with date range, time slot preference, and confirmation."""
        q = update.callback_query
        target = wiz.get("target", {})
        tipo = wiz.get("tipo", "new")
        prov_list = wiz.get("province", [])
        data_dal = wiz.get("data_dal", "")
        data_a = wiz.get("data_a", "")
        fascia = wiz.get("fascia", "")
        # Prescription targets have 'prestazioni' (list) and 'codice' (NRE);
        # appointments have 'prestazione' (string). Service name first, then other fields, then fallback.
        prestazioni = target.get("prestazioni") or []
        if prestazioni:
            desc = ", ".join(p for p in prestazioni if p)
        else:
            desc = target.get("prestazione") or target.get("descrizione") or ""
        nre = target.get("codice") or ""
        if not desc and not nre:
            # Avoid placeholder 'monitor': without service name (prescription) and
            # without NRE the flow would not know which card to open
            self._wizard.pop(chat_id, None)
            if q:
                await q.edit_message_text(
                    "⚠️ Non riesco a ricavare la prestazione da monitorare dall'elenco. "
                    "Riprova con /monitora e scegli la ricetta dall'elenco.")
            return
        was_paused = bool(self.controller and getattr(self.controller, "paused", False) is True)
        data_ricetta = target.get("data_ricetta") or ""
        data_attuale = target.get("data_ora") or ""
        if tipo == "reschedule" and data_attuale:
            from services.deadline import check_reschedule_deadline, get_reschedule_min_working_days
            min_days = get_reschedule_min_working_days()
            is_too_late, app_dt, dl_dt, dl_msg = check_reschedule_deadline(data_attuale, min_working_days=min_days)
            if is_too_late:
                self._wizard.pop(chat_id, None)
                q = update.callback_query
                dl_fmt = dl_dt.strftime('%d/%m/%Y alle %H:%M') if dl_dt else "scaduto"
                await q.edit_message_text(
                    f"⚠️ <b>Impossibile avviare il monitor di anticipo</b>\n\n"
                    f"📌 <b>Appuntamento attuale</b>: {self._esc(data_attuale)}\n"
                    f"⏳ <b>Termine ultimo utile</b>: {self._esc(dl_fmt)}\n\n"
                    f"Mancano meno di {min_days} giorni lavorativi (esclusi sab, dom e festivi).\n"
                    f"La modifica o disdetta comporterebbe la penale (malus ticket).",
                    parse_mode=ParseMode.HTML,
                )
                return
        criteri = {
            "province": prov_list,
            "data_dal": data_dal,
            "data_a": data_a,
            "fascia": fascia,
        }
        mid = self.controller.aggiungi_monitor(
            tipo, desc, nre=nre,
            criteri=criteri,
            data_ricetta=data_ricetta,
            data_attuale=data_attuale)
        self._wizard.pop(chat_id, None)
        if mid:
            prov_txt = ", ".join(prov_list) if prov_list else "default"
            nre_txt = f"\n🔖 NRE: {self._esc(nre)}" if nre else ""
            fascia_txt = ""
            if fascia:
                from services.matcher import descrivi_fascia
                fascia_txt = f"\n⏰ Fascia: {self._esc(descrivi_fascia(fascia))}"
            txt = (
                f"✅ Monitor creato!\n🎯 {self._esc(desc)}{nre_txt}\n🏙 {self._esc(prov_txt)}\n"
                f"📅 Range: {self._esc(data_dal)} → {self._esc(data_a)}{fascia_txt}\n"
                f"🆔 {self._esc(mid)}\n\n"
                f"Ti notificherò solo se trovo disponibilità nel range."
            )
            if was_paused:
                txt += "\n\n▶️ La pausa del monitoraggio è stata resettata: la ricerca automatica è ora attiva."
            if q:
                await q.edit_message_text(txt)
        else:
            if q:
                await q.edit_message_text("Errore creazione monitor.")

    async def _h_monitora(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
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
        """Handle button clicks (monitoring wizard)."""
        if not self._autorizzato(update):
            await update.answer("Non autorizzato")
            return
        q = update.callback_query
        data = q.data or ""
        chat_id = update.effective_chat.id
        try:
            # SPID/OTP authentication interactions
            if data.startswith("auth:"):
                parti = data.split(":", 1)
                sub = parti[1]
                if sub == "notify":
                    if self._auth_event is not None:
                        self._auth_choice = "notify"
                        self._auth_event.set()
                    await q.edit_message_text(
                        "📲 <b>Scelto: Invia notifica</b>\nInvio di una nuova notifica push in corso...",
                        parse_mode=ParseMode.HTML)
                elif sub == "otp":
                    if self._auth_event is not None:
                        self._auth_choice = "otp"
                        self._auth_event.set()
                    await q.edit_message_text(
                        "🔢 <b>Scelto: Immetti codice OTP</b>\nPrepara il codice dell'app MySielteID...",
                        parse_mode=ParseMode.HTML)
                elif sub == "qr":
                    if self._auth_event is not None:
                        self._auth_choice = "qr"
                        self._auth_event.set()
                    await q.edit_message_text(
                        "📲 <b>Scelto: Invia QR Code</b>\nRecupero del QR Code in corso, te lo invio subito qui in chat...",
                        parse_mode=ParseMode.HTML)
                elif sub == "cancel":
                    if self._auth_event is not None:
                        self._auth_choice = "cancel"
                        self._auth_event.set()
                    await q.edit_message_text("❌ <b>Login annullato.</b>", parse_mode=ParseMode.HTML)
                elif sub == "cancel_otp":
                    if self._otp_event is not None:
                        self._otp_value = None
                        self._pending_otp = False
                        self._otp_event.set()
                    await q.edit_message_text("❌ <b>Inserimento OTP annullato.</b>", parse_mode=ParseMode.HTML)
                return

            # Screenshot on demand via button
            if data in ("screenshot", "action:screenshot"):
                await q.answer("📸 Acquisizione screenshot...")
                await self._invia_screenshot(update, ctx)
                return

            # Generate calendar event (.ics) from /appuntamenti
            if data.startswith("app:ics:"):
                await self._h_genera_ics_callback(update, ctx, data)
                return

            # Download prescription from /ricette command
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
            # Decision on proposal (approve/deny/reschedule via buttons)
            if data.startswith("decide:"):
                parti = data.split(":")
                azione = parti[1]
                req_id = ":".join(parti[2:])
                approved = azione in ("approve", "anticipa", "posticipa")
                if self.controller:
                    self.controller.decide_request(req_id, approved, extra={"azione": azione})
                    try:
                        await q.answer()
                    except Exception as e_ans:
                        log.debug("q.answer non riuscita: %s", e_ans)
                    msg_text = {
                        "approve": "✅ Approvato: procedo con la prenotazione.",
                        "deny": "❌ Richiesta rifiutata. La disponibilità è stata aggiunta alla blacklist (puoi gestirla con /blacklist).",
                        "deny_day": "🚫 Giorno rifiutato. L'intera giornata è stata aggiunta alla blacklist (puoi gestirla con /blacklist).",
                        "anticipa": "↩️ Anticipo dell'appuntamento in corso...",
                        "posticipa": "↪️ Posticipo dell'appuntamento in corso...",
                    }.get(azione, "Azione registrata.")
                    await self._safe_edit_message_text(q, msg_text)
                else:
                    await q.answer("Controller non inizializzato")
                return
            # Post-booking choice: stop or continue (converts to reschedule)
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
                        pren = self.controller.store.get_prenotazione(mid) if hasattr(self.controller, "store") else {}
                        data_ora = (pren.get("data_ora") if isinstance(pren, dict) else "") or "la data prenotata"
                        await q.edit_message_text(
                            f"🔄 <b>Monitoraggio aggiornato: ricerca anticipo attiva!</b>\n\n"
                            f"📌 Appuntamento attuale: <b>{self._esc(data_ora)}</b>\n"
                            f"Il bot continuerà a verificare automaticamente per provare ad anticipare la visita rispetto alla data prenotata.",
                            parse_mode=ParseMode.HTML,
                        )
                    else:
                        await q.edit_message_text(f"⚠️ Impossibile aggiornare il monitor {self._esc(mid)}: nessuna prenotazione valida registrata.")
                    return
            # Select monitor to reschedule earlier
            if data.startswith("anticipa:pick:"):
                mid = data.split(":", 2)[2]
                if not self.controller:
                    await q.answer("Controller non inizializzato.")
                    return
                ok = self.controller.converti_in_reschedule(mid)
                if ok:
                    pren = self.controller.store.get_prenotazione(mid) if hasattr(self.controller, "store") else {}
                    d_str = pren.get("data_ora") if isinstance(pren, dict) else ""
                    data_info = f"\n📌 Appuntamento attuale: <b>{self._esc(d_str)}</b>" if d_str else ""
                    await q.edit_message_text(
                        f"🔄 <b>Ricerca anticipo attivata per {self._esc(mid)}!</b>{data_info}\n\n"
                        f"Il bot continuerà a verificare automaticamente per provare ad anticipare la visita.",
                        parse_mode=ParseMode.HTML,
                    )
                    self.controller.force_poll()
                else:
                    await q.edit_message_text(f"⚠️ Impossibile aggiornare il monitor {self._esc(mid)}: nessuna prenotazione valida registrata.")
                return
            # Inline calendar (date selection)
            if (data or "").startswith("cal:"):
                await self._h_cal(update, chat_id)
                return
            # Stop monitor from list
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
            # Blacklist management (bl:...)
            if data.startswith("bl:"):
                parti = data.split(":")
                azione_bl = parti[1]

                if azione_bl == "list":
                    await self._mostra_scelta_monitor_blacklist(q, is_edit=True)
                    return

                elif azione_bl == "close":
                    await q.edit_message_text("Operazione blacklist terminata 👍")
                    return

                elif azione_bl == "pick":
                    mid = parti[2]
                    await self._mostra_dettaglio_blacklist(q, mid)
                    return

                elif azione_bl == "del":
                    mid = parti[2]
                    entry_id = parti[3]
                    if self.controller:
                        removed = self.controller.rimuovi_da_blacklist(mid, entry_id)
                        if removed:
                            d_t = f"{removed.get('date_str','')} {removed.get('time_str','')}".strip()
                            await q.answer(f"✅ Rimosso {d_t} dalla blacklist!")
                        else:
                            await q.answer("Voce non trovata o già rimossa.")
                    else:
                        await q.answer("Controller non inizializzato.")
                    await self._mostra_dettaglio_blacklist(q, mid)
                    return

                elif azione_bl == "clear_ask":
                    mid = parti[2]
                    if not self.controller:
                        await q.answer("Controller non inizializzato.")
                        return
                    mon = next((m for m in self.controller.store.get_monitors() if m.get("id") == mid), None)
                    if mon is None:
                        mon = next((m for m in self.controller.cfg.active_monitors if m.get("id") == mid), None)
                    nome = self.controller._nome_leggibile(mon or {"id": mid})
                    entries = self.controller.get_blacklist(mid)
                    testo = (
                        f"⚠️ <b>Conferma cancellazione blacklist</b>\n\n"
                        f"Sei sicuro di voler cancellare <b>tutte le {len(entries)} disponibilità</b> "
                        f"rifiutate per <b>{self._esc(nome)}</b>?\n\n"
                        f"<i>Tutte le date rifiutate potranno essere nuovamente valutate e proposte dal bot.</i>"
                    )
                    kb = [
                        [InlineKeyboardButton("✅ Sì, cancella tutto", callback_data=f"bl:clear_do:{mid}")],
                        [InlineKeyboardButton("❌ Annulla", callback_data=f"bl:pick:{mid}")],
                    ]
                    await q.edit_message_text(testo, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
                    return

                elif azione_bl == "clear_do":
                    mid = parti[2]
                    if self.controller:
                        count = self.controller.svuota_blacklist(mid)
                        mon = next((m for m in self.controller.store.get_monitors() if m.get("id") == mid), None)
                        if mon is None:
                            mon = next((m for m in self.controller.cfg.active_monitors if m.get("id") == mid), None)
                        nome = self.controller._nome_leggibile(mon or {"id": mid})
                        testo = (
                            f"✅ <b>Blacklist svuotata!</b>\n\n"
                            f"Rimosse <b>{count} disponibilità</b> dalla blacklist per <b>{self._esc(nome)}</b>.\n"
                            f"Tutte le date potranno essere nuovamente considerate durante i prossimi controlli."
                        )
                        kb = [
                            [InlineKeyboardButton("🔙 Torna ai monitor", callback_data="bl:list")],
                            [InlineKeyboardButton("❌ Chiudi", callback_data="bl:close")],
                        ]
                        await q.edit_message_text(testo, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
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
                # Multiple selection toggle
                sel = wiz.setdefault("province", [])
                if prov in sel:
                    sel.remove(prov)
                else:
                    sel.append(prov)
                wiz["step"] = "provincia"
                self._wizard[chat_id] = wiz
                await self._chiedi_provincia(update, ctx, chat_id)
                # Confirm click (small feedback), does not alter text other than toggle
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
            elif data.startswith("mon:fascia:"):
                fascia_val = data.split(":", 2)[2]
                wiz = self._wizard.get(chat_id, {})
                wiz["fascia"] = "" if fascia_val == "qualsiasi" else fascia_val
                self._wizard[chat_id] = wiz
                await self._crea_monitor_finale(update, chat_id, wiz)
            elif data == "mon:annulla":
                self._wizard.pop(chat_id, None)
                await q.edit_message_text("Monitoraggio annullato.")
            else:
                await q.answer("Scelta non riconosciuta")
        except (telegram.error.NetworkError, telegram.error.TimedOut, httpx.HTTPError, ConnectionError, OSError) as net_err:
            log.warning("Errore di rete Telegram durante gestione callback %s: %s", data, net_err)
        except Exception as e:  # noqa: BLE001
            log.exception("Errore non gestito durante callback %s: %s", data, e)
            try:
                await self._safe_edit_message_text(q, f"⚠️ Si è verificato un errore: {e}")
            except Exception:  # noqa: BLE001
                pass

    async def _chiedi_provincia(self, update, ctx, chat_id):
        """Show MULTIPLE province selection (toggle with click).
        The message updates on each click; then 'Conferma'.
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
        """After provinces: show CALENDAR to pick 'From' date."""
        import datetime as _dt
        wiz["step"] = "range_da"
        self._wizard[chat_id] = wiz
        now = _dt.date.today()
        testo, kb = self._mostra_calendario(chat_id, now.year, now.month)
        await update.callback_query.edit_message_text(
            "📅 Scegli la <b>data da cui</b> cercare:",
            reply_markup=kb, parse_mode=ParseMode.HTML)

    async def _gestisci_range_da(self, update, chat_id, testo):
        """(Deprecated: date range via calendar)"""
        pass

    async def _gestisci_range_a(self, update, chat_id, testo):
        """(Deprecated: date range via calendar)"""
        pass

    async def _h_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        if self.controller is None:
            await update.message.reply_text("Controller non inizializzato.")
            return
        # With explicit ID: remove immediately
        if ctx.args:
            mid = ctx.args[0]
            if self.controller.rimuovi_monitor(mid):
                await update.message.reply_text(f"🛑 Monitor {mid} interrotto.")
            else:
                await update.message.reply_text(f"Monitor {mid} non trovato.")
            return
        # Without arguments: show list with buttons
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

    async def _mostra_scelta_monitor_blacklist(self, update_or_query, is_edit: bool = False) -> None:
        """Show active monitors list to choose which blacklist to inspect."""
        if not self.controller:
            text = "Controller non inizializzato."
            if is_edit:
                await update_or_query.edit_message_text(text)
            else:
                await update_or_query.reply_text(text)
            return

        monitors = self.controller.monitors_attivi()
        if not monitors:
            text = (
                "ℹ️ <b>Nessun monitoraggio attualmente in corso.</b>\n\n"
                "La blacklist può essere consultata solo se ci sono visite o appuntamenti attivi in monitoraggio.\n"
                "Usa /monitora per avviare un nuovo monitoraggio."
            )
            if is_edit:
                await update_or_query.edit_message_text(text, parse_mode=ParseMode.HTML)
            else:
                await update_or_query.reply_text(text, parse_mode=ParseMode.HTML)
            return

        kb = []
        for m in monitors:
            nome = self.controller._nome_leggibile(m)
            bl_count = len(self.controller.get_blacklist(m.get("id", "")))
            badge = f" ({bl_count} rifiutate)" if bl_count > 0 else ""
            kb.append([InlineKeyboardButton(
                f"🩺 {nome[:28]}{badge}",
                callback_data=f"bl:pick:{m['id']}"
            )])
        kb.append([InlineKeyboardButton("❌ Chiudi", callback_data="bl:close")])

        testo = (
            "🚫 <b>Gestione Blacklist Appuntamenti</b>\n\n"
            "Seleziona la visita in monitoraggio di cui vuoi consultare o modificare la blacklist delle disponibilità rifiutate:"
        )
        if is_edit:
            await update_or_query.edit_message_text(testo, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
        else:
            await update_or_query.reply_text(testo, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

    async def _mostra_dettaglio_blacklist(self, query, mid: str) -> None:
        """Show blacklist entries for a specific monitor, with buttons to delete them."""
        if not self.controller:
            await query.edit_message_text("Controller non inizializzato.")
            return

        mon = next((m for m in self.controller.store.get_monitors() if m.get("id") == mid), None)
        if mon is None:
            mon = next((m for m in self.controller.cfg.active_monitors if m.get("id") == mid), None)
        nome = self.controller._nome_leggibile(mon or {"id": mid})

        entries = self.controller.get_blacklist(mid)
        if not entries:
            testo = (
                f"🚫 <b>Blacklist per {self._esc(nome)}</b>\n\n"
                "La blacklist per questa visita è attualmente <b>vuota</b>.\n"
                "Nessuna disponibilità è stata scartata finora."
            )
            kb = [
                [InlineKeyboardButton("🔙 Torna ai monitor", callback_data="bl:list")],
                [InlineKeyboardButton("❌ Chiudi", callback_data="bl:close")],
            ]
            await query.edit_message_text(testo, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
            return

        lines = [
            f"🚫 <b>Blacklist per {self._esc(nome)}</b>\n"
            f"<i>Disponibilità rifiutate che il bot esclude automaticamente:</i>\n"
        ]
        import datetime as _dt
        for i, e in enumerate(entries):
            d_str = e.get("date_str", "")
            t_str = e.get("time_str", "")
            is_day = bool(e.get("block_day"))
            az = e.get("azienda", "")
            sede = e.get("sede", "")
            comune = e.get("comune", "")
            if is_day:
                riga = f"<b>{i + 1}) 🗓 {self._esc(d_str)} 🚫 (INTERA GIORNATA BLOCCATA)</b>"
            else:
                riga = f"<b>{i + 1}) 🗓 {self._esc(d_str)} ore {self._esc(t_str)}</b>"
            if az:
                riga += f"\n   🏥 {self._esc(az)}"
            luogo = []
            if sede:
                luogo.append(self._esc(sede))
            if comune:
                luogo.append(f"({self._esc(comune)})")
            if luogo:
                riga += f"\n   📍 {' · '.join(luogo)}"
            rej = e.get("rejected_at")
            if rej:
                dt_rej = _dt.datetime.fromtimestamp(rej).strftime("%d/%m/%Y %H:%M")
                riga += f"\n   ⏱ <i>Rifiutata il: {dt_rej}</i>"
            lines.append(riga)

        lines.append(
            "\n💡 <i>La blacklist resta attiva per tutta la durata di validità della ricetta (1 anno). "
            "Puoi eliminare singole voci per consentire al bot di riproporle, oppure cancellare l'intera blacklist.</i>"
        )
        testo = "\n".join(lines)

        kb = []
        for i, e in enumerate(entries):
            if e.get("block_day"):
                d_label = f"{e.get('date_str', '')} (giorno)"
            else:
                d_label = f"{e.get('date_str', '')[-5:]} {e.get('time_str', '')}".strip()
            kb.append([InlineKeyboardButton(
                f"🗑 Elimina #{i + 1} ({d_label})",
                callback_data=f"bl:del:{mid}:{e['id']}"
            )])

        kb.append([InlineKeyboardButton("💥 Cancella intera blacklist", callback_data=f"bl:clear_ask:{mid}")])
        kb.append([
            InlineKeyboardButton("🔙 Torna ai monitor", callback_data="bl:list"),
            InlineKeyboardButton("❌ Chiudi", callback_data="bl:close"),
        ])

        await query.edit_message_text(testo, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

    async def _h_blacklist(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/blacklist command: inspect and manage rejected appointment availabilities."""
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        await self._mostra_scelta_monitor_blacklist(update.message, is_edit=False)

    async def _h_appuntamenti(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        if self.controller and getattr(self.controller, "login_in_corso", False) is True:
            await update.message.reply_text("🔄 Tentativo di accesso attualmente in corso. Controlla il telefono/dispositivo per completare l'autenticazione prima di procedere.")
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
            # Location: facility (if != company), address, municipality
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
                # Google Maps link (query = facility/company + municipality)
                qparts = [p for p in (presi or azienda, indirizzo, comune) if p]
                if qparts:
                    import urllib.parse as _up
                    q = _up.quote(" ".join(qparts))
                    maps_url = f"https://www.google.com/maps/search/?api=1&query={q}"
                    lines.append(f'   🗺 <a href="{maps_url.replace("&", "&amp;")}">Apri in Google Maps</a>')
            if a.get("codice"):
                lines.append(f"   🎫 {self._esc(a.get('codice',''))}")
        msg = "\n".join(lines)

        # Cache for interactive callbacks (e.g. generating .ics)
        chat_id = update.effective_chat.id
        self._appuntamenti_cache[chat_id] = appuntamenti
        self._wizard[chat_id] = {"appuntamenti": appuntamenti}

        kb = []
        if len(appuntamenti) == 1:
            kb.append([InlineKeyboardButton("📅 Genera evento calendario", callback_data="app:ics:0")])
        elif len(appuntamenti) > 1:
            for i, a in enumerate(appuntamenti):
                data_lbl = ""
                if a.get("data_ora"):
                    import re as _re
                    m_dt = _re.search(r"\d{2}/\d{2}", a["data_ora"])
                    if m_dt:
                        data_lbl = f" ({m_dt.group(0)})"
                p_short = (a.get("prestazione") or "").strip()
                if len(p_short) > 22:
                    p_short = p_short[:19] + "…"
                desc_lbl = f" - {p_short}" if p_short else ""
                btn_txt = f"📅 Genera calendario #{i + 1}{data_lbl}{desc_lbl}"
                kb.append([InlineKeyboardButton(btn_txt, callback_data=f"app:ics:{i}")])
            kb.append([InlineKeyboardButton("📅 Genera tutti gli eventi calendario", callback_data="app:ics:all")])

        reply_markup = InlineKeyboardMarkup(kb) if kb else None

        chunks = list(msg[i:i + 3800] for i in range(0, len(msg), 3800))
        for idx_c, chunk in enumerate(chunks):
            rm = reply_markup if idx_c == len(chunks) - 1 else None
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML, reply_markup=rm)

    async def _h_ricette(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        if self.controller and getattr(self.controller, "login_in_corso", False) is True:
            await update.message.reply_text("🔄 Tentativo di accesso attualmente in corso. Controlla il telefono/dispositivo per completare l'autenticazione prima di procedere.")
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
        # Limit to 3 most recent (parser sorts date descending)
        ricette = ricette[:3]
        lines = [f"📋 Le {len(ricette)} ricette più recenti:\n"]
        for i, r in enumerate(ricette):
            prest = ", ".join(r["prestazioni"])
            if len(prest) > 45:
                prest = prest[:42] + "…"
            stato = (r.get("stato") or "").strip()
            prenotabile = r.get("prenotabile", False)
            if prenotabile:
                stato_icona = "🅿️ Da prenotare"
            elif "erogata" in stato.lower():
                stato_icona = "⚪ Erogata"
            elif "prenotata" in stato.lower():
                stato_icona = "✅ Già prenotata"
            elif r.get("categoria") == "farmaceutica":
                stato_icona = "💊 Farmaco"
            elif r.get("categoria") == "laboratorio":
                stato_icona = "🧪 Laboratorio"
            elif stato.lower().startswith("prescritta") or "attiva" in stato.lower():
                stato_icona = "🅿️ Da prenotare"
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
        # Send in chunks if too long
        msg = "\n".join(lines)
        for chunk in (msg[i:i + 3800] for i in range(0, len(msg), 3800)):
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
        # Download buttons for prescriptions with download link
        self._wizard[update.effective_chat.id] = {"ricette": ricette}
        scaricabili = [(i, r) for i, r in enumerate(ricette) if r.get("link_download")]
        if scaricabili:
            kb = [[InlineKeyboardButton(
                f"⬇️ Scarica {r.get('codice','')}", callback_data=f"ric:dl:{i}")]
                  for i, r in scaricabili]
            kb.append([InlineKeyboardButton("❌ Chiudi", callback_data="ric:done")])
            await update.message.reply_text(
                "🗂 Puoi scaricare il promemoria PDF delle ricette:",
                reply_markup=InlineKeyboardMarkup(kb))

    async def _h_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        was_blocked = self._risveglia_se_needed(update)
        if self.controller:
            text = self.controller.status_text()
        else:
            text = "Controller non inizializzato."
        if was_blocked:
            text = "🔄 Login sbloccato e riavviato!\n\n" + text
        await update.message.reply_text(text)

    async def _invia_screenshot(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Cattura uno screenshot istantaneo del portale e lo invia nella chat."""
        target_msg = update.effective_message
        if not self.controller:
            if target_msg:
                await target_msg.reply_text("❌ Controller non inizializzato.")
            return

        status_msg = None
        if target_msg:
            try:
                status_msg = await target_msg.reply_text("📸 Acquisizione screenshot del portale in corso...")
            except Exception as e_status:
                log.debug("Invio messaggio di attesa screenshot non riuscito: %s", e_status)

        loop = asyncio.get_running_loop()
        try:
            res = await loop.run_in_executor(None, self.controller.cattura_screenshot)
        except Exception as e:
            log.warning("Errore executor cattura_screenshot: %s", e)
            if status_msg:
                try:
                    await status_msg.edit_text(f"❌ Errore durante l'acquisizione dello screenshot: {e}")
                except Exception:
                    pass
            return

        if not res or not res.get("ok"):
            err_msg = res.get("error", "browser non disponibile") if res else "errore sconosciuto"
            if status_msg:
                try:
                    await status_msg.edit_text(
                        f"❌ Impossibile acquisire lo screenshot del portale:\n<code>{self._esc(err_msg)}</code>",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
            return

        png_path = res.get("png_path", "")
        url = res.get("url", "")
        title = res.get("title", "")
        stato = res.get("stato", "")
        ts = res.get("timestamp", "")
        in_corso = res.get("in_corso", False)

        caption_lines = [
            "📸 <b>Screenshot del Portale</b>",
            "",
            f"🌐 <b>URL:</b> <code>{self._esc(url)}</code>" if url else "",
            f"📄 <b>Titolo:</b> {self._esc(title)}" if title else "",
            f"ℹ️ <b>Stato:</b> {self._esc(stato)}" if stato else "",
            "⚡ <i>Catturato con priorità (flusso attivo preservato)</i>" if in_corso else "",
            f"⏱ <i>{self._esc(ts)}</i>",
        ]
        caption = "\n".join(line for line in caption_lines if line)

        chat_id = update.effective_chat.id if update.effective_chat else None
        bot = ctx.bot if (ctx and hasattr(ctx, "bot")) else getattr(self, "app", None).bot if getattr(self, "app", None) else None

        sent = False
        try:
            with open(png_path, "rb") as f:
                if target_msg and hasattr(target_msg, "reply_photo"):
                    await target_msg.reply_photo(
                        photo=f,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )
                elif bot and chat_id:
                    await bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                    )
                sent = True
        except Exception as e_photo:
            log.warning("reply_photo fallito (%s), tento invio come file/documento", e_photo)
            try:
                from pathlib import Path
                filename = Path(png_path).name
                with open(png_path, "rb") as f:
                    if target_msg and hasattr(target_msg, "reply_document"):
                        await target_msg.reply_document(
                            document=f,
                            filename=filename,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                        )
                    elif bot and chat_id:
                        await bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            filename=filename,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                        )
                    sent = True
            except Exception as e_doc:
                log.error("Invio screenshot come documento fallito: %s", e_doc)
                if status_msg:
                    try:
                        await status_msg.edit_text(f"❌ Errore durante l'invio dell'immagine: {e_doc}")
                    except Exception:
                        pass
                return

        if sent and status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass

    async def _h_screenshot(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        await self._invia_screenshot(update, ctx)

    async def _h_poll(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        if self.controller and getattr(self.controller, "login_in_corso", False) is True:
            await update.message.reply_text("🔄 Tentativo di accesso attualmente in corso. Controlla il telefono/dispositivo per completare l'autenticazione prima di forzare il controllo.")
            return
        was_blocked = self._risveglia_se_needed(update)
        if was_blocked:
            await update.message.reply_text("🔄 Tentativi di login sbloccati! Eseguo l'accesso e procederò con il controllo...")
            return
        if self.controller and getattr(self.controller, "paused", False) is True:
            await update.message.reply_text("Avvio controllo forzato (nota: il monitoraggio periodico è in pausa, usa /resume o /pause per riattivarlo)...")
        else:
            await update.message.reply_text("Avvio polling forzato...")
        if self.controller:
            self.controller.force_poll()

    async def _h_anticipa(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        if self.controller is None:
            await update.message.reply_text("Controller non inizializzato.")
            return

        # 1. If explicit ID provided as argument
        if ctx.args:
            mid = ctx.args[0]
            ok = self.controller.converti_in_reschedule(mid)
            if ok:
                pren = self.controller.store.get_prenotazione(mid)
                d_str = pren.get("data_ora") or ""
                data_info = f"\n📌 Appuntamento attuale: <b>{self._esc(d_str)}</b>" if d_str else ""
                await update.message.reply_text(
                    f"🔄 <b>Ricerca anticipo attivata per {self._esc(mid)}!</b>{data_info}\n\n"
                    f"Il bot continuerà a verificare automaticamente per provare ad anticipare la visita.",
                    parse_mode=ParseMode.HTML,
                )
                self.controller.force_poll()
            else:
                await update.message.reply_text(f"⚠️ Impossibile attivare l'anticipo per {self._esc(mid)}: monitor non trovato o nessuna prenotazione valida registrata.")
            return

        # 2. Without arguments: if only 1 active monitor, use directly
        monitors = self.controller.monitors_attivi()
        if not monitors:
            await update.message.reply_text("Nessun monitor attivo.")
            return

        if len(monitors) == 1:
            mid = monitors[0]["id"]
            ok = self.controller.converti_in_reschedule(mid)
            if ok:
                pren = self.controller.store.get_prenotazione(mid)
                d_str = pren.get("data_ora") or ""
                data_info = f"\n📌 Appuntamento attuale: <b>{self._esc(d_str)}</b>" if d_str else ""
                await update.message.reply_text(
                    f"🔄 <b>Ricerca anticipo attivata per {self._esc(mid)}!</b>{data_info}\n\n"
                    f"Il bot continuerà a verificare automaticamente per provare ad anticipare la visita.",
                    parse_mode=ParseMode.HTML,
                )
                self.controller.force_poll()
            else:
                await update.message.reply_text(f"⚠️ Impossibile attivare l'anticipo per {self._esc(mid)}: nessuna prenotazione valida registrata.")
            return

        # 3. Multiple active monitors: show selection buttons
        kb = [[InlineKeyboardButton(
            f"{m.get('ricetta','')[:28]} [{m.get('type','')}]",
            callback_data=f"anticipa:pick:{m['id']}")] for m in monitors]
        kb.append([InlineKeyboardButton("❌ Annulla", callback_data="stop:annulla")])
        await update.message.reply_text(
            "🔄 Scegli per quale visita cercare un <b>anticipo</b>:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML,
        )

    async def _h_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        if self.controller is None:
            await update.message.reply_text("Controller non inizializzato.")
            return

        arg = (ctx.args[0].lower() if ctx.args else "").strip()
        if arg in ("resume", "unpause", "off", "start", "riprendi", "via"):
            if not getattr(self.controller, "paused", False):
                await update.message.reply_text(
                    "ℹ️ <b>Il monitoraggio automatico è già attivo!</b>\nUsa /pause per metterlo in pausa.",
                    parse_mode=ParseMode.HTML)
            else:
                self.controller.resume()
                await update.message.reply_text(
                    "▶️ <b>Ricerca riattivata!</b>\n\n"
                    "Il bot ha ripreso il controllo periodico automatico della disponibilità.\n"
                    "<i>(È stato avviato un controllo immediato)</i>",
                    parse_mode=ParseMode.HTML)
            return

        if arg in ("pause", "on", "stop", "ferma", "pausa"):
            if getattr(self.controller, "paused", False):
                await update.message.reply_text(
                    "ℹ️ <b>Il monitoraggio automatico è già in pausa!</b>\nUsa /resume o /pause per riattivarlo.",
                    parse_mode=ParseMode.HTML)
            else:
                self.controller.pause()
                await update.message.reply_text(
                    "⏸️ <b>Ricerca messa in pausa!</b>\n\n"
                    "I controlli automatici periodici sono stati sospesi.\n"
                    "• Per riprendere: usa <b>/resume</b> (oppure di nuovo <b>/pause</b>)\n"
                    "• Per un controllo singolo manuale: usa <b>/poll</b>",
                    parse_mode=ParseMode.HTML)
            return

        # Toggle (default for /pause or /pause_unpause without arguments)
        is_now_paused = self.controller.toggle_pause()
        if is_now_paused:
            await update.message.reply_text(
                "⏸️ <b>Ricerca messa in pausa!</b>\n\n"
                "I controlli automatici periodici sono stati sospesi.\n"
                "• Per riprendere: usa <b>/resume</b> (oppure di nuovo <b>/pause</b>)\n"
                "• Per un controllo singolo manuale: usa <b>/poll</b>",
                parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(
                "▶️ <b>Ricerca riattivata!</b>\n\n"
                "Il bot ha ripreso il controllo periodico automatico della disponibilità.\n"
                "<i>(È stato avviato un controllo immediato)</i>",
                parse_mode=ParseMode.HTML)

    async def _h_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        if self.controller is None:
            await update.message.reply_text("Controller non inizializzato.")
            return

        if not getattr(self.controller, "paused", False):
            await update.message.reply_text(
                "ℹ️ <b>Il monitoraggio automatico è già attivo!</b>\nUsa /pause per metterlo in pausa.",
                parse_mode=ParseMode.HTML)
            return

        self.controller.resume()
        await update.message.reply_text(
            "▶️ <b>Ricerca riattivata!</b>\n\n"
            "Il bot ha ripreso il controllo periodico automatico della disponibilità.\n"
            "<i>(È stato avviato un controllo immediato)</i>",
            parse_mode=ParseMode.HTML)

    async def _h_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._autorizzato(update):
            return
        self._risveglia_se_needed(update)
        await update.message.reply_text(HELP_TEXT)

    async def _h_echo_chat(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle generic text messages (chat info, OTP input, fallback responses)."""
        if not self._autorizzato(update):
            return

        raw_text = update.message.text if (update.message and isinstance(update.message.text, str)) else ""
        text = raw_text.strip()

        # 1. If waiting for OTP code input
        if getattr(self, "_pending_otp", False) and self._otp_event is not None:
            if text.lower() in ("/cancel", "annulla", "esci"):
                self._otp_value = None
                self._pending_otp = False
                self._otp_event.set()
                await update.message.reply_text("❌ Inserimento OTP annullato.")
                return

            clean_code = text.replace(" ", "").replace("-", "").strip()
            self._otp_value = clean_code
            self._pending_otp = False
            self._otp_event.set()
            masked = clean_code[:2] + "****" if len(clean_code) > 2 else "****"
            await update.message.reply_text(f"✅ Codice OTP ricevuto ({masked}). Procedo con l'autenticazione...")
            return

        # 2. If waiting for fallback choice (notification vs OTP vs QR)
        if getattr(self, "_auth_event", None) is not None:
            low = text.lower()
            if any(k in low for k in ["qr", "qrcode", "codice qr", "foto"]):
                self._auth_choice = "qr"
                self._auth_event.set()
                await update.message.reply_text("📲 Hai scelto: Mostra QR Code. Recupero l'immagine...")
                return
            elif any(k in low for k in ["notifica", "invia notifica", "push", "1"]):
                self._auth_choice = "notify"
                self._auth_event.set()
                await update.message.reply_text("📲 Hai scelto: Invia notifica push.")
                return
            elif any(k in low for k in ["otp", "codice", "sms", "app", "2"]):
                self._auth_choice = "otp"
                self._auth_event.set()
                await update.message.reply_text("🔢 Hai scelto: Immetti codice OTP. Inserisci il codice generato dall'app:")
                return
            elif any(k in low for k in ["annulla", "cancella", "cancel", "esci"]):
                self._auth_choice = "cancel"
                self._auth_event.set()
                await update.message.reply_text("❌ Login annullato.")
                return
            # If user directly typed a numerical code (e.g. 6 or 8 digits)
            cleaned = text.replace(" ", "").replace("-", "").strip()
            if cleaned.isdigit() and len(cleaned) in (6, 8):
                self._auth_choice = "otp"
                self._received_otp = cleaned
                self._auth_event.set()
                masked = cleaned[:2] + "****"
                await update.message.reply_text(f"✅ Codice OTP ricevuto direttamente ({masked})! Procedo con l'accesso...")
                return

        # If user types /pause-unpause (dash is not recognized as standard command by Telegram)
        if text and text.lower().startswith(("/pause-unpause", "pause-unpause")):
            await self._h_pause(update, ctx)
            return

        chat = update.effective_chat
        await update.message.reply_text(
            f"🤖 Bot attivo!\nChat ID: `{chat.id}`\nTipo: {chat.type}"
            "\nUsa /help per i comandi.")

    async def _h_genera_ics_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE, data: str) -> None:
        """Generate and send .ics calendar event file for existing appointments."""
        q = update.callback_query
        chat_id = update.effective_chat.id
        arg = data.split(":", 2)[2]

        appuntamenti = self._appuntamenti_cache.get(chat_id) or []
        if not appuntamenti:
            appuntamenti = (self._wizard.get(chat_id) or {}).get("appuntamenti", [])

        if not appuntamenti and self.controller:
            await q.answer("Recupero appuntamenti dal portale...")
            try:
                appuntamenti = self.controller.get_appuntamenti() or []
                self._appuntamenti_cache[chat_id] = appuntamenti
            except Exception as e:
                log.warning("Recupero appuntamenti per ICS fallito: %s", e)

        if not appuntamenti:
            await q.answer("Nessun appuntamento in memoria. Usa /appuntamenti per ricaricare la lista.", show_alert=True)
            return

        from pathlib import Path
        from services.appuntamento import appuntamento_to_info
        from services.calendar import write_ics

        target_items = []
        if arg == "all":
            target_items = list(enumerate(appuntamenti))
        else:
            try:
                idx = int(arg)
                if 0 <= idx < len(appuntamenti):
                    target_items = [(idx, appuntamenti[idx])]
            except ValueError:
                pass

        if not target_items:
            await q.answer("Appuntamento non trovato o indice non valido.", show_alert=True)
            return

        await q.answer("Generazione evento calendario in corso...")

        for idx, app in target_items:
            try:
                info = appuntamento_to_info(app)
                ics_path = write_ics(info, Path("data/ics"))
                lines = [
                    f"📅 <b>Evento calendario per {self._esc(info.prestazione or 'visita')}</b>",
                    f"🗓 Data e ora: {self._esc(info.data_ora or '-')}",
                ]
                if info.azienda:
                    lines.append(f"🏥 Azienda: {self._esc(info.azienda)}")
                loc = " · ".join(x for x in [info.presentarsi_in, info.indirizzo] if x)
                if loc:
                    lines.append(f"📍 Luogo: {self._esc(loc)}")
                if info.codice:
                    lines.append(f"🎫 Codice: {self._esc(info.codice)}")
                caption = "\n".join(lines)

                send_doc = getattr(q.message, "reply_document", None)
                if send_doc is not None:
                    with open(ics_path, "rb") as f:
                        await send_doc(
                            document=f,
                            filename=ics_path.name,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                        )
                elif ctx and hasattr(ctx, "bot") and hasattr(ctx.bot, "send_document"):
                    with open(ics_path, "rb") as f:
                        await ctx.bot.send_document(
                            chat_id=chat_id,
                            document=f,
                            filename=ics_path.name,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                        )
                else:
                    await q.message.reply_text(f"{caption}\n(File generato in: {ics_path})")
            except Exception as e:
                log.exception("Errore generazione ICS per appuntamento %s: %s", idx, e)
                await q.message.reply_text(f"⚠️ Errore durante la creazione del file ICS per l'appuntamento #{idx + 1}: {e}")

    async def _scarica_ricetta(self, update, ricetta):
        """Trigger prescription download (in bot's browser)."""
        q = update.callback_query
        link = (ricetta.get("link_download") or "")
        if not link:
            await q.answer("Nessun link download")
            return
        # Extract URL from javascript:addCurrentPage('URL')
        import re as _re
        m = _re.search(r"'([^']+)'", link)
        url = m.group(1) if m else link
        url = url.replace("&amp;", "&")
        try:
            driver = self.controller.browser.start()
            await q.edit_message_text("⬇️ Genero il file della ricetta...")
            driver.get(url)
            # Wait and verify download has started (file goes to download dir)
            import time as _time
            _time.sleep(6)
            await q.edit_message_text("✅ Download della ricetta avviato.\n"
                                      f"📄 {self._esc(ricetta.get('codice',''))} · "
                                      f"{self._esc(', '.join(ricetta.get('prestazioni', [])[:1]))}")
        except Exception as e:  # noqa: BLE001
            await q.answer(f"Errore download: {e}")

    async def _send_startup_welcome(self, application: Application) -> None:
        """Send startup welcome notification with version and update status."""
        from services.updater import (
            get_cached_or_check_updates,
            format_telegram_welcome,
            is_tg_startup_notification_enabled,
        )
        cfg = getattr(self.controller, "cfg", None) if hasattr(self, "controller") else None
        if is_tg_startup_notification_enabled(cfg) and self.token and self.chat_ids:
            try:
                info = get_cached_or_check_updates(cfg=cfg)
                text, buttons = format_telegram_welcome(info)
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(lbl, url=cb) if str(cb).startswith(("http://", "https://"))
                    else InlineKeyboardButton(lbl, callback_data=cb) for lbl, cb in row
                ] for row in buttons])
                for cid in self.chat_ids:
                    try:
                        await application.bot.send_message(
                            chat_id=cid,
                            text=text,
                            reply_markup=kb,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True,
                        )
                    except Exception as e:  # noqa: BLE001
                        log.warning("Invio messaggio di benvenuto a %s non riuscito: %s", cid, e)
            except Exception as e:  # noqa: BLE001
                log.warning("Errore invio messaggio di benvenuto Telegram all'avvio: %s", e)

    # ---------- run ----------
    def run(self) -> None:
        """Start bot polling (blocking; runs in a separate process/thread)."""
        from telegram import BotCommand

        async def _post_init(application: Application) -> None:
            commands = [
                BotCommand("start", "Panoramica e benvenuto"),
                BotCommand("monitora", "Crea un nuovo monitor (wizard guidato)"),
                BotCommand("blacklist", "Visualizza ed elimina disponibilità rifiutate"),
                BotCommand("status", "Stato dei monitor e sessione SPID"),
                BotCommand("poll", "Forza controllo disponibilità adesso"),
                BotCommand("anticipa", "Cerca date precedenti per visita prenotata"),
                BotCommand("screenshot", "Cattura e invia screenshot del portale"),
                BotCommand("pause", "Mette in pausa o riprende la ricerca automatica"),
                BotCommand("resume", "Riprende la ricerca automatica"),
                BotCommand("ricette", "Elenco delle ricette dematerializzate"),
                BotCommand("appuntamenti", "Elenco degli appuntamenti già presi"),
                BotCommand("stop", "Interrompi un monitor attivo"),
                BotCommand("version", "Versione e verifica aggiornamenti"),
                BotCommand("help", "Guida ai comandi"),
            ]
            try:
                await application.bot.set_my_commands(commands)
                log.info("Comandi del bot registrati automaticamente su Telegram")
            except Exception as e:  # noqa: BLE001
                log.warning("Impossibile registrare i comandi su Telegram: %s", e)

            await self._send_startup_welcome(application)

        async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            if isinstance(context.error, telegram.error.Conflict):
                log.error("⚠️ Conflitto Telegram (HTTP 409 Conflict): un'altra istanza del bot sta usando lo stesso TG_TOKEN contemporaneamente! Assicurati che solo una istanza del bot sia in esecuzione (es. arresta istanze locali o stack duplicati su Portainer/server).")
            elif isinstance(context.error, (telegram.error.NetworkError, telegram.error.TimedOut, httpx.HTTPError, ConnectionError, OSError)):
                log.warning("Avviso di rete gestito da TelegramBot: %s", context.error)
            else:
                log.error("Errore gestito da TelegramBot: %s", context.error, exc_info=context.error)

        self.app = self._create_application_builder().post_init(_post_init).build()
        self.app.add_error_handler(_error_handler)
        self.app.add_handler(CommandHandler("start", self._h_start))
        self.app.add_handler(CommandHandler("status", self._h_status))
        self.app.add_handler(CommandHandler("ricette", self._h_ricette))
        self.app.add_handler(CommandHandler(["appuntamenti", "calendario", "ics"], self._h_appuntamenti))
        self.app.add_handler(CommandHandler("poll", self._h_poll))
        self.app.add_handler(CommandHandler("anticipa", self._h_anticipa))
        self.app.add_handler(CommandHandler(["screenshot", "screen", "foto"], self._h_screenshot))
        self.app.add_handler(CommandHandler(["pause", "pause_unpause"], self._h_pause))
        self.app.add_handler(CommandHandler(["resume", "unpause"], self._h_resume))
        self.app.add_handler(CommandHandler("monitora", self._h_monitora))
        self.app.add_handler(CommandHandler("blacklist", self._h_blacklist))
        self.app.add_handler(CommandHandler("stop", self._h_stop))
        self.app.add_handler(CommandHandler(["version", "versione", "aggiornamenti", "update"], self._h_version))
        self.app.add_handler(CommandHandler("bug", self._h_bug))
        self.app.add_handler(CommandHandler(["dona", "donazione", "caffe", "coffee", "supporto"], self._h_dona))
        self.app.add_handler(CommandHandler("help", self._h_help))
        # Inline buttons (wizard /monitora and replies)
        self.app.add_handler(CallbackQueryHandler(self._h_callback))
        # Any non-command message -> chat info (for setup / commands)
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._h_echo_chat))
        log.info("Bot Telegram avviato")
        self.app.run_polling()
