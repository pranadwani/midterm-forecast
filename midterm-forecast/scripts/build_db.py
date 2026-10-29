"""Rebuild data/forecast.db from the CSV files in data/raw/.

    python scripts/build_db.py

You normally don't need this: python run.py builds the database for you.
Historical 538 polls (2018-2022) are always loaded. If FIFTYPLUSONE_API_KEY is
set, the latest 2026 polls are downloaded first.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.updater import build_database, fetch_polls  # noqa: E402


def main():
    api_key = os.environ.get("FIFTYPLUSONE_API_KEY", "").strip()
    if api_key:
        fetch_polls(api_key, max_age_hours=0)   # explicit rebuild: always fetch fresh
    build_database()


if __name__ == "__main__":
    main()
