"""Load poll files into the SQLite database.

Three formats are supported:
  * 538 historical polls (raw_polls.csv) - 2018-2022 races WITH actual results,
    used to test ("backtest") the model.
  * FiftyPlusOne poll files (senate_general.csv / governor_general.csv) - the
    current 2026 polls. Each row there is ONE candidate's number in ONE poll
    question, so rows are grouped back into head-to-head polls here.
  * VoteHub polls (votehub_polls.json) - free 2026 polls, used when there are
    no FiftyPlusOne files. Candidate parties come from data/candidates_2026.json.
"""
import csv
import json
from collections import Counter, defaultdict
from datetime import date

from .db import get_or_create_pollster

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
STATE_ABBREV = {name: abbr for abbr, name in STATES.items()}

# Candidate "A" is always the more Democratic-leaning one, so a positive
# margin means the left-of-center candidate is ahead in every race.
PARTY_ORDER = {"DEM": 0, "IND": 1, "LIB": 2, "GRE": 1, "REP": 3}


def party_rank(party: str) -> int:
    return PARTY_ORDER.get((party or "").upper(), 1)


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value):
    f = _float(value)
    return int(f) if f is not None else None


def normalize_name(name: str) -> str:
    """Loose key for matching pollster names across sources."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


# ---------------------------------------------------------------- ratings
def load_pollster_ratings(conn, path: str) -> dict:
    """Store 538 pollster grades. Returns {538 rating id: pollster db id}."""
    id_map = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pid = get_or_create_pollster(conn, row["pollster"])
            conn.execute(
                "UPDATE pollsters SET grade = ?, rank = ? WHERE id = ?",
                (_float(row["numeric_grade"]), _int(row["rank"]), pid),
            )
            id_map[row["pollster_rating_id"]] = pid
    conn.commit()
    return id_map


# ---------------------------------------------------------------- 538 historical
STAGE_FROM_538 = {"G": "general", "GS": "special", "R": "runoff"}


def import_538_polls(conn, path: str, rating_ids: dict) -> int:
    """Import 538 raw polls (Senate + Governor general elections, with results)."""
    count = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            # race looks like "2022_Sen-G_PA" or "2022_Sen-GS_OK"
            _, kind, abbr = row["race"].split("_")
            office_code, stage_code = kind.split("-")
            office = {"Sen": "senate", "Gov": "governor"}.get(office_code)
            if office is None or abbr not in STATES or stage_code not in STAGE_FROM_538:
                continue

            a = (row["cand1_name"], row["cand1_party"], _float(row["cand1_pct"]), _float(row["cand1_actual"]))
            b = (row["cand2_name"], row["cand2_party"], _float(row["cand2_pct"]), _float(row["cand2_actual"]))
            if None in (a[2], b[2]):
                continue
            if party_rank(a[1]) > party_rank(b[1]):
                a, b = b, a                       # keep the Democratic-leaning candidate as "A"

            winner = a[1] if (a[3] or 0) > (b[3] or 0) else b[1]
            conn.execute(
                """INSERT OR IGNORE INTO races
                   (id, cycle, office, state, state_abbrev, stage, election_date,
                    actual_winner_party, actual_margin)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["race"], int(row["cycle"]), office, STATES[abbr], abbr,
                 STAGE_FROM_538[stage_code], row["electiondate"], winner,
                 (a[3] or 0) - (b[3] or 0)),
            )

            pollster_id = rating_ids.get(row["pollster_rating_id"]) or get_or_create_pollster(conn, row["pollster"])
            partisan = row["partisan"] if row["partisan"] not in ("", "NA") else None
            cur = conn.execute(
                """INSERT OR IGNORE INTO polls
                   (source, source_id, race_id, pollster_id, end_date, sample_size, population,
                    internal, partisan, cand_a_name, cand_a_party, cand_a_pct,
                    cand_b_name, cand_b_party, cand_b_pct)
                   VALUES ('538', ?, ?, ?, ?, ?, NULL, 0, ?, ?, ?, ?, ?, ?, ?)""",
                (row["question_id"], row["race"], pollster_id, row["polldate"],
                 _int(row["samplesize"]), partisan, a[0], a[1], a[2], b[0], b[1], b[2]),
            )
            count += cur.rowcount
    conn.commit()
    return count


# ---------------------------------------------------------------- FiftyPlusOne (2026)
OFFICES = {"U.S. Senate": "senate", "Governor": "governor"}
SENATE_CLASS_BY_CYCLE = {0: "Class II", 1: "Class III", 2: "Class I"}   # 2026 -> Class II


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "t", "1", "yes")


def import_fiftyplusone_polls(conn, path: str, cycle: int = 2026) -> int:
    """Import a FiftyPlusOne senate_general.csv or governor_general.csv file."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [
            r for r in csv.DictReader(f)
            if r.get("office_type") in OFFICES
            and str(r.get("cycle")) == str(cycle)
            and r.get("stage") in ("general", "runoff")
            and not _truthy(r.get("hypothetical"))
            and r.get("state") in STATE_ABBREV
            and _float(r.get("pct")) is not None
        ]

    # Group the one-row-per-candidate layout back into poll questions.
    questions = defaultdict(list)
    for r in rows:
        questions[(r["race_id"], r["question_id"])].append(r)

    # The race's two main candidates = the two that appear in the most polls.
    appearances = defaultdict(Counter)
    for (race_id, _), answers in questions.items():
        for r in answers:
            appearances[race_id][(r["candidate_name"], r["party"])] += 1

    regular_class = SENATE_CLASS_BY_CYCLE[((cycle - 2026) // 2) % 3]
    count = 0
    for (race_id, question_id), answers in questions.items():
        main = [c for c, _ in appearances[race_id].most_common(2)]
        if len(main) < 2:
            continue
        by_cand = {(r["candidate_name"], r["party"]): r for r in answers}
        if not all(c in by_cand for c in main):
            continue                              # poll didn't ask about both main candidates
        a, b = sorted(main, key=lambda c: party_rank(c[1]))
        ra, rb = by_cand[a], by_cand[b]
        first = answers[0]

        office = OFFICES[first["office_type"]]
        stage = first["stage"]
        if office == "senate" and stage == "general" and first.get("seat_name") not in ("", None, regular_class):
            stage = "special"
        db_race_id = f"fpo_{race_id}"
        conn.execute(
            """INSERT OR IGNORE INTO races
               (id, cycle, office, state, state_abbrev, stage, election_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (db_race_id, cycle, office, first["state"], STATE_ABBREV[first["state"]],
             stage, first.get("election_date") or f"{cycle}-11-03"),
        )
        pollster_id = match_pollster(conn, first["pollster"])
        cur = conn.execute(
            """INSERT OR IGNORE INTO polls
               (source, source_id, race_id, pollster_id, end_date, sample_size, population,
                internal, partisan, cand_a_name, cand_a_party, cand_a_pct,
                cand_b_name, cand_b_party, cand_b_pct)
               VALUES ('fiftyplusone', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (question_id, db_race_id, pollster_id, first["end_date"][:10],
             _int(first.get("sample_size")), (first.get("population") or "").lower() or None,
             1 if _truthy(first.get("internal")) else 0, first.get("partisan") or None,
             a[0], a[1], _float(ra["pct"]), b[0], b[1], _float(rb["pct"])),
        )
        count += cur.rowcount
    conn.commit()
    return count


# ---------------------------------------------------------------- VoteHub (2026, free)
VOTEHUB_OFFICES = {"us-senator": "senate", "governor": "governor"}
RECENT_DAYS = 60          # the race's matchup = the strongest candidates in its last 60 days


def import_votehub_polls(conn, path: str, candidates: dict, special_states=(), cycle: int = 2026):
    """Import polls from the free VoteHub API (https://api.votehub.com/polls).

    VoteHub doesn't give candidate parties, so they come from `candidates`
    ({"parties": {name: party}, "aliases": {misspelling: name}}). Returns
    (polls imported, races skipped because a candidate's party is unknown).
    """
    with open(path, encoding="utf-8") as f:
        polls = json.load(f)
    parties, aliases = candidates.get("parties", {}), candidates.get("aliases", {})

    # Subject is "2026 Ohio" for a general election, "2026 Texas Democratic" for a primary.
    races = defaultdict(list)
    for p in polls:
        office = VOTEHUB_OFFICES.get(p.get("poll_type"))
        year, _, state = (p.get("subject") or "").partition(" ")
        if office and year == str(cycle) and state in STATE_ABBREV and p.get("end_date"):
            answers = {aliases.get(a["choice"], a["choice"]): _float(a.get("pct"))
                       for a in p.get("answers", []) if _float(a.get("pct")) is not None}
            races[(office, state)].append((p, answers))

    count, unknown = 0, []
    for (office, state), race_polls in races.items():
        # Early polls test matchups from before the primaries; the nominees are
        # the two candidates with the most total support in the race's recent
        # polls (support, not appearances, so minor candidates listed in every
        # poll don't outrank a major one left out of a single poll).
        newest = max(date.fromisoformat(p["end_date"]) for p, _ in race_polls)
        support = Counter()
        for p, answers in race_polls:
            if (newest - date.fromisoformat(p["end_date"])).days <= RECENT_DAYS:
                support.update(answers)
        main = [name for name, _ in support.most_common(2)]
        if len(main) < 2:
            continue
        if any(name not in parties for name in main):
            unknown.append(f"{state} {office}: " + ", ".join(n for n in main if n not in parties))
            continue
        a, b = sorted(main, key=lambda n: party_rank(parties[n]))

        abbr = STATE_ABBREV[state]
        stage = "special" if office == "senate" and abbr in special_states else "general"
        race_id = f"vh_{cycle}_{office}_{abbr}"
        conn.execute(
            """INSERT OR IGNORE INTO races
               (id, cycle, office, state, state_abbrev, stage, election_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (race_id, cycle, office, state, abbr, stage, f"{cycle}-11-03"),
        )
        for p, answers in race_polls:
            if a not in answers or b not in answers:
                continue                              # poll didn't ask about both main candidates
            cur = conn.execute(
                """INSERT OR IGNORE INTO polls
                   (source, source_id, race_id, pollster_id, end_date, sample_size, population,
                    internal, partisan, cand_a_name, cand_a_party, cand_a_pct,
                    cand_b_name, cand_b_party, cand_b_pct)
                   VALUES ('votehub', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (p["id"], race_id, match_pollster(conn, p["pollster"]), p["end_date"][:10],
                 _int(p.get("sample_size")), (p.get("population") or "").lower() or None,
                 1 if p.get("internal") else 0, p.get("partisan") or None,
                 a, parties[a], answers[a], b, parties[b], answers[b]),
            )
            count += cur.rowcount
    conn.commit()
    return count, sorted(unknown)


def match_pollster(conn, name: str) -> int:
    """Reuse a rated pollster when names match loosely (e.g. 'Emerson' vs 'Emerson College')."""
    key = normalize_name(name)
    best = None
    for row in conn.execute("SELECT id, name FROM pollsters"):
        other = normalize_name(row["name"])
        if other == key:
            return row["id"]
        if len(key) >= 5 and (other.startswith(key) or key.startswith(other)) and len(other) >= 5:
            best = best or row["id"]
    return best or get_or_create_pollster(conn, name)
