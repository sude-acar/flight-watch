"""Flight Watch application factory.

Creates the Flask app, loads config from environment variables (.env),
connects the database and registers the API blueprints.
"""
import os

from dotenv import load_dotenv
from flask import Flask, jsonify
from sqlalchemy import text

from .models import db


def create_app(config=None):
    """Build and return a configured Flask app.

    `config` lets tests override settings (e.g. an in-memory database).
    """
    load_dotenv()
    app = Flask(__name__)
    instance = os.getenv("INSTANCE_CONNECTION_NAME")
    # Tests pass their own database URI; it must win over Cloud SQL from .env.
    if instance and not (config and "SQLALCHEMY_DATABASE_URI" in config):
        # Cloud SQL: connect through the Cloud SQL Python Connector, which opens
        # an encrypted, IAM-authorised connection (no IP allow-list, no proxy).
        app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+pg8000://"
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"creator": _cloud_sql_creator(instance)}
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///flightwatch.db")
    # Reuse an OpenSky fetch for this many seconds (0 = no cache).
    app.config["OPENSKY_CACHE_SECONDS"] = int(os.getenv("OPENSKY_CACHE_SECONDS", "15"))
    if config:
        app.config.update(config)

    db.init_app(app)

    from .watches import bp as watches_bp
    app.register_blueprint(watches_bp)

    @app.get("/")
    def index():
        """Map page (app/static/index.html): a small UI on top of the API."""
        return app.send_static_file("index.html")

    # Liveness/readiness probe for Kubernetes; does not touch the database.
    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok"), 200

    @app.get("/health")
    def health():
        """Is the app up and can it reach its database?

        Checked after a deploy to see which database the app is connected to
        (sqlite locally, postgresql on Cloud SQL). Kubernetes probes use /healthz.
        """
        try:
            db.session.execute(text("SELECT 1"))
        except Exception:
            return jsonify(status="error", database=db.engine.dialect.name), 503
        return jsonify(status="ok", database=db.engine.dialect.name), 200

    # Return JSON (not HTML) for errors Flask raises itself, e.g. unknown URLs.
    @app.errorhandler(404)
    def not_found(_):
        """Unknown URL: answer with JSON instead of Flask's HTML page."""
        return jsonify(error="not found"), 404

    @app.errorhandler(405)
    def method_not_allowed(_):
        """Known URL but wrong HTTP method, e.g. PATCH /watches."""
        return jsonify(error="method not allowed"), 405

    with app.app_context():
        # Creates tables on first start; enough for our two-table schema.
        db.create_all()

    return app


def _cloud_sql_creator(instance):
    """Return a function that opens a new Cloud SQL connection (used by SQLAlchemy's pool)."""
    from google.cloud.sql.connector import Connector

    connector = Connector()

    def connect():
        """Open one connection to the Cloud SQL instance."""
        return connector.connect(
            instance, "pg8000",
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            db=os.environ["DB_NAME"],
        )
    return connect
