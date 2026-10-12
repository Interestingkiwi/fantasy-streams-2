"""
Goalies' share of their team's starts, read again from how often they have
really started.

The projections give each goalie a season of starts - a call on each team's
tandem made in the summer, and by October some of those calls are wrong:
Colorado's were projected near half and half, and Blackwood started four of
the first five. So a goalie's projected starts become

    (k x projected share + starts) / (k + n) x the team's games

after n of his team's games: the projection counts as k games of evidence,
and his real usage takes over as the games pile up. Applied whatever the Stat
Sourcing - it is who starts, not what a start is worth.

**n counts from his first game for his current team**, not the team's first
game. Measured, that predicts the rest of a season better (below), for a
reason worth knowing: a starter hurt in training camp keeps his projected
share until he is back, rather than reading as a backup for every game he
missed. And it is what makes a trade work - a goalie traded in is judged on
his new team's games only, since his old tandem says nothing about the new
one. A goalie who has not played for his team yet keeps his projected share.

**Shares are reconciled per team first**, scaled down where a team's goalies
are projected for more starts than it has games (DET's come to 113 of 84), as
`goalie_starts._targets` does - or a blend would start from shares that cannot
all happen. Where a team's goalies fall short, the rest stays with nobody, as
it does there: the balancing hands it to its residual goalie.

**What changes is volume, never quality.** His counting stats are scaled with
his starts (and his appearances with them), so his per-start line - what
`daily_value` reads - is unchanged. Start odds (`goalie_starts`) are balanced
against the new totals, and everything after follows.

**k is measured**, by `derive_goalie_usage.py` on 2025-26: the k whose blend
after 5-40 team games best predicted each goalie's share of the rest of the
season, for the 81 goalies who played for one team all year. Counting from his
own first game it was 8 (within 1% of the best from 6 to 11); from the team's
first game 12, with a worse fit; over only the last 15 games 11, worse again.
After 20 games the blend's error was 0.142 against 0.164 for usage alone and
0.191 for the prior alone. The prior there is last season's share, which the
real projections should beat, so the measured k is low; `PRIOR_GAMES` sits at
the top of the band. Re-measure between seasons, never mid-season.

Author - Jason Druckenmiller
Created - 10/12/2026
Updated - 10/12/2026
"""

from collections import defaultdict

import daily_value as dv
import goalie_starts as gs

# Team games of trust in the projected share of starts (see the docstring)
PRIOR_GAMES = 10

# A goalie's counting columns, scaled with his starts so his per-start line
# stays put
COUNTING = ('proj_wins', 'proj_losses', 'proj_otLosses', 'proj_shutouts',
            'proj_goalsAgainst', 'proj_shotsAgainst', 'proj_saves', 'proj_timeOnIce')


def usage_from(rows, games):
    """
    {playerId: {'team', 'starts', 'games', 'first'}} for every goalie with a
    game this season: his latest team, his starts for it, its games since his
    first appearance for it, and that first date. From per goalie per team
    (first, last, starts) and every (team, gameId, gameDate) played -
    `in_season.season_facts` reads both.
    """
    dates = defaultdict(list)
    for row in games:
        dates[row['teamAbbrev']].append(str(row['gameDate']))

    latest = {}
    for row in rows:
        key = str(row['playerId'])
        if key not in latest or str(row['last']) > str(latest[key]['last']):
            latest[key] = row
    out = {}
    for key, row in latest.items():
        first = str(row['first'])
        out[key] = {
            'team': row['teamAbbrev'],
            'starts': int(row['starts'] or 0),
            'games': sum(1 for d in dates[row['teamAbbrev']] if d >= first),
            'first': first,
        }
    return out


def team_game_counts(schedule):
    """{team: games in the season} from [(date, home, away)]."""
    counts = defaultdict(int)
    for _day, home, away in schedule:
        counts[home] += 1
        counts[away] += 1
    return dict(counts)


def apply(rows, used, team_games, prior_games=PRIOR_GAMES):
    """
    `rows` with each goalie's projected starts blended with his usage on his
    current team, as new dicts where anything changed. A goalie whose row
    names another team than his usage (not yet moved), or who has no games
    for it, is left as projected. Each changed row carries `usage`: {starts,
    games, projectedShare, share}.
    """
    by_team = defaultdict(list)
    for index, row in enumerate(rows):
        if row.get('positionCode') == 'G':
            by_team[gs.primary_team(row.get('teamAbbrevs'))].append(index)

    out = list(rows)
    for team, indexes in by_team.items():
        season = team_games.get(team)
        if not season:
            continue
        starts = [max(0.0, dv.games_for(rows[i])) for i in indexes]
        total = sum(starts)
        scale = season / total if total > season else 1.0
        for index, projected in zip(indexes, starts):
            row = rows[index]
            seen = used.get(str(row.get('playerId')))
            if not seen or seen['team'] != team or seen['games'] <= 0:
                continue
            prior = projected * scale / season
            share = (prior_games * prior + seen['starts']) / (prior_games + seen['games'])
            out[index] = rescaled(row, share * season)
            out[index]['usage'] = {'starts': seen['starts'], 'games': seen['games'],
                                   'projectedShare': round(prior, 3), 'share': round(share, 3)}
    return out


def rescaled(row, starts):
    """A goalie's row at `starts` starts, his per-start line unchanged."""
    new = dict(row)
    before = dv.games_for(row)
    old_games = float(row.get('projectedGames') or 0)
    old_starts = float(row.get('proj_gamesStarted') or 0)
    new['proj_gamesStarted'] = starts
    # Appearances move with starts - relief outings come with the job. An
    # imported rookie has no projected starts, and his appearances stood in
    # for them.
    new['projectedGames'] = max(starts, old_games * starts / old_starts) if old_starts > 0 else starts
    after = dv.games_for(new)
    if before > 0:
        for column in COUNTING:
            value = row.get(column)
            if value is not None:
                new[column] = float(value) / before * after
    return new
