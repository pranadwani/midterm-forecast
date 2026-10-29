"""Tests for loading CSV files into SQLite and for the API.

The FiftyPlusOne test uses a tiny made-up file (fictional candidates and
pollsters) written in the same column format as the real download.
"""
import csv
import json
import os

import pytest

from app import db as dbmod
from app.forecast import build_forecasts
from app.importers import import_fiftyplusone_polls, import_votehub_polls, normalize_name

FIELDS = ["poll_id", "question_id", "pollster", "state", "end_date", "sample_size", "population",
          "internal", "partisan", "race_id", "cycle", "office_type", "seat_name", "election_date",
          "stage", "hypothetical", "party", "answer", "candidate_id", "candidate_name", "pct"]


def row(q, pollster, state, end, cand, party, pct, race="900", seat="Class II", **extra):
    base = {"poll_id": q, "question_id": q, "pollster": pollster, "state": state, "end_date": end,
            "sample_size": "800", "population": "lv", "internal": "false", "partisan": "",
            "race_id": race, "cycle": "2026", "office_type": "U.S. Senate", "seat_name": seat,
            "election_date": "2026-11-03", "stage": "general", "hypothetical": "false",
            "party": party, "answer": cand.split()[-1], "candidate_id": cand, "candidate_name": cand,
            "pct": pct}
    base.update(extra)
    return base


@pytest.fixture
def conn(tmp_path):
    c = dbmod.connect(str(tmp_path / "test.db"))
    dbmod.init_db(c)
    yield c
    c.close()


def write_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def test_fiftyplusone_rows_become_head_to_head_polls(conn, tmp_path):
    path = tmp_path / "senate_general.csv"
    write_csv(path, [
        # poll 1: both candidates + a minor candidate
        row("1", "Test Poll Co", "Ohio", "2026-10-20", "Alex Rivera", "DEM", "47"),
        row("1", "Test Poll Co", "Ohio", "2026-10-20", "Jordan Blake", "REP", "45"),
        row("1", "Test Poll Co", "Ohio", "2026-10-20", "Sam Minor", "LIB", "3"),
        # poll 2: Republican listed first in the file - order shouldn't matter
        row("2", "Other Research", "Ohio", "2026-10-25", "Jordan Blake", "REP", "46"),
        row("2", "Other Research", "Ohio", "2026-10-25", "Alex Rivera", "DEM", "44"),
        # hypothetical matchup - must be skipped
        row("3", "Test Poll Co", "Ohio", "2026-10-26", "Alex Rivera", "DEM", "50", hypothetical="true"),
        row("3", "Test Poll Co", "Ohio", "2026-10-26", "Pat Other", "REP", "40", hypothetical="true"),
    ])
    assert import_fiftyplusone_polls(conn, str(path)) == 2

    polls = conn.execute("SELECT * FROM polls ORDER BY source_id").fetchall()
    assert all(p["cand_a_party"] == "DEM" for p in polls)        # Democrat is always candidate A
    assert [p["cand_a_pct"] - p["cand_b_pct"] for p in polls] == [2, -2]

    race = conn.execute("SELECT * FROM races").fetchone()
    assert (race["state_abbrev"], race["office"], race["stage"]) == ("OH", "senate", "general")


def test_special_election_detected_from_seat_class(conn, tmp_path):
    path = tmp_path / "senate_general.csv"
    write_csv(path, [
        row("1", "Test Poll Co", "Florida", "2026-10-20", "A One", "DEM", "45", race="901", seat="Class III"),
        row("1", "Test Poll Co", "Florida", "2026-10-20", "B Two", "REP", "48", race="901", seat="Class III"),
    ])
    import_fiftyplusone_polls(conn, str(path))
    assert conn.execute("SELECT stage FROM races").fetchone()["stage"] == "special"


def test_forecast_filters_by_pollster(conn, tmp_path):
    path = tmp_path / "senate_general.csv"
    write_csv(path, [
        row("1", "Test Poll Co", "Ohio", "2026-10-20", "Alex Rivera", "DEM", "50"),
        row("1", "Test Poll Co", "Ohio", "2026-10-20", "Jordan Blake", "REP", "40"),
        row("2", "Other Research", "Ohio", "2026-10-25", "Alex Rivera", "DEM", "40"),
        row("2", "Other Research", "Ohio", "2026-10-25", "Jordan Blake", "REP", "50"),
    ])
    import_fiftyplusone_polls(conn, str(path))
    test_co = conn.execute("SELECT id FROM pollsters WHERE name = 'Test Poll Co'").fetchone()["id"]

    only_one = build_forecasts(conn, 2026, "senate", pollster_ids=[test_co])
    assert only_one[0]["margin"] == pytest.approx(10)
    both = build_forecasts(conn, 2026, "senate")
    assert abs(both[0]["margin"]) < 10


def vh_poll(pid, end, answers, subject="2026 Ohio", poll_type="us-senator", **extra):
    base = {"id": pid, "poll_type": poll_type, "subject": subject, "end_date": end,
            "pollster": "Test Poll Co", "sample_size": 800, "population": "lv",
            "internal": False, "partisan": None,
            "answers": [{"choice": name, "pct": pct} for name, pct in answers]}
    base.update(extra)
    return base


VH_PARTIES = {"parties": {"Alex Rivera": "DEM", "Jordan Blake": "REP", "Casey Early": "DEM",
                          "Sam Minor": "LIB"},
              "aliases": {"Alex Rivra": "Alex Rivera"}}


def test_votehub_uses_recent_nominees(conn, tmp_path):
    path = tmp_path / "votehub_polls.json"
    path.write_text(json.dumps([
        # before the primary: a different Democrat was polled
        vh_poll("1", "2026-03-01", [("Casey Early", 44), ("Jordan Blake", 46)]),
        # after it: the nominee, with a minor candidate listed in every poll
        vh_poll("2", "2026-10-01", [("Jordan Blake", 45), ("Alex Rivera", 47), ("Sam Minor", 3)]),
        vh_poll("3", "2026-10-20", [("Alex Rivra", 48), ("Jordan Blake", 44), ("Sam Minor", 2)]),
        vh_poll("4", "2026-10-21", [("Jordan Blake", 49), ("Sam Minor", 4)]),
        # primary poll - must be skipped
        vh_poll("5", "2026-02-01", [("Alex Rivera", 50), ("Casey Early", 40)],
                subject="2026 Ohio Democratic"),
    ]))
    n, unknown = import_votehub_polls(conn, str(path), VH_PARTIES, special_states={"OH"})
    assert (n, unknown) == (2, [])
    polls = conn.execute("SELECT * FROM polls ORDER BY source_id").fetchall()
    assert [p["source_id"] for p in polls] == ["2", "3"]
    assert all((p["cand_a_name"], p["cand_a_party"]) == ("Alex Rivera", "DEM") for p in polls)
    assert [p["cand_a_pct"] - p["cand_b_pct"] for p in polls] == [2, 4]
    race = conn.execute("SELECT * FROM races").fetchone()
    assert (race["state_abbrev"], race["office"], race["stage"]) == ("OH", "senate", "special")


def test_votehub_skips_race_with_unknown_party(conn, tmp_path):
    path = tmp_path / "votehub_polls.json"
    path.write_text(json.dumps([
        vh_poll("1", "2026-10-01", [("Alex Rivera", 47), ("Pat Newcomer", 45)],
                subject="2026 Iowa", poll_type="governor"),
    ]))
    assert import_votehub_polls(conn, str(path), VH_PARTIES) == (0, ["Iowa governor: Pat Newcomer"])
    assert conn.execute("SELECT COUNT(*) FROM races").fetchone()[0] == 0


def test_pollster_names_match_loosely():
    assert normalize_name("Emerson College") == normalize_name("emerson  college")


@pytest.mark.skipif(not os.path.exists(dbmod.DB_PATH), reason="run scripts/build_db.py first")
def test_api_returns_2022_senate_forecast():
    from app.server import app
    client = app.test_client()
    data = client.get("/api/forecast?cycle=2022&office=senate").get_json()
    assert len(data["races"]) > 20
    assert 0 <= data["chamber"]["prob_dem_control"] <= 1
    assert client.get("/api/forecast?cycle=2022&office=house").status_code == 400
