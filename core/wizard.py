"""Interactive guided procedure (Wizard) for initial .env file setup."""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

from core.config import BASE_DIR, BUNDLE_DIR, ENV_FILE, reload_dotenv, get_base_dir, get_bundle_dir

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def prompt_input(message: str, default: str = "", secret: bool = False) -> str:
    """Prompts user for text input, with optional default value."""
    prompt_lbl = f"{message} [{default}]: " if default else f"{message}: "
    if secret:
        try:
            val = getpass.getpass(prompt_lbl).strip()
        except Exception:
            val = input(prompt_lbl).strip()
    else:
        val = input(prompt_lbl).strip()
    return val if val else default


def prompt_choice(title: str, options: list[tuple[str, str]], default: int = 1) -> str:
    """Presents a numbered multiple-choice menu and returns the selected value."""
    print(f"\n{title}")
    for i, (label, _) in enumerate(options, 1):
        indicator = "->" if i == default else "  "
        print(f"  {indicator} [{i}] {label}")
    while True:
        choice = input(f"\nScegli un'opzione [{default}]: ").strip()
        if not choice:
            return options[default - 1][1]
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1][1]
        print(f"[!] Scelta non valida. Inserisci un numero tra 1 e {len(options)}.")


def populate_env_content(template_content: str, values: dict[str, str]) -> str:
    """Substitutes variable values into the .env.example template.

    Preserves comments, empty lines, and original formatting.
    Appends any missing keys to the end.
    """
    lines = template_content.splitlines()
    output_lines = []
    used_keys = set()

    for line in lines:
        stripped = line.strip()
        # Recognizes lines like KEY=val or # KEY=val
        key_candidate = None
        if "=" in stripped and not stripped.startswith("##"):
            raw_key = stripped.split("=", 1)[0].strip()
            # If the line is commented with a single # (e.g. "# CIE_USERNAME="), check the key
            clean_key = raw_key.lstrip("#").strip()
            if clean_key in values:
                key_candidate = clean_key

        if key_candidate and key_candidate in values:
            val = values[key_candidate]
            output_lines.append(f"{key_candidate}={val}")
            used_keys.add(key_candidate)
        else:
            output_lines.append(line)

    # Append any missing keys from template
    missing_keys = [k for k in values if k not in used_keys]
    if missing_keys:
        output_lines.append("")
        output_lines.append("# --- Additional variables configured by wizard ---")
        for k in missing_keys:
            output_lines.append(f"{k}={values[k]}")

    return "\n".join(output_lines) + "\n"


def run_wizard(env_path: Path | None = None, example_path: Path | None = None) -> bool:
    """Runs interactive wizard to collect parameters and generate .env."""
    try:
        return _run_wizard_interactive(env_path=env_path, example_path=example_path)
    except (KeyboardInterrupt, EOFError):
        print("\n\n[!] Configurazione interrotta dall'utente.\n")
        return False


def _run_wizard_interactive(env_path: Path | None = None, example_path: Path | None = None) -> bool:
    target_env = env_path or (get_base_dir() / ".env")
    template_file = example_path or (get_bundle_dir() / ".env.example")
    if not template_file.exists():
        template_file = get_base_dir() / ".env.example"

    template_content = ""
    if template_file.exists():
        try:
            template_content = template_file.read_text(encoding="utf-8")
        except Exception:
            template_content = ""

    print("\n" + "=" * 66)
    print("  Fascicolo Sanitario Regione Lombardia - Prenotazioni")
    print("           Procedura di Configurazione Iniziale (.env)")
    print("=" * 66)
    print("Questo assistente ti aiuterà a configurare il bot Telegram e")
    print("il metodo di accesso al fascicolo. Premi INVIO per confermare i default.\n")

    values: dict[str, str] = {}

    # 1. Telegram configuration
    print("------------------------------------------------------------------")
    print("  1. BOT TELEGRAM")
    print("  (Serve per ricevere notifiche e gestire le visite in chat)")
    print("  Se non ne hai uno, crealo gratuitamente su Telegram con @BotFather")
    print("------------------------------------------------------------------")
    while True:
        tg_token = prompt_input("Token del Bot Telegram (es. 123456789:ABCdefGhI...)")
        if tg_token:
            if tg_token.startswith("bot") and len(tg_token) > 4 and tg_token[3].isdigit():
                tg_token = tg_token[3:]
            values["TG_TOKEN"] = tg_token
            break
        print("[!] Il token del bot Telegram è obbligatorio per utilizzare il servizio.")

    while True:
        tg_chat_id = prompt_input("Il tuo Chat ID Telegram (trovalo inviando un messaggio a @userinfobot)")
        if tg_chat_id:
            values["TG_CHAT_ID"] = tg_chat_id
            break
        print("[!] Il Chat ID Telegram è obbligatorio per ricevere le notifiche.")

    tg_sec = prompt_input("Chat ID secondario (opzionale, es. per un familiare, o lascia vuoto)", default="")
    values["TG_CHAT_ID_SECONDARY"] = tg_sec

    # 2. Authentication method
    print("\n------------------------------------------------------------------")
    print("  2. METODO DI AUTENTICAZIONE")
    print("  Come vuoi che il servizio acceda al Fascicolo Sanitario?")
    print("------------------------------------------------------------------")
    auth_options = [
        ("SPID SielteID (Consigliato: automatizzato con notifica push/OTP)", "spid_sielte"),
        ("SPID con altro fornitore (Poste, Aruba, InfoCert, Lepida, Namirial...)", "spid_other"),
        ("CIE con Credenziali (App CieID Livello 2: CF/Numero CIE + Password)", "cie_app"),
        ("CIE con QR Code diretto (Nessuna password salvata: invia il QR su Telegram)", "cie_qr"),
        ("Manuale (Apre una finestra di Chrome per accedere a mano)", "manual"),
    ]
    scelta_auth = prompt_choice("Seleziona il metodo di accesso:", auth_options, default=1)

    if scelta_auth == "spid_sielte":
        values["AUTH_METHOD"] = "spid"
        values["SPID_PROVIDER"] = "sielte"
        values["SPID_USERNAME"] = prompt_input("Username SielteID / Codice Fiscale")
        values["SPID_PASSWORD"] = prompt_input("Password SielteID", secret=True)
        # Backwards compatibility
        values["SIELTE_USERNAME"] = values["SPID_USERNAME"]
        values["SIELTE_PASSWORD"] = values["SPID_PASSWORD"]

        otp_opts = [
            ("Notifica push sull'app MySielteID (Consigliata)", "notifica"),
            ("Codice OTP generato da app o via SMS", "otp"),
        ]
        values["SPID_OTP_MODE"] = prompt_choice("Modalità di approvazione secondo fattore SPID:", otp_opts, default=1)
        values["SIELTE_OTP_MODE"] = values["SPID_OTP_MODE"]

    elif scelta_auth == "spid_other":
        values["AUTH_METHOD"] = "spid"
        prov_opts = [
            ("Poste Italiane (PosteID)", "poste"),
            ("Aruba ID", "aruba"),
            ("InfoCert ID", "infocert"),
            ("Lepida ID", "lepida"),
            ("Namirial ID", "namirial"),
            ("TIM id", "tim"),
            ("Register.it (SpidItalia)", "register"),
            ("Intesi Group", "intesigroup"),
            ("TeamSystem ID", "teamsystem"),
            ("EtnaID", "etna"),
        ]
        values["SPID_PROVIDER"] = prompt_choice("Seleziona il tuo gestore SPID:", prov_opts, default=1)
        values["SPID_USERNAME"] = prompt_input(f"Username {values['SPID_PROVIDER'].upper()}")
        values["SPID_PASSWORD"] = prompt_input("Password", secret=True)
        values["SPID_OTP_MODE"] = "notifica"

    elif scelta_auth == "cie_app":
        values["AUTH_METHOD"] = "cie"
        values["CIE_MODE"] = "app"
        values["CIE_USERNAME"] = prompt_input("Codice Fiscale o Numero CIE (es. CA00000AA)")
        values["CIE_PASSWORD"] = prompt_input("Password delle credenziali CIE", secret=True)

    elif scelta_auth == "cie_qr":
        values["AUTH_METHOD"] = "cie"
        values["CIE_MODE"] = "qr"
        values["CIE_USERNAME"] = ""
        values["CIE_PASSWORD"] = ""
        print("[i] In modalità QR Code diretto, ad ogni rinnovo sessione riceverai su Telegram")
        print("    la foto del QR Code e il pulsante per aprire direttamente l'app CieID.")

    elif scelta_auth == "manual":
        values["AUTH_METHOD"] = "manual"
        print("[i] In modalità Manuale, il browser Chrome si aprirà visibile per consentire il login a mano.")

    # 3. Optional search settings
    print("\n------------------------------------------------------------------")
    print("  3. IMPOSTAZIONI DI RICERCA (Parametri avanzati)")
    print("------------------------------------------------------------------")
    strat_opts = [
        ("first_match (Consigliata: prenota subito il primo slot libero disponibile)", "first_match"),
        ("best_match (Scansiona tutte le province alla ricerca della data più vicina)", "best_match"),
    ]
    values["SEARCH_STRATEGY"] = prompt_choice("Strategia di ricerca multi-provincia:", strat_opts, default=1)

    minuti = prompt_input("Intervallo di controllo delle disponibilità (in minuti)", default="5")
    try:
        sec = int(minuti) * 60
        values["POLL_INTERVAL_SECONDS"] = str(max(60, sec))
    except ValueError:
        values["POLL_INTERVAL_SECONDS"] = "300"

    # Defaults for system parameters
    values.setdefault("MAX_LOGIN_RETRIES", "3")
    values.setdefault("APPROVAL_TIMEOUT_SECONDS", "600")
    values.setdefault("RESCHEDULE_WAIT_TIMEOUT", "30")
    values.setdefault("DEBUG_DUMP_DIR", "data/debug")

    # Write .env file
    env_content = populate_env_content(template_content, values)
    target_env.parent.mkdir(parents=True, exist_ok=True)
    target_env.write_text(env_content, encoding="utf-8")

    # Immediately reload environment variables into current process
    reload_dotenv(target_env)

    print("\n" + "=" * 66)
    print("  [OK] CONFIGURAZIONE COMPLETATA CON SUCCESSO!")
    print(f"  I parametri sono stati salvati in: {target_env.resolve()}")
    print("  Puoi rieseguire questa procedura in ogni momento con: --wizard")
    print("=" * 66 + "\n")
    return True


def check_or_run_wizard(env_path: Path | None = None, force: bool = False) -> bool:
    """Checks if .env exists or if --wizard is forced. If necessary, executes the wizard."""
    target_env = env_path or (get_base_dir() / ".env")

    if force:
        return run_wizard(target_env)

    # If .env exists and size > 0, all good
    if target_env.exists() and target_env.stat().st_size > 0:
        return True

    # If stdin is not an interactive terminal (e.g. non-interactive Docker / nohup)
    if not sys.stdin.isatty():
        print("[!] File .env non trovato. Per la prima configurazione, esegui il programma da un terminale "
              "interattivo (es. python main.py) oppure copia .env.example in .env.", file=sys.stderr)
        return False

    return run_wizard(target_env)
