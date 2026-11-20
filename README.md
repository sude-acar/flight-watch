# Flight Watch

Cloud-based airspace monitoring service (DSAI523 mini project).
Users save **watches** on an airport, an area (bounding box) or a single aircraft,
and the app returns live traffic from the OpenSky Network, stored in a cloud database.

> Status: week 3 — all endpoints work locally (SQLite) and against Cloud SQL.
> `/aircraft` returns **live** OpenSky data with a short database-backed cache.
> Docker image, GKE manifests and a Locust load test are in the repo; GKE deploy comes next.

## Architecture

```
user ──HTTP──> load balancer ──> Flask pods on GKE (autoscaled) ──> Cloud SQL (PostgreSQL)
                                          │
                                          └──> OpenSky Network API
```

The pods are stateless. Watches and aircraft snapshots live in Cloud SQL, which also
acts as the cache for OpenSky responses, so every pod sees the same data.

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

Project `dsai523-510708`, region `europe-west1` (same as Cloud SQL).

```bash
# 1. Build and push the image to Artifact Registry
gcloud artifacts repositories create flightwatch --repository-format=docker --location=europe-west1
gcloud builds submit --tag europe-west1-docker.pkg.dev/dsai523-510708/flightwatch/api:v1

# 2. Create the cluster (Autopilot has Workload Identity switched on)
gcloud container clusters create-auto flightwatch --region=europe-west1
gcloud container clusters get-credentials flightwatch --region=europe-west1

# 3. Workload Identity: let the Kubernetes service account `flightwatch`
#    act as the Google service account `flightwatch-sql` (roles/cloudsql.client).
#    Works only after the cluster exists (it creates the identity pool).
gcloud iam service-accounts add-iam-policy-binding   flightwatch-sql@dsai523-510708.iam.gserviceaccount.com   --role=roles/iam.workloadIdentityUser   --member="serviceAccount:dsai523-510708.svc.id.goog[default/flightwatch]"

# 4. Secrets (never commit these)
kubectl create secret generic flightwatch-secrets   --from-literal=INSTANCE_CONNECTION_NAME='dsai523-510708:europe-west1:flightwatch-db'   --from-literal=DB_USER='flightwatch' --from-literal=DB_PASSWORD='...' --from-literal=DB_NAME='flightwatch'   --from-literal=OPENSKY_CLIENT_ID='...' --from-literal=OPENSKY_CLIENT_SECRET='...'

# 5. Deploy and get the public IP (EXTERNAL-IP can take a minute)
kubectl apply -f k8s/
kubectl get service flightwatch
curl http://EXTERNAL_IP/health      # expect "database": "postgresql"
```

Pods need a shared database: on GKE the app must use Cloud SQL
(SQLite would give every pod its own file).

**Workload Identity** (how pods get into Cloud SQL without a key file):

| Piece | Name | Status |
|---|---|---|
| Google service account | `flightwatch-sql@dsai523-510708.iam.gserviceaccount.com` | created |
| Its project role | `roles/cloudsql.client` | granted |
| Kubernetes service account | `flightwatch` (`k8s/serviceaccount.yaml`), used by the Deployment | in repo |
| Link between the two | `roles/iam.workloadIdentityUser` (step 3) | granted (stays when the cluster is deleted) |

The HPA keeps average CPU near 60 % of the 250m request with 2–10 pods.
Watch it during a load test with `kubectl get hpa flightwatch --watch`.

**Cost:** the cluster fee is covered by the GKE free tier (one Autopilot cluster per
billing account); 2 pods + the load balancer are roughly $0.06/hour. Scaling to 0
pods does not stop the load balancer or cluster fee, so delete the cluster after a session:
`kubectl delete -f k8s/` then `gcloud container clusters delete flightwatch --region=europe-west1`.

### Known issue: OpenSky blocks GCP (tested 2026-11-20)

First deploy: pods started, Workload Identity worked and `/health` returned
`"database": "postgresql"`, watch CRUD worked against Cloud SQL. But
`/watches/{id}/aircraft` returned **503** (`OpenSky unavailable: ConnectTimeout`).

From inside a pod, `www.google.com` and `api.github.com` answer, while
`opensky-network.org` and `auth.opensky-network.org` time out at connection level.
The same requests work from a home connection. OpenSky's API docs say they may block
AWS and other hyperscalers' IP ranges because of abuse, so this is a deliberate
block, not a bug in our client; API credentials will not help (the token server is
blocked too). Proxies or VPNs to hide the origin are not an option: they go around
OpenSky's policy and could get the account banned.

Options under discussion:

1. **Collector outside GCP** (proposed): a small script on a non-cloud machine calls
   OpenSky for the active watches every N seconds and writes `aircraft_snapshots`
   into Cloud SQL; GKE pods only read the database. Reuses `opensky.get_states()`.
   Data is only fresh while the collector runs.
2. **Ask OpenSky** for academic access from GCP (can run in parallel).
3. **Another ADS-B source** (e.g. adsb.lol, airplanes.live, adsb.fi) behind
   `get_states()`; terms and cloud access not checked yet.

## Load test (Locust)

```bash
pip install -r loadtest/requirements.txt
locust -f loadtest/locustfile.py --host http://EXTERNAL_IP   --headless -u 100 -r 10 -t 5m --csv results/run1
```

All simulated users share three watches created at test start, so OpenSky is
called at most once per watch per cache window and its daily limit is safe.

## Cloud SQL

| | |
|---|---|
| GCP project | `dsai523-510708` |
| Instance | `flightwatch-db` (PostgreSQL 16, `db-f1-micro`, `europe-west1`) |
| Database / user | `flightwatch` / `flightwatch` |

When `INSTANCE_CONNECTION_NAME` is set (see `.env.example`), the app connects with the
[Cloud SQL Python Connector](https://github.com/GoogleCloudPlatform/cloud-sql-python-connector);
otherwise it uses `DATABASE_URL`. The connector authorises with Google credentials, so
there is no IP allow-list or proxy. Locally, run `gcloud auth application-default login` once.

The instance is **stopped when not in use** to save credits:

```bash
gcloud sql instances patch flightwatch-db --activation-policy=ALWAYS   # start (~1-2 min)
gcloud sql instances patch flightwatch-db --activation-policy=NEVER    # stop
```

## Configuration rule

The database address is never written in code. It comes only from environment variables (`DATABASE_URL` or the Cloud SQL settings)
(`.env` locally, a Kubernetes Secret on GKE). Same for OpenSky credentials.

## Release checklist (before every deploy)

1. Tests pass: `.venv/bin/pytest -q`
2. No secrets in the code or in git (`git diff` / `git log -p` show no passwords or tokens)
3. Right database: after deploy, `GET /health` shows `"database": "postgresql"`
4. Cost: when the work session ends, stop Cloud SQL and scale the GKE deployment to 0

Workflow: one branch per feature, open a pull request, the other person reviews.
Run `.venv/bin/pytest -q` before pushing.
