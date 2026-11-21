"""Tests for the adsb.lol client and the source switch. No network is used:
`requests` calls are replaced with fakes."""
from types import SimpleNamespace

import pytest
import requests

from app import adsblol, flightdata, opensky


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


# Shapes taken from a real /v2/point response (feet, knots).
FLYING = {"hex": "4baad0", "flight": "THY60W  ", "lat": 41.155, "lon": 28.139,
          "alt_baro": 8575, "alt_geom": 8750, "gs": 282.2}
GROUND = {"hex": "4bb1e5", "flight": "THY9ZQ  ", "lat": 41.261, "lon": 28.721,
          "alt_baro": "ground", "gs": 8.0}
NO_POS = {"hex": "4bb1c2", "flight": "PGT2KD", "alt_baro": 1500, "gs": 95.0}
TISB = {"hex": "~2a0b1c", "lat": 41.2, "lon": 28.7, "alt_baro": 2000, "gs": 120.0}
FAR = {"hex": "4b1234", "flight": "FAR1", "lat": 42.5, "lon": 28.7, "alt_baro": 30000, "gs": 450.0}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setattr(adsblol.time, "sleep", lambda s: None)
    for name in ("ADSBLOL_API_KEY", "ADSBLOL_URL", "FLIGHT_SOURCE", "AIRPORT_RADIUS"):
        monkeypatch.delenv(name, raising=False)


def watch(**kw):
    return SimpleNamespace(**kw)


def fake_get(monkeypatch, *responses):
    """Replace requests.get; returns the list of (url, headers) it was called with."""
    calls, answers = [], iter(responses)

    def get(url, headers=None, timeout=None):
        calls.append((url, headers))
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(requests, "get", get)
    return calls


def test_converts_units_and_skips_unusable():
    assert adsblol.to_state(FLYING) == {"icao24": "4baad0", "callsign": "THY60W", "lat": 41.155,
                                        "lon": 28.139, "altitude": 2613.7, "velocity": 145.2}
    ground = adsblol.to_state(GROUND)
    assert ground["altitude"] is None and ground["velocity"] == 4.1
    assert adsblol.to_state(NO_POS) is None        # no position
    assert adsblol.to_state(TISB) is None          # non-ICAO address
    only_geom = adsblol.to_state({**FLYING, "alt_baro": None})
    assert only_geom["altitude"] == 2667.0         # falls back to alt_geom


def test_aircraft_watch_uses_hex(monkeypatch):
    calls = fake_get(monkeypatch, FakeResponse(200, {"ac": [FLYING]}))
    states = adsblol.get_states(watch(kind="aircraft", icao24="4baad0"))
    assert calls[0][0] == "https://api.adsb.lol/v2/hex/4baad0"
    assert [s["icao24"] for s in states] == ["4baad0"]


def test_box_uses_covering_circle_and_filters(monkeypatch):
    calls = fake_get(monkeypatch, FakeResponse(200, {"ac": [FLYING, GROUND, NO_POS, TISB, FAR]}))
    states = adsblol.get_states(watch(kind="bbox", lamin=40.0, lomin=26.0, lamax=41.5, lomax=30.0))
    assert calls[0][0].startswith("https://api.adsb.lol/v2/point/40.75/28.0/")
    assert sorted(s["icao24"] for s in states) == ["4baad0", "4bb1e5"]   # FAR is outside the box


def test_covering_circle_reaches_corners_and_is_capped():
    lat, lon, nm = adsblol.covering_circle(40.0, 26.0, 41.5, 30.0)
    assert (lat, lon) == (40.75, 28.0)
    assert 100 <= nm <= 105          # half-diagonal of the Marmara box is ~101 NM
    assert adsblol.covering_circle(-60, -150, 60, 150)[2] == 250


def test_airport_watch_and_unknown_airport(monkeypatch):
    calls = fake_get(monkeypatch, FakeResponse(200, {"ac": [FLYING, GROUND]}))
    states = adsblol.get_states(watch(kind="airport", airport="LTFM"))
    assert "/v2/point/41.27" in calls[0][0]
    # LTFM box is lon 28.25..29.25: FLYING (lon 28.139) is outside, GROUND inside
    assert [s["icao24"] for s in states] == ["4bb1e5"]
    with pytest.raises(flightdata.UnknownAirportError):
        adsblol.get_states(watch(kind="airport", airport="ZZZZ"))


def test_empty_and_null_ac(monkeypatch):
    fake_get(monkeypatch, FakeResponse(200, {"ac": []}), FakeResponse(200, {"ac": None}))
    assert adsblol.get_states(watch(kind="aircraft", icao24="abcdef")) == []
    assert adsblol.get_states(watch(kind="aircraft", icao24="abcdef")) == []


def test_api_key_header(monkeypatch):
    monkeypatch.setenv("ADSBLOL_API_KEY", "k123")
    calls = fake_get(monkeypatch, FakeResponse(200, {"ac": []}))
    adsblol.get_states(watch(kind="aircraft", icao24="abcdef"))
    assert calls[0][1] == {"x-api-key": "k123"}


def test_retries_then_fails(monkeypatch):
    calls = fake_get(monkeypatch, requests.ConnectTimeout(), FakeResponse(502), requests.ReadTimeout())
    with pytest.raises(adsblol.AdsbLolError):
        adsblol.get_states(watch(kind="aircraft", icao24="abcdef"))
    assert len(calls) == 3           # 1 try + 2 retries


def test_retry_recovers_and_bad_json_is_retried(monkeypatch):
    fake_get(monkeypatch, FakeResponse(200, ValueError("not json")), FakeResponse(200, {"ac": [FLYING]}))
    assert len(adsblol.get_states(watch(kind="aircraft", icao24="4baad0"))) == 1


def test_rate_limit_is_not_retried(monkeypatch):
    calls = fake_get(monkeypatch, FakeResponse(429, headers={"Retry-After": "3600"}))
    with pytest.raises(adsblol.AdsbLolError, match="3600"):
        adsblol.get_states(watch(kind="aircraft", icao24="abcdef"))
    assert len(calls) == 1


def test_errors_are_flight_data_errors():
    assert issubclass(adsblol.AdsbLolError, flightdata.FlightDataError)
    assert issubclass(opensky.OpenSkyError, flightdata.FlightDataError)


def test_source_switch(monkeypatch):
    monkeypatch.setattr(adsblol, "get_states", lambda w: ["adsblol"])
    monkeypatch.setattr(opensky, "get_states", lambda w: ["opensky"])
    w = watch(kind="aircraft", icao24="abcdef")
    assert flightdata.get_states(w) == ["adsblol"]          # default
    monkeypatch.setenv("FLIGHT_SOURCE", "OpenSky")
    assert flightdata.get_states(w) == ["opensky"]
    monkeypatch.setenv("FLIGHT_SOURCE", "flightradar")
    with pytest.raises(flightdata.FlightDataError, match="FLIGHT_SOURCE"):
        flightdata.get_states(w)
