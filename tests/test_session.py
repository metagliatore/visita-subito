import json
import time
from unittest.mock import MagicMock
import pytest

from core.session import SessionManager


def test_is_autenticato(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)
    mock_driver = MagicMock()

    mock_driver.current_url = "https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/home?param=1"
    assert sm.is_autenticato(mock_driver) is True

    mock_driver.current_url = "https://www.fascicolosanitario.regione.lombardia.it/prenotaonline/riservata"
    assert sm.is_autenticato(mock_driver) is True

    mock_driver.current_url = "https://identity.sieltecloud.it/login"
    assert sm.is_autenticato(mock_driver) is False

    mock_driver.current_url = "https://idpcwrapper.crs.lombardia.it/PublisherMetadata/SSOService"
    assert sm.is_autenticato(mock_driver) is False

    mock_driver.current_url = "https://www.fascicolosanitario.regione.lombardia.it/accesso-spid"
    assert sm.is_autenticato(mock_driver) is False

    assert sm.is_autenticato(None) is False


def test_is_expired(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)

    # Without meta.json file -> expired
    assert sm.is_expired(max_idle_seconds=3600) is True

    # With recent save (10 seconds ago) -> not expired
    meta_file = tmp_path / "meta.json"
    meta_file.write_text(json.dumps({"saved_at": time.time() - 10}), encoding="utf-8")
    assert sm.is_expired(max_idle_seconds=3600) is False

    # With old save (2 hours ago) -> expired
    meta_file.write_text(json.dumps({"saved_at": time.time() - 7200}), encoding="utf-8")
    assert sm.is_expired(max_idle_seconds=3600) is True


def test_needs_login(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)

    # Without cookies -> login required
    assert sm.needs_login() is True

    # Create cookies.pkl
    (tmp_path / "cookies.pkl").write_bytes(b"dummy")
    assert sm.needs_login() is False


def test_invalidate(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)
    sm.session_valid = True
    (tmp_path / "cookies.pkl").write_bytes(b"dummy")
    (tmp_path / "meta.json").write_text("{}", encoding="utf-8")

    sm.invalidate()
    assert sm.session_valid is False
    assert not (tmp_path / "cookies.pkl").exists()
    assert not (tmp_path / "meta.json").exists()
    assert sm.needs_login() is True


def test_save_and_load_cookies(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)
    mock_driver = MagicMock()
    fake_cookies = [{"name": "SESSION_ID", "value": "xyz123", "domain": ".regione.lombardia.it"}]
    mock_driver.get_cookies.return_value = fake_cookies

    # Save
    sm.save(mock_driver)
    assert sm.cookie_file.exists()
    assert sm.meta_file.exists()

    # Load
    load_driver = MagicMock()
    success = sm.load_cookies(load_driver)
    assert success is True
    load_driver.add_cookie.assert_called_once_with(fake_cookies[0])


def test_find_and_fill(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)
    mock_driver = MagicMock()
    mock_el = MagicMock()
    mock_driver.find_element.return_value = mock_el

    res = sm._find_and_fill(mock_driver, [("id", "username")], "testuser")
    assert res is True
    mock_el.clear.assert_called_once()
    mock_el.send_keys.assert_called_once_with("testuser")

    # Failure case
    mock_driver.find_element.side_effect = Exception("Not found")
    res_fail = sm._find_and_fill(mock_driver, [("id", "notfound")], "val")
    assert res_fail is False


def test_find_and_click(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)
    mock_driver = MagicMock()
    mock_el = MagicMock()
    mock_driver.find_element.return_value = mock_el

    # Normal click
    res = sm._find_and_click(mock_driver, [("css", "button.submit")])
    assert res is True
    mock_el.click.assert_called_once()

    # Fallback execute_script if click raises exception
    mock_el.click.side_effect = Exception("Click intercepted")
    res_fallback = sm._find_and_click(mock_driver, [("css", "button.submit")])
    assert res_fallback is True
    mock_driver.execute_script.assert_called_with("arguments[0].click();", mock_el)

    # Complete failure
    mock_driver.find_element.side_effect = Exception("Not found")
    res_fail = sm._find_and_click(mock_driver, [("xpath", "//missing")])
    assert res_fail is False


def test_relogin_spid_already_authenticated(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/home"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    # is_autenticato ritorna True
    monkeypatch.setattr(sm, "is_autenticato", lambda d: True)

    driver = sm.relogin_spid("poste", username="u", password="p", wait_otp_sec=5)
    assert driver == mock_driver
    assert sm.session_valid is True


def test_relogin_cie_already_authenticated(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/home"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    monkeypatch.setattr(sm, "is_autenticato", lambda d: True)

    driver = sm.relogin_cie(username="cf", password="pwd", mode="app", wait_otp_sec=5)
    assert driver == mock_driver
    assert sm.session_valid is True


def test_relogin_sielte_push_success(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://identity.sieltecloud.it/loginform.php"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    auth_state = [False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)
    monkeypatch.setattr("time.sleep", lambda s: None)

    driver = sm.relogin_sielte(username="user", password="pwd", wait_otp_sec=30)
    assert driver == mock_driver
    assert sm.session_valid is True


def test_relogin_sielte_fallback_to_otp(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://identity.sieltecloud.it/loginform.php"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    sm.on_auth_fallback = MagicMock(return_value="otp")
    sm.on_otp_prompt = MagicMock(return_value="123456")

    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__(0, clock[0] + s))

    # Authenticated only after OTP entry (when clock exceeds 1055)
    monkeypatch.setattr(sm, "is_autenticato", lambda d: clock[0] > 1055)
    monkeypatch.setattr(sm, "_inserisci_otp_sielte", MagicMock(return_value=True))

    driver = sm.relogin_sielte(username="user", password="pwd", wait_otp_sec=180)
    assert driver == mock_driver
    assert sm.session_valid is True
    sm.on_auth_fallback.assert_called_once()
    sm.on_otp_prompt.assert_called_once()
    sm._inserisci_otp_sielte.assert_called_once_with(mock_driver, "123456")


def test_relogin_sielte_fallback_re_notify(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://identity.sieltecloud.it/loginform.php"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    sm.on_auth_fallback = MagicMock(return_value="notify")

    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__(0, clock[0] + s))

    # Authenticated during second notification (when clock exceeds 1060)
    monkeypatch.setattr(sm, "is_autenticato", lambda d: clock[0] > 1060)
    notify_mock = MagicMock(return_value=True)
    monkeypatch.setattr(sm, "_invia_notifica_sielte", notify_mock)

    driver = sm.relogin_sielte(username="user", password="pwd", wait_otp_sec=180)
    assert driver == mock_driver
    assert sm.session_valid is True
    sm.on_auth_fallback.assert_called_once()
    assert notify_mock.call_count >= 2


def test_relogin_sielte_fallback_cancel(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://identity.sieltecloud.it/loginform.php"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    sm.on_auth_fallback = MagicMock(return_value="cancel")
    monkeypatch.setattr(sm, "is_autenticato", lambda d: False)

    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__(0, clock[0] + s))

    import pytest
    with pytest.raises(RuntimeError, match="annullato"):
        sm.relogin_sielte(username="user", password="pwd", wait_otp_sec=180)


def test_relogin_sielte_direct_otp_mode(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://identity.sieltecloud.it/loginform.php"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    sm.on_otp_prompt = MagicMock(return_value="654321")

    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(sm, "is_autenticato", lambda d: clock[0] > 1020)

    mock_activate = MagicMock(return_value=True)
    mock_fill = MagicMock(return_value=True)
    monkeypatch.setattr(sm, "_attiva_otp_sielte", mock_activate)
    monkeypatch.setattr(sm, "_inserisci_otp_sielte", mock_fill)

    driver = sm.relogin_sielte(username="user", password="pwd", otp_mode="otp", wait_otp_sec=60)
    assert driver == mock_driver
    assert sm.session_valid is True
    mock_activate.assert_called_once()
    mock_fill.assert_called_once_with(mock_driver, "654321")


def test_relogin_sielte_preserves_browser_and_headless_mode(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_browser.settings.headless = True
    mock_driver.current_url = "https://identity.sieltecloud.it/loginform.php"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    monkeypatch.setattr(sm, "is_autenticato", lambda d: True)
    monkeypatch.setattr("time.sleep", lambda s: None)

    driver = sm.relogin_sielte(username="user", password="pwd", wait_otp_sec=30)
    assert driver == mock_driver
    # Browser.stop() must never be called
    mock_browser.stop.assert_not_called()
    # Browser.set_headless must not be called to kill the driver
    mock_browser.set_headless.assert_not_called()


def test_relogin_cie_livello2_credentials_success(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://idserver.servizicie.interno.gov.it/idp/login/livello2"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    auth_state = [False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)
    monkeypatch.setattr("time.sleep", lambda s: None)

    filled_fields = {}

    def mock_fill(d, sel, val):
        filled_fields[sel[0][1]] = val
        return True

    clicked_buttons = []

    def mock_click(d, sel):
        clicked_buttons.append(sel[0][1] if sel else "")
        return True

    monkeypatch.setattr(sm, "_find_and_fill", mock_fill)
    monkeypatch.setattr(sm, "_find_and_click", mock_click)

    driver = sm.relogin_cie(username="RSSMRA80A01H501U", password="Password123!", mode="app", wait_otp_sec=30)
    assert driver == mock_driver
    assert sm.session_valid is True
    assert filled_fields.get("username") == "RSSMRA80A01H501U"
    assert filled_fields.get("password") == "Password123!"


def test_relogin_cie_livello2_otp_prompt(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://idserver.servizicie.interno.gov.it/idp/login/livello2"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    monkeypatch.setattr("time.sleep", lambda s: None)

    auth_state = [False, False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)

    mock_otp_el = MagicMock()

    def mock_find_element(d, sel):
        from core import selectors
        if sel == selectors.LOGIN_CIE["otp"]:
            return mock_otp_el
        return None

    monkeypatch.setattr(sm, "_find_element", mock_find_element)

    filled_fields = {}
    monkeypatch.setattr(sm, "_find_and_fill", lambda d, sel, val: filled_fields.__setitem__(sel[0][1], val) or True)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, sel: True)

    auth_cb = MagicMock(return_value="987654")

    driver = sm.relogin_cie(username="CF123", password="PWD", mode="app", wait_otp_sec=30, auth_callback=auth_cb)
    assert driver == mock_driver
    assert sm.session_valid is True
    auth_cb.assert_called_with("ask_otp", prompt="🔢 Inserisci il codice OTP ricevuto via SMS o generato dall'app CieID:")
    assert filled_fields.get("otp") == "987654"


def test_relogin_cie_qr_code_send_file(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://idserver.servizicie.interno.gov.it/idp/login/livello2"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    monkeypatch.setattr("time.sleep", lambda s: None)

    auth_state = [False, False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)

    mock_qr_el = MagicMock()

    def mock_find_element(d, sel):
        from core import selectors
        if sel == selectors.LOGIN_CIE["qr_code"]:
            return mock_qr_el
        return None

    monkeypatch.setattr(sm, "_find_element", mock_find_element)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, sel: True)

    auth_cb = MagicMock()

    driver = sm.relogin_cie(username="", password="", mode="app", wait_otp_sec=30, auth_callback=auth_cb)
    assert driver == mock_driver
    assert sm.session_valid is True
    mock_qr_el.screenshot.assert_called_once_with("data/cie_qr.png")
    auth_cb.assert_called_with("send_photo", path="data/cie_qr.png",
                               caption="📲 <b>Accesso CIE (Livello 2)</b>\nScansiona questo QR Code con l'app CieID (opzione <i>Scansiona codice QR</i>) per completare l'accesso.",
                               buttons=[])


def test_relogin_cie_direct_qr_mode(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://idserver.servizicie.interno.gov.it/idp/login/livello2"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    monkeypatch.setattr("time.sleep", lambda s: None)

    auth_state = [False, False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)

    mock_qr_el = MagicMock()
    clicked_sels = []

    # Facciamo in modo che qr_code non sia presente all'inizio, così prova a cliccare btn_smartphone
    qr_calls = [0]
    def mock_find_element(d, sel):
        from core import selectors
        if sel == selectors.LOGIN_CIE["qr_code"]:
            qr_calls[0] += 1
            if qr_calls[0] > 1:
                return mock_qr_el
            return None
        return None

    monkeypatch.setattr(sm, "_find_element", mock_find_element)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, sel: clicked_sels.append(sel) or True)

    auth_cb = MagicMock()

    driver = sm.relogin_cie(username="", password="", mode="qr", wait_otp_sec=30, auth_callback=auth_cb)
    assert driver == mock_driver
    assert sm.session_valid is True
    from core import selectors
    assert any(selectors.LOGIN_CIE["btn_smartphone"] == c for c in clicked_sels)
    auth_cb.assert_called_with("send_photo", path="data/cie_qr.png",
                               caption="📲 <b>Accesso CIE (Livello 2)</b>\nScansiona questo QR Code con l'app CieID (opzione <i>Scansiona codice QR</i>) per completare l'accesso.",
                               buttons=[])


def test_relogin_cie_with_deep_link_button(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://idserver.servizicie.interno.gov.it/idp/login/livello2"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    monkeypatch.setattr("time.sleep", lambda s: None)

    auth_state = [False, False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)

    mock_qr_el = MagicMock()
    mock_link_el = MagicMock()
    mock_link_el.get_attribute.return_value = "https://ios.idserver.servizicie.interno.gov.it/idp/login/app?opId=123"

    def mock_find_element(d, sel):
        from core import selectors
        if sel == selectors.LOGIN_CIE["qr_code"]:
            return mock_qr_el
        if sel == selectors.LOGIN_CIE["link_app_cie"]:
            return mock_link_el
        return None

    monkeypatch.setattr(sm, "_find_element", mock_find_element)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, sel: True)

    auth_cb = MagicMock()

    driver = sm.relogin_cie(username="", password="", mode="qr", wait_otp_sec=30, auth_callback=auth_cb)
    assert driver == mock_driver
    assert sm.session_valid is True
    call_args = auth_cb.call_args[1]
    assert call_args["buttons"] == [[("📲 Apri in App CieID", "https://ios.idserver.servizicie.interno.gov.it/idp/login/app?opId=123")]]
    assert "Da questo smartphone" in call_args["caption"]


def test_relogin_cie_push_fallback_to_qr(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://idserver.servizicie.interno.gov.it/idp/login/livello2"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)

    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__(0, clock[0] + s))

    mock_qr_el = MagicMock()

    def mock_find_element(d, s):
        from core import selectors
        # Ritorna qr_el solo dopo 40 secondi (quando si attiva il fallback QR)
        if clock[0] > 1040 and s == selectors.LOGIN_CIE["qr_code"]:
            return mock_qr_el
        return None

    clicked_sels = []
    monkeypatch.setattr(sm, "_find_element", mock_find_element)
    monkeypatch.setattr(sm, "_find_and_fill", lambda d, s, v: True)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, s: clicked_sels.append(s) or True)

    monkeypatch.setattr(sm, "is_autenticato", lambda d: clock[0] > 1050)

    auth_cb = MagicMock()

    def mock_cb(action, **kw):
        if action == "ask_action":
            return "qr"
        return None

    auth_cb.side_effect = mock_cb

    driver = sm.relogin_cie(username="CF123", password="PWD", mode="app", wait_otp_sec=60, auth_callback=auth_cb)
    assert driver == mock_driver
    assert sm.session_valid is True
    from core import selectors
    assert any(selectors.LOGIN_CIE["btn_mostra_qr"] == c for c in clicked_sels)
    auth_cb.assert_any_call("send_photo", path="data/cie_qr.png",
                            caption="📲 <b>Accesso CIE (Livello 2)</b>\nScansiona questo QR Code con l'app CieID (opzione <i>Scansiona codice QR</i>) per completare l'accesso.",
                            buttons=[])


def test_relogin_cie_push_fallback_cancel(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://idserver.servizicie.interno.gov.it/idp/login/livello2"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)

    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(sm, "is_autenticato", lambda d: False)
    monkeypatch.setattr(sm, "_find_element", lambda d, s: None)
    monkeypatch.setattr(sm, "_find_and_fill", lambda d, s, v: True)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, s: True)

    auth_cb = MagicMock(return_value="cancel")

    with pytest.raises(RuntimeError, match="annullato dall'utente"):
        sm.relogin_cie(username="CF123", password="PWD", mode="app", wait_otp_sec=60, auth_callback=auth_cb)


def test_relogin_cie_smartcard_mode(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://idserver.servizicie.interno.gov.it/idp/login/livello2"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    monkeypatch.setattr("time.sleep", lambda s: None)

    auth_state = [False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)

    clicked = []
    monkeypatch.setattr(sm, "_find_and_click", lambda d, s: clicked.append(s) or True)

    driver = sm.relogin_cie(username="", password="", mode="smartcard", wait_otp_sec=30)
    assert driver == mock_driver
    assert sm.session_valid is True
    from core import selectors
    assert any(selectors.LOGIN_CIE["btn_computer"] == c for c in clicked)




