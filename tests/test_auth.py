from unittest.mock import MagicMock
import pytest

from core.auth import (
    AuthConfig,
    SPID_PROVIDERS,
    SielteSpidAuthProvider,
    PosteSpidAuthProvider,
    ArubaSpidAuthProvider,
    InfocertSpidAuthProvider,
    LepidaSpidAuthProvider,
    NamirialSpidAuthProvider,
    GenericSpidAuthProvider,
    CieAuthProvider,
    ManualAuthProvider,
    get_auth_provider,
)


def test_auth_config_defaults():
    cfg = AuthConfig()
    assert cfg.method == "spid"
    assert cfg.spid_provider == "sielte"
    assert cfg.cie_mode == "app"
    assert "SielteID" in cfg.describe()


def test_auth_config_env_overrides(monkeypatch):
    monkeypatch.setenv("AUTH_METHOD", "cie")
    monkeypatch.setenv("CIE_MODE", "smartcard")
    monkeypatch.setenv("CIE_USERNAME", "TEST_CIE_USER")

    cfg = AuthConfig.from_settings_and_env({})
    assert cfg.method == "cie"
    assert cfg.cie_mode == "smartcard"
    assert cfg.cie_user == "TEST_CIE_USER"
    assert "Smartcard/NFC" in cfg.describe()


def test_auth_config_spid_retrocompatibility(monkeypatch):
    monkeypatch.delenv("AUTH_METHOD", raising=False)
    monkeypatch.delenv("SPID_PROVIDER", raising=False)
    monkeypatch.delenv("SPID_USERNAME", raising=False)
    monkeypatch.delenv("SPID_PASSWORD", raising=False)

    # Impostiamo le vecchie SIELTE_USERNAME / SIELTE_PASSWORD
    monkeypatch.setenv("SIELTE_USERNAME", "CODICEFISCALE123")
    monkeypatch.setenv("SIELTE_PASSWORD", "Segreta123!")

    cfg = AuthConfig.from_settings_and_env({})
    assert cfg.method == "spid"
    assert cfg.spid_provider == "sielte"
    assert cfg.spid_user == "CODICEFISCALE123"
    assert cfg.spid_pwd == "Segreta123!"


def test_auth_config_describe_providers():
    cfg_poste = AuthConfig(method="spid", spid_provider="poste")
    assert "PosteID" in cfg_poste.describe()

    cfg_aruba = AuthConfig(method="spid", spid_provider="aruba")
    assert "Aruba ID" in cfg_aruba.describe()

    cfg_cie_app = AuthConfig(method="cie", cie_mode="app")
    assert "CieID" in cfg_cie_app.describe()

    cfg_manual = AuthConfig(method="manual")
    assert "Manuale" in cfg_manual.describe()


def test_get_auth_provider_factory():
    # Sielte
    p_sielte = get_auth_provider(AuthConfig(method="spid", spid_provider="sielte", spid_user="u", spid_pwd="p"))
    assert isinstance(p_sielte, SielteSpidAuthProvider)
    assert p_sielte.username == "u"

    # Poste
    p_poste = get_auth_provider(AuthConfig(method="spid", spid_provider="poste", spid_user="u", spid_pwd="p"))
    assert isinstance(p_poste, PosteSpidAuthProvider)
    assert p_poste.username == "u"

    # Aruba
    p_aruba = get_auth_provider(AuthConfig(method="spid", spid_provider="aruba", spid_user="u", spid_pwd="p"))
    assert isinstance(p_aruba, ArubaSpidAuthProvider)

    # InfoCert
    p_infocert = get_auth_provider(AuthConfig(method="spid", spid_provider="infocert"))
    assert isinstance(p_infocert, InfocertSpidAuthProvider)

    # Lepida
    p_lepida = get_auth_provider(AuthConfig(method="spid", spid_provider="lepida"))
    assert isinstance(p_lepida, LepidaSpidAuthProvider)

    # Namirial
    p_namirial = get_auth_provider(AuthConfig(method="spid", spid_provider="namirial"))
    assert isinstance(p_namirial, NamirialSpidAuthProvider)

    # Generic SPID (e.g. Tim, Register, etc.)
    p_generic = get_auth_provider(AuthConfig(method="spid", spid_provider="tim"))
    assert isinstance(p_generic, GenericSpidAuthProvider)
    assert p_generic.provider_key == "tim"

    # CIE
    p_cie = get_auth_provider(AuthConfig(method="cie", cie_mode="app", cie_user="c_u", cie_pwd="c_p"))
    assert isinstance(p_cie, CieAuthProvider)
    assert p_cie.mode == "app"
    assert p_cie.username == "c_u"

    # Manual
    p_manual = get_auth_provider(AuthConfig(method="manual"))
    assert isinstance(p_manual, ManualAuthProvider)


def test_spid_providers_login():
    mock_sess = MagicMock()
    mock_browser = MagicMock()

    # Poste
    p_poste = PosteSpidAuthProvider(username="POSTE_U", password="PWD")
    p_poste.login(mock_sess, mock_browser)
    mock_sess.relogin_spid.assert_called_with("poste", username="POSTE_U", password="PWD")

    # Aruba
    p_aruba = ArubaSpidAuthProvider(username="ARUBA_U", password="PWD")
    p_aruba.login(mock_sess, mock_browser)
    mock_sess.relogin_spid.assert_called_with("aruba", username="ARUBA_U", password="PWD")

    # Infocert
    p_infocert = InfocertSpidAuthProvider(username="INFO_U", password="PWD")
    p_infocert.login(mock_sess, mock_browser)
    mock_sess.relogin_spid.assert_called_with("infocert", username="INFO_U", password="PWD")

    # Lepida
    p_lepida = LepidaSpidAuthProvider(username="LEP_U", password="PWD")
    p_lepida.login(mock_sess, mock_browser)
    mock_sess.relogin_spid.assert_called_with("lepida", username="LEP_U", password="PWD")

    # Namirial
    p_namirial = NamirialSpidAuthProvider(username="NAM_U", password="PWD")
    p_namirial.login(mock_sess, mock_browser)
    mock_sess.relogin_spid.assert_called_with("namirial", username="NAM_U", password="PWD")

    # Generic
    p_gen = GenericSpidAuthProvider(provider_key="tim", username="TIM_U", password="PWD")
    p_gen.login(mock_sess, mock_browser)
    mock_sess.relogin_spid.assert_called_with("tim", username="TIM_U", password="PWD")


def test_cie_provider_login():
    mock_sess = MagicMock()
    mock_browser = MagicMock()

    p_cie = CieAuthProvider(mode="app", username="CF12345", password="PWD")
    p_cie.login(mock_sess, mock_browser)
    mock_sess.relogin_cie.assert_called_once_with(username="CF12345", password="PWD", mode="app")


def test_sielte_provider_login_with_credentials():
    provider = SielteSpidAuthProvider(username="USER123", password="PWD")
    mock_sess = MagicMock()
    mock_browser = MagicMock()

    provider.login(session_manager=mock_sess, browser=mock_browser)
    mock_sess.relogin_sielte.assert_called_once_with(username="USER123", password="PWD")


def test_sielte_provider_login_without_credentials():
    provider = SielteSpidAuthProvider(username="", password="")
    mock_sess = MagicMock()
    mock_browser = MagicMock()
    mock_notify = MagicMock()

    provider.login(session_manager=mock_sess, browser=mock_browser, notify_cb=mock_notify)
    mock_sess.relogin_manual.assert_called_once()
    mock_notify.assert_called_once()


def test_manual_provider_login():
    provider = ManualAuthProvider()
    mock_sess = MagicMock()
    mock_browser = MagicMock()
    mock_notify = MagicMock()

    provider.login(session_manager=mock_sess, browser=mock_browser, notify_cb=mock_notify)
    mock_sess.relogin_manual.assert_called_once()
    mock_notify.assert_called_once()
