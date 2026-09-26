"""Script di studio ed estrazione automatica dei form di login IdP SPID e CIE.
Naviga verso l'IdPC di Regione Lombardia, estrae la struttura dei bottoni SPID e CIE,
e visita ciascun provider per ispezionare i form di autenticazione.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from selenium.webdriver.common.by import By
from core.browser import Browser, BrowserSettings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("inspect_providers")

OUTPUT_DIR = Path("data/study/providers")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def inspect_idpc_page(driver):
    log.info("Navigazione alla home page per atterrare sull'IdPC...")
    driver.get("https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/homepage")
    time.sleep(5)
    log.info("URL corrente: %s", driver.current_url)

    # Dump pagina IdPC
    (OUTPUT_DIR / "idpc_landing.html").write_text(driver.page_source, encoding="utf-8")

    # Ispezione bottoni SPID e CIE
    spid_btn = driver.find_elements(By.CSS_SELECTOR, "[spid-idp-button], a.button-spid, .pulsante-spid, #spid-btn, a[href*='spid'], a[href*='SPID']")
    cie_btn = driver.find_elements(By.CSS_SELECTOR, "[cie-button], a.button-cie, .pulsante-cie, #cie-btn, a[href*='cie'], a[href*='CIE'], a[href*='Cie']")

    log.info("Trovati %d bottoni SPID e %d bottoni CIE", len(spid_btn), len(cie_btn))

    cie_info = []
    for idx, btn in enumerate(cie_btn):
        cie_info.append({
            "idx": idx,
            "tag": btn.tag_name,
            "text": btn.text,
            "id": btn.get_attribute("id"),
            "class": btn.get_attribute("class"),
            "href": btn.get_attribute("href"),
            "onclick": btn.get_attribute("onclick"),
        })

    # Clicca sul bottone SPID per aprire il menu a tendina/lista fornitori
    driver.execute_script("var a=document.querySelector('[spid-idp-button], a.button-spid, .pulsante-spid');if(a)a.click();")
    time.sleep(2)

    boxes = driver.find_elements(By.CSS_SELECTOR, "a.home-box-fornitore")
    log.info("Trovati %d box fornitori SPID", len(boxes))
    providers = []
    for idx, b in enumerate(boxes):
        name = b.text.strip()
        href = b.get_attribute("href")
        onclick = b.get_attribute("onclick")
        data_idp = b.get_attribute("data-idp") or b.get_attribute("id")
        providers.append({
            "index": idx,
            "name": name,
            "href": href,
            "onclick": onclick,
            "data_idp": data_idp,
        })
        log.info("SPID Provider [%d]: %s (href: %s, onclick: %s)", idx, name, href, onclick)

    idpc_data = {
        "url": driver.current_url,
        "cie_buttons": cie_info,
        "spid_providers": providers,
    }
    (OUTPUT_DIR / "idpc_structure.json").write_text(json.dumps(idpc_data, indent=2, ensure_ascii=False), encoding="utf-8")
    return idpc_data


def inspect_cie_login(browser):
    log.info("--- Ispezione Login CIE ---")
    driver = browser.start()
    driver.get("https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/homepage")
    time.sleep(5)

    # Click sul pulsante CIE
    try:
        driver.execute_script(
            "var c=document.querySelector('[cie-button], a.button-cie, .pulsante-cie, a[href*=\"cie\"], a[href*=\"CIE\"], a[href*=\"Cie\"]');"
            "if(c){c.click();return true} return false;"
        )
        time.sleep(5)
    except Exception as e:
        log.warning("Errore click CIE: %s", e)

    log.info("URL post-click CIE: %s", driver.current_url)
    (OUTPUT_DIR / "cie_landing.html").write_text(driver.page_source, encoding="utf-8")

    # Analisi elementi form CIE
    inputs = driver.find_elements(By.TAG_NAME, "input")
    buttons = driver.find_elements(By.TAG_NAME, "button")
    forms = driver.find_elements(By.TAG_NAME, "form")

    input_data = []
    for inp in inputs:
        input_data.append({
            "id": inp.get_attribute("id"),
            "name": inp.get_attribute("name"),
            "type": inp.get_attribute("type"),
            "placeholder": inp.get_attribute("placeholder"),
            "class": inp.get_attribute("class"),
        })

    btn_data = []
    for btn in buttons:
        btn_data.append({
            "id": btn.get_attribute("id"),
            "name": btn.get_attribute("name"),
            "type": btn.get_attribute("type"),
            "text": btn.text.strip(),
            "class": btn.get_attribute("class"),
        })

    cie_analysis = {
        "final_url": driver.current_url,
        "forms_count": len(forms),
        "inputs": input_data,
        "buttons": btn_data,
    }
    (OUTPUT_DIR / "cie_analysis.json").write_text(json.dumps(cie_analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("CIE ispezionato con successo. URL: %s", driver.current_url)
    return cie_analysis


def inspect_spid_provider(browser, provider_name, provider_index):
    log.info("--- Ispezione SPID Provider: %s (idx %s) ---", provider_name, provider_index)
    driver = browser.start()
    driver.get("https://www.fascicolosanitario.regione.lombardia.it/web/areaprivata/homepage")
    time.sleep(5)

    try:
        driver.execute_script("var a=document.querySelector('[spid-idp-button], a.button-spid, .pulsante-spid');if(a)a.click();")
        time.sleep(1)
        boxes = driver.find_elements(By.CSS_SELECTOR, "a.home-box-fornitore")
        target = None
        for b in boxes:
            if provider_name.lower() in (b.text or "").lower():
                target = b
                break
        if target is None and provider_index < len(boxes):
            target = boxes[provider_index]

        if target:
            driver.execute_script("arguments[0].click();", target)
            time.sleep(6)
    except Exception as e:
        log.warning("Errore selezione %s: %s", provider_name, e)
        return None

    log.info("URL landing %s: %s", provider_name, driver.current_url)
    safe_name = provider_name.lower().replace(" ", "_")
    (OUTPUT_DIR / f"spid_{safe_name}.html").write_text(driver.page_source, encoding="utf-8")

    inputs = driver.find_elements(By.TAG_NAME, "input")
    buttons = driver.find_elements(By.TAG_NAME, "button")
    forms = driver.find_elements(By.TAG_NAME, "form")

    input_data = []
    for inp in inputs:
        input_data.append({
            "id": inp.get_attribute("id"),
            "name": inp.get_attribute("name"),
            "type": inp.get_attribute("type"),
            "placeholder": inp.get_attribute("placeholder"),
            "class": inp.get_attribute("class"),
        })

    btn_data = []
    for btn in buttons:
        btn_data.append({
            "id": btn.get_attribute("id"),
            "name": btn.get_attribute("name"),
            "type": btn.get_attribute("type"),
            "text": btn.text.strip(),
            "class": btn.get_attribute("class"),
        })

    analysis = {
        "provider": provider_name,
        "landing_url": driver.current_url,
        "forms_count": len(forms),
        "inputs": input_data,
        "buttons": btn_data,
    }
    (OUTPUT_DIR / f"spid_{safe_name}.json").write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    return analysis


def main():
    b = Browser(BrowserSettings(headless=True))
    driver = b.start()
    try:
        idpc = inspect_idpc_page(driver)
    finally:
        b.stop()

    # Ispeziona CIE
    b_cie = Browser(BrowserSettings(headless=True))
    try:
        inspect_cie_login(b_cie)
    except Exception as e:
        log.error("CIE inspection error: %s", e)
    finally:
        b_cie.stop()

    # Ispeziona i principali provider SPID: Poste, Aruba, Infocert, Lepida, Namirial
    targets = [
        ("Poste", 0),
        ("Aruba", 1),
        ("Infocert", 2),
        ("Lepida", 3),
        ("Namirial", 11),
    ]
    for name, idx in targets:
        b_p = Browser(BrowserSettings(headless=True))
        try:
            inspect_spid_provider(b_p, name, idx)
        except Exception as e:
            log.error("Provider %s error: %s", name, e)
        finally:
            b_p.stop()

    log.info("Studio completato! Tutti i file salvati in %s", OUTPUT_DIR)


if __name__ == "__main__":
    main()
