"""The forecast model.

Step 1  Weight every poll   (newer, bigger, better-rated, likely-voter polls count more)
Step 2  Average the margins (weighted average of candidate A's lead over B)
Step 3  Add uncertainty     (how far off could that average be? learned from past elections)
Step 4  Win probability     P(A wins) = chance the real margin ends up above zero
Step 5  Simulate the Senate (play out every race 10,000 times, with errors that move together)

Everything here is plain Python with no database code, so it is easy to test.
"""
import math
import random
from dataclasses import dataclass, field
from datetime import date

# Default settings. `backtest.py` re-learns the error numbers from 2018 and
# 2020 elections and saves them to data/model_params.json.
DEFAULT_PARAMS = {
    "half_life_days": 14,       # a poll's weight halves every 14 days
    "reference_sample": 600,    # a 600-person poll gets a sample weight of 1.0
    "max_sample_weight": 1.5,   # huge samples don't get unlimited weight
    "population_weight": {"lv": 1.0, "v": 0.9, "rv": 0.8, "a": 0.6},
    "unknown_population_weight": 0.9,
    "unrated_pollster_grade": 1.0,   # grades run 0.5 (worst) .. 3.0 (best)
    "min_poll_spread": 3.0,     # assume polls disagree by at least 3 pts
    "systematic_error": 4.5,    # typical miss of a polling average, in points
    "national_error_share": 0.4,  # share of that miss that hits every race at once
    "daily_drift": 0.12,        # extra uncertainty per day still left before the election
}


def normal_cdf(x: float) -> float:
    """Chance that a standard normal variable is below x."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


@dataclass
class Poll:
    pollster: str
    end_date: date
    margin: float               # candidate A pct minus candidate B pct
    sample_size: int = None
    population: str = None
    grade: float = None         # 538 pollster grade (0.5-3.0) or None if unrated
    source_id: str = ""


@dataclass
class RaceForecast:
    margin: float               # weighted average lead of candidate A (points)
    sigma: float                # uncertainty in the margin (points, one std. dev.)
    prob_a: float               # chance candidate A wins (0-1)
    n_polls: int
    effective_polls: float      # after weighting, "how many polls' worth" of info
    weights: list = field(default_factory=list)   # (poll, weight share) for the UI


def poll_weight(poll: Poll, as_of: date, params: dict = DEFAULT_PARAMS) -> float:
    """How much a single poll should count, before adjusting for repeat pollsters."""
    days_old = max((as_of - poll.end_date).days, 0)
    recency = 0.5 ** (days_old / params["half_life_days"])

    n = poll.sample_size or params["reference_sample"]
    sample = min(math.sqrt(n / params["reference_sample"]), params["max_sample_weight"])

    population = params["population_weight"].get(
        (poll.population or "").lower(), params["unknown_population_weight"])

    grade = poll.grade if poll.grade is not None else params["unrated_pollster_grade"]
    quality = 0.4 + 0.2 * grade          # A+ pollster (3.0) -> 1.0, D pollster (0.5) -> 0.5

    return recency * sample * population * quality


def forecast_race(polls: list, as_of: date, election_date: date,
                  params: dict = DEFAULT_PARAMS) -> RaceForecast:
    """Turn a list of polls for one race into a margin and a win probability."""
    if not polls:
        raise ValueError("a race needs at least one poll")

    raw = [poll_weight(p, as_of, params) for p in polls]

    # One pollster releasing 5 polls shouldn't count 5 times as much as a
    # pollster releasing 1. Divide by the square root of each pollster's count.
    per_pollster = {}
    for p in polls:
        per_pollster[p.pollster] = per_pollster.get(p.pollster, 0) + 1
    w = [wt / math.sqrt(per_pollster[p.pollster]) for p, wt in zip(polls, raw)]

    total = sum(w)
    if total <= 0:                       # every poll is ancient -> fall back to equal weights
        w, total = [1.0] * len(polls), float(len(polls))

    margin = sum(wt * p.margin for p, wt in zip(polls, w)) / total
    effective = total ** 2 / sum(wt ** 2 for wt in w)

    # How much the polls disagree with each other (weighted standard deviation).
    variance = sum(wt * (p.margin - margin) ** 2 for p, wt in zip(polls, w)) / total
    spread = max(math.sqrt(variance), params["min_poll_spread"])

    days_left = max((election_date - as_of).days, 0)
    sigma = math.sqrt(
        params["systematic_error"] ** 2           # polls as a whole can miss
        + spread ** 2 / effective                 # few / noisy polls = less sure
        + (params["daily_drift"] ** 2) * days_left  # opinion can still move
    )
    prob_a = normal_cdf(margin / sigma)

    shares = sorted(((p, wt / total) for p, wt in zip(polls, w)), key=lambda x: -x[1])
    return RaceForecast(round(margin, 2), round(sigma, 2), prob_a, len(polls),
                        round(effective, 2), shares)


def simulate_chamber(races: list, seats_not_up: dict, tiebreak_party: str,
                     params: dict = DEFAULT_PARAMS, n_sims: int = 10000, seed: int = 7) -> dict:
    """Play out every Senate race many times and count who controls the chamber.

    races: list of dicts with keys
        margin, sigma, party_a, party_b   -> a polled race
        holder                            -> an unpolled race, assumed held by `holder`
    Polling misses are correlated: in each simulated election a shared
    "national" error is added to every race, plus a race-specific error.
    """
    rng = random.Random(seed)
    share = params["national_error_share"]
    nat_w, local_w = math.sqrt(share), math.sqrt(1 - share)

    control = {"DEM": 0, "REP": 0}
    dem_seat_counts = []
    for _ in range(n_sims):
        seats = dict(seats_not_up)
        national = rng.gauss(0, 1)
        for race in races:
            if "holder" in race:
                winner = race["holder"]
            else:
                error = race["sigma"] * (nat_w * national + local_w * rng.gauss(0, 1))
                winner = race["party_a"] if race["margin"] + error > 0 else race["party_b"]
            seats[winner] = seats.get(winner, 0) + 1

        dem, rep = seats.get("DEM", 0), seats.get("REP", 0)
        dem_seat_counts.append(dem)
        if dem > rep:
            control["DEM"] += 1
        elif rep > dem:
            control["REP"] += 1
        else:
            control[tiebreak_party] += 1     # the Vice President breaks a tie

    dem_seat_counts.sort()
    return {
        "prob_dem_control": control["DEM"] / n_sims,
        "prob_rep_control": control["REP"] / n_sims,
        "median_dem_seats": dem_seat_counts[n_sims // 2],
        "dem_seats_80pct_range": [dem_seat_counts[int(n_sims * 0.1)], dem_seat_counts[int(n_sims * 0.9)]],
        "n_sims": n_sims,
    }
