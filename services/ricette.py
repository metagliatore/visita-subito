"""Catalogo ricette: legge la lista delle ricette prenotabili dalla pagina
`/web/areaprivata/ricette`, le classifica e le marca per la gestione Telegram.

Classificazione:
    farmaceutica   -> ricetta di farmaci (ARCOXIA, CLEXANE, ...). Non prenotabile.
    laboratorio    -> esame di analisi/laboratorio (URINE, URINOCOLTURA...).
                      Ha un flusso DIFFERENTE (prenotaLaboratorio): lo mostriamo
                      ma NON lo automatizziamo.
    specialistica  -> visita/esame specialistico: automatizzabile (Flow A).

La distinzione "laboratorio" è la più affidabile quando la ricetta è aperta nel
portale via `ricettaCtrl.ricetta.isEsameLaboratorio()`, ma nella lista la
inferiamo anche da keyword nella descrizione della prestazione.
"""
from __future__ import annotations

import re

# Keyword tipiche di esami/analisi di laboratorio (inferite dalla prestazione).
_LAB_KEYWORDS = (
    "esame", "urino", "urinoc", "chimico", "microscopic", "emocromo",
    "emocito", "glicem", "colesterol", "triglicer", "esami", "analisi",
    "azotem", "creatinin", "transaminas", "bilirubin", "emoculture",
    "biopsia", "tampone rino", "test rapido",
)
# Keyword tipiche di ricette farmaceutiche (farmaci)
_FARM_KEYWORDS = ("mg", "cpr", "cps", "sir", "flacon", "fiale", "gtt", "unit.")

# Pattern frammento: una "prestazione" è spesso separata da virgola o ";".


def _norm(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "")).strip().lower()


def classify(prestazioni: list[str]) -> str:
    """Ritorna 'farmaceutica' | 'laboratorio' | 'specialistica'."""
    blob = _norm(" ".join(prestazioni))
    if any(_norm(k) in blob for k in _LAB_KEYWORDS):
        return "laboratorio"
    if any(k in blob for k in _FARM_KEYWORDS):
        return "farmaceutica"
    return "specialistica"


def prenotabile_in_automatico(categoria: str) -> bool:
    """Solo le specialistiche (visite) vengono automatizzate."""
    return categoria == "specialistica"


def parse_ricette(html: str) -> list[dict]:
    """Estrae le card ricette dalla pagina `/web/areaprivata/ricette`.

    Ogni card è un <div id="NRE" class="prescrizioni-row ricette-row row">
    Il marcatore primario di classificazione è il testo esplicito
    "Ricetta specialistica" / "Ricetta farmaceutica" dentro la card;
    le specialistiche vengono poi riclassificate come 'laboratorio' se la
    prestazione matcha keyword di analisi (per escluderle dall'automazione).
    """
    html = html.replace(r'\"', '"').replace(r'\n', '\n')
    out = []
    cards: list[tuple[str, str]] = []
    # Cerca i div card gestendo qualsiasi ordine di id e class e qualsiasi variante di classi CSS
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
        # Fallback retrocompatibilità su split legacy
        parts = re.split(
            r'<div[^>]*id="([A-Za-z0-9_\-]+)"[^>]*class="prescrizioni-row (?:ricette-row|visite-row) row"',
            html)
        for k in range(1, len(parts) - 1, 2):
            cards.append((parts[k], parts[k + 1]))

    for rid, content in cards:

        # codice ricetta (NRE): sta nello <span id="codiceRicetta"> NASCOSTO,
        # NON nel primo <b> dopo "Codice ricetta:" (in quello c'è l'idPrescrizione
        # interno a 16 cifre, es. 0300A...) che confonderebbe il flow.
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

        # marcatore testuale di tipologia
        if 'Ricetta farmaceutica' in content:
            tipologia = 'farmaceutica'
        else:
            tipologia = 'specialistica'

        # stato: estrazione da div stato-text (con b, span o testo diretto) o da icona SVG
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
        # data ricetta: da <time datetime="YYYY-MM-DD"> o testo DD/MM/YYYY / YYYY-MM-DD
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
        # regime / prescrittore
        regime = None; prescrittore = None
        m = re.search(r'Regime:</b>\s*<b>([^<]+)</b>', content)
        if m: regime = m.group(1).strip()
        m = re.search(r'Prescrittore:</b>\s*<b>([^<]+)</b>', content)
        if m: prescrittore = m.group(1).strip()

        # prestazioni: i <b> nel blocco "Prestazione:" (o testo semplice)
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

        # link prenota
        link = None
        m = re.search(r'href="([^"]*prenotaonline[^"]*)"[^>]*class="cambia-visibilita"', content)
        if m:
            link = m.group(1)
        # link scarica (download ricetta)
        link_download = None
        m = re.search(r'href="(javascript:addCurrentPage\([^)]*step=downloadRicetta[^)]*)\)"', content)
        if m:
            link_download = m.group(1) + ")"

        # categoria finale
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
    # ordina per data ricetta decrescente (più recenti in alto); senza data in fondo
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
        return -1e18  # senza data valida -> fondo (più vecchio)
    out.sort(key=_datakey, reverse=True)
    return out
