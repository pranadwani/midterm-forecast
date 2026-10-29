"""Production entry point (used on Railway):  gunicorn wsgi:app

The latest polls are downloaded and the database built before the server
starts taking requests, then refreshed every MAX_AGE_HOURS in the background
so a long-running server never goes stale. Each request opens the database
fresh, so it picks up a rebuilt file automatically.
"""
import threading
import time

from app.server import app
from app.updater import MAX_AGE_HOURS, ensure_database

ensure_database()


def _refresh_forever():
    while True:
        time.sleep(MAX_AGE_HOURS * 3600)
        try:
            ensure_database()
        except Exception as err:          # keep serving the last good data
            print(f"Data refresh failed: {err}")


threading.Thread(target=_refresh_forever, daemon=True).start()
