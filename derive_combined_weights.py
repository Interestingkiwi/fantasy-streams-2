"""
Measures how many games of trust a projection is worth, stat by stat, for
League Home's *Combined* Stat Sourcing (`stat_sourcing.PRIOR_GAMES`).

Not part of any job - it prints constants to copy into `stat_sourcing.py` by
hand, as `derive_venue_prior.py` and `derive_aging_curve.py` do. Run it
between seasons, once the season just finished is in `player_game_stats`:

    python derive_combined_weights.py
    python derive_combined_weights.py --season 2025

**The blend.** Combined values a player at

    (k x projected rate + n x season rate) / (k + n)

after n games: the projection counts as k games' worth of evidence, and the
season takes over as its games pile up. k is the stat's own - shots settle
within weeks, goals and plus/minus take most of a season - and is measured
here rather than chosen: for each stat, the k whose blend after a player's
first 5, 10, 20, 30 and 40 games best predicts his rate over the rest of that
season (squared error, weighted by the games remaining).

**The projection it is measured against** is rebuilt for the season being
tested from the three before it, the pipeline's way (`calculate_*_projections`):
per-game rates weighted 60/30/10, seasons under 10 games (5 starts) left out,
40 games (20 starts) in all required. Two things the real projections add are
missing - the age curve (no birthdates are stored) and the goalie regression
toward the league - so this prior is somewhat worse than the real one, and the
k measured against it somewhat low: Combined leans on the season a little
sooner than it strictly should. The print says how much worse the prior alone
does than the blend, so the gap is visible.

Author - Jason Druckenmiller
Created - 10/3/2026
Updated - 10/3/2026
"""

import argparse
import math
from collections import defaultdict

import numpy as np

from db import fetch_all

# code: (player_game_stats column, historic table column)
SKATER = {
    'G': ('goals', 'goals'), 'A': ('assists', 'assists'), 'PPG': ('ppGoals', 'ppGoals'),
    'PPP': ('ppPoints', 'ppPoints'), 'SHP': ('shPoints', 'shPoints'), 'SOG': ('shots', 'shots'),
    'HIT': ('hits', 'hits'), 'BLK': ('blockedShots', 'blockedShots'),
    'PIM': ('penaltyMinutes', 'penaltyMinutes'), '+/-': ('plusMinus', 'plusMinus'),
    'FW': ('faceoffWins', 'totalFaceoffWins'), 'FL': ('faceoffLosses', 'totalFaceoffLosses'),
}
GOALIE = {
    'W': ('wins', 'wins'), 'L': ('losses', 'losses'), 'OTL': ('otLosses', 'otLosses'),
    'SHO': ('shutouts', 'shutouts'), 'GA': ('goalsAgainst', 'goalsAgainst'),
    'SA': ('shotsAgainst', 'shotsAgainst'), 'TOI': ('timeOnIce', 'timeOnIce'),
}
WEIGHTS = (6, 3, 1)                  # the pipeline's 60/30/10, newest first
CHECKPOINTS = (5, 10, 20, 30, 40)
MIN_REMAINING = 15                   # games left after a checkpoint to judge it on
K_GRID = np.arange(0, 1001)
SKATER_FLOOR = {'season': 10, 'total': 40, 'tested': 30}
GOALIE_FLOOR = {'season': 5, 'total': 20, 'tested': 20}


def season_id(season):
    return int(f"{season}{season + 1}")


def priors(table, columns, games_column, seasons, floor):
    """{playerId: {code: per-game (per-start) rate}} from the three seasons before."""
    rows = fetch_all(
        f'SELECT "playerId", "seasonId", "{games_column}" AS games, '
        + ', '.join(f'"{source}"' for _col, source in columns.values())
        + f' FROM {table} WHERE "seasonId" = ANY(:seasons)',
        {'seasons': [str(season_id(s)) for s in seasons]})
    by_player = defaultdict(dict)
    for row in rows:
        by_player[row['playerId']][int(str(row['seasonId'])[:4])] = row
    out = {}
    for player, history in by_player.items():
        used = [(weight, history[s]) for weight, s in zip(WEIGHTS, seasons)
                if s in history and (history[s]['games'] or 0) >= floor['season']]
        if sum(row['games'] for _w, row in used) < floor['total']:
            continue
        rate = {}
        for code, (_col, source) in columns.items():
            values = [(w, row[source] / row['games']) for w, row in used if row[source] is not None]
            if values:
                rate[code] = sum(w * v for w, v in values) / sum(w for w, _v in values)
        out[player] = rate
    return out


def games(season, columns, goalie):
    """{playerId: [per-game {code: value}], in date order} for one season."""
    kind = ('"positionCode" IS NULL AND COALESCE("gamesStarted", 0) >= 1' if goalie
            else '"positionCode" IS NOT NULL')
    rows = fetch_all(
        'SELECT "playerId", ' + ', '.join(f'"{col}"' for col, _s in columns.values())
        + f' FROM player_game_stats WHERE {kind} AND "gameId" >= :first AND "gameId" < :next'
        ' ORDER BY "playerId", "gameDate", "gameId"',
        {'first': season * 1_000_000, 'next': (season + 1) * 1_000_000})
    out = defaultdict(list)
    for row in rows:
        out[row['playerId']].append(row)
    return out


def samples(prior, played, code, column, floor):
    """Arrays (prior, observed, target, n, weight) over every player and checkpoint."""
    cols = defaultdict(list)
    for player, rows in played.items():
        if player not in prior or code not in prior[player] or len(rows) < floor['tested']:
            continue
        values = [row[column] for row in rows]
        if any(v is None for v in values):
            continue                                 # a column not collected that season
        values = np.array(values, dtype=float)
        for n in CHECKPOINTS:
            remaining = len(values) - n
            if remaining < MIN_REMAINING:
                continue
            cols['prior'].append(prior[player][code])
            cols['obs'].append(values[:n].mean())
            cols['target'].append(values[n:].mean())
            cols['n'].append(n)
            cols['w'].append(remaining)
    return {key: np.array(value) for key, value in cols.items()}


def fit(data):
    """(best k, [k range within 1% of the best loss], RMSE dict) for one stat."""
    p, o, t, n, w = data['prior'], data['obs'], data['target'], data['n'], data['w']
    losses = np.array([np.sum(w * (((k * p + n * o) / (k + n)) - t) ** 2) for k in K_GRID])
    best = int(K_GRID[np.argmin(losses)])
    near = K_GRID[losses <= losses.min() * 1.01]

    def rmse(pred, mask):
        return math.sqrt(np.average((pred[mask] - t[mask]) ** 2, weights=w[mask]))

    ten = n == 10
    errors = {'projection': rmse(p, ten), 'season': rmse(o, ten),
              'blend': rmse((best * p + n * o) / (best + n), ten)}
    return best, (int(near.min()), int(near.max())), errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', type=int, default=None,
                        help='Season to test (2025 for 2025-26). Default: the latest full one held.')
    args = parser.parse_args()
    season = args.season or int(fetch_all(
        'SELECT MAX("gameId") / 1000000 AS s FROM player_game_stats WHERE "gameId" < :now',
        {'now': (fetch_all('SELECT MAX("gameId") / 1000000 AS s FROM player_game_stats')[0]['s']) * 1_000_000})[0]['s'])
    before = [season - 1, season - 2, season - 3]
    print(f"Testing {season}-{str(season + 1)[-2:]} against a prior from "
          f"{', '.join(f'{s}-{str(s + 1)[-2:]}' for s in before)}.\n")

    found = {}
    for label, columns, table, games_column, floor, goalie in (
            ('Skaters (per game)', SKATER, 'historic_skaters_baseline', 'gamesPlayed', SKATER_FLOOR, False),
            ('Goalies (per start)', GOALIE, 'historic_goalies_baseline', 'gamesStarted', GOALIE_FLOOR, True)):
        prior = priors(table, columns, games_column, before, floor)
        played = games(season, columns, goalie)
        print(f"{label}: {len(prior)} with a prior, "
              f"{sum(1 for p, r in played.items() if p in prior and len(r) >= floor['tested'])} tested")
        print(f"  {'stat':<5} {'k':>5} {'within 1%':>11}   RMSE after 10 games: projection / season / blend")
        for code, (column, _source) in columns.items():
            data = samples(prior, played, code, column, floor)
            if not len(data.get('n', [])):
                print(f"  {code:<5} {'-':>5}   (no data - column not collected that season)")
                continue
            best, near, errors = fit(data)
            found[code] = best
            print(f"  {code:<5} {best:>5} {near[0]:>5}-{near[1]:<5}   "
                  f"{errors['projection']:.4f} / {errors['season']:.4f} / {errors['blend']:.4f}")
        print()

    print("PRIOR_GAMES = {")
    for code, k in found.items():
        print(f"    {code!r}: {k},")
    print("}")


if __name__ == '__main__':
    main()
