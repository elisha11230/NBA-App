"""
Your settings for the NBA app. This file is yours: the builder only reads it, and
app updates never replace it.

Links are built as   <base>/live/nba/<date>/<away>-<home>   (Stream button)
and                  <base>/embed/nba/<date>/<away>-<home>  (player on a live game)
for example          /embed/nba/2026-10-03/mia-tor

Leave a base empty to keep links relative to this site; otherwise put the host,
with no trailing slash, e.g. "https://example.com".
"""

# Where the "Stream" button on a live game points.
STREAM_BASE = "https://ppv.to"

# Where the embedded player on a live game loads from.
EMBED_BASE = "https://embedindia.st"

# Team codes used in the links, lowercased. These are the NBA's own three letter
# codes. Change any one here if your stream site uses a different code for a team.
TEAM_CODES = {
    "ATL": "atl", "BOS": "bos", "BKN": "bkn", "CHA": "cha", "CHI": "chi", "CLE": "cle",
    "DAL": "dal", "DEN": "den", "DET": "det", "GS": "gs", "HOU": "hou", "IND": "ind",
    "LAC": "lac", "LAL": "lal", "MEM": "mem", "MIA": "mia", "MIL": "mil", "MIN": "min",
    "NO": "no", "NY": "ny", "OKC": "okc", "ORL": "orl", "PHI": "phi", "PHX": "phx",
    "POR": "por", "SAC": "sac", "SA": "sas", "TOR": "tor", "UTAH": "utah", "WSH": "was",
}

# ---------------------------------------------------------------- second stream
# A second player you can switch to on any live game ("Stream 2"). Its address is
#   <STREAM2_BASE><STREAM2_PATH>
# where {home} and {away} are the team names below, for example
#   /embed/admin/ppv-new-york-knicks-vs-philadelphia-76-ers/1
# If the away team should come first, swap {home} and {away} in STREAM2_PATH.
# {date} (like 2026-10-20) is also available if a source needs it.

# Host for the second stream. None uses the same host as EMBED_BASE.
STREAM2_BASE = "https://embed.st"

STREAM2_PATH = "/embed/admin/ppv-{away}-vs-{home}/1"

# Team names used in the second stream's address. Change any one if the source spells it
# differently (for example "los-angeles-clippers").
TEAM_NAMES = {
    "ATL": "atlanta-hawks",
    "BOS": "boston-celtics",
    "BKN": "brooklyn-nets",
    "CHA": "charlotte-hornets",
    "CHI": "chicago-bulls",
    "CLE": "cleveland-cavaliers",
    "DAL": "dallas-mavericks",
    "DEN": "denver-nuggets",
    "DET": "detroit-pistons",
    "GS": "golden-state-warriors",
    "HOU": "houston-rockets",
    "IND": "indiana-pacers",
    "LAC": "la-clippers",
    "LAL": "los-angeles-lakers",
    "MEM": "memphis-grizzlies",
    "MIA": "miami-heat",
    "MIL": "milwaukee-bucks",
    "MIN": "minnesota-timberwolves",
    "NO": "new-orleans-pelicans",
    "NY": "new-york-knicks",
    "OKC": "oklahoma-city-thunder",
    "ORL": "orlando-magic",
    "PHI": "philadelphia-76-ers",
    "PHX": "phoenix-suns",
    "POR": "portland-trail-blazers",
    "SAC": "sacramento-kings",
    "SA": "san-antonio-spurs",
    "TOR": "toronto-raptors",
    "UTAH": "utah-jazz",
    "WSH": "washington-wizards",
}
