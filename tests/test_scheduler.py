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
    ctrl.auth_config = MagicMock()
    ctrl.auth_config.describe.return_value = "SPID (SielteID)"
    ctrl.login_bloccato = False
    ctrl.login_in_corso = True
    ctrl.sess.session_valid = False
    text = Controller.status_text(ctrl)
    assert "🔄 Login in corso (SPID (SielteID))" in text

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
    # Il vecchio flusso è stato sostituito
    assert f_old not in ctrl._flows
    assert len(ctrl._flows) == 1
    # Lo store ha registrato il monitor aggiornato come reschedule
    ctrl.store.add_monitor.assert_called_once()
    saved_mon = ctrl.store.add_monitor.call_args[0][0]
    assert saved_mon["type"] == "reschedule"
    assert saved_mon["codice_appuntamento"] == "PREN999"
    assert saved_mon["data_attuale"] == "20/10/2026 11:30"
    # data_a viene ristretta alla data dell'appuntamento prenotato
    assert saved_mon["criteri"]["data_a"] == "20/10/2026"
    # action_state viene ripristinato a idle per continuare a monitorare
    ctrl.store.mark_action.assert_called_once_with(
        "new-visita-1", "idle", "convertito a reschedule (data 20/10/2026 11:30)"
    )


def test_build_flows_dedup_and_disabled():
    ctrl = MagicMock()
    ctrl.cfg.active_monitors = [
        {"id": "m1", "enabled": True, "type": "new"},
        {"id": "m2", "enabled": True, "type": "new"},
    ]
    # m1 nello store dinamico sovrascrive quello statico
    ctrl.store.get_monitors.return_value = [
        {"id": "m1", "enabled": True, "type": "reschedule"},
    ]
    # m2 risulta disabilitato
    ctrl.store.is_disabled.side_effect = lambda mid: mid == "m2"
    ctrl.store.is_prenotato.return_value = False

    Controller._build_flows(ctrl)

    # Solo m1 deve essere istanziato (m2 scartato perché disabilitato, m1 non duplicato)
    assert len(ctrl._flows) == 1
    assert ctrl._flows[0].mid == "m1"
    assert ctrl._flows[0].type == "reschedule"


