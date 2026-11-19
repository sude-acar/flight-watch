# Flight Watch

Cloud-based airspace monitoring service (DSAI523 mini project).
Users save **watches** on an airport, an area (bounding box) or a single aircraft,
and the app returns live traffic from the OpenSky Network, stored in a cloud database.

> Status: week 2 — all endpoints work locally with SQLite. `/aircraft` returns
> **live** OpenSky data with a short database-backed cache. Docker image, GKE
> manifests and a Locust load test are in the repo; Cloud SQL comes next.

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
| GET /watches/{id}/aircraft | Live aircraft from OpenSky (cached, header `X-Cache: HIT/MISS`) | 200, 404, 422, 503 |
| GET /watches/{id}/history?limit=100 | Stored snapshots, newest first | 200, 404, 422 |
| GET /healthz | Health check for Kubernetes probes | 200 |

**400** = body is not JSON or a required field is missing.
**422** = fields are present but a value is invalid (for `/aircraft`: an airport
code we have no coordinates for).
**503** = OpenSky is down, too slow or rate-limited.

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
app/opensky.py    OpenSky client - get_states(watch), OAuth2 token, retries
tests/            pytest tests (no network: OpenSky is faked)
Dockerfile        gunicorn image, port 8080
k8s/              GKE Deployment, LoadBalancer Service, HPA
loadtest/         Locust load test
```

## OpenSky client and cache

- `get_states(watch)` calls `GET https://opensky-network.org/api/states/all` with
  `icao24` (aircraft watch), the box (bbox watch) or a ±0.5° box around the
  airport (airport watch; coordinates from the offline `airportsdata` package).
- With `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` in `.env` it uses an OAuth2
  client-credentials token (cached, renewed before expiry, refreshed once on 401).
  Without them it calls OpenSky anonymously — fine for development, but the
  daily credit limit is much lower.
- 10 s timeout, 2 retries with backoff on network errors and 5xx. A 429
  (rate limit) is not retried; it becomes a 503 straight away.
- **Cache:** `/aircraft` reuses the latest stored fetch for a watch if it is
  younger than `OPENSKY_CACHE_SECONDS` (default 15). The cache is the
  `aircraft_snapshots` table, so all pods share it. A fetch that found no
  aircraft stores nothing and is therefore not cached.

## Team and work split

| Area | Owner |
|---|---|
| REST API, validation, status codes | Sude |
| Cloud database (Cloud SQL PostgreSQL) | Sude |
| OpenSky client and caching | Enes |
| Docker, GKE, autoscaling (HPA), load test | Enes |
| README and code documentation | Sude (lead), both |

## Deploy to GKE (Autopilot)

Replace `PROJECT_ID` and `REGION` (e.g. `europe-west1`); also in `k8s/deployment.yaml`.

```bash
# 1. Build and push the image to Artifact Registry
gcloud artifacts repositories create flightwatch --repository-format=docker --location=REGION
gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT_ID/flightwatch/api:v1

# 2. Create the cluster
gcloud container clusters create-auto flightwatch --region=REGION
gcloud container clusters get-credentials flightwatch --region=REGION

# 3. Secrets (never commit these)
kubectl create secret generic flightwatch-secrets   --from-literal=DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/flightwatch'   --from-literal=OPENSKY_CLIENT_ID='...' --from-literal=OPENSKY_CLIENT_SECRET='...'

# 4. Deploy and get the public IP (EXTERNAL-IP can take a minute)
kubectl apply -f k8s/
kubectl get service flightwatch
curl http://EXTERNAL_IP/healthz
```

Pods need a shared database: on GKE `DATABASE_URL` must point at Cloud SQL
(SQLite would give every pod its own file). The Postgres driver and Cloud SQL
connection are part of the Cloud SQL work.

The HPA keeps average CPU near 60 % of the 250m request with 2–10 pods.
Watch it during a load test with `kubectl get hpa flightwatch --watch`.

## Load test (Locust)

```bash
pip install -r loadtest/requirements.txt
locust -f loadtest/locustfile.py --host http://EXTERNAL_IP   --headless -u 100 -r 10 -t 5m --csv results/run1
```

All simulated users share three watches created at test start, so OpenSky is
called at most once per watch per cache window and its daily limit is safe.

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
