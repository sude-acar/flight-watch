"""Database tables (SQLAlchemy models).

watches            - what a user wants to monitor (airport, area or one aircraft)
aircraft_snapshots - aircraft positions fetched from OpenSky for a watch
"""
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


class Watch(db.Model):
    """A saved watch. Which fields are filled depends on `kind`:
    - "airport": airport (4-letter ICAO airport code, e.g. LTFM)
    - "bbox":    lamin, lomin, lamax, lomax (area corners in degrees)
    - "aircraft": icao24 (6 hex chars, the aircraft's transponder ID)
    """
    __tablename__ = "watches"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    kind = db.Column(db.String(10), nullable=False)
    airport = db.Column(db.String(4))
    icao24 = db.Column(db.String(6))
    lamin = db.Column(db.Float)
    lomin = db.Column(db.Float)
    lamax = db.Column(db.Float)
    lomax = db.Column(db.Float)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    snapshots = db.relationship("AircraftSnapshot", backref="watch",
                                cascade="all, delete-orphan")

    def to_dict(self):
        """JSON shape returned by the API."""
        data = {"id": self.id, "name": self.name, "kind": self.kind,
                "created_at": self.created_at.isoformat() if self.created_at else None}
        if self.kind == "airport":
            data["airport"] = self.airport
        elif self.kind == "aircraft":
            data["icao24"] = self.icao24
        else:
            data["bbox"] = {"lamin": self.lamin, "lomin": self.lomin,
                            "lamax": self.lamax, "lomax": self.lomax}
        return data


class AircraftSnapshot(db.Model):
    """One aircraft position seen at one moment for a watch.
    Field names match the sample JSON agreed with the OpenSky client.
    """
    __tablename__ = "aircraft_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    watch_id = db.Column(db.Integer, db.ForeignKey("watches.id"), nullable=False, index=True)
    icao24 = db.Column(db.String(6), nullable=False)
    callsign = db.Column(db.String(10))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    altitude = db.Column(db.Float)
    velocity = db.Column(db.Float)
    fetched_at = db.Column(db.DateTime(timezone=True), default=utcnow, index=True)

    def to_dict(self):
        return {"icao24": self.icao24, "callsign": self.callsign, "lat": self.lat,
                "lon": self.lon, "altitude": self.altitude, "velocity": self.velocity,
                "time": self.fetched_at.isoformat() if self.fetched_at else None}
