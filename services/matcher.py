"""Matcher: confronta una lista di disponibilità con i criteri dell'utente.

Criteri possibili (da monitors.yaml -> criteri):
    giorni          -> giorno della settimana preferito, ['MON','WED',...]
    escludi_giorni  -> giorni sempre esclusi
    ora_min         -> "08:00"
    ora_max         -> "13:00"
    ricetta_match   -> filtro aggiuntivo opzionale sul contenuto (es. testo sede)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DAYS = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
}
WEEKDAYS = list(DAYS.keys())

# alias nomi italiani (corti e completi) -> codice ISO
_IT_DAYS = {
    "lun": "MON", "lunedi": "MON", "lunedì": "MON", "monday": "MON", "mon": "MON",
    "mar": "TUE", "martedi": "TUE", "martedì": "TUE", "tuesday": "TUE", "tue": "TUE",
    "mer": "WED", "mercoledi": "WED", "mercoledì": "WED", "wednesday": "WED", "wed": "WED",
    "gio": "THU", "giovedi": "THU", "giovedì": "THU", "thursday": "THU", "thu": "THU",
    "ven": "FRI", "venerdi": "FRI", "venerdì": "FRI", "friday": "FRI", "fri": "FRI",
    "sab": "SAT", "sabato": "SAT", "saturday": "SAT", "sat": "SAT",
    "dom": "SUN", "domenica": "SUN", "sunday": "SUN", "sun": "SUN",
}


def _norm_day(g) -> str:
    """Normalizza un giorno (IT corto/completo o ISO) al codice ISO (MON..SUN)."""
    s = str(g).lower().replace("'", "")
    if s in _IT_DAYS:
        return _IT_DAYS[s]
    # accetta anche 'MON'/'TUE' ecc.
    s2 = str(g).upper()
    if s2 in DAYS:
        return s2
    return s2  # fallback


@dataclass
class Slot:
    """Una disponibilità estratta dal portale."""
    datetime: datetime          # data e ora della visita candidata
    extra: dict = None          # sede, reparto, link, ecc.

    def __post_init__(self):
        if self.extra is None:
            self.extra = {}

    @property
    def weekday(self) -> str:
        return WEEKDAYS[self.datetime.weekday()]

    @property
    def time_str(self) -> str:
        return self.datetime.strftime("%H:%M")

    @property
    def date_str(self) -> str:
        return self.datetime.strftime("%d/%m/%Y")

    @property
    def key(self) -> str:
        """Identificatore univoco per dedup (data+ora+extra)."""
        base = self.datetime.strftime("%Y%m%dT%H%M")
        extra = "|".join(f"{k}={v}" for k, v in sorted(self.extra.items()))
        return base if not extra else f"{base}|{extra}"


def match_slot(slot: Slot, criteri: dict) -> tuple[bool, str]:
    """Ritorna (True, '') se lo slot soddisfa i criteri, altrimenti (False, motivo).

    Nota: giorni/fascia qui filtrati a VALLE (in Python) e non sull'interfaccia
    web: si estraggono tutti gli slot e si scartano qui. I giorni possono essere
    in formato corto ('mon','tue') o esteso ('MON','TUE'). La fascia può essere
    'mattina' (07-13) o 'pomeriggio' (>13).
    """
    giorni = criteri.get("giorni") or []
    escludi = [_norm_day(d) for d in (criteri.get("escludi_giorni") or [])]
    ora_min = criteri.get("ora_min")
    ora_max = criteri.get("ora_max")
    fascia = (criteri.get("fascia") or "").lower()

    wd = slot.weekday  # 'MON'..'SUN'
    preferiti = [_norm_day(g) for g in giorni]

    # ---- range temporale (data_dal / data_a, GG/MM/AAAA) ----
    from datetime import datetime as _dt
    def _parsedata(s):
        try:
            return _dt.strptime(s, "%d/%m/%Y").date()
        except Exception:  # noqa: BLE001
            return None
    dal = _parsedata(str(criteri.get("data_dal") or ""))
    al = _parsedata(str(criteri.get("data_a") or ""))
    sd = slot.datetime.date()
    if dal and sd < dal:
        return False, f"prima del range ({slot.date_str})"
    if al and sd > al:
        return False, f"dopo il range ({slot.date_str})"

    if wd in escludi:
        return False, f"giorno escluso ({wd})"
    if preferiti and wd not in preferiti:
        return False, f"giorno non preferito ({wd})"
    if ora_min and slot.time_str < ora_min:
        return False, f"troppo presto ({slot.time_str})"
    if ora_max and slot.time_str > ora_max:
        return False, f"troppo tardi ({slot.time_str})"
    if fascia == "mattina" and not ("07:00" <= slot.time_str <= "13:00"):
        return False, f"non in mattina ({slot.time_str})"
    if fascia == "pomeriggio" and not ("13:00" < slot.time_str <= "23:59"):
        return False, f"non in pomeriggio ({slot.time_str})"
    return True, ""


def best_match(slots: list[Slot], criteri: dict) -> Slot | None:
    """Restituisce il primo slot valido, ordinando per preferenza.

    Ordina per: giorni nell'ordine in cui l'utente li elenca (se presenti),
    poi data/ora. I giorni vanno normalizzati a forma estesa.
    """
    valid = [s for s in slots if match_slot(s, criteri)[0]]
    if not valid:
        return None
    giorni_ord = {_norm_day(d): i for i, d in enumerate(criteri.get("giorni") or [])}
    valid.sort(key=lambda s: (giorni_ord.get(s.weekday, 99), s.datetime))
    return valid[0]


# Messaggi che il portale mostra quando NON ci sono disponibilità.
# NON sono errori: equivalgono a 0 slot per quella ricerca.
NESSUN_MESSAGGI = (
    "Non sono state trovate disponibilità",
    "non ci sono disponibilità online idonee",
    "Al momento non ci sono disponibilita' online",
    "non ci sono disponibili",
    "Nessuna disponibilità",
)


def nessuna_disponibilita(page_text: str) -> bool:
    """True se la pagina/testo indica assenza di disponibilità (0 slot).

    Usato per distinguere un esito VALIDO '0 disponibilità' (per la provincia
    nel dato momento) da un errore reale di interazione.
    """
    low = (page_text or "").lower()
    return any(m.lower() in low for m in NESSUN_MESSAGGI)
