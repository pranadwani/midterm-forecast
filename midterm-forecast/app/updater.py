"""Keep the data current so nobody has to download files by hand.

run.py calls ensure_database() on startup:
  * If FIFTYPLUSONE_API_KEY is set and the 2026 poll files in data/raw/ are
    missing or older than MAX_AGE_HOURS, fresh copies are downloaded from the
    FiftyPlusOne API.
  * If the database is missing, or any file in data/raw/ is newer than it,
    the database is rebuilt.
Without a key, or if a download fails, the app keeps using whatever is
already on disk (the bundled 2018-2022 polls always work).
"""
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .db import DB_PATH, connect, init_db
from .importers import import_538_polls, import_fiftyplusone_polls, load_pollster_ratings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "data", "raw")

API_URL = "https://fiftyplusone.news/api/csv"
POLL_FILES = ("senate_general.csv", "governor_general.csv")
REQUIRED_COLUMNS = {"question_id", "pollster", "state", "candidate_name", "party", "pct"}
MAX_AGE_HOURS = 6


def _is_stale(path: str, max_age_hours: float) -> bool:
    return not os.path.exists(path) or time.time() - os.path.getmtime(path) > max_age_hours * 3600


def _looks_like_poll_csv(body: bytes) -> bool:
    header = body.split(b"\n", 1)[0].decode("utf-8-sig", errors="replace").strip()
    return REQUIRED_COLUMNS <= {c.strip() for c in header.split(",")}


def fetch_polls(api_key: str, raw_dir: str = RAW_DIR, max_age_hours: float = MAX_AGE_HOURS) -> list:
    """Download any stale 2026 poll file. Returns the names of files updated."""
    updated = []
    for name in POLL_FILES:
        path = os.path.join(raw_dir, name)
        if not _is_stale(path, max_age_hours):
            continue
        # The URL contains the API key, so it is never printed.
        url = API_URL + "?" + urllib.parse.urlencode({"api_key": api_key, "filename": name})
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                body = resp.read()
        except urllib.error.HTTPError as err:
            if err.code == 401:
                print("FiftyPlusOne rejected the API key - check FIFTYPLUSONE_API_KEY.")
                break
            print(f"Could not download {name} (HTTP {err.code}); using the copy on disk.")
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            print(f"Could not download {name} ({getattr(err, 'reason', err)}); using the copy on disk.")
            continue

        if not _looks_like_poll_csv(body):
            print(f"Download of {name} doesn't look like a poll file; using the copy on disk.")
            continue
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(body)
        os.replace(tmp, path)                   # never leave a half-written file behind
        updated.append(name)
        print(f"Downloaded latest {name}")
    return updated


def build_database(raw_dir: str = RAW_DIR, db_path: str = DB_PATH) -> None:
    """Rebuild the database from scratch from the CSV files in raw_dir."""
    tmp = db_path + ".tmp"                       # build aside, then swap in
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = connect(tmp)
    init_db(conn)

    ratings = load_pollster_ratings(conn, os.path.join(raw_dir, "538_pollster_ratings.csv"))
    print(f"Pollster ratings loaded: {len(ratings)}")

    n = import_538_polls(conn, os.path.join(raw_dir, "538_raw_polls_2018_2022.csv"), ratings)
    print(f"Historical polls (2018-2022): {n}")

    for name in POLL_FILES:
        path = os.path.join(raw_dir, name)
        if os.path.exists(path):
            n = import_fiftyplusone_polls(conn, path, cycle=2026)
            print(f"2026 polls from {name}: {n}")
        else:
            print(f"(no {name} yet - set FIFTYPLUSONE_API_KEY to download it, see README)")

    conn.close()
    os.replace(tmp, db_path)
    print(f"Database ready: {db_path}")


def needs_rebuild(raw_dir: str = RAW_DIR, db_path: str = DB_PATH) -> bool:
    if not os.path.exists(db_path):
        return True
    built = os.path.getmtime(db_path)
    return any(os.path.getmtime(os.path.join(raw_dir, f)) > built
               for f in os.listdir(raw_dir) if f.endswith(".csv"))


def ensure_database() -> None:
    """Fetch the latest polls if possible, then rebuild the database if needed."""
    api_key = os.environ.get("FIFTYPLUSONE_API_KEY", "").strip()
    if api_key:
        fetch_polls(api_key)
    else:
        print("FIFTYPLUSONE_API_KEY not set - skipping the 2026 poll download.")
    if needs_rebuild():
        build_database()
