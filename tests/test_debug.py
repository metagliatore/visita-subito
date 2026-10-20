import os
from pathlib import Path
from unittest.mock import MagicMock

from core.debug import DEFAULT_DEBUG_DIR, get_debug_dir, salva_debug_dump


def test_get_debug_dir_default(monkeypatch):
    monkeypatch.delenv("DEBUG_DUMP_DIR", raising=False)
    d = get_debug_dir()
    assert d == Path(DEFAULT_DEBUG_DIR)


def test_get_debug_dir_custom_env(tmp_path, monkeypatch):
    custom_dir = tmp_path / "custom_dumps"
    monkeypatch.setenv("DEBUG_DUMP_DIR", str(custom_dir))
    d = get_debug_dir()
    assert d == custom_dir
    assert custom_dir.exists()


def test_salva_debug_dump_success(tmp_path, monkeypatch):
    custom_dir = tmp_path / "debug_out"
    monkeypatch.setenv("DEBUG_DUMP_DIR", str(custom_dir))

    mock_driver = MagicMock()
    mock_driver.page_source = "<html><body>Test Page Content</body></html>"

    res = salva_debug_dump(mock_driver, tag="test_tag", prefix="monitor_1")
    assert res is not None
    assert "ref" in res
    assert "monitor_1_test_tag" in res["ref"]
    assert res["dir"] == str(custom_dir)
    assert f"{res['ref']}.png" in res["message"]
    assert str(custom_dir) in res["message"]

    # Verify HTML writing
    html_file = Path(res["html_path"])
    assert html_file.exists()
    assert "Test Page Content" in html_file.read_text(encoding="utf-8")

    # Verify save_screenshot call with expected path
    mock_driver.save_screenshot.assert_called_once_with(str(res["png_path"]))


def test_salva_debug_dump_driver_none():
    res = salva_debug_dump(None, tag="driver_none")
    assert res is None


def test_salva_debug_dump_handles_screenshot_error(tmp_path, monkeypatch):
    custom_dir = tmp_path / "debug_out_err"
    monkeypatch.setenv("DEBUG_DUMP_DIR", str(custom_dir))

    mock_driver = MagicMock()
    mock_driver.page_source = "<html>Error Screen Test</html>"
    mock_driver.save_screenshot.side_effect = RuntimeError("Screenshot failed")

    res = salva_debug_dump(mock_driver, tag="scr_fail")
    # Must not crash, should return None
    assert res is None

