"""Session server di STUDIO.

Apre Chrome VISIBILE (per il login manuale SPID/OTP) e resta in vita come
servizio HTTP locale su 127.0.0.1:8765, così io (agente) posso guidarlo e
studiare il DOM del portale mentre tu fai login e mi indichi cosa recuperare.

Per LANCIARe:
    python study/session_server.py          # avvia su :8765, aspetta login

Comandi HTTP (dalla mia parte):
    GET /status                  -> url, title, logged?
    GET /dump                    -> HTML completo della pagina corrente
    GET /shot                    -> screenshot -> data/study/shot.png
    GET /save                    -> salva i cookie di sessione (dopo login)
    GET /goto?url=...            -> naviga
    GET /click?by=CSS&sel=...    -> click su primo elemento match
    GET /type?by=CSS&sel=...&text=..  -> scrivi in un input
    GET /select?by=CSS&sel=...&value=.. -> seleziona opzione per testo visibile
    GET /els?by=CSS&sel=...      -> elenca tutti i match (tag, id, class, text)
    GET /quit                    -> chiude browser e server
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

BASE = Path(__file__).resolve().parent.parent
CHROME = BASE / ".chrome/opt/google/chrome/google-chrome"
SHOT_DIR = BASE / "data/study"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

BY = {
    "id": By.ID, "name": By.NAME, "css": By.CSS_SELECTOR,
    "xpath": By.XPATH, "class": By.CLASS_NAME, "tag": By.TAG_NAME,
    "text": By.LINK_TEXT, "partial": By.PARTIAL_LINK_TEXT,
}


def make_driver():
    o = Options()
    o.binary_location = str(CHROME)
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--window-size=1400,950")
    # VISIBILE (niente headless) per il login manuale/OTP
    drv = webdriver.Chrome(options=o)
    drv.set_page_load_timeout(90)
    return drv


def login_sielte(d, user, pwd, wait_otp_sec=180):
    """Esegue il login SielteID: seleziona provider, credenziali, notifica push."""
    import time
    d.get("https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/homepage")
    time.sleep(4)
    if "/areaprivata/" in d.current_url or "/prenotaonline/" in d.current_url:
        return {"ok": True, "msg": "gia autenticato", "url": d.current_url}
    try:
        if "idpcwrapper" in d.current_url:
            d.execute_script("var a=document.querySelector('[spid-idp-button], a.button-spid, .pulsante-spid');if(a)a.click();")
            time.sleep(1)
            boxes = d.find_elements(By.CSS_SELECTOR, "a.home-box-fornitore")
            target = None
            for b in boxes:
                if "Sielte" in (b.text or ""):
                    target = b; break
            if target is None and len(boxes) > 11:
                target = boxes[10]
            if target:
                d.execute_script("arguments[0].click();", target)
    except Exception as e:  # noqa: BLE001
        pass
    time.sleep(4)
    if "identity.sieltecloud.it" in d.current_url:
        try:
            d.find_element(By.ID, "username").send_keys(user)
            d.find_element(By.ID, "password").send_keys(pwd)
            d.find_elements(By.ID, "autorizza")[0].click()
        except Exception as e:  # noqa: BLE001
            pass
    time.sleep(4)
    try:
        if "identity.sieltecloud.it" in d.current_url:
            d.execute_script("useNotify();")
    except Exception:  # noqa: BLE001
        pass
    # attesa approvazione push + consenso
    for _ in range(wait_otp_sec // 5):
        time.sleep(5)
        try:
            if "identity.sieltecloud.it" in d.current_url and "accept" in d.page_source:
                d.execute_script("var f=document.querySelector('form#piLoginForm');if(f){var b=f.querySelector('button[type=submit]');if(b)b.click();}")
                time.sleep(3)
        except Exception:  # noqa: BLE001
            pass
        if "/areaprivata/" in d.current_url or "/prenotaonline/" in d.current_url:
            return {"ok": True, "msg": "autenticato", "url": d.current_url}
    return {"ok": False, "msg": "timeout attesa approvazione OTP"}


driver = None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silenzia i log http
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global driver
        try:
            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            g = lambda k, d="": (q.get(k) or [d])[0]
            cmd = parsed.path.lstrip("/")
            if driver is None:
                return self._send({"error": "driver non avviato"})

            if cmd == "status":
                return self._send({
                    "url": driver.current_url,
                    "title": driver.title,
                    "handles": len(driver.window_handles),
                })
            if cmd == "login":
                import os
                sys.path.insert(0, str(BASE))
                try:
                    from core.config import _load_dotenv
                    _load_dotenv()
                except Exception as exc:  # noqa: BLE001
                    return self._send({"error": "dotenv: %s" % exc})
                user = os.environ.get("SIELTE_USERNAME", "")
                pwd = os.environ.get("SIELTE_PASSWORD", "")
                res = login_sielte(driver, user, pwd)
                if res.get("ok"):
                    import pickle
                    (BASE / "data/cookies" / "persistent.pkl").parent.mkdir(parents=True, exist_ok=True)
                    with open(BASE / "data/cookies" / "persistent.pkl", "wb") as f:
                        pickle.dump(driver.get_cookies(), f)
                return self._send(res)
            if cmd == "tabs":
                out = []
                for h in driver.window_handles:
                    try:
                        driver.switch_to.window(h)
                        out.append({"url": driver.current_url, "title": driver.title})
                    except Exception as e:  # noqa: BLE001
                        out.append({"error": str(e)})
                return self._send({"tabs": out})
            if cmd == "switch":
                driver.switch_to.window(driver.window_handles[int(g("n", "0"))])
                return self._send({"url": driver.current_url, "title": driver.title})
            if cmd == "eval":
                return self._send({"result": driver.execute_script(g("js"))})
            if cmd == "close_modal":
                # chiude qualsiasi elemento [data-dismiss=modal]
                n = driver.execute_script("""
                    const els=[...document.querySelectorAll('[data-dismiss="modal"]')];
                    let c=0; for(const e of els){e.click(); c++;}
                    return c;
                """)
                return self._send({"closed": n})
            if cmd == "dump":
                return self._send({"html": driver.page_source})
            if cmd == "shot":
                p = SHOT_DIR / "shot.png"
                driver.save_screenshot(str(p))
                return self._send({"shot": str(p)})
            if cmd == "save":
                import pickle
                cookies = driver.get_cookies()
                (BASE / "data/cookies" / "study.pkl").parent.mkdir(parents=True, exist_ok=True)
                with open(BASE / "data/cookies" / "study.pkl", "wb") as f:
                    pickle.dump(cookies, f)
                return self._send({"saved": len(cookies)})
            if cmd == "load":
                import pickle
                p = BASE / "data/cookies" / "study.pkl"
                if not p.exists():
                    return self._send({"error": "no cookies", "loaded": 0})
                with open(p, "rb") as f:
                    cookies = pickle.load(f)
                # cookie richiede prima di stare sul dominio
                if not driver.current_url.startswith("https://www.fascicolosanitario.regione.lombardia.it"):
                    driver.get("https://www.fascicolosanitario.regione.lombardia.it/")
                n = 0
                for c in cookies:
                    try:
                        driver.add_cookie(c); n += 1
                    except Exception:  # noqa: BLE001
                        pass
                return self._send({"loaded": n})
            if cmd == "goto":
                driver.get(g("url"))
                return self._send({"url": driver.current_url, "title": driver.title})
            if cmd == "click":
                self.click(g("by", "css"), g("sel"))
                return self._send({"clicked": g("sel")})
            if cmd == "type":
                el = self.find(g("by", "css"), g("sel"))
                el.clear(); el.send_keys(g("text"))
                return self._send({"typed": g("text")})
            if cmd == "select":
                el = self.find(g("by", "css"), g("sel"))
                Select(el).select_by_visible_text(g("value"))
                return self._send({"selected": g("value")})
            if cmd == "els":
                return self._send({"els": self.list_els(g("by", "css"), g("sel"))})
            if cmd == "quit":
                t = threading.Thread(target=driver.quit)
                t.start()
                driver = None
                return self._send({"quit": True})
            return self._send({"error": "comando ignoto: " + cmd}, 404)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._send({"error": str(e)}, 500)

    def find(self, by, sel, timeout=10):
        try:
            return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((BY[by], sel)))
        except Exception:
            return driver.find_element(BY[by], sel)

    def click(self, by, sel):
        self.find(by, sel).click()

    def list_els(self, by, sel):
        try:
            els = driver.find_elements(BY[by], sel)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for e in els[:80]:
            try:
                tag = e.tag_name
            except Exception:  # noqa: BLE001
                tag = "?"
            out.append({
                "tag": tag,
                "id": e.get_attribute("id") or "",
                "class": (e.get_attribute("class") or "")[:60],
                "text": (e.text or "")[:80],
                "name": e.get_attribute("name") or "",
                "aria": e.get_attribute("aria-label") or "",
                "href": e.get_attribute("href") or "",
            })
        return out


def main():
    global driver
    port = 8765
    if len(sys.argv) > 1 and sys.argv[1] == "--port":
        port = int(sys.argv[2])
    driver = make_driver()
    driver.get("https://www.fascicolosanitario.regione.lombardia.it/")
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"SERVER su http://127.0.0.1:{port}")
    print("Per il login automatico: chiama GET /login  (usa SIELTE_USERNAME/PASSWORD dal .env)")
    print("Nella finestra Chrome aperta: se non autenticato, esegui /login e approva la push.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
