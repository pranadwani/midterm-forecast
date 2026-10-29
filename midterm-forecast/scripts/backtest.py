"""Test the model on past elections where we know the real results.

    python scripts/backtest.py

1. LEARN from 2018 + 2020: how far off were polling averages from the real
   result? That sets the model's uncertainty (systematic_error) and how much of
   the miss is shared by every race at once (national_error_share).
2. TEST on 2022, which the model never learned from: how often did the
   favorite win, and were the percentages honest? (e.g. do "70%" favorites win
   about 70% of the time?)

Results are saved to data/model_params.json (used by the app) and
data/backtest_report.json (shown on the app's "Model accuracy" panel).
"""
import json
import math
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.db import connect  # noqa: E402
from app.forecast import build_forecasts  # noqa: E402
from app.model import DEFAULT_PARAMS  # noqa: E402

TRAIN_CYCLES = (2018, 2020)
TEST_CYCLE = 2022
OFFICES = ("senate", "governor")


def forecasts_for(conn, cycles, params):
    out = []
    for cycle in cycles:
        for office in OFFICES:
            for race in build_forecasts(conn, cycle, office, params=params):
                if race["actual"]:
                    race["cycle"], race["office"] = cycle, office
                    out.append(race)
    return out


def learn_errors(races, params):
    """Estimate the polling-average miss and how correlated it is across races."""
    errors = [r["actual"]["margin"] - r["margin"] for r in races]
    # Part of each race's sigma comes from having few polls; remove that so we
    # only measure the error that's left over (the systematic part).
    noise = [r["sigma"] ** 2 - params["systematic_error"] ** 2 for r in races]
    mean_sq = sum(e * e for e in errors) / len(errors)
    systematic = math.sqrt(max(mean_sq - sum(noise) / len(noise), 1.0))

    groups = defaultdict(list)
    for r, e in zip(races, errors):
        groups[(r["cycle"], r["office"])].append(e)
    shared = [(sum(g) / len(g)) ** 2 - mean_sq / len(g) for g in groups.values()]
    national_share = min(max(sum(shared) / len(shared) / mean_sq, 0.2), 0.7)
    return round(systematic, 2), round(national_share, 2), round(math.sqrt(mean_sq), 2)


def score(races, simple_average=False):
    """Accuracy, Brier score (lower = better) and calibration buckets."""
    correct, brier, buckets = 0, 0.0, defaultdict(lambda: [0, 0])
    for r in races:
        if simple_average:
            p_fav = 1.0 if r["simple_margin"] != 0 else 0.5
            won = (r["simple_margin"] > 0) == (r["actual"]["winner_party"] == r["cand_a"]["party"])
        else:
            p_fav = max(r["prob_a"], r["prob_b"])
            won = r["actual"]["favorite_won"]
        correct += won
        brier += (1 - p_fav) ** 2 if won else p_fav ** 2
        if not simple_average:
            lo = 50 if p_fav < 0.6 else 60 if p_fav < 0.8 else 80 if p_fav < 0.95 else 95
            buckets[lo][0] += 1
            buckets[lo][1] += won
    n = len(races)
    labels = {50: "50-60%", 60: "60-80%", 80: "80-95%", 95: "95-100%"}
    return {
        "races": n,
        "favorite_won_pct": round(100 * correct / n, 1),
        "brier_score": round(brier / n, 4),
        "calibration": [{"bucket": labels[k], "races": v[0],
                         "favorite_won_pct": round(100 * v[1] / v[0], 1)}
                        for k, v in sorted(buckets.items())],
    }


def main():
    conn = connect()
    params = dict(DEFAULT_PARAMS)

    train = forecasts_for(conn, TRAIN_CYCLES, params)
    systematic, national_share, rmse = learn_errors(train, params)
    params.update(systematic_error=systematic, national_error_share=national_share)
    print(f"Learned from {len(train)} races in {TRAIN_CYCLES}: "
          f"average miss (RMSE) {rmse} pts -> systematic_error={systematic}, "
          f"national_error_share={national_share}")

    test = forecasts_for(conn, (TEST_CYCLE,), params)
    for r in test:      # unweighted average, for comparison
        r["simple_margin"] = sum(p["margin"] for p in r["polls"]) / len(r["polls"])
    model_score = score(test)
    naive_score = score(test, simple_average=True)
    abs_err = sum(abs(r["actual"]["margin"] - r["margin"]) for r in test) / len(test)
    naive_err = sum(abs(r["actual"]["margin"] - r["simple_margin"]) for r in test) / len(test)

    report = {
        "trained_on": list(TRAIN_CYCLES), "tested_on": TEST_CYCLE,
        "learned": {"systematic_error": systematic, "national_error_share": national_share,
                    "training_races": len(train), "training_rmse": rmse},
        "model": {**model_score, "avg_margin_error": round(abs_err, 2)},
        "simple_average": {"favorite_won_pct": naive_score["favorite_won_pct"],
                           "avg_margin_error": round(naive_err, 2)},
        "misses": [{"race": f"{r['state']} {r['office']} ({r['stage']})",
                    "favorite_prob": round(max(r["prob_a"], r["prob_b"]), 2),
                    "poll_margin": r["margin"], "actual_margin": r["actual"]["margin"],
                    "candidate_a": r["cand_a"]["name"]}
                   for r in test if not r["actual"]["favorite_won"]],
    }
    with open(os.path.join(ROOT, "data", "model_params.json"), "w") as f:
        json.dump({"params": {"systematic_error": systematic,
                              "national_error_share": national_share}}, f, indent=2)
    with open(os.path.join(ROOT, "data", "backtest_report.json"), "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
