"""Build data/forecast.db from the CSV files in data/raw/.

    python scripts/build_db.py

Historical 538 polls (2018-2022) are always loaded. To add current 2026 polls,
download senate_general.csv and/or governor_general.csv from
https://fiftyplusone.news/downloads into data/raw/ and run this again.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.db import DB_PATH, connect, init_db  # noqa: E402
from app.importers import (import_538_polls, import_fiftyplusone_polls,  # noqa: E402
                           load_pollster_ratings)

RAW = os.path.join(ROOT, "data", "raw")


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)                      # always rebuild from scratch
    conn = connect()
    init_db(conn)

    ratings = load_pollster_ratings(conn, os.path.join(RAW, "538_pollster_ratings.csv"))
    print(f"Pollster ratings loaded: {len(ratings)}")

    n = import_538_polls(conn, os.path.join(RAW, "538_raw_polls_2018_2022.csv"), ratings)
    print(f"Historical polls (2018-2022): {n}")

    for name in ("senate_general.csv", "governor_general.csv"):
        path = os.path.join(RAW, name)
        if os.path.exists(path):
            n = import_fiftyplusone_polls(conn, path, cycle=2026)
            print(f"2026 polls from {name}: {n}")
        else:
            print(f"(skipped {name}: not found in data/raw - see README)")

    conn.close()
    print(f"Database ready: {DB_PATH}")


if __name__ == "__main__":
    main()
