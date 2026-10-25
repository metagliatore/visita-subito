"""Calendar (.ics) generation for booked appointments."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from services.appuntamento import AppuntamentoInfo


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


def build_ics(info: AppuntamentoInfo, uid: Optional[str] = None, durata_minuti: int = 30) -> str:
    """Generate RFC 5545 iCalendar format text."""
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
        "BEGIN:VEVENT",
        f"UID:{event_uid}",
        f"DTSTAMP:{dt_stamp}",
        f"DTSTART:{_fmt_dt(dt_start)}",
        f"DTEND:{_fmt_dt(dt_end)}",
        _fold(f"SUMMARY:{summary}"),
    ]
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


import threading

_ics_counter = 0
_ics_lock = threading.Lock()


def write_ics(info: AppuntamentoInfo, out_dir: Path, filename: str | None = None) -> Path:
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
    path.write_text(build_ics(info), encoding="utf-8")
    return path
