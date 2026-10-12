"""
Measures how many team games of trust a goalie's projected share of his
team's starts is worth against how often he has really started
(`goalie_usage.PRIOR_GAMES`).

Not part of any job - it prints a constant to copy into `goalie_usage.py` by
hand, as `derive_combined_weights.py` does for Combined. Run it between
seasons, once the season just finished is in `player_game_stats`:

    python derive_goalie_usage.py
    python derive_goalie_usage.py --season 2025

**The blend.** A goalie's share of his team's starts after n of its games is

    (k x projected share + starts) / (k + n)

so the projection counts as k games of evidence. k is the one whose blend
after 5, 10, 15, 20, 30 and 40 team games best predicts his share of the rest
of the season (squared error, weighted by the games remaining).

**The projection it is measured against** is the season before's: his starts
then over its games, the shares on each team scaled down where they add up to
more than one. The real projections are a person's call on each team's
tandem (`goalie_gp_overrides.csv`), which should be better than last season's
share - so this k is somewhat low, and usage takes over a little sooner than
it strictly should. Only goalies who played for one team all season are
tested; a traded goalie's usage is counted from his first game with his new
team, which this does not measure.

Two other ways of counting usage are printed beside it, to show they lose:
counting from the goalie's first appearance rather than the team's first game
(a backup who sits a week reads as a starter on his first start), and only the
team's last 15 games (a role that changed mid-season, at the price of noise).

Author - Jason Druckenmiller
Created - 10/12/2026
Updated - 10/12/2026
"""

import argparse
import math
from collections import defaultdict

import numpy as np

from db import fetch_all

CHECKPOINTS = (5, 10, 15, 20, 30, 40)
MIN_REMAINING = 15
K_GRID = np.arange(0, 301)
RECENT_GAMES = 15


def team_games(season):
    """{team: [gameId, ...] in date order} for one season."""
    rows = fetch_all(
        'SELECT DISTINCT "teamAbbrev", "gameId", "gameDate" FROM player_game_stats '
        'WHERE "gameId" >= :first AND "gameId" < :next ORDER BY "gameDate", "gameId"',
        {'first': season * 1_000_000, 'next': (season + 1) * 1_000_000})
    out = defaultdict(list)
    for row in rows:
        if not out[row['teamAbbrev']] or out[row['teamAbbrev']][-1] != row['gameId']:
            out[row['teamAbbrev']].append(row['gameId'])
    return out


def goalie_games(season):
    """{playerId: {team: {gameId: started}}} for one season."""
    rows = fetch_all(
        'SELECT "playerId", "teamAbbrev", "gameId", COALESCE("gamesStarted", 0) AS gs '
        'FROM player_game_stats WHERE "positionCode" IS NULL '
        'AND "gameId" >= :first AND "gameId" < :next',
        {'first': season * 1_000_000, 'next': (season + 1) * 1_000_000})
    out = defaultdict(lambda: defaultdict(dict))
    for row in rows:
        out[row['playerId']][row['teamAbbrev']][row['gameId']] = row['gs'] >= 1
    return out


def priors(season, played, games_by_team):
    """{playerId: share} - last season's starts over its games, reconciled per team."""
    before = f"{season - 1}{season}"
    rows = fetch_all(
        'SELECT "playerId", SUM("gamesStarted") AS gs FROM historic_goalies_baseline '
        'WHERE "seasonId" = :s GROUP BY "playerId"', {'s': before})
    season_games = 82
    raw = {row['playerId']: float(row['gs'] or 0) / season_games for row in rows}

    out = {}
    by_team = defaultdict(list)
    for player, teams in played.items():
        if player in raw and len(teams) == 1:
            by_team[next(iter(teams))].append(player)
    for team, players in by_team.items():
        total = sum(raw[p] for p in players)
        scale = 1.0 / total if total > 1 else 1.0
        for p in players:
            out[p] = raw[p] * scale
    return out


def samples(prior, played, games_by_team):
    """Arrays per way of counting: (prior, starts, n, target, weight)."""
    cols = {kind: defaultdict(list) for kind in ('team', 'appearance', 'recent')}
    for player, teams in played.items():
        if player not in prior or len(teams) != 1:
            continue
        team, mine = next(iter(teams.items()))
        schedule = games_by_team[team]
        started = np.array([1.0 if mine.get(g) else 0.0 for g in schedule])
        appeared = np.array([1.0 if g in mine else 0.0 for g in schedule])
        for n in CHECKPOINTS:
            remaining = len(schedule) - n
            if remaining < MIN_REMAINING:
                continue
            target = started[n:].mean()
            first = int(np.argmax(appeared[:n])) if appeared[:n].any() else n
            counts = {
                'team': (started[:n].sum(), n),
                'appearance': (started[first:n].sum(), n - first),
                'recent': (started[max(0, n - RECENT_GAMES):n].sum(), min(n, RECENT_GAMES)),
            }
            for kind, (s, games) in counts.items():
                c = cols[kind]
                c['prior'].append(prior[player])
                c['starts'].append(s)
                c['n'].append(games)
                c['target'].append(target)
                c['w'].append(remaining)
                c['checkpoint'].append(n)
    return {kind: {k: np.array(v) for k, v in c.items()} for kind, c in cols.items()}


def fit(data):
    """(best k, (k range within 1% of the best), {projection, usage, blend} RMSE by checkpoint)."""
    p, s, n, t, w = data['prior'], data['starts'], data['n'], data['target'], data['w']
    losses = np.array([np.sum(w * ((k * p + s) / np.maximum(k + n, 1e-9) - t) ** 2) for k in K_GRID])
    best = int(K_GRID[np.argmin(losses)])
    near = K_GRID[losses <= losses.min() * 1.01]
    observed = np.where(n > 0, s / np.maximum(n, 1), p)
    blend = (best * p + s) / np.maximum(best + n, 1e-9)

    def rmse(pred, mask):
        return math.sqrt(np.average((pred[mask] - t[mask]) ** 2, weights=w[mask]))

    errors = {}
    for checkpoint in (10, 20):
        mask = data['checkpoint'] == checkpoint
        errors[checkpoint] = (rmse(p, mask), rmse(observed, mask), rmse(blend, mask))
    return best, (int(near.min()), int(near.max())), errors, float(losses.min())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', type=int, default=None,
                        help='Season to test (2025 for 2025-26). Default: the latest full one held.')
    args = parser.parse_args()
    latest = fetch_all('SELECT MAX("gameId") / 1000000 AS s FROM player_game_stats')[0]['s']
    season = args.season or int(fetch_all(
        'SELECT MAX("gameId") / 1000000 AS s FROM player_game_stats WHERE "gameId" < :now',
        {'now': latest * 1_000_000})[0]['s'])

    games_by_team = team_games(season)
    played = goalie_games(season)
    prior = priors(season, played, games_by_team)
    data = samples(prior, played, games_by_team)
    tested = sum(1 for p, t in played.items() if p in prior and len(t) == 1)
    print(f"Testing {season}-{str(season + 1)[-2:]}: {tested} goalies on one team all season, "
          f"with last season's share as the prior.\n")
    print(f"  {'counting from':<16} {'k':>4} {'within 1%':>10}   RMSE after 10 / 20 games: "
          "prior / usage / blend")
    best_k = None
    for kind, label in (('team', "team's 1st game"), ('appearance', 'his 1st game'),
                        ('recent', f'last {RECENT_GAMES} games')):
        best, near, errors, loss = fit(data[kind])
        if kind == 'team':
            best_k = best
        print(f"  {label:<16} {best:>4} {near[0]:>4}-{near[1]:<5}   "
              + '   '.join(f"{a:.3f} / {b:.3f} / {c:.3f}" for a, b, c in errors.values())
              + f"   (loss {loss:.2f})")
    print(f"\nPRIOR_GAMES = {best_k}")


if __name__ == '__main__':
    main()
