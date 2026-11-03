from unittest.mock import MagicMock, patch
import pytest

from services.scheduler import Controller


def test_nome_leggibile():
    # Monitor with leftover portal text to clean up
    mon1 = {"ricetta": "VISITA CARDIOLOGICA = 1 --> vedi tutti i dettagli", "type": "new"}
    nome1 = Controller._nome_leggibile(mon1)
    assert "VISITA-CARDIOLOGICA" in nome1
    assert "prenotazione" in nome1
    assert "vedi" not in nome1

    # Reschedule monitor type
    mon2 = {"ricetta": "VISITA DERMATOLOGICA", "type": "reschedule"}
    nome2 = Controller._nome_leggibile(mon2)
    assert "spostamento" in nome2
    assert "VISITA-DERMATOLOGICA" in nome2


def test_status_text_variations(tmp_path):
    # Mock necessary controller parts to verify status formatting
    ctrl = MagicMock()
    ctrl._flows = []
    ctrl.cfg.settings = {"session": {"max_idle_seconds": 3600}}
    ctrl.store.get_monitors.return_value = []
    ctrl._nome_leggibile = Controller._nome_leggibile

    # Case 1: Login blocked due to too many attempts
    ctrl.login_bloccato = True
    ctrl.login_in_corso = False
    ctrl.sess.session_valid = False
    text = Controller.status_text(ctrl)
    assert "⛔ Bloccata" in text

    # Case 2: Login in progress (push sent)
    ctrl.auth_config = MagicMock()
    ctrl.auth_config.describe.return_value = "SPID (SielteID)"
    ctrl.login_bloccato = False
    ctrl.login_in_corso = True
    ctrl.sess.session_valid = False
    text = Controller.status_text(ctrl)
    assert "🔄 Login in corso (SPID (SielteID))" in text

    # Case 3: Active and authenticated session
    ctrl.login_bloccato = False
    ctrl.login_in_corso = False
    ctrl.sess.session_valid = True
    ctrl.sess.is_expired.return_value = False
    text = Controller.status_text(ctrl)
    assert "✅ Attiva e autenticata" in text

    # Case 4: Failed / inactive session
    ctrl.login_bloccato = False
    ctrl.login_in_corso = False
    ctrl.sess.session_valid = False
    ctrl.sess.is_expired.return_value = False
    ctrl.sess.needs_login.return_value = False
    text = Controller.status_text(ctrl)
    assert "❌ Non attiva / fallita" in text


def test_controller_retry_and_blocking():
    ctrl = MagicMock()
    ctrl.max_login_retries = 3
    ctrl.login_retries = 2
    ctrl.login_bloccato = False
    ctrl.bot = MagicMock()

    Controller._incrementa_retry(ctrl)
    assert ctrl.login_retries == 3
    assert ctrl.login_bloccato is True
    ctrl.bot.notify.assert_called_once()


def test_controller_risveglia():
    ctrl = MagicMock()
    ctrl.login_bloccato = True
    ctrl.login_retries = 3
    ctrl._avvia_login.return_value = True

    res = Controller.risveglia(ctrl)
    assert res is True
    assert ctrl.login_bloccato is False
    assert ctrl.login_retries == 0
    ctrl._avvia_login.assert_called_once()


def test_controller_force_poll():
    import threading
    ctrl = MagicMock()
    ctrl._force = threading.Event()
    ctrl._poll_manual = False

    Controller.force_poll(ctrl)
    assert ctrl._poll_manual is True
    assert ctrl._force.is_set()


def test_controller_rimuovi_monitor():
    ctrl = MagicMock()
    f1 = MagicMock(); f1.mid = "m1"
    f2 = MagicMock(); f2.mid = "m2"
    ctrl._flows = [f1, f2]
    ctrl.store = MagicMock()

    ok = Controller.rimuovi_monitor(ctrl, "m1")
    assert ok is True
    assert len(ctrl._flows) == 1
    assert ctrl._flows[0].mid == "m2"
    ctrl.store.remove_monitor.assert_called_once_with("m1")


def test_run_poll_session_expired_triggers_relogin():
    ctrl = MagicMock()
    ctrl.cfg.settings = {"session": {"max_idle_seconds": 3600}}
    ctrl.sess.is_expired.return_value = True
    ctrl.sess.session_valid = True
    ctrl.browser.driver = MagicMock()
    ctrl.sess.is_autenticato.return_value = False
    ctrl.assicura_sessione_attiva.return_value = False
    ctrl.bot = MagicMock()
    ctrl._flows = [MagicMock()]

    Controller._run_poll(ctrl, manual=True)

    assert ctrl.sess.session_valid is False
    ctrl.assicura_sessione_attiva.assert_called_once()
    assert any("impossibile autenticare" in str(c) for c in ctrl.bot.notify.call_args_list)


def test_converti_in_reschedule():
    ctrl = MagicMock()
    ctrl.store.get_prenotazione.return_value = {
        "codice": "PREN999",
        "data_ora": "20/10/2026 11:30",
        "info": {"prestazione": "VISITA UROLOGICA"},
    }
    mon_original = {
        "id": "new-visita-1",
        "type": "new",
        "ricetta": "VISITA UROLOGICA",
        "criteri": {"data_dal": "10/10/2026", "data_a": ""},
    }
    ctrl.store.get_monitors.return_value = [mon_original]
    f_old = MagicMock()
    f_old.mid = "new-visita-1"
    ctrl._flows = [f_old]

    ok = Controller.converti_in_reschedule(ctrl, "new-visita-1")

    assert ok is True
    # The old flow was replaced
    assert f_old not in ctrl._flows
    assert len(ctrl._flows) == 1
    # The store recorded the updated monitor as reschedule
    ctrl.store.add_monitor.assert_called_once()
    saved_mon = ctrl.store.add_monitor.call_args[0][0]
    assert saved_mon["type"] == "reschedule"
    assert saved_mon["codice_appuntamento"] == "PREN999"
    assert saved_mon["data_attuale"] == "20/10/2026 11:30"
    # data_a is tightened to the booked appointment date
    assert saved_mon["criteri"]["data_a"] == "20/10/2026"
    # action_state is reset to idle to continue monitoring
    ctrl.store.mark_action.assert_called_once_with(
        "new-visita-1", "idle", "convertito a reschedule (data 20/10/2026 11:30)"
    )


def test_converti_in_reschedule_tightens_existing_data_a():
    ctrl = MagicMock()
    ctrl.store.get_prenotazione.return_value = {
        "codice": "PREN123",
        "data_ora": "15/10/2026 - 09:00",
    }
    # Case 1: existing data_a is later (e.g. 31/12/2026) -> must be tightened to 15/10/2026
    ctrl.store.get_monitors.return_value = [
        {"id": "m1", "type": "new", "criteri": {"data_a": "31/12/2026"}},
    ]
    ctrl._flows = [MagicMock(mid="m1")]
    Controller.converti_in_reschedule(ctrl, "m1")

    saved_mon = ctrl.store.add_monitor.call_args[0][0]
    assert saved_mon["criteri"]["data_a"] == "15/10/2026"

    # Case 2: existing data_a is already earlier (e.g. 01/10/2026) -> must not be widened
    ctrl.store.get_monitors.return_value = [
        {"id": "m2", "type": "new", "criteri": {"data_a": "01/10/2026"}},
    ]
    ctrl._flows = [MagicMock(mid="m2")]
    Controller.converti_in_reschedule(ctrl, "m2")

    saved_mon2 = ctrl.store.add_monitor.call_args[0][0]
    assert saved_mon2["criteri"]["data_a"] == "01/10/2026"


def test_converti_in_reschedule_fails_when_no_booking():
    ctrl = MagicMock()
    ctrl.store.get_prenotazione.return_value = {"codice": "", "data_ora": "", "info": {}}
    ctrl.store.is_prenotato.return_value = False
    ctrl.store.get_monitors.return_value = []
    ctrl.cfg.active_monitors = []

    ok = Controller.converti_in_reschedule(ctrl, "m_ghost")
    assert ok is False
    ctrl.store.add_monitor.assert_not_called()


def test_converti_in_reschedule_succeeds_on_existing_reschedule_monitor():
    ctrl = MagicMock()
    ctrl.store.get_prenotazione.return_value = {"codice": "", "data_ora": "", "info": {}}
    ctrl.store.is_prenotato.return_value = False
    existing_mon = {
        "id": "reschedule-visita-1",
        "type": "reschedule",
        "ricetta": "VISITA UROLOGICA",
        "data_attuale": "15/11/2026 10:00",
        "criteri": {"data_a": "15/11/2026"},
    }
    ctrl.store.get_monitors.return_value = [existing_mon]
    ctrl.cfg.active_monitors = []
    ctrl._flows = [MagicMock(mid="reschedule-visita-1")]

    ok = Controller.converti_in_reschedule(ctrl, "reschedule-visita-1")
    assert ok is True
    ctrl.store.add_monitor.assert_called_once()
    saved_mon = ctrl.store.add_monitor.call_args[0][0]
    assert saved_mon["type"] == "reschedule"
    assert saved_mon["data_attuale"] == "15/11/2026 10:00"
    ctrl.store.mark_action.assert_called_once_with(
        "reschedule-visita-1", "idle", "convertito a reschedule (data 15/11/2026 10:00)"
    )


def test_build_flows_dedup_and_disabled():
    ctrl = MagicMock()
    ctrl.cfg.active_monitors = [
        {"id": "m1", "enabled": True, "type": "new"},
        {"id": "m2", "enabled": True, "type": "new"},
    ]
    # m1 in dynamic store overrides static one
    ctrl.store.get_monitors.return_value = [
        {"id": "m1", "enabled": True, "type": "reschedule"},
    ]
    # m2 is disabled
    ctrl.store.is_disabled.side_effect = lambda mid: mid == "m2"
    ctrl.store.is_prenotato.return_value = False
    ctrl.store.get_action.return_value = "done"

    Controller._build_flows(ctrl)

    # Only m1 must be instantiated (m2 skipped as disabled, m1 not duplicated)
    assert len(ctrl._flows) == 1
    assert ctrl._flows[0].mid == "m1"
    assert ctrl._flows[0].type == "reschedule"
    # m1 was 'done', must have been reset to 'idle'
    ctrl.store.mark_action.assert_called_once_with("m1", "idle", "riarmo all'avvio")


def test_controller_pause_resume_toggle():
    import threading
    ctrl = MagicMock()
    ctrl.paused = False
    ctrl.store = MagicMock()
    ctrl._force = threading.Event()
    ctrl.pause = lambda: Controller.pause(ctrl)
    ctrl.resume = lambda: Controller.resume(ctrl)
    ctrl.toggle_pause = lambda: Controller.toggle_pause(ctrl)

    # Test pause
    assert ctrl.pause() is True
    assert ctrl.paused is True
    ctrl.store.set_preferenza.assert_called_with("polling_paused", True)

    # Test resume
    assert ctrl.resume() is True
    assert ctrl.paused is False
    ctrl.store.set_preferenza.assert_called_with("polling_paused", False)
    assert ctrl._force.is_set()

    # Test toggle: from False to True
    ctrl._force.clear()
    is_paused = ctrl.toggle_pause()
    assert is_paused is True
    assert ctrl.paused is True

    # Test toggle: from True to False
    is_paused = ctrl.toggle_pause()
    assert is_paused is False
    assert ctrl.paused is False
    assert ctrl._force.is_set()


def test_status_text_paused():
    ctrl = MagicMock()
    ctrl._flows = []
    ctrl.cfg.settings = {"session": {"max_idle_seconds": 3600}}
    ctrl.store.get_monitors.return_value = []
    ctrl._nome_leggibile = Controller._nome_leggibile
    ctrl.login_bloccato = False
    ctrl.login_in_corso = False
    ctrl.sess.session_valid = True
    ctrl.sess.is_expired.return_value = False

    ctrl.paused = True
    text = Controller.status_text(ctrl)
    assert "⏸️ IN PAUSA" in text
    assert "/resume" in text


def test_status_text_polling_in_corso():
    ctrl = MagicMock()
    ctrl._flows = []
    ctrl.cfg.settings = {"session": {"max_idle_seconds": 3600}}
    ctrl.store.get_monitors.return_value = []
    ctrl._nome_leggibile = Controller._nome_leggibile
    ctrl.login_bloccato = False
    ctrl.login_in_corso = False
    ctrl.paused = False
    ctrl.sess.session_valid = True
    ctrl.sess.is_expired.return_value = False

    ctrl.polling_in_corso = True
    text = Controller.status_text(ctrl)
    assert "🔄 Controllo disponibilità in corso..." in text


def test_status_text_with_fascia():
    ctrl = MagicMock()
    mock_flow = MagicMock()
    mock_flow.mid = "new-VISITA-CARD-1"
    mock_flow.type = "new"
    mock_flow.criteri = {
        "province": ["MILANO CITTA'"],
        "data_dal": "01/11/2026",
        "data_a": "30/11/2026",
        "fascia": "mattina",
    }
    mock_flow.get_proposta.return_value = None
    mock_flow.ultimo_controllo = None
    mock_flow.prossimo_controllo = None
    mock_flow.monitor = {}

    ctrl._flows = [mock_flow]
    ctrl.cfg.settings = {"session": {"max_idle_seconds": 3600}}
    ctrl.store.get_monitors.return_value = [{
        "id": "new-VISITA-CARD-1",
        "descrizione": "VISITA CARDIOLOGICA",
        "nre": "0300123",
        "criteri": mock_flow.criteri,
    }]
    ctrl._nome_leggibile = Controller._nome_leggibile
    ctrl.login_bloccato = False
    ctrl.login_in_corso = False
    ctrl.paused = False
    ctrl.sess.session_valid = True
    ctrl.sess.is_expired.return_value = False
    ctrl.polling_in_corso = False

    text = Controller.status_text(ctrl)
    assert "⏰ Fascia: Solo Mattina (07:00 - 13:00)" in text


def test_ensure_session_force_login_and_reset_blocchi():
    ctrl = MagicMock()
    ctrl.login_bloccato = True
    ctrl.login_retries = 3
    ctrl._browser_lock = MagicMock()
    ctrl._login_lock = MagicMock()
    ctrl.browser = MagicMock()
    ctrl.browser.start.return_value = MagicMock()
    ctrl.browser.driver = ctrl.browser.start.return_value
    ctrl._avvia_login.return_value = True

    res = Controller.ensure_session(ctrl, force_login=True, reset_blocchi=True)
    assert res == ctrl.browser.driver
    assert ctrl.login_bloccato is False
    assert ctrl.login_retries == 0
    ctrl._avvia_login.assert_called_once()


def test_risveglia_sets_poll_manual():
    ctrl = MagicMock()
    ctrl.login_bloccato = True
    ctrl.login_retries = 3
    ctrl.login_in_corso = False
    ctrl._browser_lock = MagicMock()
    ctrl._login_lock = MagicMock()
    ctrl._avvia_login.return_value = True
    ctrl._force = MagicMock()
    ctrl._poll_manual = False

    res = Controller.risveglia(ctrl, manual_poll=True)
    assert res is True
    assert ctrl.login_bloccato is False
    assert ctrl.login_retries == 0
    assert ctrl._poll_manual is True
    ctrl._force.set.assert_called_once()


def test_aggiungi_monitor_resets_pause_when_paused():
    ctrl = MagicMock()
    ctrl.paused = True
    ctrl.store = MagicMock()
    ctrl._force = MagicMock()
    ctrl._flows = []
    ctrl.resume = lambda: Controller.resume(ctrl)
    ctrl.criteri_default.return_value = {}

    mid = Controller.aggiungi_monitor(ctrl, tipo="new", descrizione="VISITA OCULISTICA")
    assert mid != ""
    assert ctrl.paused is False
    ctrl.store.set_preferenza.assert_called_with("polling_paused", False)
    ctrl._force.set.assert_called_once()


def test_rimuovi_monitor_resets_pause_when_last_monitor_removed():
    ctrl = MagicMock()
    ctrl.paused = True
    ctrl.store = MagicMock()
    ctrl._force = MagicMock()
    f1 = MagicMock(); f1.mid = "m1"
    ctrl._flows = [f1]
    ctrl.resume = lambda: Controller.resume(ctrl)

    ok = Controller.rimuovi_monitor(ctrl, "m1")
    assert ok is True
    assert len(ctrl._flows) == 0
    assert ctrl.paused is False
    ctrl.store.set_preferenza.assert_called_with("polling_paused", False)
    ctrl._force.set.assert_called_once()


def test_converti_in_reschedule_resets_pause_when_paused():
    ctrl = MagicMock()
    ctrl.paused = True
    ctrl.store = MagicMock()
    ctrl._force = MagicMock()
    ctrl.store.get_prenotazione.return_value = {
        "codice": "PREN123",
        "data_ora": "20/11/2026 10:00",
    }
    ctrl.store.is_prenotato.return_value = True
    ctrl.store.get_monitors.return_value = [
        {"id": "m1", "type": "new", "criteri": {}}
    ]
    ctrl._flows = [MagicMock(mid="m1")]
    ctrl.resume = lambda: Controller.resume(ctrl)

    ok = Controller.converti_in_reschedule(ctrl, "m1")
    assert ok is True
    assert ctrl.paused is False
    ctrl.store.set_preferenza.assert_called_with("polling_paused", False)
    ctrl._force.set.assert_called_once()



def test_avvia_login_failure_sends_screenshot_telegram(tmp_path):
    from unittest.mock import patch
    png_file = tmp_path / "login_fallito.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n")

    ctrl = MagicMock()
    ctrl.browser = MagicMock()
    ctrl.sess = MagicMock()
    ctrl.bot = MagicMock()
    ctrl.auth_provider = MagicMock()
    ctrl.auth_config = MagicMock()
    ctrl.auth_config.describe.return_value = "SPID (SielteID, notifica) [automatico]"
    ctrl.auth_provider.login.side_effect = TimeoutError("Timeout nell'attesa approvazione SielteID")

    with patch("core.debug.salva_debug_dump") as mock_dump:
        mock_dump.return_value = {
            "ref": "20261104_193456_login_fallito",
            "png_path": png_file,
            "message": "📸 Screenshot di debug salvato: 20261104_193456_login_fallito.png (cartella: data/debug)",
        }
        ok = Controller._avvia_login(ctrl)

        assert ok is False
        assert ctrl.login_in_corso is False
        assert ctrl.sess.session_valid is False
        ctrl.bot.notify.assert_called_once()
        assert "Login fallito" in ctrl.bot.notify.call_args[0][0]
        assert "Timeout nell'attesa approvazione SielteID" in ctrl.bot.notify.call_args[0][0]

        ctrl.bot.send_photo.assert_called_once()
        assert str(png_file) in ctrl.bot.send_photo.call_args[0][0]
        assert "Screenshot di debug" in ctrl.bot.send_photo.call_args[1]["caption"]
        ctrl.browser.stop.assert_called_once()


def test_avvia_login_failure_fallback_send_file(tmp_path):
    from unittest.mock import patch
    png_file = tmp_path / "login_fallito.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n")

    ctrl = MagicMock()
    ctrl.browser = MagicMock()
    ctrl.sess = MagicMock()
    # bot only has send_file, no send_photo
    bot = MagicMock(spec=["notify", "send_file"])
    ctrl.bot = bot
    ctrl.auth_provider = MagicMock()
    ctrl.auth_config = MagicMock()
    ctrl.auth_config.describe.return_value = "SPID"
    ctrl.auth_provider.login.side_effect = RuntimeError("Errore connessione")

    with patch("core.debug.salva_debug_dump") as mock_dump:
        mock_dump.return_value = {
            "ref": "ref_123",
            "png_path": png_file,
            "message": "msg",
        }
        ok = Controller._avvia_login(ctrl)

        assert ok is False
        bot.send_file.assert_called_once()
        assert str(png_file) in bot.send_file.call_args[0][0]


def test_avvia_login_failure_handles_send_photo_exception(tmp_path):
    from unittest.mock import patch
    png_file = tmp_path / "login_fallito.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n")

    ctrl = MagicMock()
    ctrl.browser = MagicMock()
    ctrl.sess = MagicMock()
    ctrl.bot = MagicMock()
    ctrl.bot.send_photo.side_effect = RuntimeError("Telegram network error")
    ctrl.auth_provider = MagicMock()
    ctrl.auth_config = MagicMock()
    ctrl.auth_provider.login.side_effect = TimeoutError("Timeout")

    with patch("core.debug.salva_debug_dump") as mock_dump:
        mock_dump.return_value = {
            "ref": "ref_fail",
            "png_path": png_file,
            "message": "msg",
        }
        ok = Controller._avvia_login(ctrl)
        assert ok is False
        ctrl.browser.stop.assert_called_once()


def test_avvia_login_failure_no_dump():
    from unittest.mock import patch
    ctrl = MagicMock()
    ctrl.browser = MagicMock()
    ctrl.sess = MagicMock()
    ctrl.bot = MagicMock()
    ctrl.auth_provider = MagicMock()
    ctrl.auth_config = MagicMock()
    ctrl.auth_provider.login.side_effect = RuntimeError("Fatal browser startup error")

    with patch("core.debug.salva_debug_dump") as mock_dump:
        mock_dump.return_value = None
        ok = Controller._avvia_login(ctrl)
        assert ok is False
        ctrl.bot.send_photo.assert_not_called()
        ctrl.bot.notify.assert_called_once()


def test_controller_monitors_attivi():
    ctrl = MagicMock()
    m1 = {"id": "m1", "type": "new"}
    m2 = {"id": "m2", "type": "reschedule"}
    ctrl.store.get_monitors.return_value = [m1, m2]
    ctrl.store.is_disabled.side_effect = lambda mid: mid == "m2"

    attivi = Controller.monitors_attivi(ctrl)
    assert attivi == [m1]


def test_controller_blacklist_delegations():
    ctrl = MagicMock()
    ctrl.store.get_blacklist.return_value = [{"id": "b1"}]
    ctrl.store.get_blacklist_dates.return_value = {"15/10/2026"}
    ctrl.store.remove_blacklist_entry.return_value = {"id": "b1"}
    ctrl.store.clear_blacklist.return_value = 1

    assert Controller.get_blacklist(ctrl, "m1") == [{"id": "b1"}]
    assert Controller.get_blacklist_dates(ctrl, "m1") == {"15/10/2026"}
    assert Controller.rimuovi_da_blacklist(ctrl, "m1", "b1") == {"id": "b1"}
    assert Controller.svuota_blacklist(ctrl, "m1") == 1


def test_controller_default_preferences_and_setters():
    ctrl = MagicMock()
    ctrl.store.get_preferenze.return_value = {
        "provincia_default": "BERGAMO",
        "data_dal": "10/11/2026",
    }
    ctrl.lista_province = lambda: ["MILANO CITTA'", "BERGAMO", "BRESCIA"]

    # 1. criteri_default
    crit = Controller.criteri_default(ctrl)
    assert crit["province"] == ["BERGAMO"]
    assert crit["data_dal"] == "10/11/2026"

    # 2. set_provincia_default
    assert Controller.set_provincia_default(ctrl, "BRESCIA") is True
    ctrl.store.set_preferenza.assert_called_with("provincia_default", "BRESCIA")
    assert Controller.set_provincia_default(ctrl, "PROVINCIA_FANTASMA") is False

    # 3. set_data_dal_default
    assert Controller.set_data_dal_default(ctrl, "25/12/2026") is True
    ctrl.store.set_preferenza.assert_called_with("data_dal", "25/12/2026")
    assert Controller.set_data_dal_default(ctrl, "2026-12-25") is False
    assert Controller.set_data_dal_default(ctrl, "") is False


def test_controller_aggiungi_monitor_validation_and_creation():
    ctrl = MagicMock()
    ctrl.paused = False
    ctrl.store.get_monitors.return_value = []
    ctrl.criteri_default.return_value = {"province": ["MILANO CITTA'"]}

    # Invalid tipo
    mid_invalid = Controller.aggiungi_monitor(ctrl, tipo="invalid_type", descrizione="VISITA")
    assert mid_invalid == ""

    # Valid tipo
    mid_valid = Controller.aggiungi_monitor(
        ctrl,
        tipo="new",
        descrizione="VISITA OCULISTICA",
        nre="0300A123",
        criteri={"data_dal": "01/12/2026"},
    )
    assert mid_valid.startswith("new-VISITA-OCULISTICA-")
    ctrl.store.add_monitor.assert_called_once()
    saved = ctrl.store.add_monitor.call_args[0][0]
    assert saved["id"] == mid_valid
    assert saved["nre"] == "0300A123"
    assert saved["criteri"]["data_dal"] == "01/12/2026"


def test_controller_decide_request():
    ctrl = MagicMock()
    Controller.decide_request(ctrl, "req_123", approved=True, extra={"k": "v"})
    ctrl.queue.decide.assert_called_once_with("req_123", True, {"k": "v"})


def test_status_text_extended_branches():
    import time
    ctrl = MagicMock()
    ctrl._flows = []
    ctrl.cfg.settings = {"session": {"max_idle_seconds": 3600}}
    ctrl.store.get_monitors.return_value = []
    ctrl._nome_leggibile = Controller._nome_leggibile
    ctrl.login_bloccato = False
    ctrl.login_in_corso = False
    ctrl.paused = False
    ctrl.polling_in_corso = False
    ctrl.sess.session_valid = False
    ctrl.sess.is_expired.return_value = False
    ctrl.sess.needs_login.return_value = True

    # 1. Needs login session state and next poll countdown
    ctrl._next_poll_time = time.time() + 45
    text_needs_login = Controller.status_text(ctrl)
    assert "⚠️ Mai loggato" in text_needs_login
    assert "⏱ Prossimo controllo tra: " in text_needs_login

    # 2. Countdown expired (negative remaining clamped to 0)
    ctrl._next_poll_time = time.time() - 10
    text_expired_countdown = Controller.status_text(ctrl)
    assert "⏱ Prossimo controllo tra: 0 s" in text_expired_countdown

    # 3. Flow with multi-province and search strategy, single province string, auto_book and custom hours
    mock_flow = MagicMock()
    mock_flow.mid = "new-cardio-multi"
    mock_flow.type = "new"
    mock_flow.search_strategy = "best_match"
    mock_flow.criteri = {
        "province": ["MILANO CITTA'", "MONZA E DELLA BRIANZA"],
        "ora_min": "09:00",
        "ora_max": "18:00",
        "auto_book": True,
    }
    ctrl._flows = [mock_flow]
    ctrl.store.get_monitors.return_value = [{
        "id": "new-cardio-multi",
        "ricetta": "VISITA CARDIOLOGICA",
        "nre": "0300NRE99",
        "criteri": mock_flow.criteri,
    }]
    ctrl.sess.session_valid = True

    text_multi = Controller.status_text(ctrl)
    assert "MILANO CITTA', MONZA E DELLA BRIANZA" in text_multi
    assert "miglior data tra tutte le province" in text_multi
    assert "Orario: da 09:00 a 18:00" in text_multi
    assert "⚡ Auto-booking: abilitato per questo monitor" in text_multi

    # 4. Reschedule monitor with deadline warning (past deadline)
    mock_resched = MagicMock()
    mock_resched.mid = "reschedule-visita"
    mock_resched.type = "reschedule"
    mock_resched.criteri = {
        "province": "BRESCIA",
        "auto_book": False,
    }
    ctrl._flows = [mock_resched]
    # Existing appointment yesterday (deadline expired)
    ctrl.store.get_monitors.return_value = [{
        "id": "reschedule-visita",
        "ricetta": "VISITA DERMATOLOGICA",
        "data_attuale": "02/11/2026 10:00",
        "criteri": mock_resched.criteri,
    }]
    with patch("services.deadline.check_reschedule_deadline", return_value=(True, None, None, "scaduto")):
        text_resched = Controller.status_text(ctrl)
        assert "📌 Appuntamento attuale: 02/11/2026 10:00" in text_resched
        assert "Provincia: BRESCIA" in text_resched
        assert "⚡ Auto-booking: disabilitato per questo monitor" in text_resched





