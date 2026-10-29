"""Start the app:  python run.py   then open http://127.0.0.1:5000

On startup the latest 2026 polls are downloaded (if FIFTYPLUSONE_API_KEY is
set) and the database is built or refreshed as needed.
"""
import os

from app.server import app
from app.updater import ensure_database

if __name__ == "__main__":
    # In debug mode Flask re-runs this file in a child process; only update once.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        ensure_database()
    app.run(debug=True, port=5000)
