"""Unit tests for commute distance and travel time calculation."""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from services.commute import (
    CommuteInfo,
    calculate_commute,
    check_commute_filter,
    find_nearest_transit_station,
    geocode_facility,
    get_commute_status_text,
    get_home_coordinates,
    haversine_distance,
    is_commute_active,
    normalize_location_text,
    parse_coordinates,
    resolve_slot_commute,
)
from services.matcher import Slot, match_slot


# ------------------------------------------------------------------------------
# 1. Coordinate Parsing
# ------------------------------------------------------------------------------
def test_parse_coordinates_valid_strings():
    assert parse_coordinates("45.4642, 9.1900") == (45.4642, 9.1900)
    assert parse_coordinates("45.4642; 9.1900") == (45.4642, 9.1900)
    assert parse_coordinates("45.4642 9.1900") == (45.4642, 9.1900)
    assert parse_coordinates("  45.123 ,  9.456  ") == (45.123, 9.456)


def test_parse_coordinates_lists_and_tuples():
    assert parse_coordinates([45.4642, 9.1900]) == (45.4642, 9.1900)
    assert parse_coordinates((45.4642, 9.1900)) == (45.4642, 9.1900)
    assert parse_coordinates(["45.4642", "9.1900"]) == (45.4642, 9.1900)


def test_parse_coordinates_invalid():
    assert parse_coordinates(None) is None
    assert parse_coordinates("") is None
    assert parse_coordinates("   ") is None
    assert parse_coordinates("invalid_text") is None
    assert parse_coordinates("45.4642") is None  # Single token
    # Out of range
    assert parse_coordinates("95.0, 9.19") is None
    assert parse_coordinates("-95.0, 9.19") is None
    assert parse_coordinates("45.0, 195.0") is None
    assert parse_coordinates("45.0, -195.0") is None
    # Zero coords
    assert parse_coordinates("0.0, 0.0") is None
    assert parse_coordinates([0.0, 0.0]) is None


# ------------------------------------------------------------------------------
# 2. Retrieving Home Coordinates
# ------------------------------------------------------------------------------
def test_get_home_coordinates_from_criteri(monkeypatch):
    monkeypatch.delenv("HOME_COORDINATES", raising=False)
    monkeypatch.delenv("HOME_LATITUDE", raising=False)
    monkeypatch.delenv("HOME_LONGITUDE", raising=False)

    criteri1 = {"home_coordinates": "45.4642, 9.1900"}
    assert get_home_coordinates(criteri1) == (45.4642, 9.1900)

    criteri2 = {"home_latitude": 45.5000, "home_longitude": 9.2000}
    assert get_home_coordinates(criteri2) == (45.5, 9.2)


def test_get_home_coordinates_from_env(monkeypatch):
    monkeypatch.setenv("HOME_COORDINATES", "45.4642, 9.1900")
    assert get_home_coordinates() == (45.4642, 9.1900)
    assert is_commute_active() is True

    # Separate latitude / longitude
    monkeypatch.delenv("HOME_COORDINATES", raising=False)
    monkeypatch.setenv("HOME_LATITUDE", "45.4642")
    monkeypatch.setenv("HOME_LONGITUDE", "9.1900")
    assert get_home_coordinates() == (45.4642, 9.1900)


def test_get_home_coordinates_missing(monkeypatch):
    monkeypatch.delenv("HOME_COORDINATES", raising=False)
    monkeypatch.delenv("HOME_LATITUDE", raising=False)
    monkeypatch.delenv("HOME_LONGITUDE", raising=False)
    with patch("core.config.Config.load", return_value=MagicMock(settings={})):
        assert get_home_coordinates() is None
        assert is_commute_active() is False


# ------------------------------------------------------------------------------
# 3. Haversine Distance
# ------------------------------------------------------------------------------
def test_haversine_distance():
    # Same point
    assert haversine_distance(45.0, 9.0, 45.0, 9.0) == 0.0

    # Duomo (45.4642, 9.1900) to Milano Centrale (45.4870, 9.2045) is ~2.75 km
    dist = haversine_distance(45.4642, 9.1900, 45.4870, 9.2045)
    assert 2500 < dist < 3000


# ------------------------------------------------------------------------------
# 4. Geocoding Facilities
# ------------------------------------------------------------------------------
def test_geocode_facility_known_hospitals():
    # Niguarda
    coords = geocode_facility("ASST GRANDE OSPEDALE METROPOLITANO NIGUARDA", "Milano")
    assert coords == (45.5108, 9.1889)

    # San Raffaele
    coords = geocode_facility("IRCCS Ospedale San Raffaele", "Milano")
    assert coords == (45.5056, 9.2661)

    # Papa Giovanni XXIII Bergamo
    coords = geocode_facility("Ospedale Papa Giovanni XXIII", "Bergamo")
    assert coords == (45.6861, 9.6458)

    # Spedali Civili Brescia
    coords = geocode_facility("ASST Spedali Civili", "Brescia")
    assert coords == (45.5564, 10.2294)

    # San Matteo Pavia
    coords = geocode_facility("Policlinico San Matteo", "Pavia")
    assert coords == (45.2017, 9.1489)


def test_geocode_facility_known_municipalities():
    # When facility is unknown generic string but municipality is known
    coords = geocode_facility("Ambulatorio Sconosciuto XYZ", "Monza")
    assert coords == (45.5845, 9.2744)


def test_geocode_facility_geocache_and_nominatim(tmp_path):
    cache_file = tmp_path / "geocache.json"

    # Pre-populate cache
    cache_file.write_text(json.dumps({"poliambulatorio rho | rho | mi": [45.5300, 9.0400]}))

    coords = geocode_facility(
        facility="Poliambulatorio Rho",
        municipality="Rho",
        province="MI",
        cache_path=cache_file,
    )
    assert coords == (45.5300, 9.0400)

    # Test Nominatim network mock when not in cache
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([{"lat": "45.7000", "lon": "9.5000"}]).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp):
        new_coords = geocode_facility(
            facility="Nuovo Presidio Unico",
            municipality="Curno",
            province="BG",
            cache_path=cache_file,
        )
        assert new_coords == (45.7, 9.5)

        # Check that it got saved to cache
        saved_cache = json.loads(cache_file.read_text())
        assert "nuovo presidio unico | curno | bg" in saved_cache


def test_geocode_facility_negative_caching(tmp_path):
    cache_file = tmp_path / "geocache.json"

    # Mock Nominatim returning empty list
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps([]).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = geocode_facility(
            facility="Struttura Inesistente Non Mappabile",
            municipality="LuogoInventato",
            cache_path=cache_file,
        )
        assert res is None

        # Verify negative cache entry [None, None]
        saved_cache = json.loads(cache_file.read_text())
        assert any(v == [None, None] for v in saved_cache.values())

        # Second lookup should return None directly from cache without calling urlopen
        with patch("urllib.request.urlopen") as mock_open:
            res2 = geocode_facility(
                facility="Struttura Inesistente Non Mappabile",
                municipality="LuogoInventato",
                cache_path=cache_file,
            )
            assert res2 is None
            mock_open.assert_not_called()


# ------------------------------------------------------------------------------
# 5. Commute Calculation (Car, Transit, Walking)
# ------------------------------------------------------------------------------
def test_calculate_commute_short_walking():
    # Points 400 meters apart in Milan center
    # Duomo (45.4642, 9.1900) to Cordusio (45.4656, 9.1864) ~380m
    commute = calculate_commute(45.4642, 9.1900, 45.4656, 9.1864, facility_name="Duomo/Cordusio")
    assert commute is not None
    assert commute.is_walking is True
    assert commute.walking_minutes is not None
    assert commute.walking_minutes <= 10
    assert "a piedi" in commute.summary_text
    assert "in auto" in commute.summary_text


def test_calculate_commute_urban_mid_distance():
    # Duomo (45.4642, 9.1900) to Niguarda (45.5108, 9.1889) ~5.2 km straight -> ~7 km road
    commute = calculate_commute(45.4642, 9.1900, 45.5108, 9.1889, facility_name="Niguarda")
    assert commute is not None
    assert commute.is_walking is False
    assert 6.0 <= commute.distance_km <= 8.5
    assert 12 <= commute.car_minutes <= 30
    assert "🚗" in commute.car_text
    assert "🚇" in commute.transit_text
    assert commute.transit_minutes >= commute.car_minutes
    assert "·" in commute.summary_text


def test_calculate_commute_long_distance():
    # Milano (45.4642, 9.1900) to Brescia (45.5416, 10.2118) ~80 km
    commute = calculate_commute(45.4642, 9.1900, 45.5416, 10.2118, facility_name="Spedali Civili")
    assert commute is not None
    assert commute.distance_km > 70.0
    assert commute.car_minutes > 50
    assert commute.transit_minutes > 50


def test_calculate_commute_invalid_coords():
    assert calculate_commute(999.0, 9.19, 45.46, 9.19) is None
    assert calculate_commute(0.0, 0.0, 45.46, 9.19) is None


# ------------------------------------------------------------------------------
# 6. Resolve Slot Commute
# ------------------------------------------------------------------------------
def test_resolve_slot_commute_without_home_coords(monkeypatch):
    monkeypatch.delenv("HOME_COORDINATES", raising=False)
    monkeypatch.delenv("HOME_LATITUDE", raising=False)
    monkeypatch.delenv("HOME_LONGITUDE", raising=False)

    slot = Slot(
        datetime=datetime(2026, 11, 10, 10, 30),
        extra={"sede": "OSPEDALE NIGUARDA CA' GRANDA", "comune": "MILANO"},
    )
    with patch("core.config.Config.load", return_value=MagicMock(settings={})):
        commute = resolve_slot_commute(slot, criteri={})
        assert commute is None


def test_resolve_slot_commute_with_home_coords(monkeypatch):
    monkeypatch.setenv("HOME_COORDINATES", "45.4642, 9.1900")  # Duomo Milano

    slot = Slot(
        datetime=datetime(2026, 11, 10, 10, 30),
        extra={"sede": "OSPEDALE NIGUARDA CA' GRANDA", "comune": "MILANO"},
    )
    commute = resolve_slot_commute(slot)
    assert commute is not None
    assert "Niguarda" in commute.facility_name or "OSPEDALE" in commute.facility_name
    assert "🚗" in commute.summary_text
    assert "🚇" in commute.summary_text


# ------------------------------------------------------------------------------
# 7. Future Filter Predisposition
# ------------------------------------------------------------------------------
def test_check_commute_filter():
    commute = CommuteInfo(
        distance_km=15.0,
        straight_km=11.0,
        car_minutes=25,
        car_text="🚗 25 min",
        transit_minutes=40,
        transit_route="M1",
        transit_text="🚇 40 min",
    )

    # 1. No criteria -> passes
    assert check_commute_filter(commute, {}) == (True, "")
    assert check_commute_filter(None, {}) == (True, "")

    # 2. max_distance_km
    assert check_commute_filter(commute, {"max_distance_km": 20})[0] is True
    ok, reason = check_commute_filter(commute, {"max_distance_km": 10})
    assert ok is False
    assert "distanza eccessiva" in reason

    # 3. max_car_minutes
    assert check_commute_filter(commute, {"max_car_minutes": 30})[0] is True
    ok, reason = check_commute_filter(commute, {"max_car_minutes": 20})
    assert ok is False
    assert "tempo auto eccessivo" in reason

    # 4. max_transit_minutes
    assert check_commute_filter(commute, {"max_transit_minutes": 45})[0] is True
    ok, reason = check_commute_filter(commute, {"max_transit_minutes": 35})
    assert ok is False
    assert "tempo mezzi eccessivo" in reason


def test_match_slot_with_commute_filter(monkeypatch):
    monkeypatch.setenv("HOME_COORDINATES", "45.4642, 9.1900")  # Duomo Milano

    # Slot at Niguarda (~7 km road distance, ~20 min car)
    slot = Slot(
        datetime=datetime(2026, 11, 15, 10, 0),
        extra={"sede": "OSPEDALE NIGUARDA CA' GRANDA", "comune": "MILANO"},
    )

    # 1. Standard criteria without commute filter -> passes
    assert match_slot(slot, {})[0] is True

    # 2. Distance filter allows up to 20 km -> passes
    assert match_slot(slot, {"max_distance_km": 20})[0] is True

    # 3. Distance filter allows max 2 km -> rejected
    ok, reason = match_slot(slot, {"max_distance_km": 2})
    assert ok is False
    assert "distanza eccessiva" in reason


# ------------------------------------------------------------------------------
# 8. Commute Status Text (/status display)
# ------------------------------------------------------------------------------
def test_get_commute_status_text(monkeypatch):
    monkeypatch.delenv("HOME_COORDINATES", raising=False)
    monkeypatch.delenv("HOME_LATITUDE", raising=False)
    monkeypatch.delenv("HOME_LONGITUDE", raising=False)

    with patch("core.config.Config.load", return_value=MagicMock(settings={})):
        txt_disabled = get_commute_status_text()
        assert "non configurato" in txt_disabled

    monkeypatch.setenv("HOME_COORDINATES", "45.4642, 9.1900")
    txt_enabled = get_commute_status_text()
    assert "ATTIVO" in txt_enabled
    assert "45.4642" in txt_enabled
    assert "9.1900" in txt_enabled


# ------------------------------------------------------------------------------
# 9. Integration with Flows Notifications
# ------------------------------------------------------------------------------
def test_flows_base_request_approval_with_and_without_commute(monkeypatch):
    from flows.base import Flow

    mock_bot = MagicMock()
    mock_queue = MagicMock()
    mock_queue.create.return_value = "req_123"
    mock_queue.wait_decision.return_value = {"cancelled": True, "reason": "timeout"}
    mock_store = MagicMock()

    flow = MagicMock(spec=Flow)
    flow.mid = "test-mid"
    flow.type = "new"
    flow.bot = mock_bot
    flow.queue = mock_queue
    flow.store = mock_store
    flow.monitor = {}
    flow.criteri = {}

    slot = Slot(
        datetime=datetime(2026, 11, 20, 11, 0),
        extra={"sede": "OSPEDALE NIGUARDA CA' GRANDA", "comune": "MILANO"},
    )

    # 1. Without HOME_COORDINATES: message should NOT contain "Distanza da casa"
    monkeypatch.delenv("HOME_COORDINATES", raising=False)
    with patch("core.config.Config.load", return_value=MagicMock(settings={})):
        Flow._request_approval(flow, slot)
        msg_without = mock_bot.notify_buttons.call_args[0][0]
        assert "Distanza da casa" not in msg_without
        assert "Apri in Google Maps" in msg_without

    # 2. With HOME_COORDINATES: message MUST contain "🧭 Distanza da casa: 🚗 ... · 🚇 ..."
    monkeypatch.setenv("HOME_COORDINATES", "45.4642, 9.1900")
    Flow._request_approval(flow, slot)
    msg_with = mock_bot.notify_buttons.call_args[0][0]
    assert "🧭 Distanza da casa:" in msg_with
    assert "🚗" in msg_with
    assert "min in auto" in msg_with


def test_flows_base_esegui_autobook_with_commute(monkeypatch):
    from flows.base import Flow

    mock_bot = MagicMock()
    mock_store = MagicMock()

    flow = MagicMock(spec=Flow)
    flow.mid = "test-mid"
    flow.type = "new"
    flow.bot = mock_bot
    flow.store = mock_store
    flow.monitor = {}
    flow.criteri = {}
    flow.execute = MagicMock()

    slot = Slot(
        datetime=datetime(2026, 11, 20, 11, 0),
        extra={"sede": "OSPEDALE NIGUARDA CA' GRANDA", "comune": "MILANO"},
    )

    monkeypatch.setenv("HOME_COORDINATES", "45.4642, 9.1900")
    Flow._esegui_autobook(flow, slot, delta_days=10, min_days=5)
    sent_pre = mock_bot.notify.call_args_list[0][0][0]
    assert "🧭 Distanza da casa:" in sent_pre
    assert "🚗" in sent_pre


def test_get_home_coordinates_from_settings(monkeypatch):
    monkeypatch.delenv("HOME_COORDINATES", raising=False)
    monkeypatch.delenv("HOME_LATITUDE", raising=False)
    monkeypatch.delenv("HOME_LONGITUDE", raising=False)

    # 1. string in settings.yaml
    mock_cfg = MagicMock()
    mock_cfg.settings = {"commute": {"home_coordinates": "45.5000, 9.2000"}}
    with patch("core.config.Config.load", return_value=mock_cfg):
        assert get_home_coordinates() == (45.5, 9.2)

    # 2. lat/lon in settings.yaml
    mock_cfg2 = MagicMock()
    mock_cfg2.settings = {"commute": {"home_latitude": 45.6, "home_longitude": 9.3}}
    with patch("core.config.Config.load", return_value=mock_cfg2):
        assert get_home_coordinates() == (45.6, 9.3)


def test_geocache_corrupted_and_save_error(tmp_path):
    from services.commute import _load_geocache, _save_geocache

    bad_file = tmp_path / "corrupt.json"
    bad_file.write_text("invalid json content")
    assert _load_geocache(bad_file) == {}

    # Save to invalid directory path should not raise
    _save_geocache({"test": [1.0, 2.0]}, cache_path="/proc/read_only_test_path.json")


def test_normalize_location_text():
    assert normalize_location_text(None) == ""
    assert normalize_location_text("") == ""
    assert normalize_location_text("  Ospedale, San... Paolo!  ") == "ospedale san paolo"


def test_nominatim_http_error_handling(tmp_path):
    import urllib.error
    cache_file = tmp_path / "geocache.json"

    # HTTP 429 rate limit error
    http_err = urllib.error.HTTPError(
        url="https://nominatim.openstreetmap.org",
        code=429,
        msg="Too Many Requests",
        hdrs={},
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=http_err):
        res = geocode_facility(
            facility="Struttura Limitata",
            municipality="Curno",
            cache_path=cache_file,
        )
        assert res is None


def test_calculate_commute_feeder_lines():
    # Test origin far from metro station (> 2000m) triggering feeder bus
    # Coordinate outside Milan (e.g., small hamlet)
    commute = calculate_commute(45.3000, 9.4000, 45.5108, 9.1889, facility_name="Niguarda")
    assert commute is not None
    assert commute.transit_minutes > 0
    assert "treno" in commute.transit_route or "bus" in commute.transit_route or "M" in commute.transit_route


def test_check_commute_filter_invalid_types():
    commute = CommuteInfo(
        distance_km=10.0,
        straight_km=7.0,
        car_minutes=15,
        car_text="🚗 15 min",
        transit_minutes=25,
        transit_route="M1",
        transit_text="🚇 25 min",
    )
    # Non-numeric or invalid filter values should not crash
    assert check_commute_filter(commute, {"max_distance_km": "invalid_num"}) == (True, "")
    assert check_commute_filter(commute, {"max_car_minutes": None}) == (True, "")
    assert check_commute_filter(commute, {"max_transit_minutes": "abc"}) == (True, "")

