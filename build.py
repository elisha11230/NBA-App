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
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            raw = r.read()
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
    h = soup.find(string=re.compile(r"^\s*NBA 2K26\s*$"))
    if h:
        tbl = h.find_next("table")
        for tr in tbl.find_all("tr") if tbl else []:
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(tds) >= 3 and tds[2].isdigit():
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

    data = {"fetched": today.isoformat(), "teams": teams_2k, "players": players_2k, "prev": prev_2k}
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
        player_2k[pid] = {"o": p["ovr"], "a": p["arch"] or det.get("arch"), "p": p["pos"], "b": p["badges"] if p["badges"] is not None else (len(dedupe_badges(det.get("badges"))) or None), "s": p["star"],
                          "g": det.get("groups") or None, "at": det.get("attrs") or None, "bd": dedupe_badges(det.get("badges")) or None,
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
def build_leaders(src, playoffs=False):
    g = src[(src.did_not_play != True) & (src.minutes.fillna(0) > 0)].copy()
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
        s = s.sort_values(ascending=asc).head(25)
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
                int(r.season_type)])
    print(f"sched {len(sch)} games for {len(schedule)} teams")

# ---------------------------------------------------------------- team history
# Ten seasons of record, seed and playoff run from ESPN standings and box scores.
history = {}
for yr in range(max(2002, stats_season - 9), stats_season + 1):
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
        result = "Champions" if p_ and p_["champ"] else (f"Lost in {rnd[min(p_['rounds'], 4)].lower()}" if p_ and p_["rounds"] else "Missed playoffs")
        if p_ and p_["rounds"] == 1 and p_["last"] and p_["last"][1] + p_["last"][2] <= 1:
            result = "Play-in"
        history.setdefault(int(tid_), []).append({
            "s": season_label(yr), "name": name_, "w": int(v_.get("wins") or 0), "l": int(v_.get("losses") or 0),
            "seed": int(v_["playoffSeed"]) if pd.notna(v_.get("playoffSeed")) else None,
            "po": result, "vs": p_["last"] if p_ and p_["last"] else None,
            "top": top_scorer.get(int(tid_), {}).get(yr)})
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
        "log": logs.get(pid, []), "sh": shot_players.get(pid), "k": player_2k.get(pid), "$": player_sal.get(pid), "yr": yearly.get(pid),
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
}

blob = json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=lambda o: (o.item() if hasattr(o, "item") else None))
blob = blob.replace("</", "<\\/")
html = open("index.html", encoding="utf-8").read()
new = re.sub(r"(<script id=\"nba-data\" type=\"application/json\">).*?(</script>)",
             lambda m: m.group(1) + blob + m.group(2), html, count=1, flags=re.S)
if new == html and blob not in html:
    sys.exit("Data markers not found in index.html")
open("index.html", "w", encoding="utf-8").write(new)
print(f"built {len(teams)} teams, {len(players)} players, stats {season_label(stats_season)}, "
      f"lineups {lineup_mode}, {len(blob)//1024} KB")
