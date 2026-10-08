"""
Measures how much a goalie's goals against and saves vary from start to start,
for the Matchup tab's GAA and save percentage odds (`goalie_ratios`
`GA_DISPERSION` / `SV_DISPERSION`), and checks the odds they give on whole
weeks.

Not part of any job - it prints constants to copy into `goalie_ratios.py` by
hand, as `derive_venue_prior.py` and `derive_combined_weights.py` do. Run it
between seasons, once the season just finished is in `player_game_stats`:

    python derive_ratio_dispersion.py
    python derive_ratio_dispersion.py --season 2025

**Per start**, against each goalie's own season rate (goalies with 5+ starts,
each scaled by n/(n-1) for the rate being his own), the convention
`matchup_weights` measured the counting categories by:

- goals against around the minutes he played, `(ga - r x minutes)^2` over
  `r x minutes` - Poisson is 1. The minutes are the real ones, so a pull and
  an overtime are inside the measurement, not beside it.
- saves around the shots he faced, `(sv - q x sa)^2` over `sa x q(1 - q)` -
  binomial is 1.

**Then on weeks**, which is what the odds are about: random sides of two
goalies drawn from the same Monday-Sunday week, every start of theirs that
week, each side's ratio predicted from its goalies' season rates over the
minutes and shots they really had. The predicted spread is right if the errors
in its units spread at 1.0, and the odds are right if sides given p won about
p of the time against another such side.

Author - Jason Druckenmiller
Created - 10/7/2026
Updated - 10/7/2026
"""

import argparse
import math
import random
from collections import defaultdict
from datetime import date, timedelta

from db import fetch_all

MIN_STARTS = 5            # for the per-start measurement
MIN_STARTS_WEEKLY = 10    # for a goalie to stand in a simulated side
SIDES = 20000


def starts(season):
    """Every start of the season with minutes: [{playerId, gameDate, ga, sa, sv, minutes}]."""
    rows = fetch_all(
        'SELECT "playerId", "gameDate", "goalsAgainst" AS ga, "shotsAgainst" AS sa,'
        ' saves AS sv, "timeOnIce" / 60.0 AS minutes FROM player_game_stats'
        ' WHERE "positionCode" IS NULL AND COALESCE("gamesStarted", 0) >= 1'
        ' AND "timeOnIce" > 0 AND "shotsAgainst" IS NOT NULL'
        ' AND "gameId" >= :first AND "gameId" < :next',
        {'first': season * 1_000_000, 'next': (season + 1) * 1_000_000})
    return [{k: (float(v) if k in ('ga', 'sa', 'sv', 'minutes') else v)
             for k, v in r.items()} for r in rows]


def season_rates(rows):
    """{playerId: (goals per minute, save rate, starts)}."""
    by = defaultdict(list)
    for row in rows:
        by[row['playerId']].append(row)
    return {pid: (sum(r['ga'] for r in s) / sum(r['minutes'] for r in s),
                  sum(r['sv'] for r in s) / sum(r['sa'] for r in s), len(s))
            for pid, s in by.items()}


def dispersion(rows, rates):
    """(goals against vs Poisson, saves vs binomial), per start."""
    goals = goals_base = saves = saves_base = 0.0
    for row in rows:
        rate, stop, n = rates[row['playerId']]
        if n < MIN_STARTS:
            continue
        own = n / (n - 1)
        goals += own * (row['ga'] - rate * row['minutes']) ** 2
        goals_base += rate * row['minutes']
        saves += own * (row['sv'] - stop * row['sa']) ** 2
        saves_base += row['sa'] * stop * (1 - stop)
    return goals / goals_base, saves / saves_base


def weekly(rows, rates, d_goals, d_saves, sides=SIDES, seed=1):
    """
    (z spread for GAA and SV%, calibration rows) from random two-goalie sides.

    Calibration rows are (category, predicted band, mean predicted, share won, n).
    """
    weeks = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if rates[row['playerId']][2] < MIN_STARTS_WEEKLY:
            continue
        day = row['gameDate'] if isinstance(row['gameDate'], date) else date.fromisoformat(str(row['gameDate']))
        weeks[day - timedelta(days=day.weekday())][row['playerId']].append(row)

    def side(goalies, week):
        g = m = a = s = expected_goals = expected_saves = save_spread = 0.0
        for pid in goalies:
            rate, stop, _n = rates[pid]
            for row in weeks[week][pid]:
                g += row['ga']; m += row['minutes']; a += row['sa']; s += row['sv']
                expected_goals += rate * row['minutes']
                expected_saves += stop * row['sa']
                save_spread += d_saves * row['sa'] * stop * (1 - stop)
        return {'GAA': (60 * g / m, 60 * expected_goals / m, (60 / m) ** 2 * d_goals * expected_goals),
                'SVpct': (s / a, expected_saves / a, save_spread / a ** 2)}

    rng = random.Random(seed)
    candidates = [w for w in weeks if len(weeks[w]) >= 4]
    z = {'GAA': [], 'SVpct': []}
    bands = defaultdict(list)
    for _ in range(sides):
        week = rng.choice(candidates)
        four = rng.sample(sorted(weeks[week]), 4)
        mine, theirs = side(four[:2], week), side(four[2:], week)
        for code, sign in (('GAA', -1), ('SVpct', 1)):
            actual, predicted, variance = mine[code]
            z[code].append((actual - predicted) / math.sqrt(variance))
            other = theirs[code]
            p = _cdf(sign * (predicted - other[1]) / math.sqrt(variance + other[2]))
            won = sign * (actual - other[0]) > 0
            bands[(code, min(9, int(p * 10)))].append((p, won))

    spread = {code: math.sqrt(sum(v * v for v in values) / len(values)) for code, values in z.items()}
    table = [(code, band, sum(p for p, _ in got) / len(got), sum(w for _, w in got) / len(got), len(got))
             for (code, band), got in sorted(bands.items())]
    return spread, table


def _cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--season', type=int, default=2025,
                        help='start year of a completed season (default 2025, i.e. 2025-26)')
    args = parser.parse_args()

    rows = starts(args.season)
    rates = season_rates(rows)
    used = sum(1 for r in rows if rates[r['playerId']][2] >= MIN_STARTS)
    d_goals, d_saves = dispersion(rows, rates)
    print(f"{args.season}-{str(args.season + 1)[-2:]}: {len(rows)} starts, {used} by goalies with {MIN_STARTS}+")
    print(f"  GA_DISPERSION = {d_goals:.2f}   # goals against around minutes played, Poisson = 1")
    print(f"  SV_DISPERSION = {d_saves:.2f}   # saves around shots faced, binomial = 1")

    spread, table = weekly(rows, rates, d_goals, d_saves)
    print(f"\nWeeks: {SIDES:,} random two-goalie sides. Errors in units of the predicted spread")
    print(f"  (1.0 is right): GAA {spread['GAA']:.2f}, SV% {spread['SVpct']:.2f}")
    print("  Predicted odds against another such side, and how often they won:")
    for code, band, predicted, won, n in table:
        print(f"    {code:5} {band / 10:.1f}-{(band + 1) / 10:.1f}: predicted {predicted:.3f}, won {won:.3f}  (n={n})")


if __name__ == '__main__':
    main()
