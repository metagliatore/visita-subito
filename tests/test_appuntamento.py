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
