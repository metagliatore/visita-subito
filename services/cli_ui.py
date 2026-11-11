"""Interactive Command-Line Interface (CLI) Controller / Terminal UI.

Serves as a full standalone alternative to TelegramBot, providing the same
capabilities directly in the console with optional sound alerts.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

from services.appuntamento import appuntamento_to_info
from services.calendar import build_ics, write_ics
from services.sound import play_alert_sound

log = logging.getLogger(__name__)


def strip_html(text: str) -> str:
    """Convert HTML-formatted messages (designed for Telegram) to plain text."""
    if not text:
        return ""
    # Transform <a href="url">label</a> into 'label (url)'
    t = re.sub(r'<a\s+href="([^"]+)">([^<]+)</a>', r"\2 (\1)", text)
    # Strip remaining HTML tags
    t = re.sub(r"<[^>]+>", "", t)
    # Replace common HTML entities
    t = (
        t.replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    return t.strip()


def safe_int(val: object) -> int | None:
    """Safely parse an integer, returning None if invalid, unicode-digit, or overflow."""
    if val is None:
        return None
    try:
        return int(str(val).strip())
    except (ValueError, TypeError, OverflowError):
        return None


class CliUI:
    """Terminal/CLI interface adapter for Controller and Flows."""

    def __init__(self, sound_alert: bool = True):
        self.controller = None  # Injected by Controller
        self.sound_alert = sound_alert
        self.token = ""  # Telegram compatibility duck-typing
        self.chat_ids = []  # Telegram compatibility duck-typing

        self._stop_event = threading.Event()
        self._appuntamenti_cache: list[dict] = []

        # Synchronization for asynchronous requests
        self._pending_approval: dict | None = None
        self._pending_postbook: dict | None = None

        self._pending_otp = False
        self._otp_value: str | None = None
        self._otp_event = threading.Event()

        self._pending_auth_fallback = False
        self._auth_choice: str | None = None
        self._auth_event = threading.Event()

    # =========================================================================
    # UI Adapter API (called by Controller, Flows, and SessionManager)
    # =========================================================================

    def notify(self, text: str) -> None:
        """Display an informational notification in terminal."""
        clean = strip_html(text)
        print(f"\n[NOTIFICA] {clean}\n")
        if "login completato con successo" in clean.lower():
            self._cmd_help()
            try:
                sys.stdout.write(self._get_prompt_label())
                sys.stdout.flush()
            except Exception:
                pass

    def notify_buttons(self, text: str, buttons: list) -> list:
        """Display an action request with choices and set pending interactive state.

        Returns dummy list [('cli', 1)] for compatibility with Telegram edit tracking.
        """
        clean = strip_html(text)

        # Check if this is an approval request (decide:approve / decide:deny)
        req_id = None
        for row in buttons:
            for lbl, cb in row:
                if str(cb).startswith("decide:approve:"):
                    parts = str(cb).split(":")
                    if len(parts) >= 3:
                        req_id = parts[2]
                        break

        # Check if this is a post-booking prompt (postbook:stop / postbook:continue)
        postbook_mid = None
        for row in buttons:
            for lbl, cb in row:
                if str(cb).startswith("postbook:"):
                    parts = str(cb).split(":")
                    if len(parts) >= 3:
                        postbook_mid = parts[2]
                        break

        if req_id:
            if self.sound_alert:
                play_alert_sound("slot_found")
            self._pending_approval = {
                "req_id": req_id,
                "msg": clean,
                "time": time.time(),
            }
            print("\n" + "=" * 66)
            print("🔔 DISPONIBILITÀ TROVATA! - RICHIESTA APPROVAZIONE")
            print("=" * 66)
            print(clean)
            print("=" * 66)
            print("⚡ RISPONDI SUBITO: digita 's' per approvare, 'n' per rifiutare l'orario, 'g' per bloccare l'intera giornata.")
            print("=" * 66 + "\n")
            return [("cli", 1)]

        if postbook_mid:
            self._pending_postbook = {
                "mid": postbook_mid,
                "msg": clean,
            }
            print("\n" + "=" * 66)
            print("🎯 PRENOTAZIONE COMPLETATA!")
            print("=" * 66)
            print(clean)
            print("=" * 66)
            print("⚡ SCELTA: digita 'ferma' (o '1') per fermare il monitoraggio,")
            print("           digita 'continua' (o '2') per continuare a cercare anticipi.")
            print("=" * 66 + "\n")
            return [("cli", 2)]

        # Generic button prompt fallback
        print("\n" + "=" * 66)
        print(clean)
        print("Opzioni disponibili:")
        for r_idx, row in enumerate(buttons, 1):
            for lbl, _ in row:
                print(f"  - {lbl}")
        print("=" * 66 + "\n")
        return [("cli", 3)]

    def edit_message(self, text: str, destinations: list) -> None:
        """Handle message updates (e.g. proposal expired or decision made)."""
        clean = strip_html(text)
        if "scaduta" in clean.lower():
            self._pending_approval = None
            print(f"\n⏳ [SCADUTA] {clean}\n")
        else:
            print(f"\n[AGGIORNAMENTO] {clean}\n")

    def send_file(self, path: str, caption: str = "") -> None:
        """Display generated file notification."""
        clean_cap = strip_html(caption)
        print(f"\n📁 File pronto: {path}")
        if clean_cap:
            print(f"   {clean_cap}\n")

    def send_photo(self, path: str, caption: str = "", buttons: list | None = None, **kwargs) -> None:
        """Display image path notification (e.g. QR code or debug screenshot)."""
        clean_cap = strip_html(caption)
        print(f"\n📸 Immagine salvata: {path}")
        if clean_cap:
            print(f"   {clean_cap}\n")
        if buttons:
            for row in buttons:
                for lbl, target in row:
                    if str(target).startswith(("http://", "https://", "cieid://")):
                        print(f"   🔗 {lbl}: {target}")

    def ask_otp(self, prompt: str = "", timeout_s: int = 120) -> str | None:
        """Prompt user on terminal for OTP code."""
        self._otp_value = None
        self._otp_event.clear()
        self._pending_otp = True

        print("\n" + "=" * 66)
        print("🔑 CODICE OTP RICHIESTO PER IL LOGIN")
        if prompt:
            print(strip_html(prompt))
        print(f"Inserisci il codice OTP al prompt di comando (timeout {timeout_s}s):")
        print("=" * 66 + "\n")

        ok = self._otp_event.wait(timeout_s)
        self._pending_otp = False
        return self._otp_value if ok else None

    def ask_auth_fallback(self, prompt: str = "", timeout_s: int = 120, buttons: list | None = None, **kwargs) -> str | None:
        """Prompt user on terminal for 2FA fallback method."""
        self._auth_choice = None
        self._auth_event.clear()
        self._pending_auth_fallback = True

        print("\n" + "=" * 66)
        print("⚠️ SECONDO FATTORE AUTENTICAZIONE")
        if prompt:
            print(strip_html(prompt))
        if buttons:
            self._fallback_options = {}
            opt_idx = 1
            for row in buttons:
                for lbl, cb in row:
                    val = cb.replace("auth:", "")
                    self._fallback_options[str(opt_idx)] = val
                    print(f"{opt_idx}) {strip_html(lbl)}")
                    opt_idx += 1
            print(f"Digita il numero dell'opzione al prompt di comando (timeout {timeout_s}s):")
        else:
            self._fallback_options = {"1": "notify", "2": "otp"}
            print("1) Invia nuovamente notifica push sull'app")
            print("2) Inserisci codice OTP")
            print(f"Digita '1' o '2' al prompt di comando (timeout {timeout_s}s):")
        print("=" * 66 + "\n")

        ok = self._auth_event.wait(timeout_s)
        self._pending_auth_fallback = False
        return self._auth_choice if ok else None

    # =========================================================================
    # Terminal Interactive Shell (run loop)
    # =========================================================================

    def _get_prompt_label(self) -> str:
        """Dynamically format the command prompt based on pending state."""
        if self._pending_otp:
            return "[🔑 INSERISCI CODICE OTP] > "
        if self._pending_auth_fallback:
            return "[⚠️ SCELTA 2FA: 1=Notifica, 2=OTP] > "
        if self._pending_approval:
            return "[🔔 APPROVAZIONE IN SOSPESO: digita 's' o 'n'] > "
        if self._pending_postbook:
            return "[🎯 POST-PRENOTAZIONE: 1=ferma, 2=continua] > "
        return "prenotazioni> "

    def run(self) -> None:
        """Foreground interactive CLI loop."""
        self._print_welcome()
        while not self._stop_event.is_set():
            try:
                prompt_lbl = self._get_prompt_label()
                raw = input(prompt_lbl).strip()
            except (KeyboardInterrupt, EOFError):
                print("\n\nArresto richiesto da tastiera. Chiusura in corso...")
                break

            if not raw:
                continue

            try:
                self._dispatch_input(raw)
            except (KeyboardInterrupt, EOFError):
                print("\n[!] Operazione annullata.\n")
            except Exception as e:
                log.exception("Errore durante l'esecuzione del comando '%s'", raw)
                print(f"\n[!] Errore durante l'esecuzione del comando: {e}\n")

        self.stop()

    def stop(self) -> None:
        """Stop CLI UI and signal controller stop."""
        self._stop_event.set()
        if self.controller and hasattr(self.controller, "_stop"):
            self.controller._stop.set()
        if self.controller and hasattr(self.controller, "browser"):
            try:
                self.controller.browser.stop()
            except Exception:  # noqa: BLE001
                pass

    def _print_welcome(self) -> None:
        from services.updater import get_cached_or_check_updates, format_cli_welcome
        cfg = getattr(self.controller, "cfg", None) if hasattr(self, "controller") else None
        info = get_cached_or_check_updates(cfg=cfg)
        print("\n" + format_cli_welcome(info))
        print("I controlli automatici sono attivi in background.")
        print("Digita 'help' o '?' per la lista dei comandi disponibili.\n")

    def _dispatch_input(self, raw: str) -> None:
        line = raw.strip()
        low = line.lower()

        # 1. Check if waiting for OTP code
        if self._pending_otp:
            clean_code = re.sub(r"[\s-]+", "", line)
            self._otp_value = clean_code
            self._otp_event.set()
            print(f"✅ Codice OTP ricevuto: {clean_code}")
            return

        # 2. Check if waiting for auth fallback choice
        if self._pending_auth_fallback:
            opts = getattr(self, "_fallback_options", {})
            if low in opts:
                self._auth_choice = opts[low]
                self._auth_event.set()
                print(f"✅ Scelta registrata: {self._auth_choice}")
                return
            if any(k in low for k in ["qr", "qrcode", "codice qr"]):
                self._auth_choice = "qr"
                self._auth_event.set()
                print("📲 Passaggio alla modalità QR Code.")
                return
            elif low in ("notifica", "push"):
                self._auth_choice = "notify"
                self._auth_event.set()
                print("🔄 Invio nuova notifica push richiesto.")
                return
            elif low in ("otp", "codice"):
                self._auth_choice = "otp"
                self._auth_event.set()
                print("🔑 Passaggio alla modalità codice OTP.")
                return
            elif low in ("annulla", "cancel"):
                self._auth_choice = "cancel"
                self._auth_event.set()
                print("❌ Autenticazione annullata.")
                return
            else:
                valid_nums = "/".join(opts.keys()) if opts else "1/2"
                print(f"[!] Scelta non valida: digita {valid_nums} o il nome dell'azione.")
                return

        # 3. Check if waiting for slot approval
        if self._pending_approval:
            req_id = self._pending_approval["req_id"]
            if low in ("s", "si", "sì", "y", "yes", "approva", "ok"):
                if self.controller:
                    self.controller.decide_request(req_id, True)
                self._pending_approval = None
                if self.sound_alert:
                    play_alert_sound("success")
                print("✅ Proposta approvata! Procedo con la conferma sul portale...")
                return
            elif low in ("g", "giorno", "giornata", "day"):
                if self.controller:
                    self.controller.decide_request(req_id, False, extra={"azione": "deny_day"})
                self._pending_approval = None
                print("🚫 Proposta rifiutata. L'intera giornata è stata aggiunta alla blacklist del monitor.")
                return
            elif low in ("n", "no", "rifiuta", "annulla"):
                if self.controller:
                    self.controller.decide_request(req_id, False, extra={"azione": "deny"})
                self._pending_approval = None
                print("❌ Proposta rifiutata. Aggiunta alla blacklist del monitor.")
                return
            # If not s/n/g, fall through to command processing, but remind user
            print("[!] Nota: c'è una proposta in sospeso. Digita 's' per approvare, 'n' per rifiutare o 'g' per bloccare la giornata.")

        # 4. Check if waiting for post-booking choice
        if self._pending_postbook:
            mid = self._pending_postbook["mid"]
            if low in ("1", "ferma", "stop", "f"):
                if self.controller:
                    self.controller.rimuovi_monitor(mid)
                self._pending_postbook = None
                print(f"🛑 Monitoraggio fermato per {mid}.")
                return
            elif low in ("2", "continua", "anticipa", "c"):
                if self.controller:
                    pren = self.controller.store.get_prenotazione(mid)
                    d_ora = pren.get("data_ora", "")
                    if d_ora:
                        from services.deadline import check_reschedule_deadline, get_reschedule_min_working_days
                        min_days = get_reschedule_min_working_days()
                        is_too_late, app_dt, dl_dt, dl_msg = check_reschedule_deadline(d_ora, min_working_days=min_days)
                        if is_too_late:
                            print(f"\n⚠️ Impossibile convertire in anticipo:")
                            print(f"   {dl_msg}")
                            print(f"   Mancano meno di {min_days} giorni lavorativi all'appuntamento.")
                            print(f"   La riprogrammazione comporterebbe l'addebito della penale (malus ticket).\n")
                            self.controller.rimuovi_monitor(mid)
                            self._pending_postbook = None
                            return
                    self.controller.converti_in_reschedule(mid)
                self._pending_postbook = None
                print(f"🔄 Monitoraggio convertito in ricerca anticipi per {mid}.")
                return
            # If not 1/2, fall through but remind user
            print("[!] Nota: scelta post-prenotazione in sospeso. Digita '1' (ferma) o '2' (continua).")

        # 5. Normal CLI Commands
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("help", "h", "?"):
            self._cmd_help()
        elif cmd in ("status", "s"):
            self._cmd_status()
        elif cmd in ("poll", "p"):
            self._cmd_poll()
        elif cmd in ("pause", "pausa"):
            self._cmd_pause()
        elif cmd in ("resume", "riprendi", "riavvia"):
            self._cmd_resume()
        elif cmd in ("appuntamenti", "app"):
            self._cmd_appuntamenti()
        elif cmd in ("ics", "calendario"):
            self._cmd_ics(arg)
        elif cmd in ("ricette", "r"):
            self._cmd_ricette()
        elif cmd in ("anticipa", "a"):
            self._cmd_anticipa(arg)
        elif cmd in ("screenshot", "screen", "foto"):
            self._cmd_screenshot()
        elif cmd in ("monitora", "m"):
            self._cmd_monitora()
        elif cmd in ("blacklist", "b"):
            self._cmd_blacklist(arg)
        elif cmd in ("version", "versione", "update", "aggiornamenti", "v"):
            self._cmd_version()
        elif cmd == "bug":
            self._cmd_bug()
        elif cmd in ("clear", "cls"):
            os.system("cls" if sys.platform == "win32" else "clear")
        elif cmd in ("quit", "exit", "q"):
            print("Arresto del servizio...")
            self.stop()
        else:
            print(f"[!] Comando sconosciuto: '{cmd}'. Digita 'help' per la lista dei comandi.")

    # =========================================================================
    # Command Implementations
    # =========================================================================

    def _cmd_help(self) -> None:
        print("\n" + "=" * 66)
        print("  COMANDI DISPONIBILI (Terminale CLI)")
        print("=" * 66)
        print("  status, s          - Visualizza lo stato dei monitor e della sessione")
        print("  poll, p            - Forza un controllo immediato delle disponibilità")
        print("  appuntamenti, app  - Elenca gli appuntamenti prenotati nel fascicolo")
        print("  ics <num|all>      - Salva file calendario .ics (es. 'ics 1' o 'ics all')")
        print("  ricette, r         - Elenca le ricette mediche (prenotabili e prenotate)")
        print("  monitora, m        - Procedura guidata per creare un nuovo monitoraggio")
        print("  anticipa, a        - Avvia la ricerca di anticipi su un appuntamento")
        print("  screenshot, screen - Cattura e salva screenshot attuale del portale")
        print("  pause              - Mette in pausa il polling automatico")
        print("  resume             - Riattiva il polling automatico")
        print("  blacklist, b       - Visualizza o svuota le disponibilità rifiutate")
        print("  version, v         - Visualizza versione e verifica aggiornamenti su GitHub")
        print("  bug                - Segnala un problema o malfunzionamento")
        print("  clear, cls         - Pulisce lo schermo del terminale")
        print("  quit, exit, q      - Chiude il programma")
        print("=" * 66 + "\n")

    def _cmd_version(self) -> None:
        from services.updater import check_for_updates, format_cli_welcome
        cfg = getattr(self.controller, "cfg", None) if hasattr(self, "controller") else None
        print("\nVerifica aggiornamenti su GitHub in corso...")
        info = check_for_updates(force=True, cfg=cfg)
        print(format_cli_welcome(info) + "\n")

    def _cmd_bug(self) -> None:
        from services.updater import format_issue_guide
        text, buttons = format_issue_guide()
        clean = strip_html(text)
        print("\n" + "=" * 66)
        print(clean)
        for row in buttons:
            for lbl, url in row:
                print(f"  • {lbl}: {url}")
        print("=" * 66 + "\n")

    def _cmd_screenshot(self) -> None:
        if not self.controller:
            print("[!] Controller non inizializzato.")
            return
        print("📸 Acquisizione screenshot del portale in corso...")
        res = self.controller.cattura_screenshot(prefix="cli")
        if not res or not res.get("ok"):
            err = res.get("error", "errore sconosciuto") if res else "errore sconosciuto"
            print(f"[!] Errore acquisizione screenshot: {err}")
            return
        print(f"✅ Screenshot salvato: {res['png_path']}")
        if res.get("url"):
            print(f"   URL: {res['url']}")
        if res.get("title"):
            print(f"   Titolo: {res['title']}")
        if res.get("stato"):
            print(f"   Stato: {res['stato']}")
        if res.get("in_corso"):
            print("   ⚡ Catturato con priorità (flusso attivo in background preservato)")

    def _cmd_status(self) -> None:
        if not self.controller:
            print("Controller non inizializzato.")
            return
        print("\n" + self.controller.status_text() + "\n")

    def _cmd_poll(self) -> None:
        if not self.controller:
            print("Controller non inizializzato.")
            return
        if getattr(self.controller, "login_bloccato", False) is True:
            print("🔄 Login attualmente bloccato: tentativo di sblocco in corso...")
            self.controller.risveglia(manual_poll=True)
            return
        print("🔄 Avvio controllo forzato delle disponibilità in corso...")
        self.controller.force_poll()

    def _cmd_pause(self) -> None:
        if not self.controller:
            print("Controller non inizializzato.")
            return
        self.controller.pause()
        print("⏸️ Polling automatico messo IN PAUSA. Nessun controllo automatico verrà eseguito.")

    def _cmd_resume(self) -> None:
        if not self.controller:
            print("Controller non inizializzato.")
            return
        self.controller.resume()
        print("▶️ Polling automatico RIPRESO. Controllo immediato pianificato.")

    def _cmd_appuntamenti(self) -> list[dict] | None:
        if not self.controller:
            print("Controller non inizializzato.")
            return None
        print("⏳ Recupero appuntamenti dal portale...")
        apps = self.controller.get_appuntamenti()
        if apps is None:
            print("❌ Impossibile recuperare gli appuntamenti (sessione non valida o errore di navigazione).")
            return None
        self._appuntamenti_cache = apps
        if not apps:
            print("ℹ️ Nessun appuntamento prenotato trovato nella sezione 'I miei appuntamenti'.")
            return apps

        print(f"\n📋 Appuntamenti trovati ({len(apps)}):")
        for idx, a in enumerate(apps, 1):
            cod = a.get("codice") or "n/d"
            print(f"  [{idx}] {a.get('prestazione', 'Visita')}")
            print(f"      📅 Data: {a.get('data_ora', 'n/d')} | Codice: {cod}")
            if a.get("azienda"):
                print(f"      🏥 Azienda: {a.get('azienda')}")
            luogo = [p for p in (a.get("presentarsi_in"), a.get("indirizzo"), a.get("comune")) if p]
            if luogo:
                print(f"      📍 Luogo: {' - '.join(luogo)}")
        print("\n💡 Usa 'ics <numero>' (es. 'ics 1') o 'ics all' per esportare i file calendario .ics.\n")
        return apps

    def _cmd_ics(self, arg: str) -> None:
        apps = self._appuntamenti_cache
        if not apps:
            apps = self._cmd_appuntamenti() or []
        if not apps:
            print("Nessun appuntamento disponibile per l'esportazione.")
            return

        dest_dir = Path("data/calendario")
        dest_dir.mkdir(parents=True, exist_ok=True)

        if not arg or arg.lower() in ("all", "tutti", "*"):
            print(f"Esportazione di {len(apps)} appuntamento/i in formato .ics...")
            saved = []
            for a in apps:
                info = appuntamento_to_info(a)
                out_path = write_ics(info, dest_dir)
                saved.append(out_path)
            print(f"✅ Generati {len(saved)} file calendario in: {dest_dir.resolve()}")
            for p in saved:
                print(f"  - {p.name}")
            return

        idx = safe_int(arg)
        if idx is not None:
            if 1 <= idx <= len(apps):
                a = apps[idx - 1]
                info = appuntamento_to_info(a)
                out_path = write_ics(info, dest_dir)
                print(f"✅ File calendario generato con successo:\n   {out_path.resolve()}\n")
                return

        print(f"[!] Parametro non valido: '{arg}'. Inserisci un indice tra 1 e {len(apps)} oppure 'all'.")

    def _cmd_ricette(self) -> list[dict] | None:
        if not self.controller:
            print("Controller non inizializzato.")
            return None
        print("⏳ Recupero ricette dal portale...")
        ric = self.controller.get_ricette()
        if ric is None:
            print("❌ Impossibile recuperare le ricette.")
            return None

        prenotabili = [r for r in ric if r.get("prenotabile")]
        prenotate = [r for r in ric if not r.get("prenotabile")]

        print(f"\n📋 Ricette trovate nel portale ({len(ric)} totali):")
        if prenotabili:
            print("\n  🟢 PRENOTABILI (Nuove visite da prenotare):")
            for idx, r in enumerate(prenotabili, 1):
                nre = r.get("nre", "n/d")
                print(f"    [{idx}] {r.get('prestazione', 'N/D')}")
                print(f"        NRE: {nre} | Data ricetta: {r.get('data_ricetta', 'n/d')}")
        else:
            print("\n  🟢 PRENOTABILI: Nessuna ricetta da prenotare.")

        if prenotate:
            print("\n  🔵 GIÀ PRENOTATE (Con appuntamento futuro fissato):")
            for idx, r in enumerate(prenotate, 1):
                nre = r.get("nre", "n/d")
                print(f"    [{idx}] {r.get('prestazione', 'N/D')}")
                print(f"        NRE: {nre} | Data attuale: {r.get('data_attuale', 'n/d')}")
        print("")
        return ric

    def _cmd_anticipa(self, arg: str) -> None:
        if not self.controller:
            print("Controller non inizializzato.")
            return
        apps = self._appuntamenti_cache
        if not apps:
            apps = self._cmd_appuntamenti() or []
        if not apps:
            print("Nessun appuntamento esistente trovato da anticipare.")
            return

        target_idx = None
        arg_idx = safe_int(arg)
        if arg_idx is not None and 1 <= arg_idx <= len(apps):
            target_idx = arg_idx - 1
        else:
            print("\nScegli quale appuntamento desideri anticipare:")
            for i, a in enumerate(apps, 1):
                print(f"  [{i}] {a.get('prestazione')} (data: {a.get('data_ora', 'n/d')})")
            scelta = input(f"Seleziona numero [1-{len(apps)}]: ").strip()
            scelta_idx = safe_int(scelta)
            if scelta_idx is not None and 1 <= scelta_idx <= len(apps):
                target_idx = scelta_idx - 1

        if target_idx is None:
            print("Operazione annullata.")
            return

        target_app = apps[target_idx]
        prestazione = target_app.get("prestazione", "Visita")
        data_ora = target_app.get("data_ora", "")
        codice = target_app.get("codice", "")

        from services.deadline import check_reschedule_deadline, get_reschedule_min_working_days
        min_days = get_reschedule_min_working_days()
        is_too_late, app_dt, dl_dt, dl_msg = check_reschedule_deadline(data_ora, min_working_days=min_days)
        if is_too_late:
            print(f"\n⚠️ Impossibile avviare il monitor di anticipo:")
            print(f"   {dl_msg}")
            print(f"   Mancano meno di {min_days} giorni lavorativi all'appuntamento.")
            print(f"   La riprogrammazione comporterebbe l'addebito della penale (malus ticket).\n")
            return

        criteri = self._ask_criteri(default_data_a_hint=f"prima del {data_ora or 'limite attuale'}")

        mid = self.controller.aggiungi_monitor(
            tipo="reschedule",
            descrizione=prestazione,
            nre=codice,
            criteri=criteri,
            data_attuale=data_ora,
        )
        if mid:
            print(f"\n✅ Monitor di anticipo avviato con ID: {mid}")
            limite_txt = (
                f"tra {criteri['data_dal'] or 'oggi'} e {criteri['data_a'] or data_ora}"
                if (criteri.get("data_dal") or criteri.get("data_a"))
                else f"precedenti al: {data_ora}"
            )
            print(f"   Ricerca attiva per trovare date {limite_txt} (Province: {', '.join(criteri.get('province', []))})\n")
        else:
            print("❌ Errore durante l'avvio del monitor di anticipo.")

    def _ask_criteri(self, default_data_a: str = "", default_data_a_hint: str = "senza limite") -> dict:
        """Prompt user for criteria: province, start date, and end date."""
        prov_disp = self.controller.lista_province() if self.controller else []
        prov_def = (
            (self.controller.criteri_default() or {}).get("province", ["MILANO CITTA'"])
            if self.controller
            else ["MILANO CITTA'"]
        )
        def_str = ", ".join(prov_def) if isinstance(prov_def, list) else str(prov_def)

        if prov_disp:
            print(f"\nProvince disponibili: {', '.join(prov_disp)}")
        prov_inp = input(f"Province da cercare separate da virgola [{def_str}]: ").strip()
        if prov_inp:
            scelte_prov = [p.strip().upper() for p in prov_inp.split(",") if p.strip()]
        else:
            scelte_prov = prov_def if isinstance(prov_def, list) else [prov_def]

        # Dates range
        data_dal = input("Data 'A partire da' (DD/MM/YYYY o invio per oggi): ").strip()
        data_a_inp = input(f"Data 'Entro il' (DD/MM/YYYY o invio per {default_data_a_hint}): ").strip()
        data_a = data_a_inp or default_data_a

        # Time slot preference
        print("\nFasce orarie disponibili:")
        print("  [1] Qualsiasi orario / Salta (default)")
        print("  [2] Solo Mattina (07:00 - 13:00)")
        print("  [3] Solo Pomeriggio (13:00 - 19:00)")
        print("  [4] Tardo Pomeriggio / Sera (17:00 - 23:59)")
        print("  [5] Pausa Pranzo (12:00 - 15:00)")
        print("  [6] Personalizzata (es. 09:00-12:00)")
        fascia_scelta = input("Fascia oraria preferita [1-6 o invio per saltare/qualsiasi]: ").strip()
        fascia_map = {
            "2": "mattina",
            "3": "pomeriggio",
            "4": "sera",
            "5": "pranzo",
        }
        if fascia_scelta == "6":
            fascia = input("Inserisci fascia personalizzata (es. 09:00-12:00): ").strip()
        else:
            fascia = fascia_map.get(fascia_scelta, "")

        return {
            "province": scelte_prov,
            "data_dal": data_dal,
            "data_a": data_a,
            "fascia": fascia,
        }

    def _cmd_monitora(self) -> None:
        if not self.controller:
            print("Controller non inizializzato.")
            return

        print("\n" + "=" * 66)
        print("  PROCEDURA GUIDATA NUOVO MONITORAGGIO")
        print("=" * 66)
        print("  [1] Da ricetta da prenotare (nuova prenotazione)")
        print("  [2] Da appuntamento già fissato (cerca anticipo)")
        print("  [3] Inserimento manuale (descrizione libera)")
        print("=" * 66)
        tipo_scelta = input("Cosa vuoi monitorare? [1/2/3]: ").strip()

        if tipo_scelta == "1":
            ric = self._cmd_ricette()
            if not ric:
                return
            prenotabili = [r for r in ric if r.get("prenotabile")]
            if not prenotabili:
                print("Nessuna ricetta prenotabile trovata.")
                return
            scelta = input(f"Seleziona la ricetta [1-{len(prenotabili)}]: ").strip()
            idx = safe_int(scelta)
            if idx is None or not (1 <= idx <= len(prenotabili)):
                print("Scelta non valida.")
                return
            r = prenotabili[idx - 1]
            desc = r.get("prestazione", "")
            nre = r.get("nre", "")
            data_ricetta = r.get("data_ricetta", "")
            tipo = "new"
            data_attuale = ""
        elif tipo_scelta == "2":
            self._cmd_anticipa("")
            return
        elif tipo_scelta == "3":
            desc = input("Descrizione visita (es. VISITA CARDIOLOGICA): ").strip()
            if not desc:
                print("Descrizione obbligatoria.")
                return
            nre = input("Codice NRE (opzionale, premi invio per saltare): ").strip()
            tipo = "new"
            data_ricetta = ""
            data_attuale = ""
        else:
            print("Operazione annullata.")
            return

        criteri = self._ask_criteri(default_data_a_hint="senza limite")

        mid = self.controller.aggiungi_monitor(
            tipo=tipo,
            descrizione=desc,
            nre=nre,
            criteri=criteri,
            data_ricetta=data_ricetta,
            data_attuale=data_attuale,
        )
        if mid:
            print(f"\n✅ Monitor aggiunto con successo! ID: {mid}\n")
        else:
            print("\n❌ Creazione monitor non riuscita.\n")

    def _cmd_blacklist(self, arg: str) -> None:
        if not self.controller:
            print("Controller non inizializzato.")
            return
        monitors = self.controller.monitors_attivi()
        if not monitors:
            print("Nessun monitor attivo configurato.")
            return

        if arg == "clear" or arg == "svuota":
            count = 0
            for m in monitors:
                count += self.controller.svuota_blacklist(m.get("id", ""))
            print(f"✅ Blacklist svuotata ({count} voci rimosse in totale).")
            return

        print("\n📋 Voci in Blacklist per i monitor attivi:")
        has_entries = False
        for m in monitors:
            mid = m.get("id", "")
            bl = self.controller.get_blacklist(mid)
            if bl:
                has_entries = True
                print(f"  • Monitor [{mid}] ({len(bl)} voci):")
                for e in bl:
                    if e.get("block_day"):
                        slot_desc = f"🗓 {e.get('date_str')} [INTERA GIORNATA BLOCCATA]"
                    else:
                        slot_desc = e.get("descrizione") or e.get("slot") or f"{e.get('date_str')} ore {e.get('time_str')}"
                    print(f"      - {slot_desc}")
        if not has_entries:
            print("  (Nessuna disponibilità attualmente in blacklist).")
        print("\n💡 Usa 'blacklist clear' per svuotare tutte le proposte rifiutate.\n")
