"""
A week of lineups for one roster, from settings typed in by hand.

The first thing standalone mode does. Everything a synced league would supply
- categories, starting slots, who is on the roster - arrives as arguments
instead, so this needs no Yahoo and no per-league tables. It is pure: the
route loads the rows and this does the maths, so the tests can hand it a
stub pool without a database.

The chain, one night at a time, is the one docs/OPTIMIZER.md builds up:

1. `daily_value` - per-game, per-category values against the **whole pool**,
   because the z weights take their σ from everyone, not from one roster.
2. `opponent_strength` - scale each category for tonight's opponent and venue.
3. `score` - collapse the adjusted line back to one number under the same
   weights, so an adjustment moves the value and not just the display.
4. `goalie_starts` - turn a goalie's per-start line into an expected one.
5. `lineup_utils.optimal_lineup` - seat the night exactly.

**No opponent roster, so no matchup weighting.** `matchup_weights` needs the
other side's projection to know which categories are in doubt; without it the
flat weights are the honest answer. The seam is left where §5 will plug in: a
`weights` argument that replaces the z-sum.

**Goalie start odds are balanced across the whole team, not the roster.** The
one-start-per-game invariant holds over a team's goalies, rostered or not -
owning only the backup does not make him the starter. So the balancing runs
over every projected goalie on each rostered goalie's team, across the full
season schedule, and the week is read out of that.

Author - Jason Druckenmiller
Created - 9/16/2026
Updated - 9/16/2026
"""

from collections import defaultdict
from datetime import date

import daily_value as dv
import goalie_starts as gs
import opponent_strength as ops
from lineup_utils import benched, optimal_lineup, starting_slots
from matchup_weights import project_totals

# The order slots are shown in: specific positions, then the generics that
# widen them, goalies last.
SLOT_ORDER = ['C', 'LW', 'RW', 'F', 'W', 'D', 'Util', 'G']

# Draft prep stores categories as projection column names; the engine speaks
# Yahoo codes. Built from daily_value's own tables so the two cannot drift.
COLUMN_TO_CATEGORY = {
    column: code
    for code, column in {**dv.COUNTING_COLUMNS, **dv.RATE_COLUMNS}.items()
}

# Columns whose values reach the response per player. Rounded, since the
# client only displays them.
DISPLAY_PLACES = 3


def categories_from_columns(columns):
    """
    (categories, unmapped) from draft prep's `fs_selectedStats` column names.

    Accepts Yahoo codes as well, so a caller that already speaks them is not
    forced through the translation. Order is kept and duplicates dropped.
    """
    categories, unmapped = [], []
    for name in columns or []:
        code = COLUMN_TO_CATEGORY.get(name)
        if code is None and name in COLUMN_TO_CATEGORY.values():
            code = name
        if code is None:
            unmapped.append(name)
        elif code not in categories:
            categories.append(code)
    return categories, unmapped


def plan_week(pool, roster, categories, roster_slots, dates, schedule,
              team_stats=None, peripheral=None, points=None, pim_positive=False,
              out=None):
    """
    The best lineup for each date in `dates`, plus a per-player summary.

    `pool` is every `final_projections` row; `roster` is the playerIds on the
    team, and `out` any of them to leave unseated (injured, suspended).
    `schedule` is the full season as (game_date, home, away) - the whole
    season, not the week, because goalie start odds are balanced against
    season totals. `points` is {category: points} for a points league;
    omitted, the league is scored as categories.

    `team_stats` (every window) and `peripheral` (from
    `opponent_strength.peripheral_venue`) are optional: without them the week
    is planned on unadjusted projections, which is the right fallback before
    the first nightly scrape rather than an error.
    """
    out = {str(player_id) for player_id in (out or [])}
    slots = starting_slots(roster_slots)

    if points:
        weights = dv.points_weights(points, categories)
        categories = [c for c in categories if weights.get(c)]
    else:
        scored, rates, _missing = dv.supported(categories)
        active = scored + rates
        weights = dv.default_weights(
            dv.category_scales(pool, active),
            dv.category_polarity(active, pim_positive=pim_positive))

    _scored, rates, missing = dv.supported(categories)
    valued = dv.value_players(pool, categories, weights=weights,
                              pim_positive=pim_positive)
    by_id = {str(row.get('playerId')): row for row in valued}

    players, unknown = [], []
    for player_id in _unique(roster):
        row = by_id.get(str(player_id))
        (players if row else unknown).append(row or player_id)

    probabilities = _goalie_probabilities(valued, players, schedule)

    dates = sorted({str(d) for d in dates})
    wanted = set(dates)
    week_rows = [row for row in schedule if str(row[0]) in wanted]
    facing = ops.opponents_on((str(d), h, a) for d, h, a in week_rows)
    home_teams = defaultdict(set)
    for game_date, home, _away in week_rows:
        home_teams[str(game_date)].add(home)
    games_on = defaultdict(int)
    for game_date, _home, _away in week_rows:
        games_on[str(game_date)] += 1

    splits = ops.blended_z_scores(team_stats) if team_stats else {}
    venue = {**ops.venue_multipliers(team_stats or []), **(peripheral or {})}

    summary = {
        str(p['playerId']): {'games': 0, 'starts': 0.0, 'benchedGames': 0}
        for p in players
    }
    days, lineups = [], []

    for night in dates:
        tonight = []
        for player in players:
            player_id = str(player['playerId'])
            team = gs.primary_team(player.get('teamAbbrevs'))
            opponent = facing.get(night, {}).get(team)
            if opponent is None:
                continue

            summary[player_id]['games'] += 1
            if player_id in out:
                continue

            is_home = team in home_teams[night]
            tonight.append(_for_tonight(
                player, opponent, is_home, weights, splits, venue,
                probabilities.get(player_id, {}).get(date.fromisoformat(night))))

        lineup = optimal_lineup(tonight, slots)
        lineups.append(lineup)
        for seats in lineup.values():
            for player in seats:
                summary[str(player['playerId'])]['starts'] += (
                    player.get('startProbability', 1.0))
        bench = benched(tonight, lineup)
        for player in bench:
            summary[str(player['playerId'])]['benchedGames'] += 1

        days.append({
            'date': night,
            'nhlGames': games_on.get(night, 0),
            'slots': _seat_list(lineup, slots),
            'bench': [_public(p) for p in bench],
        })

    return {
        'categories': categories,
        'rateCategories': rates,
        'missingCategories': missing,
        'weights': {c: round(w, 6) for c, w in weights.items()},
        'adjusted': bool(splits),
        'days': days,
        'players': [
            {**_public(p), 'out': str(p['playerId']) in out,
             **_rounded(summary[str(p['playerId'])])}
            for p in players
        ],
        'unknownPlayers': unknown,
        'totals': _rounded({c: v for c, v in project_totals(lineups, categories).items()
                            if c not in dv.RATE_COLUMNS}),
    }


def _for_tonight(player, opponent, is_home, weights, splits, venue, probability):
    """One player's line for one night: adjusted, re-scored, start-weighted."""
    per_game = player.get('perGame') or {}
    if splits:
        per_game = ops.adjust(per_game, opponent, ops.opponent_z_for(is_home, splits),
                              is_home=is_home, venue=venue)

    row = dict(player)
    row['perGame'] = per_game
    row['value'] = dv.score(per_game, weights)
    row['opponent'] = opponent
    row['home'] = is_home
    if not row.get('eligiblePositions'):
        row['eligiblePositions'] = row.get('positionCode')

    if _is_goalie(player):
        row = gs.expected_value(row, probability or 0.0)
    return row


def _goalie_probabilities(valued, players, schedule):
    """
    {playerId: {date: probability}} for the rostered goalies.

    Balanced over every projected goalie on those goalies' teams - see the
    module docstring for why the roster alone is the wrong population.
    """
    teams = {gs.primary_team(p.get('teamAbbrevs')) for p in players if _is_goalie(p)}
    if not teams:
        return {}

    goalies = [row for row in valued
               if _is_goalie(row) and gs.primary_team(row.get('teamAbbrevs')) in teams]
    rows = gs.start_probabilities_by_team(goalies, schedule)
    return {str(goalies[i]['playerId']): row for i, row in rows.items()}


def _seat_list(lineup, slots):
    """Every seat in display order, with None for one nobody could fill."""
    ordered = [s for s in SLOT_ORDER if s in slots]
    ordered += sorted(s for s in slots if s not in SLOT_ORDER)

    seats = []
    for slot in ordered:
        filled = lineup.get(slot, [])
        for index in range(slots[slot]):
            player = filled[index] if index < len(filled) else None
            seats.append({'slot': slot, 'player': _public(player) if player else None})
    return seats


def _public(player):
    """The fields the page shows, and nothing else of a 60-column row."""
    fields = {
        'playerId': player.get('playerId'),
        'fullName': player.get('fullName'),
        'teamAbbrevs': player.get('teamAbbrevs'),
        'positionCode': player.get('positionCode'),
        'eligiblePositions': player.get('eligiblePositions') or player.get('positionCode'),
    }
    for key in ('opponent', 'home'):
        if key in player:
            fields[key] = player[key]
    if 'value' in player:
        fields['value'] = round(float(player['value'] or 0.0), DISPLAY_PLACES)
    if 'startProbability' in player:
        fields['startProbability'] = round(player['startProbability'], DISPLAY_PLACES)
    return fields


def _rounded(values):
    return {k: round(v, DISPLAY_PLACES) if isinstance(v, float) else v
            for k, v in values.items()}


def _is_goalie(player):
    return 'G' in str(player.get('positionCode') or '').split(',')


def _unique(ids):
    seen, ordered = set(), []
    for player_id in ids or []:
        key = str(player_id)
        if key not in seen:
            seen.add(key)
            ordered.append(player_id)
    return ordered
