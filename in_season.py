"""
The preseason projections brought up to date with the season so far.

`final_projections` is built once, in the summer, and never touched again in
season. Four things it says go stale, and League Home reads them from this
season's games (`player_game_stats`) instead:

- **Where a player plays.** His team is the one he played his latest game
  for. On 10/12/2026, 16 projected players were already playing somewhere
  else - Kreider for Montreal, Knies for Columbus, Talbot for Columbus - and
  were being planned on their old team's schedule. A player traded but not
  yet played for his new team stays where he was until he does.
- **The players nobody projected.** A rookie who made the team, a call-up -
  T.J. Hughes in Colorado, Gavin McKenna in Toronto - had no row, so no Stat
  Sourcing could value him, a roster scrape dropped him, and he was never a
  free agent. Anyone with a game this season and no projection now gets a
  projection-shaped row (`projectionSource` 'in-season'), built below.
- **Goalies' share of starts** - `goalie_usage`, applied last.
- **Which goalies cannot start.** A goalie ESPN has out, on IR or suspended
  is marked with the nights he cannot start - from the date of ESPN's note up
  to his expected return (`outFrom`, `outUntil`) - and `goalie_starts` gives
  those nights to his teammates. Day-to-day is a doubt, not an absence, as on
  a roster. A note older than his last game is ignored: he has played since.
  So is a goalie on no NHL roster in the preseason who has not played since,
  until he does. Only ESPN's report and the rosters move a team's starts,
  never a league's own Out ticks or Yahoo's slots - those say who a manager
  is starting, not who the team is.

**A row for an unprojected player is Combined's blend** (`stat_sourcing`),
with a stand-in for the projection he never had: what the league's players at
his position produce for the ice time he gets. Per second of ice time, his
position's league rate times his time on ice a game - power-play goals and
points per second of power-play time, times his - and plus/minus at zero.
Ice time says what role a team has given him, which is most of what a
projection knows; the league rate says nothing about his talent, which is
why his own games take over at Combined's measured pace (`PRIOR_GAMES`). A
goalie's stand-in is the league's average start. His projected games are
his share of his team's games since his first, over the season, so a call-up
who has dressed for one game in four is not ranked as an every-night player.
A goalie with relief appearances and no start gets no row: there is no start
to value.

These rows are projections in every sense the page uses: they are valued,
ranked on the draft board, matched on rosters and offered as free agents. Under
Combined they are left as built, since they are already that blend; Season to
date reads them back to his season rate like anyone else.

`python in_season.py` lists who has games and no projection, and who has
changed teams; `--yahoo` adds each unprojected player's Yahoo ownership, from
Yahoo's public player search.

Author - Jason Druckenmiller
Created - 10/12/2026
Updated - 10/12/2026
"""

import argparse
import logging
import threading
import time
from collections import defaultdict

import goalie_starts as gs
import goalie_usage
import stat_sourcing as ss
from db import fetch_all

log = logging.getLogger(__name__)

IN_SEASON = ss.IN_SEASON_SOURCE

# The season's game data only changes when the nightly job runs
FACTS_TTL_SECONDS = 3600
_facts_cache = {}
_facts_lock = threading.Lock()

# The ice time each stat is a rate of. Everything else is per second of all
# of it; plus/minus has no stand-in rate (zero).
PP_COLUMNS = frozenset({'ppGoals', 'ppPoints'})
NO_PRIOR = frozenset({'plusMinus'})
SKATER_EXTRA = ('ppTimeOnIce',)

# ESPN's statuses that keep a player off the ice; day-to-day is a doubt
OUT_STATUSES = frozenset({'Out', 'Injured Reserve', 'Suspension'})

# Yahoo's public player search, over the current season's player pool
YAHOO_SEARCH = "https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2/league/nhl.l.public/"

# Yahoo's vocabulary for an NHL position
ELIGIBLE = {'C': 'C', 'L': 'LW', 'R': 'RW', 'D': 'D', 'G': 'G'}

# The columns a built row fills, beyond the projection columns. The rest of
# `final_projections`' 40 are left out - nothing reads them for a row like this.
STAT_COLUMNS = sorted(set(ss.SOURCE_COLUMNS.values()))


# ------------------------------------------------------------------ the reads

def season_facts(season, fresh=False):
    """`facts_from` this season's game data, read at most hourly. None before
    the table exists or with no season."""
    if not season:
        return None
    with _facts_lock:
        cached = _facts_cache.get(season)
        if cached and not fresh and time.monotonic() - cached[0] < FACTS_TTL_SECONDS:
            return cached[1]
    bounds = {'first': season * 1_000_000, 'next': (season + 1) * 1_000_000}
    counted = 'NOT ("positionCode" IS NULL AND COALESCE("gamesStarted", 0) < 1)'
    sums = ', '.join(f'SUM(CASE WHEN {counted} THEN "{c}" END) AS "{c}"'
                     for c in STAT_COLUMNS + list(SKATER_EXTRA))
    try:
        rows = fetch_all(
            'SELECT "playerId", "teamAbbrev", MAX("fullName") AS "fullName",'
            ' MAX("positionCode") AS "positionCode", MIN("gameDate") AS first,'
            ' MAX("gameDate") AS last, COUNT(*) AS appearances,'
            f' COUNT(*) FILTER (WHERE {counted}) AS games,'
            ' COUNT(*) FILTER (WHERE COALESCE("gamesStarted", 0) >= 1) AS starts,'
            f' {sums} FROM player_game_stats WHERE "gameId" >= :first AND "gameId" < :next'
            ' GROUP BY "playerId", "teamAbbrev"', bounds)
        games = fetch_all(
            'SELECT DISTINCT "teamAbbrev", "gameId", "gameDate" FROM player_game_stats'
            ' WHERE "gameId" >= :first AND "gameId" < :next', bounds)
    except Exception:                             # noqa: BLE001 - no table yet
        log.warning("No player_game_stats yet - the projections stand as built.")
        return None
    try:
        injuries = fetch_all('SELECT "playerId", "injuryStatus", "injuryDate", "returnDate"'
                             ' FROM current_injuries WHERE "playerId" IS NOT NULL')
    except Exception:                             # noqa: BLE001 - the report is optional
        injuries = []
    facts = facts_from(rows, games, injuries)
    with _facts_lock:
        _facts_cache[season] = (time.monotonic(), facts)
    return facts


def facts_from(rows, games, injuries=()):
    """
    {players, teamDates, usage, injuries} from the reads: per player per team
    his totals (a goalie's over his starts), every (team, gameId, gameDate),
    and ESPN's report (`current_injuries`: playerId, injuryStatus, injuryDate,
    returnDate).

    `players` is {playerId: {name, position, team (latest), first and last
    date, appearances, games, starts, totals, byTeam}}, position None for a
    goalie.
    """
    team_dates = defaultdict(set)
    for row in games:
        team_dates[row['teamAbbrev']].add((str(row['gameDate']), row['gameId']))

    players = {}
    for row in rows:
        key = str(row['playerId'])
        entry = players.setdefault(key, {
            'playerId': row['playerId'], 'name': row['fullName'], 'position': row['positionCode'],
            'team': None, 'first': None, 'last': '', 'appearances': 0, 'games': 0, 'starts': 0,
            'totals': defaultdict(float), 'byTeam': {}})
        last = str(row['last'])
        if last > entry['last']:
            entry.update(team=row['teamAbbrev'], last=last, name=row['fullName'])
        first = str(row['first'])
        entry['first'] = min(entry['first'] or first, first)
        entry['position'] = entry['position'] or row['positionCode']
        for field in ('appearances', 'games', 'starts'):
            entry[field] += int(row[field] or 0)
        for column in STAT_COLUMNS + list(SKATER_EXTRA):
            if row.get(column) is not None:
                entry['totals'][column] += float(row[column])
        entry['byTeam'][row['teamAbbrev']] = {
            'first': first, 'last': last, 'appearances': int(row['appearances'] or 0),
            'games': int(row['games'] or 0), 'starts': int(row['starts'] or 0)}

    goalie_rows = [{'playerId': key, 'teamAbbrev': team, **t}
                   for key, p in players.items() if p['position'] is None
                   for team, t in p['byTeam'].items()]
    flat_games = [{'teamAbbrev': team, 'gameId': game_id, 'gameDate': day}
                  for team, entries in team_dates.items() for day, game_id in entries]
    return {'players': players, 'teamDates': {t: sorted(d for d, _g in v) for t, v in team_dates.items()},
            'usage': goalie_usage.usage_from(goalie_rows, flat_games),
            'injuries': {str(int(r['playerId'])): r for r in injuries if r.get('playerId') is not None}}


# ------------------------------------------------------------------ the pool

def pool(projections, schedule, facts=None):
    """
    The projections for this season as League Home values them: each on his
    current team, the unprojected added, goalies' starts blended with their
    usage. `facts` defaults to this season's (`season_facts`); with none - no
    game data yet - the projections come back as they are.
    """
    if facts is None:
        facts = season_facts(ss.schedule_season(schedule))
    if not facts:
        return list(projections)
    team_games = goalie_usage.team_game_counts(schedule)
    rows = current_teams(projections, facts)
    rows += unprojected_rows(facts, {str(p.get('playerId')) for p in projections}, team_games)
    opening = min((str(day) for day, _home, _away in schedule), default=None)
    return unavailable_goalies(goalie_usage.apply(rows, facts['usage'], team_games), facts, opening)


def unavailable_goalies(rows, facts, opening=None):
    """
    The rows, each goalie who cannot start marked with the nights he cannot
    (`outFrom`, `outUntil` - None for no end), as new dicts where marked:

    - one ESPN has out, on IR or suspended, from the note's date to his
      expected return. Day-to-day is not out, and a note older than his last
      game is stale.
    - one on no NHL roster when the preseason pipeline synced them
      (`onNhlRoster` false) who has not played a game since: from `opening`,
      with no end. Jonathan Quick, unsigned, was otherwise taking the Rangers'
      nights once both their goalies were hurt. His first game makes him a
      goalie again.
    """
    out = []
    for row in rows:
        key = str(row.get('playerId'))
        if row.get('positionCode') != 'G':
            out.append(row)
            continue
        note = facts.get('injuries', {}).get(key)
        last = (facts['players'].get(key) or {}).get('last')
        if note and note.get('injuryStatus') in OUT_STATUSES:
            dated = str(note.get('injuryDate') or '')[:10] or None
            if dated and not (last and last > dated):
                row = {**row, 'outFrom': dated, 'outUntil': str(note.get('returnDate') or '')[:10] or None}
        elif row.get('onNhlRoster') is False and not last and opening:
            row = {**row, 'outFrom': opening, 'outUntil': None}
        out.append(row)
    return out


def current_teams(projections, facts):
    """The projections, a player who has played for another team this season
    moved to it (`projectedTeam` keeps the old one) - as new dicts where moved."""
    out = []
    for row in projections:
        seen = facts['players'].get(str(row.get('playerId')))
        if seen and seen['team'] and seen['team'] != gs.primary_team(row.get('teamAbbrevs')):
            row = {**row, 'teamAbbrevs': seen['team'], 'projectedTeam': row.get('teamAbbrevs')}
        out.append(row)
    return out


def unprojected_rows(facts, projected_ids, team_games):
    """A projection-shaped row for everyone with a game and no projection
    (see the docstring), skaters and goalies with a start."""
    players = facts['players']
    skater_rates, goalie_rates = league_rates(players)
    out = []
    for key, player in players.items():
        if key in projected_ids:
            continue
        season = team_games.get(player['team'])
        share = _games_share(player, facts['teamDates'], 'games' if player['position'] else 'starts')
        if not season or not share:
            continue
        if player['position'] is None:
            out.append(_goalie_row(player, goalie_rates, season, share,
                                   _games_share(player, facts['teamDates'], 'appearances')))
        else:
            out.append(_skater_row(player, skater_rates.get(player['position']) or {}, season, share))
    return out


def league_rates(players):
    """
    ({position: {column: per second}}, {column: per start}) over every
    player's games this season: skaters per second of the ice time each stat
    is a rate of, goalies per start.
    """
    totals, seconds = defaultdict(lambda: defaultdict(float)), defaultdict(lambda: defaultdict(float))
    goalie_totals, starts = defaultdict(float), 0
    for player in players.values():
        if player['position'] is None:
            starts += player['games']
            for column, value in player['totals'].items():
                goalie_totals[column] += value
            continue
        position = player['position']
        for column, value in player['totals'].items():
            totals[position][column] += value
        seconds[position]['all'] += player['totals'].get('timeOnIce', 0.0)
        seconds[position]['pp'] += player['totals'].get('ppTimeOnIce', 0.0)
    skaters = {}
    for position, columns in totals.items():
        rates = {}
        for column, value in columns.items():
            basis = seconds[position]['pp' if column in PP_COLUMNS else 'all']
            if basis > 0 and column not in NO_PRIOR:
                rates[column] = value / basis
        skaters[position] = rates
    goalies = {column: value / starts for column, value in goalie_totals.items()} if starts else {}
    return skaters, goalies


def _games_share(player, team_dates, field):
    """His `field` (games, starts, appearances) for his current team over its
    games since his first for it, at most one."""
    mine = player['byTeam'].get(player['team']) or {}
    since = sum(1 for day in team_dates.get(player['team'], []) if day >= mine.get('first', '9999'))
    return min(1.0, mine.get(field, 0) / since) if since else 0.0


def _blended(prior, season, games, code):
    """Combined's blend per game (per start) of a stand-in rate and his own."""
    k = ss.PRIOR_GAMES[code]
    if prior is None:
        return season
    return (k * prior + games * season) / (k + games)


def _base_row(player, season, share):
    position = player['position'] or 'G'
    return {
        'playerId': player['playerId'], 'fullName': player['name'], 'positionCode': position,
        'teamAbbrevs': player['team'], 'eligiblePositions': ELIGIBLE.get(position, position),
        'projectedGames': round(share * season, 1), 'projectionSource': IN_SEASON,
        'onNhlRoster': True, 'productionTrend': None, 'peripheralTrend': None,
    }


def _skater_row(player, rates, season, share):
    row = _base_row(player, season, share)
    games = player['games']
    totals = player['totals']
    per_second = {'all': totals.get('timeOnIce', 0.0) / games,
                  'pp': totals.get('ppTimeOnIce', 0.0) / games}
    for column, source in ss.SOURCE_COLUMNS.items():
        if column in ss.GOALIE_COLUMNS or column not in ss.BLEND_CODES:
            continue
        own = totals.get(source, 0.0) / games
        prior = (0.0 if source in NO_PRIOR else
                 rates[source] * per_second['pp' if source in PP_COLUMNS else 'all']
                 if source in rates else None)
        row[column] = _blended(prior, own, games, ss.BLEND_CODES[column]) * row['projectedGames']
    row['proj_points'] = row['proj_goals'] + row['proj_assists']
    row['proj_totalFaceoffs'] = row['proj_totalFaceoffWins'] + row['proj_totalFaceoffLosses']
    return row


def _goalie_row(player, rates, season, share, appearance_share):
    row = _base_row(player, season, max(share, appearance_share))
    starts = player['games']                 # a goalie's counted games are his starts
    row['proj_gamesStarted'] = round(share * season, 1)
    totals = player['totals']
    for column in ss.GOALIE_COLUMNS:
        source = ss.SOURCE_COLUMNS[column]
        if column not in ss.BLEND_CODES:
            continue
        own = totals.get(source, 0.0) / starts
        row[column] = _blended(rates.get(source), own, starts, ss.BLEND_CODES[column]) * row['proj_gamesStarted']
    row['proj_saves'] = row['proj_shotsAgainst'] - row['proj_goalsAgainst']
    shots, allowed, seconds = row['proj_shotsAgainst'], row['proj_goalsAgainst'], row['proj_timeOnIce']
    row['proj_savePct'] = 1 - allowed / shots if shots else None
    row['proj_goalsAgainstAverage'] = allowed * 3600 / seconds if seconds else None
    return row


# ---------------------------------------------------------------- the report

def yahoo_lookup(name, team):
    """Yahoo's (percent owned, its change this week, eligible positions) for a
    name, from the public player search; the entry on `team` if several."""
    import requests
    import yahoo_rosters

    url = (f"{YAHOO_SEARCH}players;search={requests.utils.quote(name)};out=percent_owned?format=json_f")
    response = requests.get(url, headers=yahoo_rosters.HEADERS, timeout=yahoo_rosters.TIMEOUT_SECONDS)
    response.raise_for_status()
    found = [e['player'] for e in response.json()['fantasy_content']['league'].get('players') or []]

    def team_of(entry):
        abbr = str(entry.get('editorial_team_abbr') or '').upper()
        return yahoo_rosters.TEAM_FIXES.get(abbr, abbr)
    match = next((p for p in found if team_of(p) == team), found[0] if len(found) == 1 else None)
    if not match:
        return None
    # Yahoo leaves ownership out for a player nobody owns
    owned = match.get('percent_owned') or {}
    return (int(owned.get('value') or 0), int(float(owned.get('delta') or 0)),
            match.get('display_position'))


def main():
    parser = argparse.ArgumentParser(description="Who is playing with no projection, and who has moved.")
    parser.add_argument('--yahoo', action='store_true', help="Add each one's Yahoo ownership.")
    args = parser.parse_args()

    schedule = [(r['gameDate'], r['homeTeam'], r['awayTeam'])
                for r in fetch_all('SELECT "gameDate", "homeTeam", "awayTeam" FROM nhl_schedule')]
    facts = season_facts(ss.schedule_season(schedule))
    if not facts:
        print("No games this season yet.")
        return
    projections = fetch_all('SELECT * FROM final_projections')
    projected = {str(p['playerId']) for p in projections}
    team_games = goalie_usage.team_game_counts(schedule)
    built = {str(r['playerId']): r for r in unprojected_rows(facts, projected, team_games)}

    missing = [p for k, p in facts['players'].items() if k not in projected]
    owned = {}
    if args.yahoo:
        for p in missing:
            try:
                owned[p['name']] = yahoo_lookup(p['name'], p['team'])
            except Exception as exc:              # noqa: BLE001 - a report, not a job
                owned[p['name']] = str(exc)
    # Most owned on Yahoo first - the surprises managers are already chasing -
    # then the most games
    missing.sort(key=lambda p: (-(owned[p['name']][0] if isinstance(owned.get(p['name']), tuple) else -1),
                                -p['games'], p['name']))
    print(f"Playing this season with no projection: {len(missing)}\n")
    for p in missing:
        t = p['totals']
        if p['position'] is None:
            line = (f"{p['starts']} GS / {p['appearances']} GP, "
                    f"{1 - t.get('goalsAgainst', 0) / t['shotsAgainst']:.3f}" if t.get('shotsAgainst') else "relief only")
        else:
            line = (f"{p['games']} GP, {t.get('goals', 0):.0f}-{t.get('assists', 0):.0f}-{t.get('points', 0):.0f}, "
                    f"{t.get('timeOnIce', 0) / max(p['games'], 1) / 60:.1f} min")
        found = owned.get(p['name'])
        extra = ('' if not args.yahoo else
                 f"  Yahoo {found[0]}% owned ({found[1]:+d} this week), {found[2]}" if isinstance(found, tuple)
                 else f"  Yahoo: {found or 'not found'}")
        valued = '' if str(p['playerId']) in built else '  (no row: no start)'
        print(f"  {p['name']:<24} {p['team']:<4} {p['position'] or 'G':<2} {line}{extra}{valued}")

    moved = [(r, facts['players'][str(r['playerId'])]['team']) for r in projections
             if str(r['playerId']) in facts['players']
             and facts['players'][str(r['playerId'])]['team'] != gs.primary_team(r.get('teamAbbrevs'))]
    print(f"\nPlaying for another team than projected: {len(moved)}\n")
    for r, team in sorted(moved, key=lambda m: m[0]['fullName']):
        print(f"  {r['fullName']:<24} {r['teamAbbrevs']:<8} -> {team}")


if __name__ == '__main__':
    main()
