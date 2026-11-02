"""Tests for /watches CRUD. Run with: pytest"""
import pytest

from app import create_app


@pytest.fixture
def client():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    return app.test_client()


def test_crud_flow(client):
    r = client.post("/watches", json={"name": "Istanbul Airport", "kind": "airport", "airport": "ltfm"})
    assert r.status_code == 201
    wid = r.get_json()["id"]
    assert r.get_json()["airport"] == "LTFM"

    assert client.get(f"/watches/{wid}").status_code == 200
    assert len(client.get("/watches").get_json()) == 1

    r = client.put(f"/watches/{wid}", json={"name": "THY plane", "kind": "aircraft", "icao24": "4BAA4E"})
    assert r.status_code == 200 and r.get_json()["icao24"] == "4baa4e"

    assert client.delete(f"/watches/{wid}").status_code == 204
    assert client.get(f"/watches/{wid}").status_code == 404


def test_bbox_ok(client):
    r = client.post("/watches", json={"name": "Marmara", "kind": "bbox",
                                      "bbox": {"lamin": 40.0, "lomin": 26.0, "lamax": 41.5, "lomax": 30.0}})
    assert r.status_code == 201


@pytest.mark.parametrize("body,code", [
    (None, 400),                                                  # no JSON
    ({"kind": "airport", "airport": "LTFM"}, 400),                # missing name
    ({"name": "x", "kind": "airport"}, 400),                      # missing airport
    ({"name": "x", "kind": "ship"}, 422),                         # bad kind
    ({"name": "x", "kind": "airport", "airport": "IST"}, 422),    # 3 letters
    ({"name": "x", "kind": "aircraft", "icao24": "zzzzzz"}, 422), # not hex
    ({"name": "x", "kind": "bbox",
      "bbox": {"lamin": 95, "lomin": 26, "lamax": 96, "lomax": 30}}, 422),  # lat > 90
])
def test_validation(client, body, code):
    r = client.post("/watches", json=body) if body is not None else client.post("/watches", data="nope")
    assert r.status_code == code


def test_unknown_watch(client):
    assert client.put("/watches/999", json={"name": "x", "kind": "airport", "airport": "LTFM"}).status_code == 404
    assert client.delete("/watches/999").status_code == 404
