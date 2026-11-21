"""adsb.lol client (owner: Enes) - the default flight data source.

adsb.lol is a community ADS-B feed with a free REST API; unlike OpenSky it
answers from GCP. Data licence: ODbL 1.0 (attribution: "adsb.lol").

Endpoints used (https://api.adsb.lol):
  /v2/hex/{icao24}               one aircraft            (aircraft watch)
  /v2/point/{lat}/{lon}/{nm}     aircraft within nm NM   (airport and bbox watches)
There is no bounding-box endpoint, so for a box we ask for the circle that
covers it and keep only the aircraft inside the box. The circle is capped at
250 NM by the API: a box wider than ~350 NM across returns only its middle.

Units: adsb.lol sends feet and knots; we convert to metres and m/s so the
result matches the shared contract in app/flightdata.py. `alt_baro` is the
string "ground" for aircraft on the ground -> altitude None.

Rate limit without a key: 500 requests/day per IP (resets 00:00 UTC), so keep
the /aircraft cache on. A free key raises it (https://www.adsb.lol/mmb).

Settings (environment variables, see .env.example):
  ADSBLOL_API_KEY   optional key, sent as x-api-key
  ADSBLOL_URL       base URL                                  (default https://api.adsb.lol)
  ADSBLOL_TIMEOUT   seconds per HTTP request                  (default 10)
  ADSBLOL_RETRIES   extra attempts on network errors / 5xx    (default 2)
"""
import math
import os
import time

import requests

from .flightdata import FlightDataError, watch_bbox

FEET_TO_M = 0.3048
KNOTS_TO_MS = 0.514444
MAX_RADIUS_NM = 250


class AdsbLolError(FlightDataError):
    """adsb.lol could not be reached or refused the request."""


def _setting(name, default, cast):
    return cast(os.getenv(name, default))


def covering_circle(lamin, lomin, lamax, lomax):
    """(lat, lon, radius_nm): centre of the box and the distance to its
    farthest corner, rounded up and capped at the API's 250 NM."""
    lat, lon = (lamin + lamax) / 2, (lomin + lomax) / 2
    radius = max(_distance_nm(lat, lon, la, lo)
                 for la in (lamin, lamax) for lo in (lomin, lomax))
    return round(lat, 4), round(lon, 4), min(math.ceil(radius) + 1, MAX_RADIUS_NM)


def _distance_nm(lat1, lon1, lat2, lon2):
    """Great-circle distance in nautical miles (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))   # Earth radius in NM


def request_path(watch):
    """API path for a watch, and the box to filter by (None for one aircraft)."""
    if watch.kind == "aircraft":
        return f"/v2/hex/{watch.icao24}", None
    box = watch_bbox(watch)
    lat, lon, nm = covering_circle(*box)
    return f"/v2/point/{lat}/{lon}/{nm}", box


def _fetch(path):
    """GET with timeout and retries (network errors, 5xx). 429 is not retried."""
    base = os.getenv("ADSBLOL_URL", "https://api.adsb.lol").rstrip("/")
    timeout = _setting("ADSBLOL_TIMEOUT", "10", float)
    retries = _setting("ADSBLOL_RETRIES", "2", int)
    key = os.getenv("ADSBLOL_API_KEY")
    headers = {"x-api-key": key} if key else {}
    attempt = 0
    while True:
        try:
            r = requests.get(base + path, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            problem = exc.__class__.__name__
        else:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    problem = "adsb.lol sent a response that is not JSON"
            elif r.status_code == 429:
                wait = r.headers.get("Retry-After", "?")
                raise AdsbLolError(f"adsb.lol rate limit reached, retry after {wait} s")
            elif r.status_code < 500:
                raise AdsbLolError(f"adsb.lol returned {r.status_code}")
            else:
                problem = f"adsb.lol returned {r.status_code}"
        if attempt >= retries:
            raise AdsbLolError(problem)
        attempt += 1
        time.sleep(0.5 * 2 ** (attempt - 1))   # 0.5 s, 1 s, 2 s ...


def to_state(ac):
    """Map one adsb.lol aircraft (a dict) to our shape, or None to skip it.

    Skipped: no position, and non-ICAO addresses (hex starting with "~",
    e.g. TIS-B targets) which do not fit our 6-character icao24.
    """
    hex_ = (ac.get("hex") or "").lower()
    if len(hex_) != 6 or hex_.startswith("~") or ac.get("lat") is None or ac.get("lon") is None:
        return None
    alt = ac.get("alt_baro")
    if not isinstance(alt, (int, float)):            # "ground" or missing
        alt = None if alt == "ground" else ac.get("alt_geom")
    gs = ac.get("gs")
    return {"icao24": hex_,
            "callsign": (ac.get("flight") or "").strip() or None,
            "lat": ac["lat"], "lon": ac["lon"],
            "altitude": round(alt * FEET_TO_M, 1) if isinstance(alt, (int, float)) else None,
            "velocity": round(gs * KNOTS_TO_MS, 1) if isinstance(gs, (int, float)) else None}


def get_states(watch):
    """Return the aircraft currently matching `watch` (a models.Watch)."""
    path, box = request_path(watch)
    body = _fetch(path)
    states = [s for s in map(to_state, body.get("ac") or []) if s is not None]
    if box is not None:
        lamin, lomin, lamax, lomax = box
        states = [s for s in states
                  if lamin <= s["lat"] <= lamax and lomin <= s["lon"] <= lomax]
    return states
