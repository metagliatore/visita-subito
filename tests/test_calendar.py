from datetime import datetime
from pathlib import Path
import pytest

from services.appuntamento import AppuntamentoInfo
from services.calendar import _fold, _fmt_dt, build_ics, write_ics


def test_fold():
    short_line = "SUMMARY:Visita breve"
    assert _fold(short_line, limit=74) == short_line

    long_line = "DESCRIPTION:" + "A" * 100
    folded = _fold(long_line, limit=50)
    assert "\r\n " in folded
    # Ogni riga (tranne la prima) inizia con spazio
    lines = folded.split("\r\n")
    assert len(lines) > 1
    assert lines[1].startswith(" ")


def test_fmt_dt():
    dt = datetime(2026, 10, 26, 14, 30, 0)
    assert _fmt_dt(dt) == "20261026T143000"


def test_build_ics():
    info = AppuntamentoInfo(
        data_ora="26/10/2026 - 11:15",
        prestazione="PRIMA VISITA OCULISTICA",
        azienda="ASST GRANDE OSPEDALE METROPOLITANO NIGUARDA",
        presentarsi_in="PADIGLIONE 1",
        indirizzo="PIAZZA OSPEDALE MAGGIORE, 3 - MILANO",
        note=["Portare tessera sanitaria", "Digiuno non necessario"],
    )
    ics_text = build_ics(info, uid="test-uid-123", durata_minuti=45)

    assert "BEGIN:VCALENDAR" in ics_text
    assert "VERSION:2.0" in ics_text
    assert "BEGIN:VEVENT" in ics_text
    assert "UID:test-uid-123" in ics_text
    assert "DTSTART:20261026T111500" in ics_text
    assert "DTEND:20261026T120000" in ics_text
    assert "SUMMARY:Visita: PRIMA VISITA OCULISTICA" in ics_text
    assert "NIGUARDA" in ics_text
    assert "PADIGLIONE 1" in ics_text
    # RFC 5545 compliance: VEVENT can have at most one DESCRIPTION property
    assert ics_text.count("DESCRIPTION:") == 1
    assert "Note:" in ics_text
    assert "END:VEVENT" in ics_text
    assert "END:VCALENDAR" in ics_text


def test_write_ics(tmp_path):
    info = AppuntamentoInfo(
        data_ora="15/11/2026 - 09:00",
        prestazione="ECOGRAFIA",
    )
    ics_path = write_ics(info, out_dir=tmp_path)
    assert ics_path.exists()
    assert ics_path.suffix == ".ics"
    content = ics_path.read_text(encoding="utf-8")
    assert "BEGIN:VCALENDAR" in content
    assert "ECOGRAFIA" in content
