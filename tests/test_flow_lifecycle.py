from unittest.mock import MagicMock, patch
import pytest
from flows.base import Flow
from services.matcher import Slot
from datetime import datetime


class DummyFlow(Flow):
    def load_page(self):
        pass

    def extract_slots(self):
        return []

    def execute(self, slot):
        pass


def test_flow_poll_once_skips_when_done():
    monitor = {"id": "m1", "type": "new", "criteri": {}}
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()
    store.get_action.return_value = "done_pending_choice"

    flow = DummyFlow(monitor, browser, queue, bot, store)
    flow._is_autenticato = MagicMock(return_value=True)

    res = flow.poll_once(manual=False)
    assert res == ""
    # Non deve chiamare nè browser nè bot se la visita è già prenotata
    bot.notify.assert_not_called()

    # In caso di poll manuale, notifica lo stato all'utente
    res_man = flow.poll_once(manual=True)
    assert res_man == ""
    bot.notify.assert_called_once()
    assert "risulta già prenotata" in bot.notify.call_args[0][0]


def test_flow_request_approval_and_post_booking_prompt():
    monitor = {"id": "m1", "type": "new", "ricetta": "VISITA CARDIOLOGICA", "criteri": {}}
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()

    flow = DummyFlow(monitor, browser, queue, bot, store)
    slot = Slot(datetime=datetime(2026, 10, 25, 9, 30))

    queue.create.return_value = "req1"
    queue.wait_decision.return_value = {"cancelled": False, "decision": True, "extra": {"azione": "approve"}}
    bot.notify_buttons.return_value = [("chat1", 123)]

    flow.execute = MagicMock()

    res = flow._request_approval(slot)

    assert res == "ok"
    flow.execute.assert_called_once_with(slot)
    # Lo stato dell'azione viene marcato done_pending_choice
    store.mark_action.assert_called_once_with("m1", "done_pending_choice", str(slot))
    # Viene notificato il completamento della prenotazione
    assert any("prenotato" in str(c) for c in bot.notify.call_args_list)
    # Viene inviato il messaggio con i bottoni ferma/continua
    assert bot.notify_buttons.call_count == 2  # prima proposta approvazione, poi scelta post-prenotazione
    second_call_buttons = bot.notify_buttons.call_args_list[1][0][1]
    assert any("postbook:stop:m1" in btn[1] for btn in second_call_buttons[0])
    assert any("postbook:continue:m1" in btn[1] for btn in second_call_buttons[0])


def test_flow_rejection_adds_to_blacklist():
    monitor = {"id": "m1", "type": "new", "ricetta": "VISITA CARDIOLOGICA", "criteri": {}, "nre": "0300A1"}
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()

    flow = DummyFlow(monitor, browser, queue, bot, store)
    slot = Slot(datetime=datetime(2026, 10, 25, 9, 30))

    queue.create.return_value = "req_deny"
    # Rifiuto da parte dell'utente
    queue.wait_decision.return_value = {"cancelled": False, "decision": False, "extra": {"azione": "deny"}}
    bot.notify_buttons.return_value = [("chat1", 123)]

    res = flow._request_approval(slot)
    assert "non confermata" in res
    store.add_blacklist_entry.assert_called_once_with("m1", slot, nre="0300A1", data_ricetta="")


def test_flow_poll_once_ignores_blacklisted_slots():
    monitor = {"id": "m1", "type": "new", "criteri": {}}
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()

    slot1 = Slot(datetime=datetime(2026, 10, 25, 9, 30))
    slot2 = Slot(datetime=datetime(2026, 10, 26, 11, 0))

    flow = DummyFlow(monitor, browser, queue, bot, store)
    flow._is_autenticato = MagicMock(return_value=True)
    flow.extract_slots = MagicMock(return_value=[slot1, slot2])

    store.get_action.return_value = "idle"
    store.seen_slots.return_value = set()
    # slot1 è in blacklist!
    store.get_blacklist_keys.return_value = {slot1.key}

    queue.create.return_value = "req2"
    queue.wait_decision.return_value = {"cancelled": False, "decision": True, "extra": {"azione": "approve"}}
    flow.execute = MagicMock()

    flow.poll_once(manual=False)

    # Solo slot2 deve essere stato proposto, non slot1!
    queue.create.assert_called_once()
    payload = queue.create.call_args[0][1]
    assert payload["slot"]["datetime"] == slot2.datetime


def test_flow_request_approval_execution_failure_resets_to_idle():
    monitor = {"id": "m1", "type": "new", "ricetta": "TC MASSICCIO FACCIALE", "criteri": {}}
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()

    flow = DummyFlow(monitor, browser, queue, bot, store)
    slot = Slot(datetime=datetime(2026, 10, 25, 9, 30))

    queue.create.return_value = "req_err"
    queue.wait_decision.return_value = {"cancelled": False, "decision": True, "extra": {"azione": "approve"}}
    bot.notify_buttons.return_value = [("chat1", 123)]

    # execute fallisce per disponibilità non più presente
    flow.execute = MagicMock(side_effect=RuntimeError("La disponibilità selezionata non è più disponibile"))

    res = flow._request_approval(slot)

    assert res == "errore"
    flow.execute.assert_called_once_with(slot)
    # L'azione deve essere ripristinata a idle (e NON done_pending_choice)
    store.mark_action.assert_called_once()
    args = store.mark_action.call_args[0]
    assert args[0] == "m1"
    assert args[1] == "idle"
    assert "fallita prenotazione" in args[2]

    # Notifica di errore inviata all'utente
    assert any("Prenotazione non riuscita" in str(c) for c in bot.notify.call_args_list)
    # Nessun prompt di continua monitoraggio (secondo notify_buttons)
    assert bot.notify_buttons.call_count == 1


def test_attendi_conferma_prenotazione_success():
    flow = DummyFlow({"id": "m1"}, MagicMock(), MagicMock(), MagicMock(), MagicMock())
    flow._is_autenticato = MagicMock(return_value=True)

    # Simula HTML di successo con codice prenotazione
    mock_driver = MagicMock()
    mock_driver.page_source = (
        '<div class="success-feedback-title">Prenotazione effettuata con successo!</div>'
        '<div class="appuntamento-field-title"><span>Codice prenotazione</span></div>'
        '<div class="appuntamento-field-value"><span>260076159770</span></div>'
    )
    # 1° call per err_msg (nessun errore), 2° call per has_success_el (True)
    mock_driver.execute_script.side_effect = ["", True]
    flow.browser.driver = mock_driver

    ok, cod, err = flow._attendi_conferma_prenotazione(timeout_s=2)
    assert ok is True
    assert cod == "260076159770"
    assert err == ""


def test_attendi_conferma_prenotazione_portal_error():
    flow = DummyFlow({"id": "m1"}, MagicMock(), MagicMock(), MagicMock(), MagicMock())
    flow._is_autenticato = MagicMock(return_value=True)

    mock_driver = MagicMock()
    mock_driver.page_source = "<html><body>Errore</body></html>"
    # JS trova una modale di errore con messaggio
    mock_driver.execute_script.return_value = "Attenzione: la disponibilità non è più disponibile"
    flow.browser.driver = mock_driver

    ok, cod, err = flow._attendi_conferma_prenotazione(timeout_s=2)
    assert ok is False
    assert cod == ""
    assert "non è più disponibile" in err


def test_verifica_riscontro_miei_appuntamenti_success():
    monitor = {"id": "m1", "ricetta": "TC MASSICCIO FACCIALE SENZA CONTRASTO"}
    flow = DummyFlow(monitor, MagicMock(), MagicMock(), MagicMock(), MagicMock())
    flow._is_autenticato = MagicMock(return_value=True)
    slot = Slot(datetime=datetime(2027, 6, 4, 14, 20))

    mock_driver = MagicMock()
    mock_driver.page_source = "<html>TC MASSICCIO FACCIALE 04/06/2027</html>"
    # Cards JS ritorna una lista di testi card
    mock_driver.execute_script.side_effect = [
        True,  # opened Gestisci
        None,  # switch tab
        None,  # dismiss modal
        ["TC MASSICCIO FACCIALE SENZA CONTRASTO Data e ora 04/06/2027 - 14:20 Codice 260076159770"],
    ]
    flow.browser.driver = mock_driver

    res = flow._verifica_riscontro_miei_appuntamenti(slot, codice="260076159770", timeout_s=2)
    assert res is True


def test_verifica_riscontro_miei_appuntamenti_not_found():
    monitor = {"id": "m1", "ricetta": "TC MASSICCIO FACCIALE"}
    flow = DummyFlow(monitor, MagicMock(), MagicMock(), MagicMock(), MagicMock())
    flow._is_autenticato = MagicMock(return_value=True)
    slot = Slot(datetime=datetime(2027, 6, 4, 14, 20))

    mock_driver = MagicMock()
    mock_driver.page_source = "<html>Nessun appuntamento trovato</html>"
    mock_driver.execute_script.return_value = []
    flow.browser.driver = mock_driver

    res = flow._verifica_riscontro_miei_appuntamenti(slot, codice="999999", timeout_s=2)
    assert res is False


def test_new_booking_flow_execute_aborts_on_confirmation_failure():
    from flows.new_booking import NewBookingFlow
    flow = NewBookingFlow({"id": "m1", "ricetta": "TC MASSICCIO"}, MagicMock(), MagicMock(), MagicMock(), MagicMock())
    slot = Slot(datetime=datetime(2026, 11, 20, 10, 0))

    btn_mock = MagicMock()
    container_mock = MagicMock()
    container_mock.text = "20/11/2026 10:00"
    btn_mock.find_element.return_value = container_mock

    chk_mock = MagicMock()
    chk_mock.is_selected.return_value = True

    conf_mock = MagicMock()
    conf_mock.get_attribute.return_value = None  # abilitato

    def _mock_find(sel, mult=False):
        if mult:
            return [btn_mock]
        if "checkbox" in str(sel):
            return chk_mock
        return conf_mock

    flow._find = MagicMock(side_effect=_mock_find)
    flow._click_el = MagicMock()
    flow.browser.driver.page_source = "<html>Modal</html>"

    flow._attendi_conferma_prenotazione = MagicMock(return_value=(False, "", "Disponibilità non più valida"))
    flow._verifica_riscontro_miei_appuntamenti = MagicMock()

    with pytest.raises(RuntimeError) as exc_info:
        flow.execute(slot)

    assert "Disponibilità non più valida" in str(exc_info.value)
    flow.store.record_prenotazione.assert_not_called()
    flow._verifica_riscontro_miei_appuntamenti.assert_not_called()


def test_new_booking_flow_execute_aborts_on_riscontro_failure():
    from flows.new_booking import NewBookingFlow
    flow = NewBookingFlow({"id": "m1", "ricetta": "TC MASSICCIO"}, MagicMock(), MagicMock(), MagicMock(), MagicMock())
    slot = Slot(datetime=datetime(2026, 11, 20, 10, 0))

    btn_mock = MagicMock()
    container_mock = MagicMock()
    container_mock.text = "20/11/2026 10:00"
    btn_mock.find_element.return_value = container_mock

    chk_mock = MagicMock()
    chk_mock.is_selected.return_value = True

    conf_mock = MagicMock()
    conf_mock.get_attribute.return_value = None  # abilitato

    def _mock_find(sel, mult=False):
        if mult:
            return [btn_mock]
        if "checkbox" in str(sel):
            return chk_mock
        return conf_mock

    flow._find = MagicMock(side_effect=_mock_find)
    flow._click_el = MagicMock()
    flow.browser.driver.page_source = "<html>Modal</html>"

    flow._attendi_conferma_prenotazione = MagicMock(return_value=(True, "COD123", ""))
    flow._verifica_riscontro_miei_appuntamenti = MagicMock(return_value=False)

    with pytest.raises(RuntimeError) as exc_info:
        flow.execute(slot)

    assert "Riscontro fallito" in str(exc_info.value)
    flow.store.record_prenotazione.assert_not_called()


def test_reschedule_flow_execute_aborts_on_riscontro_failure():
    from flows.reschedule import RescheduleFlow
    flow = RescheduleFlow({"id": "res-1", "ricetta": "TC MASSICCIO"}, MagicMock(), MagicMock(), MagicMock(), MagicMock())
    slot = Slot(datetime=datetime(2026, 11, 20, 10, 0))

    chk_mock = MagicMock()
    chk_mock.is_selected.return_value = True

    conf_mock = MagicMock()
    conf_mock.get_attribute.return_value = None  # abilitato

    flow._trova_bottone_slot = MagicMock(return_value=MagicMock())
    flow._click_el = MagicMock()
    flow._find = MagicMock(side_effect=lambda sel, mult=False: chk_mock if "checkbox" in str(sel) else conf_mock)
    flow.browser.driver.page_source = "<html>Modal</html>"

    flow._attendi_conferma_prenotazione = MagicMock(return_value=(True, "COD123", ""))
    flow._verifica_riscontro_miei_appuntamenti = MagicMock(return_value=False)

    with pytest.raises(RuntimeError) as exc_info:
        flow.execute(slot)

    assert "Riscontro fallito" in str(exc_info.value)
    flow.store.record_prenotazione.assert_not_called()



