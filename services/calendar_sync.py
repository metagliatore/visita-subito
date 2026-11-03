"""Calendar synchronization and conflict detection for VisitaSubito.

Reads read-only iCal (.ics) feeds from Google Calendar, Outlook / Microsoft 365,
Apple iCloud, Nextcloud, etc., caches them in memory, and checks whether proposed
appointment slots conflict with existing user commitments (including configurable
travel/buffer time).
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

ROME_TZ = ZoneInfo("Europe/Rome")

# Day mapping for RFC 5545 BYDAY
_BYDAY_MAP = {
    "MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6,
}


@dataclass
class CalendarEvent:
    uid: str
    summary: str
    dt_start: datetime
    dt_end: datetime
    is_all_day: bool = False
    status: str = "CONFIRMED"
    transparent: bool = False  # True if TRANSP:TRANSPARENT (marked as 'show as free')
    source: str = ""

    @property
    def start_str(self) -> str:
        return self.dt_start.strftime("%d/%m/%Y" if self.is_all_day else "%H:%M")

    @property
    def end_str(self) -> str:
        return self.dt_end.strftime("%d/%m/%Y" if self.is_all_day else "%H:%M")

    @property
    def date_str(self) -> str:
        return self.dt_start.strftime("%d/%m/%Y")


def unfold_ics(text: str) -> str:
    """Unfold lines per RFC 5545 section 3.1."""
    return re.sub(r"\r?\n[ \t]", "", text)


def unescape_ics_text(val: str) -> str:
    """Unescape text property characters per RFC 5545."""
    s = val.replace(r"\n", "\n").replace(r"\N", "\n")
    s = s.replace(r"\,", ",").replace(r"\;", ";").replace(r"\\", "\\")
    return s.strip()


def parse_ics_duration(val: str) -> timedelta:
    """Parse ISO 8601 / RFC 5545 duration string (e.g. PT1H, PT45M, P1D)."""
    m = re.match(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$", val)
    if not m:
        return timedelta(minutes=30)
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def parse_ics_datetime(val: str, params: dict[str, str] | None = None) -> tuple[datetime, bool]:
    """Parse an iCalendar date/datetime string into local Europe/Rome naive datetime.

    Returns (datetime_obj, is_all_day).
    """
    params = params or {}
    val = val.strip()

    # All-day date (YYYYMMDD)
    if params.get("VALUE") == "DATE" or len(val) == 8:
        try:
            d = datetime.strptime(val[:8], "%Y%m%d")
            return d, True
        except ValueError:
            pass

    # UTC datetime (ends with Z, e.g. 20261118T100000Z)
    if val.endswith("Z"):
        clean_val = val[:-1]
        try:
            dt_utc = datetime.strptime(clean_val[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            dt_local = dt_utc.astimezone(ROME_TZ).replace(tzinfo=None)
            return dt_local, False
        except ValueError:
            pass

    # Datetime with TZID (e.g. TZID=Europe/Rome:20261118T100000)
    tzid = params.get("TZID", "")
    if tzid:
        try:
            tz = ZoneInfo(tzid)
            dt_tz = datetime.strptime(val[:15], "%Y%m%dT%H%M%S").replace(tzinfo=tz)
            dt_local = dt_tz.astimezone(ROME_TZ).replace(tzinfo=None)
            return dt_local, False
        except Exception:
            pass

    # Floating / local datetime (assume Europe/Rome local time)
    try:
        return datetime.strptime(val[:15], "%Y%m%dT%H%M%S"), False
    except ValueError:
        # Fallback to date only if time fails
        try:
            return datetime.strptime(val[:8], "%Y%m%d"), True
        except ValueError:
            return datetime.now(), False


def parse_ics_property(line: str) -> tuple[str, dict[str, str], str]:
    """Split an iCalendar property line into (name, params, value)."""
    if ":" not in line:
        return line.strip().upper(), {}, ""
    prop_part, val = line.split(":", 1)
    params = {}
    if ";" in prop_part:
        parts = prop_part.split(";")
        name = parts[0].strip().upper()
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                params[k.strip().upper()] = v.strip().strip('"')
    else:
        name = prop_part.strip().upper()
    return name, params, val.strip()


def expand_recurring_events(
    event: CalendarEvent,
    rrule_str: str,
    horizon_days: int = 180,
    ref_now: datetime | None = None,
) -> list[CalendarEvent]:
    """Expand a recurring event across horizon_days from ref_now."""
    now = ref_now or datetime.now()
    cutoff_end = now + timedelta(days=horizon_days)
    events: list[CalendarEvent] = []

    # Parse RRULE keys
    parts = rrule_str.split(";")
    rule_dict: dict[str, str] = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            rule_dict[k.upper()] = v

    freq = rule_dict.get("FREQ", "").upper()
    interval = int(rule_dict.get("INTERVAL", 1))
    until_str = rule_dict.get("UNTIL")
    until_dt = None
    if until_str:
        until_dt, _ = parse_ics_datetime(until_str)
        if until_dt < cutoff_end:
            cutoff_end = until_dt

    byday = rule_dict.get("BYDAY", "")
    target_weekdays = [_BYDAY_MAP[d.strip()[-2:]] for d in byday.split(",") if d.strip()[-2:] in _BYDAY_MAP]

    dur = event.dt_end - event.dt_start

    curr = event.dt_start
    # If the original event is far in the past, advance it closer to now
    if freq == "WEEKLY" and target_weekdays:
        # Step through days from max(event.dt_start, now - 7 days) to cutoff_end
        start_scan = max(event.dt_start.date(), (now - timedelta(days=7)).date())
        scan_days = (cutoff_end.date() - start_scan).days
        for day_offset in range(max(0, scan_days + 1)):
            scan_date = start_scan + timedelta(days=day_offset)
            if scan_date.weekday() in target_weekdays:
                instance_start = datetime.combine(scan_date, event.dt_start.time())
                instance_end = instance_start + dur
                if instance_end >= now and instance_start <= cutoff_end:
                    events.append(
                        CalendarEvent(
                            uid=f"{event.uid}_{instance_start.strftime('%Y%m%d%H%M')}",
                            summary=event.summary,
                            dt_start=instance_start,
                            dt_end=instance_end,
                            is_all_day=event.is_all_day,
                            status=event.status,
                            transparent=event.transparent,
                            source=event.source,
                        )
                    )
    elif freq == "DAILY":
        start_scan = max(event.dt_start.date(), (now - timedelta(days=1)).date())
        scan_days = (cutoff_end.date() - start_scan).days
        for day_offset in range(0, max(0, scan_days + 1), interval):
            scan_date = start_scan + timedelta(days=day_offset)
            instance_start = datetime.combine(scan_date, event.dt_start.time())
            instance_end = instance_start + dur
            if instance_end >= now and instance_start <= cutoff_end:
                events.append(
                    CalendarEvent(
                        uid=f"{event.uid}_{instance_start.strftime('%Y%m%d%H%M')}",
                        summary=event.summary,
                        dt_start=instance_start,
                        dt_end=instance_end,
                        is_all_day=event.is_all_day,
                        status=event.status,
                        transparent=event.transparent,
                        source=event.source,
                    )
                )
    return events


def parse_ics_content(
    content: str,
    source_name: str = "",
    ref_now: datetime | None = None,
) -> list[CalendarEvent]:
    """Parse iCalendar text into a list of CalendarEvent objects."""
    now = ref_now or datetime.now()
    unfolded = unfold_ics(content)
    lines = unfolded.splitlines()

    events: list[CalendarEvent] = []
    in_vevent = False
    current_props: dict[str, tuple[dict[str, str], str]] = {}
    rrule_str = ""

    for line in lines:
        line_s = line.strip()
        if not line_s:
            continue
        if line_s == "BEGIN:VEVENT":
            in_vevent = True
            current_props = {}
            rrule_str = ""
            continue
        if line_s == "END:VEVENT":
            in_vevent = False
            # Process current event
            uid_p = current_props.get("UID", ({}, ""))
            sum_p = current_props.get("SUMMARY", ({}, "Impegno"))
            start_p = current_props.get("DTSTART")
            end_p = current_props.get("DTEND")
            dur_p = current_props.get("DURATION")
            stat_p = current_props.get("STATUS", ({}, "CONFIRMED"))
            transp_p = current_props.get("TRANSP", ({}, "OPAQUE"))

            if not start_p:
                continue

            dt_start, is_all_day = parse_ics_datetime(start_p[1], start_p[0])
            if end_p:
                dt_end, _ = parse_ics_datetime(end_p[1], end_p[0])
            elif dur_p:
                dt_end = dt_start + parse_ics_duration(dur_p[1])
            else:
                dt_end = dt_start + timedelta(days=1 if is_all_day else 0, minutes=0 if is_all_day else 60)

            # Ensure dt_end >= dt_start
            if dt_end < dt_start:
                dt_end = dt_start + timedelta(minutes=30)

            summary = unescape_ics_text(sum_p[1]) or "Impegno"
            uid = uid_p[1] or f"evt_{int(dt_start.timestamp())}"
            status = stat_p[1].upper()
            transparent = transp_p[1].upper() == "TRANSPARENT"

            if status == "CANCELLED":
                continue

            base_event = CalendarEvent(
                uid=uid,
                summary=summary,
                dt_start=dt_start,
                dt_end=dt_end,
                is_all_day=is_all_day,
                status=status,
                transparent=transparent,
                source=source_name,
            )

            if rrule_str:
                expanded = expand_recurring_events(base_event, rrule_str, ref_now=now)
                events.extend(expanded)
            else:
                events.append(base_event)
            continue

        if in_vevent:
            name, params, val = parse_ics_property(line_s)
            if name == "RRULE":
                rrule_str = val
            else:
                current_props[name] = (params, val)

    # Filter out events that ended in the past (before now - 1 day)
    recent_cutoff = now - timedelta(days=1)
    future_events = [e for e in events if e.dt_end >= recent_cutoff]
    return future_events


# Global in-memory cache for downloaded calendar feeds
_CALENDAR_CACHE: dict[str, Any] = {
    "timestamp": 0.0,
    "urls": [],
    "events": [],
}


def clear_calendar_cache() -> None:
    """Clear in-memory cache of calendar events."""
    global _CALENDAR_CACHE
    _CALENDAR_CACHE = {"timestamp": 0.0, "urls": [], "events": []}


def fetch_feed_content(url: str, timeout: float = 10.0) -> str:
    """Fetch raw ICS content from URL or local file."""
    norm_url = url.strip()
    if norm_url.lower().startswith("webcal://"):
        norm_url = "https://" + norm_url[9:]

    # Local file support
    if norm_url.lower().startswith("file://"):
        p = Path(norm_url[7:])
        return p.read_text(encoding="utf-8")
    if Path(norm_url).is_file():
        return Path(norm_url).read_text(encoding="utf-8")

    # Remote HTTP/HTTPS fetch
    headers = {"User-Agent": "VisitaSubito/2.0 (CalendarSync; +https://github.com/metagliatore/visita-subito)"}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(norm_url, headers=headers)
        resp.raise_for_status()
        return resp.text


def get_calendar_urls(criteri: dict | None = None) -> list[str]:
    """Retrieve list of configured calendar ICS URLs from env or criteri."""
    criteri = criteri or {}
    raw = criteri.get("calendar_ics_url") or os.environ.get("CALENDAR_ICS_URL", "")
    if not raw:
        return []
    # Can be separated by commas, semicolons, or newlines
    tokens = re.split(r"[,;\n]", str(raw))
    urls = [t.strip() for t in tokens if t.strip()]
    return urls


def is_calendar_sync_enabled(criteri: dict | None = None) -> bool:
    """Check if calendar conflict detection is enabled."""
    criteri = criteri or {}
    if "check_calendar" in criteri and not criteri["check_calendar"]:
        return False
    env_enabled = os.environ.get("CALENDAR_SYNC_ENABLED", "")
    if env_enabled.lower() in ("0", "false", "no"):
        return False
    # If explicitly true or if CALENDAR_ICS_URL is provided, consider it enabled
    if env_enabled.lower() in ("1", "true", "yes"):
        return True
    return bool(get_calendar_urls(criteri))


def get_calendar_buffer_minutes(criteri: dict | None = None) -> int:
    """Get buffer time in minutes (travel/prep before and after visit)."""
    criteri = criteri or {}
    if "calendar_buffer_minutes" in criteri:
        try:
            return max(0, int(criteri["calendar_buffer_minutes"]))
        except (ValueError, TypeError):
            pass
    raw = os.environ.get("CALENDAR_BUFFER_MINUTES", "45")
    try:
        return max(0, int(raw))
    except (ValueError, TypeError):
        return 45


def get_calendar_conflict_mode(criteri: dict | None = None) -> str:
    """Get conflict behavior mode: 'warn' (default, alert user) or 'skip' (drop slot)."""
    criteri = criteri or {}
    mode = (criteri.get("calendar_conflict_mode") or os.environ.get("CALENDAR_CONFLICT_MODE", "warn")).lower().strip()
    return "skip" if mode == "skip" else "warn"


def is_calendar_ignore_all_day(criteri: dict | None = None) -> bool:
    """Check if all-day events (e.g. birthdays, holidays) should be ignored."""
    criteri = criteri or {}
    if "calendar_ignore_all_day" in criteri:
        return bool(criteri["calendar_ignore_all_day"])
    return os.environ.get("CALENDAR_IGNORE_ALL_DAY", "true").lower() in ("1", "true", "yes")


def get_calendar_cache_ttl() -> int:
    """Cache TTL in seconds (default: 600s = 10 min)."""
    try:
        return max(60, int(os.environ.get("CALENDAR_CACHE_TTL", "600")))
    except (ValueError, TypeError):
        return 600


def get_calendar_events(
    urls: list[str] | None = None,
    force_refresh: bool = False,
    now: datetime | None = None,
    criteri: dict | None = None,
) -> list[CalendarEvent]:
    """Retrieve and cache all calendar events across all configured feeds."""
    global _CALENDAR_CACHE
    urls = urls if urls is not None else get_calendar_urls(criteri)
    if not urls:
        return []

    curr_time = time.time()
    cache_ttl = get_calendar_cache_ttl()

    # Check cache validity
    if (
        not force_refresh
        and _CALENDAR_CACHE["urls"] == urls
        and (curr_time - _CALENDAR_CACHE["timestamp"]) < cache_ttl
    ):
        return _CALENDAR_CACHE["events"]

    all_events: list[CalendarEvent] = []
    fetch_success = False

    for u in urls:
        try:
            content = fetch_feed_content(u)
            evts = parse_ics_content(content, source_name=u, ref_now=now)
            all_events.extend(evts)
            fetch_success = True
        except Exception as e:
            log.warning("Errore sincronizzazione calendario da '%s': %s", u, e)

    if fetch_success or not _CALENDAR_CACHE["events"]:
        _CALENDAR_CACHE = {
            "timestamp": curr_time,
            "urls": urls,
            "events": all_events,
        }
        return all_events

    # If all fetches failed, return previous stale cache if available
    return _CALENDAR_CACHE["events"]


def check_calendar_conflict(
    slot_dt: datetime,
    durata_minuti: int = 30,
    buffer_minuti: int | None = None,
    criteri: dict | None = None,
    events: list[CalendarEvent] | None = None,
    now: datetime | None = None,
) -> tuple[bool, CalendarEvent | None, str]:
    """Check if an appointment slot conflicts with an event in connected calendars.

    Returns:
        (has_conflict, conflicting_event, explanation_string)
    """
    if not is_calendar_sync_enabled(criteri):
        return False, None, ""

    if buffer_minuti is None:
        buffer_minuti = get_calendar_buffer_minutes(criteri)

    if events is None:
        events = get_calendar_events(criteri=criteri, now=now)

    if not events:
        return False, None, ""

    ignore_all_day = is_calendar_ignore_all_day(criteri)

    # Calculate active window with travel buffer
    window_start = slot_dt - timedelta(minutes=buffer_minuti)
    window_end = slot_dt + timedelta(minutes=durata_minuti + buffer_minuti)

    for ev in events:
        if ev.is_all_day and ignore_all_day:
            continue
        if ev.transparent:
            # Event marked as 'Show as free' (Disponibile)
            continue
        if ev.status.upper() == "CANCELLED":
            continue

        # Overlap condition: (StartA < EndB) and (EndA > StartB)
        if ev.dt_start < window_end and ev.dt_end > window_start:
            buf_info = f" (incluso buffer ±{buffer_minuti}m)" if buffer_minuti > 0 else ""
            msg = (
                f"Conflitto con '{ev.summary}' "
                f"({ev.start_str} - {ev.end_str}){buf_info}"
            )
            return True, ev, msg

    return False, None, ""


def get_calendar_status_text(criteri: dict | None = None) -> str:
    """Format calendar sync status text for bot /status and logs."""
    if not is_calendar_sync_enabled(criteri):
        return "Sincronizzazione Calendario: DISATTIVATA (nessun URL configurato)"

    urls = get_calendar_urls(criteri)
    buf = get_calendar_buffer_minutes(criteri)
    mode = get_calendar_conflict_mode(criteri)
    mode_str = "scarta slot" if mode == "skip" else "avviso in notifica"
    feed_str = f"{len(urls)} feed" if len(urls) != 1 else "1 feed"

    return f"Sincronizzazione Calendario: ATTIVA ({feed_str}, buffer ±{buf}m, modalità: {mode_str})"

