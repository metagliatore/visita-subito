from datetime import datetime
import pytest

from services.appuntamento import (
    AppuntamentoInfo,
    _clean,
    _tr_api_a,
    _snippet,
    _modale_testo,
    parse_conferma,
)


def test_appuntamento_datetime_property():
    info = AppuntamentoInfo(data_ora="26/10/2026 - 11:15")
    dt = info.datetime
    assert dt == datetime(2026, 10, 26, 11, 15)

    info_invalid = AppuntamentoInfo(data_ora="non valida")
    assert info_invalid.datetime is None


def test_clean():
    assert _clean("  Test    con    spazi   multipli  \n") == "Test con spazi multipli"
    assert _clean(None) == ""


def test_tr_api_a():
    text = "Ospedale Niguarda Milano Presentarsi al piano terra"
    res = _tr_api_a(text, ["Presentarsi"])
    assert res == "Ospedale Niguarda"


def test_snippet():
    text = "Data e ora 26/10/2026 - 11:15 Prestazione VISITA CARDIOLOGICA Azienda NIGUARDA"
    val = _snippet(text, "Prestazione", stop="Azienda")
    assert val == "VISITA CARDIOLOGICA"


def test_parse_conferma():
    sample_modal_html = """
    <div modal-render="true">
        <span>verificaPrenotazioneCtrl.nrDisponibilita</span>
        <div>Data e ora: 26/10/2026 - 11:15</div>
        <div>Prestazione: VISITA CARDIOLOGICA</div>
        <div>Azienda: ASST NIGUARDA</div>
        <div>Comune: MILANO</div>
        <div>Presentarsi in: Padiglione Centrale</div>
        <div>Ulteriori indicazioni: Piazza Ospedale 3, Milano</div>
    </div>
    """
    info = parse_conferma(sample_modal_html)
    assert info.data_ora == "26/10/2026 - 11:15"
    assert info.prestazione == "VISITA CARDIOLOGICA"
    assert info.azienda == "ASST NIGUARDA"
    assert info.presentarsi_in == "Padiglione Centrale"
    assert info.indirizzo == "Piazza Ospedale 3, Milano"


def test_parse_successo_numeric_and_alphanumeric():
    from services.appuntamento import parse_successo
    html_num = "<div><h1>Operazione completata</h1><p>Codice prenotazione: 12345678</p></div>"
    assert parse_successo(html_num) == "12345678"

    html_alpha = "<div><p>Codice prenotazione 0300A-1234567890</p></div>"
    assert parse_successo(html_alpha) == "0300A-1234567890"

    html_none = "<div><p>Nessun codice trovato</p></div>"
    assert parse_successo(html_none) == ""


def test_appuntamento_to_info():
    from services.appuntamento import appuntamento_to_info
    raw = {
        "prestazione": "VISITA UROLOGICA (PRIMA VISITA)",
        "data_ora": "20/11/2026 14:10",
        "codice": "260076159770",
        "azienda": "ASST SANTI PAOLO E CARLO",
        "presentarsi_in": "Presidio San Paolo",
        "comune": "Milano",
        "indirizzo": "Via Di Rudinì, 8",
        "cap": "20142",
        "note": ["Portare esami precedenti"],
    }
    info = appuntamento_to_info(raw)
    assert info.prestazione == "VISITA UROLOGICA (PRIMA VISITA)"
    assert info.data_ora == "20/11/2026 - 14:10"
    assert info.datetime == datetime(2026, 11, 20, 14, 10)
    assert info.codice == "260076159770"
    assert info.azienda == "ASST SANTI PAOLO E CARLO"
    assert info.presentarsi_in == "Presidio San Paolo"
    assert "Via Di Rudinì, 8" in info.indirizzo
    assert "20142 Milano" in info.indirizzo
    assert info.note == ["Portare esami precedenti"]


def test_parse_conferma_with_notes_and_multiline_address():
    sample_modal_html = """
    <div modal-render="true">
        <span>verificaPrenotazioneCtrl.nrDisponibilita</span>
        <div>Data e ora: 18/11/2026 - 09:30</div>
        <div>Prestazione: VISITA DERMATOLOGICA</div>
        <div>Azienda: ASST NIGUARDA</div>
        <div>Comune: MILANO</div>
        <div>Presentarsi in: Padiglione 1  Piano Terra  Stanza 4</div>
        <div>Ulteriori indicazioni: Ingresso Principale</div>
        <div>Note di preparazione: - Digiuno da 6 ore - Portare tessera sanitaria Note operatore: Paziente con allergie Presa visione</div>
        <div class="modal-backdrop"></div>
    </div>
    """
    info = parse_conferma(sample_modal_html)
    assert info.data_ora == "18/11/2026 - 09:30"
    assert info.prestazione == "VISITA DERMATOLOGICA"
    assert info.azienda == "ASST NIGUARDA"
    assert "Padiglione 1" in info.presentarsi_in
    assert "Ingresso Principale" in info.indirizzo
    assert "Digiuno da 6 ore" in info.note
    assert "Portare tessera sanitaria" in info.note
    assert "Paziente con allergie" in info.note


def test_appuntamento_to_info_single_string_note():
    from services.appuntamento import appuntamento_to_info
    raw = {
        "prestazione": "VISITA OCULISTICA",
        "data_ora": "15/12/2026 10:00",
        "note": "Presentarsi con 15 minuti di anticipo",
    }
    info = appuntamento_to_info(raw)
    assert info.note == ["Presentarsi con 15 minuti di anticipo"]
    assert info.data_ora == "15/12/2026 - 10:00"


def test_modale_testo_fallbacks():
    # 1. HTML without 'verificaPrenotazioneCtrl' strips tags directly
    html_raw = "<div><span>Solo testo senza controller</span></div>"
    assert "Solo testo senza controller" in _modale_testo(html_raw)

    # 2. HTML with 'verificaPrenotazioneCtrl' but without 'modal-render' marker
    html_no_render = "<div><span>verificaPrenotazioneCtrl.nrDisponibilita</span><p>Contenuto</p><div class='navbar-toggle'></div></div>"
    extracted = _modale_testo(html_no_render)
    assert "Contenuto" in extracted

