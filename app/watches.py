"""REST endpoints for /watches (create, list, read, update, delete).

Status code rules:
  400 - body is not JSON, or a required field is missing
  422 - fields are present but a value is invalid (e.g. latitude 95)
  404 - no watch with that id
"""
import re

from flask import Blueprint, jsonify, request

from .models import Watch, db

bp = Blueprint("watches", __name__, url_prefix="/watches")

KINDS = ("airport", "bbox", "aircraft")
AIRPORT_RE = re.compile(r"^[A-Z]{4}$")
ICAO24_RE = re.compile(r"^[0-9a-f]{6}$")


def error(message, code):
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
    fields, err = validate(request.get_json(silent=True))
    if err:
        return error(*err)
    watch = Watch(**fields)
    db.session.add(watch)
    db.session.commit()
    return jsonify(watch.to_dict()), 201, {"Location": f"/watches/{watch.id}"}


@bp.get("")
def list_watches():
    watches = Watch.query.order_by(Watch.id).all()
    return jsonify([w.to_dict() for w in watches]), 200


@bp.get("/<int:watch_id>")
def get_watch(watch_id):
    watch = db.session.get(Watch, watch_id)
    if watch is None:
        return error("watch not found", 404)
    return jsonify(watch.to_dict()), 200


@bp.put("/<int:watch_id>")
def update_watch(watch_id):
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
    watch = db.session.get(Watch, watch_id)
    if watch is None:
        return error("watch not found", 404)
    db.session.delete(watch)
    db.session.commit()
    return "", 204
