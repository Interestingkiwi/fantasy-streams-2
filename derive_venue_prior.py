"""
Re-measures `opponent_strength.VENUE_PRIOR` and `VENUE_PRIOR_GAMES`.

Not part of any job - it produces constants, not rows. Run it between seasons,
once the one just finished can join the average:

    python derive_venue_prior.py
    python derive_venue_prior.py --first 2015 --last 2026

It prints each season's home and road multipliers, their average (the prior),
and how far a season's own split can be trusted against it. Copy the numbers
into `opponent_strength.py` by hand, as `aging.py`'s are: a prior that moved
every time the job ran would not be a prior.

**Where the numbers come from.** Each season's home/road split is read from the
NHL stats API's team reports (`summary`, `realtime`, `penalties`), which accept
the same `homeRoad` filter `scrape_team_stats` uses, so this needs no table of
past seasons. A multiplier is that split's league mean over the overall mean,
exactly as `venue_multipliers` computes it.

**How much a season's split is worth.** A season's split is the true effect
plus sampling noise. The noise per game is measured off the latest season in
`player_game_stats` - each game's home total minus its road total, the delta
method on the ratio - and divided by each season's game count gives the spread
the seasons would show if the true effect never moved. Whatever spread is left
over is real drift (tau). The break-even between the prior and a season's own
split is `noise / tau^2` games.

The point estimate of tau is zero for most quantities - seasons differ by no
more than noise - which would say never to trust the current season at all.
Ten seasons cannot prove that, so this also prints the break-even under the
most drift they cannot rule out (the 90% upper bound on tau^2, from the
chi-square distribution of a sample variance). `VENUE_PRIOR_GAMES` is a
round number inside that range.

Two exclusions, both on purpose and both overridable:

- **2020-21** (`--keep-2021`), played without fans. Its PIM and blocks effects
  vanished, which is the one real shift in the data and not one to average in.
- **Before 2015-16** (`--first`), when hits were recorded differently: the
  home effect ran +4-5% and has sat between +1.2% and +2.4% since.

Author - Jason Druckenmiller
Created - 9/27/2026
Updated - 9/27/2026
"""

import argparse
import math
import statistics
from collections import defaultdict

import requests

from db import fetch_all

REPORT_URL = 'https://api.nhle.com/stats/rest/en/team/{report}'
TIMEOUT = 30
NO_CROWDS = 20202021

# quantity -> (report, field, how the league mean is taken). 'rate' averages a
# per-game field over teams, as `opponent_strength._league_mean` does; 'total'
# divides a season total by games first; 'wins' is wins over games played.
QUANTITIES = {
    'goalsForPerGame':     ('summary', 'goalsForPerGame', 'rate'),
    'goalsAgainstPerGame': ('summary', 'goalsAgainstPerGame', 'rate'),
    'shotsForPerGame':     ('summary', 'shotsForPerGame', 'rate'),
    'shotsAgainstPerGame': ('summary', 'shotsAgainstPerGame', 'rate'),
    'winRate':             ('summary', 'wins', 'wins'),
    'hits':                ('realtime', 'hits', 'total'),
    'blockedShots':        ('realtime', 'blockedShots', 'total'),
    'penaltyMinutes':      ('penalties', 'penaltyMinutes', 'total'),
}

# quantity -> the per-game column its noise is measured on. Against-quantities
# are the other side's for-quantities, so they share its noise.
NOISE_COLUMNS = {
    'goalsForPerGame': 'goals', 'goalsAgainstPerGame': 'goals',
    'shotsForPerGame': 'shots', 'shotsAgainstPerGame': 'shots',
    'winRate': 'wins', 'hits': 'hits', 'blockedShots': 'blockedShots',
    'penaltyMinutes': 'penaltyMinutes',
}

# One-sided 90%: the 10th percentile of a standard normal, for the chi-square
# quantile below.
Z_10TH = -1.2816


def fetch(report, season, side=None):
    """A team report for one regular season, optionally home or road only."""
    cayenne = f'seasonId={season} and gameTypeId=2'
    if side:
        cayenne += f' and homeRoad="{side}"'
    response = requests.get(
        REPORT_URL.format(report=report),
        params={'isAggregate': 'false', 'isGame': 'false', 'start': 0, 'limit': 50,
                'cayenneExp': cayenne},
        timeout=TIMEOUT)
    response.raise_for_status()
    return response.json().get('data', [])


def league_mean(rows, field, how):
    if how == 'wins':
        played = sum(row.get('gamesPlayed') or 0 for row in rows)
        return sum(row.get('wins') or 0 for row in rows) / played if played else None
    if how == 'total':
        values = [row[field] / row['gamesPlayed'] for row in rows
                  if row.get('gamesPlayed') and row.get(field) is not None]
    else:
        values = [row[field] for row in rows if row.get(field) is not None]
    return sum(values) / len(values) if values else None


def season_effects(season):
    """({quantity: {'home', 'road'}}, games) for one season."""
    reports = {}
    for report in {r for r, _, _ in QUANTITIES.values()}:
        reports[report] = {side: fetch(report, season, side) for side in (None, 'H', 'R')}

    effects = {}
    for quantity, (report, field, how) in QUANTITIES.items():
        overall = league_mean(reports[report][None], field, how)
        if not overall:
            continue
        effects[quantity] = {
            'home': league_mean(reports[report]['H'], field, how) / overall,
            'road': league_mean(reports[report]['R'], field, how) / overall,
        }
    games = sum(row.get('gamesPlayed') or 0 for row in reports['summary']['H'])
    return effects, games


def noise_per_game():
    """
    {quantity: variance of the home multiplier x games}, from the latest
    season in `player_game_stats`.

    For a small effect, home multiplier - 1 is (home - road) / (home + road)
    per game, so its variance over n games is var(home - road) over
    n x mean(home + road)^2. Taking the difference game by game keeps the
    within-game link - exactly one side wins - that treating the two sides as
    independent would miss.
    """
    rows = fetch_all(
        'SELECT "gameId", "homeRoad", goals, shots, wins, hits, "blockedShots",'
        ' "penaltyMinutes" FROM player_game_stats'
        ' WHERE left("gameId"::text, 4) = (SELECT left(max("gameId")::text, 4)'
        '                                  FROM player_game_stats)')
    games = defaultdict(lambda: {'H': defaultdict(float), 'R': defaultdict(float)})
    for row in rows:
        if row['homeRoad'] not in ('H', 'R'):
            continue
        side = games[row['gameId']][row['homeRoad']]
        for column in set(NOISE_COLUMNS.values()):
            side[column] += float(row[column] or 0)
    games = [g for g in games.values() if g['H'] and g['R']]
    if not games:
        raise RuntimeError('player_game_stats is empty - run scrape_game_results.py first.')

    noise = {}
    for quantity, column in NOISE_COLUMNS.items():
        differences = [g['H'][column] - g['R'][column] for g in games]
        mean_total = statistics.mean(g['H'][column] + g['R'][column] for g in games)
        noise[quantity] = statistics.pvariance(differences) / mean_total ** 2
    return noise, len(games)


def chi2_lower(k, z=Z_10TH):
    """The lower-tail chi-square quantile with k degrees of freedom (Wilson-Hilferty)."""
    return k * (1 - 2 / (9 * k) + z * math.sqrt(2 / (9 * k))) ** 3


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--first', type=int, default=2015,
                        help='first season, by its starting year (default 2015 = 2015-16)')
    parser.add_argument('--last', type=int, default=2025,
                        help='last season, by its starting year (default 2025 = 2025-26)')
    parser.add_argument('--keep-2021', action='store_true',
                        help='include 2020-21, played without fans')
    args = parser.parse_args()

    seasons = [int(f'{y}{y + 1}') for y in range(args.first, args.last + 1)
               if args.keep_2021 or int(f'{y}{y + 1}') != NO_CROWDS]

    measured = {}
    print('Home multiplier by season:')
    print('  season    games ' + ' '.join(f'{q[:8]:>8s}' for q in QUANTITIES))
    for season in seasons:
        effects, games = season_effects(season)
        measured[season] = (effects, games)
        print(f'  {season} {games:6d} '
              + ' '.join(f'{effects[q]["home"]:8.4f}' for q in QUANTITIES))

    noise, noise_games = noise_per_game()
    print(f'\nNoise per game from the latest {noise_games} games in player_game_stats.')

    print('\nBreak-even games (prior vs a season\'s own split):')
    print(f'  {"quantity":20s} {"spread/noise":>12s} {"tau":>7s} {"K":>8s} '
          f'{"tau 90%":>8s} {"K 90%":>8s}')
    for quantity in QUANTITIES:
        homes = [measured[s][0][quantity]['home'] for s in seasons]
        observed = statistics.variance(homes)
        sampling = statistics.mean(noise[quantity] / measured[s][1] for s in seasons)
        tau2 = observed - sampling
        upper = observed * (len(homes) - 1) / chi2_lower(len(homes) - 1) - sampling

        def fmt_k(t2):
            return f'{noise[quantity] / t2:8,.0f}' if t2 > 0 else '     inf'

        print(f'  {quantity:20s} {observed / sampling:12.2f} '
              f'{math.sqrt(max(tau2, 0)):7.4f} {fmt_k(tau2)} '
              f'{math.sqrt(max(upper, 0)):8.4f} {fmt_k(upper)}')

    print(f'\nVENUE_PRIOR, over {len(seasons)} seasons '
          f'({seasons[0]} to {seasons[-1]}):\n')
    print('VENUE_PRIOR = {')
    for quantity in QUANTITIES:
        home = statistics.mean(measured[s][0][quantity]['home'] for s in seasons)
        road = statistics.mean(measured[s][0][quantity]['road'] for s in seasons)
        key = f"'{quantity}':"
        print(f"    {key:22s} {{'home': {home:.4f}, 'road': {road:.4f}}},")
    print('}')


if __name__ == '__main__':
    main()
