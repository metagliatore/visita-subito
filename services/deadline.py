"""Working days and deadline calculations for medical appointments.

Enforces the Italian NHS (SSN / Regione Lombardia) rule:
Appointments must be cancelled or rescheduled at least 3 working days before
the scheduled date to avoid penalty ticket charges (malus ticket).

Working days exclude Saturdays, Sundays, and Italian national public holidays.
"""

from __future__ import annotations

import datetime as _dt
from datetime import date, datetime, time, timedelta
import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)


def easter_date(year: int) -> date:
    """Calculate Western Easter Sunday using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def italian_holidays(year: int) -> set[date]:
    """Return the set of official Italian national holidays for a given year."""
    easter = easter_date(year)
    pasquetta = easter + timedelta(days=1)
    return {
        date(year, 1, 1),    # Capodanno
        date(year, 1, 6),    # Epifania
        pasquetta,           # Lunedì dell'Angelo
        date(year, 4, 25),   # Liberazione
        date(year, 5, 1),    # Festa del Lavoro
        date(year, 6, 2),    # Festa della Repubblica
        date(year, 8, 15),   # Assunzione / Ferragosto
        date(year, 11, 1),   # Ognissanti
        date(year, 12, 8),   # Immacolata Concezione
        date(year, 12, 25),  # Natale
        date(year, 12, 26),  # Santo Stefano
    }


def is_working_day(d: date) -> bool:
    """Return True if the date is a working day (Mon-Fri and not a national holiday)."""
    # Saturday (5) or Sunday (6)
    if d.weekday() in (5, 6):
        return False
    if d in italian_holidays(d.year):
        return False
    return True


def parse_appuntamento_datetime(val: Any) -> datetime | None:
    """Parse appointment date and time from various formats into a datetime object."""
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, date):
        return datetime.combine(val, time(0, 0))

    raw = str(val).strip()
    if not raw:
        return None

    # Match DD/MM/YYYY or YYYY-MM-DD
    d_match = re.search(r"(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})", raw)
    if not d_match:
        return None

    ds = d_match.group(1)
    d_obj = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            d_obj = datetime.strptime(ds, fmt).date()
            break
        except Exception:
            pass

    if not d_obj:
        return None

    # Match time HH:MM or HH:MM:SS
    t_match = re.search(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b", raw)
    if t_match:
        h = int(t_match.group(1))
        m = int(t_match.group(2))
        s = int(t_match.group(3)) if t_match.group(3) else 0
        t_obj = time(h, m, s)
    else:
        t_obj = time(0, 0)

    return datetime.combine(d_obj, t_obj)


def calculate_reschedule_deadline(app_dt: datetime, min_working_days: int = 3) -> datetime:
    """Calculate the latest datetime before which an appointment can be rescheduled without penalty.

    Steps backwards day-by-day from the day before the appointment, counting
    working days (excluding weekends and Italian national holidays).
    If appointment specifies a time other than 00:00, the deadline preserves that time
    on the calculated day (ensuring exactly min_working_days of full working hours).
    If no time is specified (00:00:00), the deadline is set to 23:59:59 of that day.
    """
    if min_working_days <= 0:
        return datetime.min

    curr = app_dt.date() - timedelta(days=1)
    counted = 0
    while True:
        if is_working_day(curr):
            counted += 1
            if counted == min_working_days:
                break
        curr -= timedelta(days=1)

    t = app_dt.time()
    if t == time(0, 0):
        t = time(23, 59, 59)

    return datetime.combine(curr, t)


def get_now() -> datetime:
    """Return current datetime. Useful for mocking in tests."""
    return datetime.now()


def check_reschedule_deadline(
    data_attuale_raw: Any,
    min_working_days: int = 3,
    now: datetime | None = None,
) -> tuple[bool, datetime | None, datetime | None, str]:
    """Check if the deadline to reschedule without penalty has passed.

    Returns:
        (is_too_late, app_dt, deadline_dt, message)
    """
    if min_working_days <= 0:
        app_dt = parse_appuntamento_datetime(data_attuale_raw)
        return False, app_dt, None, "Controllo giorni lavorativi disabilitato."

    app_dt = parse_appuntamento_datetime(data_attuale_raw)
    if not app_dt:
        return False, None, None, "Data appuntamento non specificata o non valida."

    deadline_dt = calculate_reschedule_deadline(app_dt, min_working_days)
    curr_now = now if now is not None else get_now()

    if curr_now >= deadline_dt:
        msg = (
            f"Mancano meno di {min_working_days} giorni lavorativi prima dell'appuntamento del "
            f"{app_dt.strftime('%d/%m/%Y alle %H:%M')}. "
            f"Il termine utile per spostare senza penale (malus ticket) era il "
            f"{deadline_dt.strftime('%d/%m/%Y alle %H:%M')}."
        )
        return True, app_dt, deadline_dt, msg

    msg = (
        f"Termine utile per anticipare senza penale: "
        f"{deadline_dt.strftime('%d/%m/%Y alle %H:%M')}."
    )
    return False, app_dt, deadline_dt, msg


def get_reschedule_min_working_days(cfg: Any = None, criteri: dict | None = None) -> int:
    """Retrieve the configured minimum working days notice threshold."""
    if criteri and "reschedule_min_working_days" in criteri:
        try:
            return int(criteri["reschedule_min_working_days"])
        except (ValueError, TypeError):
            pass

    env_val = os.environ.get("RESCHEDULE_MIN_WORKING_DAYS")
    if env_val is not None:
        try:
            return int(env_val)
        except (ValueError, TypeError):
            pass

    if cfg is not None:
        try:
            val = cfg.settings.get("scheduler", {}).get("reschedule_min_working_days")
            if val is not None:
                return int(val)
        except Exception:
            pass
    else:
        try:
            from core.config import Config
            c = Config.load()
            val = c.settings.get("scheduler", {}).get("reschedule_min_working_days")
            if val is not None:
                return int(val)
        except Exception:
            pass

    return 3


def is_autobook_enabled(cfg: Any = None, criteri: dict | None = None) -> bool:
    """Check if fast-track auto-booking is enabled.

    Priority:
        1. Explicit monitor criteria ('auto_book' in criteri)
        2. Environment variable 'AUTO_BOOK'
        3. Config settings.yaml ('scheduler.auto_book')
        4. Default: False
    """
    if criteri and "auto_book" in criteri:
        v = criteri["auto_book"]
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "1", "yes", "si", "sì", "on")

    env_val = os.environ.get("AUTO_BOOK")
    if env_val is not None:
        return env_val.strip().lower() in ("true", "1", "yes", "si", "sì", "on")

    if cfg is not None:
        try:
            val = cfg.settings.get("scheduler", {}).get("auto_book")
            if val is not None:
                if isinstance(val, bool):
                    return val
                return str(val).strip().lower() in ("true", "1", "yes", "si", "sì", "on")
        except Exception:
            pass
    else:
        try:
            from core.config import Config
            c = Config.load()
            val = c.settings.get("scheduler", {}).get("auto_book")
            if val is not None:
                if isinstance(val, bool):
                    return val
                return str(val).strip().lower() in ("true", "1", "yes", "si", "sì", "on")
        except Exception:
            pass

    return False


def get_autobook_min_days(cfg: Any = None, criteri: dict | None = None) -> int:
    """Retrieve minimum days required to allow auto-booking (default: 5).

    Priority:
        1. Explicit monitor criteria ('auto_book_min_days' in criteri)
        2. Environment variable 'AUTO_BOOK_MIN_DAYS'
        3. Config settings.yaml ('scheduler.auto_book_min_days')
        4. Default: 5
    """
    if criteri and "auto_book_min_days" in criteri:
        try:
            return int(criteri["auto_book_min_days"])
        except (ValueError, TypeError):
            pass

    env_val = os.environ.get("AUTO_BOOK_MIN_DAYS")
    if env_val is not None:
        try:
            return int(env_val)
        except (ValueError, TypeError):
            pass

    if cfg is not None:
        try:
            val = cfg.settings.get("scheduler", {}).get("auto_book_min_days")
            if val is not None:
                return int(val)
        except Exception:
            pass
    else:
        try:
            from core.config import Config
            c = Config.load()
            val = c.settings.get("scheduler", {}).get("auto_book_min_days")
            if val is not None:
                return int(val)
        except Exception:
            pass

    return 5


def check_autobook_eligibility(
    slot_or_dt: Any,
    min_days: int = 5,
    now: datetime | None = None,
) -> tuple[bool, int, str]:
    """Check if an appointment slot satisfies the minimum days threshold for auto-booking.

    Args:
        slot_or_dt: Slot object, datetime object, or date string.
        min_days: Minimum days notice required between now and slot date.
        now: Reference datetime (defaults to get_now()).

    Returns:
        (is_eligible, delta_days, reason_message)
    """
    if slot_or_dt is None:
        return False, 0, "Slot o data non valida."

    if hasattr(slot_or_dt, "datetime") and isinstance(slot_or_dt.datetime, datetime):
        slot_dt = slot_or_dt.datetime
    elif isinstance(slot_or_dt, datetime):
        slot_dt = slot_or_dt
    elif isinstance(slot_or_dt, str):
        slot_dt = parse_appuntamento_datetime(slot_or_dt)
        if not slot_dt:
            return False, 0, f"Impossibile interpretare la data '{slot_or_dt}'."
    else:
        return False, 0, "Formato slot non supportato."

    curr_now = now if now is not None else get_now()
    delta_days = (slot_dt.date() - curr_now.date()).days

    if delta_days < 0:
        return False, delta_days, "Data dell'appuntamento antecedente alla data odierna."

    if min_days <= 0:
        return True, delta_days, "Controllo giorni minimi auto-booking disabilitato."

    if delta_days >= min_days:
        msg = f"Slot a {delta_days} giorni di distanza (soglia minima: {min_days} giorni)."
        return True, delta_days, msg

    reason = (
        f"L'appuntamento dista solo {delta_days} {'giorno' if delta_days == 1 else 'giorni'} "
        f"(soglia minima configurata: {min_days} giorni)."
    )
    return False, delta_days, reason
