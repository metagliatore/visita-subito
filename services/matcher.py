"""Matcher: compares a list of available slots with user criteria.

Possible criteria (from monitors.yaml -> criteri):
    giorni          -> preferred days of the week, ['MON','WED',...]
    escludi_giorni  -> days always excluded
    ora_min         -> "08:00"
    ora_max         -> "13:00"
    ricetta_match   -> optional additional filter on content (e.g. facility name)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DAYS = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
}
WEEKDAYS = list(DAYS.keys())

# Italian day name aliases (short and full) -> ISO code
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
    """Normalize day name (Italian short/full or ISO) to ISO code (MON..SUN)."""
    s = str(g).lower().replace("'", "")
    if s in _IT_DAYS:
        return _IT_DAYS[s]
    # also accept 'MON'/'TUE' etc.
    s2 = str(g).upper()
    if s2 in DAYS:
        return s2
    return s2  # fallback


def _norm_time(t: str | int | None) -> str | None:
    """Normalize a time to two-digit HH:MM format for reliable ordered comparisons."""
    if not t:
        return None
    s = str(t).strip()
    if not s:
        return None
    if ":" in s:
        parts = s.split(":")
        try:
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            return f"{h:02d}:{m:02d}"
        except ValueError:
            return s
    try:
        h = int(s)
        return f"{h:02d}:00"
    except ValueError:
        return s


@dataclass
class Slot:
    """An available appointment slot extracted from portal."""
    datetime: datetime          # date and time of candidate appointment
    extra: dict = None          # facility, department, link, etc.

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
        """Unique identifier for deduplication (date+time+extra)."""
        base = self.datetime.strftime("%Y%m%dT%H%M")
        extra = "|".join(f"{k}={v}" for k, v in sorted(self.extra.items()))
        return base if not extra else f"{base}|{extra}"


FASCE_ORARIE = {
    "mattina": ("07:00", "13:00"),
    "pomeriggio": ("13:00", "19:00"),
    "sera": ("17:00", "23:59"),
    "tardo_pomeriggio": ("17:00", "23:59"),
    "pranzo": ("12:00", "15:00"),
    "pausa_pranzo": ("12:00", "15:00"),
}

FASCE_LABELS = {
    "mattina": "Solo Mattina (07:00 - 13:00)",
    "pomeriggio": "Solo Pomeriggio (13:00 - 19:00)",
    "sera": "Tardo Pomeriggio / Sera (17:00 - 23:59)",
    "tardo_pomeriggio": "Tardo Pomeriggio / Sera (17:00 - 23:59)",
    "pranzo": "Pausa Pranzo (12:00 - 15:00)",
    "pausa_pranzo": "Pausa Pranzo (12:00 - 15:00)",
    "qualsiasi": "Qualsiasi orario",
}


def _slot_in_fascia(time_str: str, fascia_item: str) -> bool:
    """Check if time_str ('HH:MM') falls into the given time slot."""
    norm_t = _norm_time(time_str) or str(time_str).strip()
    f_clean = str(fascia_item).strip().lower()
    if not f_clean or f_clean in ("qualsiasi", "all", "tutte", "none", "*"):
        return True
    if f_clean in FASCE_ORARIE:
        t_start, t_end = FASCE_ORARIE[f_clean]
        return t_start <= norm_t <= t_end
    if "-" in f_clean:
        parts = f_clean.split("-", 1)
        start_t = _norm_time(parts[0])
        end_t = _norm_time(parts[1])
        if start_t and end_t:
            return start_t <= norm_t <= end_t
        if start_t:
            return norm_t >= start_t
        if end_t:
            return norm_t <= end_t
    return True


def descrivi_fascia(fascia: str | list | None) -> str:
    """Return user-friendly label for configured time slot(s)."""
    if not fascia:
        return "Qualsiasi orario"
    if isinstance(fascia, str) and "," in fascia:
        fascia = [x.strip() for x in fascia.split(",") if x.strip()]
    if isinstance(fascia, (list, tuple, set)):
        items = [descrivi_fascia(f) for f in fascia if f and str(f).lower() not in ("qualsiasi", "all", "")]
        return ", ".join(items) if items else "Qualsiasi orario"
    f_clean = str(fascia).strip().lower()
    if f_clean in FASCE_LABELS:
        return FASCE_LABELS[f_clean]
    if "-" in f_clean:
        parts = f_clean.split("-", 1)
        s = _norm_time(parts[0])
        e = _norm_time(parts[1])
        if s and e:
            return f"Personalizzata ({s} - {e})"
    return str(fascia)


def match_slot(slot: Slot, criteri: dict) -> tuple[bool, str]:
    """Return (True, '') if slot satisfies criteria, else (False, reason).

    Note: days/time range are filtered in Python downstream from portal extraction.
    Days can be short ('mon','tue') or uppercase ('MON','TUE'). Time slots can be
    'mattina' (07-13) or 'pomeriggio' (>13).
    """
    giorni = criteri.get("giorni") or []
    escludi = [_norm_day(d) for d in (criteri.get("escludi_giorni") or [])]
    ora_min = _norm_time(criteri.get("ora_min"))
    ora_max = _norm_time(criteri.get("ora_max"))

    wd = slot.weekday  # 'MON'..'SUN'
    preferiti = [_norm_day(g) for g in giorni]

    # ---- time range (data_dal / data_a, DD/MM/YYYY or YYYY-MM-DD) ----
    from datetime import datetime as _dt
    def _parsedata(s):
        if not s:
            return None
        if hasattr(s, "strftime"):
            try:
                return s.date() if hasattr(s, "date") else s
            except Exception:
                pass
        raw = str(s).strip()
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return _dt.strptime(raw[:10], fmt).date()
            except Exception:
                continue
        return None
    dal = _parsedata(criteri.get("data_dal"))
    al = _parsedata(criteri.get("data_a"))
    sd = slot.datetime.date()
    if dal and sd < dal:
        return False, f"prima del range ({slot.date_str})"
    if al and sd > al:
        return False, f"dopo il range ({slot.date_str})"

    # ---- verify improvement over current appointment date ----
    data_att_raw = criteri.get("data_attuale")
    if data_att_raw:
        def _parse_data_e_ora(val):
            import re
            if not val:
                return None, None
            if hasattr(val, "date") and hasattr(val, "time"):
                t = val.time()
                return val.date(), f"{t.hour:02d}:{t.minute:02d}"
            if hasattr(val, "date"):
                return val.date(), None
            raw = str(val).strip()
            d_match = re.search(r"(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})", raw)
            d_obj = None
            if d_match:
                ds = d_match.group(1)
                for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
                    try:
                        d_obj = _dt.strptime(ds, fmt).date()
                        break
                    except Exception:
                        pass
            t_match = re.search(r"\b(\d{1,2}:\d{2})\b", raw)
            t_str = _norm_time(t_match.group(1)) if t_match else None
            return d_obj, t_str

        cur_date, cur_time = _parse_data_e_ora(data_att_raw)
        if cur_date:
            if sd > cur_date:
                return False, f"dopo l'appuntamento attuale ({slot.date_str} > {cur_date.strftime('%d/%m/%Y')})"
            if sd == cur_date and cur_time:
                if slot.time_str >= cur_time:
                    return False, f"stesso giorno dell'appuntamento attuale ma orario non migliorativo ({slot.time_str} >= {cur_time})"

    if wd in escludi:
        return False, f"giorno escluso ({wd})"
    if preferiti and wd not in preferiti:
        return False, f"giorno non preferito ({wd})"
    # ---- time slots (fasce orarie) ----
    raw_fascia = criteri.get("fascia") or criteri.get("fasce_orarie")
    if raw_fascia:
        if isinstance(raw_fascia, str) and "," in raw_fascia:
            items = [x.strip() for x in raw_fascia.split(",") if x.strip()]
        elif isinstance(raw_fascia, (list, tuple, set)):
            items = list(raw_fascia)
        else:
            items = [raw_fascia]
        valid_items = [it for it in items if it and str(it).strip().lower() not in ("qualsiasi", "all", "tutte", "none", "*", "")]
        if valid_items:
            in_any = any(_slot_in_fascia(slot.time_str, it) for it in valid_items)
            if not in_any:
                desc = descrivi_fascia(valid_items if len(valid_items) > 1 else valid_items[0])
                return False, f"orario fuori fascia ({slot.time_str} non in {desc})"

    # ---- explicit min / max hours ----
    if ora_min and slot.time_str < ora_min:
        return False, f"troppo presto ({slot.time_str})"
    if ora_max and slot.time_str > ora_max:
        return False, f"troppo tardi ({slot.time_str})"
    return True, ""


def best_match(slots: list[Slot], criteri: dict) -> Slot | None:
    """Return the first valid slot ordered by preference.

    Sorts by: user-preferred days in listed order, then date/time.
    """
    valid = [s for s in slots if match_slot(s, criteri)[0]]
    if not valid:
        return None
    giorni_ord = {_norm_day(d): i for i, d in enumerate(criteri.get("giorni") or [])}
    valid.sort(key=lambda s: (giorni_ord.get(s.weekday, 99), s.datetime))
    return valid[0]


# Messages shown by the portal when there are NO availabilities.
# These are NOT errors: they represent 0 slots for that query.
NESSUN_MESSAGGI = (
    "Non sono state trovate disponibilità",
    "non ci sono disponibilità online idonee",
    "Al momento non ci sono disponibilita' online",
    "non ci sono disponibili",
    "Nessuna disponibilità",
)


def nessuna_disponibilita(page_text: str) -> bool:
    """True if page text indicates absence of available slots (0 slots).

    Used to distinguish a VALID '0 availability' outcome (for that province
    at that moment) from a genuine interaction error.
    """
    low = (page_text or "").lower()
    return any(m.lower() in low for m in NESSUN_MESSAGGI)
