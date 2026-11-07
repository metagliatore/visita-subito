import base64
from pathlib import Path
from unittest.mock import MagicMock, call
import pytest

from core.auth import AuthConfig, PosteSpidAuthProvider, get_auth_provider
from core import selectors
from core.session import SessionManager


@pytest.fixture(autouse=True)
def virtual_clock(monkeypatch):
    """Ensure all tests use deterministic, bounded virtual time so loops never hang or bloat memory."""
    clock = [1000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    return clock


def test_auth_config_poste_defaults():
    cfg = AuthConfig(method="spid", spid_provider="poste")
    assert cfg.method == "spid"
    assert cfg.spid_provider == "poste"
    desc = cfg.describe()
    assert "PosteID" in desc
    assert "App notifica" in desc
    assert "semi-automatico" in desc


def test_auth_config_poste_env_overrides(monkeypatch):
    monkeypatch.setenv("SPID_PROVIDER", "poste")
    monkeypatch.setenv("POSTE_USERNAME", "mario.rossi@poste.it")
    monkeypatch.setenv("POSTE_PASSWORD", "Segreta123!")
    monkeypatch.setenv("POSTE_MODE", "sms")

    cfg = AuthConfig.from_settings_and_env({})
    assert cfg.spid_provider == "poste"
    assert cfg.spid_user == "mario.rossi@poste.it"
    assert cfg.spid_pwd == "Segreta123!"
    assert cfg.spid_otp_mode == "sms"
    desc = cfg.describe()
    assert "PosteID" in desc
    assert "SMS OTP" in desc
    assert "automatico" in desc


def test_auth_config_poste_qr_mode(monkeypatch):
    monkeypatch.setenv("SPID_PROVIDER", "poste")
    monkeypatch.setenv("POSTE_MODE", "qr")
    monkeypatch.delenv("POSTE_USERNAME", raising=False)
    monkeypatch.delenv("POSTE_PASSWORD", raising=False)
    monkeypatch.delenv("SPID_USERNAME", raising=False)
    monkeypatch.delenv("SPID_PASSWORD", raising=False)

    cfg = AuthConfig.from_settings_and_env({})
    assert cfg.spid_otp_mode == "qr"
    desc = cfg.describe()
    assert "PosteID" in desc
    assert "QR Code" in desc


def test_get_auth_provider_poste():
    cfg = AuthConfig(
        method="spid",
        spid_provider="poste",
        spid_user="user123",
        spid_pwd="password123",
        spid_otp_mode="sms",
    )
    provider = get_auth_provider(cfg)
    assert isinstance(provider, PosteSpidAuthProvider)
    assert provider.username == "user123"
    assert provider.password == "password123"
    assert provider.mode == "sms"


def test_poste_provider_login_dispatch():
    mock_sess = MagicMock()
    mock_sess.on_notify = None
    mock_browser = MagicMock()
    mock_notify = MagicMock()
    mock_auth_cb = MagicMock()

    provider = PosteSpidAuthProvider(username="POSTE_USER", password="POSTE_PASSWORD", mode="app")
    provider.login(mock_sess, mock_browser, notify_cb=mock_notify, auth_callback=mock_auth_cb)

    mock_sess.relogin_poste.assert_called_once_with(
        username="POSTE_USER",
        password="POSTE_PASSWORD",
        mode="app",
        auth_callback=mock_auth_cb,
    )
    assert mock_sess.on_notify == mock_notify


def test_relogin_poste_already_authenticated(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    monkeypatch.setattr(sm, "is_autenticato", lambda d: True)

    driver = sm.relogin_poste(username="u", password="p", wait_otp_sec=5)
    assert driver == mock_driver
    assert sm.session_valid is True
    assert not mock_driver.find_elements.called


def test_relogin_poste_push_success(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://posteid.poste.it/jod-fcc/custom/login.jsp"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    auth_state = [False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)
    monkeypatch.setattr(sm, "_find_element", lambda d, sel: None)

    filled_fields = {}
    clicked_buttons = []

    def mock_fill(d, sel, val):
        filled_fields[sel[0][1]] = val
        return True

    def mock_click(d, sel):
        clicked_buttons.append(sel[0][1] if sel else "")
        return True

    monkeypatch.setattr(sm, "_find_and_fill", mock_fill)
    monkeypatch.setattr(sm, "_find_and_click", mock_click)

    driver = sm.relogin_poste(username="mario.rossi", password="Password123!", mode="app", wait_otp_sec=30)
    assert driver == mock_driver
    assert sm.session_valid is True
    assert filled_fields.get("username") == "mario.rossi"
    assert filled_fields.get("password") == "Password123!"


def test_relogin_poste_sms_mode(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://posteid.poste.it/jod-fcc/custom/login.jsp"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    monkeypatch.setattr(sm, "_find_element", lambda d, sel: None)

    auth_state = [False, False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)

    filled_fields = {}
    clicked_selectors = []

    def mock_fill(d, sel, val):
        filled_fields[sel[0][1]] = val
        return True

    def mock_click(d, sel):
        clicked_selectors.append(sel)
        return True

    monkeypatch.setattr(sm, "_find_and_fill", mock_fill)
    monkeypatch.setattr(sm, "_find_and_click", mock_click)

    auth_cb = MagicMock(return_value="654321")

    driver = sm.relogin_poste(
        username="mario.rossi",
        password="Password123!",
        mode="sms",
        wait_otp_sec=30,
        auth_callback=auth_cb,
    )
    assert driver == mock_driver
    assert sm.session_valid is True
    auth_cb.assert_called_with("ask_otp", prompt="🔢 Inserisci il codice OTP ricevuto via SMS da Poste Italiane:")
    assert filled_fields.get("otp") == "654321"


def test_relogin_poste_direct_qr_mode(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://posteid.poste.it/jod-fcc/custom/login.jsp"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)

    auth_state = [False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)

    mock_qr_el = MagicMock()

    def mock_find_element(d, sel):
        if sel == selectors.LOGIN_IDP_POSTEID["qr_code"]:
            return mock_qr_el
        return None

    monkeypatch.setattr(sm, "_find_element", mock_find_element)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, sel: True)

    auth_cb = MagicMock()

    driver = sm.relogin_poste(username="", password="", mode="qr", wait_otp_sec=30, auth_callback=auth_cb)
    assert driver == mock_driver
    assert sm.session_valid is True
    mock_qr_el.screenshot.assert_called_once_with("data/poste_qr.png")
    auth_cb.assert_called_with(
        "send_photo",
        path="data/poste_qr.png",
        caption=(
            "📲 <b>Accesso SPID PosteID</b>\n\n"
            "• Inquadra questo codice QR con l'app <b>PosteID</b> (opzione <i>Inquadra codice QR</i> o <i>Scansiona QR</i>).\n"
            "• Autorizza l'accesso sul tuo smartphone confermando con codice PosteID o biometria (impronta/FaceID)."
        ),
    )


def test_relogin_poste_push_fallback_to_sms(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://posteid.poste.it/jod-fcc/custom/login.jsp"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)

    filled_fields = {}
    monkeypatch.setattr(sm, "_find_and_fill", lambda d, sel, val: filled_fields.__setitem__(sel[0][1], val) or True)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, sel: True)
    monkeypatch.setattr(sm, "_find_element", lambda d, sel: None)

    # User authenticates only after OTP is entered into the form
    monkeypatch.setattr(sm, "is_autenticato", lambda d: "otp" in filled_fields)

    def fake_auth_cb(action, **kwargs):
        if action == "ask_action":
            return "otp"
        if action == "ask_otp":
            return "112233"
        return None

    auth_cb = MagicMock(side_effect=fake_auth_cb)

    driver = sm.relogin_poste(
        username="mario.rossi",
        password="Password123!",
        mode="app",
        wait_otp_sec=60,
        auth_callback=auth_cb,
    )
    assert driver == mock_driver
    assert sm.session_valid is True
    assert filled_fields.get("otp") == "112233"
    assert any(c.args and c.args[0] == "ask_action" for c in auth_cb.call_args_list)


def test_relogin_poste_push_fallback_to_qr(tmp_path, monkeypatch, virtual_clock):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://posteid.poste.it/jod-fcc/custom/login.jsp"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)

    mock_qr_el = MagicMock()
    qr_sent_flag = [False]

    def mock_find_element(d, sel):
        if virtual_clock[0] > 1035 and sel == selectors.LOGIN_IDP_POSTEID["qr_code"]:
            return mock_qr_el
        return None

    monkeypatch.setattr(sm, "_find_element", mock_find_element)
    monkeypatch.setattr(sm, "_find_and_fill", lambda d, sel, val: True)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, sel: True)
    monkeypatch.setattr(sm, "is_autenticato", lambda d: qr_sent_flag[0])

    def fake_auth_cb(action, **kwargs):
        if action == "ask_action":
            return "qr"
        if action == "send_photo":
            qr_sent_flag[0] = True
            return True
        return None

    auth_cb = MagicMock(side_effect=fake_auth_cb)

    driver = sm.relogin_poste(
        username="mario.rossi",
        password="Password123!",
        mode="app",
        wait_otp_sec=60,
        auth_callback=auth_cb,
    )
    assert driver == mock_driver
    assert sm.session_valid is True
    assert any(c.args and c.args[0] == "send_photo" for c in auth_cb.call_args_list)


def test_relogin_poste_fallback_cancel(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://posteid.poste.it/jod-fcc/custom/login.jsp"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)

    monkeypatch.setattr(sm, "is_autenticato", lambda d: False)
    monkeypatch.setattr(sm, "_find_element", lambda d, sel: None)
    monkeypatch.setattr(sm, "_find_and_fill", lambda d, sel, val: True)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, sel: True)

    auth_cb = MagicMock(return_value="cancel")

    with pytest.raises(RuntimeError, match="Login SPID PosteID annullato"):
        sm.relogin_poste(
            username="mario.rossi",
            password="Password123!",
            mode="app",
            wait_otp_sec=60,
            auth_callback=auth_cb,
        )


def test_relogin_poste_timeout_error(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://posteid.poste.it/jod-fcc/custom/login.jsp"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)
    monkeypatch.setattr(sm, "is_autenticato", lambda d: False)
    monkeypatch.setattr(sm, "_find_element", lambda d, sel: None)
    monkeypatch.setattr(sm, "_find_and_fill", lambda d, sel, val: True)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, sel: True)

    with pytest.raises(TimeoutError, match="Timeout nell'attesa approvazione SPID PosteID"):
        sm.relogin_poste(username="u", password="p", mode="app", wait_otp_sec=1)


def test_relogin_poste_credentials_tab_switch(tmp_path, monkeypatch):
    mock_browser = MagicMock()
    mock_driver = MagicMock()
    mock_driver.get_cookies.return_value = []
    mock_browser.start.return_value = mock_driver
    mock_driver.current_url = "https://posteid.poste.it/jod-fcc/custom/login.jsp"

    sm = SessionManager(browser=mock_browser, cookie_dir=tmp_path)

    auth_state = [False, False, True]
    monkeypatch.setattr(sm, "is_autenticato", lambda d: auth_state.pop(0) if auth_state else True)

    username_visible = [False, True]

    def mock_find_element(d, sel):
        if sel == selectors.LOGIN_IDP_POSTEID["username"]:
            return MagicMock() if username_visible.pop(0) else None
        return None

    clicked = []

    def mock_click(d, sel):
        clicked.append(sel)
        return True

    monkeypatch.setattr(sm, "_find_element", mock_find_element)
    monkeypatch.setattr(sm, "_find_and_fill", lambda d, sel, val: True)
    monkeypatch.setattr(sm, "_find_and_click", mock_click)

    driver = sm.relogin_poste(username="u", password="p", mode="app", wait_otp_sec=30)
    assert driver == mock_driver
    assert any(sel == selectors.LOGIN_IDP_POSTEID["btn_credenziali"] for sel in clicked)


def test_relogin_spid_delegates_to_relogin_poste(tmp_path, monkeypatch):
    sm = SessionManager(browser=MagicMock(), cookie_dir=tmp_path)
    mock_relogin_poste = MagicMock()
    sm.relogin_poste = mock_relogin_poste

    sm.relogin_spid(
        provider_key="poste",
        username="user123",
        password="pwd",
        wait_otp_sec=120,
        auth_callback="fake_cb",
    )
    mock_relogin_poste.assert_called_once_with(
        username="user123",
        password="pwd",
        wait_otp_sec=120,
        auth_callback="fake_cb",
    )


def test_cattura_qr_poste_data_uri_and_fallbacks(tmp_path, monkeypatch):
    sm = SessionManager(browser=MagicMock(), cookie_dir=tmp_path)

    mock_driver = MagicMock()
    mock_qr_el = MagicMock()

    # Case 1: data-URI extraction
    tiny_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    mock_qr_el.get_attribute.return_value = f"data:image/png;base64,{tiny_png_b64}"
    monkeypatch.setattr(sm, "_find_element", lambda d, sel: mock_qr_el)

    auth_cb = MagicMock()
    assert sm._cattura_e_invia_qr_poste(mock_driver, auth_callback=auth_cb) is True
    assert Path("data/poste_qr.png").exists()
    assert Path("data/poste_qr.png").read_bytes() == base64.b64decode(tiny_png_b64)
    assert any(c.args and c.args[0] == "send_photo" for c in auth_cb.call_args_list)

    # Case 2: send_photo raises exception, falls back to send_file
    mock_qr_el.get_attribute.return_value = ""
    auth_cb_err = MagicMock()
    auth_cb_err.side_effect = [Exception("send_photo error"), None]
    assert sm._cattura_e_invia_qr_poste(mock_driver, auth_callback=auth_cb_err) is True
    assert any(c.args and c.args[0] == "send_photo" for c in auth_cb_err.call_args_list)
    assert any(c.args and c.args[0] == "send_file" for c in auth_cb_err.call_args_list)


def test_chiedi_e_inserisci_otp_poste_fallbacks(tmp_path, monkeypatch):
    sm = SessionManager(browser=MagicMock(), cookie_dir=tmp_path)
    mock_driver = MagicMock()

    # Case 1: spaces and dashes cleaned
    filled = {}
    monkeypatch.setattr(sm, "_find_and_fill", lambda d, sel, val: filled.__setitem__("code", val) or True)
    monkeypatch.setattr(sm, "_find_and_click", lambda d, sel: True)
    monkeypatch.setattr(sm, "is_autenticato", lambda d: True)

    auth_cb = MagicMock(return_value=" 123 - 456 ")
    assert sm._chiedi_e_inserisci_otp_poste(mock_driver, auth_callback=auth_cb) is True
    assert filled["code"] == "123456"

    # Case 2: on_otp_prompt fallback
    sm.on_otp_prompt = MagicMock(return_value="999888")
    assert sm._chiedi_e_inserisci_otp_poste(mock_driver, auth_callback=None) is True
    assert filled["code"] == "999888"

    # Case 3: no code provided
    sm.on_otp_prompt = None
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert sm._chiedi_e_inserisci_otp_poste(mock_driver, auth_callback=None) is False
