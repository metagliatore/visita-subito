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

    mock_driver.current_url = "https://www.fascicolosanitario.regione.lombardia.it/accesso-spid"
    assert sm.is_autenticato(mock_driver) is False


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
