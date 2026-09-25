from unittest.mock import MagicMock
import pytest

from services.scheduler import Controller


def test_nome_leggibile():
    # Monitor con resti del portale da pulire
    mon1 = {"ricetta": "VISITA CARDIOLOGICA = 1 --> vedi tutti i dettagli", "type": "new"}
    nome1 = Controller._nome_leggibile(mon1)
    assert "VISITA-CARDIOLOGICA" in nome1
    assert "prenotazione" in nome1
    assert "vedi" not in nome1

    # Monitor di tipo reschedule (spostamento)
    mon2 = {"ricetta": "VISITA DERMATOLOGICA", "type": "reschedule"}
    nome2 = Controller._nome_leggibile(mon2)
    assert "spostamento" in nome2
    assert "VISITA-DERMATOLOGICA" in nome2


def test_status_text_variations(tmp_path):
    # Mockiamo le parti necessarie del controller per verificare la formattazione dello stato
    ctrl = MagicMock()
    ctrl._flows = []
    ctrl.cfg.settings = {"session": {"max_idle_seconds": 3600}}
    ctrl.store.get_monitors.return_value = []
    ctrl._nome_leggibile = Controller._nome_leggibile

    # Caso 1: Login bloccato per troppi tentativi
    ctrl.login_bloccato = True
    ctrl.login_in_corso = False
    ctrl.sess.session_valid = False
    text = Controller.status_text(ctrl)
    assert "⛔ Bloccata" in text

    # Caso 2: Login in corso (push inviata)
    ctrl.login_bloccato = False
    ctrl.login_in_corso = True
    ctrl.sess.session_valid = False
    text = Controller.status_text(ctrl)
    assert "🔄 Login SielteID in corso" in text

    # Caso 3: Sessione attiva e autenticata
    ctrl.login_bloccato = False
    ctrl.login_in_corso = False
    ctrl.sess.session_valid = True
    ctrl.sess.is_expired.return_value = False
    text = Controller.status_text(ctrl)
    assert "✅ Attiva e autenticata" in text

    # Caso 4: Sessione fallita / non attiva
    ctrl.login_bloccato = False
    ctrl.login_in_corso = False
    ctrl.sess.session_valid = False
    ctrl.sess.is_expired.return_value = False
    ctrl.sess.needs_login.return_value = False
    text = Controller.status_text(ctrl)
    assert "❌ Non attiva / fallita" in text
