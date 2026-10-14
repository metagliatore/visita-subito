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
    bot.notify_buttons.assert_not_called()

    # In caso di poll manuale, notifica lo stato all'utente con pulsanti per decidere
    res_man = flow.poll_once(manual=True)
    assert res_man == ""
    assert bot.notify_buttons.called or bot.notify.called
    msg = bot.notify_buttons.call_args[0][0] if bot.notify_buttons.called else bot.notify.call_args[0][0]
    assert "risulta già prenotata" in msg


def test_reschedule_flow_poll_once_rearms_when_done():
    monitor = {"id": "reschedule-visita-1", "type": "reschedule", "criteri": {}}
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()
    # Simula monitor reschedule che si ritrova in stato 'done'
    store.get_action.return_value = "done"

    flow = DummyFlow(monitor, browser, queue, bot, store)
    flow.type = "reschedule"
    flow._is_autenticato = MagicMock(return_value=True)
    flow.load_page = MagicMock()
    flow.extract_slots = MagicMock(return_value=[])

    res = flow.poll_once(manual=False)
    # Deve aver riarmato lo stato ad 'idle' e proseguito con load_page
    store.mark_action.assert_called_with("reschedule-visita-1", "idle", "riarmo monitor reschedule")
    flow.load_page.assert_called_once()
    flow.extract_slots.assert_called_once()


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


def test_flow_data_dal_effettiva_supports_iso_and_date_objects():
    from flows.base import Flow
    import datetime as _dt
    oggi = _dt.date.today()

    # Formato italiano futuro
    futuro = (oggi + _dt.timedelta(days=30)).strftime("%d/%m/%Y")
    assert Flow._data_dal_effettiva({"data_dal": futuro}) == futuro

    # Formato ISO futuro
    futuro_iso = (oggi + _dt.timedelta(days=30)).strftime("%Y-%m-%d")
    assert Flow._data_dal_effettiva({"data_dal": futuro_iso}) == futuro

    # Oggetto date futuro
    futuro_date = oggi + _dt.timedelta(days=30)
    assert Flow._data_dal_effettiva({"data_dal": futuro_date}) == futuro

    # Data passata -> fallback a oggi
    passato = (oggi - _dt.timedelta(days=10)).strftime("%d/%m/%Y")
    assert Flow._data_dal_effettiva({"data_dal": passato}) == oggi.strftime("%d/%m/%Y")

    # Stringa vuota o None -> fallback a oggi
    assert Flow._data_dal_effettiva({}) == oggi.strftime("%d/%m/%Y")


def test_flow_request_approval_timeout_edits_message_without_blacklisting():
    monitor = {"id": "m1", "type": "new", "ricetta": "VISITA CARDIOLOGICA", "criteri": {}}
    browser = MagicMock()
    queue = MagicMock()
    bot = MagicMock()
    store = MagicMock()

    flow = DummyFlow(monitor, browser, queue, bot, store)
    slot = Slot(datetime=datetime(2026, 10, 25, 9, 30))

    queue.create.return_value = "req_timeout"
    # Timeout decision
    queue.wait_decision.return_value = {"cancelled": True, "reason": "timeout"}
    inviati_mock = [("chat1", 123)]
    bot.notify_buttons.return_value = inviati_mock

    res = flow._request_approval(slot)
    assert "non confermata" in res
    # Deve modificare il messaggio su Telegram segnalando la scadenza
    bot.edit_message.assert_called_once()
    msg_edited = bot.edit_message.call_args[0][0]
    assert "Proposta scaduta" in msg_edited
    # NON deve inserire lo slot in blacklist perché era solo scaduto il timeout
    store.add_blacklist_entry.assert_not_called()


def test_flow_notifica_poll_vuoto_formatting():
    bot = MagicMock()
    slot = Slot(datetime=datetime(2026, 11, 20, 10, 0))

    # Caso 1: entrambi dal e al presenti
    flow1 = DummyFlow({"id": "m1", "criteri": {"data_dal": "01/10/2026", "data_a": "31/10/2026"}}, MagicMock(), MagicMock(), bot, MagicMock())
    flow1._notifica_poll_vuoto([slot])
    assert "da 01/10/2026 a 31/10/2026" in bot.notify.call_args[0][0]

    # Caso 2: solo data_dal
    bot.notify.reset_mock()
    flow2 = DummyFlow({"id": "m2", "criteri": {"data_dal": "01/10/2026"}}, MagicMock(), MagicMock(), bot, MagicMock())
    flow2._notifica_poll_vuoto([slot])
    assert "a partire da 01/10/2026" in bot.notify.call_args[0][0]

    # Caso 3: solo data_a
    bot.notify.reset_mock()
    flow3 = DummyFlow({"id": "m3", "criteri": {"data_a": "31/10/2026"}}, MagicMock(), MagicMock(), bot, MagicMock())
    flow3._notifica_poll_vuoto([slot])
    assert "fino a 31/10/2026" in bot.notify.call_args[0][0]

    # Caso 4: nessun criterio di data
    bot.notify.reset_mock()
    flow4 = DummyFlow({"id": "m4", "criteri": {}}, MagicMock(), MagicMock(), bot, MagicMock())
    flow4._notifica_poll_vuoto([slot])
    assert "nel range richiesto" in bot.notify.call_args[0][0]


def test_reschedule_flow_card_selection_safe_and_keyword_matching():
    from flows.reschedule import RescheduleFlow

    # Mock 2 card appuntamento: Card 1 = Cardiologia, Card 2 = Urologia
    b1 = MagicMock()
    c1 = MagicMock()
    c1.text = "VISITA CARDIOLOGICA - Data 10/11/2026 - Codice COD-CARDIO-1"
    b1.find_element.return_value = c1

    b2 = MagicMock()
    c2 = MagicMock()
    c2.text = "VISITA UROLOGICA (PRIMA VISITA) - Data 20/11/2026 - NRE: 0300A1234567890"
    b2.find_element.return_value = c2

    flow = RescheduleFlow(
        {"id": "res-uro", "ricetta": "VISITA UROLOGICA (PRIMA VISITA)", "nre": "0300A-1234567890"},
        MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    flow._find = MagicMock(return_value=[b1, b2])
    flow._is_autenticato = MagicMock(return_value=True)
    flow._click_el = MagicMock()
    flow._gestisci_completa_dati = MagicMock()
    flow._compila_dove_quando_e_cerca = MagicMock()

    # Naviga e seleziona
    with patch("selenium.webdriver.support.ui.WebDriverWait.until", return_value=MagicMock()):
        flow.load_page()

    # DEVE aver selezionato b2 (Urologica), NON b1
    flow._click_el.assert_any_call(b2)
    assert not any(call[0][0] == b1 for call in flow._click_el.call_args_list)


def test_reschedule_flow_card_selection_aborts_when_no_match():
    from flows.reschedule import RescheduleFlow

    b1 = MagicMock()
    c1 = MagicMock()
    c1.text = "VISITA CARDIOLOGICA"
    b1.find_element.return_value = c1

    b2 = MagicMock()
    c2 = MagicMock()
    c2.text = "VISITA DERMATOLOGICA"
    b2.find_element.return_value = c2

    # Monitor cerca OCULISTICA
    flow = RescheduleFlow(
        {"id": "res-ocu", "ricetta": "VISITA OCULISTICA"},
        MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    flow._find = MagicMock(return_value=[b1, b2])
    flow._is_autenticato = MagicMock(return_value=True)
    flow._click_el = MagicMock()

    with pytest.raises(RuntimeError) as exc_info:
        flow.load_page()

    assert "Nessuna card appuntamento corrisponde ai criteri" in str(exc_info.value)
    flow._click_el.assert_not_called()


def test_new_booking_flow_apri_ricetta_normalizes_nre():
    from flows.new_booking import NewBookingFlow

    driver_mock = MagicMock()
    driver_mock.current_url = "https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/ricette"
    driver_mock.window_handles = ["w1"]
    driver_mock.current_window_handle = "w1"
    driver_mock.execute_script.return_value = True

    flow = NewBookingFlow(
        {"id": "new-1", "ricetta": "VISITA DERMATOLOGICA", "nre": "0300A-1112223334"},
        MagicMock(driver=driver_mock), MagicMock(), MagicMock(), MagicMock()
    )
    flow._applica_filtro_specialistiche = MagicMock()

    res = flow._apri_ricetta_e_prenota()
    assert res is True

    # Verifica che il primo script execute_script abbia passato l'NRE pulito (senza trattino)
    args = driver_mock.execute_script.call_args_list[0][0]
    script_str, nre_arg = args[0], args[1]
    assert "clean" in script_str
    assert nre_arg == "0300a1112223334"


def test_reschedule_flow_poll_once_unblocks_done_or_pending_choice():
    from flows.reschedule import RescheduleFlow
    store = MagicMock()
    # Simula monitor reschedule con action_state 'done' (precedentemente bloccato)
    store.get_action.return_value = "done"
    store.get_prenotazione.return_value = {"data_ora": "25/10/2026 10:00"}

    flow = RescheduleFlow(
        {"id": "res-1", "ricetta": "VISITA UROLOGICA", "type": "reschedule"},
        MagicMock(), MagicMock(), MagicMock(), store
    )
    flow._is_autenticato = MagicMock(return_value=True)
    flow.load_page = MagicMock()
    flow._cambia_provincia_e_ricerca = MagicMock(return_value={"stato": "risultati", "provincia": ""})
    flow.check_availability = MagicMock(return_value=[])

    # Esegui poll_once sia automatico che manuale
    flow.poll_once(manual=False)

    # Verifica che sia stato riarmato a idle per consentire la continuazione del monitoraggio
    store.mark_action.assert_called_with("res-1", "idle", "riarmo monitor reschedule")
    flow.load_page.assert_called_once()


def test_new_booking_flow_poll_once_manual_auto_promotes_done_pending_choice():
    from flows.new_booking import NewBookingFlow
    store = MagicMock()
    store.get_action.return_value = "done_pending_choice"
    store.get_prenotazione.return_value = {"data_ora": "25/10/2026 10:00"}
    bot = MagicMock()

    flow = NewBookingFlow(
        {"id": "new-1", "ricetta": "VISITA DERMATOLOGICA", "type": "new", "criteri": {}},
        MagicMock(), MagicMock(), bot, store
    )
    flow._is_autenticato = MagicMock(return_value=True)
    flow.load_page = MagicMock()
    flow._cambia_provincia_e_ricerca = MagicMock(return_value={"stato": "risultati", "provincia": ""})
    flow.check_availability = MagicMock(return_value=[])

    # Un poll manuale su monitor in attesa scelta deve promuovere automaticamente a reschedule
    flow.poll_once(manual=True)

    assert flow.type == "reschedule"
    assert flow.monitor["type"] == "reschedule"
    assert flow.criteri["data_attuale"] == "25/10/2026 10:00"
    store.mark_action.assert_called_with("new-1", "idle", "promosso a reschedule da /poll manuale")
    bot.notify.assert_called()
    assert any("anticipare" in call[0][0] for call in bot.notify.call_args_list)





