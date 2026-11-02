"""Local entry point: `python run.py` starts the dev server on port 5001
(5000 is taken by AirPlay on macOS)."""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5001)
