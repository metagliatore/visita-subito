import time
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.browser import Browser, BrowserSettings

b = Browser(BrowserSettings(headless=True))
d = b.start()
try:
    d.get("https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/homepage")
    time.sleep(5)
    d.execute_script("document.querySelector('form[action*=\"AuthRequestCieService\"]').submit();")
    time.sleep(6)
    print("CIE Landing URL:", d.current_url)
    print("Page Title:", d.title)
    Path("data/study/providers/cie_real_landing.html").write_text(d.page_source, encoding="utf-8")
finally:
    b.stop()
