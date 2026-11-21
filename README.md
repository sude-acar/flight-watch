# Flight Watch

Cloud-based airspace monitoring service (DSAI523 mini project).
Users save **watches** on an airport, an area (bounding box) or a single aircraft,
and the app returns live traffic from the adsb.lol ADS-B feed (OpenSky as an alternative),
stored in a cloud database.

> Status: week 4 — all endpoints work locally (SQLite) and against Cloud SQL, and a
> first GKE deploy worked (Workload Identity → Cloud SQL). `/aircraft` returns **live**
> data from **adsb.lol** with a short database-backed cache; OpenSky blocks GCP, see
> "Flight data sources".

## Architecture

```
user ──HTTP──> load balancer ──> Flask pods on GKE (autoscaled) ──> Cloud SQL (PostgreSQL)
                                          │
                                          └──> adsb.lol API (FLIGHT_SOURCE; OpenSky optional)
```

The pods are stateless. Watches and aircraft snapshots live in Cloud SQL, which also
acts as the cache for flight data responses, so every pod sees the same data.

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
| GET /watches/{id}/aircraft | Live aircraft from the flight data source (cached, header `X-Cache: HIT/MISS`) | 200, 404, 422, 503 |
| GET /watches/{id}/history?limit=100 | Stored snapshots, newest first | 200, 404, 422 |
| GET /healthz | Health check for Kubernetes probes | 200 |

**400** = body is not JSON or a required field is missing.
**422** = fields are present but a value is invalid (for `/aircraft`: an airport
code we have no coordinates for).
**503** = the flight data source is down, too slow or rate-limited.

### Watch kinds

```json
{"name": "Istanbul Airport", "kind": "airport", "airport": "LTFM"}
{"name": "Marmara", "kind": "bbox", "bbox": {"lamin": 40.0, "lomin": 26.0, "lamax": 41.5, "lomax": 30.0}}
{"name": "One aircraft", "kind": "aircraft", "icao24": "4baa4e"}
```

### Aircraft data shape (same for every flight data source)

```json
[{"icao24": "4baa4e", "callsign": "THY7AB", "lat": 41.26, "lon": 28.74,
  "altitude": 3200, "velocity": 140, "time": "2026-11-10T14:00:00Z"}]
```

`altitude` in metres (`null` = on the ground or unknown), `velocity` in m/s.

## Project layout

```
app/__init__.py   app factory, config, error handlers
app/models.py     database tables: watches, aircraft_snapshots
app/watches.py    /watches endpoints and validation
app/flightdata.py picks the source (FLIGHT_SOURCE), shared errors, airport boxes
app/adsblol.py    adsb.lol client (default) - get_states(watch), unit conversion
app/opensky.py    OpenSky client - get_states(watch), OAuth2 token, retries
tests/            pytest tests (no network: flight data sources are faked)
Dockerfile        gunicorn image, port 8080
k8s/              GKE Deployment, LoadBalancer Service, HPA
loadtest/         Locust load test
```

## Flight data sources and cache

The endpoint calls `flightdata.get_states(watch)`, which uses the source named in
`FLIGHT_SOURCE`. Both sources return the same shape (see above).

| | **adsb.lol** (`adsblol`, default) | **OpenSky** (`opensky`) |
|---|---|---|
| Works from GCP / GKE | yes | **no**, OpenSky blocks cloud IP ranges |
| Aircraft watch | `/v2/hex/{icao24}` | `states/all?icao24=` |
| Airport / area watch | `/v2/point/{lat}/{lon}/{nm}`: circle around the box, then filtered to the box (max 250 NM radius) | `states/all?lamin=…` |
| Units sent | feet, knots → converted to m, m/s | m, m/s |
| Limit | 500 requests/day per IP without a key; `ADSBLOL_API_KEY` raises it ([free key](https://www.adsb.lol/mmb)) | daily credits; more with `OPENSKY_CLIENT_ID/SECRET` |
| Data licence | ODbL 1.0, attribution "adsb.lol" | OpenSky terms of use |

- Airport watches use a ±`AIRPORT_RADIUS`° box (default 0.5) around the airport;
  coordinates come from the offline `airportsdata` package. An unknown code gives 422.
- Both clients: 10 s timeout, 2 retries with backoff on network errors and 5xx.
  A 429 (rate limit) is not retried; it becomes a 503 straight away.
- adsb.lol skips aircraft without a position and non-ICAO addresses (hex starting
  with `~`); `alt_baro: "ground"` becomes `altitude: null`.
- **Cache:** `/aircraft` reuses the latest stored fetch for a watch if it is younger
  than `FLIGHT_CACHE_SECONDS` (default 15; the old name `OPENSKY_CACHE_SECONDS` still
  works). The cache is the `aircraft_snapshots` table, so all pods share it. A fetch
  that found no aircraft stores nothing and is therefore not cached.
- **Mind the adsb.lol budget:** one watch refreshed every 15 s is 240 requests/hour,
  so 500/day goes quickly. On GKE the Deployment sets `FLIGHT_CACHE_SECONDS=60`.

## Team and work split

| Area | Owner |
|---|---|
| REST API, validation, status codes | Sude |
| Cloud database (Cloud SQL PostgreSQL) | Sude |
| Flight data clients (adsb.lol, OpenSky) and caching | Enes |
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
gcloud iam service-accounts add-iam-policy-binding \
  flightwatch-sql@dsai523-510708.iam.gserviceaccount.com \
  --role=roles/iam.workloadIdentityUser \
  --member="serviceAccount:dsai523-510708.svc.id.goog[default/flightwatch]"

# 4. Secrets (never commit these)
#    (add --from-literal=ADSBLOL_API_KEY='...' if we get an adsb.lol key)
kubectl create secret generic flightwatch-secrets \
  --from-literal=INSTANCE_CONNECTION_NAME='dsai523-510708:europe-west1:flightwatch-db' \
  --from-literal=DB_USER='flightwatch' --from-literal=DB_PASSWORD='...' --from-literal=DB_NAME='flightwatch'

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

### Resolved: OpenSky blocks GCP (tested 2026-11-20)

**Decision (PR #2 review):** use adsb.lol as the default source (`FLIGHT_SOURCE=adsblol`);
Sude tested the alternatives from GCP and only adsb.lol returned real data. If adsb.lol
causes problems, fall back to option 1 below (collector outside GCP).

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

Options that were considered:

1. **Collector outside GCP** (proposed): a small script on a non-cloud machine calls
   OpenSky for the active watches every N seconds and writes `aircraft_snapshots`
   into Cloud SQL; GKE pods only read the database. Reuses `opensky.get_states()`.
   Data is only fresh while the collector runs.
2. **Ask OpenSky** for academic access from GCP (can run in parallel).
3. **Another ADS-B source** (e.g. adsb.lol, airplanes.live, adsb.fi) behind
   `get_states()`. **Chosen: adsb.lol** (`app/adsblol.py`).

## Load test (Locust)

```bash
pip install -r loadtest/requirements.txt
locust -f loadtest/locustfile.py --host http://EXTERNAL_IP \
  --headless -u 100 -r 10 -t 5m --csv results/run1
```

All simulated users share three watches created at test start, so the flight data
source is called at most once per watch per cache window, however many users there
are. With `FLIGHT_CACHE_SECONDS=60` a 10-minute test uses about 30 adsb.lol requests.

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
(`.env` locally, a Kubernetes Secret on GKE). Same for API keys (adsb.lol, OpenSky).

## Release checklist (before every deploy)

1. Tests pass: `.venv/bin/pytest -q`
2. No secrets in the code or in git (`git diff` / `git log -p` show no passwords or tokens)
3. Right database: after deploy, `GET /health` shows `"database": "postgresql"`
4. Cost: when the work session ends, stop Cloud SQL and scale the GKE deployment to 0

Workflow: one branch per feature, open a pull request, the other person reviews.
Run `.venv/bin/pytest -q` before pushing.
