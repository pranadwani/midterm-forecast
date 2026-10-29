"""Keep the data current so nobody has to download files by hand.

run.py calls ensure_database() on startup:
  * If FIFTYPLUSONE_API_KEY is set and the 2026 poll files in data/raw/ are
    missing or older than MAX_AGE_HOURS, fresh copies are downloaded from the
    FiftyPlusOne API.
  * Otherwise the free VoteHub poll feed is downloaded instead (no key needed).
  * If the database is missing, or any source file is newer than it, the
    database is rebuilt.
If a download fails, the app keeps using whatever is already on disk (the
bundled 2018-2022 polls always work).
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from .db import DB_PATH, connect, init_db
from .forecast import load_seats
from .importers import (import_538_polls, import_fiftyplusone_polls, import_votehub_polls,
                        load_pollster_ratings)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(ROOT, "data", "raw")
CANDIDATES_PATH = os.path.join(ROOT, "data", "candidates_2026.json")

API_URL = "https://fiftyplusone.news/api/csv"
POLL_FILES = ("senate_general.csv", "governor_general.csv")
REQUIRED_COLUMNS = {"question_id", "pollster", "state", "candidate_name", "party", "pct"}

VOTEHUB_URL = "https://api.votehub.com/polls"
VOTEHUB_FILE = "votehub_polls.json"
VOTEHUB_TYPES = ("us-senator", "governor")

MAX_AGE_HOURS = 6


def _is_stale(path: str, max_age_hours: float) -> bool:
    return not os.path.exists(path) or time.time() - os.path.getmtime(path) > max_age_hours * 3600


def _looks_like_poll_csv(body: bytes) -> bool:
    header = body.split(b"\n", 1)[0].decode("utf-8-sig", errors="replace").strip()
    return REQUIRED_COLUMNS <= {c.strip() for c in header.split(",")}


def _download(url: str, name: str):
    """Return the response body, or None (with a message) if the download failed.
    A 401 (bad API key) is raised so the caller can stop trying."""
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        if err.code == 401:
            raise
        print(f"Could not download {name} (HTTP {err.code}); using the copy on disk.")
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        print(f"Could not download {name} ({getattr(err, 'reason', err)}); using the copy on disk.")
    return None


def _save(path: str, body: bytes) -> None:
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, path)                       # never leave a half-written file behind


def fetch_polls(api_key: str, raw_dir: str = RAW_DIR, max_age_hours: float = MAX_AGE_HOURS) -> list:
    """Download any stale FiftyPlusOne poll file. Returns the names of files updated."""
    updated = []
    for name in POLL_FILES:
        path = os.path.join(raw_dir, name)
        if not _is_stale(path, max_age_hours):
            continue
        # The URL contains the API key, so it is never printed.
        url = API_URL + "?" + urllib.parse.urlencode({"api_key": api_key, "filename": name})
        try:
            body = _download(url, name)
        except urllib.error.HTTPError:
            print("FiftyPlusOne rejected the API key - check FIFTYPLUSONE_API_KEY.")
            break
        if body is None:
            continue
        if not _looks_like_poll_csv(body):
            print(f"Download of {name} doesn't look like a poll file; using the copy on disk.")
            continue
        _save(path, body)
        updated.append(name)
        print(f"Downloaded latest {name}")
    return updated


def fetch_votehub(raw_dir: str = RAW_DIR, max_age_hours: float = MAX_AGE_HOURS) -> bool:
    """Download the free VoteHub polls if the local copy is stale. Returns True if updated."""
    path = os.path.join(raw_dir, VOTEHUB_FILE)
    if not _is_stale(path, max_age_hours):
        return False
    try:
        body = _download(VOTEHUB_URL, "VoteHub polls")
    except urllib.error.HTTPError as err:
        print(f"Could not download VoteHub polls (HTTP {err.code}); using the copy on disk.")
        return False
    try:
        polls = json.loads(body) if body is not None else None
    except ValueError:
        polls = None
    if not isinstance(polls, list):
        if body is not None:
            print("VoteHub download doesn't look like a poll list; using the copy on disk.")
        return False
    keep = [p for p in polls if isinstance(p, dict) and p.get("poll_type") in VOTEHUB_TYPES]
    _save(path, json.dumps(keep).encode("utf-8"))
    print(f"Downloaded latest VoteHub polls ({len(keep)} Senate and governor polls)")
    return True


def _has_fiftyplusone(raw_dir: str) -> bool:
    return any(os.path.exists(os.path.join(raw_dir, name)) for name in POLL_FILES)


def build_database(raw_dir: str = RAW_DIR, db_path: str = DB_PATH,
                   candidates_path: str = CANDIDATES_PATH) -> None:
    """Rebuild the database from scratch from the files in raw_dir."""
    tmp = db_path + ".tmp"                       # build aside, then swap in
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = connect(tmp)
    init_db(conn)

    ratings = load_pollster_ratings(conn, os.path.join(raw_dir, "538_pollster_ratings.csv"))
    print(f"Pollster ratings loaded: {len(ratings)}")

    n = import_538_polls(conn, os.path.join(raw_dir, "538_raw_polls_2018_2022.csv"), ratings)
    print(f"Historical polls (2018-2022): {n}")

    votehub_path = os.path.join(raw_dir, VOTEHUB_FILE)
    if _has_fiftyplusone(raw_dir):               # the paid source wins when it's there
        for name in POLL_FILES:
            path = os.path.join(raw_dir, name)
            if os.path.exists(path):
                n = import_fiftyplusone_polls(conn, path, cycle=2026)
                print(f"2026 polls from {name}: {n}")
    elif os.path.exists(votehub_path):
        with open(candidates_path, encoding="utf-8") as f:
            candidates = json.load(f)
        seats_up = load_seats().get("2026", {}).get("seats_up", {})
        special = {k.split("-")[0] for k in seats_up if k.endswith("-special")} - set(seats_up)
        n, unknown = import_votehub_polls(conn, votehub_path, candidates, special)
        print(f"2026 polls from VoteHub: {n}")
        for race in unknown:
            print(f"  skipped {race} - party unknown, add to data/candidates_2026.json")
    else:
        print("(no 2026 polls yet - they download automatically when you're online)")

    conn.close()
    os.replace(tmp, db_path)
    print(f"Database ready: {db_path}")


def needs_rebuild(raw_dir: str = RAW_DIR, db_path: str = DB_PATH,
                  candidates_path: str = CANDIDATES_PATH) -> bool:
    if not os.path.exists(db_path):
        return True
    built = os.path.getmtime(db_path)
    sources = [os.path.join(raw_dir, f) for f in os.listdir(raw_dir) if f.endswith((".csv", ".json"))]
    if os.path.exists(candidates_path):
        sources.append(candidates_path)
    return any(os.path.getmtime(path) > built for path in sources)


def ensure_database() -> None:
    """Fetch the latest polls if possible, then rebuild the database if needed."""
    api_key = os.environ.get("FIFTYPLUSONE_API_KEY", "").strip()
    if api_key:
        fetch_polls(api_key)
    if not _has_fiftyplusone(RAW_DIR):
        fetch_votehub()
    if needs_rebuild():
        build_database()
