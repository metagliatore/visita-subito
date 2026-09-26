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
