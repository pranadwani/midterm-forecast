"""Flask web server: serves the map page and a small JSON API.

    GET /api/cycles                      which elections are loaded
    GET /api/pollsters?cycle=&office=    pollsters to show as checkboxes
    GET /api/forecast?cycle=&office=&pollsters=1,2,3&include_partisan=0
                                         every race's forecast (+ Senate control)
    GET /api/backtest                    how accurate the model was on 2022
"""
import json
import os

from flask import Flask, abort, g, jsonify, request, send_from_directory

from .db import DB_PATH, connect
from .forecast import (DATA_DIR, build_forecasts, list_cycles, list_pollsters,
                       senate_simulation, states_with_races)

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")


def db():
    if "db" not in g:
        if not os.path.exists(DB_PATH):
            abort(500, "Database not built yet. Start the app with: python run.py")
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _cycle_and_office():
    try:
        cycle = int(request.args.get("cycle", ""))
    except ValueError:
        abort(400, "cycle must be a year, e.g. 2022")
    office = request.args.get("office", "senate")
    if office not in ("senate", "governor"):
        abort(400, "office must be 'senate' or 'governor'")
    return cycle, office


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/cycles")
def api_cycles():
    return jsonify(list_cycles(db()))


@app.get("/api/pollsters")
def api_pollsters():
    cycle, office = _cycle_and_office()
    return jsonify(list_pollsters(db(), cycle, office))


@app.get("/api/forecast")
def api_forecast():
    cycle, office = _cycle_and_office()
    ids = request.args.get("pollsters", "").strip()
    try:
        pollster_ids = [int(x) for x in ids.split(",") if x] or None
    except ValueError:
        abort(400, "pollsters must be a comma-separated list of ids")
    include_partisan = request.args.get("include_partisan") == "1"

    races = build_forecasts(db(), cycle, office, pollster_ids,
                            exclude_partisan=not include_partisan)
    chamber = senate_simulation(races, cycle) if office == "senate" else None
    return jsonify({
        "cycle": cycle, "office": office,
        "updated": int(os.path.getmtime(DB_PATH)),     # when the polls were last loaded
        "states_with_races": states_with_races(db(), cycle, office),
        "races": races,
        "chamber": chamber,
    })


@app.get("/api/backtest")
def api_backtest():
    path = os.path.join(DATA_DIR, "backtest_report.json")
    if not os.path.exists(path):
        return jsonify(None)
    with open(path) as f:
        return jsonify(json.load(f))


@app.errorhandler(400)
@app.errorhandler(500)
def json_error(err):
    return jsonify({"error": getattr(err, "description", str(err))}), err.code
