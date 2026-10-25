import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.cli_ui import CliUI, strip_html


def test_strip_html():
    raw = '<b>VISITA</b> a <a href="https://maps.google.com/?q=Milano">Milano</a> &amp; dintorni'
    clean = strip_html(raw)
    assert "<b>" not in clean
    assert "VISITA a Milano (https://maps.google.com/?q=Milano) & dintorni" in clean


def test_notify(capsys):
    ui = CliUI()
    ui.notify("<b>Attenzione</b>: sessione valida")
    captured = capsys.readouterr().out
    assert "[NOTIFICA] Attenzione: sessione valida" in captured


def test_notify_buttons_approval_triggers_sound_and_state(capsys):
    ui = CliUI(sound_alert=True)
    with patch("services.cli_ui.play_alert_sound") as mock_sound:
        buttons = [[("✅ Approva", "decide:approve:req123"), ("❌ Rifiuta", "decide:deny:req123")]]
        inviati = ui.notify_buttons("Disponibilità trovata per Visita", buttons)
        assert inviati == [("cli", 1)]
        assert mock_sound.called
        assert mock_sound.call_args[0][0] == "slot_found"
        assert ui._pending_approval is not None
        assert ui._pending_approval["req_id"] == "req123"

        out = capsys.readouterr().out
        assert "DISPONIBILITÀ TROVATA" in out
        assert "req123" not in out  # clean output


def test_notify_buttons_postbook_sets_state(capsys):
    ui = CliUI()
    buttons = [[("🛑 Ferma", "postbook:stop:mon-1"), ("🔄 Continua", "postbook:continue:mon-1")]]
    inviati = ui.notify_buttons("Prenotazione effettuata!", buttons)
    assert inviati == [("cli", 2)]
    assert ui._pending_postbook is not None
    assert ui._pending_postbook["mid"] == "mon-1"
    out = capsys.readouterr().out
    assert "PRENOTAZIONE COMPLETATA" in out


def test_edit_message_clears_expired_proposal(capsys):
    ui = CliUI()
    ui._pending_approval = {"req_id": "test", "msg": "test"}
    ui.edit_message("Proposta scaduta: tempo esaurito", destinations=[("cli", 1)])
    assert ui._pending_approval is None
    out = capsys.readouterr().out
    assert "[SCADUTA]" in out


def test_send_file_and_photo(capsys):
    ui = CliUI()
    ui.send_file("data/test.ics", caption="Evento calendario")
    out = capsys.readouterr().out
    assert "File pronto: data/test.ics" in out
    assert "Evento calendario" in out

    ui.send_photo("data/debug/test.png", caption="Screenshot errore")
    out2 = capsys.readouterr().out
    assert "Immagine salvata: data/debug/test.png" in out2


def test_ask_otp_and_dispatch(monkeypatch):
    ui = CliUI()

    # Simulate thread calling ask_otp
    def call_ask_otp():
        return ui.ask_otp(prompt="Inserisci codice SMS", timeout_s=5)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(call_ask_otp)
        # Wait until ui is waiting for otp
        import time
        time.sleep(0.1)
        assert ui._pending_otp is True
        # User enters code via _dispatch_input
        ui._dispatch_input("123-456")
        res = future.result(timeout=2)
        assert res == "123456"
        assert ui._pending_otp is False


def test_ask_auth_fallback_and_dispatch():
    ui = CliUI()

    def call_fallback():
        return ui.ask_auth_fallback(prompt="Seleziona secondo fattore", timeout_s=5)

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(call_fallback)
        import time
        time.sleep(0.1)
        assert ui._pending_auth_fallback is True
        ui._dispatch_input("1")
        res = future.result(timeout=2)
        assert res == "notify"
        assert ui._pending_auth_fallback is False


def test_dispatch_approval_yes_and_no():
    ui = CliUI()
    mock_ctl = MagicMock()
    ui.controller = mock_ctl

    # Test approval
    ui._pending_approval = {"req_id": "req-999", "msg": "Visita"}
    ui._dispatch_input("s")
    mock_ctl.decide_request.assert_called_once_with("req-999", True)
    assert ui._pending_approval is None

    # Test rejection
    mock_ctl.reset_mock()
    ui._pending_approval = {"req_id": "req-888", "msg": "Visita"}
    ui._dispatch_input("n")
    mock_ctl.decide_request.assert_called_once_with("req-888", False)
    assert ui._pending_approval is None


def test_dispatch_postbook_choice():
    ui = CliUI()
    mock_ctl = MagicMock()
    ui.controller = mock_ctl

    # Choice 1: stop
    ui._pending_postbook = {"mid": "mon-cardiologia", "msg": "Done"}
    ui._dispatch_input("1")
    mock_ctl.rimuovi_monitor.assert_called_once_with("mon-cardiologia")
    assert ui._pending_postbook is None

    # Choice 2: continue (anticipa)
    mock_ctl.reset_mock()
    ui._pending_postbook = {"mid": "mon-oculistica", "msg": "Done"}
    ui._dispatch_input("2")
    mock_ctl.converti_in_reschedule.assert_called_once_with("mon-oculistica")
    assert ui._pending_postbook is None


def test_commands_status_poll_pause_resume(capsys):
    ui = CliUI()
    mock_ctl = MagicMock()
    mock_ctl.status_text.return_value = "🤖 Monitor attivi: 1"
    ui.controller = mock_ctl

    ui._dispatch_input("status")
    mock_ctl.status_text.assert_called_once()
    assert "🤖 Monitor attivi: 1" in capsys.readouterr().out

    ui._dispatch_input("poll")
    mock_ctl.force_poll.assert_called_once()

    ui._dispatch_input("pause")
    mock_ctl.pause.assert_called_once()

    ui._dispatch_input("resume")
    mock_ctl.resume.assert_called_once()


def test_commands_appuntamenti_and_ics(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    ui = CliUI()
    mock_ctl = MagicMock()
    sample_appuntamenti = [
        {
            "prestazione": "VISITA CARDIOLOGICA",
            "data_ora": "20/11/2026 - 10:30",
            "codice": "0300123456",
            "azienda": "ASST NIGUARDA",
            "presentarsi_in": "Padiglione Rossini",
            "comune": "Milano",
            "indirizzo": "Piazza Ospedale Maggiore 3",
        },
        {
            "prestazione": "VISITA DERMATOLOGICA",
            "data_ora": "15/12/2026 14:00",
            "codice": "0300999888",
            "azienda": "POLICLINICO",
            "comune": "Milano",
        }
    ]
    mock_ctl.get_appuntamenti.return_value = sample_appuntamenti
    ui.controller = mock_ctl

    # 1. Fetch appointments
    ui._dispatch_input("appuntamenti")
    out = capsys.readouterr().out
    assert "VISITA CARDIOLOGICA" in out
    assert "VISITA DERMATOLOGICA" in out
    assert len(ui._appuntamenti_cache) == 2

    # 2. Export single .ics
    ui._dispatch_input("ics 1")
    out_ics1 = capsys.readouterr().out
    assert "File calendario generato con successo" in out_ics1
    created_files = list((tmp_path / "data" / "calendario").glob("appuntamento_*.ics"))
    assert len(created_files) == 1
    content = created_files[0].read_text(encoding="utf-8")
    assert "BEGIN:VCALENDAR" in content
    assert "VISITA CARDIOLOGICA" in content

    # 3. Export all .ics
    ui._dispatch_input("ics all")
    out_all = capsys.readouterr().out
    assert "Generati 2 file calendario" in out_all
    created_files_all = list((tmp_path / "data" / "calendario").glob("appuntamento_*.ics"))
    assert len(created_files_all) == 3


def test_commands_ricette(capsys):
    ui = CliUI()
    mock_ctl = MagicMock()
    mock_ctl.get_ricette.return_value = [
        {"prestazione": "VISITA OCULISTICA", "prenotabile": True, "nre": "12345"},
        {"prestazione": "VISITA FISIATRICA", "prenotabile": False, "nre": "67890", "data_attuale": "10/01/2027"},
    ]
    ui.controller = mock_ctl

    ui._dispatch_input("ricette")
    out = capsys.readouterr().out
    assert "PRENOTABILI" in out
    assert "VISITA OCULISTICA" in out
    assert "GIÀ PRENOTATE" in out
    assert "VISITA FISIATRICA" in out


def test_command_anticipa(monkeypatch, capsys):
    ui = CliUI()
    mock_ctl = MagicMock()
    mock_ctl.aggiungi_monitor.return_value = "reschedule-CARD-1"
    ui.controller = mock_ctl
    ui._appuntamenti_cache = [
        {"prestazione": "VISITA CARDIOLOGICA", "data_ora": "20/11/2026 10:30", "codice": "COD123"}
    ]

    ui._dispatch_input("anticipa 1")
    mock_ctl.aggiungi_monitor.assert_called_once_with(
        tipo="reschedule",
        descrizione="VISITA CARDIOLOGICA",
        nre="COD123",
        data_attuale="20/11/2026 10:30",
    )
    assert "Monitor di anticipo avviato con ID: reschedule-CARD-1" in capsys.readouterr().out


def test_command_blacklist(capsys):
    ui = CliUI()
    mock_ctl = MagicMock()
    mock_ctl.monitors_attivi.return_value = [{"id": "mon-1"}]
    mock_ctl.get_blacklist.return_value = [{"descrizione": "Slot 10/11 Ospedale X"}]
    mock_ctl.svuota_blacklist.return_value = 1
    ui.controller = mock_ctl

    # View
    ui._dispatch_input("blacklist")
    out = capsys.readouterr().out
    assert "Slot 10/11 Ospedale X" in out

    # Clear
    ui._dispatch_input("blacklist clear")
    mock_ctl.svuota_blacklist.assert_called_once_with("mon-1")
    assert "Blacklist svuotata" in capsys.readouterr().out


def test_command_monitora_interactive(monkeypatch, capsys):
    ui = CliUI()
    mock_ctl = MagicMock()
    mock_ctl.lista_province.return_value = ["MILANO CITTA'", "MONZA-BRIANZA", "BERGAMO"]
    mock_ctl.criteri_default.return_value = {"province": ["MILANO CITTA'"]}
    mock_ctl.aggiungi_monitor.return_value = "new-VISITA-DERM-1"
    ui.controller = mock_ctl

    # Interactive inputs:
    # 3: manual description
    # "VISITA DERMATOLOGICA"
    # "0300123" (NRE)
    # "MILANO CITTA', BERGAMO" (Province)
    # "01/11/2026" (Data dal)
    # "31/12/2026" (Data a)
    inputs = [
        "3",
        "VISITA DERMATOLOGICA",
        "0300123",
        "MILANO CITTA', BERGAMO",
        "01/11/2026",
        "31/12/2026",
    ]
    input_iter = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(input_iter))

    ui._dispatch_input("monitora")
    assert mock_ctl.aggiungi_monitor.called
    kwargs = mock_ctl.aggiungi_monitor.call_args[1]
    assert kwargs["tipo"] == "new"
    assert kwargs["descrizione"] == "VISITA DERMATOLOGICA"
    assert kwargs["nre"] == "0300123"
    assert kwargs["criteri"]["province"] == ["MILANO CITTA'", "BERGAMO"]
    assert kwargs["criteri"]["data_dal"] == "01/11/2026"
    assert kwargs["criteri"]["data_a"] == "31/12/2026"
    assert "Monitor aggiunto con successo! ID: new-VISITA-DERM-1" in capsys.readouterr().out
