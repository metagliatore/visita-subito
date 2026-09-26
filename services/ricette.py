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
        r'<div\b(?=[^>]*\bid=["\']([A-Z0-9]+)["\'])(?=[^>]*\bclass=["\'][^"\']*prescrizioni-row[^"\']*["\'])[^>]*>',
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
            r'<div[^>]*id="([A-Z0-9]+)"[^>]*class="prescrizioni-row (?:ricette-row|visite-row) row"',
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

        # stato
        stato = None
        m = re.search(r'stato-text"><b>([^<]+)</b>', content)
        if m:
            stato = m.group(1).strip()
        # data ricetta: da <time datetime="YYYY-MM-DD"> o testo DD/MM/YYYY
        data_ricetta = ""
        m = re.search(r'Data ricetta.*?datetime="(\d{4}-\d{2}-\d{2})"', content)
        if m:
            data_ricetta = m.group(1)  # YYYY-MM-DD
        else:
            m = re.search(r'Data ricetta:?\s*<b>([^<]+)</b>', content)
            if m:
                t = m.group(1).strip()
                mm = re.search(r'(\d{2}/\d{2}/\d{4})', t)
                if mm:
                    # converto DD/MM/YYYY -> YYYY-MM-DD
                    d, mo, y = mm.group(1).split("/")
                    data_ricetta = f"{y}-{mo}-{d}"
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
        d = (r.get("data_ricetta") or "")
        if len(d) == 10 and d[4] == "-":  # YYYY-MM-DD
            try:
                import datetime as _dt
                return _dt.datetime.strptime(d, "%Y-%m-%d").timestamp()
            except Exception:  # noqa: BLE001
                return -1e18
        return -1e18  # senza data -> fondo (più vecchio)
    out.sort(key=_datakey, reverse=True)
    return out
