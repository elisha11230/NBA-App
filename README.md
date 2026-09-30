# NBA Rosters

A personal NBA app: starting five on the court, bench, player cards, team stats,
shot charts, 2K ratings, contracts and cap, coaches, year by year stats and
league leaders. One self contained page, `index.html`, hosted on GitHub Pages.

## Files

| File | What it does |
|---|---|
| `app.html` | The app's code. Edit this, never index.html. |
| `index.html` | The site: app.html with all the data filled in, written by `build.py`. |
| `build.py` | Downloads fresh data and writes `index.html` from `app.html`. |
| `.github/workflows/update.yml` | Runs `build.py` twice a day (7:15 AM and 5:15 PM New York time) and right after any push to app.html, build.py or the workflow. |
| `coaches.json` | Last good copy of the coaching staffs, used if Wikipedia can't be reached. |

These appear on their own after the first run and are kept as backups:
`nba2k.json`, `nba2k_players.json` and `nba2k_history.json` (2K ratings, full
attributes and rating changes), `salaries.json` (contracts), `draft_picks.json`
(future picks), `awards.json`, `bios.json`, `transactions.json`,
`prospects.json`, `photos.json` (NBA.com ids and Commons photos for headshots), and `bbref_history.json` (seasons before 2001-02, filled in
on the first run and kept for good).

## Data sources

| Data | Source |
|---|---|
| Rosters, box scores, stats, shots, standings, year by year | ESPN via sportsdataverse (GitHub releases) |
| Live score in the header | ESPN scoreboard, fetched in the browser |
| 2K ratings | 2KRatings.com team pages |
| Contracts | Basketball Reference contracts page |
| Cap, tax and apron lines | Set in `build.py` (`CAP_RULES`), update each July |
| Coaches | Wikipedia |
| Schedule, team history | ESPN via sportsdataverse |
| Future draft picks | RealGM |
| Injuries, live stats, live shot charts | ESPN, fetched in the browser |
| News, game highlights, odds, arena and officials | ESPN, fetched in the browser |
| Career shot charts back to 2001-02, college stats | ESPN via sportsdataverse |
| Awards, player bios | Wikipedia (bios linked by ESPN id through Wikidata) |
| Transactions | RealGM |
| Advanced stats, rosters before 2001-02 | Basketball Reference (older seasons saved once, never refetched) |
| Draft big board and mock draft | Tankathon |
| Play clips (links to each play's NBA.com video) | NBA.com, through the Val Town relay `elishaben/nba-replays` |
| Standings | ESPN standings (live in the browser in season), Basketball Reference before 2001-02 |
| On/off impact | Rebuilt from ESPN play by play via sportsdataverse (saved in onoff.json) |
| Past drafts since 1980 | Wikipedia (saved in draft_history.json, each year fetched once) |
| Headshots | ESPN, NBA.com (ids from Wikidata and the nba_api list), Wikimedia Commons |

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
    sched 1206 games for 30 teams       schedule loaded
    hist 30 teams, 300 team seasons     team history built
    picks 30 teams, drafts 2027 on      draft picks loaded
    award ... players with awards       awards loaded
    bios  ... players                   bios loaded
    tx    ... transactions              moves loaded
    adv   ... players with advanced     advanced stats loaded
    bbref 22 seasons before 2001-02     old rosters saved
    tank  100 prospects, 60 mock picks  draft board loaded

A line saying `failed` or `cached copy` means that source was skipped this run
and the previous data was kept.

## Each July

Update the new season's cap numbers in `build.py` under `CAP_RULES`.

## The Val Town relay

`elishaben/nba-replays` on Val Town reads NBA.com game pages, which refuse browsers
on other sites. The app calls it for NBA.com game ids (`/games?date=`) and each
game's plays (`/pbp?game=`), then links every play to its own clip on NBA.com.
Free, nothing to maintain. If it ever stops answering, the app falls back to a
link to the game's NBA.com page.

## Updates

Changes are pushed straight to this repo (app.html, build.py, the workflow). Each
push starts the update workflow, which rebuilds index.html with fresh data, so the
site updates about ten minutes later without downloading or uploading anything.
