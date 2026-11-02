"""Flight Watch application factory.

Creates the Flask app, loads config from environment variables (.env),
connects the database and registers the API blueprints.
"""
import os

from dotenv import load_dotenv
from flask import Flask, jsonify

from .models import db


def create_app(config=None):
    """Build and return a configured Flask app.

    `config` lets tests override settings (e.g. an in-memory database).
    """
    load_dotenv()
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///flightwatch.db")
    if config:
        app.config.update(config)

    db.init_app(app)

    from .watches import bp as watches_bp
    app.register_blueprint(watches_bp)

    # Return JSON (not HTML) for errors Flask raises itself, e.g. unknown URLs.
    @app.errorhandler(404)
    def not_found(_):
        return jsonify(error="not found"), 404

    @app.errorhandler(405)
    def method_not_allowed(_):
        return jsonify(error="method not allowed"), 405

    with app.app_context():
        # Creates tables on first start; enough for our two-table schema.
        db.create_all()

    return app
