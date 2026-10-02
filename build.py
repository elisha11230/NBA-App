"""
NBA depth chart builder.

Downloads the free sportsdataverse (hoopR) NBA files that are published to
GitHub every day, turns them into one compact JSON payload, and writes it
into index.html between the NBA-DATA markers. GitHub Actions runs this on a
schedule, so the page stays current without anyone harvesting anything.

Sources (all public GitHub release files, no keys, never blocked):
  espn_nba_rosters             current rosters, bio, headshots
  espn_nba_player_boxscores    every player line from every game
  espn_nba_team_boxscores      every team line from every game
  espn_nba_standings           records, seeds, splits

Starting five logic:
  In season  -> who started the team's most recent game (still on roster)
  Offseason  -> projected from last season's starts and minutes
"""
import io, json, re, sys, datetime as dt, urllib.request
import pandas as pd

BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"

DIVS = {
    "Atlantic": ["BOS", "BKN", "NY", "PHI", "TOR"],
    "Central": ["CHI", "CLE", "DET", "IND", "MIL"],
    "Southeast": ["ATL", "CHA", "MIA", "ORL", "WSH"],
    "Northwest": ["DEN", "MIN", "OKC", "POR", "UTAH"],
    "Pacific": ["GS", "LAC", "LAL", "PHX", "SAC"],
    "Southwest": ["DAL", "HOU", "MEM", "NO", "SA"],
}
EAST = {"Atlantic", "Central", "Southeast"}
TEAM_ABBRS = {a for v in DIVS.values() for a in v}


def get(release, name):
    url = f"{BASE}/{release}/{name}"
    import os as _os
    cache_dir = _os.environ.get("PARQUET_CACHE")  # optional, for local test runs only
    cpath = _os.path.join(cache_dir, f"{release}__{name}") if cache_dir else None
    try:
        if cpath and _os.path.exists(cpath):
            raw = open(cpath, "rb").read()
            df = pd.read_parquet(io.BytesIO(raw))
            return df
        with urllib.request.urlopen(url, timeout=120) as r:
            raw = r.read()
        if cpath:
            open(cpath, "wb").write(raw)
        df = pd.read_parquet(io.BytesIO(raw))
        print(f"ok   {release}/{name}  {len(df)} rows")
        return df
    except Exception as e:
        print(f"miss {release}/{name}  ({e.__class__.__name__})")
        return None


def stamp(release):
    try:
        with urllib.request.urlopen(f"{BASE}/{release}/timestamp.json", timeout=30) as r:
            return json.loads(r.read()).get("last_updated")
    except Exception:
        return None


def num(x, nd=1):
    try:
        if pd.isna(x):
            return None
        return round(float(x), nd)
    except Exception:
        return None


def height_in(h):
    m = re.match(r"(\d+)'\s*(\d+)", str(h or ""))
    return int(m.group(1)) * 12 + int(m.group(2)) if m else 78


def season_label(s):
    return f"{s-1}-{str(s)[-2:]}"


# ---------------------------------------------------------------- load
today = dt.date.today()
S = today.year + 1 if today.month >= 8 else today.year

rost = get("espn_nba_rosters", f"rosters_{S}.parquet")
roster_season = S
if rost is None or rost.empty:
    rost = get("espn_nba_rosters", f"rosters_{S-1}.parquet")
    roster_season = S - 1

box, tbox, stats_season = None, None, None
for s in (S, S - 1):
    b = get("espn_nba_player_boxscores", f"player_box_{s}.parquet")
    if b is not None and (b.season_type.isin([2, 3])).any():
        box, stats_season = b, s
        tbox = get("espn_nba_team_boxscores", f"team_box_{s}.parquet")
        break
if box is None:
    sys.exit("No box score data found; leaving index.html untouched.")

stand = get("espn_nba_standings", f"standings_{stats_season}.parquet")

# Preseason games of the new season (type 1) tell us who started most recently
pre_box = None
if stats_season != S:
    pre_box = get("espn_nba_player_boxscores", f"player_box_{S}.parquet")
    if pre_box is not None and pre_box.empty:
        pre_box = None

box = box[box.team_abbreviation.isin(TEAM_ABBRS)].copy()
box["athlete_id"] = pd.to_numeric(box.athlete_id, errors="coerce").astype("Int64")
tbox = tbox[tbox.team_abbreviation.isin(TEAM_ABBRS) & tbox.opponent_team_abbreviation.isin(TEAM_ABBRS)].copy()
NUMC = ["team_score", "opponent_team_score", "assists", "blocks", "defensive_rebounds", "fast_break_points",
        "field_goals_made", "field_goals_attempted", "free_throws_made", "free_throws_attempted",
        "offensive_rebounds", "points_in_paint", "steals", "three_point_field_goals_made",
        "three_point_field_goals_attempted", "total_rebounds", "total_turnovers", "turnover_points"]
for c in NUMC:
    tbox[c] = pd.to_numeric(tbox[c], errors="coerce").fillna(0)
rost["athlete_id"] = pd.to_numeric(rost.athlete_id, errors="coerce").astype("Int64")

reg = box[box.season_type == 2]
played = reg[(reg.did_not_play != True) & (reg.minutes.fillna(0) > 0)]

# ---------------------------------------------------------------- players
SUM = ["minutes", "points", "rebounds", "offensive_rebounds", "defensive_rebounds", "assists",
       "steals", "blocks", "turnovers", "fouls", "field_goals_made", "field_goals_attempted",
       "three_point_field_goals_made", "three_point_field_goals_attempted",
       "free_throws_made", "free_throws_attempted"]
played = played.copy()
played["pm"] = pd.to_numeric(played.plus_minus.astype(str).str.replace("+", "", regex=False), errors="coerce")
agg = played.groupby("athlete_id").agg(
    gp=("game_id", "nunique"), gs=("starter", "sum"), pm=("pm", "sum"),
    **{c: (c, "sum") for c in SUM})
agg["dd"] = played.assign(
    c=(played[["points", "rebounds", "assists", "steals", "blocks"]] >= 10).sum(axis=1)
).groupby("athlete_id").c.apply(lambda s: int((s >= 2).sum()))


def per_game(r):
    g = max(r.gp, 1)
    fga, fta = r.field_goals_attempted, r.free_throws_attempted
    tsa = 2 * (fga + 0.44 * fta)
    return {
        "gp": int(r.gp), "gs": int(r.gs),
        "min": num(r.minutes / g), "pts": num(r.points / g), "reb": num(r.rebounds / g),
        "oreb": num(r.offensive_rebounds / g), "dreb": num(r.defensive_rebounds / g),
        "ast": num(r.assists / g), "stl": num(r.steals / g), "blk": num(r.blocks / g),
        "tov": num(r.turnovers / g), "pf": num(r.fouls / g),
        "fgm": num(r.field_goals_made / g), "fga": num(fga / g),
        "tpm": num(r.three_point_field_goals_made / g), "tpa": num(r.three_point_field_goals_attempted / g),
        "ftm": num(r.free_throws_made / g), "fta": num(fta / g),
        "fgp": num(100 * r.field_goals_made / fga) if fga else None,
        "tpp": num(100 * r.three_point_field_goals_made / r.three_point_field_goals_attempted)
        if r.three_point_field_goals_attempted else None,
        "ftp": num(100 * r.free_throws_made / fta) if fta else None,
        "ts": num(100 * r.points / tsa) if tsa else None,
        "efg": num(100 * (r.field_goals_made + 0.5 * r.three_point_field_goals_made) / fga) if fga else None,
        "pm": num(r.pm / g), "dd": int(r.dd),
        "p36": num(36 * r.points / r.minutes) if r.minutes else None,
        "r36": num(36 * r.rebounds / r.minutes) if r.minutes else None,
        "a36": num(36 * r.assists / r.minutes) if r.minutes else None,
    }


season_line = {int(i): per_game(r) for i, r in agg.iterrows()}

# League ranks among qualified players (20+ games, 10+ minutes)
# 20 games once the season is deep enough, fewer in the first weeks
max_gp = max((s["gp"] for s in season_line.values()), default=0)
GP_MIN = min(20, max(1, int(max_gp * 0.6)))
qual = [i for i, s in season_line.items() if s["gp"] >= GP_MIN and (s["min"] or 0) >= 10]
RANK_KEYS = {"pts": 1, "reb": 1, "ast": 1, "stl": 1, "blk": 1, "min": 1, "tpm": 1,
             "ts": 1, "efg": 1, "fgp": 1, "tpp": 1, "ftp": 1, "pm": 1, "tov": -1,
             "p36": 1, "r36": 1, "a36": 1}
for k, d in RANK_KEYS.items():
    vals = sorted(((season_line[i][k] or 0) for i in qual), reverse=d > 0)
    for i in qual:
        v = season_line[i][k] or 0
        season_line[i].setdefault("rk", {})[k] = vals.index(v) + 1
QUAL_N = len(qual)

# Playoff line
po = box[(box.season_type == 3) & (box.did_not_play != True) & (box.minutes.fillna(0) > 0)]
po_line = {}
for i, g in po.groupby("athlete_id"):
    n = g.game_id.nunique()
    po_line[int(i)] = {"gp": n, "pts": num(g.points.sum() / n), "reb": num(g.rebounds.sum() / n),
                       "ast": num(g.assists.sum() / n), "min": num(g.minutes.sum() / n),
                       "fgp": num(100 * g.field_goals_made.sum() / g.field_goals_attempted.sum())
                       if g.field_goals_attempted.sum() else None}

# Splits
def split_line(g):
    n = g.game_id.nunique()
    if not n:
        return None
    fga = g.field_goals_attempted.sum()
    return [n, num(g.minutes.sum() / n), num(g.points.sum() / n), num(g.rebounds.sum() / n),
            num(g.assists.sum() / n), num(100 * g.field_goals_made.sum() / fga) if fga else None]

splits = {}
for i, g in played.groupby("athlete_id"):
    g = g.sort_values("game_date")
    splits[int(i)] = {
        "Home": split_line(g[g.home_away == "home"]),
        "Road": split_line(g[g.home_away == "away"]),
        "As starter": split_line(g[g.starter == True]),
        "Off the bench": split_line(g[g.starter != True]),
        "Wins": split_line(g[g.team_winner == True]),
        "Losses": split_line(g[g.team_winner != True]),
        "Last 10": split_line(g.tail(10)),
    }

# Game logs (regular season + playoffs), compact arrays
logs = {}
lb = box[(box.season_type.isin([2, 3])) & (box.did_not_play != True) & (box.minutes.fillna(0) > 0)].copy()
lb["pm"] = pd.to_numeric(lb.plus_minus.astype(str).str.replace("+", "", regex=False), errors="coerce")
lb = lb.sort_values("game_date")
for r in lb.itertuples():
    logs.setdefault(int(r.athlete_id), []).append([
        str(r.game_date)[5:], r.opponent_team_abbreviation, "@" if r.home_away == "away" else "",
        "W" if r.team_winner else "L", int(r.team_score), int(r.opponent_team_score),
        int(r.minutes or 0), int(r.points or 0), int(r.rebounds or 0), int(r.assists or 0),
        int(r.steals or 0), int(r.blocks or 0), int(r.turnovers or 0),
        int(r.field_goals_made or 0), int(r.field_goals_attempted or 0),
        int(r.three_point_field_goals_made or 0), int(r.three_point_field_goals_attempted or 0),
        int(r.free_throws_made or 0), int(r.free_throws_attempted or 0),
        None if pd.isna(r.pm) else int(r.pm), 1 if r.starter else 0,
        1 if r.season_type == 3 else 0, r.team_abbreviation])

# Last team each player appeared for (to flag new arrivals)
last_team = lb.groupby("athlete_id").team_abbreviation.last().to_dict()

# ---------------------------------------------------------------- teams
tb = tbox[tbox.season_type == 2].copy()
tb = tb.merge(
    tb[["game_id", "team_id", "field_goals_attempted", "offensive_rebounds", "defensive_rebounds",
        "total_turnovers", "free_throws_attempted", "field_goals_made", "three_point_field_goals_made",
        "three_point_field_goals_attempted", "free_throws_made", "team_score"]]
    .rename(columns=lambda c: c if c == "game_id" else "o_" + c),
    left_on=["game_id", "opponent_team_id"], right_on=["game_id", "o_team_id"], how="left")
for c in ["total_turnovers", "o_total_turnovers"]:
    tb[c] = pd.to_numeric(tb[c], errors="coerce").fillna(0)
tb["poss"] = 0.5 * ((tb.field_goals_attempted - tb.offensive_rebounds + tb.total_turnovers + 0.44 * tb.free_throws_attempted)
                    + (tb.o_field_goals_attempted - tb.o_offensive_rebounds + tb.o_total_turnovers + 0.44 * tb.o_free_throws_attempted))

team_stats = {}
for tid, g in tb.groupby("team_id"):
    n = len(g)
    P, OP, POS = g.team_score.sum(), g.opponent_team_score.sum(), g.poss.sum()
    fga, ofga = g.field_goals_attempted.sum(), g.o_field_goals_attempted.sum()
    s = {
        "ppg": P / n, "opp": OP / n, "diff": (P - OP) / n,
        "ortg": 100 * P / POS, "drtg": 100 * OP / POS, "net": 100 * (P - OP) / POS, "pace": POS / n,
        "efg": 100 * (g.field_goals_made.sum() + 0.5 * g.three_point_field_goals_made.sum()) / fga,
        "tovp": 100 * g.total_turnovers.sum() / POS,
        "orbp": 100 * g.offensive_rebounds.sum() / (g.offensive_rebounds.sum() + g.o_defensive_rebounds.sum()),
        "ftr": 100 * g.free_throws_made.sum() / fga,
        "oefg": 100 * (g.o_field_goals_made.sum() + 0.5 * g.o_three_point_field_goals_made.sum()) / ofga,
        "otovp": 100 * g.o_total_turnovers.sum() / POS,
        "drbp": 100 * g.defensive_rebounds.sum() / (g.defensive_rebounds.sum() + g.o_offensive_rebounds.sum()),
        "oftr": 100 * g.o_free_throws_made.sum() / ofga,
        "fgp": 100 * g.field_goals_made.sum() / fga,
        "tpp": 100 * g.three_point_field_goals_made.sum() / g.three_point_field_goals_attempted.sum(),
        "tpa": g.three_point_field_goals_attempted.sum() / n,
        "tpr": 100 * g.three_point_field_goals_attempted.sum() / fga,
        "ftp": 100 * g.free_throws_made.sum() / g.free_throws_attempted.sum(),
        "reb": g.total_rebounds.sum() / n, "ast": g.assists.sum() / n,
        "stl": g.steals.sum() / n, "blk": g.blocks.sum() / n, "tov": g.total_turnovers.sum() / n,
        "paint": g.points_in_paint.sum() / n, "fb": g.fast_break_points.sum() / n,
        "potov": pd.to_numeric(g.turnover_points, errors="coerce").sum() / n,
    }
    team_stats[int(tid)] = s

# Lower is better for these
LOWER = {"opp", "drtg", "tovp", "oefg", "oftr", "tov"}
team_ranks = {}
for k in next(iter(team_stats.values())).keys():
    order = sorted(team_stats, key=lambda t: team_stats[t][k], reverse=k not in LOWER)
    for pos, t in enumerate(order, 1):
        team_ranks.setdefault(t, {})[k] = pos
LEAGUE_AVG = {k: sum(s[k] for s in team_stats.values()) / len(team_stats) for k in next(iter(team_stats.values()))}

# Game by game results
games = {}
for r in tbox.sort_values("game_date").itertuples():
    games.setdefault(int(r.team_id), []).append([
        str(r.game_date)[5:], r.opponent_team_abbreviation, "@" if r.team_home_away == "away" else "",
        int(r.team_score), int(r.opponent_team_score), int(r.season_type)])

# Standings
recs = {}
if stand is not None:
    for tid, g in stand.groupby("team_id"):
        d = dict(zip(g.stat_name, g.display_value))
        v = dict(zip(g.stat_name, g.value))
        recs[int(tid)] = {"rec": d.get("overall"), "seed": int(v["playoffSeed"]) if pd.notna(v.get("playoffSeed")) else None,
                          "home": d.get("Home"), "road": d.get("Road"), "l10": d.get("Last Ten Games"),
                          "conf": d.get("vs. Conf."), "div": d.get("vs. Div."), "strk": d.get("streak"),
                          "gb": d.get("gamesBehind"), "w": int(v.get("wins") or 0), "l": int(v.get("losses") or 0)}

# ---------------------------------------------------------------- lineups
def slot_order(pids, P):
    """Order five players PG, SG, SF, PF, C by size and role."""
    def big(pid):
        p = P[pid]
        pw = {"G": 0, "PG": 0, "SG": 0, "F": 1, "SF": 1, "PF": 1.4, "C": 2}.get(p["pos"], 1)
        s = season_line.get(pid)
        if not s and yearly.get(pid):
            r = yearly[pid][-1]
            s = {"reb": r[6], "ast": r[7]}
        s = s or {}
        return pw * 100 + p["_h"] + 0.6 * (s.get("reb") or 0) - 0.9 * (s.get("ast") or 0)
    order = sorted(pids, key=big)
    # The point guard is the best passer among the smaller three, not simply the smallest.
    def ast(pid):
        v = season_line.get(pid)
        if v:
            return v.get("ast") or 0
        return (yearly.get(pid) or [[0] * 8])[-1][7] or 0
    small = order[:3]
    pg = max(small, key=ast)
    return [pg] + [x for x in order if x != pg]


def pick_projection(ids, P):
    """Best five by last season's starts, with a sane positional mix."""
    def role(pid):
        """(start share, minutes, points) when he played, from last season, or from
        the last season he played if he missed it or barely played (injury)."""
        s = season_line.get(pid)
        hist = yearly.get(pid) or []
        prev = None
        for r in reversed(hist):
            if int("20" + r[0][-2:]) < stats_season and int("20" + r[0][-2:]) >= stats_season - 2 and r[2] >= 20:
                prev = r
                break
        if s and s["gp"] >= 10:
            return s["gs"] / s["gp"], s["min"] or 0, s["pts"] or 0
        if prev:
            return prev[3] / prev[2], prev[4] or 0, prev[5] or 0
        if s:
            return s["gs"] / max(s["gp"], 1), s["min"] or 0, s["pts"] or 0
        if hist and int("20" + hist[-1][0][-2:]) >= stats_season - 2:
            r = hist[-1]
            return r[3] / max(r[2], 1), r[4] or 0, r[5] or 0
        return None

    def score(pid):
        # Judge the role he holds when healthy, not how many games he was
        # available for: a star who missed half a season is still a starter.
        r = role(pid)
        if not r:
            return 0
        share, mpg, ppg = r
        return share * 60 + mpg + ppg * 0.8

    ranked = sorted(ids, key=score, reverse=True)
    five = []
    for pid in ranked:
        pos = P[pid]["pos"]
        guards = sum(P[x]["pos"] == "G" for x in five)
        bigs = sum(P[x]["pos"] == "C" for x in five)
        if pos == "G" and guards >= 3:
            continue
        if pos == "C" and bigs >= 2:
            continue
        five.append(pid)
        if len(five) == 5:
            break
    # Every lineup needs a center. If none made it, bring in the best center with a
    # real role (a regular starter) in place of the lowest ranked non center.
    if five and not any(P[x]["pos"] == "C" for x in five):
        bigs = [x for x in ranked if P[x]["pos"] == "C" and x not in five and (role(x) or (0,))[0] >= .5]
        if bigs:
            out = min((x for x in five), key=score)
            five = [bigs[0] if x == out else x for x in five]
    return five, ranked


# ---------------------------------------------------------------- shots
# ESPN shot locations: x runs 0 to 50 across the court, y is feet out from the
# rim. Checked against made shots: a 23.5 ft arc plus the corner rule matches the
# official 2 or 3 on 99.9% of them.
HEX_R = 1.3
HEX_W = 3 ** 0.5 * HEX_R
ZONES = ["At the rim", "Paint", "Midrange", "Corner 3", "Above break 3"]


def hex_of(x, y):
    """Nearest pointy top hex center; returns an int key col + row * 100."""
    yb = y + 5.25  # measured from the baseline
    best = None
    r0 = int(round(yb / (1.5 * HEX_R)))
    for row in (r0 - 1, r0, r0 + 1):
        off = (row % 2) * HEX_W / 2
        col = int(round((x - off) / HEX_W))
        cx, cy = col * HEX_W + off, row * 1.5 * HEX_R
        d = (x - cx) ** 2 + (yb - cy) ** 2
        if best is None or d < best[0]:
            best = (d, col, row)
    return best[1] + best[2] * 100


shots = get("espn_nba_shots", f"shots_{stats_season}.parquet")
shot_players, shot_teams, shot_def, league_zones = {}, {}, {}, None
if shots is not None and not shots.empty:
    import numpy as np
    reg_games = set(box.loc[box.season_type == 2, "game_id"].astype(str))
    sh = shots[shots.game_id.astype(str).isin(reg_games)].copy()
    sh = sh[~sh.type_text.fillna("").str.contains("Free Throw")]
    sh = sh.dropna(subset=["coordinate_x_raw", "coordinate_y_raw", "athlete_id_1"])
    x, y = sh.coordinate_x_raw.astype(float), sh.coordinate_y_raw.astype(float)
    dist = np.hypot(x - 25, y)
    three = (dist >= 23.5) | ((y < 9) & ((x - 25).abs() >= 21.5))
    sh["made"] = sh.scoring_play.astype(bool).astype(int)
    sh["zone"] = np.select(
        [three & (y < 9), three, dist < 4, ((x - 25).abs() < 8) & (y <= 13.75)],
        [3, 4, 0, 1], default=2)
    keep = (y + 5.25) <= 36
    sh["hex"] = [hex_of(a, b) if k else -1 for a, b, k in zip(x, y, keep)]
    sh["pid"] = pd.to_numeric(sh.athlete_id_1, errors="coerce").astype("Int64")
    sh["tid"] = pd.to_numeric(sh.team_id, errors="coerce").astype("Int64")
    # opponent of the shooting team, for team defense charts
    opp = tbox[["game_id", "team_id", "opponent_team_id"]].copy()
    opp["game_id"] = opp.game_id.astype(str)
    opp_map = {(g, int(t)): int(o) for g, t, o in opp.itertuples(index=False)}
    sh["def"] = [opp_map.get((str(g), int(t))) if pd.notna(t) else None for g, t in zip(sh.game_id, sh.tid)]

    ALPH = [chr(c) for c in range(40, 127) if c != 92]

    def dots(g):
        g = g[(g.coordinate_y_raw >= -5) & (g.coordinate_y_raw <= 36)]
        xi = g.coordinate_x_raw.round().clip(0, 50).astype(int)
        yi = (g.coordinate_y_raw.round() + 5).astype(int)
        return "".join(ALPH[a] + ALPH[b * 2 + m] for a, b, m in zip(xi, yi, g.made))

    def pack(g):
        z = g.groupby("zone").made.agg(["size", "sum"]).reindex(range(5), fill_value=0)
        hx = g[g.hex >= 0].groupby("hex").made.agg(["size", "sum"])
        return {"n": int(len(g)), "z": [[int(a), int(b)] for a, b in z.values],
                "h": [[int(k), int(a), int(b)] for k, (a, b) in hx.iterrows()]}

    lz = sh.groupby("zone").made.agg(["size", "sum"]).reindex(range(5), fill_value=0)
    league_zones = [[int(a), int(b)] for a, b in lz.values]
    roster_ids = set(int(i) for i in rost.athlete_id.dropna())
    for pid, g in sh[sh.pid.isin(roster_ids)].groupby("pid"):
        shot_players[int(pid)] = pack(g)
        shot_players[int(pid)]["d"] = dots(g)
    for tid, g in sh.groupby("tid"):
        shot_teams[int(tid)] = pack(g)
    for tid, g in sh.dropna(subset=["def"]).groupby("def"):
        shot_def[int(tid)] = pack(g)
    print(f"shots: {len(sh)} located attempts, {len(shot_players)} players")

# ---------------------------------------------------------------- NBA 2K ratings
# 2K publishes no ratings feed, and no public dataset of 2K27 ratings exists the
# way one did for Madden. The ratings come from 2KRatings.com team pages: 30
# requests, once or twice a day. If the site blocks or changes layout, the last
# good copy in nba2k.json is used and the build carries on.
TWOK_SLUGS = {
    "ATL": "atlanta-hawks", "BOS": "boston-celtics", "BKN": "brooklyn-nets", "CHA": "charlotte-hornets",
    "CHI": "chicago-bulls", "CLE": "cleveland-cavaliers", "DAL": "dallas-mavericks", "DEN": "denver-nuggets",
    "DET": "detroit-pistons", "GS": "golden-state-warriors", "HOU": "houston-rockets", "IND": "indiana-pacers",
    "LAC": "los-angeles-clippers", "LAL": "los-angeles-lakers", "MEM": "memphis-grizzlies", "MIA": "miami-heat",
    "MIL": "milwaukee-bucks", "MIN": "minnesota-timberwolves", "NO": "new-orleans-pelicans", "NY": "new-york-knicks",
    "OKC": "oklahoma-city-thunder", "ORL": "orlando-magic", "PHI": "philadelphia-76ers", "PHX": "phoenix-suns",
    "POR": "portland-trail-blazers", "SAC": "sacramento-kings", "SA": "san-antonio-spurs", "TOR": "toronto-raptors",
    "UTAH": "utah-jazz", "WSH": "washington-wizards"}
POS_SLUG = {"point-guard": "PG", "shooting-guard": "SG", "small-forward": "SF", "power-forward": "PF", "center": "C"}
TWOK_CACHE, TWOK_HISTORY = "nba2k.json", "nba2k_history.json"


def norm_name(n):
    import unicodedata
    n = unicodedata.normalize("NFKD", str(n)).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[.'\u2019`-]", "", n)
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


TWOK_EDITIONS = {}  # player name -> {"2K25": 88, ...}
TWOK_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
TWOK_PLAYERS = "nba2k_players.json"
ATTR_GROUPS = ["Outside Scoring", "Inside Scoring", "Athleticism", "Playmaking", "Defense", "Defending", "Rebounding"]
ATTR_NAMES = ["Close Shot", "Mid-Range Shot", "Three-Point Shot", "Free Throw", "Shot IQ", "Offensive Consistency",
              "Layup", "Standing Dunk", "Driving Dunk", "Post Hook", "Post Fade", "Post Control", "Draw Foul", "Hands",
              "Speed", "Agility", "Strength", "Vertical", "Stamina", "Hustle", "Overall Durability",
              "Pass Accuracy", "Ball Handle", "Speed with Ball", "Pass IQ", "Pass Vision",
              "Interior Defense", "Perimeter Defense", "Steal", "Block", "Help Defense IQ", "Pass Perception",
              "Defensive Consistency", "Offensive Rebound", "Defensive Rebound", "Intangibles", "Potential"]
ATTR_KEY = {n.lower(): n for n in ATTR_NAMES}


def get_page(url, route):
    """Fetch a 2KRatings page directly, or through r.jina.ai (which fetches it
    from its own servers and hands back the HTML), since the site refuses GitHub."""
    if route == "direct":
        req = urllib.request.Request(url, headers={"User-Agent": TWOK_UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"})
    else:
        req = urllib.request.Request("https://r.jina.ai/" + url, headers={
            "User-Agent": "NBA-Rosters personal app", "X-Return-Format": "html", "X-Timeout": "30"})
    with urllib.request.urlopen(req, timeout=70) as r:
        return r.read().decode("utf-8", "ignore")


def parse_2k_html(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    team = None
    m = re.search(r"T(\d)\s*TIER\s*(\d+)\s*OVR\s*(\d+)\s*INS\s*(\d+)\s*OUT\s*(\d+)\s*ATH\s*(\d+)\s*PLA\s*(\d+)\s*DEF\s*(\d+)\s*REB\s*(\d+)\s*INT", text)
    if m:
        team = dict(zip(["tier", "ovr", "ins", "out", "ath", "pla", "def", "reb", "int"], [int(x) for x in m.groups()]))
    players = []
    for tr in soup.find_all("tr"):
        pos_links = [a for a in tr.find_all("a", href=True) if "/lists/" in a["href"] and a["href"].rstrip("/").split("/")[-1] in POS_SLUG]
        if not pos_links:
            continue  # history tables and team lists have no position links
        tds = tr.find_all("td")
        nums = []
        for td in reversed(tds):
            t = td.get_text(strip=True)
            if re.fullmatch(r"\d{2}|--", t):
                nums.insert(0, None if t == "--" else int(t))
            else:
                break
        if not nums or nums[0] is None:
            continue
        name, slug = None, None
        for a in tr.find_all("a", href=True):
            t = a.get_text(strip=True)
            if t and not any(k in a["href"] for k in ("/lists/", "/countries/", "/teams/")):
                name, slug = t, a["href"].rstrip("/").split("/")[-1]
                break
        if not name:
            continue
        cell = " ".join(td.get_text(" ", strip=True) for td in tds[:-len(nums)])
        parts = [p.strip() for p in cell.split("|")]
        arch = parts[-1] if len(parts) >= 3 else None
        if arch and re.search(r"\d'\d", arch):
            arch = None
        b = re.search(r"(\d+)\s+" + re.escape(name), cell)
        players.append({"name": name, "slug": slug, "ovr": nums[0],
                        "tpt": nums[1] if len(nums) > 1 else None, "dnk": nums[2] if len(nums) > 2 else None,
                        "pos": "/".join(POS_SLUG[a["href"].rstrip("/").split("/")[-1]] for a in pos_links[:2]),
                        "arch": arch, "badges": int(b.group(1)) if b else None,
                        "star": bool(tr.find("img", src=re.compile("all-star"))) or "all-star" in str(tr)})
    prev = {}
    # Every past edition table on the page ("NBA 2K26", "NBA 2K25", ...): the
    # players on this team that year and their overall rating in that game.
    for h in soup.find_all(string=re.compile(r"^\s*NBA 2K(\d\d)\s*$")):
        ed = "2K" + re.search(r"2K(\d\d)", h).group(1)
        tbl = h.find_next("table")
        for tr in tbl.find_all("tr") if tbl else []:
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(tds) >= 3 and tds[2].isdigit():
                TWOK_EDITIONS.setdefault(tds[1], {})[ed] = int(tds[2])
                if ed == "2K26":
                    prev[tds[1]] = int(tds[2])
    return team, players, prev


def parse_2k_player_html(html):
    """Archetype, every attribute, the category scores and the badges from a player page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = {"arch": None, "groups": {}, "attrs": {}, "badges": []}
    for p in soup.find_all("p"):
        t = p.get_text(" ", strip=True)
        if t.startswith("Archetype:"):
            sp = p.find("span")
            out["arch"] = (sp.get_text(strip=True) if sp else t.split(":", 1)[1].strip()) or None
            break
    for el in soup.find_all(["h4", "h5", "li", "div", "span", "p"]):
        if el.find(["li", "ul", "h4", "div"]):
            continue  # leaf elements only
        t = el.get_text(" ", strip=True)
        m = re.match(r"^(\d{2})\s*(?:[+-]\d+)?\s+([A-Za-z][A-Za-z -]+?)\s*$", t) or re.match(r"^([A-Za-z][A-Za-z -]+?)\s+(\d{2})\s*(?:[+-]\d+)?$", t)
        if not m:
            continue
        a, b = m.groups()
        val, label = (int(a), b) if a.isdigit() else (int(b), a)
        key = label.strip().lower()
        if key in ATTR_KEY:
            out["attrs"].setdefault(ATTR_KEY[key], val)
        elif label.strip() in ATTR_GROUPS:
            out["groups"].setdefault("Defense" if label.strip() == "Defending" else label.strip(), val)
    for card in soup.select("div.badge-card, .badge-card"):
        img = card.find("img")
        if not img:
            continue
        name = (img.get("title") or img.get("alt") or "").strip()
        srcs = " ".join(filter(None, [img.get("data-src"), img.get("src"), " ".join(card.get("class") or [])])).lower()
        tier = next((t for t, k in (("Legend", "legend"), ("Hall of Fame", "hall-of-fame"), ("Hall of Fame", "hof"),
                                   ("Gold", "gold"), ("Silver", "silver"), ("Bronze", "bronze")) if k in srcs), None)
        if name and all(b[0] != name for b in out["badges"]):  # each badge appears twice on the page
            out["badges"].append([name, tier])
    return out


def dedupe_badges(bd):
    seen, out = set(), []
    for name, tier in bd or []:
        if name not in seen:
            seen.add(name)
            out.append([name, tier])
    return out


def load_2k():
    import time
    teams_2k, players_2k, prev_2k, ok, fails = {}, [], {}, 0, 0
    try:
        import bs4  # noqa
    except ImportError:
        print("2k   beautifulsoup4 missing, using cached ratings")
        return None
    route = "direct"
    for abbr, slug in TWOK_SLUGS.items():
        tm = pl = pv = None
        for attempt in (["direct", "reader"] if route == "direct" else ["reader"]):
            try:
                tm, pl, pv = parse_2k_html(get_page(f"https://www.2kratings.com/teams/{slug}", attempt))
                if pl:
                    if attempt != route:
                        print("2k   direct route refused, switching to the reader route")
                    route = attempt
                    break
            except Exception as e:
                print(f"2k   {slug} ({attempt}): {e.__class__.__name__} {getattr(e, 'code', '')}")
            if attempt == "direct":
                route = "reader"  # stop knocking on a door that is closed
        if pl:
            ok += 1
            teams_2k[abbr] = tm
            for p in pl:
                p["team"] = abbr
            players_2k += pl
            prev_2k.update(pv or {})
        else:
            fails += 1
            if ok == 0 and fails >= 3:
                print("2k   first three team pages failed on both routes, not trying the rest")
                break
        time.sleep(3.2 if route == "reader" else 1.5)
    if ok < 25:
        print(f"2k   only {ok} of 30 team pages parsed, using cached ratings")
        return None
    print(f"2k   {len(players_2k)} players from {ok} teams via the {route} route, "
          f"{sum(1 for p in players_2k if p['arch'])} with archetypes, team ratings for {sum(1 for t in teams_2k.values() if t)}")

    # Player pages: every attribute, category scores and badges. About 540 pages, so
    # they are cached and refreshed in a rolling way: missing ones first, then any
    # whose overall moved, then anything older than a week, up to MAX per run.
    import os
    cache = json.load(open(TWOK_PLAYERS)) if os.path.exists(TWOK_PLAYERS) else {}
    today = dt.date.today()
    MAX = int(os.environ.get("TWOK_PLAYER_PAGES", "600"))

    def stale(p):
        c = cache.get(p["slug"])
        if not c or not c.get("attrs"):
            return 0
        if c.get("ovr") != p["ovr"]:
            return 1
        age = (today - dt.date.fromisoformat(c["fetched"])).days
        return 2 if age >= 7 else None

    todo = sorted((p for p in players_2k if p.get("slug") and stale(p) is not None), key=stale)[:MAX]
    got = bad = 0
    started = time.time()
    for p in todo:
        if time.time() - started > 75 * 60:
            print("2k   player pages: time budget reached, the rest continue next run")
            break
        try:
            d = parse_2k_player_html(get_page(f"https://www.2kratings.com/{p['slug']}", route))
            if d["attrs"]:
                cache[p["slug"]] = dict(d, ovr=p["ovr"], fetched=today.isoformat())
                got += 1
            else:
                bad += 1
        except Exception as e:
            bad += 1
            print(f"2k   {p['slug']}: {e.__class__.__name__} {getattr(e, 'code', '')}")
            if got == 0 and bad >= 5:
                print("2k   player pages are not coming through, stopping for this run")
                break
        time.sleep(3.2 if route == "reader" else 1.0)
    json.dump(cache, open(TWOK_PLAYERS, "w"), ensure_ascii=False)
    print(f"2k   player pages: {got} refreshed, {bad} failed, {sum(1 for v in cache.values() if v.get('attrs'))} cached with full attributes")

    data = {"fetched": today.isoformat(), "teams": teams_2k, "players": players_2k, "prev": prev_2k, "editions": TWOK_EDITIONS}
    json.dump(data, open(TWOK_CACHE, "w"), ensure_ascii=False)
    return data


twok = load_2k()
if twok is None:
    try:
        twok = json.load(open(TWOK_CACHE))
        print(f"2k   cached copy from {twok.get('fetched')}")
    except Exception:
        twok = None
        print("2k   no ratings available yet")

player_2k, team_2k, twok_meta = {}, {}, None
try:
    pages_2k = json.load(open(TWOK_PLAYERS))
except Exception:
    pages_2k = {}
if twok:
    import os
    hist = json.load(open(TWOK_HISTORY)) if os.path.exists(TWOK_HISTORY) else {}
    by_team_name = {}
    for r in rost.itertuples():
        by_team_name[(r.team_abbreviation, norm_name(r.display_name))] = int(r.athlete_id)
    by_name = {}
    for (t, n), pid in by_team_name.items():
        by_name.setdefault(n, []).append(pid)
    rated = []
    for p in twok["players"]:
        n = norm_name(p["name"])
        pid = by_team_name.get((p["team"], n)) or (by_name[n][0] if len(by_name.get(n, [])) == 1 else None)
        if pid:
            rated.append((pid, p))
    all_ovr = sorted((p["ovr"] for _, p in rated), reverse=True)
    pos_pool = {}
    for pid, p in rated:
        pos_pool.setdefault(p["pos"].split("/")[0], []).append(p["ovr"])
    for v in pos_pool.values():
        v.sort(reverse=True)
    for pid, p in rated:
        prim = p["pos"].split("/")[0]
        h = hist.setdefault(str(pid), [])
        if not h or h[-1][1] != p["ovr"]:
            h.append([twok["fetched"], p["ovr"]])
        h[:] = h[-12:]
        move = h[-1][1] - h[-2][1] if len(h) > 1 else None
        prev = twok["prev"].get(p["name"])
        det = pages_2k.get(p.get("slug") or "", {})
        eds = (twok.get("editions") or {}).get(p["name"]) or {}
        player_2k[pid] = {"o": p["ovr"], "a": p["arch"] or det.get("arch"), "p": p["pos"], "b": p["badges"] if p["badges"] is not None else (len(dedupe_badges(det.get("badges"))) or None), "s": p["star"],
                          "g": det.get("groups") or None, "at": det.get("attrs") or None, "bd": dedupe_badges(det.get("badges")) or None,
                          "eds": dict(sorted(eds.items())) or None,
                          "t3": p["tpt"], "dk": p["dnk"], "rk": all_ovr.index(p["ovr"]) + 1,
                          "prk": [pos_pool[prim].index(p["ovr"]) + 1, len(pos_pool[prim]), prim],
                          "last": prev, "mv": move, "mvd": h[-2][0] if len(h) > 1 else None}
    json.dump(hist, open(TWOK_HISTORY, "w"))
    tovr = sorted(((a, t["ovr"]) for a, t in twok["teams"].items() if t), key=lambda x: -x[1])
    for i, (a, o) in enumerate(tovr, 1):
        team_2k[a] = dict(twok["teams"][a], rk=i)
    twok_meta = {"date": twok["fetched"], "n": len(rated)}
    print(f"2k   matched {len(rated)} of {len(twok['players'])} rated players to rosters")

# ---------------------------------------------------------------- salaries and cap
# Player contracts come from Basketball-Reference's contracts page: one request,
# every contract, updated as deals are signed. Cap thresholds are set by the
# league each July and are listed here by season; add a line each summer.
CAP_RULES = {
    # season: cap, tax line, first apron, second apron, minimum team salary
    "2026-27": dict(cap=164_961_000, tax=200_428_000, a1=209_015_000, a2=221_686_000, floor=148_465_000),
}
BBR_TEAM = {"BRK": "BKN", "CHO": "CHA", "GSW": "GS", "NOP": "NO", "NYK": "NY", "PHO": "PHX",
            "SAS": "SA", "UTA": "UTAH", "WAS": "WSH"}
SAL_CACHE = "salaries.json"


def fetch_contracts():
    from bs4 import BeautifulSoup, Comment
    req = urllib.request.Request("https://www.basketball-reference.com/contracts/players.html", headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
        "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "ignore")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="player-contracts")
    if table is None:  # sometimes shipped inside an HTML comment
        for c in soup.find_all(string=lambda t: isinstance(t, Comment) and "player-contracts" in t):
            table = BeautifulSoup(c, "html.parser").find("table", id="player-contracts")
            if table:
                break
    if table is None:
        raise ValueError("contracts table not found")
    seasons = [h.get_text(strip=True) for h in table.find("thead").find_all("tr")[-1].find_all(["th", "td"])]
    seasons = [x for x in seasons if re.fullmatch(r"\d{4}-\d{2}", x)]
    cap = re.search(r"Salary Cap:\s*\$([\d,]+)", soup.get_text(" "))
    rows = []
    for tr in table.find("tbody").find_all("tr"):
        if "thead" in (tr.get("class") or []):
            continue
        a = tr.find("a", href=re.compile(r"/players/"))
        tm = tr.find("a", href=re.compile(r"/contracts/[A-Z]{3}\.html"))
        if not a or not tm:
            continue
        money = [td for td in tr.find_all("td") if td.get("data-stat", "").startswith("y")]
        if not money:  # fall back to position: the season columns follow the team cell
            tds = tr.find_all("td")
            i = next(k for k, td in enumerate(tds) if td.find("a", href=re.compile(r"/contracts/")))
            money = tds[i + 1:i + 1 + len(seasons)]
        years = []
        for td in money[:len(seasons)]:
            t = td.get_text(strip=True).replace("$", "").replace(",", "")
            cls = " ".join(td.get("class") or [])
            opt = "P" if "salary-pl" in cls else "T" if "salary-tm" in cls else "E" if "salary-et" in cls else ""
            years.append([int(t) if t.isdigit() else None, opt])
        g = tr.find("td", attrs={"data-stat": "remain_gtd"})
        gt = g.get_text(strip=True).replace("$", "").replace(",", "") if g else ""
        code = tm.get_text(strip=True)
        rows.append({"name": a.get_text(strip=True), "bbr": a["href"].split("/")[-1].replace(".html", ""),
                     "team": BBR_TEAM.get(code, code), "years": years, "gtd": int(gt) if gt.isdigit() else None})
    if len(rows) < 300:
        raise ValueError(f"only {len(rows)} contracts parsed")
    return {"fetched": dt.date.today().isoformat(), "seasons": seasons,
            "cap": int(cap.group(1).replace(",", "")) if cap else None, "rows": rows}


def load_contracts():
    try:
        data = fetch_contracts()
        json.dump(data, open(SAL_CACHE, "w"), ensure_ascii=False)
        print(f"sal  {len(data['rows'])} contracts, seasons {data['seasons'][0]} to {data['seasons'][-1]}")
        return data
    except Exception as e:
        print(f"sal  contracts page failed: {e.__class__.__name__} {getattr(e, 'code', '') or e}")
    try:
        data = json.load(open(SAL_CACHE))
        print(f"sal  cached copy from {data.get('fetched')}")
        return data
    except Exception:
        print("sal  no salary data available yet")
        return None


sal = load_contracts()
player_sal, team_sal, cap_meta = {}, {}, None
if sal:
    season0 = sal["seasons"][0]
    rules = dict(CAP_RULES.get(season0, {}))
    if sal.get("cap"):
        rules["cap"] = sal["cap"]
    by_tn, by_n = {}, {}
    for r in rost.itertuples():
        by_tn[(r.team_abbreviation, norm_name(r.display_name))] = int(r.athlete_id)
        by_n.setdefault(norm_name(r.display_name), []).append((int(r.athlete_id), r.team_abbreviation))
    cur_team = {int(r.athlete_id): r.team_abbreviation for r in rost.itertuples()}
    rows_per_player = {}
    for r in sal["rows"]:
        rows_per_player[r["bbr"]] = rows_per_player.get(r["bbr"], 0) + 1
    for r in sal["rows"]:
        n = norm_name(r["name"])
        pid = by_tn.get((r["team"], n))
        dead = False
        if pid is None and len(by_n.get(n, [])) == 1:
            pid, t = by_n[n][0]
            dead = t != r["team"]  # he is owed money by a team he no longer plays for
        y0 = r["years"][0][0] if r["years"] else None
        ts = team_sal.setdefault(r["team"], {"total": 0, "next": 0, "dead": [], "camp": [], "n": 0})
        if (pid is None or dead) and not (r["gtd"] or 0):
            # Not on this team's roster and nothing guaranteed: a training camp or
            # exhibit deal (or a signing ESPN has not added yet). Money with no
            # guarantee cannot be dead money, so it is listed, not counted.
            if y0:
                ts["camp"].append([r["name"], y0])
            continue
        if y0:
            ts["total"] += y0
        if len(r["years"]) > 1 and r["years"][1][0]:
            ts["next"] += r["years"][1][0]
        if pid is None or dead:
            if y0:
                if dead and rows_per_player.get(r["bbr"], 0) == 1:
                    # His only contract is with this team, so he is on it: a trade or
                    # signing ESPN's roster file has not caught up with yet.
                    ts.setdefault("new", []).append([r["name"], y0])
                else:
                    ts["dead"].append([r["name"], y0])  # waived, still being paid
            continue
        ts["n"] += 1
        player_sal[pid] = {"y": r["years"], "g": r["gtd"], "bbr": r["bbr"]}
    ranked = sorted((v["y"][0][0] for v in player_sal.values() if v["y"] and v["y"][0][0]), reverse=True)
    for v in player_sal.values():
        if v["y"] and v["y"][0][0]:
            v["rk"] = ranked.index(v["y"][0][0]) + 1
    order = sorted(team_sal, key=lambda t: -team_sal[t]["total"])
    for i, t in enumerate(order, 1):
        team_sal[t]["rk"] = i
    cap_meta = {"date": sal["fetched"], "seasons": sal["seasons"], "rules": rules, "n": len(ranked)}
    print(f"sal  matched {len(player_sal)} rostered players, {sum(len(v['dead']) for v in team_sal.values())} dead money lines")

# ---------------------------------------------------------------- year by year
# One ESPN season stats file per season back to 2001-02, published by
# sportsdataverse next to the box scores. One row per player per season; for a
# player traded midseason ESPN lists the team he finished with.
OLD_TEAMS = {"Seattle SuperSonics": "SEA", "New Jersey Nets": "NJ", "Charlotte Bobcats": "CHA",
             "New Orleans Hornets": "NOH", "New Orleans/Oklahoma City Hornets": "NOK",
             "Vancouver Grizzlies": "VAN", "Washington Bullets": "WSB"}
id_abbr = {int(r.team_id): r.team_abbreviation for r in rost.drop_duplicates("team_id").itertuples()}
roster_ids = set(int(i) for i in rost.athlete_id.dropna())
yearly = {}
top_scorer = {}


def f_or_none(v):
    try:
        v = float(v)
        return None if v != v else v
    except Exception:
        return None


for yr in range(2002, stats_season + 1):
    df = get("espn_nba_player_season_stats", f"player_season_stats_{yr}.parquet")
    if df is None:
        continue
    df = df[df.category == "averages"]
    try:
        ap = df[df.stat_name == "avgPoints"].copy()
        gp = df[df.stat_name == "gamesPlayed"].set_index("athlete_id").value
        ap["gp"] = ap.athlete_id.map(gp)
        ap = ap[ap.gp.fillna(0) >= 20]
        for tid_, g_ in ap.groupby("team_id"):
            r_ = g_.sort_values("value", ascending=False).iloc[0]
            top_scorer.setdefault(int(tid_), {})[yr] = [r_.athlete_display_name, round(float(r_.value), 1)]
    except Exception:
        pass
    df = df[pd.to_numeric(df.athlete_id, errors="coerce").isin(roster_ids)]
    for (aid, tid, tname), g in df.groupby(["athlete_id", "team_id", "team_display_name"], dropna=False):
        v = dict(zip(g.stat_name, g.value))
        dv = dict(zip(g.stat_name, g.display_value))
        gp = f_or_none(v.get("gamesPlayed"))
        if not gp:
            continue
        tpm = None
        m = re.match(r"([\d.]+)-", str(dv.get("avgThreePointFieldGoalsMade-avgThreePointFieldGoalsAttempted", "")))
        if m:
            tpm = float(m.group(1))
        try:
            ab = OLD_TEAMS.get(tname) or id_abbr.get(int(tid)) or ""
        except Exception:
            ab = ""
        row = [f"{str(yr - 1)[-2:]}-{str(yr)[-2:]}", ab, int(gp), int(f_or_none(v.get("gamesStarted")) or 0)]
        row += [num(f_or_none(v.get(k))) for k in ("avgMinutes", "avgPoints", "avgRebounds", "avgAssists",
                                                   "avgSteals", "avgBlocks", "avgTurnovers")]
        row += [num(tpm)] + [num(f_or_none(v.get(k))) for k in ("fieldGoalPct", "threePointFieldGoalPct", "freeThrowPct")]
        yearly.setdefault(int(aid), []).append(row)
print(f"yrs  {len(yearly)} rostered players with season history")

# ---------------------------------------------------------------- coaches
# Head coaches and their records come from Wikipedia's list of current NBA head
# coaches; assistants from each team's roster template on Wikipedia. Editors keep
# both current within hours of a hire. Last good copy is kept in coaches.json.
import urllib.parse
WIKI_UA = {"User-Agent": "NBA-Rosters personal app (https://github.com/elisha11230) python-urllib"}
COACH_CACHE = "coaches.json"


def wiki_json(params):
    q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(f"https://en.wikipedia.org/w/api.php?{q}&format=json&formatversion=2", headers=WIKI_UA)
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read())


def wiki_names(txt):
    txt = re.sub(r"\{\{\s*sortname\s*\|([^|}]*)\|([^|}]*)[^}]*\}\}", lambda m: f"{m.group(1).strip()} {m.group(2).strip()}", txt, flags=re.I)
    txt = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", txt)
    txt = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", txt, flags=re.S)
    txt = re.sub(r"\{\{[^{}]*\}\}", "", txt)
    out = []
    for p in re.split(r"<br\s*/?>|\n|\*|;|,", txt):
        p = re.sub(r"<[^>]+>|'{2,3}|\(.*?\)", "", p).strip(" :-\u2013")
        if p and len(p) < 40 and re.search(r"[A-Za-z]", p):
            out.append(p)
    return out


def fetch_coaches():
    from bs4 import BeautifulSoup
    html = wiki_json({"action": "parse", "page": "List_of_current_NBA_head_coaches", "prop": "text"})["parse"]["text"]
    soup = BeautifulSoup(html, "html.parser")
    team_rows = list(rost.drop_duplicates("team_id").itertuples())
    nick = {}
    for r in team_rows:
        name = r.team_display_name
        nick["Trail Blazers" if name.endswith("Trail Blazers") else name.split(" ")[-1]] = r.team_abbreviation
    heads = {}
    for tr in soup.select("table.wikitable tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 14:
            continue
        team = next((a for k, a in nick.items() if k in cells[0]), None)
        if not team:
            continue
        ints = [int(x.replace(",", "")) for x in cells[6:] if re.fullmatch(r"[\d,]+", x)]
        heads[team] = {"name": cells[2].rstrip("*").strip(), "since": cells[5], "first": cells[2].endswith("*"),
                       "team": ints[0:3] if len(ints) >= 3 else None, "career": ints[3:6] if len(ints) >= 6 else None}
    if len(heads) < 28:
        raise ValueError(f"only {len(heads)} head coaches parsed")
    for abbr in heads:
        full = next(r.team_display_name for r in team_rows if r.team_abbreviation == abbr)
        full = full.replace("LA Clippers", "Los Angeles Clippers")
        staff = []
        for title in (f"Template:{full} roster", f"Template:{full} current roster"):
            try:
                wt = wiki_json({"action": "parse", "page": title, "prop": "wikitext"}).get("parse", {}).get("wikitext", "")
            except Exception:
                wt = ""
            m = re.search(r"\|\s*(?:asst|assistant)[ _]coach(?:es)?\s*=\s*(.*?)(?=\n\s*\|\s*[a-z _]+=|\n\}\})", wt, re.S | re.I)
            if m:
                staff = [n for n in wiki_names(m.group(1)) if n != heads[abbr]["name"]]
                break
        heads[abbr]["asst"] = staff[:14]
    return {"fetched": dt.date.today().isoformat(), "teams": heads}


coaches = None
try:
    coaches = fetch_coaches()
    json.dump(coaches, open(COACH_CACHE, "w"), ensure_ascii=False)
    print(f"coach {len(coaches['teams'])} head coaches, {sum(1 for t in coaches['teams'].values() if t['asst'])} teams with assistants")
except Exception as e:
    print(f"coach fetch failed: {e.__class__.__name__} {getattr(e, 'code', '') or e}")
    try:
        coaches = json.load(open(COACH_CACHE))
        print(f"coach cached copy from {coaches.get('fetched')}")
    except Exception:
        print("coach no coaching data available yet")

# ---------------------------------------------------------------- league leaders
# Built from every box score, so players no longer in the league still count.
# Qualifying rules follow the NBA's: per game leaders need 70% of games, and the
# shooting leaders need a minimum number of makes (scaled early in a season).
def build_leaders(src, playoffs=False, top=25):
    g = src[(src.did_not_play != True) & (src.minutes.fillna(0) > 0)].copy()
    if g.empty:
        return None
    if not playoffs:
        tg_ = g.groupby("team_abbreviation").game_id.nunique()
        g = g[g.team_abbreviation.isin(tg_[tg_ >= 20].index)]
        if g.empty:
            return None
    g["pm"] = pd.to_numeric(g.plus_minus.astype(str).str.replace("+", "", regex=False), errors="coerce")
    g["dd"] = ((g[["points", "rebounds", "assists", "steals", "blocks"]] >= 10).sum(axis=1) >= 2).astype(int)
    a = g.groupby("athlete_id").agg(gp=("game_id", "nunique"), pm=("pm", "sum"), dd=("dd", "sum"),
                                    **{c: (c, "sum") for c in SUM})
    last = g.sort_values("game_date").groupby("athlete_id").agg(n=("athlete_display_name", "last"), t=("team_abbreviation", "last"))
    a = a.join(last)
    import math
    team_games = min(82, g.groupby("team_abbreviation").game_id.nunique().max())
    frac = team_games / 82 if not playoffs else 1
    min_gp = max(1, math.ceil(team_games * .7)) if not playoffs else max(1, math.ceil(team_games * .5))
    fgm_min, tpm_min, ftm_min = (round(300 * frac), round(82 * frac), round(125 * frac)) if not playoffs else (25, 8, 10)
    per_game = a[a.gp >= min_gp]
    cats = []

    def add(key, label, grp, series, fmt=1, asc=False, note=None):
        s = series.dropna()
        s = s.sort_values(ascending=asc).head(top)
        rows = []
        for aid, v in s.items():
            r = a.loc[aid]
            pid = int(aid)
            rows.append([pid if pid in players_all else None, r.n, r.t, round(float(v), fmt) if fmt is not None else int(v), int(r.gp), pid])
        cats.append({"k": key, "l": label, "g": grp, "note": note, "rows": rows, "fmt": fmt})

    pgn = f"{min_gp}+ games"
    add("pts", "Points", "Per game", per_game.points / per_game.gp, note=pgn)
    add("reb", "Rebounds", "Per game", per_game.rebounds / per_game.gp, note=pgn)
    add("ast", "Assists", "Per game", per_game.assists / per_game.gp, note=pgn)
    add("stl", "Steals", "Per game", per_game.steals / per_game.gp, note=pgn)
    add("blk", "Blocks", "Per game", per_game.blocks / per_game.gp, note=pgn)
    add("tpm", "Threes made", "Per game", per_game.three_point_field_goals_made / per_game.gp, note=pgn)
    add("min", "Minutes", "Per game", per_game.minutes / per_game.gp, note=pgn)
    add("pm", "Plus minus", "Per game", per_game.pm / per_game.gp, note=pgn + ", team margin while on the floor")
    q = a[a.field_goals_made >= fgm_min]
    add("fgp", "Field goal %", "Shooting", 100 * q.field_goals_made / q.field_goals_attempted, note=f"{fgm_min}+ field goals made")
    q = a[a.three_point_field_goals_made >= tpm_min]
    add("tpp", "Three point %", "Shooting", 100 * q.three_point_field_goals_made / q.three_point_field_goals_attempted, note=f"{tpm_min}+ threes made")
    q = a[a.free_throws_made >= ftm_min]
    add("ftp", "Free throw %", "Shooting", 100 * q.free_throws_made / q.free_throws_attempted, note=f"{ftm_min}+ free throws made")
    q = a[a.field_goals_made >= fgm_min]
    add("ts", "True shooting %", "Shooting", 100 * q.points / (2 * (q.field_goals_attempted + .44 * q.free_throws_attempted)),
        note=f"{fgm_min}+ field goals made. Counts threes and free throws")
    add("tpts", "Points", "Totals", a.points, fmt=None)
    add("treb", "Rebounds", "Totals", a.rebounds, fmt=None)
    add("tast", "Assists", "Totals", a.assists, fmt=None)
    add("ttpm", "Threes made", "Totals", a.three_point_field_goals_made, fmt=None)
    add("tdd", "Double doubles", "Totals", a.dd, fmt=None)
    add("tgp", "Games played", "Totals", a.gp, fmt=None)
    return cats


players_all = set(int(r.athlete_id) for r in rost.itertuples())
leaders = {"season": season_label(stats_season),
           "reg": build_leaders(box[box.season_type == 2]),
           "po": build_leaders(box[box.season_type == 3], playoffs=True)}
print(f"lead {len(leaders['reg'] or [])} regular season boards, {len(leaders['po'] or [])} playoff boards")
leaders_by = {leaders["season"]: {"reg": leaders["reg"], "po": leaders["po"]}}

# ---------------------------------------------------------------- schedule
# The full regular season schedule, published by sportsdataverse from ESPN. Scores
# fill in as games are played; the app's Scores page covers anything live.
schedule = {}
sch = get("espn_nba_schedules", f"nba_schedule_{S}.parquet")
if sch is not None and not sch.empty:
    sch = sch.sort_values("date")
    for r in sch.itertuples():
        done = str(r.status_type_state) == "post"
        for side, opp, ha in (("home", "away", ""), ("away", "home", "@")):
            tid_ = int(getattr(r, f"{side}_id"))
            me_s, op_s = getattr(r, f"{side}_score"), getattr(r, f"{opp}_score")
            schedule.setdefault(tid_, []).append([
                str(r.id), str(r.date), getattr(r, f"{opp}_abbreviation"), ha,
                (r.broadcast_name if isinstance(r.broadcast_name, str) else "") or "",
                int(me_s) if done and pd.notna(me_s) else None, int(op_s) if done and pd.notna(op_s) else None,
                int(r.season_type), r.venue_full_name if isinstance(r.venue_full_name, str) else ""])
    print(f"sched {len(sch)} games for {len(schedule)} teams")

# ---------------------------------------------------------------- team history
# Ten seasons of record, seed and playoff run from ESPN standings and box scores.
history = {}
for yr in range(2002, stats_season + 1):
    st_ = get("espn_nba_standings", f"standings_{yr}.parquet")
    tb_ = get("espn_nba_team_boxscores", f"team_box_{yr}.parquet")
    if st_ is None:
        continue
    po_ = {}
    if tb_ is not None and not tb_.empty:
        p3 = tb_[tb_.season_type == 3].copy()
        p3["won"] = p3.team_score.astype(float) > p3.opponent_team_score.astype(float)
        for tid_, g_ in p3.sort_values("game_date").groupby("team_id"):
            series = []
            for opp_, gg in g_.groupby("opponent_team_id", sort=False):
                series.append([gg.game_date.min(), gg.opponent_team_abbreviation.iloc[0], int(gg.won.sum()), int((~gg.won).sum())])
            series.sort()
            rounds = len(series)
            last = series[-1] if series else None
            champ = rounds >= 4 and last and last[2] > last[3]
            po_[int(tid_)] = {"rounds": rounds, "champ": bool(champ),
                              "last": [last[1], last[2], last[3]] if last else None}
    for tid_, g_ in st_.groupby("team_id"):
        d_ = dict(zip(g_.stat_name, g_.display_value))
        v_ = dict(zip(g_.stat_name, g_.value))
        name_ = g_.team_display_name.iloc[0]
        p_ = po_.get(int(tid_))
        rnd = ["Missed playoffs", "First round", "Conference semifinals", "Conference finals", "NBA Finals"]
        result = "Champions" if p_ and p_["champ"] else (f"Lost in {rnd[min(p_['rounds'], 4)].lower().replace('nba', 'NBA')}" if p_ and p_["rounds"] else "Missed playoffs")
        if p_ and p_["rounds"] == 1 and p_["last"] and p_["last"][1] + p_["last"][2] <= 1:
            result = "Play-in"
        history.setdefault(int(tid_), []).append({
            "s": season_label(yr), "name": name_, "w": int(v_.get("wins") or 0), "l": int(v_.get("losses") or 0),
            "seed": int(v_["playoffSeed"]) if pd.notna(v_.get("playoffSeed")) else None,
            "po": result, "vs": p_["last"] if p_ and p_["last"] else None,
            "top": top_scorer.get(int(tid_), {}).get(yr),
            "conf": "East" if "East" in str(g_.group_name.iloc[0]) else "West",
            "gb": d_.get("gamesBehind"), "l10": d_.get("Last Ten Games"), "strk": d_.get("streak"),
            "home": d_.get("Home"), "road": d_.get("Road"), "diff": d_.get("differential"), "cl": d_.get("clincher")})
print(f"hist {len(history)} teams, {sum(len(v) for v in history.values())} team seasons")

# ---------------------------------------------------------------- draft picks
# RealGM's future draft page lists every team's first and second round picks for
# the next seven drafts, protections and swaps included. One page; last good copy
# kept in draft_picks.json.
PICKS_CACHE = "draft_picks.json"
REALGM_CODES = {"SAN": "SA", "UTH": "UTAH", "BRK": "BKN", "GOS": "GS", "PHL": "PHI", "NOP": "NO", "NYK": "NY",
                "WAS": "WSH", "SAC": "SAC", "PHX": "PHX", "CHA": "CHA", "LAC": "LAC", "LAL": "LAL"}


def fetch_picks():
    from bs4 import BeautifulSoup
    html = None
    for route in ("direct", "reader"):
        try:
            html = get_page("https://basketball.realgm.com/nba/draft/future_drafts/team", route)
            if "Future NBA Draft Picks" in html:
                break
        except Exception as e:
            print(f"picks {route}: {e.__class__.__name__} {getattr(e, 'code', '')}")
            html = None
    if not html:
        raise ValueError("draft pick page not reachable")
    soup = BeautifulSoup(html, "html.parser")
    teams_ = {}
    full_to_abbr = {}
    for r in rost.drop_duplicates("team_id").itertuples():
        full_to_abbr[r.team_display_name.replace("LA Clippers", "Los Angeles Clippers").replace("Philadelphia 76ers", "Philadelphia Sixers")] = r.team_abbreviation
    fix = lambda t: re.sub(r"\b(" + "|".join(REALGM_CODES) + r")\b", lambda m: REALGM_CODES[m.group(1)], t)
    for h in soup.find_all(["h2", "h3"]):
        m = re.match(r"(.+?) Future NBA Draft Picks", h.get_text(" ", strip=True))
        if not m:
            continue
        abbr = full_to_abbr.get(m.group(1).strip())
        tbl = h.find_next("table")
        if not abbr or not tbl:
            continue
        rows = []
        for tr in tbl.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 3 or not re.match(r"\d{4}$", tds[0].get_text(strip=True)):
                continue
            out = [int(tds[0].get_text(strip=True))]
            for td in tds[1:3]:
                for br in td.find_all("br"):
                    br.replace_with("\n")
                txt = td.get_text(" ", strip=False)
                txt = re.sub(r"[ \t\xa0]+", " ", txt).strip()
                cm = re.search(r"(\d+(?:\s*\+\s*\d+)?)\s*$", txt)
                count = cm.group(1).replace(" ", "") if cm else None
                if cm:
                    txt = txt[:cm.start()].strip()
                txt = re.sub(r"\s*\n\s*", "\n", txt).strip(" ;\n").replace(" ;", ";")
                out += [fix(txt), count]
            rows.append(out)
        if rows:
            teams_[abbr] = rows
    if len(teams_) < 28:
        raise ValueError(f"only {len(teams_)} teams parsed")
    return {"fetched": dt.date.today().isoformat(), "teams": teams_}


picks = None
try:
    picks = fetch_picks()
    json.dump(picks, open(PICKS_CACHE, "w"), ensure_ascii=False)
    print(f"picks {len(picks['teams'])} teams, drafts {picks['teams'][next(iter(picks['teams']))][0][0]} on")
except Exception as e:
    print(f"picks fetch failed: {e.__class__.__name__} {e}")
    try:
        picks = json.load(open(PICKS_CACHE))
        print(f"picks cached copy from {picks.get('fetched')}")
    except Exception:
        print("picks no draft pick data available yet")

# ---------------------------------------------------------------- historical rosters
# (ESPN box scores carry each player's latest jersey number, not that season's, so
# past jersey numbers are left out.)
# Every team's roster and starting five for every season back to 2001-02, rebuilt
# from ESPN box scores: the five who started the most games, then everyone else
# by minutes. A player traded midseason appears on each team he played for.
PAST_FROM = 2002
past = {}          # team id -> {season label: {...}}
past_people = {}   # athlete id -> [name, position]
reg_games_by_year = {}
SLOT_W = {"PG": 0, "SG": 1, "G": 0.5, "SF": 2, "GF": 1.5, "F": 2.5, "PF": 3, "FC": 3.5, "C": 4}


def past_slots(rows):
    """Order five starters PG, SG, SF, PF, C from listed positions and their numbers."""
    def key(r):
        pw = SLOT_W.get(r["pos"], 2)
        return pw * 10 + 0.4 * r["reb"] - 0.6 * r["ast"]
    order = sorted(rows, key=key)
    small = order[:3]
    pg = max(small, key=lambda r: (r["pos"] == "PG", r["ast"]))
    return [pg] + [r for r in order if r is not pg]


for yr in range(PAST_FROM, stats_season + 1):
    pb = box if yr == stats_season else get("espn_nba_player_boxscores", f"player_box_{yr}.parquet")
    if pb is None or pb.empty:
        continue
    if yr != stats_season:
        try:
            leaders_by[season_label(yr)] = {"reg": build_leaders(pb[pb.season_type == 2], top=15),
                                            "po": build_leaders(pb[pb.season_type == 3], playoffs=True, top=15)}
        except Exception as e:
            print(f"lead {season_label(yr)} skipped: {e.__class__.__name__}")
    pb = pb[(pb.season_type == 2) & (pb.did_not_play != True) & (pb.minutes.fillna(0) > 0)].copy()
    reg_games_by_year[yr] = set(pb.game_id.astype(str))
    pb["athlete_id"] = pd.to_numeric(pb.athlete_id, errors="coerce")
    pb = pb.dropna(subset=["athlete_id"])
    label = season_label(yr)
    agg_ = pb.groupby(["team_id", "athlete_id"]).agg(
        gp=("game_id", "nunique"), gs=("starter", "sum"), mins=("minutes", "sum"), pts=("points", "sum"),
        reb=("rebounds", "sum"), ast=("assists", "sum"), stl=("steals", "sum"), blk=("blocks", "sum"),
        fgm=("field_goals_made", "sum"), fga=("field_goals_attempted", "sum"),
        tpm=("three_point_field_goals_made", "sum"), tpa=("three_point_field_goals_attempted", "sum"),
        ftm=("free_throws_made", "sum"), fta=("free_throws_attempted", "sum"),
        name=("athlete_display_name", "last"), pos=("athlete_position_abbreviation", "last"),
        jersey=("athlete_jersey", "last")).reset_index()
    meta_ = pb.groupby("team_id").agg(tn=("team_display_name", "last"), ta=("team_abbreviation", "last"))
    for tid_, g_ in agg_.groupby("team_id"):
        try:
            tid_i = int(tid_)
        except Exception:
            continue
        if meta_.loc[tid_, "ta"] not in TEAM_ABBRS and tid_i not in id_abbr:
            continue  # all star and exhibition teams
        rows = []
        for r in g_.itertuples():
            gp_ = int(r.gp)
            rows.append({"id": int(r.athlete_id), "gp": gp_, "gs": int(r.gs), "min": r.mins / gp_,
                         "pts": r.pts / gp_, "reb": r.reb / gp_, "ast": r.ast / gp_, "stl": r.stl / gp_, "blk": r.blk / gp_,
                         "fgp": 100 * r.fgm / r.fga if r.fga else None, "tpp": 100 * r.tpm / r.tpa if r.tpa else None,
                         "ftp": 100 * r.ftm / r.fta if r.fta else None, "tot": r.mins,
                         "pos": r.pos if isinstance(r.pos, str) else "F", "j": None if pd.isna(r.jersey) else str(r.jersey)})
            past_people[int(r.athlete_id)] = [r.name, rows[-1]["pos"]]
        starters = sorted(rows, key=lambda r: (-r["gs"], -r["tot"]))[:5]
        five = past_slots(starters)
        bench = sorted((r for r in rows if r not in five), key=lambda r: -r["tot"])
        pack = lambda r: [r["id"], r["gp"], r["gs"], num(r["min"]), num(r["pts"]), num(r["reb"]), num(r["ast"]),
                          num(r["stl"]), num(r["blk"]), num(r["fgp"]), num(r["tpp"]), num(r["ftp"])]
        past.setdefault(tid_i, {})[label] = {"n": meta_.loc[tid_, "tn"], "a": meta_.loc[tid_, "ta"],
                                             "five": [pack(r) for r in five], "bench": [pack(r) for r in bench]}
print(f"past {sum(len(v) for v in past.values())} team seasons, {len(past_people)} players")

# ================================================================ expansion sources
import numpy as np
import os
import time
import urllib.parse
from collections import defaultdict

roster_ids_all = set(int(i) for i in rost.athlete_id.dropna())


def zone_of(x, y):
    d = np.hypot(x - 25, y)
    three = (d >= 23.5) | ((y < 9) & ((x - 25).abs() >= 21.5))
    return np.select([three & (y < 9), three, d < 4, ((x - 25).abs() < 8) & (y <= 13.75)], [3, 4, 0, 1], default=2)


# ---------------------------------------------------------------- 1. shot history
# Shot locations back to 2001-02. Kept compact: each current player's career shot
# chart plus his zone numbers season by season; each team season's zone numbers
# (for the history slider); and the league's zone averages each season.
career_hex = defaultdict(lambda: defaultdict(lambda: [0, 0]))
career_zone_by = defaultdict(dict)
# One file per season in data/shots/ (every player with 40+ shots, every team), loaded
# by the page only when you slide to that season. Finished seasons are written once.
os.makedirs("data/shots", exist_ok=True)
shot_file_seasons = []


def hex_vec(x, y):
    """Vectorized hex_of: nearest pointy top hex for arrays of shots."""
    yb = y + 5.25
    best_d = best_c = best_r = None
    r0 = np.round(yb / (1.5 * HEX_R)).astype(int)
    for dr in (-1, 0, 1):
        row = r0 + dr
        off = (row % 2) * HEX_W / 2
        col = np.round((x - off) / HEX_W).astype(int)
        d = (x - (col * HEX_W + off)) ** 2 + (yb - row * 1.5 * HEX_R) ** 2
        if best_d is None:
            best_d, best_c, best_r = d, col, row
        else:
            m = d < best_d
            best_d = np.where(m, d, best_d); best_c = np.where(m, col, best_c); best_r = np.where(m, row, best_r)
    return best_c + best_r * 100


def season_pack(df_):
    z = df_.groupby("z").m.agg(["size", "sum"]).reindex(range(5), fill_value=0)
    h = df_[df_.hx >= 0].groupby("hx").m.agg(["size", "sum"])
    return {"n": int(len(df_)), "z": [[int(a), int(b)] for a, b in z.values], "h": [[int(k), int(a), int(b)] for k, (a, b) in h.iterrows()]}
team_zone = {}
league_zone_by = {}
for yr in range(2002, stats_season + 1):
    shx = shots if yr == stats_season and shots is not None else get("espn_nba_shots", f"shots_{yr}.parquet")
    if shx is None or shx.empty:
        continue
    g_ = shx[shx.game_id.astype(str).isin(reg_games_by_year.get(yr, set()))]
    g_ = g_[~g_.type_text.fillna("").str.contains("Free Throw")].dropna(subset=["coordinate_x_raw", "coordinate_y_raw"])
    if g_.empty:
        continue
    x_, y_ = g_.coordinate_x_raw.astype(float), g_.coordinate_y_raw.astype(float)
    g_ = g_.assign(z=zone_of(x_, y_), m=g_.scoring_play.astype(bool).astype(int),
                   pid=pd.to_numeric(g_.athlete_id_1, errors="coerce"), tid=pd.to_numeric(g_.team_id, errors="coerce"))
    label = season_label(yr)
    lz_ = g_.groupby("z").m.agg(["size", "sum"]).reindex(range(5), fill_value=0)
    league_zone_by[label] = [[int(a), int(b)] for a, b in lz_.values]
    spath = f"data/shots/{label}.json"
    if not (os.path.exists(spath) and yr < stats_season):
        xs, ys = x_.values, y_.values
        sf = g_.assign(hx=np.where((ys + 5.25) <= 36, hex_vec(xs, ys), -1))
        counts_ = sf.groupby("pid").size()
        out_ = {"lz": league_zone_by[label], "p": {}, "t": {}}
        for pid_, p_ in sf[sf.pid.isin(counts_[counts_ >= 40].index)].groupby("pid"):
            out_["p"][str(int(pid_))] = season_pack(p_)
        for tid_, t_ in sf.dropna(subset=["tid"]).groupby("tid"):
            out_["t"][str(int(tid_))] = season_pack(t_)
        json.dump(out_, open(spath, "w"), separators=(",", ":"))
    shot_file_seasons.append(label)
    for tid_, t_ in g_.groupby("tid"):
        zz = t_.groupby("z").m.agg(["size", "sum"]).reindex(range(5), fill_value=0)
        team_zone[(int(tid_), label)] = [[int(a), int(b)] for a, b in zz.values]
    mine = g_[g_.pid.isin(roster_ids_all)]
    for pid_, p_ in mine.groupby("pid"):
        zz = p_.groupby("z").m.agg(["size", "sum"]).reindex(range(5), fill_value=0)
        career_zone_by[int(pid_)][label] = [[int(a), int(b)] for a, b in zz.values]
    keep = mine[(mine.coordinate_y_raw.astype(float) + 5.25) <= 36]
    for pid_, xx, yy, mm in zip(keep.pid, keep.coordinate_x_raw.astype(float), keep.coordinate_y_raw.astype(float), keep.m):
        c = career_hex[int(pid_)][hex_of(xx, yy)]
        c[0] += 1
        c[1] += int(mm)
    print(f"shots {label}: {len(g_)} attempts")
career_shots = {}
for pid_, hx in career_hex.items():
    by = career_zone_by.get(pid_, {})
    tot = [[sum(v[i][0] for v in by.values()), sum(v[i][1] for v in by.values())] for i in range(5)]
    career_shots[pid_] = {"n": sum(a for a, _ in tot), "z": tot, "h": [[k, v[0], v[1]] for k, v in hx.items()], "by": by}
for (tid_, label), zz in team_zone.items():
    if tid_ in past and label in past[tid_]:
        past[tid_][label]["z"] = zz
print(f"shots career charts for {len(career_shots)} players, season files for {len(shot_file_seasons)} seasons")

# ---------------------------------------------------------------- 2. college
# ESPN uses one athlete id for college and the NBA, so a current player's college
# seasons join straight to his card. Box scores back to 2002-03.
college = defaultdict(list)
for yr in range(2003, stats_season + 1):
    cb = get("espn_mens_college_basketball_player_boxscores", f"player_box_{yr}.parquet")
    if cb is None or cb.empty:
        continue
    cb = cb[(cb.did_not_play != True) & (cb.minutes.fillna(0) > 0)].copy()
    cb["aid"] = pd.to_numeric(cb.athlete_id, errors="coerce")
    cb = cb[cb.aid.isin(roster_ids_all)]
    for (aid_, tn_), g_ in cb.groupby(["aid", "team_display_name"]):
        n = g_.game_id.nunique()
        s_ = g_[["minutes", "points", "rebounds", "assists", "steals", "blocks", "field_goals_made", "field_goals_attempted",
                 "three_point_field_goals_made", "three_point_field_goals_attempted", "free_throws_made", "free_throws_attempted"]].sum()
        college[int(aid_)].append([season_label(yr), tn_, int(n), num(s_.minutes / n), num(s_.points / n), num(s_.rebounds / n),
                                  num(s_.assists / n), num(s_.steals / n), num(s_.blocks / n),
                                  num(100 * s_.field_goals_made / s_.field_goals_attempted) if s_.field_goals_attempted else None,
                                  num(100 * s_.three_point_field_goals_made / s_.three_point_field_goals_attempted) if s_.three_point_field_goals_attempted else None,
                                  num(100 * s_.free_throws_made / s_.free_throws_attempted) if s_.free_throws_attempted else None,
                                  g_.team_logo.iloc[0] if "team_logo" in g_ else None])
print(f"college {len(college)} current players with college seasons")

# ---------------------------------------------------------------- name matching
name_ids = defaultdict(set)
for r in rost.itertuples():
    name_ids[norm_name(r.display_name)].add(int(r.athlete_id))
for pid_, v in past_people.items():
    name_ids[norm_name(v[0])].add(pid_)


def id_for(name):
    ids = name_ids.get(norm_name(name))
    return next(iter(ids)) if ids and len(ids) == 1 else None


# ---------------------------------------------------------------- 6. awards (Wikipedia)
AWARD_PAGES = [("MVP", "NBA Most Valuable Player Award"), ("Finals MVP", "Bill Russell NBA Finals Most Valuable Player Award"),
               ("Defensive Player of the Year", "NBA Defensive Player of the Year Award"), ("Rookie of the Year", "NBA Rookie of the Year Award"),
               ("Sixth Man of the Year", "NBA Sixth Man of the Year Award"), ("Most Improved Player", "NBA Most Improved Player Award"),
               ("All-NBA", "All-NBA Team"), ("All-Defensive", "NBA All-Defensive Team")]
AWARDS_CACHE = "awards.json"


def season_from_text(t):
    m = re.search(r"(19\d{2}|20\d{2})\s*[–\-]\s*(\d{2,4})", t)
    if not m:
        return None
    y0 = int(m.group(1))
    return f"{y0}-{str(y0 + 1)[-2:]}"


def fetch_awards():
    from bs4 import BeautifulSoup
    out = defaultdict(list)
    for label, page in AWARD_PAGES:
        try:
            html = wiki_json({"action": "parse", "page": page, "prop": "text"})["parse"]["text"]
        except Exception as e:
            print(f"award {page}: {e.__class__.__name__}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        found = 0
        for tbl in soup.select("table.wikitable"):
            heads = [th.get_text(" ", strip=True) for th in (tbl.find("tr").find_all(["th", "td"]) if tbl.find("tr") else [])]
            season = None
            for tr in tbl.find_all("tr")[1:]:
                cells = tr.find_all(["td", "th"])
                if not cells:
                    continue
                s_ = season_from_text(cells[0].get_text(" ", strip=True))
                season = s_ or season
                if not season:
                    continue
                offset = len(heads) - len(cells)  # rows under a spanning season cell are shorter
                for i, c in enumerate(cells):
                    head = heads[i + offset] if 0 <= i + offset < len(heads) else ""
                    tier = next((t for t in ("First", "Second", "Third") if t.lower() in head.lower()), None)
                    for a in c.find_all("a"):
                        pid_ = id_for(a.get_text(strip=True))
                        if pid_:
                            name = label if not tier else f"{label} {tier} Team"
                            if [season, name] not in out[pid_]:
                                out[pid_].append([season, name])
                                found += 1
        print(f"award {label}: {found} player seasons matched")
    # All-Star selections: one Wikipedia page per All-Star Game
    for yr in range(2002, stats_season + 1):
        try:
            html = wiki_json({"action": "parse", "page": f"{yr} NBA All-Star Game", "prop": "text"})["parse"]["text"]
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")
        season = season_label(yr)
        for tbl in soup.select("table.wikitable"):
            if not re.search(r"Pos|Player|Starters|Reserves", tbl.get_text(" ", strip=True)[:300]):
                continue
            for a in tbl.find_all("a"):
                pid_ = id_for(a.get_text(strip=True))
                if pid_ and [season, "All-Star"] not in out[pid_]:
                    out[pid_].append([season, "All-Star"])
    return {str(k): sorted(v) for k, v in out.items()}


awards = {}
try:
    awards = fetch_awards()
    if sum(len(v) for v in awards.values()) < 200:
        raise ValueError("too few awards matched")
    json.dump({"fetched": dt.date.today().isoformat(), "awards": awards}, open(AWARDS_CACHE, "w"))
except Exception as e:
    print(f"award fetch failed: {e.__class__.__name__} {e}")
    try:
        awards = json.load(open(AWARDS_CACHE))["awards"]
        print("award cached copy")
    except Exception:
        awards = {}
print(f"award {len(awards)} players with awards")

# ---------------------------------------------------------------- 7. bios (Wikidata + Wikipedia)
# Wikidata links each ESPN player id to his Wikipedia article, so there is no name
# guessing. The article's infobox gives college, high school and draft details.
BIO_CACHE = "bios.json"


def fetch_bios():
    q = 'SELECT ?espn ?article WHERE { ?item wdt:P3685 ?espn . ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }'
    req = urllib.request.Request("https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q), headers=WIKI_UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        rows = json.loads(r.read())["results"]["bindings"]
    title_for = {}
    for b in rows:
        try:
            title_for[int(b["espn"]["value"])] = urllib.parse.unquote(b["article"]["value"].rsplit("/wiki/", 1)[1]).replace("_", " ")
        except Exception:
            pass
    want = [(pid_, title_for[pid_]) for pid_ in roster_ids_all if pid_ in title_for]
    print(f"bios  {len(title_for)} ESPN ids linked on Wikidata, {len(want)} on current rosters")
    bios = {}
    for i in range(0, len(want), 40):
        chunk = want[i:i + 40]
        res = wiki_json({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                         "titles": "|".join(t for _, t in chunk), "redirects": 1})
        pages = {p["title"]: p for p in res.get("query", {}).get("pages", [])}
        redir = {r_["from"]: r_["to"] for r_ in res.get("query", {}).get("redirects", [])}
        for pid_, title in chunk:
            p = pages.get(redir.get(title, title))
            try:
                wt = p["revisions"][0]["slots"]["main"]["content"]
            except Exception:
                continue
            f = {}
            for key in ("college", "high_school", "draft_year", "draft_round", "draft_pick", "draft_team", "nationality"):
                m = re.search(r"\|\s*" + key + r"\s*=\s*(.*)", wt)
                if m:
                    v = m.group(1)
                    v = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>|<!--.*?-->", "", v)
                    v = re.sub(r"\{\{\s*(?:nowrap|ubl|plainlist|hlist)\s*\|", "", v, flags=re.I)
                    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
                    v = re.sub(r"\{\{[^{}]*\}\}|<br\s*/?>", ", ", v)
                    v = re.sub(r"[{}\[\]']", "", v).strip(" ,|*")
                    v = re.sub(r"\s*,\s*,+", ",", v)
                    if v:
                        f[key] = v[:120]
            f["wiki"] = title
            bios[str(pid_)] = f
        time.sleep(0.5)
    return bios


import time
bios = {}
try:
    bios = fetch_bios()
    if len(bios) < 200:
        raise ValueError(f"only {len(bios)} bios")
    json.dump({"fetched": dt.date.today().isoformat(), "bios": bios}, open(BIO_CACHE, "w"), ensure_ascii=False)
except Exception as e:
    print(f"bios  fetch failed: {e.__class__.__name__} {e}")
    try:
        bios = json.load(open(BIO_CACHE))["bios"]
        print("bios  cached copy")
    except Exception:
        bios = {}
print(f"bios  {len(bios)} players")

# ---------------------------------------------------------------- 8. transactions (RealGM)
TX_CACHE = "transactions.json"


def fetch_transactions():
    from bs4 import BeautifulSoup
    html = None
    for route in ("direct", "reader"):
        try:
            html = get_page("https://basketball.realgm.com/nba/transactions/league", route)
            if "Transactions" in html:
                break
        except Exception as e:
            print(f"tx    {route}: {e.__class__.__name__} {getattr(e, 'code', '')}")
            html = None
    if not html:
        raise ValueError("transactions page not reachable")
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n", strip=True)
    items, day = [], None
    for line in text.split("\n"):
        m = re.match(r"^(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2}), (20\d\d)$", line)
        if m:
            day = dt.datetime.strptime(line, "%B %d, %Y").date().isoformat()
            continue
        if day and len(line) > 25 and re.search(r"\b(sign|waive|trade|acquire|claim|release|convert|exercise|decline|extend|re-sign|assign|recall)", line, re.I):
            items.append([day, line[:300]])
    if len(items) < 10:
        raise ValueError(f"only {len(items)} transactions parsed")
    return {"fetched": dt.date.today().isoformat(), "items": items[:400]}


tx = None
try:
    tx = fetch_transactions()
    json.dump(tx, open(TX_CACHE, "w"), ensure_ascii=False)
    print(f"tx    {len(tx['items'])} transactions")
except Exception as e:
    print(f"tx    fetch failed: {e.__class__.__name__} {e}")
    try:
        tx = json.load(open(TX_CACHE))
        print("tx    cached copy")
    except Exception:
        tx = None
team_tx = defaultdict(list)
if tx:
    for r in rost.drop_duplicates("team_id").itertuples():
        nm_ = r.team_display_name.replace("LA Clippers", "Los Angeles Clippers")
        keys = [nm_, nm_.split(" ")[-1] if not nm_.endswith("Trail Blazers") else "Trail Blazers"]
        for day, line in tx["items"]:
            if any(k in line for k in keys):
                team_tx[r.team_abbreviation].append([day, line])

# ---------------------------------------------------------------- 9. Basketball Reference
# (a) Advanced stats for the latest season: one page, every player.
# (b) Seasons before 2001-02: one totals page, one standings page and one playoffs
#     page per season, fetched once and saved for good in bbref_history.json.
BBR_CACHE = "bbref_history.json"
BBR_FROM = int(os.environ.get("BBREF_FROM", "1980"))
BBR_CODE = {"ATL": "ATL", "BOS": "BOS", "CHI": "CHI", "CLE": "CLE", "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GSW": "GS",
            "HOU": "HOU", "IND": "IND", "KCK": "SAC", "SAC": "SAC", "LAL": "LAL", "LAC": "LAC", "SDC": "LAC", "MIL": "MIL",
            "NJN": "BKN", "BRK": "BKN", "NYK": "NY", "PHI": "PHI", "PHO": "PHX", "POR": "POR", "SAS": "SA", "SEA": "OKC",
            "OKC": "OKC", "UTA": "UTAH", "WSB": "WSH", "WAS": "WSH", "CHH": "CHA", "CHA": "CHA", "MIA": "MIA", "ORL": "ORL",
            "MIN": "MIN", "TOR": "TOR", "VAN": "MEM", "MEM": "MEM", "NOH": "NO", "NOP": "NO"}
import os
abbr_tid = {r.team_abbreviation: int(r.team_id) for r in rost.drop_duplicates("team_id").itertuples()}


def bbr_page(path):
    for route in ("direct", "reader"):
        try:
            h = get_page("https://www.basketball-reference.com" + path, route)
            if "<table" in h:
                return h
        except Exception as e:
            print(f"bbref {path} ({route}): {e.__class__.__name__} {getattr(e, 'code', '')}")
    return None


def bbr_tables(html):
    from bs4 import BeautifulSoup, Comment
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    for c in soup.find_all(string=lambda t: isinstance(t, Comment) and "<table" in t):
        tables += BeautifulSoup(c, "html.parser").find_all("table")
    return soup, tables


def cell(tr, *keys):
    for k in keys:
        td = tr.find(["td", "th"], attrs={"data-stat": k})
        if td is not None:
            return td
    return None


def fval(tr, *keys):
    td = cell(tr, *keys)
    try:
        return float(td.get_text(strip=True))
    except Exception:
        return None


advanced = {}
try:
    html = bbr_page(f"/leagues/NBA_{stats_season}_advanced.html")
    if html:
        _, tables = bbr_tables(html)
        for tbl in tables:
            for tr in tbl.find_all("tr"):
                nm_ = cell(tr, "name_display", "player")
                if nm_ is None or not nm_.get_text(strip=True) or nm_.name == "th" and not tr.find("td"):
                    continue
                team_ = (cell(tr, "team_name_abbr", "team_id") or nm_).get_text(strip=True)
                pid_ = id_for(nm_.get_text(strip=True))
                if not pid_ or pid_ not in players_all:
                    continue
                row = {"per": fval(tr, "per"), "ws": fval(tr, "ws"), "ws48": fval(tr, "ws_per_48"), "bpm": fval(tr, "bpm"),
                       "vorp": fval(tr, "vorp"), "usg": fval(tr, "usg_pct"), "obpm": fval(tr, "obpm"), "dbpm": fval(tr, "dbpm"),
                       "g": fval(tr, "games", "g")}
                if str(pid_) not in advanced or re.match(r"\d?TM|TOT", team_):
                    advanced[str(pid_)] = row  # combined row wins for traded players
    print(f"adv   {len(advanced)} players with advanced stats")
except Exception as e:
    print(f"adv   failed: {e.__class__.__name__} {e}")

bbr = {}
try:
    bbr = json.load(open(BBR_CACHE))
except Exception:
    bbr = {}
fetched_now = 0
for yr in range(BBR_FROM, 2002):
    label = season_label(yr)
    if label in bbr and bbr[label].get("teams"):
        continue
    if fetched_now >= int(os.environ.get("BBREF_MAX_SEASONS", "25")):
        break
    try:
        tot_html = bbr_page(f"/leagues/NBA_{yr}_totals.html")
        time.sleep(3.5)
        std_html = bbr_page(f"/leagues/NBA_{yr}.html")
        time.sleep(3.5)
        po_html = bbr_page(f"/playoffs/NBA_{yr}.html")
        time.sleep(3.5)
        if not tot_html:
            continue
        teams_ = defaultdict(list)
        _, tables = bbr_tables(tot_html)
        for tbl in tables:
            for tr in tbl.find_all("tr"):
                nm_ = cell(tr, "name_display", "player")
                tm_ = cell(tr, "team_name_abbr", "team_id")
                if nm_ is None or tm_ is None or not tr.find("td"):
                    continue
                code = tm_.get_text(strip=True)
                if code in ("TOT", "2TM", "3TM", "4TM", "5TM") or code not in BBR_CODE:
                    continue
                g = fval(tr, "games", "g") or 0
                if not g:
                    continue
                slug = nm_.get("data-append-csv") or (nm_.find("a")["href"].split("/")[-1].replace(".html", "") if nm_.find("a") else nm_.get_text(strip=True))
                gs = fval(tr, "games_started", "gs")
                mp = fval(tr, "mp") or 0
                fga, fg = fval(tr, "fga") or 0, fval(tr, "fg") or 0
                tpa, tp = fval(tr, "fg3a") or 0, fval(tr, "fg3") or 0
                fta, ft = fval(tr, "fta") or 0, fval(tr, "ft") or 0
                teams_[code].append([slug, nm_.get_text(strip=True), (cell(tr, "pos").get_text(strip=True) if cell(tr, "pos") else ""),
                                     int(g), None if gs is None else int(gs), mp, fval(tr, "pts") or 0, fval(tr, "trb") or 0,
                                     fval(tr, "ast") or 0, fval(tr, "stl") or 0, fval(tr, "blk") or 0,
                                     100 * fg / fga if fga else None, 100 * tp / tpa if tpa else None, 100 * ft / fta if fta else None])
        standings = {}
        if std_html:
            _, tables = bbr_tables(std_html)
            for tbl in tables:
                if not re.search(r"standings", tbl.get("id", "")):
                    continue
                rank = 0
                for tr in tbl.find_all("tr"):
                    a = tr.find("a", href=re.compile(r"/teams/[A-Z]{3}/"))
                    if not a:
                        continue
                    code = re.search(r"/teams/([A-Z]{3})/", a["href"]).group(1)
                    w, l = fval(tr, "wins"), fval(tr, "losses")
                    if w is None:
                        continue
                    rank += 1
                    standings.setdefault(code, {"w": int(w), "l": int(l), "name": a.get_text(strip=True), "seed": rank})
        playoffs = []
        if po_html:
            psoup, _ = bbr_tables(po_html)
            txt = psoup.get_text(" ", strip=True)
            for m in re.finditer(r"(Finals|Conference Finals|Conference Semifinals|Conference First Round|First Round|Western Conference Finals|Eastern Conference Finals)\s+(.+?)\s+over\s+(.+?)\s*\((\d)-(\d)\)", txt):
                playoffs.append([m.group(1), m.group(2).strip(), m.group(3).strip(), int(m.group(4)), int(m.group(5))])
        bbr[label] = {"teams": teams_, "standings": standings, "playoffs": playoffs}
        fetched_now += 1
        print(f"bbref {label}: {sum(len(v) for v in teams_.values())} player lines, {len(standings)} teams, {len(playoffs)} series")
    except Exception as e:
        print(f"bbref {label} failed: {e.__class__.__name__} {e}")
json.dump(bbr, open(BBR_CACHE, "w"), ensure_ascii=False)

# fold the saved seasons into the history slider and the team history
ROUND = {"First Round": 1, "Conference First Round": 1, "Conference Semifinals": 2, "Conference Finals": 3,
         "Eastern Conference Finals": 3, "Western Conference Finals": 3, "Finals": 4}
RESULT = {1: "first round", 2: "conference semifinals", 3: "conference finals", 4: "NBA Finals"}
bbr_people = {}
def bbr_divisions(yr):
    """Division of each team for a season before 2001-02 (yr = the season's end year)."""
    if yr == 1980:
        d = {"Atlantic": "BOS NJN NYK PHI WSB", "Central": "ATL CLE DET HOU IND SAS",
             "Midwest": "CHI DEN KCK MIL UTA", "Pacific": "GSW LAL PHO POR SDC SEA"}
    elif yr <= 1988:
        d = {"Atlantic": "BOS NJN NYK PHI WSB", "Central": "ATL CHI CLE DET IND MIL",
             "Midwest": "DAL DEN HOU KCK SAC SAS UTA", "Pacific": "GSW LAL LAC PHO POR SDC SEA"}
    elif yr == 1989:
        d = {"Atlantic": "BOS CHH NJN NYK PHI WSB", "Central": "ATL CHI CLE DET IND MIL",
             "Midwest": "DAL DEN HOU MIA SAS UTA", "Pacific": "GSW LAC LAL PHO POR SAC SEA"}
    elif yr == 1990:
        d = {"Atlantic": "BOS MIA NJN NYK PHI WSB", "Central": "ATL CHI CLE DET IND MIL ORL",
             "Midwest": "CHH DAL DEN HOU MIN SAS UTA", "Pacific": "GSW LAC LAL PHO POR SAC SEA"}
    else:
        d = {"Atlantic": "BOS MIA NJN NYK ORL PHI WSB WAS", "Central": "ATL CHH CHI CLE DET IND MIL TOR",
             "Midwest": "DAL DEN HOU MIN SAS UTA VAN", "Pacific": "GSW LAC LAL PHO POR SAC SEA"}
    out = {}
    for div, codes in d.items():
        for c in codes.split():
            out[c] = ("East" if div in ("Atlantic", "Central") else "West", div)
    return out


def bbr_seeds(label, standings):
    """Seeds as the NBA set them then: the two division winners in each conference
    get the top two seeds, everyone else follows by record."""
    yr = int(label[:4]) + 1
    divs = bbr_divisions(yr)
    pct = lambda c: standings[c]["w"] / max(1, standings[c]["w"] + standings[c]["l"])
    seeds = {}
    for conf in ("East", "West"):
        teams_c = [c for c in standings if divs.get(c, ("?",))[0] == conf]
        winners = []
        for div in {divs[c][1] for c in teams_c}:
            in_div = [c for c in teams_c if divs[c][1] == div]
            winners.append(max(in_div, key=pct))
        order = sorted(winners, key=pct, reverse=True) + sorted((c for c in teams_c if c not in winners), key=pct, reverse=True)
        for i, c in enumerate(order, 1):
            seeds[c] = i
    return seeds


def clean_series(sd):
    """Series lines read from the playoffs page can carry page text before the
    winner's name; keep only the real team names."""
    names = [v["name"] for v in sd.get("standings", {}).values()]
    out = []
    for rnd, win, lose, a, b in sd.get("playoffs", []):
        w = win if win in names else next((n for n in sorted(names, key=len, reverse=True) if win.endswith(n)), None)
        l = lose if lose in names else next((n for n in sorted(names, key=len, reverse=True) if lose.startswith(n)), None)
        if w and l:
            out.append([rnd, w, l, a, b])
    return out


for label, sd in bbr.items():
    sd["playoffs"] = clean_series(sd)
    for code, seed in bbr_seeds(label, sd.get("standings", {})).items():
        sd["standings"][code]["seed"] = seed
    names_ = {v["name"]: code for code, v in sd.get("standings", {}).items()}
    for code, rows_ in sd.get("teams", {}).items():
        ab_ = BBR_CODE.get(code)
        tid_ = abbr_tid.get(ab_)
        if not tid_ or not rows_:
            continue
        people_rows = []
        for r_ in rows_:
            slug, nm_, pos_, g, gs, mp, pts, trb, ast, stl, blk, fgp, tpp, ftp = r_
            pid_ = id_for(nm_) or ("b:" + slug)  # join to ESPN when the name matches
            if isinstance(pid_, str):
                bbr_people[pid_] = [nm_, (pos_ or "").split("-")[0]]
            people_rows.append({"id": pid_, "gp": g, "gs": gs if gs is not None else 0, "min": mp / g, "pts": pts / g,
                                "reb": trb / g, "ast": ast / g, "stl": stl / g, "blk": blk / g, "fgp": fgp, "tpp": tpp,
                                "ftp": ftp, "tot": mp, "pos": (pos_ or "F").split("-")[0]})
        has_gs = any(r["gs"] for r in people_rows)
        starters = sorted(people_rows, key=lambda r: (-(r["gs"] if has_gs else 0), -r["tot"]))[:5]
        five = past_slots(starters)
        bench = sorted((r for r in people_rows if r not in five), key=lambda r: -r["tot"])
        pack = lambda r: [r["id"], r["gp"], r["gs"], num(r["min"]), num(r["pts"]), num(r["reb"]), num(r["ast"]),
                          num(r["stl"]), num(r["blk"]), num(r["fgp"]), num(r["tpp"]), num(r["ftp"])]
        st_ = sd.get("standings", {}).get(code, {})
        past.setdefault(tid_, {})[label] = {"n": st_.get("name") or code, "a": code, "five": [pack(r) for r in five],
                                            "bench": [pack(r) for r in bench], "src": "bbref", "gs": has_gs}
        # playoff result for this team
        tname = st_.get("name")
        res, vs = "Missed playoffs", None
        mine = [p for p in sd.get("playoffs", []) if tname and (p[1] == tname or p[2] == tname)]
        if mine:
            deepest = max(mine, key=lambda p: ROUND.get(p[0], 0))
            if deepest[0] == "Finals" and deepest[1] == tname:
                res, vs = "Champions", [names_.get(deepest[2], deepest[2]), deepest[3], deepest[4]]
            else:
                lost = next((p for p in mine if p[2] == tname), deepest)
                res = f"Lost in {RESULT.get(ROUND.get(lost[0], 1), 'playoffs')}"
                vs = [names_.get(lost[1], lost[1]), lost[4], lost[3]]
        top = max(people_rows, key=lambda r: r["pts"] if r["gp"] >= 20 else 0)
        entry = {"s": label, "name": tname or code, "w": st_.get("w", 0), "l": st_.get("l", 0), "seed": st_.get("seed"),
                 "conf": bbr_divisions(int(label[:4]) + 1).get(code, (None,))[0],
                 "po": res, "vs": vs, "top": [bbr_people.get(top["id"], [None])[0] if isinstance(top["id"], str) else past_people.get(top["id"], [None])[0], num(top["pts"])]}
        history.setdefault(tid_, [])
        if not any(h["s"] == label for h in history[tid_]):
            history[tid_].append(entry)
for label, sd in bbr.items():
    if label in leaders_by:
        continue
    tot_ = {}
    for code, rows_ in sd.get("teams", {}).items():
        for r_ in rows_:
            slug, nm_, pos_, g, gs, mp, pts, trb, ast, stl, blk = r_[:11]
            key = id_for(nm_) or ("b:" + slug)
            t = tot_.setdefault(key, {"n": nm_, "t": BBR_CODE.get(code, code), "gp": 0, "pts": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0, "min": 0})
            t["gp"] += g; t["pts"] += pts; t["reb"] += trb; t["ast"] += ast; t["stl"] += stl; t["blk"] += blk; t["min"] += mp
            t["t"] = BBR_CODE.get(code, code)
    if not tot_:
        continue
    import math
    team_g = max((max((r_[3] for r_ in rows_), default=0) for rows_ in sd.get("teams", {}).values()), default=82)
    min_gp = max(1, math.ceil(min(team_g, 82) * .7))
    cats = []

    def board(key, label, grp, per, fmt=1, note=None):
        pool = [(k, v) for k, v in tot_.items() if (not per or v["gp"] >= min_gp) and v[key]]
        pool.sort(key=lambda kv: -(kv[1][key] / kv[1]["gp"] if per else kv[1][key]))
        rows = [[k if isinstance(k, int) and k in players_all else None, v["n"], v["t"],
                 round(v[key] / v["gp"], 1) if per else int(v[key]), int(v["gp"]), k] for k, v in pool[:15]]
        cats.append({"k": ("" if per else "t") + key, "l": label, "g": grp, "note": note, "rows": rows, "fmt": fmt if per else None})
    pgn = f"{min_gp}+ games"
    for k_, l_ in (("pts", "Points"), ("reb", "Rebounds"), ("ast", "Assists"), ("stl", "Steals"), ("blk", "Blocks"), ("min", "Minutes")):
        board(k_, l_, "Per game", True, note=pgn)
    for k_, l_ in (("pts", "Points"), ("reb", "Rebounds"), ("ast", "Assists"), ("gp", "Games played")):
        board(k_, l_, "Totals", False)
    leaders_by[label] = {"reg": cats, "po": None, "src": "bbref"}
for tid_ in history:
    history[tid_].sort(key=lambda h: h["s"])
print(f"bbref {len(bbr)} seasons before 2001-02 saved, {fetched_now} fetched this run, {len(bbr_people)} extra players")

# ---------------------------------------------------------------- 10. Tankathon
TANK_CACHE = "prospects.json"
TANK_TEAMS = {"hawks": "ATL", "celtics": "BOS", "nets": "BKN", "hornets": "CHA", "bulls": "CHI", "cavaliers": "CLE", "mavericks": "DAL",
              "nuggets": "DEN", "pistons": "DET", "warriors": "GS", "rockets": "HOU", "pacers": "IND", "clippers": "LAC", "lakers": "LAL",
              "grizzlies": "MEM", "heat": "MIA", "bucks": "MIL", "timberwolves": "MIN", "pelicans": "NO", "knicks": "NY", "thunder": "OKC",
              "magic": "ORL", "76ers": "PHI", "suns": "PHX", "trail-blazers": "POR", "kings": "SAC", "spurs": "SA", "raptors": "TOR",
              "jazz": "UTAH", "wizards": "WSH"}


def parse_prospects(html, with_teams):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out, team, seen = [], None, set()
    for a in soup.find_all("a", href=True):
        h = a["href"].rstrip("/")
        slug = h.split("/")[-1]
        if with_teams and re.search(r"tankathon\.com/[a-z0-9-]+$|^/[a-z0-9-]+$", h) and slug in TANK_TEAMS:
            team = TANK_TEAMS[slug]
            continue
        if "/players/" in h and slug not in seen and slug != "compare":
            t = a.get_text(" ", strip=True)
            m = re.match(r"(.+?)\s+([A-Z]{1,2}(?:/[A-Z]{1,2})?)\s*\|\s*(.+)$", t)
            if not m:
                continue
            seen.add(slug)
            out.append([len(out) + 1, m.group(1).strip(), m.group(2), m.group(3).strip(), team if with_teams else None])
    return out


prospects = None
try:
    bb_html = mk_html = None
    for route in ("direct", "reader"):
        try:
            bb_html = get_page("https://www.tankathon.com/big-board", route)
            mk_html = get_page("https://www.tankathon.com/mock-draft", route)
            break
        except Exception as e:
            print(f"tank  {route}: {e.__class__.__name__} {getattr(e, 'code', '')}")
    board = parse_prospects(bb_html, False)[:100] if bb_html else []
    mock = parse_prospects(mk_html, True)[:60] if mk_html else []
    if len(board) < 20:
        raise ValueError(f"only {len(board)} prospects parsed")
    prospects = {"fetched": dt.date.today().isoformat(), "board": board, "mock": mock}
    json.dump(prospects, open(TANK_CACHE, "w"), ensure_ascii=False)
    print(f"tank  {len(board)} prospects, {len(mock)} mock picks")
except Exception as e:
    print(f"tank  fetch failed: {e.__class__.__name__} {e}")
    try:
        prospects = json.load(open(TANK_CACHE))
        print("tank  cached copy")
    except Exception:
        prospects = None

# ---------------------------------------------------------------- photos for every player
# NBA.com has official headshots for nearly everyone who has played, keyed by NBA
# person id. Ids come from Wikidata (linked to the ESPN and Basketball Reference
# ids this app uses) and, failing that, from the nba_api player list on GitHub,
# matched by name when the name is unique. Wikidata also gives a Wikimedia Commons
# photo as a last resort. The page tries NBA.com, then ESPN, then Commons.
PHOTO_CACHE = "photos.json"
try:
    photo_cache = json.load(open(PHOTO_CACHE))
except Exception:
    photo_cache = {"wd": {}, "names": {}}
try:
    q = """SELECT ?espn ?bbref ?nba ?img WHERE {
      { ?p wdt:P3685 ?espn } UNION { ?p wdt:P2685 ?bbref }
      OPTIONAL { ?p wdt:P3685 ?espn } OPTIONAL { ?p wdt:P2685 ?bbref }
      OPTIONAL { ?p wdt:P3647 ?nba } OPTIONAL { ?p wdt:P18 ?img } }"""
    req = urllib.request.Request("https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(q), headers=WIKI_UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        rows = json.loads(r.read())["results"]["bindings"]
    wd = {}
    for b in rows:
        nba_ = b.get("nba", {}).get("value")
        img_ = b.get("img", {}).get("value")
        img_ = urllib.parse.unquote(img_.rsplit("/", 1)[-1]) if img_ else None
        # Wikidata keeps Basketball Reference ids with their letter folder ("j/jordami01")
        for key in ([b["espn"]["value"]] if "espn" in b else []) + (["b:" + b["bbref"]["value"].split("/")[-1]] if "bbref" in b else []):
            cur_ = wd.get(key, [None, None])
            wd[key] = [cur_[0] or (int(nba_) if nba_ and nba_.isdigit() else None), cur_[1] or img_]
    if len(wd) > 1000:
        photo_cache["wd"] = wd
    print(f"photo {len(wd)} players linked on Wikidata")
except Exception as e:
    print(f"photo Wikidata failed: {e.__class__.__name__} {e}; using {len(photo_cache.get('wd', {}))} cached")
try:
    src_ = urllib.request.urlopen("https://raw.githubusercontent.com/swar/nba_api/master/src/nba_api/stats/library/data.py", timeout=60).read().decode()
    rows = re.findall(r'^\s*\[(\d+), "([^"]*)", "([^"]*)", "([^"]*)", (?:True|False)\]', src_[:src_.index("teams = [")], re.M)
    by_ = defaultdict(list)
    for i_, _, _, full_ in rows:
        by_[norm_name(full_)].append(int(i_))
    photo_cache["names"] = {k: v[0] for k, v in by_.items() if len(v) == 1}
    print(f"photo {len(rows)} players in the nba_api list")
except Exception as e:
    print(f"photo nba_api list failed: {e.__class__.__name__}; using cached")
# NBA.com answers every id with an image, and players it has no photo of get the same
# generic silhouette (4,937 bytes at 260x190). Check each id once and remember it, so
# the page only asks NBA.com for real photos and otherwise moves on to ESPN or Commons.
SILHOUETTE_BYTES = 4937
checked = photo_cache.setdefault("nbaok", {})


def nba_photo_ok(pid_):
    try:
        req = urllib.request.Request(f"https://cdn.nba.com/headshots/nba/latest/260x190/{pid_}.png", method="HEAD",
                                     headers={"User-Agent": TWOK_UA, "Referer": "https://www.nba.com/"})
        with urllib.request.urlopen(req, timeout=20) as r:
            n = r.headers.get("Content-Length")
        if n is None:
            with urllib.request.urlopen(urllib.request.Request(req.full_url, headers=req.headers), timeout=20) as r:
                n = len(r.read())
        return 0 if int(n) == SILHOUETTE_BYTES else 1
    except Exception:
        return None  # unknown: could not check this run


def all_nba_ids():
    ids_ = set()
    names_ = photo_cache.get("names", {})
    for key, name in everyone.items():
        w_ = photo_cache.get("wd", {}).get(key, [None, None])
        n_ = w_[0] or names_.get(norm_name(name))
        if n_:
            ids_.add(int(n_))
    return ids_


everyone = {str(k): v[0] for k, v in past_people.items()}
everyone.update({k: v[0] for k, v in bbr_people.items()})
everyone.update({str(int(r.athlete_id)): r.display_name for r in rost.itertuples()})
todo_ids = [i for i in all_nba_ids() if str(i) not in checked]
if todo_ids:
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(nba_photo_ok, todo_ids))
    for i, ok in zip(todo_ids, results):
        if ok is not None:
            checked[str(i)] = ok
    unknown = sum(1 for r_ in results if r_ is None)
    print(f"photo checked {len(todo_ids) - unknown} NBA.com photos ({sum(1 for r_ in results if r_ == 1)} real, "
          f"{sum(1 for r_ in results if r_ == 0)} silhouettes){f', {unknown} not reachable' if unknown else ''}")
json.dump(photo_cache, open(PHOTO_CACHE, "w"), separators=(",", ":"))
photos = {}
for key, name in everyone.items():
    w_ = photo_cache.get("wd", {}).get(key, [None, None])
    nba_ = w_[0] or photo_cache.get("names", {}).get(norm_name(name))
    if nba_ and checked.get(str(nba_)) == 0:
        nba_ = None  # NBA.com only has the silhouette for him
    if nba_ or w_[1]:
        photos[key] = [nba_ or 0, w_[1] or ""]
print(f"photo {len(photos)} of {len(everyone)} players have an NBA.com id or a Commons photo")

# ---------------------------------------------------------------- NBA.com game ids
# NBA.com's replay pages are addressed by its own game id (0022501159). The season
# schedule file on NBA.com's CDN lists every game with that id. Ids are kept across
# runs in nba_games.json, so past seasons stay linked after the file moves on.
NBAG_CACHE = "nba_games.json"
try:
    nba_games = json.load(open(NBAG_CACHE))
except Exception:
    nba_games = {}
try:
    raw_ = None
    for route in ("direct", "reader"):
        try:
            if route == "direct":
                req = urllib.request.Request("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
                                             headers={"User-Agent": TWOK_UA, "Referer": "https://www.nba.com/", "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw_ = r.read().decode()
            else:
                req = urllib.request.Request("https://r.jina.ai/https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
                                             headers={"User-Agent": "NBA-Rosters personal app", "X-Return-Format": "text"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    raw_ = r.read().decode()
                raw_ = raw_[raw_.index("{"):raw_.rindex("}") + 1]
            sched_ = json.loads(raw_)
            break
        except Exception as e:
            print(f"nbaid {route}: {e.__class__.__name__} {getattr(e, 'code', '')}")
            sched_ = None
    added = 0
    for day in (sched_ or {}).get("leagueSchedule", {}).get("gameDates", []):
        for g in day.get("games", []):
            d_ = (g.get("gameDateEst") or "")[:10]
            a_, h_ = g.get("awayTeam", {}).get("teamTricode"), g.get("homeTeam", {}).get("teamTricode")
            if d_ and a_ and h_ and g.get("gameId"):
                key = f"{d_}|{a_}|{h_}"
                if key not in nba_games:
                    added += 1
                nba_games[key] = g["gameId"]
    json.dump(nba_games, open(NBAG_CACHE, "w"), separators=(",", ":"))
    print(f"nbaid {len(nba_games)} NBA.com game ids ({added} new)")
except Exception as e:
    print(f"nbaid failed: {e.__class__.__name__} {e}")

# ---------------------------------------------------------------- on/off impact
# Who was on the court for every second of the season, rebuilt from play by play:
# start from the box score starters, apply each substitution, and at the start of a
# later quarter take the five who show up in plays before being subbed in. Each
# player's team is then compared with him on the court and off it, per 100 possessions.
ONOFF_CACHE = "onoff.json"


def build_onoff(yr, box_):
    pbp = get("espn_nba_pbp", f"play_by_play_{yr}.parquet")
    if pbp is None or pbp.empty:
        return None
    pbp = pbp[pbp.season_type == 2].sort_values(["game_id", "game_play_number"])
    b_ = box_[box_.season_type == 2]
    team_of = {(str(g), int(a)): int(t) for g, a, t in zip(b_.game_id, b_.athlete_id, b_.team_id) if pd.notna(a)}
    starters = defaultdict(lambda: defaultdict(list))
    for g, a, t, st in zip(b_.game_id, b_.athlete_id, b_.team_id, b_.starter):
        if st and pd.notna(a):
            starters[str(g)][int(t)].append(int(a))
    on = defaultdict(lambda: [0.0, 0, 0])      # (player, team) -> seconds, points for, points against
    team_tot = defaultdict(lambda: [0.0, 0, 0])
    games_ok = games_bad = 0
    for gid, g in pbp.groupby("game_id", sort=False):
        gid = str(gid)
        st_ = starters.get(gid)
        if not st_ or len(st_) != 2 or any(len(v) != 5 for v in st_.values()):
            games_bad += 1
            continue
        home_t, away_t = int(g.home_team_id.iloc[0]), int(g.away_team_id.iloc[0])
        rows = list(zip(g.period_number, g.start_game_seconds_remaining, g.type_text, g.team_id,
                        g.athlete_id_1, g.athlete_id_2, g.athlete_id_3, g.home_score, g.away_score))
        lineup = {t: set(v) for t, v in st_.items()}
        cur_period = 1
        prev_t = None
        prev_h = prev_a = 0
        ok = True

        def infer(period, i0):
            """Five on the court for each team at the start of a later period."""
            found = {home_t: [], away_t: []}
            came_in = {home_t: set(), away_t: set()}
            for r in rows[i0:]:
                if r[0] != period:
                    break
                if r[2] == "Substitution" and pd.notna(r[3]):
                    t = int(r[3]); inn = int(r[4]) if pd.notna(r[4]) else None; out = int(r[5]) if pd.notna(r[5]) else None
                    if t in found and out and out not in came_in[t] and out not in found[t] and len(found[t]) < 5:
                        found[t].append(out)
                    if t in came_in and inn:
                        came_in[t].add(inn)
                    continue
                for a in (r[4], r[5], r[6]):
                    if pd.isna(a):
                        continue
                    t = team_of.get((gid, int(a)))
                    if t in found and int(a) not in came_in[t] and int(a) not in found[t] and len(found[t]) < 5:
                        found[t].append(int(a))
                if all(len(v) == 5 for v in found.values()):
                    break
            for t in found:  # a player with no events all quarter: keep the last lineup's players
                for a in lineup[t]:
                    if len(found[t]) >= 5:
                        break
                    if a not in found[t] and a not in came_in[t]:
                        found[t].append(a)
            return {t: set(v) for t, v in found.items()}

        for i, r in enumerate(rows):
            period, secs = r[0], r[1]
            if period != cur_period:
                cur_period = period
                lineup = infer(period, i)
                prev_t = secs
            if prev_t is not None and pd.notna(secs):
                dt_ = max(0.0, float(prev_t) - float(secs))
                if dt_:
                    for t, pl in lineup.items():
                        for a in pl:
                            on[(a, t)][0] += dt_
                    team_tot[home_t][0] += dt_; team_tot[away_t][0] += dt_
            prev_t = secs if pd.notna(secs) else prev_t
            h, a_ = int(r[7] or 0), int(r[8] or 0)
            dh, da = h - prev_h, a_ - prev_a
            if dh > 0 or da > 0:
                for t, pl in lineup.items():
                    pf, pa = (dh, da) if t == home_t else (da, dh)
                    for a in pl:
                        on[(a, t)][1] += pf; on[(a, t)][2] += pa
                team_tot[home_t][1] += dh; team_tot[home_t][2] += da
                team_tot[away_t][1] += da; team_tot[away_t][2] += dh
            prev_h, prev_a = h, a_
            if r[2] == "Substitution" and pd.notna(r[3]) and pd.notna(r[4]) and pd.notna(r[5]):
                t = int(r[3])
                if t in lineup:
                    lineup[t].discard(int(r[5])); lineup[t].add(int(r[4]))
                    if len(lineup[t]) != 5:
                        ok = False
        games_ok += ok
        games_bad += (not ok)
    out = {}
    for (a, t), (sec, pf, pa) in on.items():
        T_ = team_tot[t]
        off_sec, off_pf, off_pa = T_[0] - sec, T_[1] - pf, T_[2] - pa
        if sec < 60 * 150 or off_sec < 60 * 60:
            continue
        pace = (team_stats.get(t) or {}).get("pace") or 99.0 if yr == stats_season else 99.0
        per = lambda net, s_: net / s_ * 2880 * 100 / pace
        on_net, off_net = per(pf - pa, sec), per(off_pf - off_pa, off_sec)
        prev = out.get(a)
        if prev and prev[0] >= sec / 60:
            continue  # traded players: keep the team he played most for
        out[a] = [round(sec / 60), round(on_net, 1), round(off_net, 1), round(on_net - off_net, 1), t]
    print(f"onoff {yr}: {games_ok} games rebuilt cleanly, {games_bad} with gaps, {len(out)} players with 150+ minutes")
    return out


try:
    onoff = json.load(open(ONOFF_CACHE))
except Exception:
    onoff = {}
key_ = season_label(stats_season)
if key_ not in onoff or stats_season == S:
    try:
        res_ = build_onoff(stats_season, box)
        if res_:
            onoff = {key_: {str(k): v for k, v in res_.items()}}
            json.dump(onoff, open(ONOFF_CACHE, "w"), separators=(",", ":"))
    except Exception as e:
        print(f"onoff failed: {e.__class__.__name__} {e}")
onoff_now = onoff.get(key_, {})

# ---------------------------------------------------------------- past drafts
# Every NBA draft since 1980 from Wikipedia's draft pages (first two rounds). A draft
# never changes once held, so each year is fetched once and kept in draft_history.json.
DRAFT_HIST = "draft_history.json"
FORMER_TEAMS = {"Seattle SuperSonics": "OKC", "New Jersey Nets": "BKN", "Charlotte Bobcats": "CHA", "Vancouver Grizzlies": "MEM",
                "Washington Bullets": "WSH", "Kansas City Kings": "SAC", "San Diego Clippers": "LAC", "New Orleans Hornets": "NO",
                "New Orleans/Oklahoma City Hornets": "NO", "Charlotte Hornets": "CHA", "LA Clippers": "LAC", "Los Angeles Clippers": "LAC"}
try:
    drafts = json.load(open(DRAFT_HIST))
except Exception:
    drafts = {}
team_by_name = {r.team_display_name: r.team_abbreviation for r in rost.drop_duplicates("team_id").itertuples()}
team_by_name.update(FORMER_TEAMS)
name_to_id = {}
for k_, v_ in past_people.items():
    name_to_id.setdefault(norm_name(v_[0]), k_)
for k_, v_ in bbr_people.items():
    name_to_id.setdefault(norm_name(v_[0]), k_)
for r in rost.itertuples():
    name_to_id[norm_name(r.display_name)] = int(r.athlete_id)
_today = dt.date.today()
last_draft = _today.year if _today.month >= 7 else _today.year - 1
fetched_d = 0
try:
    from bs4 import BeautifulSoup
    draft_errors = []
    for yr in range(1980, last_draft + 1):
        if str(yr) in drafts:
            continue
        try:
            html = wiki_json({"action": "parse", "page": f"{yr} NBA draft", "prop": "text", "redirects": 1})["parse"]["text"]
        except Exception as e:
            draft_errors.append(f"{yr} {e.__class__.__name__}")
            time.sleep(2)
            continue
        soup = BeautifulSoup(html, "html.parser")
        picks_ = []
        for tbl in soup.select("table.wikitable"):
            if tbl.find("tr") is None:
                continue  # empty tables sit between the legend and the pick list on some pages
            for st_tag in tbl.find_all(["style", "link"]):
                st_tag.decompose()  # hidden style text glued to headings ("...}Rnd.")
            heads = [re.sub(r"\[.*?\]", "", th.get_text(" ", strip=True)).strip() for th in tbl.find("tr").find_all(["th", "td"])]
            col = lambda pat: next((i for i, h in enumerate(heads) if re.search(pat, h, re.I)), None)
            ci = {"rnd": col(r"^R(ou)?nd"), "pick": col(r"^Pick"), "player": col(r"^Player"), "pos": col(r"^Pos"),
                  "team": col(r"^(NBA\s+)?Team\b"), "school": col(r"School|College|club")}
            if ci["pick"] is None or ci["player"] is None or ci["team"] is None:
                continue
            for tr in tbl.find_all("tr")[1:]:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if not cells:
                    continue
                if len(cells) < len(heads) - 1:
                    continue
                try:
                    pick = int(re.sub(r"\D", "", cells[ci["pick"]]) or 0)
                    rnd = int(re.sub(r"\D", "", cells[ci["rnd"]]) or 0) if ci["rnd"] is not None else (1 if pick <= 30 else 2)
                except (ValueError, IndexError):
                    continue
                if not pick or rnd > 2:
                    continue
                name = re.sub(r"\s*\[[^\]]*\]", "", re.sub(r"[\^~*+#†‡§]+", "", cells[ci["player"]])).strip()
                team = re.sub(r"\[.*?\]", "", cells[ci["team"]]).strip()
                ab = team_by_name.get(team) or next((a for n, a in team_by_name.items() if n and n in team), None)
                picks_.append([rnd, pick, name, cells[ci["pos"]] if ci["pos"] is not None else "",
                               re.sub(r"\s+\)", ")", re.sub(r"\(\s+", "(", re.sub(r"\[.*?\]", "", cells[ci["school"]]))).strip() if ci["school"] is not None else "", ab, team])
            if picks_:
                break
        if len(picks_) >= 20:
            drafts[str(yr)] = picks_
            fetched_d += 1
            json.dump(drafts, open(DRAFT_HIST, "w"), ensure_ascii=False, separators=(",", ":"))  # keep progress
        else:
            draft_errors.append(f"{yr} only {len(picks_)} picks read")
        time.sleep(0.5)
    json.dump(drafts, open(DRAFT_HIST, "w"), ensure_ascii=False, separators=(",", ":"))
    if draft_errors:
        print("draft skipped this run (retried next run): " + ", ".join(draft_errors))
except Exception as e:
    print(f"draft history stopped: {e.__class__.__name__} {e}")
for yr_, rows_ in drafts.items():
    for r_ in rows_:
        r_[2] = re.sub(r"\s*\[[^\]]*\]", "", r_[2]).strip()  # footnote marks like "[1]"
        pid_ = name_to_id.get(norm_name(r_[2]))
        if len(r_) < 8:
            r_.append(pid_)
        else:
            r_[7] = pid_
print(f"draft {len(drafts)} past drafts ({fetched_d} fetched this run)")

# ---------------------------------------------------------------- play style
# NBA.com's play type data (Synergy) is out of reach, so each field goal is sorted
# into a style from the play by play: how soon it came after a steal or defensive
# rebound (transition), whether it followed an offensive rebound (second chance),
# and ESPN's shot description (cuts, drives, post moves, pull-ups, spot-ups).
STYLES = ["Transition", "Second chance", "Cuts and lobs", "Drives", "Post ups", "Off the dribble", "Catch and shoot", "Other"]


def style_of(desc):
    d = desc or ""
    if re.search(r"Tip|Putback", d): return 1
    if re.search(r"Cutting|Alley Oop", d): return 2
    if re.search(r"Hook|Turnaround", d): return 4
    if re.search(r"Driving|Finger Roll|Running Layup|Running Dunk|Floating|Reverse", d): return 3
    if re.search(r"Pullup|Step Back|Fade Away|Running Jump|Running Pullup", d): return 5
    if re.fullmatch(r"Jump Shot", d.strip()): return 6
    return 7


def build_playstyle(yr):
    pbp = get("espn_nba_pbp", f"play_by_play_{yr}.parquet")
    if pbp is None or pbp.empty:
        return None
    pbp = pbp[pbp.season_type == 2].sort_values(["game_id", "game_play_number"])
    team_c = defaultdict(lambda: [[0, 0, 0] for _ in STYLES])   # team -> style -> [attempts, makes, points]
    opp_c = defaultdict(lambda: [[0, 0, 0] for _ in STYLES])
    lg = [[0, 0, 0] for _ in STYLES]
    ply = defaultdict(lambda: [[0, 0, 0] for _ in STYLES])       # (player, team) -> style counts
    made_ast = defaultdict(lambda: [0, 0])                       # team -> [assisted makes, makes]
    p_ast = defaultdict(lambda: [0, 0])                          # player -> [assisted makes, makes]
    pairs = defaultdict(int)                                     # (team, passer, scorer) -> assists
    real = set(int(t) for t in rost.team_id.dropna().unique())  # the 30 teams: no All-Star or exhibition games
    for gid, g in pbp.groupby("game_id", sort=False):
        home, away = int(g.home_team_id.iloc[0]), int(g.away_team_id.iloc[0])
        if home not in real or away not in real:
            continue
        other = {home: away, away: home}
        start_t, start_team, start_kind = None, None, None       # when and how the current possession began
        oreb_t, oreb_team = None, None
        for typ, tid, a1, a2, sv, scoring, shooting, secs in zip(
                g.type_text, g.team_id, g.athlete_id_1, g.athlete_id_2, g.score_value, g.scoring_play, g.shooting_play,
                g.start_game_seconds_remaining):
            if pd.isna(tid) or pd.isna(secs):
                continue
            tid = int(tid); typ = typ or ""
            if typ == "Defensive Rebound":
                start_t, start_team, start_kind = secs, tid, "live"; oreb_t = None; continue
            if typ == "Offensive Rebound":
                oreb_t, oreb_team = secs, tid; continue
            if "Turnover" in typ and tid in other:
                start_t, start_team = secs, other[tid]
                start_kind = "live" if pd.notna(a2) else "dead"  # a steal starts a live ball break
                oreb_t = None; continue
            if not shooting or "Free Throw" in typ:
                continue
            if tid not in other:
                continue
            if start_team == tid and start_kind == "live" and start_t is not None and start_t - secs <= 8:
                st = 0
            elif oreb_team == tid and oreb_t is not None and oreb_t - secs <= 4:
                st = 1
            else:
                st = style_of(typ)
            pts = int(sv or 0) if scoring else 0
            mk = 1 if scoring else 0
            for bucket in (team_c[tid][st], opp_c[other[tid]][st], lg[st]):
                bucket[0] += 1; bucket[1] += mk; bucket[2] += pts
            if pd.notna(a1):
                b = ply[(int(a1), tid)][st]; b[0] += 1; b[1] += mk; b[2] += pts
            if scoring:
                made_ast[tid][1] += 1
                if pd.notna(a1):
                    p_ast[int(a1)][1] += 1
                if pd.notna(a2):
                    made_ast[tid][0] += 1
                    if pd.notna(a1):
                        p_ast[int(a1)][0] += 1
                        pairs[(tid, int(a2), int(a1))] += 1
            # a make hands the ball over (dead ball), a miss waits for the rebound
            if scoring:
                start_t, start_team, start_kind = secs, other[tid], "dead"
            oreb_t = None if not scoring else oreb_t
    def summarize(c):
        tot = sum(x[0] for x in c) or 1
        return [[round(100 * x[0] / tot, 1), round(x[2] / x[0], 3) if x[0] else None, x[0]] for x in c]
    lg_sum = summarize(lg)
    out = {"styles": STYLES, "league": lg_sum, "teams": {}, "players": {}}
    for tid in team_c:
        off, dfn = summarize(team_c[tid]), summarize(opp_c[tid])
        top = sorted(((k[1], k[2], n) for k, n in pairs.items() if k[0] == tid), key=lambda x: -x[2])[:10]
        out["teams"][str(tid)] = {"off": off, "def": dfn, "ast": round(100 * made_ast[tid][0] / max(1, made_ast[tid][1]), 1), "pairs": top}
    # ranks among the 30 teams: frequency (most = 1st) and points per shot (offense: higher is better, defense: lower)
    for side, better_low in (("off", False), ("def", True)):
        for i in range(len(STYLES)):
            vals = [(t, v[side][i][1]) for t, v in out["teams"].items() if v[side][i][1] is not None]
            order = sorted(vals, key=lambda x: x[1], reverse=not better_low)
            for rk, (t, _) in enumerate(order, 1):
                out["teams"][t][side][i].append(rk)
            fq = sorted(((t, v[side][i][0]) for t, v in out["teams"].items()), key=lambda x: -x[1])
            for rk, (t, _) in enumerate(fq, 1):
                out["teams"][t][side][i].append(rk)
    ast_rank = sorted(out["teams"], key=lambda t: -out["teams"][t]["ast"])
    for rk, t in enumerate(ast_rank, 1):
        out["teams"][t]["astrk"] = rk
    best = {}
    for (pid, tid), c in ply.items():
        n = sum(x[0] for x in c)
        if n >= 150 and (pid not in best or n > best[pid][0]):
            best[pid] = (n, tid, c)
    for pid, (n, tid, c) in best.items():
        a = p_ast.get(pid, [0, 0])
        out["players"][str(pid)] = {"s": summarize(c), "n": n, "t": tid, "self": round(100 * (1 - a[0] / a[1]), 1) if a[1] else None}
    print(f"style {yr}: {sum(x[0] for x in lg)} field goals sorted, {len(out['teams'])} teams, {len(out['players'])} players")
    return out


STYLE_CACHE = "playstyle.json"
try:
    style_all = json.load(open(STYLE_CACHE))
except Exception:
    style_all = {}
if key_ not in style_all or stats_season == S:
    try:
        res_ = build_playstyle(stats_season)
        if res_:
            style_all = {key_: res_}
            json.dump(style_all, open(STYLE_CACHE, "w"), separators=(",", ":"))
    except Exception as e:
        print(f"style failed: {e.__class__.__name__} {e}")
style_now = style_all.get(key_, {})

# ---------------------------------------------------------------- your settings
# Stream and embed hosts and team codes live in site_config.py, which updates to the
# app never touch.
try:
    import site_config as _sc
    site_settings = {"streamBase": str(getattr(_sc, "STREAM_BASE", "") or "").rstrip("/"),
                     "embedBase": str(getattr(_sc, "EMBED_BASE", "") or "").rstrip("/"),
                     "codes": dict(getattr(_sc, "TEAM_CODES", {}) or {})}
except Exception as e:
    print(f"site  settings not read ({e.__class__.__name__}); links stay relative")
    site_settings = {"streamBase": "", "embedBase": "", "codes": {}}

players, teams = {}, {}
for r in rost.itertuples():
    pid = int(r.athlete_id)
    s = season_line.get(pid)
    lt = last_team.get(pid)
    players[pid] = {
        "n": r.display_name, "j": None if pd.isna(r.jersey) else str(r.jersey),
        "pos": r.position_abbreviation if isinstance(r.position_abbreviation, str) else "F",
        "posn": r.position_name if isinstance(r.position_name, str) else "",
        "ht": r.height if isinstance(r.height, str) else None,
        "wt": r.weight if isinstance(r.weight, str) else None,
        "age": None if pd.isna(r.age) else int(r.age),
        "exp": None if pd.isna(r.experience_years) else int(r.experience_years),
        "from": ", ".join(x for x in [r.birth_place_city, r.birth_place_state if isinstance(r.birth_place_state, str) else r.birth_place_country] if isinstance(x, str)),
        "t": int(r.team_id), "s": s, "po": po_line.get(pid), "sp": splits.get(pid),
        "log": logs.get(pid, []), "sh": shot_players.get(pid), "oo": onoff_now.get(str(pid)),
        "ps": (style_now.get("players") or {}).get(str(pid)), "k": player_2k.get(pid), "$": player_sal.get(pid), "yr": yearly.get(pid),
        "shc": career_shots.get(pid), "col": college.get(pid), "aw": awards.get(str(pid)), "bio": bios.get(str(pid)), "adv": advanced.get(str(pid)),
        "prev": lt if lt and lt != r.team_abbreviation else None,
        "_h": height_in(r.height),
    }

pre = None
if pre_box is not None:
    pre = pre_box[pre_box.team_abbreviation.isin(TEAM_ABBRS)].copy()
    pre["athlete_id"] = pd.to_numeric(pre.athlete_id, errors="coerce").astype("Int64")

in_season = stats_season == S and (box.season_type == 2).any()
lineup_mode = "lastgame" if in_season else "projected"

for tid, g in rost.groupby("team_id"):
    tid = int(tid)
    row = g.iloc[0]
    abbr = row.team_abbreviation
    ids = [int(x) for x in g.athlete_id]
    five, ranked = pick_projection(ids, players)
    note = f"Projected from {season_label(stats_season)} starts and minutes"
    src = box if in_season else pre
    if src is not None:
        tg = src[src.team_id.astype(int) == tid]
        if not tg.empty:
            last_gid = tg.sort_values("game_date").game_id.iloc[-1]
            last = tg[tg.game_id == last_gid]
            st = [int(x) for x in last[last.starter == True].athlete_id if int(x) in ids]
            if len(st) >= 3:
                fill = [x for x in five if x not in st]
                five = (st + fill)[:5]
                d = str(last.game_date.iloc[0])
                note = f"Started the last game, {d[5:7].lstrip('0')}/{d[8:10].lstrip('0')}"
    five = slot_order(five, players)
    bench = [x for x in ranked if x not in five]

    div = next(k for k, v in DIVS.items() if abbr in v)
    color = row.team_color if isinstance(row.team_color, str) else "556677"
    alt = row.team_alternate_color if isinstance(row.team_alternate_color, str) else None
    st = team_stats.get(tid, {})
    teams[tid] = {
        "abbr": abbr, "name": row.team_display_name, "color": color, "alt": alt,
        "logo": row.team_logo, "div": div, "conf": "East" if div in EAST else "West",
        "five": five, "bench": bench, "note": note,
        "rec": recs.get(tid), "games": games.get(tid, []),
        "sh": shot_teams.get(tid), "shd": shot_def.get(tid), "k": team_2k.get(abbr), "$": team_sal.get(abbr),
        "coach": (coaches or {}).get("teams", {}).get(abbr),
        "sched": schedule.get(tid, []), "hist": history.get(tid, []),
        "picks": (picks or {}).get("teams", {}).get(abbr),
        "past": past.get(tid, {}),
        "style": (style_now.get("teams") or {}).get(str(tid)), "tx": team_tx.get(abbr, [])[:25],
        "st": {k: [num(v, 1), team_ranks[tid][k]] for k, v in st.items()},
    }

for p in players.values():
    p.pop("_h", None)

data = {
    "meta": {
        "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "rosterStamp": stamp("espn_nba_rosters"), "boxStamp": stamp("espn_nba_player_boxscores"),
        "stats": season_label(stats_season), "roster": season_label(roster_season),
        "mode": lineup_mode, "qualN": QUAL_N, "gpMin": GP_MIN,
        "lg": {k: num(v, 1) for k, v in LEAGUE_AVG.items()},
        "lz": league_zones, "zones": ZONES, "hexR": HEX_R, "k2": twok_meta, "cap": cap_meta, "coachDate": (coaches or {}).get("fetched"), "picksDate": (picks or {}).get("fetched"),
    },
    "divs": DIVS, "teams": teams, "players": players, "leaders": leaders,
    "leadersBy": {k: leaders_by[k] for k in sorted(leaders_by)},
    "people": {**{str(k): v + ([awards[str(k)]] if str(k) in awards else []) for k, v in past_people.items() if k not in players},
               **{k: v + ([awards[k]] if k in awards else []) for k, v in bbr_people.items()}},
    "prospects": prospects, "lzs": league_zone_by, "photos": photos, "nbaGames": nba_games, "drafts": drafts, "shotSeasons": shot_file_seasons,
    "style": {"styles": style_now.get("styles"), "league": style_now.get("league")} if style_now else None,
    "site": site_settings,
}

def clean(o):
    """Browsers reject NaN and Infinity in JSON, so turn any that slipped through into null."""
    if isinstance(o, float):
        return None if o != o or o in (float("inf"), float("-inf")) else o
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if hasattr(o, "item"):
        return clean(o.item())
    return o


blob = json.dumps(clean(data), separators=(",", ":"), ensure_ascii=False, allow_nan=False,
                  default=lambda o: (o.item() if hasattr(o, "item") else None))
blob = blob.replace("</", "<\\/")
# The app lives in app.html (code only, small). The site, index.html, is app.html
# with the data filled in, so code changes never touch the data and vice versa.
import os as _os
src_file = "app.html" if _os.path.exists("app.html") else "index.html"
html = open(src_file, encoding="utf-8").read()
new = re.sub(r"(<script id=\"nba-data\" type=\"application/json\">).*?(</script>)",
             lambda m: m.group(1) + blob + m.group(2), html, count=1, flags=re.S)
if new == html and blob not in html:
    sys.exit(f"Data markers not found in {src_file}")
open("index.html", "w", encoding="utf-8").write(new)
print(f"built {len(teams)} teams, {len(players)} players, stats {season_label(stats_season)}, "
      f"lineups {lineup_mode}, {len(blob)//1024} KB")
