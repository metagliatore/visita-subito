"""Extraction and summary of appointment data from the confirmation modal.

The modal 'Vuoi confermare l'appuntamento?' (PrenotaOnline screen) displays:
  - Data e ora            e.g. '26/10/2026 - 11:15'
  - Prestazione           e.g. 'VISITA SPECIALISTICA'
  - Azienda               e.g. 'AZIENDA OSPEDALIERA ESEMPIO'
  - Presentarsi in        facility name + address
  - Ulteriori indicazioni e.g. 'Via Roma, 1 - 20100 Milano (MI)'
  - Note di preparazione  (collapsible notes)
Extracted to (a) send via Telegram and (b) generate .ics calendar event.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AppuntamentoInfo:
    data_ora: str = ""
    prestazione: str = ""
    azienda: str = ""
    presentarsi_in: str = ""
    indirizzo: str = ""
    note: list = field(default_factory=list)
    codice: str = ""  # From success screen, optional

    @property
    def datetime(self) -> datetime | None:
        m = re.search(r"(\d{2}/\d{2}/\d{4})\s*(?:-|\s|ore)*\s*(\d{2}:\d{2})", self.data_ora or "")
        if not m:
            return None
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d/%m/%Y %H:%M")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _tr_api_a(s: str, stops: list[str]) -> str:
    """Truncate a string at the first matching stop marker (strips noise)."""
    low = s.lower()
    pos = len(s)
    for st in stops:
        k = low.find(st.lower())
        if 0 <= k < pos:
            pos = k
    out = s[:pos]
    # If truncated mid-word, strip incomplete last token
    if pos < len(s):
        toks = out.split()
        if toks:
            out = " ".join(toks[:-1]) if len(toks) > 1 else out
    return out.strip(" -,")


def _snippet(text: str, label: str, stop: str | None = None) -> str:
    """Search for 'label' in text and return value up to 'stop'."""
    idx = text.find(label)
    if idx < 0:
        return ""
    rest = text[idx + len(label):]
    if stop:
        m = re.match(r"\s*(.*?)\s*" + re.escape(stop), rest, re.S)
        if m:
            return _clean(m.group(1))
    # Otherwise capture to end of line
    m = re.match(r"\s*([^\n]+)", rest)
    return _clean(m.group(1)) if m else ""


def _modale_testo(html: str) -> str:
    """Isolate text of confirmation modal only (verificaPrenotazioneCtrl)."""
    html = html.replace(r'\"', '"').replace(r'\n', '\n')
    # Find modal-render containing 'verificaPrenotazioneCtrl'
    i = html.find("verificaPrenotazioneCtrl.nrDisponibilita")
    if i < 0:
        return re.sub(r"<[^>]+>", " ", html)
    start = html.rfind("<div modal-render", 0, i)
    if start < 0:
        start = max(0, i - 2000)
    # End: up to end of modal block (subsequent modal-backdrop or navbar)
    end = i + 20000
    nxt = html.find("screen-reader-text", i)  # Start of body/navbar
    for marker in ["modal-backdrop", "navbar-toggle"]:
        k = html.find(marker, i + 5000)
        if 0 < k < end:
            end = k
    seg = html[start:end]
    return re.sub(r"<[^>]+>", " ", seg)


def parse_conferma(html: str) -> AppuntamentoInfo:
    """Extract fields from confirmation modal (page HTML)."""
    text = re.sub(r"\s+", " ", _modale_testo(html))

    info = AppuntamentoInfo()
    info.data_ora = _snippet(text, "Data e ora", "Prestazione").lstrip(": ")
    info.prestazione = _snippet(text, "Prestazione", "Azienda").lstrip(": ")
    info.azienda = _snippet(text, "Azienda", "Comune").lstrip(": ")
    # Truncate at subsequent markers (avoid noise from lower list)
    info.prestazione = _tr_api_a(info.prestazione, ["Azienda", "Presentarsi", "Comune"]).lstrip(": ")
    info.azienda = _tr_api_a(info.azienda, ["Presentarsi", "Comune", "Ulteriori"]).lstrip(": ")

    # 'Presentarsi in' up to 'Ulteriori indicazioni'
    p = _snippet(text, "Presentarsi in", "Ulteriori indicazioni")
    p = _tr_api_a(p, ["Ulteriori", "Note di preparazione", "Comune"])
    if p:
        righe = [x.strip(" -") for x in p.split("  ") if x.strip(" -")]
        if righe:
            info.presentarsi_in = righe[0].lstrip(": ")
            if len(righe) > 1:
                info.indirizzo = " ".join(righe[1:]).strip(" -").lstrip(": ")

    # 'Ulteriori indicazioni' -> full address (fallback)
    info.indirizzo = info.indirizzo or _snippet(text, "Ulteriori indicazioni", "Note di preparazione")
    info.indirizzo = info.indirizzo or _snippet(text, "Ulteriori indicazioni")
    info.indirizzo = _tr_api_a(info.indirizzo, ["Note di preparazione", "Note operatore"]).lstrip(": ")

    # Preparation notes: everything after 'Note di preparazione' up to next marker
    ni = text.find("Note di preparazione")
    if ni >= 0:
        stop_i = len(text)
        for stop in ["Note operatore", "Confermo lettura", "Altre note", "Presa visione"]:
            s = text.find(stop, ni + 20)
            if 0 < s < stop_i:
                stop_i = s
        note_txt = _clean(text[ni + len("Note di preparazione"):stop_i])
        if note_txt:
            info.note = [x.strip("- ") for x in note_txt.split("-") if x.strip("- ")]

    # Operator / other notes
    nio = text.find("Note operatore")
    if nio >= 0:
        stop_i = len(text)
        for stop in ["Presa visione", "Confermo lettura"]:
            s = text.find(stop, nio + 20)
            if 0 < s < stop_i:
                stop_i = s
        extra = _clean(text[nio + len("Note operatore"):stop_i])
        if extra:
            info.note.append(extra)

    return info


def parse_successo(html: str) -> str:
    """Extract booking code from success screen (optional)."""
    html = html.replace(r'\"', '"').replace(r'\n', '\n')
    clean_text = re.sub(r"<[^>]+>", " ", html)
    m = re.search(
        r"(?:Codice|Numero)\s*(?:di\s*)?prenotazione\s*[:\-]?\s*([A-Za-z0-9\-]+)",
        clean_text,
        re.IGNORECASE,
    )
    return m.group(1).strip() if m else ""


def appuntamento_to_info(a: dict) -> AppuntamentoInfo:
    """Build an AppuntamentoInfo instance from an appointment dictionary (from UI card or store)."""
    addr_parts = []
    ind = (a.get("indirizzo") or "").strip()
    if ind:
        addr_parts.append(ind)
    cap_com = []
    if a.get("cap"):
        cap_com.append(str(a["cap"]).strip())
    if a.get("comune"):
        cap_com.append(str(a["comune"]).strip())
    if cap_com:
        cap_com_str = " ".join(cap_com)
        if addr_parts:
            addr_parts.append(f"({cap_com_str})")
        else:
            addr_parts.append(cap_com_str)
    full_addr = " · ".join(addr_parts)

    notes = []
    if a.get("note"):
        if isinstance(a["note"], list):
            notes.extend(str(n) for n in a["note"])
        else:
            notes.append(str(a["note"]))

    codice = (a.get("codice") or "").strip()
    data_ora = (a.get("data_ora") or "").strip()
    if data_ora and " - " not in data_ora:
        m = re.search(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})", data_ora)
        if m:
            data_ora = f"{m.group(1)} - {m.group(2)}"

    return AppuntamentoInfo(
        data_ora=data_ora,
        prestazione=(a.get("prestazione") or "").strip(),
        azienda=(a.get("azienda") or "").strip(),
        presentarsi_in=(a.get("presentarsi_in") or "").strip(),
        indirizzo=full_addr,
        note=notes,
        codice=codice,
    )
