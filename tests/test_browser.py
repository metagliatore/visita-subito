import pytest
from core.browser import Browser, BrowserSettings


def test_browser_options():
    settings = BrowserSettings(
        binary_path="/usr/bin/google-chrome",
        headless=True,
        window_size=(1920, 1080),
        timeouts={"page": 45, "implicit": 10}
    )
    browser = Browser(settings)
    options = browser._options()

    assert options.binary_location == "/usr/bin/google-chrome"
    args = options.arguments
    assert "--no-sandbox" in args
    assert "--disable-dev-shm-usage" in args
    assert "--headless=new" in args
    assert "--window-size=1920,1080" in args
    assert options.page_load_strategy == "eager"


def test_browser_options_visible():
    settings = BrowserSettings(headless=False)
    browser = Browser(settings)
    options = browser._options()
    assert "--headless=new" not in options.arguments


def test_browser_default_implicit_wait_is_zero(monkeypatch):
    from unittest.mock import MagicMock
    mock_chrome_cls = MagicMock()
    mock_driver = MagicMock()
    mock_chrome_cls.return_value = mock_driver

    import core.browser as browser_mod
    monkeypatch.setattr(browser_mod.webdriver, "Chrome", mock_chrome_cls)

    b = Browser(BrowserSettings())
    b.start()
    mock_driver.implicitly_wait.assert_called_once_with(0)


def test_selectors_no_invalid_has_text():
    import core.selectors as sel
    # Iterate over all dictionary constants in selectors and verify that there is no ':has-text'
    def check_obj(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                check_obj(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, tuple) and len(item) == 2:
                    strat, val = item
                    if strat == "css":
                        assert ":has-text" not in val, f"Invalid CSS :has-text found in {path}: {val}"
                elif isinstance(item, dict):
                    check_obj(item, path)

    for attr in dir(sel):
        if attr.isupper():
            check_obj(getattr(sel, attr), attr)


def test_browser_recovers_from_invalid_binary_path(monkeypatch):
    from unittest.mock import MagicMock
    from selenium.common.exceptions import NoSuchDriverException
    import core.browser as browser_mod

    mock_driver = MagicMock()
    call_count = 0

    def mock_chrome(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        options = kwargs.get("options")
        # The first attempt with the non-existent binary path fails
        if call_count == 1 and getattr(options, "binary_location", "") == "/percorso/inesistente/chrome":
            raise NoSuchDriverException("Unable to obtain driver for chrome")
        return mock_driver

    monkeypatch.setattr(browser_mod.webdriver, "Chrome", mock_chrome)
    monkeypatch.setattr(browser_mod, "find_chrome_executable", lambda: "C:\\Windows\\chrome.exe")

    b = Browser(BrowserSettings(binary_path="/percorso/inesistente/chrome"))
    driver = b.start()
    assert driver == mock_driver
    assert call_count == 2


def test_browser_fails_with_clear_error(monkeypatch):
    from selenium.common.exceptions import NoSuchDriverException
    import core.browser as browser_mod

    def mock_chrome(*args, **kwargs):
        raise NoSuchDriverException("Unable to obtain driver for chrome")

    monkeypatch.setattr(browser_mod.webdriver, "Chrome", mock_chrome)
    monkeypatch.setattr(browser_mod, "find_chromedriver_executable", lambda: None)
    monkeypatch.setattr(browser_mod, "find_edge_executable", lambda: None)

    b = Browser(BrowserSettings())
    with pytest.raises(RuntimeError) as exc_info:
        b.start()
    assert "Impossibile avviare il driver Chrome" in str(exc_info.value)


