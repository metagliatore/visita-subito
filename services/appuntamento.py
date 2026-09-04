"""Estrazione e riepilogo dei dati dell'appuntamento dalla modale di conferma.

La modale 'Vuoi confermare l'appuntamento?' (schermata PrenotaOnline) espone:
  - Data e ora            es. '26/10/2026 - 11:15'
  - Prestazione           es. 'VISITA SPECIALISTICA'
  - Azienda               es. 'DOTT. GIOVANNI TENCONI - STUDIO RADIOLOGICO SRL'
  - Presentarsi in        nome struttura + indirizzo
  - Ulteriori indicazioni es. 'Via Dezza, 26 - 20144 Milano (MI)'
  - Note di preparazione  (collassate)
Tutto va estratto per (a) inviarlo su Telegram e (b) generare il calendario .ics.
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
    codice: str = ""  # dalla schermata di successo, opzionale

    @property
    def datetime(self) -> datetime | None:
        m = re.search(r"(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}:\d{2})", self.data_ora or "")
        if not m:
            return None
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d/%m/%Y %H:%M")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _tr_api_a(s: str, stops: list[str]) -> str:
    """Tronca una stringa al primo marker tra quelli dati (rimuove rumore)."""
    low = s.lower()
    pos = len(s)
    for st in stops:
        k = low.find(st.lower())
        if 0 <= k < pos:
            pos = k
    out = s[:pos]
    # se il troncamento avviene a metà parola (es. 'Present' di Presentarsi),
    # togli l'ultimo token parziale
    if pos < len(s):
        toks = out.split()
        if toks:
            out = " ".join(toks[:-1]) if len(toks) > 1 else out
    return out.strip(" -,")


def _snippet(text: str, label: str, stop: str | None = None) -> str:
    """Cerca 'label' nel testo e restituisce il valore fino a 'stop'."""
    idx = text.find(label)
    if idx < 0:
        return ""
    rest = text[idx + len(label):]
    if stop:
        m = re.match(r"\s*(.*?)\s*" + re.escape(stop), rest, re.S)
        if m:
            return _clean(m.group(1))
    # altrimenti prendi fino alla fine (o fino a riga)
    m = re.match(r"\s*([^\n]+)", rest)
    return _clean(m.group(1)) if m else ""


def _modale_testo(html: str) -> str:
    """Isola il testo della sola modale di conferma (verificaPrenotazioneCtrl)."""
    html = html.replace(r'\"', '"').replace(r'\n', '\n')
    # trova il modal-render che contiene 'verificaPrenotazioneCtrl'
    i = html.find("verificaPrenotazioneCtrl.nrDisponibilita")
    if i < 0:
        return re.sub(r"<[^>]+>", " ", html)
    start = html.rfind("<div modal-render", 0, i)
    if start < 0:
        start = max(0, i - 2000)
    # fine: fino a fine blocco modale (modal-backdrop successivo o inizio navbar)
    end = i + 20000
    nxt = html.find("screen-reader-text", i)  # inizio body/navbar
    for marker in ["modal-backdrop", "navbar-toggle"]:
        k = html.find(marker, i + 5000)
        if 0 < k < end:
            end = k
    seg = html[start:end]
    return re.sub(r"<[^>]+>", " ", seg)


def parse_conferma(html: str) -> AppuntamentoInfo:
    """Estrae i campi dalla modale di conferma (html della pagina)."""
    text = re.sub(r"\s+", " ", _modale_testo(html))

    info = AppuntamentoInfo()
    info.data_ora = _snippet(text, "Data e ora")
    info.prestazione = _snippet(text, "Prestazione", "Azienda")
    info.azienda = _snippet(text, "Azienda", "Comune")
    # tronca ai successivi marcatori (evita rumore flat dalla lista sotto)
    info.prestazione = _tr_api_a(info.prestazione, ["Azienda", "Presentarsi", "Comune"])
    info.azienda = _tr_api_a(info.azienda, ["Presentarsi", "Comune", "Ulteriori"])

    # 'Presentarsi in' fino a 'Ulteriori indicazioni'
    p = _snippet(text, "Presentarsi in", "Ulteriori indicazioni")
    p = _tr_api_a(p, ["Ulteriori", "Note di preparazione", "Comune"])
    if p:
        righe = [x.strip(" -") for x in p.split("  ") if x.strip(" -")]
        if righe:
            info.presentarsi_in = righe[0]
            if len(righe) > 1:
                info.indirizzo = " ".join(righe[1:]).strip(" -")

    # 'Ulteriori indicazioni' -> indirizzo completo (fallback)
    info.indirizzo = info.indirizzo or _snippet(text, "Ulteriori indicazioni", "Note di preparazione")
    info.indirizzo = info.indirizzo or _snippet(text, "Ulteriori indicazioni")
    info.indirizzo = _tr_api_a(info.indirizzo, ["Note di preparazione", "Note operatore"])

    # note di preparazione: tutto dopo 'Note di preparazione' fino al prossimo marker
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

    # 'Note operatore / Altre note' -> nota bassa (memo presentarsi)
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
    """Estrae il codice prenotazione dalla schermata di successo (opzionale)."""
    html = html.replace(r'\"', '"').replace(r'\n', '\n')
    m = re.search(r"Codice prenotazione\s*(\d+)", re.sub(r"<[^>]+>", " ", html))
    return m.group(1) if m else ""
