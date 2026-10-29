"""Database setup: one SQLite file holds every poll, race and pollster.

Why SQLite? It's a real SQL database that lives in a single file, so the
project runs anywhere with no database server to install.
"""
import os
import sqlite3

DB_PATH = os.environ.get(
    "FORECAST_DB",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "forecast.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pollsters (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    grade         REAL,          -- 538 numeric grade, 0.5 (worst) to 3.0 (best); NULL = unrated
    rank          INTEGER        -- 538 rank (1 = best); NULL = unrated
);

CREATE TABLE IF NOT EXISTS races (
    id             TEXT PRIMARY KEY,   -- e.g. "2022_Sen-G_PA"
    cycle          INTEGER NOT NULL,   -- election year
    office         TEXT NOT NULL,      -- 'senate' or 'governor'
    state          TEXT NOT NULL,      -- full state name, e.g. "Pennsylvania"
    state_abbrev   TEXT NOT NULL,      -- "PA"
    stage          TEXT NOT NULL,      -- 'general', 'special' or 'runoff'
    election_date  TEXT NOT NULL,      -- ISO date
    actual_winner_party TEXT,          -- filled in only once results are known
    actual_margin  REAL                -- cand_a minus cand_b, in points (NULL until known)
);

-- One row per poll question. Every poll is stored as a head-to-head between
-- the race's two main candidates ("A" and "B"), so all polls of a race line up.
CREATE TABLE IF NOT EXISTS polls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,       -- '538', 'fiftyplusone' or 'votehub'
    source_id     TEXT NOT NULL,       -- question id in the source file
    race_id       TEXT NOT NULL REFERENCES races(id),
    pollster_id   INTEGER NOT NULL REFERENCES pollsters(id),
    end_date      TEXT NOT NULL,       -- last day the poll was in the field
    sample_size   INTEGER,
    population    TEXT,                -- 'lv', 'rv', 'a' or 'v'
    internal      INTEGER NOT NULL DEFAULT 0,   -- 1 = paid for by a campaign/party
    partisan      TEXT,                -- party code if the sponsor leans to a party
    cand_a_name   TEXT NOT NULL,
    cand_a_party  TEXT NOT NULL,
    cand_a_pct    REAL NOT NULL,
    cand_b_name   TEXT NOT NULL,
    cand_b_party  TEXT NOT NULL,
    cand_b_pct    REAL NOT NULL,
    UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_polls_race ON polls(race_id);
CREATE INDEX IF NOT EXISTS idx_races_cycle_office ON races(cycle, office);
"""


def connect(path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def get_or_create_pollster(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM pollsters WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO pollsters (name) VALUES (?)", (name,))
    return cur.lastrowid
