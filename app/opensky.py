"""OpenSky Network client (owner: Enes).

The API code only calls `get_states(watch)`. Right now it returns MOCK data
so the endpoints can be built and tested; Enes replaces the body with real
OpenSky calls (OAuth2 client credentials, timeouts, retries).

Contract:
  get_states(watch) -> list of dicts shaped like
      {"icao24": "4baa4e", "callsign": "THY7AB", "lat": 41.26, "lon": 28.74,
       "altitude": 3200.0, "velocity": 140.0}
  raises OpenSkyError when OpenSky is down, slow or rate-limited
  (the endpoint turns that into HTTP 503).
"""


class OpenSkyError(Exception):
    """OpenSky could not be reached or refused the request."""


# TODO(Enes): delete once real OpenSky calls are in place.
MOCK_STATES = [
    {"icao24": "4baa4e", "callsign": "THY7AB", "lat": 41.26, "lon": 28.74,
     "altitude": 3200.0, "velocity": 140.0},
    {"icao24": "4bb1c2", "callsign": "PGT2KD", "lat": 40.90, "lon": 29.31,
     "altitude": 1500.0, "velocity": 95.0},
]


def get_states(watch):
    """Return the aircraft currently matching `watch` (a models.Watch).

    TODO(Enes): call GET https://opensky-network.org/api/states/all with
    lamin/lomin/lamax/lomax (bbox), icao24 (aircraft), or a box around the
    airport; map each state vector to the dict shape above.
    """
    if watch.kind == "aircraft":
        return [s for s in MOCK_STATES if s["icao24"] == watch.icao24]
    return list(MOCK_STATES)
