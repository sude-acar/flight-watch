"""Locust load test for Flight Watch.

Run (web UI on http://localhost:8089):
    pip install -r loadtest/requirements.txt
    locust -f loadtest/locustfile.py --host http://EXTERNAL_IP

Headless example (100 users, 10 new per second, 5 minutes, CSV results):
    locust -f loadtest/locustfile.py --host http://EXTERNAL_IP \
        --headless -u 100 -r 10 -t 5m --csv results/run1

The flight data source has a daily limit (adsb.lol: 500 requests/day per IP
without a key), so all simulated users share a few watches created once at test
start. With the cache (FLIGHT_CACHE_SECONDS) each shared watch calls the source
at most once per cache window, no matter how many users there are - the load
lands on our pods and database, not on adsb.lol.
Write traffic (create/update/delete) uses short-lived watches that never
call /aircraft.
"""
import random

import requests
from locust import HttpUser, between, events, task

SHARED = [
    {"name": "load LTFM", "kind": "airport", "airport": "LTFM"},
    {"name": "load LTFJ", "kind": "airport", "airport": "LTFJ"},
    {"name": "load Marmara", "kind": "bbox",
     "bbox": {"lamin": 40.0, "lomin": 26.0, "lamax": 41.5, "lomax": 30.0}},
]
shared_ids = []


@events.test_start.add_listener
def create_shared_watches(environment, **_):
    for body in SHARED:
        r = requests.post(f"{environment.host}/watches", json=body, timeout=10)
        r.raise_for_status()
        shared_ids.append(r.json()["id"])


@events.test_stop.add_listener
def delete_shared_watches(environment, **_):
    for wid in shared_ids:
        requests.delete(f"{environment.host}/watches/{wid}", timeout=10)
    shared_ids.clear()


class WatchUser(HttpUser):
    wait_time = between(0.5, 2)

    @task(5)
    def aircraft(self):
        if not shared_ids:
            return
        wid = random.choice(shared_ids)
        with self.client.get(f"/watches/{wid}/aircraft", name="/watches/{id}/aircraft",
                             catch_response=True) as r:
            if r.status_code == 503:   # flight data source refused: not our server failing
                r.failure("503 from flight data source")

    @task(3)
    def history(self):
        if shared_ids:
            wid = random.choice(shared_ids)
            self.client.get(f"/watches/{wid}/history?limit=50", name="/watches/{id}/history")

    @task(2)
    def list_watches(self):
        self.client.get("/watches")

    @task(1)
    def write_cycle(self):
        r = self.client.post("/watches", name="/watches [POST]",
                             json={"name": "tmp", "kind": "aircraft", "icao24": "4baa4e"})
        if r.status_code != 201:
            return
        wid = r.json()["id"]
        self.client.put(f"/watches/{wid}", name="/watches/{id} [PUT]",
                        json={"name": "tmp2", "kind": "aircraft", "icao24": "4bb1c2"})
        self.client.delete(f"/watches/{wid}", name="/watches/{id} [DELETE]")
