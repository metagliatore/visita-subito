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

    # Senza file meta.json -> scaduta
    assert sm.is_expired(max_idle_seconds=3600) is True

    # Con salvataggio recente (10 secondi fa) -> non scaduta
    meta_file = tmp_path / "meta.json"
    meta_file.write_text(json.dumps({"saved_at": time.time() - 10}), encoding="utf-8")
    assert sm.is_expired(max_idle_seconds=3600) is False

    # Con salvataggio vecchio (2 ore fa) -> scaduta
    meta_file.write_text(json.dumps({"saved_at": time.time() - 7200}), encoding="utf-8")
    assert sm.is_expired(max_idle_seconds=3600) is True


def test_needs_login(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)

    # Senza cookie -> serve login
    assert sm.needs_login() is True

    # Creiamo cookies.pkl
    (tmp_path / "cookies.pkl").write_bytes(b"dummy")
    assert sm.needs_login() is False


def test_invalidate(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)
    sm.session_valid = True
    sm.invalidate()
    assert sm.session_valid is False


def test_save_and_load_cookies(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)
    mock_driver = MagicMock()
    fake_cookies = [{"name": "SESSION_ID", "value": "xyz123", "domain": ".regione.lombardia.it"}]
    mock_driver.get_cookies.return_value = fake_cookies

    # Salva
    sm.save(mock_driver)
    assert sm.cookie_file.exists()
    assert sm.meta_file.exists()

    # Carica
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

    # Caso fallimento
    mock_driver.find_element.side_effect = Exception("Not found")
    res_fail = sm._find_and_fill(mock_driver, [("id", "notfound")], "val")
    assert res_fail is False


def test_find_and_click(tmp_path):
    sm = SessionManager(browser=None, cookie_dir=tmp_path)
    mock_driver = MagicMock()
    mock_el = MagicMock()
    mock_driver.find_element.return_value = mock_el

    # Click normale
    res = sm._find_and_click(mock_driver, [("css", "button.submit")])
    assert res is True
    mock_el.click.assert_called_once()

    # Fallback execute_script se click lancia eccezione
    mock_el.click.side_effect = Exception("Click intercepted")
    res_fallback = sm._find_and_click(mock_driver, [("css", "button.submit")])
    assert res_fallback is True
    mock_driver.execute_script.assert_called_with("arguments[0].click();", mock_el)

    # Fallimento completo
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

    # Autenticato solo dopo inserimento OTP (quando il clock supera 1055)
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

    # Autenticato durante la seconda notifica (quando il clock supera 1060)
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


