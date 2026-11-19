"""OpenSky Network client (owner: Enes).

The API code only calls `get_states(watch)`.

Contract:
  get_states(watch) -> list of dicts shaped like
      {"icao24": "4baa4e", "callsign": "THY7AB", "lat": 41.26, "lon": 28.74,
       "altitude": 3200.0, "velocity": 140.0}
  raises OpenSkyError when OpenSky is down, slow or rate-limited
  (the endpoint turns that into HTTP 503).
  raises UnknownAirportError when an airport watch has an ICAO code we have
  no coordinates for (the endpoint turns that into HTTP 422).

Authentication: if OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET are set, an
OAuth2 client-credentials token is fetched and cached until shortly before it
expires. Without them the client calls OpenSky anonymously (lower rate limit,
10 s data resolution) - fine for local development.

Settings (environment variables, see .env.example):
  OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET  API client credentials (optional)
  OPENSKY_TIMEOUT          seconds per HTTP request          (default 10)
  OPENSKY_RETRIES          extra attempts on network/5xx     (default 2)
  OPENSKY_AIRPORT_RADIUS   half-size in degrees of the box
                           drawn around an airport           (default 0.5)
"""
import os
import threading
import time

import airportsdata
import requests

API_URL = "https://opensky-network.org/api/states/all"
TOKEN_URL = ("https://auth.opensky-network.org/auth/realms/opensky-network"
             "/protocol/openid-connect/token")

# Positions inside a state vector (see OpenSky REST API docs, "State Vectors").
ICAO24, CALLSIGN, LON, LAT, BARO_ALT, VELOCITY, GEO_ALT = 0, 1, 5, 6, 7, 9, 13


class OpenSkyError(Exception):
    """OpenSky could not be reached or refused the request."""


class UnknownAirportError(Exception):
    """No coordinates are known for the airport code of a watch."""


def _setting(name, default, cast):
    return cast(os.getenv(name, default))


# ---------------------------------------------------------------- airports

_airports = None


def airport_bbox(code):
    """Square box (lamin, lomin, lamax, lomax) around an ICAO airport code."""
    global _airports
    if _airports is None:
        _airports = airportsdata.load("ICAO")   # ~28k airports, offline data
    airport = _airports.get(code)
    if airport is None:
        raise UnknownAirportError(f"unknown airport code: {code}")
    r = _setting("OPENSKY_AIRPORT_RADIUS", "0.5", float)
    lat, lon = airport["lat"], airport["lon"]
    return (max(lat - r, -90.0), max(lon - r, -180.0),
            min(lat + r, 90.0), min(lon + r, 180.0))


def query_params(watch):
    """OpenSky query string parameters for a watch."""
    if watch.kind == "aircraft":
        return {"icao24": watch.icao24}
    if watch.kind == "airport":
        lamin, lomin, lamax, lomax = airport_bbox(watch.airport)
    else:
        lamin, lomin, lamax, lomax = watch.lamin, watch.lomin, watch.lamax, watch.lomax
    return {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}


# ------------------------------------------------------------------- token

_token = {"value": None, "expires": 0.0}
_token_lock = threading.Lock()   # gunicorn threads share one token


def _credentials():
    cid, secret = os.getenv("OPENSKY_CLIENT_ID"), os.getenv("OPENSKY_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


def _get_token(force=False):
    """Bearer token, or None for anonymous access. Refreshed 60 s before expiry."""
    creds = _credentials()
    if creds is None:
        return None
    with _token_lock:
        if not force and _token["value"] and time.time() < _token["expires"] - 60:
            return _token["value"]
        try:
            r = requests.post(TOKEN_URL, timeout=_setting("OPENSKY_TIMEOUT", "10", float),
                              data={"grant_type": "client_credentials",
                                    "client_id": creds[0], "client_secret": creds[1]})
        except requests.RequestException as exc:
            raise OpenSkyError(f"token request failed: {exc.__class__.__name__}") from exc
        if r.status_code != 200:
            raise OpenSkyError(f"token request returned {r.status_code}")
        body = r.json()
        _token["value"] = body["access_token"]
        _token["expires"] = time.time() + float(body.get("expires_in", 1800))
        return _token["value"]


# ----------------------------------------------------------------- request

def _fetch(params):
    """GET /states/all with timeout, retries (network errors, 5xx) and one
    token refresh on 401. Returns the decoded JSON body."""
    timeout = _setting("OPENSKY_TIMEOUT", "10", float)
    retries = _setting("OPENSKY_RETRIES", "2", int)
    refreshed = False
    attempt = 0
    while True:
        token = _get_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            r = requests.get(API_URL, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            problem = f"{exc.__class__.__name__}"
        else:
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401 and token and not refreshed:
                _get_token(force=True)      # token revoked/expired early: renew once
                refreshed = True
                continue
            if r.status_code == 429:
                wait = r.headers.get("X-Rate-Limit-Retry-After-Seconds", "?")
                raise OpenSkyError(f"rate limited, retry after {wait} s")
            if r.status_code < 500:
                raise OpenSkyError(f"OpenSky returned {r.status_code}")
            problem = f"OpenSky returned {r.status_code}"
        if attempt >= retries:
            raise OpenSkyError(problem)
        attempt += 1
        time.sleep(0.5 * 2 ** (attempt - 1))   # 0.5 s, 1 s, 2 s ...


def to_state(vector):
    """Map one OpenSky state vector (a list) to our dict shape."""
    altitude = vector[BARO_ALT] if vector[BARO_ALT] is not None else vector[GEO_ALT]
    return {"icao24": vector[ICAO24],
            "callsign": (vector[CALLSIGN] or "").strip() or None,
            "lat": vector[LAT], "lon": vector[LON],
            "altitude": altitude, "velocity": vector[VELOCITY]}


def get_states(watch):
    """Return the aircraft currently matching `watch` (a models.Watch)."""
    body = _fetch(query_params(watch))
    vectors = body.get("states") or []          # OpenSky sends null when empty
    return [to_state(v) for v in vectors if v[LAT] is not None and v[LON] is not None]
