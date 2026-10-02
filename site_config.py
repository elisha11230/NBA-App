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
STREAM_BASE = ""

# Where the embedded player on a live game loads from.
EMBED_BASE = ""

# Team codes used in the links, lowercased. These are the NBA's own three letter
# codes. Change any one here if your stream site uses a different code for a team.
TEAM_CODES = {
    "ATL": "atl", "BOS": "bos", "BKN": "bkn", "CHA": "cha", "CHI": "chi", "CLE": "cle",
    "DAL": "dal", "DEN": "den", "DET": "det", "GS": "gsw", "HOU": "hou", "IND": "ind",
    "LAC": "lac", "LAL": "lal", "MEM": "mem", "MIA": "mia", "MIL": "mil", "MIN": "min",
    "NO": "nop", "NY": "nyk", "OKC": "okc", "ORL": "orl", "PHI": "phi", "PHX": "phx",
    "POR": "por", "SAC": "sac", "SA": "sas", "TOR": "tor", "UTAH": "uta", "WSH": "was",
}
