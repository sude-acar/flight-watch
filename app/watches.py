"""REST endpoints for /watches (create, list, read, update, delete)
plus live aircraft and stored history for a watch.

Status code rules:
  400 - body is not JSON, or a required field is missing
  422 - fields are present but a value is invalid (e.g. latitude 95)
  404 - no watch with that id
  503 - OpenSky is unavailable
"""
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func

from . import opensky
from .models import AircraftSnapshot, Watch, db, utcnow

bp = Blueprint("watches", __name__, url_prefix="/watches")

KINDS = ("airport", "bbox", "aircraft")
AIRPORT_RE = re.compile(r"^[A-Z]{4}$")
ICAO24_RE = re.compile(r"^[0-9a-f]{6}$")


def error(message, code):
    """Build a JSON error response: {"error": message} with the given status code."""
    return jsonify(error=message), code


def validate(body):
    """Check a request body. Returns (clean_fields, None) or (None, (message, code))."""
    if not isinstance(body, dict):
        return None, ("request body must be a JSON object", 400)
    for field in ("name", "kind"):
        if field not in body:
            return None, (f"missing field: {field}", 400)

    name, kind = body["name"], body["kind"]
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        return None, ("name must be a non-empty string (max 100 chars)", 422)
    if kind not in KINDS:
        return None, (f"kind must be one of {', '.join(KINDS)}", 422)

    fields = {"name": name.strip(), "kind": kind, "airport": None, "icao24": None,
              "lamin": None, "lomin": None, "lamax": None, "lomax": None}

    if kind == "airport":
        if "airport" not in body:
            return None, ("missing field: airport", 400)
        code = str(body["airport"]).upper()
        if not AIRPORT_RE.match(code):
            return None, ("airport must be a 4-letter ICAO code, e.g. LTFM", 422)
        fields["airport"] = code

    elif kind == "aircraft":
        if "icao24" not in body:
            return None, ("missing field: icao24", 400)
        code = str(body["icao24"]).lower()
        if not ICAO24_RE.match(code):
            return None, ("icao24 must be 6 hex characters, e.g. 4baa4e", 422)
        fields["icao24"] = code

    else:  # bbox
        box = body.get("bbox")
        if not isinstance(box, dict):
            return None, ("missing field: bbox {lamin, lomin, lamax, lomax}", 400)
        for key in ("lamin", "lomin", "lamax", "lomax"):
            if key not in box:
                return None, (f"missing field: bbox.{key}", 400)
            if isinstance(box[key], bool) or not isinstance(box[key], (int, float)):
                return None, (f"bbox.{key} must be a number", 422)
            fields[key] = float(box[key])
        if not (-90 <= fields["lamin"] < fields["lamax"] <= 90):
            return None, ("latitudes must satisfy -90 <= lamin < lamax <= 90", 422)
        if not (-180 <= fields["lomin"] < fields["lomax"] <= 180):
            return None, ("longitudes must satisfy -180 <= lomin < lomax <= 180", 422)

    return fields, None


@bp.post("")
def create_watch():
    """POST /watches - validate the body and save a new watch (201)."""
    fields, err = validate(request.get_json(silent=True))
    if err:
        return error(*err)
    watch = Watch(**fields)
    db.session.add(watch)
    db.session.commit()
    return jsonify(watch.to_dict()), 201, {"Location": f"/watches/{watch.id}"}


@bp.get("")
def list_watches():
    """GET /watches - all watches, oldest first."""
    watches = Watch.query.order_by(Watch.id).all()
    return jsonify([w.to_dict() for w in watches]), 200


@bp.get("/<int:watch_id>")
def get_watch(watch_id):
    """GET /watches/<id> - one watch, or 404."""
    watch = db.session.get(Watch, watch_id)
    if watch is None:
        return error("watch not found", 404)
    return jsonify(watch.to_dict()), 200


@bp.put("/<int:watch_id>")
def update_watch(watch_id):
    """PUT /watches/<id> - replace a watch with a new valid body."""
    watch = db.session.get(Watch, watch_id)
    if watch is None:
        return error("watch not found", 404)
    fields, err = validate(request.get_json(silent=True))
    if err:
        return error(*err)
    for key, value in fields.items():
        setattr(watch, key, value)
    db.session.commit()
    return jsonify(watch.to_dict()), 200


@bp.delete("/<int:watch_id>")
def delete_watch(watch_id):
    """DELETE /watches/<id> - remove a watch and its snapshots (204)."""
    watch = db.session.get(Watch, watch_id)
    if watch is None:
        return error("watch not found", 404)
    # One DELETE for all snapshots; the ORM cascade would delete them row by row,
    # one Cloud SQL round trip each, which got slow once a watch had hundreds.
    AircraftSnapshot.query.filter_by(watch_id=watch.id).delete(synchronize_session=False)
    db.session.delete(watch)
    db.session.commit()
    return "", 204


def cached_snapshots(watch):
    """Snapshots of the latest fetch for `watch` if it is younger than
    OPENSKY_CACHE_SECONDS, else None. The cache lives in the database, so it is
    shared by every pod behind the load balancer.

    A fetch that returned no aircraft stores no rows, so it is not cached.
    """
    max_age = current_app.config["OPENSKY_CACHE_SECONDS"]
    if max_age <= 0:
        return None
    latest = (db.session.query(func.max(AircraftSnapshot.fetched_at))
              .filter(AircraftSnapshot.watch_id == watch.id).scalar())
    if latest is None:
        return None
    if isinstance(latest, str):          # SQLite returns MAX() of a datetime as text
        latest = datetime.fromisoformat(latest)
    aware = latest if latest.tzinfo else latest.replace(tzinfo=timezone.utc)
    if utcnow() - aware > timedelta(seconds=max_age):
        return None
    return (AircraftSnapshot.query
            .filter_by(watch_id=watch.id, fetched_at=latest).all())


@bp.get("/<int:watch_id>/aircraft")
def watch_aircraft(watch_id):
    """Live aircraft for a watch. Each OpenSky fetch is stored as snapshots;
    a repeat request within OPENSKY_CACHE_SECONDS is served from those
    snapshots instead (response header X-Cache: HIT / MISS)."""
    watch = db.session.get(Watch, watch_id)
    if watch is None:
        return error("watch not found", 404)

    cached = cached_snapshots(watch)
    if cached is not None:
        return jsonify([s.to_dict() for s in cached]), 200, {"X-Cache": "HIT"}

    try:
        states = opensky.get_states(watch)
    except opensky.UnknownAirportError as exc:
        return error(str(exc), 422)
    except opensky.OpenSkyError as exc:
        return error(f"OpenSky unavailable: {exc}", 503)

    now = utcnow()   # one timestamp per fetch, so the batch can be found again
    snapshots = [AircraftSnapshot(watch_id=watch.id, fetched_at=now, **s) for s in states]
    db.session.add_all(snapshots)
    db.session.commit()
    return jsonify([s.to_dict() for s in snapshots]), 200, {"X-Cache": "MISS"}


@bp.get("/<int:watch_id>/history")
def watch_history(watch_id):
    """Stored snapshots for a watch, newest first. `?limit=` caps the count (default 100, max 1000)."""
    watch = db.session.get(Watch, watch_id)
    if watch is None:
        return error("watch not found", 404)
    limit = request.args.get("limit", 100, type=int)
    if limit is None or not 1 <= limit <= 1000:
        return error("limit must be between 1 and 1000", 422)
    rows = (AircraftSnapshot.query.filter_by(watch_id=watch.id)
            .order_by(AircraftSnapshot.fetched_at.desc()).limit(limit).all())
    return jsonify([r.to_dict() for r in rows]), 200
