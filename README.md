# NBA Rosters

A personal NBA app: starting five on the court, bench, player cards, team stats,
shot charts, 2K ratings, contracts and cap, coaches, year by year stats and
league leaders. One self contained page, `index.html`, hosted on GitHub Pages.

## Files

| File | What it does |
|---|---|
| `index.html` | The app. All data is baked into it. |
| `build.py` | Downloads fresh data and rewrites the data inside `index.html`. |
| `.github/workflows/update.yml` | Runs `build.py` twice a day (7:15 AM and 5:15 PM New York time). |
| `coaches.json` | Last good copy of the coaching staffs, used if Wikipedia can't be reached. |

These appear on their own after the first run and are kept as backups:
`nba2k.json` and `nba2k_history.json` (2K ratings and their changes),
`salaries.json` (contracts).

## Data sources

| Data | Source |
|---|---|
| Rosters, box scores, stats, shots, standings, year by year | ESPN via sportsdataverse (GitHub releases) |
| Live score in the header | ESPN scoreboard, fetched in the browser |
| 2K ratings | 2KRatings.com team pages |
| Contracts | Basketball Reference contracts page |
| Cap, tax and apron lines | Set in `build.py` (`CAP_RULES`), update each July |
| Coaches | Wikipedia |

If a source is down or blocks a run, the app keeps the last good copy and the
rest of the build carries on.

## Setup (all in the browser)

1. Create a public repository, for example `NBA-Rosters`.
2. Upload `index.html`, `build.py`, `coaches.json` and `README.md`.
3. Add file, Create new file, name it `.github/workflows/update.yml`, paste in its contents, commit.
4. Settings, Actions, General, Workflow permissions: choose **Read and write permissions**, save.
5. Settings, Pages: Source **Deploy from a branch**, branch **main**, folder **/ (root)**, save.
6. Actions tab, **update**, **Run workflow**.

## Checking a run

Open the finished run, then the `python build.py` step. Lines to look for:

    built 30 teams, 548 players ...     everything built
    2k   540 players from 30 teams      2K ratings loaded
    sal  499 contracts ...              contracts loaded
    coach 30 head coaches ...           coaches loaded
    lead 18 regular season boards ...   leaders built

A line saying `failed` or `cached copy` means that source was skipped this run
and the previous data was kept.

## Each July

Update the new season's cap numbers in `build.py` under `CAP_RULES`.
