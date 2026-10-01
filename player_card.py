"""
One player's card, for the player modal on League Home.

The old site had a modal per question - his last game's line, his trend
table, his power-play usage, a goalie's starts, his week's opponents - each
opened from a different cell. Here they are one card with a tab each, built
by `card()` from rows the route loads, so everything below is pure:

- **Stats** - his season so far and the same stats over his last 20, 10 and 5
  games and at home and on the road (`season_stats.windows`), beside his
  projection per game. The game log follows it.
- **Line & PP** (skaters) - the line he played on last game, his linemates and
  how long they were on together, who else he skated with, and his power-play
  unit, from `game_lines`; then his line game by game.
- **Starts** (goalies) - his share of his team's starts over its last ten
  games and the season, the rest he would have for his team's next game, and
  how he has done by rest and by venue.
- **Schedule** - his team's games in the chosen week with each opponent's
  strength, from `team_stats`.

**Small samples are named, not hidden or dressed up.** Every window and split
carries its game count, and the page says when there is too little to read
anything into; a goalie's record on no rest over two starts is shown as two
starts, not as a percentage.

Author - Jason Druckenmiller
Created - 10/1/2026
Updated - 10/1/2026
"""

from datetime import date, timedelta

import daily_value as dv
import season_stats

LOG_GAMES = 10
LINE_HISTORY = 10
GOALIE_RECENT_TEAM_GAMES = 10


def _n(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def projection_line(row):
    """
    {code: projected value per game} - per start for a goalie, which is how
    `daily_value` rates them. Ratios come straight from the projection.
    """
    if not row:
        return {}
    goalie = row.get('positionCode') == 'G'
    games = _n(row.get('proj_gamesStarted') if goalie else row.get('projectedGames'))
    if not games:
        return {}
    out = {}
    for code, column in dv.COUNTING_COLUMNS.items():
        value = _n(row.get(column))
        if value is not None:
            out[code] = value / games
    for code, (first, second) in dv.DERIVED_COLUMNS.items():
        a, b = _n(row.get(first)), _n(row.get(second))
        if a is not None and b is not None:
            out[code] = (a - b) / games
    for code, column in dv.RATE_COLUMNS.items():
        value = _n(row.get(column))
        if value is not None:
            out[code] = value
    goals, shots = _n(row.get('proj_goals')), _n(row.get('proj_shots'))
    if goals is not None and shots:
        out['SH%'] = goals / shots * 100
    wins, losses = _n(row.get('proj_totalFaceoffWins')), _n(row.get('proj_totalFaceoffLosses'))
    if wins is not None and losses is not None and wins + losses:
        out['FO%'] = wins / (wins + losses) * 100
    return out


def game_log(games, lines_by_game, limit=LOG_GAMES):
    """His last `limit` games, newest first, each with its stat line and his line that night."""
    log = []
    for game in reversed(games[-limit:]):
        lines = lines_by_game.get(game.get('gameId')) or {}
        log.append({
            'date': game.get('gameDate'), 'gameId': game.get('gameId'),
            'opponent': game.get('opponentTeamAbbrev'), 'home': game.get('homeRoad') == 'H',
            'stats': season_stats.game_line(game),
            'line': lines.get('line'), 'unit': lines.get('unit'), 'ppUnit': lines.get('ppUnit'),
        })
    return log


def lines_summary(rows, names, opponents):
    """
    His line in his last game and his line game by game, from `game_lines`
    rows newest first. `names` is {playerId: name}; `opponents` {gameId: team}.
    None with no rows.
    """
    if not rows:
        return None

    def person(player_id, seconds=None):
        entry = {'playerId': str(player_id), 'name': names.get(int(player_id)) or names.get(str(player_id))
                 or f'#{player_id}'}
        if seconds is not None:
            entry['seconds'] = seconds
        return entry

    last = rows[0]
    on_line = {int(p) for p in last.get('lineMates') or []}
    shared = {int(p): s for p, s in last.get('mates') or []}
    # The last game his team had a power play says which unit he is on; a
    # night without one says nothing.
    powered = next((r for r in rows if (r.get('teamPpSeconds') or 0) > 0), None)
    return {
        'date': last.get('gameDate'), 'opponent': opponents.get(last.get('gameId')),
        'unit': last.get('unit'), 'line': last.get('line'),
        'lineMates': [person(p, shared.get(int(p))) for p in last.get('lineMates') or []],
        'lineSeconds': last.get('lineSeconds'),
        'evenSeconds': last.get('evenSeconds'),
        # Who else he skated with at 5-on-5 - a line change in progress shows here first
        'others': [person(p, s) for p, s in last.get('mates') or [] if int(p) not in on_line],
        'pp': None if not powered else {
            'date': powered.get('gameDate'), 'opponent': opponents.get(powered.get('gameId')),
            'unit': powered.get('ppUnit'),
            'mates': [person(p) for p in powered.get('ppMates') or []],
            'seconds': powered.get('ppSeconds'), 'teamSeconds': powered.get('teamPpSeconds'),
        },
        'history': [{
            'date': r.get('gameDate'), 'opponent': opponents.get(r.get('gameId')),
            'line': r.get('line'), 'unit': r.get('unit'),
            'ppUnit': r.get('ppUnit') if (r.get('teamPpSeconds') or 0) > 0 else None,
            'teamPp': bool(r.get('teamPpSeconds')),
            'mates': [person(p) for p in r.get('lineMates') or []],
        } for r in rows[:LINE_HISTORY]],
    }


def _record(games):
    """GP, W and the ratios for a goalie's split, from his appearances."""
    if not games:
        return {'games': 0}
    cells = season_stats.line(games, season_stats.GOALIE_STATS)
    value = lambda code: (cells.get(code) or {}).get('total')
    return {'games': len(games), 'starts': value('GS'), 'wins': value('W'),
            'svpct': value('SVpct'), 'gaa': value('GAA')}


def goalie_usage(games, team, schedule, today, through=None):
    """
    His starts against his team's games, his rest before its next one, and
    his record by rest and by venue this season.

    `schedule` is [(date, home, away)] for the season. Team games count up to
    `through`, the last night scraped (yesterday if not given), so a game not
    yet in the data is not counted as one he missed.
    """
    today_iso = today.isoformat()
    through = min(through or today_iso, (today - timedelta(days=1)).isoformat())
    team_dates = sorted(d for d, home, away in schedule if team in (home, away))
    past = [d for d in team_dates if d <= through]
    started = {g.get('gameDate') for g in games if (_n(g.get('gamesStarted')) or 0) >= 1}

    recent = past[-GOALIE_RECENT_TEAM_GAMES:]
    upcoming = next(((d, home, away) for d, home, away in sorted(schedule)
                     if d >= today_iso and team in (home, away)), None)

    appearances = sorted(g.get('gameDate') for g in games if g.get('gameDate'))
    next_game = None
    if upcoming:
        day, home, away = upcoming
        rest = None
        if appearances:
            rest = (date.fromisoformat(day) - date.fromisoformat(appearances[-1])).days - 1
        before = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
        next_game = {'date': day, 'opponent': away if home == team else home, 'home': home == team,
                     'rest': rest, 'teamBackToBack': before in team_dates}

    # Rest before each appearance - days since his previous one. The first of
    # the season has no previous, so it is left out of the rest split.
    by_rest = {'0': [], '1': [], '2+': []}
    previous = None
    for game in sorted(games, key=lambda g: g.get('gameDate') or ''):
        day = game.get('gameDate')
        if previous and day:
            gap = (date.fromisoformat(day) - date.fromisoformat(previous)).days - 1
            by_rest['0' if gap <= 0 else '1' if gap == 1 else '2+'].append(game)
        previous = day or previous

    return {
        'team': team,
        'recent': {'teamGames': len(recent), 'starts': sum(1 for d in recent if d in started)},
        'season': {'teamGames': len(past), 'starts': sum(1 for d in past if d in started)},
        'lastGame': appearances[-1] if appearances else None,
        'next': next_game,
        'byRest': {key: _record(rows) for key, rows in by_rest.items()},
        'byVenue': {'home': _record([g for g in games if g.get('homeRoad') == 'H']),
                    'road': _record([g for g in games if g.get('homeRoad') == 'R'])},
    }


# What an opponent gives up to a skater, and what it throws at a goalie:
# (key, team_stats column, higher is better for the player)
SKATER_OPPONENT = [('ga', 'goalsAgainstPerGame', True), ('sa', 'shotsAgainstPerGame', True),
                   ('pk', 'penaltyKillPct', False)]
GOALIE_OPPONENT = [('gf', 'goalsForPerGame', False), ('sf', 'shotsForPerGame', True),
                   ('pp', 'powerPlayPct', False)]
OPPONENT_WINDOWS = ('season', 'last-2w')


def opponent_table(team_stats, goalie):
    """
    {window: {team: {key: value, key+'Rank': 1..n, games}}} - each opponent's
    numbers with a rank among teams with games in that window, 1 the kindest
    to this player (most goals allowed, for a skater; fewest scored, for a
    goalie - except shots, which make a goalie's saves).
    """
    measures = GOALIE_OPPONENT if goalie else SKATER_OPPONENT
    table = {}
    for window in OPPONENT_WINDOWS:
        rows = [r for r in team_stats if r.get('statWindow') == window and (r.get('gamesPlayed') or 0) > 0]
        teams = {r['teamCode']: {'games': r.get('gamesPlayed')} for r in rows}
        for key, column, higher in measures:
            ranked = sorted((r for r in rows if r.get(column) is not None),
                            key=lambda r: r[column], reverse=higher)
            for rank, r in enumerate(ranked, start=1):
                teams[r['teamCode']][key] = r[column]
                teams[r['teamCode']][f'{key}Rank'] = rank
        table[window] = {'teams': teams, 'count': len(rows)}
    return table


def week_games(team, schedule, start, end, team_stats, goalie):
    """His team's games between `start` and `end`, each with its opponent's numbers."""
    if not team or not start or not end:
        return None
    table = opponent_table(team_stats, goalie)
    games = []
    for day, home, away in sorted(schedule):
        if start <= day <= end and team in (home, away):
            opponent = away if home == team else home
            games.append({'date': day, 'opponent': opponent, 'home': home == team,
                          **{window: table[window]['teams'].get(opponent) for window in OPPONENT_WINDOWS}})
    return {'games': games, 'teams': {w: table[w]['count'] for w in OPPONENT_WINDOWS}}


def card(player_id, projection, games, line_rows, names, schedule, team_stats,
         today, start=None, end=None, injury=None, through=None):
    """
    Everything the player modal shows for one player. `projection` is his
    `final_projections` row (or None), `games` his `player_game_stats` rows for
    the season oldest first, `line_rows` his `game_lines` rows newest first,
    `through` the last night in `player_game_stats`.
    """
    last_team = games[-1].get('teamAbbrev') if games else None
    team = last_team or (projection or {}).get('teamAbbrevs')
    goalie = (projection or {}).get('positionCode') == 'G' if projection else season_stats.is_goalie(games)
    opponents = {g.get('gameId'): g.get('opponentTeamAbbrev') for g in games}
    lines_by_game = {r.get('gameId'): r for r in line_rows}
    name = (projection or {}).get('fullName') or (games[-1].get('fullName') if games else None)

    return {
        'player': {
            'playerId': str(player_id), 'fullName': name, 'team': team,
            'positionCode': (projection or {}).get('positionCode') or ('G' if goalie else None),
            'eligiblePositions': (projection or {}).get('eligiblePositions'),
            'goalie': goalie, 'injury': injury,
        },
        'stats': season_stats.windows(games) if games else None,
        'projection': projection_line(projection),
        'log': game_log(games, lines_by_game),
        'lines': None if goalie else lines_summary(line_rows, names, opponents),
        'starts': goalie_usage(games, team, schedule, today, through) if goalie and team else None,
        'week': week_games(team, schedule, start, end, team_stats, goalie),
    }
