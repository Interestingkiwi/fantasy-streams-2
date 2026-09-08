"""
What kind of manager you are playing this week.

Tier 3 of the opponent-transaction ladder in docs/OPTIMIZER.md §6. Every
projection of an opponent's week silently assumes something about their
add/drops: freezing their roster assumes none, which under-projects everyone,
and by an amount that depends entirely on how that particular manager plays.

The data to do better is already there. `transactions` holds every manager's
real add/drop history, and two numbers separate the styles well:

- **adds per week** - how active they are.
- **median hold duration** - how long an added player stays. Someone dropping
  adds within a few days is streaming for games played; someone holding them
  for weeks is chasing hot hands and roster upgrades.

Measured across 254 managers in the 24 imported leagues, both spread widely
enough to be worth splitting on. Adds per week runs 0.26 at the 10th
percentile to 3.26 at the 90th, with a median of 1.60. Median hold runs 4 days
to 87, with a population median of 11. The thresholds below come from those
percentiles rather than from taste.

**Censoring is real and has to be handled.** 18% of holds never end - the
player was still rostered when the data stops - so their true length is
unknown. They are recorded at their lower bound, which is safe for a *median*
specifically: an unfinished hold is a long one, so it sits above the median
either way and cannot move it. That stops being true once more than half a
manager's holds are unfinished, and `medianIsLowerBound` says so when it
happens.

Takes plain transaction rows rather than reading the database, like the rest
of the lineup modules:

    SELECT league_id, transaction_date, player_id, fantasy_team, move_type
    FROM transactions

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

from collections import defaultdict
from datetime import date

# Below this, a manager will not meaningfully change a matchup - fewer than
# one move a fortnight. The 10th percentile of the imported leagues is 0.26.
INACTIVE_ADDS_PER_WEEK = 0.5

# A streamer has to actually be streaming, not simply churning a thin roster.
STREAMER_MIN_ADDS_PER_WEEK = 1.0

# Median hold at or under this marks a games-played streamer. The population
# median is 11 days; the busiest managers all sit between 3 and 6.
STREAMER_HOLD_DAYS = 10

# What counts as a same-week pickup, for the share-of-short-holds diagnostic.
SHORT_HOLD_DAYS = 3

STYLES = ('inactive', 'streamer', 'targeted')

# Moves that end a hold. A trade ends one without being a drop, so it is
# counted separately - a manager who mostly trades is neither streaming nor
# working the wire.
ENDING_MOVES = ('drop', 'trade')


def profile_managers(transactions):
    """
    {(league_id, fantasy_team): profile} across every manager in `transactions`.

    Each profile carries the two headline numbers, the diagnostics behind
    them, a style, and `simulatedAddsPerWeek` - the cap a simulation may
    invent moves up to. That cap is the manager's own observed rate, never
    more: an opponent model that invents activity is worse than one assuming
    none, because it is wrong in a direction the user cannot see.
    """
    rows = _clean(transactions)
    if not rows:
        return {}

    span = _league_spans(rows)
    holds, censored = _holds(rows, span)

    counts = defaultdict(lambda: defaultdict(int))
    for row in rows:
        counts[(row['league_id'], row['fantasy_team'])][row['move_type']] += 1

    profiles = {}
    for key, moves in counts.items():
        league_id, team = key
        first, last = span[league_id]
        weeks = max(1.0, (last - first).days / 7.0)

        durations = holds.get(key, [])
        unfinished = censored.get(key, 0)
        censored_share = (unfinished / len(durations)) if durations else 0.0
        adds_per_week = moves.get('add', 0) / weeks

        median = _median(durations)
        profiles[key] = {
            'leagueId': league_id,
            'team': team,
            'adds': moves.get('add', 0),
            'drops': moves.get('drop', 0),
            'trades': moves.get('trade', 0),
            'activeWeeks': round(weeks, 1),
            'addsPerWeek': round(adds_per_week, 2),
            'medianHoldDays': median,
            'medianIsLowerBound': censored_share > 0.5,
            'shortHoldShare': round(
                sum(1 for d in durations if d <= SHORT_HOLD_DAYS) / len(durations), 2
            ) if durations else None,
            'censoredShare': round(censored_share, 2),
            'holds': len(durations),
            'style': classify(adds_per_week, median),
            'simulatedAddsPerWeek': round(adds_per_week, 2),
        }
    return profiles


def classify(adds_per_week, median_hold_days):
    """
    One of `STYLES`, from the two headline numbers.

    Activity is checked first: someone making fewer than one move a fortnight
    is not going to reshape a week whatever their holds look like. Among the
    active, hold length is what separates streaming for games played from
    hunting upgrades.
    """
    if adds_per_week < INACTIVE_ADDS_PER_WEEK:
        return 'inactive'
    if (median_hold_days is not None
            and median_hold_days <= STREAMER_HOLD_DAYS
            and adds_per_week >= STREAMER_MIN_ADDS_PER_WEEK):
        return 'streamer'
    return 'targeted'


def simulation_plan(profile):
    """
    How to model this manager's week, as a hint for the tiers above.

    A streamer's adds are aimed at games played, so simulate them as filling
    the empty lineup slots their schedule leaves - Tier 1. A targeted manager
    makes fewer, better moves, so simulate a small number of upgrades to their
    weakest starters instead; modelling those as slot-filling would both
    over-count the moves and aim them at the wrong thing. An inactive manager
    gets nothing.
    """
    style = profile.get('style')
    per_week = profile.get('simulatedAddsPerWeek') or 0.0

    if style == 'inactive':
        return {'style': style, 'moves': 0.0, 'aim': 'none'}
    if style == 'streamer':
        return {'style': style, 'moves': per_week, 'aim': 'fill-empty-slots'}
    return {'style': style, 'moves': per_week, 'aim': 'upgrade-weakest'}


def _clean(transactions):
    """Rows with a usable date, team and move type, as plain dicts."""
    rows = []
    for row in transactions or []:
        when = _as_date(row.get('transaction_date'))
        team = row.get('fantasy_team')
        move = row.get('move_type')
        if when is None or not team or not move:
            continue
        rows.append({
            'league_id': row.get('league_id'),
            'date': when,
            'player_id': row.get('player_id'),
            'fantasy_team': team,
            'move_type': move,
        })
    return rows


def _league_spans(rows):
    """{league_id: (first, last)} - the window a league was actually active."""
    span = {}
    for row in rows:
        league_id, when = row['league_id'], row['date']
        first, last = span.get(league_id, (when, when))
        span[league_id] = (min(first, when), max(last, when))
    return span


def _holds(rows, span):
    """
    How long each added player stayed, per manager.

    Adds are paired to the next ending move for the same player by the same
    manager, chronologically - not grouped, because managers re-add the same
    player often enough to matter (1,396 times in the imported data). A hold
    still open when the data ends is recorded at its lower bound and counted
    as censored.
    """
    timeline = defaultdict(list)
    for row in rows:
        timeline[(row['league_id'], row['fantasy_team'], row['player_id'])].append(
            (row['date'], row['move_type']))

    holds = defaultdict(list)
    censored = defaultdict(int)

    for (league_id, team, _player), moves in timeline.items():
        moves.sort(key=lambda item: item[0])
        opened = None
        for when, move in moves:
            if move == 'add':
                if opened is None:
                    opened = when
            elif move in ENDING_MOVES and opened is not None:
                holds[(league_id, team)].append((when - opened).days)
                opened = None

        if opened is not None:
            holds[(league_id, team)].append((span[league_id][1] - opened).days)
            censored[(league_id, team)] += 1

    return holds, censored


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
