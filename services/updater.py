"""Update checking and startup welcome messaging for Visita-Subito.

Queries the GitHub Releases API (https://api.github.com/repos/metagliatore/visita-subito/releases/latest)
with a fast, non-blocking timeout using only Python standard library utilities.
Formats friendly welcome messages for logs, CLI terminal, and Telegram.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import Config

log = logging.getLogger(__name__)

APP_NAME = "Visita-Subito"
APP_VERSION = "0.2.0"
APP_DESCRIPTION = "Il tuo assistente personale per monitorare e anticipare visite ed esami sanitari in Lombardia"
GITHUB_REPO = "metagliatore/visita-subito"
GITHUB_URL = f"https://github.com/{GITHUB_REPO}"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
GITHUB_LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_TAGS_API = f"https://api.github.com/repos/{GITHUB_REPO}/tags"
GITHUB_ISSUES_URL = f"https://github.com/{GITHUB_REPO}/issues"
GITHUB_NEW_ISSUE_URL = f"https://github.com/{GITHUB_REPO}/issues/new"

# Singleton cache for startup check
_cached_update_info: UpdateInfo | None = None
_banner_already_logged: bool = False


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str | None = None
    has_update: bool = False
    release_name: str | None = None
    release_url: str | None = None
    release_tag: str | None = None
    published_at: str | None = None
    checked_at: float = 0.0
    error: str | None = None
    git_ref: str | None = None


def parse_semver(version_str: str) -> tuple[int, ...]:
    """Parse semver string like 'v0.2.0', '0.2.1', '1.0' into a tuple of ints."""
    clean = str(version_str or "").strip().lstrip("vV")
    m = re.match(r"^(\d+(?:\.\d+)*)", clean)
    if not m:
        return (0, 0, 0)
    parts = [int(p) for p in m.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer_version(candidate: str, current: str) -> bool:
    """Return True if candidate version is strictly greater than current version."""
    try:
        return parse_semver(candidate) > parse_semver(current)
    except Exception:
        return False


def get_git_version() -> str | None:
    """Attempt to get git describe version if running inside a git repository."""
    try:
        res = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if res.returncode == 0:
            out = res.stdout.strip()
            if out:
                return out
    except Exception:
        pass
    return None


def is_update_check_enabled(cfg: Config | None = None) -> bool:
    """Check whether GitHub update checks are enabled via env or config."""
    # 1. Environment variable override
    env_val = os.environ.get("CHECK_UPDATES")
    if env_val is not None:
        return env_val.strip().lower() in ("1", "true", "yes", "on")
    # 2. Config file settings
    if cfg and hasattr(cfg, "settings") and isinstance(cfg.settings, dict):
        app_cfg = cfg.settings.get("app", {})
        if "check_updates" in app_cfg:
            return bool(app_cfg["check_updates"])
    return True


def is_tg_startup_notification_enabled(cfg: Config | None = None) -> bool:
    """Check whether Telegram startup welcome notification is enabled via env or config."""
    env_val = os.environ.get("TG_STARTUP_NOTIFICATION")
    if env_val is not None:
        return env_val.strip().lower() in ("1", "true", "yes", "on")
    if cfg and hasattr(cfg, "settings") and isinstance(cfg.settings, dict):
        app_cfg = cfg.settings.get("app", {})
        if "tg_startup_notification" in app_cfg:
            return bool(app_cfg["tg_startup_notification"])
    return True


def check_for_updates(
    current_version: str = APP_VERSION,
    repo: str = GITHUB_REPO,
    timeout: float = 3.5,
    force: bool = False,
    cfg: Config | None = None,
) -> UpdateInfo:
    """Query GitHub releases API to check for updates.

    Cached across calls unless `force=True`.
    Fails safely and quickly without raising exceptions if offline or rate-limited.
    """
    global _cached_update_info
    if not force and _cached_update_info is not None:
        return _cached_update_info

    git_ref = get_git_version()
    if not is_update_check_enabled(cfg):
        info = UpdateInfo(
            current_version=current_version,
            git_ref=git_ref,
            checked_at=time.time(),
            error="disabilitato da configurazione",
        )
        _cached_update_info = info
        return info

    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {
        "User-Agent": f"visita-subito/{current_version}",
        "Accept": "application/vnd.github.v3+json",
    }
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            tag = str(data.get("tag_name") or "").strip()
            name = str(data.get("name") or "").strip() or tag
            html_url = str(data.get("html_url") or "").strip() or f"https://github.com/{repo}/releases/tag/{tag}"
            published = str(data.get("published_at") or "").strip()

            clean_tag = tag.lstrip("vV")
            newer = is_newer_version(clean_tag, current_version)

            info = UpdateInfo(
                current_version=current_version,
                latest_version=clean_tag or tag,
                has_update=newer,
                release_name=name,
                release_url=html_url,
                release_tag=tag,
                published_at=published,
                checked_at=time.time(),
                git_ref=git_ref,
            )
            _cached_update_info = info
            return info
    except urllib.error.HTTPError as he:
        # If 404, repository might have tags but no formal releases yet; try tags API
        if he.code == 404:
            try:
                tags_url = f"https://api.github.com/repos/{repo}/tags"
                tags_req = urllib.request.Request(tags_url, headers=headers)
                with urllib.request.urlopen(tags_req, timeout=timeout) as resp_tags:
                    tags_data = json.loads(resp_tags.read().decode("utf-8"))
                    if isinstance(tags_data, list) and len(tags_data) > 0:
                        first_tag = str(tags_data[0].get("name") or "").strip()
                        clean_tag = first_tag.lstrip("vV")
                        newer = is_newer_version(clean_tag, current_version)
                        info = UpdateInfo(
                            current_version=current_version,
                            latest_version=clean_tag or first_tag,
                            has_update=newer,
                            release_name=first_tag,
                            release_url=f"https://github.com/{repo}/releases/tag/{first_tag}",
                            release_tag=first_tag,
                            checked_at=time.time(),
                            git_ref=git_ref,
                        )
                        _cached_update_info = info
                        return info
            except Exception:
                pass
        err_msg = f"HTTP {he.code}"
        log.debug("Controllo aggiornamenti GitHub fallito: %s", he)
    except Exception as e:
        err_msg = str(e)
        log.debug("Controllo aggiornamenti GitHub fallito: %s", e)

    info = UpdateInfo(
        current_version=current_version,
        git_ref=git_ref,
        checked_at=time.time(),
        error=err_msg,
    )
    _cached_update_info = info
    return info


def get_cached_or_check_updates(cfg: Config | None = None) -> UpdateInfo:
    """Return cached update info or perform check if not yet cached."""
    global _cached_update_info
    if _cached_update_info is not None:
        return _cached_update_info
    return check_for_updates(cfg=cfg)


def format_log_banner(info: UpdateInfo) -> str:
    """Generate high-visibility ASCII banner for stdout and logging."""
    width = 72
    sep = "=" * width
    subsep = "-" * width
    lines = [
        sep,
        f"  👋  Benvenuto su {APP_NAME}! (v{info.current_version})",
        f"  🏥  {APP_DESCRIPTION}",
        f"  🔗  GitHub: {GITHUB_URL}",
        "  ✨  Verifica sempre di usare la build più recente del progetto!",
        subsep,
    ]
    if info.has_update and info.latest_version:
        release_url = info.release_url or f"{GITHUB_RELEASES_URL}/tag/{info.release_tag or ('v' + info.latest_version)}"
        lines.append(f"  🚀  NUOVA VERSIONE DISPONIBILE: v{info.latest_version} (installata: v{info.current_version})")
        lines.append(f"  📥  Scarica l'aggiornamento / Note: {release_url}")
    elif info.error:
        lines.append(f"  ℹ️   Controllo aggiornamenti GitHub non disponibile ({info.error})")
    else:
        lines.append(f"  ✅  Visita-Subito è aggiornato all'ultima versione (v{info.current_version})")
    lines.append(sep)
    return "\n".join(lines)


def format_cli_welcome(info: UpdateInfo) -> str:
    """Generate welcome header for CLI Terminal UI."""
    width = 66
    sep = "=" * width
    subsep = "-" * width
    lines = [
        sep,
        f"  👋  Benvenuto su {APP_NAME}! (v{info.current_version})",
        "      Controllo Interattivo da Terminale",
        sep,
        f"  🔗  GitHub: {GITHUB_URL}",
        "  💡  Mantieni sempre aggiornata la tua versione!",
        subsep,
    ]
    if info.has_update and info.latest_version:
        release_url = info.release_url or f"{GITHUB_RELEASES_URL}/tag/{info.release_tag or ('v' + info.latest_version)}"
        lines.append(f"  🚀  Aggiornamento disponibile: v{info.latest_version} (attuale: v{info.current_version})")
        lines.append(f"  📥  Note di rilascio: {release_url}")
    elif info.error:
        lines.append("  ℹ️   Controllo aggiornamenti non disponibile al momento.")
    else:
        lines.append(f"  ✅  Applicativo aggiornato all'ultima versione (v{info.current_version}).")
    lines.append(sep)
    return "\n".join(lines)


def format_telegram_welcome(info: UpdateInfo) -> tuple[str, list[list[tuple[str, str]]]]:
    """Format friendly HTML message and inline URL buttons for Telegram."""
    lines = [
        f"👋 <b>Benvenuto su {APP_NAME}!</b> (<code>v{info.current_version}</code>)",
        "",
        f"🏥 <i>{APP_DESCRIPTION}</i>",
        "",
        f"🔗 <b>GitHub:</b> <a href=\"{GITHUB_URL}\">{GITHUB_REPO}</a>",
        "💡 <i>Consiglio:</i> mantieni sempre aggiornato l'applicativo con le ultime release per la piena compatibilità con il portale regionale!",
        "",
    ]
    buttons: list[list[tuple[str, str]]] = []

    if info.has_update and info.latest_version:
        release_url = info.release_url or f"{GITHUB_RELEASES_URL}/tag/{info.release_tag or ('v' + info.latest_version)}"
        lines.append(f"🚀 <b>Nuova versione disponibile: <code>v{info.latest_version}</code>!</b>")
        lines.append(f"📥 <a href=\"{release_url}\">Scarica l'aggiornamento / Note di rilascio</a>")
        buttons.append([("📥 Scarica v" + info.latest_version, release_url)])
    elif info.error:
        lines.append("ℹ️ <i>Controllo aggiornamenti su GitHub al momento non disponibile.</i>")
    else:
        lines.append(f"✅ <i>Stai utilizzando l'ultima versione disponibile ({info.current_version}).</i>")

    buttons.append([("⭐ Progetto su GitHub", GITHUB_URL)])
    return "\n".join(lines), buttons


def format_issue_guide(version: str | None = None) -> tuple[str, list[list[tuple[str, str]]]]:
    """Format guidance message and interactive buttons for reporting a bug on GitHub."""
    v = version or APP_VERSION
    lines = [
        "🐛 <b>Segnala un Errore (Bug)</b>",
        "",
        "Hai riscontrato un problema o un comportamento anomalo?",
        "Puoi aprire una segnalazione su GitHub per aiutarci a risolverlo:",
        "",
        "📋 <b>Cosa indicare nella segnalazione:</b>",
        f"• <b>Versione:</b> <code>v{v}</code>",
        "• <b>Ambiente:</b> Docker, Linux, Windows o macOS",
        "• <b>Accesso:</b> SPID (Poste / Sielte) o CIE",
        "• <b>Descrizione:</b> cosa stavi facendo e le eventuali righe di errore/log",
        "",
        "🔒 <b>Attenzione alla privacy:</b>",
        "<i>Non condividere mai dati sensibili come Codice Fiscale, password, credenziali SPID o token Telegram.</i>",
    ]
    buttons = [
        [("🐛 Apri segnalazione su GitHub", GITHUB_NEW_ISSUE_URL)],
        [("📋 Consulta problemi noti", GITHUB_ISSUES_URL)],
    ]
    return "\n".join(lines), buttons


def check_and_log_startup_banner(cfg: Config | None = None, logger: logging.Logger | None = None) -> UpdateInfo:
    """Check for updates and log the visible banner once per process run."""
    global _banner_already_logged
    info = get_cached_or_check_updates(cfg=cfg)
    if not _banner_already_logged:
        banner = format_log_banner(info)
        target_log = logger or log
        for line in banner.split("\n"):
            target_log.info(line)
        _banner_already_logged = True
    return info

