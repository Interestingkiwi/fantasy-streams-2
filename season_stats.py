"""
A player's season so far, from `player_game_stats`, in Yahoo's category codes.

The player card on League Home shows a player's season-to-date line and the
same stats over his last 20, 10 and 5 games and at home and on the road - the
windows `player_form` judges trends over, so an arrow on the roster view can
be read off the card as numbers. (Season to date Stat Sourcing needs every
player's totals at once, in projection columns rather than Yahoo codes, and
reads them with one SQL aggregate in `stat_sourcing` instead.)

**Every stat is a definition, not a column.** Each entry in `SKATER_STATS` /
`GOALIE_STATS` says how to read one game row and whether the stat is counted
or a ratio. A ratio - shooting percentage, save percentage, GAA, a share of
the team's power play - is rebuilt from its summed parts over the window,
never averaged game by game: three shots and a goal in one game and none in
the next is 33%, not the mean of 33% and nothing.

**Per game or totals, one table.** Every cell carries both, and the page
toggles between them. A goalie's "game" is an appearance - relief outings
count, as they do in Yahoo's totals.

**Arrows are `player_form.trend`'s test**, per stat: a window's per-game mean
against the season's in standard errors of an N-game mean, past
`player_form.TREND_Z`. Counting stats only - a ratio over five games is mostly
noise - and only once a player has played the window's games.

Author - Jason Druckenmiller
Created - 10/1/2026
Updated - 10/1/2026
"""

import math

import player_form


def _n(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number


def _col(name):
    return lambda g: _n(g.get(name))


def _diff(first, second):
    return lambda g: _n(g.get(first)) - _n(g.get(second))


def _sum(*names):
    return lambda g: sum(_n(g.get(n)) for n in names)


# kind: 'count' sums and divides by games; 'time' too, shown as minutes;
# 'rate' is num/den summed over the window, times `scale`.
SKATER_STATS = [
    {'code': 'G', 'label': 'Goals', 'group': 'Scoring', 'kind': 'count', 'of': _col('goals')},
    {'code': 'A', 'label': 'Assists', 'group': 'Scoring', 'kind': 'count', 'of': _col('assists')},
    {'code': 'P', 'label': 'Points', 'group': 'Scoring', 'kind': 'count', 'of': _col('points')},
    {'code': '+/-', 'label': 'Plus/minus', 'group': 'Scoring', 'kind': 'count', 'of': _col('plusMinus')},
    {'code': 'EVP', 'label': 'Even-strength points', 'group': 'Scoring', 'kind': 'count', 'of': _col('evPoints')},
    {'code': 'PPG', 'label': 'Power-play goals', 'group': 'Scoring', 'kind': 'count', 'of': _col('ppGoals')},
    {'code': 'PPA', 'label': 'Power-play assists', 'group': 'Scoring', 'kind': 'count', 'of': _diff('ppPoints', 'ppGoals')},
    {'code': 'PPP', 'label': 'Power-play points', 'group': 'Scoring', 'kind': 'count', 'of': _col('ppPoints')},
    {'code': 'SHG', 'label': 'Shorthanded goals', 'group': 'Scoring', 'kind': 'count', 'of': _col('shGoals')},
    {'code': 'SHA', 'label': 'Shorthanded assists', 'group': 'Scoring', 'kind': 'count', 'of': _diff('shPoints', 'shGoals')},
    {'code': 'SHP', 'label': 'Shorthanded points', 'group': 'Scoring', 'kind': 'count', 'of': _col('shPoints')},
    {'code': 'GWG', 'label': 'Game-winning goals', 'group': 'Scoring', 'kind': 'count', 'of': _col('gameWinningGoals')},
    {'code': 'SOG', 'label': 'Shots on goal', 'group': 'Shooting', 'kind': 'count', 'of': _col('shots')},
    {'code': 'ATT', 'label': 'Shot attempts', 'group': 'Shooting', 'kind': 'count',
     'of': _sum('shots', 'missedShots', 'shotAttemptsBlocked'), 'needs': 'missedShots'},
    {'code': 'MISS', 'label': 'Missed shots', 'group': 'Shooting', 'kind': 'count',
     'of': _col('missedShots'), 'needs': 'missedShots'},
    {'code': 'ABLK', 'label': 'Attempts blocked', 'group': 'Shooting', 'kind': 'count',
     'of': _col('shotAttemptsBlocked'), 'needs': 'shotAttemptsBlocked'},
    {'code': 'SH%', 'label': 'Shooting %', 'group': 'Shooting', 'kind': 'rate',
     'num': _col('goals'), 'den': _col('shots'), 'scale': 100},
    {'code': 'HIT', 'label': 'Hits', 'group': 'Physical', 'kind': 'count', 'of': _col('hits')},
    {'code': 'BLK', 'label': 'Blocked shots', 'group': 'Physical', 'kind': 'count', 'of': _col('blockedShots')},
    {'code': 'PIM', 'label': 'Penalty minutes', 'group': 'Physical', 'kind': 'count', 'of': _col('penaltyMinutes')},
    {'code': 'TK', 'label': 'Takeaways', 'group': 'Physical', 'kind': 'count', 'of': _col('takeaways')},
    {'code': 'GV', 'label': 'Giveaways', 'group': 'Physical', 'kind': 'count', 'of': _col('giveaways')},
    {'code': 'FW', 'label': 'Faceoffs won', 'group': 'Faceoffs', 'kind': 'count',
     'of': _col('faceoffWins'), 'needs': 'faceoffWins'},
    {'code': 'FL', 'label': 'Faceoffs lost', 'group': 'Faceoffs', 'kind': 'count',
     'of': _col('faceoffLosses'), 'needs': 'faceoffLosses'},
    {'code': 'FO%', 'label': 'Faceoff %', 'group': 'Faceoffs', 'kind': 'rate',
     'num': _col('faceoffWins'), 'den': _sum('faceoffWins', 'faceoffLosses'), 'scale': 100,
     'needs': 'faceoffWins'},
    {'code': 'TOI', 'label': 'Time on ice', 'group': 'Ice time', 'kind': 'time', 'of': _col('timeOnIce')},
    {'code': 'EVTOI', 'label': 'Even strength', 'group': 'Ice time', 'kind': 'time',
     'of': _col('evTimeOnIce'), 'needs': 'evTimeOnIce'},
    {'code': 'PPTOI', 'label': 'Power play', 'group': 'Ice time', 'kind': 'time',
     'of': _col('ppTimeOnIce'), 'needs': 'ppTimeOnIce'},
    {'code': 'SHTOI', 'label': 'Shorthanded', 'group': 'Ice time', 'kind': 'time',
     'of': _col('shTimeOnIce'), 'needs': 'shTimeOnIce'},
    {'code': 'PP%', 'label': 'Share of team PP', 'group': 'Ice time', 'kind': 'rate',
     'num': _col('ppTimeOnIce'), 'den': _col('teamPpTimeOnIce'), 'scale': 100,
     'needs': 'teamPpTimeOnIce'},
    {'code': 'SHIFTS', 'label': 'Shifts', 'group': 'Ice time', 'kind': 'count',
     'of': _col('shifts'), 'needs': 'shifts'},
]

GOALIE_STATS = [
    {'code': 'GS', 'label': 'Games started', 'group': 'Results', 'kind': 'count', 'of': _col('gamesStarted')},
    {'code': 'W', 'label': 'Wins', 'group': 'Results', 'kind': 'count', 'of': _col('wins')},
    {'code': 'L', 'label': 'Losses', 'group': 'Results', 'kind': 'count', 'of': _col('losses')},
    {'code': 'OTL', 'label': 'Overtime losses', 'group': 'Results', 'kind': 'count', 'of': _col('otLosses')},
    {'code': 'SHO', 'label': 'Shutouts', 'group': 'Results', 'kind': 'count', 'of': _col('shutouts')},
    {'code': 'GA', 'label': 'Goals against', 'group': 'Goaltending', 'kind': 'count', 'of': _col('goalsAgainst')},
    {'code': 'SA', 'label': 'Shots against', 'group': 'Goaltending', 'kind': 'count', 'of': _col('shotsAgainst')},
    {'code': 'SV', 'label': 'Saves', 'group': 'Goaltending', 'kind': 'count', 'of': _col('saves')},
    {'code': 'SVpct', 'label': 'Save %', 'group': 'Goaltending', 'kind': 'rate',
     'num': _col('saves'), 'den': _col('shotsAgainst'), 'scale': 1},
    # GAA over the goalie's own seconds, so a pulled start or overtime counts
    # what it really was rather than an assumed hour
    {'code': 'GAA', 'label': 'Goals-against average', 'group': 'Goaltending', 'kind': 'rate',
     'num': _col('goalsAgainst'), 'den': _col('timeOnIce'), 'scale': 3600},
    {'code': 'TOI', 'label': 'Time on ice', 'group': 'Goaltending', 'kind': 'time', 'of': _col('timeOnIce')},
]

# Lower is better, for the arrows' colour - an arrow up in goals against is bad news.
LOWER_IS_BETTER = frozenset({'GA', 'L', 'OTL', 'FL', 'GV', 'GAA', 'MISS', 'ABLK'})


def stats_for(goalie):
    return GOALIE_STATS if goalie else SKATER_STATS


def is_goalie(games):
    return any(not g.get('positionCode') for g in games)


def line(games, stats):
    """
    {code: {'total', 'perGame'}} over a list of game rows, or {} with none. A
    stat whose column no game in the window carries (collected after the
    window was scraped) is None rather than a misleading zero.
    """
    if not games:
        return {}
    out = {}
    for stat in stats:
        needs = stat.get('needs')
        if needs and all(g.get(needs) is None for g in games):
            out[stat['code']] = None
            continue
        if stat['kind'] == 'rate':
            den = sum(stat['den'](g) for g in games)
            value = sum(stat['num'](g) for g in games) / den * stat['scale'] if den else None
            out[stat['code']] = {'total': value, 'perGame': value}
        else:
            total = sum(stat['of'](g) for g in games)
            out[stat['code']] = {'total': total, 'perGame': total / len(games)}
    return out


def windows(games):
    """
    The card's stats table: Season, L20, L10, L5, Home, Road over one player's
    games for one season, oldest first.

    {'goalie', 'columns': [{key, label, games}], 'rows': [{code, label, group,
    kind, values: {key: {total, perGame} | None}, marks: {key: 'up' | 'down'}}]}.
    A window he has not played enough games for is left out of `columns`.
    """
    goalie = is_goalie(games)
    stats = stats_for(goalie)
    home = [g for g in games if g.get('homeRoad') == 'H']
    road = [g for g in games if g.get('homeRoad') == 'R']

    columns = [{'key': 'season', 'label': 'Season', 'games': len(games)}]
    picked = {'season': games}
    for n in player_form.TREND_WINDOWS:
        if len(games) >= n:
            key = f'L{n}'
            columns.append({'key': key, 'label': f'Last {n}', 'games': n})
            picked[key] = games[-n:]
    for key, label, subset in (('home', 'Home', home), ('road', 'Road', road)):
        columns.append({'key': key, 'label': label, 'games': len(subset)})
        picked[key] = subset

    lines = {key: line(subset, stats) for key, subset in picked.items()}
    rows = []
    for stat in stats:
        code = stat['code']
        marks = {}
        if stat['kind'] != 'rate' and lines['season'].get(code) is not None:
            values = [stat['of'](g) for g in games]
            for n in player_form.TREND_WINDOWS:
                direction = player_form.trend(values, n) if len(games) > n else None
                if direction in ('up', 'down'):
                    marks[f'L{n}'] = direction
        rows.append({
            'code': code, 'label': stat['label'], 'group': stat['group'], 'kind': stat['kind'],
            'lowerIsBetter': code in LOWER_IS_BETTER,
            'values': {key: lines[key].get(code) for key in picked},
            'marks': marks,
        })
    return {'goalie': goalie, 'columns': columns, 'rows': rows}


def game_line(game):
    """One game as {code: value}, for the game log."""
    return {code: (cell or {}).get('total')
            for code, cell in line([game], stats_for(not game.get('positionCode'))).items()}

