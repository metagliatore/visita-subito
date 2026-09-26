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
