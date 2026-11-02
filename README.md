# Flight Watch

Cloud-based airspace monitoring service (DSAI523 mini project).
Users save **watches** on an airport, an area (bounding box) or a single aircraft,
and the app returns live traffic from the OpenSky Network, stored in a cloud database.

> Status: week 1 skeleton — `/watches` CRUD works locally with SQLite.
> OpenSky integration, Cloud SQL and GKE are coming next.

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
| POST /watches | Create a watch | 201, 400, 422 |
| GET /watches | List watches | 200 |
| GET /watches/{id} | Read one watch | 200, 404 |
| PUT /watches/{id} | Update a watch | 200, 400, 404, 422 |
| DELETE /watches/{id} | Delete a watch | 204, 404 |
| GET /watches/{id}/aircraft | Live aircraft (todo) | 200, 404, 503 |
| GET /watches/{id}/history | Stored snapshots (todo) | 200, 404 |

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
app/watches.py    /watches CRUD endpoints and validation
tests/            pytest tests
```
