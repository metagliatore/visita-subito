"""Calendar event generator .ics (RFC 5545) for appointments.

Used to produce an importable file for Google Calendar / Apple / Outlook
with date, time, and address of the visit.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from services.appuntamento import AppuntamentoInfo


def _fold(line: str, limit: int = 74) -> str:
    """RFC 5545 line folding: splits long lines with CRLF + space."""
    if len(line) <= limit:
        return line
    out, cur = [], ""
    for ch in line:
        if len(cur) >= limit:
            out.append(cur)
            cur = " "
        cur += ch
    out.append(cur)
    return "\r\n".join(out)


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def build_ics(info: AppuntamentoInfo, uid: str | None = None,
              durata_minuti: int = 30) -> str:
    """Build .ics event content for the appointment."""
    dt = info.datetime or datetime.now()
    end = dt + timedelta(minutes=durata_minuti)
    uid = uid or f"{dt.strftime('%Y%m%d%H%M%S')}-fascicolo-monitor"

    location = ", ".join(
        x for x in [info.presentarsi_in, info.indirizzo] if x and x not in ("-", " "))

    summary = f"Visita: {info.prestazione}" if info.prestazione else "Appuntamento sanitario"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//fascicolo-monitor//IT",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_fmt_dt(datetime.now())}",
        f"DTSTART:{_fmt_dt(dt)}",
        f"DTEND:{_fmt_dt(end)}",
        f"SUMMARY:{_fold(summary)}",
    ]
    if location:
        lines.append(_fold(f"LOCATION:{location}"))
    desc_parts = []
    if info.azienda:
        desc_parts.append(f"Azienda: {info.azienda}")
    if info.note:
        notes = "\\n".join(f"- {n}" for n in info.note)
        desc_parts.append(f"Note:\\n{notes}")
    if desc_parts:
        full_desc = "\\n\\n".join(desc_parts)
        lines.append(_fold(f"DESCRIPTION:{full_desc}"))
    lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def write_ics(info: AppuntamentoInfo, out_dir: Path) -> Path:
    """Save .ics file in out_dir and return its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"appuntamento_{timestamp}.ics"
    path.write_text(build_ics(info), encoding="utf-8")
    return path
