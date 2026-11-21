"""Live flight data: picks the source and holds what all sources share.

The API code only calls `flightdata.get_states(watch)`.

Contract (same for every source):
  get_states(watch) -> list of dicts shaped like
      {"icao24": "4baa4e", "callsign": "THY7AB", "lat": 41.26, "lon": 28.74,
       "altitude": 975.4, "velocity": 72.0}
  altitude in metres (None = on the ground or unknown), velocity in m/s.
  raises FlightDataError when the source is down, slow or rate-limited
  (the endpoint turns that into HTTP 503).
  raises UnknownAirportError when an airport watch has an ICAO code we have
  no coordinates for (the endpoint turns that into HTTP 422).

Sources (environment variable FLIGHT_SOURCE):
  adsblol  (default) adsb.lol community feed - works from GCP          app/adsblol.py
  opensky  OpenSky Network - blocks GCP/cloud IPs, fine from home       app/opensky.py

Shared settings:
  AIRPORT_RADIUS   half-size in degrees of the box drawn around an airport (default 0.5)
"""
import os

import airportsdata

SOURCES = ("adsblol", "opensky")


class FlightDataError(Exception):
    """The flight data source could not be reached or refused the request."""


class UnknownAirportError(Exception):
    """No coordinates are known for the airport code of a watch."""


_airports = None


def airport_bbox(code):
    """Square box (lamin, lomin, lamax, lomax) around an ICAO airport code."""
    global _airports
    if _airports is None:
        _airports = airportsdata.load("ICAO")   # ~28k airports, offline data
    airport = _airports.get(code)
    if airport is None:
        raise UnknownAirportError(f"unknown airport code: {code}")
    r = float(os.getenv("AIRPORT_RADIUS", "0.5"))
    lat, lon = airport["lat"], airport["lon"]
    return (max(lat - r, -90.0), max(lon - r, -180.0),
            min(lat + r, 90.0), min(lon + r, 180.0))


def watch_bbox(watch):
    """(lamin, lomin, lamax, lomax) for an airport or bbox watch."""
    if watch.kind == "airport":
        return airport_bbox(watch.airport)
    return watch.lamin, watch.lomin, watch.lamax, watch.lomax


def source_name():
    """Configured source; an unknown value is a deployment mistake, so fail loudly."""
    name = os.getenv("FLIGHT_SOURCE", "adsblol").lower()
    if name not in SOURCES:
        raise FlightDataError(f"FLIGHT_SOURCE must be one of {', '.join(SOURCES)}, not {name!r}")
    return name


def get_states(watch):
    """Aircraft currently matching `watch`, from the configured source."""
    from . import adsblol, opensky    # imported here: both import this module
    source = adsblol if source_name() == "adsblol" else opensky
    return source.get_states(watch)
