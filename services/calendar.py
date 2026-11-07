"""Calendar (.ics) generation for booked appointments."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from services.appuntamento import AppuntamentoInfo
from services.deadline import parse_appuntamento_datetime

log = logging.getLogger(__name__)


def _fold(line: str, limit: int = 75) -> str:
    """Fold long lines at limit octets per RFC 5545 section 3.1."""
    if len(line.encode("utf-8")) <= limit:
        return line
    parts = []
    current = ""
    for ch in line:
        candidate = current + ch
        if len(candidate.encode("utf-8")) > limit:
            parts.append(current)
            current = " " + ch
        else:
            current = candidate
    if current:
        parts.append(current)
    return "\r\n".join(parts)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def _parse_old_dt(vecchio_appuntamento: Any) -> datetime | None:
    """Extract datetime from various representations of an existing appointment."""
    if not vecchio_appuntamento:
        return None
    if isinstance(vecchio_appuntamento, datetime):
        return vecchio_appuntamento
    if hasattr(vecchio_appuntamento, "datetime") and getattr(vecchio_appuntamento, "datetime"):
        return getattr(vecchio_appuntamento, "datetime")
    if isinstance(vecchio_appuntamento, dict):
        d_val = vecchio_appuntamento.get("data_ora") or vecchio_appuntamento.get("datetime")
        if d_val:
            return _parse_old_dt(d_val)
    if isinstance(vecchio_appuntamento, str):
        try:
            return parse_appuntamento_datetime(vecchio_appuntamento)
        except Exception:
            return None
    return None


def build_cancellation_event(
    old_dt: datetime,
    dt_stamp: str,
    summary: str = "Visita medica",
    uid: Optional[str] = None,
    durata_minuti: int = 30,
    new_dt: Optional[datetime] = None,
) -> list[str]:
    """Build VEVENT lines for a cancelled previous appointment (RFC 5545)."""
    old_end = old_dt + timedelta(minutes=durata_minuti)
    event_uid = uid or f"prenotazione-{int(old_dt.timestamp())}@fascicolosanitario.lombardia.it"
    clean_summary = summary.replace(" [SPOSTATA / ANNULLATA]", "").replace(" [ANNULLATA]", "")
    new_dt_str = new_dt.strftime("%d/%m/%Y alle %H:%M") if new_dt else "nuova data"

    lines = [
        "BEGIN:VEVENT",
        f"UID:{event_uid}",
        f"DTSTAMP:{dt_stamp}",
        f"DTSTART:{_fmt_dt(old_dt)}",
        f"DTEND:{_fmt_dt(old_end)}",
        "STATUS:CANCELLED",
        "SEQUENCE:1",
        _fold(f"SUMMARY:{clean_summary} [SPOSTATA / ANNULLATA]"),
        _fold(f"DESCRIPTION:Appuntamento precedente annullato o anticipato/posticipato al {new_dt_str} con VisitaSubito."),
        "END:VEVENT",
    ]
    return lines


def build_ics(
    info: AppuntamentoInfo,
    uid: Optional[str] = None,
    durata_minuti: int = 30,
    vecchio_appuntamento: Optional[Any] = None,
    vecchio_uid: Optional[str] = None,
) -> str:
    """Generate RFC 5545 iCalendar format text.

    If `vecchio_appuntamento` is provided (e.g. when rescheduling an appointment),
    an initial VEVENT with STATUS:CANCELLED and SEQUENCE:1 is included for the previous
    date/time, instructing calendar applications to remove or update the old appointment.
    """
    dt_start = info.datetime or datetime.now()
    dt_end = dt_start + timedelta(minutes=durata_minuti)
    dt_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    event_uid = uid or f"prenotazione-{int(dt_start.timestamp())}@fascicolosanitario.lombardia.it"

    # Assemble summary
    summary = f"Visita: {info.prestazione}" if info.prestazione else "Visita medica"

    # Assemble location
    loc_parts = [p for p in (info.azienda, info.presentarsi_in, info.indirizzo) if p]
    location = ", ".join(loc_parts)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Fascicolo Sanitario Regione Lombardia//Prenotazioni Bot//IT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    # If rescheduling, add cancellation block for previous appointment
    old_dt = _parse_old_dt(vecchio_appuntamento)
    if old_dt and old_dt != dt_start:
        old_lines = build_cancellation_event(
            old_dt=old_dt,
            dt_stamp=dt_stamp,
            summary=summary,
            uid=vecchio_uid,
            durata_minuti=durata_minuti,
            new_dt=dt_start,
        )
        lines.extend(old_lines)

    # Confirmed new event
    lines.extend([
        "BEGIN:VEVENT",
        f"UID:{event_uid}",
        f"DTSTAMP:{dt_stamp}",
        f"DTSTART:{_fmt_dt(dt_start)}",
        f"DTEND:{_fmt_dt(dt_end)}",
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
        _fold(f"SUMMARY:{summary}"),
    ])
    if location:
        lines.append(_fold(f"LOCATION:{location}"))
    desc_parts = []
    if info.codice:
        desc_parts.append(f"Codice prenotazione: {info.codice}")
    if info.note:
        notes = "\\n".join(f"- {n}" for n in info.note)
        desc_parts.append(f"Note:\\n{notes}")
    if desc_parts:
        full_desc = "\\n\\n".join(desc_parts)
        lines.append(_fold(f"DESCRIPTION:{full_desc}"))
    lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def build_cancellation_ics(
    old_dt_or_info: Any,
    uid: Optional[str] = None,
    prestazione: str = "",
    nuova_data: Optional[datetime | str] = None,
    durata_minuti: int = 30,
) -> str:
    """Generate a dedicated RFC 5545/5546 cancellation iCalendar text (METHOD:CANCEL)."""
    old_dt = _parse_old_dt(old_dt_or_info) or datetime.now()
    new_dt = _parse_old_dt(nuova_data) if nuova_data else None
    dt_end = old_dt + timedelta(minutes=durata_minuti)
    dt_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    event_uid = uid or f"prenotazione-{int(old_dt.timestamp())}@fascicolosanitario.lombardia.it"
    summary = f"Visita: {prestazione}" if prestazione else "Visita medica"
    new_dt_str = new_dt.strftime("%d/%m/%Y alle %H:%M") if new_dt else "nuova data"

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Fascicolo Sanitario Regione Lombardia//Prenotazioni Bot//IT",
        "CALSCALE:GREGORIAN",
        "METHOD:CANCEL",
        "BEGIN:VEVENT",
        f"UID:{event_uid}",
        f"DTSTAMP:{dt_stamp}",
        f"DTSTART:{_fmt_dt(old_dt)}",
        f"DTEND:{_fmt_dt(dt_end)}",
        "STATUS:CANCELLED",
        "SEQUENCE:1",
        _fold(f"SUMMARY:{summary} [ANNULLATA]"),
        _fold(f"DESCRIPTION:Appuntamento annullato o anticipato/posticipato al {new_dt_str} con VisitaSubito."),
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines)


_ics_counter = 0
_ics_lock = threading.Lock()


def write_ics(
    info: AppuntamentoInfo,
    out_dir: Path,
    filename: str | None = None,
    vecchio_appuntamento: Optional[Any] = None,
    vecchio_uid: Optional[str] = None,
) -> Path:
    """Save .ics file in out_dir and return its path."""
    global _ics_counter
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if filename:
        path = out_dir / filename
    else:
        with _ics_lock:
            _ics_counter += 1
            seq = _ics_counter
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = out_dir / f"appuntamento_{timestamp}_{seq}.ics"
    path.write_text(
        build_ics(
            info,
            vecchio_appuntamento=vecchio_appuntamento,
            vecchio_uid=vecchio_uid,
        ),
        encoding="utf-8",
    )
    return path
