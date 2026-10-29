# Midterm Poll Forecast

An interactive US map that turns public polls into a win probability for every
Senate and governor race. Click a state to see each candidate's chance of
winning, the polls behind it and how much each poll counted. Check or uncheck
pollsters to see how the forecast changes.

**Stack:** Python (Flask) · SQLite (SQL) · JavaScript + D3.js for the map

## Run it

```bash
pip install -r requirements.txt
python run.py                   # open http://127.0.0.1:5000
python -m pytest                # 25 tests
```

`run.py` builds the database on first start and refreshes it whenever the data
changes; there is no separate setup step. To re-learn the model's error sizes
(2018-2020) and re-test on 2022, run `python scripts/backtest.py`.

## Live 2026 polls

The app ships with real 2018-2022 polls (with actual results) so it works out
of the box. The 2026 polls come from [FiftyPlusOne](https://fiftyplusone.news/downloads),
which requires an API key (a paid subscription). Set it once and the app
downloads the latest Senate and governor polls every time it starts:

```bash
# macOS / Linux
export FIFTYPLUSONE_API_KEY=your-key
# Windows PowerShell
$env:FIFTYPLUSONE_API_KEY = "your-key"

python run.py
```

Files are re-downloaded when they are more than 6 hours old. If the download
fails (no internet, bad key), the app keeps using the last copy it saved. The
downloaded files are git-ignored, so your licensed data is never committed.
The 2026 option in the Election dropdown fills in once polls are loaded.

## How the forecast works

For each race:

1. **Weight every poll.** Newer polls, larger samples, likely-voter samples and
   better-rated pollsters (538's 0.5-3.0 star ratings) count more. A pollster
   that releases many polls has its weight divided so it can't drown out others.
   Campaign- and party-sponsored polls are left out unless you include them.
2. **Average the margins** (candidate A's lead over candidate B).
3. **Add uncertainty.** Polling averages miss. The model learned the typical miss
   from 108 races in 2018 and 2020, and adds extra uncertainty when a race has
   few polls or the polls disagree.
4. **Win probability** = the chance the real margin ends up above zero, using a
   normal (bell curve) distribution. The curve in the side panel shows this.
5. **Senate control.** Every race is simulated 10,000 times. Part of the polling
   error is shared across all races (when polls miss, they tend to miss in the
   same direction everywhere), so upsets cluster like they do in real life.
   Seats not up for election and the Vice President's tie-breaking vote come from
   `data/seats.json`.

## Does it work? (backtest)

`scripts/backtest.py` learns from 2018 + 2020 and is then tested on the 2022
midterms, which it never saw:

| | This model | Simple average |
|---|---|---|
| Races where the favorite won | 95.2% (60 of 63) | 96.8% |
| Average miss on the margin | 4.36 pts | 4.99 pts |

Weighting the polls made the margins about 13% more accurate. The three races
the favorite lost (Nevada Senate, Arizona and Wisconsin governor) were all
near toss-ups at 51-65%, and all three missed in the same direction, which is
exactly why the Senate simulation uses a shared error.

One honest caveat: the 538 pollster ratings were last updated in 2024, after
the 2022 results were known, so the 2022 test is slightly optimistic.

## Project structure

```
app/
  db.py          SQLite schema (pollsters, races, polls) and connection
  importers.py   load 538 and FiftyPlusOne CSV files into the database
  model.py       the math: poll weights, averages, win probability, simulation
  forecast.py    SQL queries + model -> forecast for every race
  server.py      Flask API (/api/forecast, /api/pollsters, ...)
static/          the web page: index.html, app.js (D3 map), styles.css
  updater.py     download the latest 2026 polls and rebuild the database
scripts/         build_db.py (force a rebuild), backtest.py
data/raw/        source CSV files
data/seats.json  Senate seats not on the ballot, tie-breaker, current holders
tests/           pytest tests for the model, importers and API
```

## Data sources

- **FiveThirtyEight** pollster ratings and historical polls,
  <https://github.com/fivethirtyeight/data/tree/master/pollster-ratings>,
  licensed CC BY 4.0. Trimmed to 2018-2022 Senate and governor races.
- **FiftyPlusOne** 2026 poll files, <https://fiftyplusone.news/readme>
  (check their terms before publishing anything built on their data).

## Limits

This is a forecast from public polls only. It doesn't use fundamentals like
fundraising or past results, races without polls are assumed to stay with the
current party, and `data/seats.json` should be checked against current Senate
membership. It is not a prediction of the outcome.
