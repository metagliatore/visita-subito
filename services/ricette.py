"""Prescription catalog: reads prescription list from `/web/areaprivata/ricette`,
classifies items and prepares them for Telegram interaction.

Classification:
    farmaceutica   -> medication prescription (ARCOXIA, CLEXANE, ...). Not bookable.
    laboratorio    -> laboratory / lab analysis tests (URINE, URINOCOLTURA...).
                      Uses a different workflow (prenotaLaboratorio): listed
                      but not automated.
    specialistica  -> specialist visit / exam: automatable (Flow A).

The "laboratorio" distinction is most reliable when prescription is open in the portal
via `ricettaCtrl.ricetta.isEsameLaboratorio()`, but in the list we also infer it
from service description keywords.
"""
from __future__ import annotations

import re

# Keywords typical of laboratory exams/analyses (inferred from service description).
_LAB_KEYWORDS = (
    "esame", "urino", "urinoc", "chimico", "microscopic", "emocromo",
    "emocito", "glicem", "colesterol", "triglicer", "esami", "analisi",
    "azotem", "creatinin", "transaminas", "bilirubin", "emoculture",
    "biopsia", "tampone rino", "test rapido",
)
# Keywords typical of pharmaceutical prescriptions (drugs)
_FARM_KEYWORDS = ("mg", "cpr", "cps", "sir", "flacon", "fiale", "gtt", "unit.")

# Fragment pattern: a "prestazione" is often separated by comma or semicolon.


def _norm(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "")).strip().lower()


def classify(prestazioni: list[str]) -> str:
    """Return 'farmaceutica' | 'laboratorio' | 'specialistica'."""
    blob = _norm(" ".join(prestazioni))
    if any(_norm(k) in blob for k in _LAB_KEYWORDS):
        return "laboratorio"
    if any(k in blob for k in _FARM_KEYWORDS):
        return "farmaceutica"
    return "specialistica"


def prenotabile_in_automatico(categoria: str) -> bool:
    """Only specialist visits are automated."""
    return categoria == "specialistica"


def parse_ricette(html: str) -> list[dict]:
    """Extract prescription cards from `/web/areaprivata/ricette`.

    Each card is a <div id="NRE" class="prescrizioni-row ricette-row row">.
    The primary classification marker is explicit text "Ricetta specialistica" /
    "Ricetta farmaceutica" inside the card; specialist items are then reclassified
    as 'laboratorio' if the service matches lab analysis keywords.
    """
    html = html.replace(r'\"', '"').replace(r'\n', '\n')
    out = []
    cards: list[tuple[str, str]] = []
    # Search card divs supporting any order of id/class and any CSS class variant
    tag_matches = list(re.finditer(
        r'<div\b(?=[^>]*\bid=["\']([A-Za-z0-9_\-]+)["\'])(?=[^>]*\bclass=["\'][^"\']*prescrizioni-row[^"\']*["\'])[^>]*>',
        html,
        re.I,
    ))
    if tag_matches:
        for idx, tm in enumerate(tag_matches):
            rid = tm.group(1)
            start_content = tm.end()
            end_content = tag_matches[idx + 1].start() if idx + 1 < len(tag_matches) else len(html)
            cards.append((rid, html[start_content:end_content]))
    else:
        # Backwards compatibility fallback on legacy split
        parts = re.split(
            r'<div[^>]*id="([A-Za-z0-9_\-]+)"[^>]*class="prescrizioni-row (?:ricette-row|visite-row) row"',
            html)
        for k in range(1, len(parts) - 1, 2):
            cards.append((parts[k], parts[k + 1]))

    for rid, content in cards:

        # Prescription code (NRE): located inside HIDDEN <span id="codiceRicetta">,
        # NOT in the first <b> after "Codice ricetta:" (which holds internal 16-digit idPrescrizione).
        cod = None
        m = re.search(r'<span[^>]*id="codiceRicetta"[^>]*>([^<]+)</span>', content)
        if m and m.group(1).strip():
            cod = m.group(1).strip()
        if not cod:
            m = re.search(r'Codice ricetta:.*?<b>([0-9A-Za-z\-]+)</b>', content, re.S)
            if m:
                cod = m.group(1)
        if not cod:
            for sc in re.findall(r'<b>([^<]+)</b>', content):
                if re.fullmatch(r'[0-9A-Z]{5,}', sc.strip()):
                    cod = sc.strip(); break

        # Textual type marker
        if 'Ricetta farmaceutica' in content:
            tipologia = 'farmaceutica'
        else:
            tipologia = 'specialistica'

        # Status: extracted from div stato-text or SVG icon
        stato = None
        m = re.search(r'stato-text[^>]*>\s*(?:<b>|<span[^>]*>)?([^<]+)', content)
        if m and m.group(1).strip() and not m.group(1).strip().startswith("%"):
            stato = m.group(1).strip()
        if not stato:
            m_icon = re.search(r'blue-sheet-icon-([a-zA-Z0-9_\-]+)', content)
            if m_icon:
                icon_tag = m_icon.group(1).lower()
                if "prescritta" in icon_tag:
                    stato = "Prescritta"
                elif "prenotata" in icon_tag:
                    stato = "Accettata /Prenotata"
                elif "erogata" in icon_tag:
                    stato = "Erogata"
        # Prescription date: from <time datetime="..."> or text
        data_ricetta = ""
        m = re.search(r'Data(?:\s+ricetta)?.*?datetime=["\'](\d{4}-\d{2}-\d{2})["\']', content, re.I)
        if m:
            data_ricetta = m.group(1)  # YYYY-MM-DD
        else:
            m = re.search(r'Data(?:\s+ricetta)?[:\s]*(?:<b>|<span[^>]*>|<p[^>]*>)?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}|[0-9]{4}-[0-9]{2}-[0-9]{2})', content, re.I)
            if m:
                raw_d = m.group(1).strip()
                if "/" in raw_d:
                    d, mo, y = raw_d.split("/")
                    data_ricetta = f"{y}-{mo}-{d}"
                else:
                    data_ricetta = raw_d
        # Healthcare system regime / prescriber
        regime = None; prescrittore = None
        m = re.search(r'Regime:</b>\s*<b>([^<]+)</b>', content)
        if m: regime = m.group(1).strip()
        m = re.search(r'Prescrittore:</b>\s*<b>([^<]+)</b>', content)
        if m: prescrittore = m.group(1).strip()

        # Services: <b> in "Prestazione:" block (or plain text)
        m = re.search(r'Prestazione:(.*?)(?:</p>|<p\b|</div>|Prescrittore)', content, re.S)
        if m:
            prestazioni = [re.sub(r'\s+', ' ', s).strip() for s
                           in re.findall(r'<b>([^<]+)</b>', m.group(1))]
            prestazioni = [p.lstrip(', ').strip() for p in prestazioni if p]
            if not prestazioni and m.group(1).strip():
                clean_txt = re.sub(r'<[^>]+>', ' ', m.group(1)).strip()
                if clean_txt:
                    prestazioni = [clean_txt]
        else:
            prestazioni = []

        # Booking link
        link = None
        m = re.search(r'href="([^"]*prenotaonline[^"]*)"[^>]*class="cambia-visibilita"', content)
        if m:
            link = m.group(1)
        # Download link (recipe PDF)
        link_download = None
        m = re.search(r'href="(javascript:addCurrentPage\([^)]*step=downloadRicetta[^)]*)\)"', content)
        if m:
            link_download = m.group(1) + ")"

        # Final category
        if tipologia == 'farmaceutica':
            categoria = 'farmaceutica'
        elif len(prestazioni) and classify(prestazioni) == 'laboratorio':
            categoria = 'laboratorio'
        else:
            categoria = 'specialistica'

        out.append({
            "id": rid,
            "codice": cod,
            "prestazioni": prestazioni,
            "stato": stato,
            "regime": regime,
            "prescrittore": prescrittore,
            "data_ricetta": data_ricetta or "",
            "categoria": categoria,
            "prenotabile": bool(link),
            "automatizzabile": bool(link) and categoria == "specialistica",
            "link": link,
            "link_download": link_download,
        })
    # Sort by prescription date descending (most recent first); missing dates at end
    def _datakey(r):
        d = (r.get("data_ricetta") or "").strip()
        if not d:
            return -1e18
        import datetime as _dt
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                return _dt.datetime.strptime(d[:10], fmt).timestamp()
            except Exception:
                continue
        return -1e18  # without valid date -> bottom (oldest)
    out.sort(key=_datakey, reverse=True)
    return out
