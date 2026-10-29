"""Connects the database to the model: SQL queries in, forecasts out."""
import json
import os
from collections import Counter, defaultdict
from datetime import date

from .model import DEFAULT_PARAMS, Poll, forecast_race, simulate_chamber

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def load_params() -> dict:
    """Model settings, with error sizes learned by backtest.py if it has been run."""
    params = dict(DEFAULT_PARAMS)
    path = os.path.join(DATA_DIR, "model_params.json")
    if os.path.exists(path):
        with open(path) as f:
            params.update(json.load(f).get("params", {}))
    return params


def load_seats() -> dict:
    with open(os.path.join(DATA_DIR, "seats.json")) as f:
        return json.load(f)


def list_cycles(conn) -> list:
    rows = conn.execute(
        """SELECT r.cycle, r.office, COUNT(p.id) AS polls, COUNT(DISTINCT r.id) AS races,
                  SUM(r.actual_winner_party IS NOT NULL) > 0 AS has_results
           FROM races r LEFT JOIN polls p ON p.race_id = r.id
           GROUP BY r.cycle, r.office ORDER BY r.cycle DESC, r.office DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def list_pollsters(conn, cycle: int, office: str) -> list:
    """Pollsters active in this cycle, best-rated first."""
    rows = conn.execute(
        """SELECT pl.id, pl.name, pl.grade, pl.rank, COUNT(*) AS polls,
                  COUNT(DISTINCT r.state_abbrev) AS states
           FROM polls p
           JOIN races r ON r.id = p.race_id
           JOIN pollsters pl ON pl.id = p.pollster_id
           WHERE r.cycle = ? AND r.office = ?
           GROUP BY pl.id
           ORDER BY pl.grade IS NULL, pl.grade DESC, pl.rank, polls DESC""",
        (cycle, office),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_polls(conn, cycle, office, pollster_ids, exclude_partisan):
    sql = """SELECT p.*, r.state, r.state_abbrev, r.stage, r.election_date,
                    r.actual_winner_party, r.actual_margin,
                    pl.name AS pollster, pl.grade
             FROM polls p
             JOIN races r ON r.id = p.race_id
             JOIN pollsters pl ON pl.id = p.pollster_id
             WHERE r.cycle = ? AND r.office = ?"""
    args = [cycle, office]
    if pollster_ids:
        sql += f" AND p.pollster_id IN ({','.join('?' * len(pollster_ids))})"
        args += list(pollster_ids)
    if exclude_partisan:
        sql += " AND p.internal = 0 AND p.partisan IS NULL"
    return conn.execute(sql, args).fetchall()


def build_forecasts(conn, cycle: int, office: str, pollster_ids=None,
                    exclude_partisan: bool = True, as_of: date = None, params: dict = None) -> list:
    params = params or load_params()
    rows = _query_polls(conn, cycle, office, pollster_ids, exclude_partisan)

    by_race = defaultdict(list)
    for row in rows:
        by_race[row["race_id"]].append(row)

    results = []
    for race_id, race_rows in by_race.items():
        info = race_rows[0]
        election_day = date.fromisoformat(info["election_date"])
        today = as_of or min(date.today(), election_day)

        # Use the head-to-head matchup asked about most often in this race.
        pair = Counter((r["cand_a_name"], r["cand_b_name"]) for r in race_rows).most_common(1)[0][0]
        usable = [r for r in race_rows
                  if (r["cand_a_name"], r["cand_b_name"]) == pair
                  and date.fromisoformat(r["end_date"]) <= today]
        if not usable:
            continue
        sample = usable[0]

        polls = [Poll(pollster=r["pollster"], end_date=date.fromisoformat(r["end_date"]),
                      margin=r["cand_a_pct"] - r["cand_b_pct"], sample_size=r["sample_size"],
                      population=r["population"], grade=r["grade"], source_id=r["source_id"])
                 for r in usable]
        fc = forecast_race(polls, today, election_day, params)
        pct_by_id = {r["source_id"]: (r["cand_a_pct"], r["cand_b_pct"]) for r in usable}

        favorite = sample["cand_a_party"] if fc.prob_a >= 0.5 else sample["cand_b_party"]
        actual = None
        if info["actual_winner_party"]:
            actual = {"winner_party": info["actual_winner_party"],
                      "margin": round(info["actual_margin"], 1),
                      "favorite_won": info["actual_winner_party"] == favorite}

        results.append({
            "race_id": race_id,
            "state": info["state"], "state_abbrev": info["state_abbrev"], "stage": info["stage"],
            "election_date": info["election_date"], "as_of": today.isoformat(),
            "cand_a": {"name": pair[0], "party": sample["cand_a_party"]},
            "cand_b": {"name": pair[1], "party": sample["cand_b_party"]},
            "margin": fc.margin, "sigma": fc.sigma,
            "prob_a": round(fc.prob_a, 4), "prob_b": round(1 - fc.prob_a, 4),
            "favorite_party": favorite,
            "n_polls": fc.n_polls, "effective_polls": fc.effective_polls,
            "actual": actual,
            "polls": [{
                "pollster": p.pollster, "end_date": p.end_date.isoformat(),
                "a_pct": pct_by_id[p.source_id][0], "b_pct": pct_by_id[p.source_id][1],
                "margin": p.margin, "sample_size": p.sample_size, "population": p.population,
                "grade": p.grade, "weight_share": round(share, 4),
            } for p, share in fc.weights],
        })
    results.sort(key=lambda r: (r["state"], r["stage"] != "general"))
    return results


def _seat_key(race: dict) -> str:
    return race["state_abbrev"] + ("-special" if race["stage"] == "special" else "")


def senate_simulation(forecasts: list, cycle: int, params: dict = None, n_sims: int = 10000) -> dict:
    """Combine race forecasts with seats not on the ballot to estimate control."""
    params = params or load_params()
    seats = load_seats().get(str(cycle))
    if not seats:
        return None

    # A runoff replaces the first round for the same seat.
    chosen = {}
    for race in forecasts:
        if race["race_id"] in seats.get("ignore_races", []):
            continue
        key = _seat_key(race)
        if key not in seats["seats_up"]:
            continue
        if key not in chosen or race["stage"] == "runoff":
            chosen[key] = race

    sim_races, assumed = [], []
    for key, holder in seats["seats_up"].items():
        race = chosen.get(key)
        if race:
            sim_races.append({"margin": race["margin"], "sigma": race["sigma"],
                              "party_a": race["cand_a"]["party"], "party_b": race["cand_b"]["party"]})
        else:
            sim_races.append({"holder": holder})
            assumed.append({"seat": key, "party": holder})

    result = simulate_chamber(sim_races, seats["seats_not_up"], seats["tiebreak_party"],
                              params, n_sims=n_sims)
    result.update({"seats_not_up": seats["seats_not_up"], "tiebreak_party": seats["tiebreak_party"],
                   "polled_seats": len(chosen), "unpolled_seats_assumed": assumed})
    return result


def states_with_races(conn, cycle: int, office: str) -> list:
    """Every state holding this kind of race, polled or not (used to gray out the map)."""
    seats = load_seats().get(str(cycle), {})
    if office == "senate" and seats:
        states = {k.split("-")[0] for k in seats["seats_up"]}
    elif office == "governor" and seats:
        states = set(seats["governor_states"])
    else:
        states = set()
    for row in conn.execute("SELECT DISTINCT state_abbrev FROM races WHERE cycle = ? AND office = ?",
                            (cycle, office)):
        states.add(row["state_abbrev"])
    return sorted(states)
