import pytest

from services.ricette import classify, prenotabile_in_automatico, parse_ricette


def test_classify_specialistica():
    assert classify(["VISITA DERMATOLOGICA", "PRIMA VISITA"]) == "specialistica"
    assert classify(["ECOGRAFIA ADDOME COMPLETO"]) == "specialistica"
    assert prenotabile_in_automatico("specialistica") is True


def test_classify_laboratorio():
    assert classify(["ESAME CHIMICO FISICO DELLE URINE"]) == "laboratorio"
    assert classify(["EMOCROMO CON FORMULA"]) == "laboratorio"
    assert classify(["DOSAGGIO GLICEMIA E COLESTEROLO"]) == "laboratorio"
    assert prenotabile_in_automatico("laboratorio") is False


def test_classify_farmaceutica():
    assert classify(["PARACETAMOLO 1000 MG 16 CPR"]) == "farmaceutica"
    assert classify(["AMOXICILLINA 12 BUST"]) == "farmaceutica" or classify(["AMOXICILLINA 1000 MG CPR"]) == "farmaceutica"
    assert classify(["GOCCE ORALI 20 FLACONI"]) == "farmaceutica"
    assert prenotabile_in_automatico("farmaceutica") is False


def test_parse_ricette_html_card():
    sample_html = """
    <div id="0300A1234567890" class="prescrizioni-row visite-row row">
        <span id="codiceRicetta" style="display:none">0300A1234567890</span>
        <div class="stato-text"><b>Prescrizione attiva</b></div>
        <p>Data ricetta: <b>12/03/2026</b></p>
        <p>Regime:</b> <b>SSN</b></p>
        <p>Prescrittore:</b> <b>DOTT. ROSSI MARIO</b></p>
        <p>Prestazione:
            <b>VISITA CARDIOLOGICA</b>
        </p>
        <p>Prescrittore</p>
        <a href="/prenotaonline/prenota?cod=123" class="cambia-visibilita">Prenota</a>
    </div>
    """
    ricette = parse_ricette(sample_html)
    assert len(ricette) == 1
    r = ricette[0]
    assert r["codice"] == "0300A1234567890"
    assert r["stato"] == "Prescrizione attiva"
    assert r["data_ricetta"] == "2026-03-12"
    assert r["regime"] == "SSN"
    assert r["prescrittore"] == "DOTT. ROSSI MARIO"
    assert "VISITA CARDIOLOGICA" in r["prestazioni"]
    assert r["categoria"] == "specialistica"
    assert r["prenotabile"] is True
    assert r["automatizzabile"] is True


def test_parse_ricette_inverted_attributes():
    # Verifica resilienza se il portale inverte l'ordine degli attributi (class prima di id)
    # o ha un ordine diverso di classi CSS
    sample_html = """
    <div class="row prescrizioni-row visite-row" id="0400B9876543210">
        <span id="codiceRicetta">0400B9876543210</span>
        <div class="stato-text"><b>Prescrizione attiva</b></div>
        <p>Prestazione: <b>PRIMA VISITA DERMATOLOGICA</b></p>
        <a href="/prenotaonline/prenota?cod=456" class="cambia-visibilita">Prenota</a>
    </div>
    """
    ricette = parse_ricette(sample_html)
    assert len(ricette) == 1
    assert ricette[0]["codice"] == "0400B9876543210"
    assert "PRIMA VISITA DERMATOLOGICA" in ricette[0]["prestazioni"]
    assert ricette[0]["automatizzabile"] is True

