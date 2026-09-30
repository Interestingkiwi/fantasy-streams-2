"""
Bench points for a private league: Yahoo's lineups from its pages, the stats
from our own NHL game data.

Yahoo's public API will not serve a private league, and reading a team's page
for every day (12 a day, ~1 MB each) is too much to ask of a browser. Two cheap
sources cover it instead:

- **Lineups: the Starting Rosters page for a date** (`startingrosters?date=D`)
  shows every team's slot for every player that day, and each player's NHL team
  and positions. Checked against the API for league 5848 on 2026-09-29: all 202
  slots identical. The bookmarklet reads one page per game day, in the signed-in
  Yahoo tab, and keeps only `[yahooId, slot]` per player - a few KB a day.
- **Pairings: League Home by week** (`?matchup_week=N&module=matchups`) links
  every matchup as `matchup?week=N&mid1=A&mid2=B`.
- **Stats: `player_game_stats`**, the nightly NHL scrape - the same feed Yahoo
  scores from. Yahoo players are matched to NHL ids by name, with the page's
  NHL team and positions settling look-alikes (`yahoo_rosters.match`).

From those this builds exactly the day and week shapes `yahoo_league_api`
reads for a public league - days of `[yahooId, slot, eligible, {statId: value}]`
and weeks of matchups with each team's totals - and `bench_points.summarise`
works on them unchanged. The week totals are summed here from each day's
starters, which is how Yahoo keeps them.

**Every category Yahoo offers that the game data carries is covered** -
faceoffs (`skater/faceoffwins`, collected since 9/30/2026 and backfilled),
time on ice in minutes, and the ratios, built from their parts: GAA from goals
against over the goalie's own seconds, SV% from saves over shots against, SH%
from goals over shots. Only game-tying goals, which no NHL report carries, is
reported as unsupported. A row scraped before faceoffs were collected has none,
which is "not collected", not zero.

Author - Jason Druckenmiller
Created - 9/30/2026
Updated - 9/30/2026
"""

from collections import defaultdict

from yahoo_rosters import TEAM_FIXES, match

# Yahoo's NHL stat ids, from /game/nhl/stat_categories, by the codes League
# Home uses (SVpct for SV%)
YAHOO_IDS = {
    "G": "1", "A": "2", "P": "3", "+/-": "4", "PIM": "5", "PPG": "6", "PPA": "7",
    "PPP": "8", "SHG": "9", "SHA": "10", "SHP": "11", "GWG": "12", "GTG": "13",
    "SOG": "14", "SH%": "15", "FW": "16", "FL": "17", "GS": "18", "W": "19",
    "L": "20", "GA": "22", "GAA": "23", "SA": "24", "SV": "25", "SVpct": "26",
    "SV%": "26", "SHO": "27", "HIT": "31", "BLK": "32",
    # A skater's time on ice; a goalie's is 28, which League Home does not offer
    "TOI": "33",
}
# What the NHL game data does not carry at all
NOT_IN_GAME_DATA = frozenset({"13"})      # game-tying goals
LOWER_BETTER = frozenset({"FL", "L", "GA", "GAA"})
GOALIE_CODES = frozenset({"GS", "W", "L", "GA", "GAA", "SA", "SV", "SVpct", "SV%", "SHO"})

# The counts every ratio is rebuilt from, carried whatever the league scores
GA, GAA, SA, SV, SV_PCT = "22", "23", "24", "25", "26"
GOALS, SHOTS, SH_PCT = "1", "14", "15"


def _n(row, column):
    value = row.get(column)
    return float(value) if value is not None else 0.0


def game_line(row):
    """
    One `player_game_stats` row as {Yahoo stat id: value}. A goalie row (it
    carries saves or shots against) gets the goalie stats and keeps his seconds
    played under "toi", which a team's GAA is built from; a skater's gets the
    skater ones and no "toi", or his minutes would dilute the team's GAA.
    """
    if row.get("shotsAgainst") is not None or row.get("saves") is not None:
        seconds = _n(row, "timeOnIce")
        ga, sa, sv = _n(row, "goalsAgainst"), _n(row, "shotsAgainst"), _n(row, "saves")
        return {
            "18": _n(row, "gamesStarted"), "19": _n(row, "wins"), "20": _n(row, "losses"),
            GA: ga, SA: sa, SV: sv, "27": _n(row, "shutouts"),
            GAA: ga * 3600.0 / seconds if seconds else 0.0,
            SV_PCT: sv / sa if sa else None,
            "toi": seconds,
        }
    goals, pp_goals, sh_goals = _n(row, "goals"), _n(row, "ppGoals"), _n(row, "shGoals")
    shots = _n(row, "shots")
    line = {
        "1": goals, "2": _n(row, "assists"), "3": _n(row, "points"), "4": _n(row, "plusMinus"),
        "5": _n(row, "penaltyMinutes"), "6": pp_goals, "7": _n(row, "ppPoints") - pp_goals,
        "8": _n(row, "ppPoints"), "9": sh_goals, "10": _n(row, "shPoints") - sh_goals,
        "11": _n(row, "shPoints"), "12": _n(row, "gameWinningGoals"), "14": shots,
        "15": goals / shots if shots else None,
        "31": _n(row, "hits"), "32": _n(row, "blockedShots"),
        # Time on ice in minutes, as Yahoo counts it
        "33": _n(row, "timeOnIce") / 60.0,
    }
    # Faceoffs are a later column; a row scraped before it has none, which is
    # "not collected" rather than "took no faceoffs"
    if row.get("faceoffWins") is not None:
        line["16"] = _n(row, "faceoffWins")
        line["17"] = _n(row, "faceoffLosses")
    return line


def league_info(codes, points=None):
    """
    The `settings` shape bench_points needs, from League Home's categories.
    Returns (info, unsupported codes).
    """
    categories, unsupported = [], []
    for code in codes:
        stat_id = YAHOO_IDS.get(code)
        if not stat_id or stat_id in NOT_IN_GAME_DATA:
            unsupported.append(code)
            continue
        categories.append({"id": stat_id, "name": "SV%" if code == "SVpct" else code, "full": code,
                           "higherBetter": code not in LOWER_BETTER, "displayOnly": False,
                           "goalie": code in GOALIE_CODES})
    return {"scoring": "headpoint" if points else "head", "categories": categories}, unsupported


def split_info(text):
    """'COL - C,LW' -> ('COL', ['C', 'LW']), as the rosters page shows it."""
    team, _, positions = str(text or "").partition(" - ")
    return team.strip(), [p.strip() for p in positions.split(",") if p.strip()]


def match_players(players, pool, aliases=None):
    """
    {yahooId: NHL playerId} for the lineups' players. `players` is {yahooId:
    [name, 'TEAM - POS']}; the page's team and positions settle look-alikes, so
    no call to Yahoo is needed.
    """
    details = {}
    parsed_players = []
    for yahoo_id, (name, info) in players.items():
        team, positions = split_info(info)
        details[yahoo_id] = {"team": TEAM_FIXES.get(team, team), "positions": positions}
        parsed_players.append({"name": name, "yahooId": yahoo_id})
    result = match({"teams": [{"players": parsed_players}]}, pool, aliases,
                   lookup=lambda ids: {i: details[i] for i in ids if i in details})
    return {p["yahooId"]: p["playerId"] for p in result["teams"][0]["players"] if p.get("playerId")}


def build_days(lineups, ids, lines):
    """
    The `yahoo_league_api.day` shape from stored lineups. `ids` maps Yahoo ids
    to NHL ids, `lines` is {(NHL id, date): game line}; a player with no line
    that day had no game.
    """
    days = []
    for when in sorted(lineups.get("dates") or {}):
        teams = {}
        for team, rows in lineups["dates"][when].items():
            out = []
            for yahoo_id, slot in rows:
                _team, positions = split_info((lineups.get("players") or {}).get(yahoo_id, ["", ""])[1])
                line = lines.get((ids.get(yahoo_id), when)) or {}
                out.append([yahoo_id, slot, positions, {k: v for k, v in line.items() if k != "toi"}
                            if line else {}])
            teams[team] = out
        days.append({"date": when, "teams": teams})
    return days


def build_weeks(weeks, lineups, ids, lines, today, not_starting):
    """
    The `yahoo_league_api.scoreboard` shape: each week's pairings with every
    team's totals, summed over its days from the players in starting slots.
    """
    out = []
    dates = lineups.get("dates") or {}
    for week in weeks:
        pairs = (lineups.get("pairs") or {}).get(str(week["week"])) or []
        totals = defaultdict(lambda: defaultdict(float))
        for when, day in dates.items():
            if not (week["start"] <= when <= week["end"]):
                continue
            for team, rows in day.items():
                for yahoo_id, slot in rows:
                    if slot in not_starting:
                        continue
                    line = lines.get((ids.get(yahoo_id), when))
                    if not line:
                        continue
                    for stat, value in line.items():
                        if stat in (GAA, SV_PCT, SH_PCT) or value is None:
                            continue
                        totals[team][stat] += value
        finished = {}
        for team, sums in totals.items():
            team_totals = dict(sums)
            seconds = team_totals.pop("toi", 0.0)
            if SA in team_totals or GA in team_totals:
                team_totals[GAA] = team_totals.get(GA, 0.0) * 3600.0 / seconds if seconds else None
                team_totals[SV_PCT] = (team_totals.get(SV, 0.0) / team_totals[SA]
                                       if team_totals.get(SA) else None)
            if team_totals.get(SHOTS):
                team_totals[SH_PCT] = team_totals.get(GOALS, 0.0) / team_totals[SHOTS]
            finished[team] = team_totals
        matchups = [{"teams": [str(a), str(b)], "names": {},
                     "totals": {str(a): finished.get(str(a), {}), str(b): finished.get(str(b), {})}}
                    for a, b in pairs]
        out.append({"week": week["week"], "start": week["start"], "end": week["end"],
                    "status": "postevent" if week["end"] < today else "midevent",
                    "playoffs": False, "matchups": matchups})
    return out
