# Flight Watch

Cloud-based airspace monitoring service (DSAI523 mini project).
Users save **watches** on an airport, an area (bounding box) or a single aircraft,
and the app returns live traffic from the OpenSky Network, stored in a cloud database.

> Status: week 1 — all endpoints work locally with SQLite. `/aircraft` returns
> **mock** data until the real OpenSky client lands. Cloud SQL and GKE come next.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python run.py          # http://localhost:5001
.venv/bin/pytest -q              # run tests
```

## Endpoints

| Method + path | Purpose | Status codes |
|---|---|---|
| GET /health | App and database status (`database`: sqlite or postgresql) | 200, 503 |
| POST /watches | Create a watch | 201, 400, 422 |
| GET /watches | List watches | 200 |
| GET /watches/{id} | Read one watch | 200, 404 |
| PUT /watches/{id} | Update a watch | 200, 400, 404, 422 |
| DELETE /watches/{id} | Delete a watch | 204, 404 |
| GET /watches/{id}/aircraft | Live aircraft (mock data for now) | 200, 404, 503 |
| GET /watches/{id}/history?limit=100 | Stored snapshots, newest first | 200, 404, 422 |

**400** = body is not JSON or a required field is missing.
**422** = fields are present but a value is invalid.

### Watch kinds

```json
{"name": "Istanbul Airport", "kind": "airport", "airport": "LTFM"}
{"name": "Marmara", "kind": "bbox", "bbox": {"lamin": 40.0, "lomin": 26.0, "lamax": 41.5, "lomax": 30.0}}
{"name": "One aircraft", "kind": "aircraft", "icao24": "4baa4e"}
```

### Aircraft data shape (agreed with the OpenSky client)

```json
[{"icao24": "4baa4e", "callsign": "THY7AB", "lat": 41.26, "lon": 28.74,
  "altitude": 3200, "velocity": 140, "time": "2026-11-10T14:00:00Z"}]
```

## Project layout

```
app/__init__.py   app factory, config, error handlers
app/models.py     database tables: watches, aircraft_snapshots
app/watches.py    /watches endpoints and validation
app/opensky.py    OpenSky client (mock for now) - get_states(watch)
tests/            pytest tests
```

## Team and work split

| Area | Owner |
|---|---|
| REST API, validation, status codes | Sude |
| Cloud database (Cloud SQL PostgreSQL) | Sude |
| OpenSky client and caching | Enes |
| Docker, GKE, autoscaling (HPA), load test | Enes |
| README and code documentation | Sude (lead), both |

## Handoff: where Enes plugs in

Everything is in place for the OpenSky and infrastructure work:

1. **`app/opensky.py`** - replace the mock body of `get_states(watch)` with real
   OpenSky calls. Keep the return shape (see the JSON above) and raise
   `OpenSkyError` on failures; the endpoint already turns that into 503.
   Put credentials in `.env` (never commit them) and add the names to `.env.example`.
2. **Cache** - in `watch_aircraft()` (`app/watches.py`), reuse snapshots younger
   than N seconds instead of calling OpenSky every time.
3. **Infrastructure** - Dockerfile (gunicorn), GKE Autopilot deployment,
   `Service type: LoadBalancer`, HPA, Locust load test.

## Configuration rule

The database address is never written in code. It comes only from `DATABASE_URL`
(`.env` locally, a Kubernetes Secret on GKE). Same for OpenSky credentials.

## Release checklist (before every deploy)

1. Tests pass: `.venv/bin/pytest -q`
2. No secrets in the code or in git (`git diff` / `git log -p` show no passwords or tokens)
3. Right database: after deploy, `GET /health` shows `"database": "postgresql"`
4. Cost: when the work session ends, stop Cloud SQL and scale the GKE deployment to 0

Workflow: one branch per feature, open a pull request, the other person reviews.
Run `.venv/bin/pytest -q` before pushing.
