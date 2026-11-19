"""Tests for the OpenSky client and the /aircraft cache. No network is used:
`requests` calls are replaced with fakes."""
from types import SimpleNamespace

import pytest
import requests

from app import create_app, opensky


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


# icao24, callsign, country, t_pos, last_contact, lon, lat, baro_alt, on_ground,
# velocity, track, v_rate, sensors, geo_alt, squawk, spi, source
VECTOR = ["4baa4e", "THY7AB  ", "Turkey", 0, 0, 28.74, 41.26, 3200.0, False,
          140.0, 90.0, 0.0, None, 3300.0, None, False, 0]
NO_POS = ["4bb1c2", "PGT2KD", "Turkey", 0, 0, None, None, None, True,
          0.0, 0.0, 0.0, None, None, None, False, 0]


@pytest.fixture(autouse=True)
def no_sleep_no_creds(monkeypatch):
    monkeypatch.setattr(opensky.time, "sleep", lambda s: None)
    monkeypatch.delenv("OPENSKY_CLIENT_ID", raising=False)
    monkeypatch.delenv("OPENSKY_CLIENT_SECRET", raising=False)
    opensky._token.update(value=None, expires=0.0)


def watch(**kw):
    return SimpleNamespace(**kw)


def test_params_for_each_kind():
    assert opensky.query_params(watch(kind="aircraft", icao24="4baa4e")) == {"icao24": "4baa4e"}
    box = opensky.query_params(watch(kind="bbox", lamin=40, lomin=26, lamax=41.5, lomax=30))
    assert box == {"lamin": 40, "lomin": 26, "lamax": 41.5, "lomax": 30}
    ltfm = opensky.query_params(watch(kind="airport", airport="LTFM"))   # 41.28, 28.75
    assert ltfm["lamin"] < 41.28 < ltfm["lamax"] and ltfm["lomin"] < 28.75 < ltfm["lomax"]
    with pytest.raises(opensky.UnknownAirportError):
        opensky.query_params(watch(kind="airport", airport="ZZZZ"))


def test_maps_state_vectors(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, {"states": [VECTOR, NO_POS]}))
    states = opensky.get_states(watch(kind="aircraft", icao24="4baa4e"))
    assert states == [{"icao24": "4baa4e", "callsign": "THY7AB", "lat": 41.26, "lon": 28.74,
                       "altitude": 3200.0, "velocity": 140.0}]   # NO_POS dropped


def test_empty_states_is_empty_list(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(200, {"time": 1, "states": None}))
    assert opensky.get_states(watch(kind="aircraft", icao24="4baa4e")) == []


def test_retries_then_fails(monkeypatch):
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        raise requests.Timeout()
    monkeypatch.setattr(requests, "get", flaky)
    with pytest.raises(opensky.OpenSkyError):
        opensky.get_states(watch(kind="aircraft", icao24="4baa4e"))
    assert len(calls) == 3          # 1 try + 2 retries (default)


def test_retry_recovers_after_5xx(monkeypatch):
    answers = iter([FakeResponse(502), FakeResponse(200, {"states": [VECTOR]})])
    monkeypatch.setattr(requests, "get", lambda *a, **k: next(answers))
    assert len(opensky.get_states(watch(kind="aircraft", icao24="4baa4e"))) == 1


def test_rate_limit_is_not_retried(monkeypatch):
    calls = []

    def limited(*a, **k):
        calls.append(1)
        return FakeResponse(429, headers={"X-Rate-Limit-Retry-After-Seconds": "30"})
    monkeypatch.setattr(requests, "get", limited)
    with pytest.raises(opensky.OpenSkyError, match="30"):
        opensky.get_states(watch(kind="aircraft", icao24="4baa4e"))
    assert len(calls) == 1


def test_token_is_fetched_once_and_sent(monkeypatch):
    monkeypatch.setenv("OPENSKY_CLIENT_ID", "id")
    monkeypatch.setenv("OPENSKY_CLIENT_SECRET", "secret")
    token_calls, seen_headers = [], []
    monkeypatch.setattr(requests, "post", lambda *a, **k: token_calls.append(k["data"]) or
                        FakeResponse(200, {"access_token": "abc", "expires_in": 1800}))
    monkeypatch.setattr(requests, "get", lambda *a, **k: seen_headers.append(k["headers"]) or
                        FakeResponse(200, {"states": []}))
    for _ in range(2):
        opensky.get_states(watch(kind="aircraft", icao24="4baa4e"))
    assert len(token_calls) == 1 and token_calls[0]["grant_type"] == "client_credentials"
    assert seen_headers == [{"Authorization": "Bearer abc"}] * 2


# ------------------------------------------------------------ /aircraft cache

@pytest.fixture
def cached_client(monkeypatch):
    calls = []

    def fake(w):
        calls.append(w.id)
        return [{"icao24": "4baa4e", "callsign": "THY7AB", "lat": 41.26, "lon": 28.74,
                 "altitude": 3200.0, "velocity": 140.0}]
    monkeypatch.setattr(opensky, "get_states", fake)
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True,
                      "OPENSKY_CACHE_SECONDS": 60})
    return app.test_client(), calls


def test_cache_hit_within_window(cached_client):
    client, calls = cached_client
    wid = client.post("/watches", json={"name": "a", "kind": "aircraft", "icao24": "4baa4e"}).get_json()["id"]
    first = client.get(f"/watches/{wid}/aircraft")
    second = client.get(f"/watches/{wid}/aircraft")
    assert first.headers["X-Cache"] == "MISS" and second.headers["X-Cache"] == "HIT"
    assert first.get_json() == second.get_json()
    assert calls == [wid]                                  # OpenSky called once
    assert len(client.get(f"/watches/{wid}/history").get_json()) == 1   # not stored twice


def test_cache_expires(cached_client, monkeypatch):
    client, calls = cached_client
    wid = client.post("/watches", json={"name": "a", "kind": "aircraft", "icao24": "4baa4e"}).get_json()["id"]
    client.get(f"/watches/{wid}/aircraft")
    from datetime import timedelta
    from app import watches
    later = watches.utcnow() + timedelta(seconds=61)
    monkeypatch.setattr(watches, "utcnow", lambda: later)
    assert client.get(f"/watches/{wid}/aircraft").headers["X-Cache"] == "MISS"
    assert len(calls) == 2


def test_unknown_airport_is_422():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    client = app.test_client()
    wid = client.post("/watches", json={"name": "x", "kind": "airport", "airport": "ZZZZ"}).get_json()["id"]
    assert client.get(f"/watches/{wid}/aircraft").status_code == 422


def test_healthz():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    assert app.test_client().get("/healthz").get_json() == {"status": "ok"}
