"""
A week of lineups for one roster, from settings typed in by hand.

Standalone mode's engine. Everything a synced league would supply -
categories, starting slots, who is on each roster, the score so far - arrives
as arguments instead, so this needs no Yahoo and no per-league tables. It is
pure: the route loads the rows and this does the maths, so the tests can hand
it a stub pool without a database.

The chain, one night at a time, is the one docs/OPTIMIZER.md builds up:

1. `daily_value` - per-game, per-category values against the **whole pool**,
   because the z weights take their σ from everyone, not from one roster.
2. `opponent_strength` - scale each category for tonight's opponent and venue.
3. `score` - collapse the adjusted line back to one number under the same
   weights, so an adjustment moves the value and not just the display.
4. `goalie_starts` - turn a goalie's per-start line into an expected one.
5. `lineup_utils.optimal_lineup` - seat the night exactly.

**With an opponent roster, the categories that are in doubt get the weight.**
A category league hands the nights to `matchup_weights.optimise_week`, which
re-weights toward categories whose projected final margin is close and seats
the week against those weights, while the opponent plays his best team on flat
weights. Without an opponent there is nothing to be in doubt against, and the
flat weights are the honest answer.

**A points league is never re-weighted.** Its score is one number, so there is
no category to chase at another's expense - every point is worth a point. It
still gets the opponent's projection and a win probability.

**Goalie start odds are balanced across the whole team, not the roster.** The
one-start-per-game invariant holds over a team's goalies, rostered or not -
owning only the backup does not make him the starter. So the balancing runs
over every projected goalie on each rostered goalie's team, across the full
season schedule, and the week is read out of that.

Author - Jason Druckenmiller
Created - 9/16/2026
Updated - 9/16/2026
"""

import math
from collections import defaultdict
from datetime import date

import daily_value as dv
import goalie_starts as gs
import matchup_weights as mw
import opponent_strength as ops
from lineup_utils import optimal_lineup, starting_slots

# The order slots are shown in: specific positions, then the generics that
# widen them, goalies last.
SLOT_ORDER = ['C', 'LW', 'RW', 'F', 'W', 'D', 'Util', 'G']

# Draft prep stores categories as projection column names; the engine speaks
# Yahoo codes. Built from daily_value's own tables so the two cannot drift.
COLUMN_TO_CATEGORY = {
    column: code
    for code, column in {**dv.COUNTING_COLUMNS, **dv.RATE_COLUMNS}.items()
}

# Values reaching the response are rounded, since the client only displays them.
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
              out=None, opponent=None, opponent_out=None, banked=None):
    """
    The best lineup for each date in `dates`, plus a per-player summary.

    `pool` is every `final_projections` row; `roster` is the playerIds on the
    team, and `out` any of them to leave unseated (injured, suspended).
    `opponent` and `opponent_out` are the same for the other side, and turn on
    the matchup. `banked` is the score so far as {category: {'mine': n,
    'theirs': n}}, for planning the rest of a week already under way.

    `schedule` is the full season as (game_date, home, away) - the whole
    season, not the week, because goalie start odds are balanced against
    season totals. `points` is {category: points} for a points league;
    omitted, the league is scored as categories.

    `team_stats` (every window) and `peripheral` (from
    `opponent_strength.peripheral_venue`) are optional: without them the week
    is planned on unadjusted projections, which is the right fallback before
    the first nightly scrape rather than an error.
    """
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
    polarity = dv.category_polarity(categories, pim_positive=pim_positive)
    valued = dv.value_players(pool, categories, weights=weights,
                              pim_positive=pim_positive)
    by_id = {str(row.get('playerId')): row for row in valued}

    mine, unknown = _resolve(roster, by_id)
    theirs, unknown_theirs = _resolve(opponent, by_id)
    has_opponent = bool(theirs)

    context = _week_context(dates, schedule, team_stats, peripheral,
                            _goalie_probabilities(valued, mine + theirs, schedule))
    dates = context['dates']

    out = {str(player_id) for player_id in (out or [])}
    opponent_out = {str(player_id) for player_id in (opponent_out or [])}
    nights = [{
        'date': night,
        'mine': _tonight(mine, night, out, weights, context),
        'theirs': _tonight(theirs, night, opponent_out, weights, context),
    } for night in dates]

    banked = _clean_banked(banked, categories)
    banked_margin = {c: v['mine'] - v['theirs'] for c, v in banked.items()}

    if has_opponent and not points:
        result = mw.optimise_week(nights, slots, categories, _normalised(weights),
                                  polarity=polarity, banked_margin=banked_margin)
        my_lineups, their_lineups = result['lineups'], result['opponentLineups']
        weights = result['weights']
    else:
        my_lineups = {n['date']: optimal_lineup(n['mine'], slots) for n in nights}
        their_lineups = {n['date']: optimal_lineup(n['theirs'], slots) for n in nights}

    my_side = _side(mine, nights, 'mine', my_lineups, slots, out, categories, context)
    response = {
        'categories': categories,
        'rateCategories': rates,
        'missingCategories': missing,
        'weights': {c: round(w, 6) for c, w in weights.items()
                    if c not in dv.RATE_COLUMNS},
        'adjusted': context['adjusted'],
        **my_side,
        'unknownPlayers': unknown,
        'opponent': None,
        'matchup': None,
    }

    if has_opponent:
        their_side = _side(theirs, nights, 'theirs', their_lineups, slots,
                           opponent_out, categories, context)
        response['opponent'] = {**their_side, 'unknownPlayers': unknown_theirs}
        response['matchup'] = matchup(
            categories, my_side['totals'], their_side['totals'], banked, polarity,
            points_per=(dv.points_weights(points, categories) if points else None))
    elif unknown_theirs:
        response['opponent'] = {'days': [], 'players': [], 'totals': {},
                                'unknownPlayers': unknown_theirs}

    return response


def matchup(categories, mine, theirs, banked=None, polarity=None, points_per=None):
    """
    Where the week is headed: per-category win odds, or a points win odds.

    `mine` and `theirs` are projected *remaining* totals; `banked` what each
    side already has. The final margin is banked plus remaining, but σ comes
    from the remaining totals alone - production that has happened carries no
    variance, the same rule `matchup_weights.category_weights` is built on.

    `contested` is how much a category is still in play, from 1 at a dead
    heat down toward 0 when the projection has settled it: `φ(z) / φ(0)`, the
    shape of the matchup weight without its units. The weights themselves are
    per unit of each stat - a shutout's is ~200x a shot's - so they say nothing
    to someone reading a table.

    Rate categories are listed but not scored: their final value depends on
    volume that has not been projected as a ratio.
    """
    banked = banked or {}
    polarity = polarity or dv.category_polarity(categories)

    rows, variance, points_margin = [], 0.0, 0.0
    for category in categories:
        if category in dv.RATE_COLUMNS:
            rows.append({'category': category, 'rate': True})
            continue

        rm, rt = mine.get(category, 0.0), theirs.get(category, 0.0)
        bm = banked.get(category, {}).get('mine', 0.0)
        bt = banked.get(category, {}).get('theirs', 0.0)
        dispersion = mw.dispersion_for(category)
        row = {
            'category': category,
            'mine': round(bm + rm, DISPLAY_PLACES),
            'theirs': round(bt + rt, DISPLAY_PLACES),
            'bankedMine': bm,
            'bankedTheirs': bt,
        }

        if points_per is not None:
            per = points_per.get(category, 0.0)
            row['points'] = per
            points_margin += per * ((bm + rm) - (bt + rt))
            variance += per * per * dispersion * (abs(rm) + abs(rt))
        else:
            sign = polarity.get(category, 1.0)
            margin = sign * ((bm + rm) - (bt + rt))
            sigma = mw.margin_sigma(rm, rt, dispersion)
            z = margin / sigma
            row['winProbability'] = round(_normal_cdf(z), DISPLAY_PLACES)
            row['contested'] = round(math.exp(-0.5 * z * z), DISPLAY_PLACES)
        rows.append(row)

    if points_per is not None:
        mine_points = sum(r['mine'] * r['points'] for r in rows if not r.get('rate'))
        theirs_points = sum(r['theirs'] * r['points'] for r in rows if not r.get('rate'))
        sigma = max(math.sqrt(variance), mw.MIN_SIGMA)
        return {
            'mode': 'points',
            'categories': rows,
            'minePoints': round(mine_points, 2),
            'theirsPoints': round(theirs_points, 2),
            'winProbability': round(_normal_cdf(points_margin / sigma), DISPLAY_PLACES),
        }

    scored = [r for r in rows if not r.get('rate')]
    return {
        'mode': 'categories',
        'categories': rows,
        'expectedWins': round(sum(r['winProbability'] for r in scored), 2),
        'scoredCategories': len(scored),
    }


def _week_context(dates, schedule, team_stats, peripheral, probabilities):
    """Who plays whom, where, and how strong they are, for the nights asked for."""
    dates = sorted({str(d) for d in dates})
    wanted = set(dates)
    week_rows = [(str(d), h, a) for d, h, a in schedule if str(d) in wanted]

    home_teams, games_on = defaultdict(set), defaultdict(int)
    for game_date, home, _away in week_rows:
        home_teams[game_date].add(home)
        games_on[game_date] += 1

    splits = ops.blended_z_scores(team_stats) if team_stats else {}
    return {
        'dates': dates,
        'facing': ops.opponents_on(week_rows),
        'homeTeams': home_teams,
        'gamesOn': games_on,
        'splits': splits,
        'venue': {**ops.venue_multipliers(team_stats or []), **(peripheral or {})},
        'probabilities': probabilities,
        'adjusted': bool(splits),
    }


def _tonight(players, night, out, weights, context):
    """The players with a game on `night` and not marked out, ready to seat."""
    tonight = []
    for player in players:
        team = gs.primary_team(player.get('teamAbbrevs'))
        opponent = context['facing'].get(night, {}).get(team)
        if opponent is None or str(player['playerId']) in out:
            continue
        probability = (context['probabilities'].get(str(player['playerId']), {})
                       .get(date.fromisoformat(night)))
        tonight.append(_for_tonight(
            player, opponent, team in context['homeTeams'][night], weights,
            context['splits'], context['venue'], probability))
    return tonight


def _side(players, nights, key, lineups, slots, out, categories, context):
    """One roster's week: nightly seats and bench, per-player summary, totals."""
    summary = {str(p['playerId']): {'games': 0, 'starts': 0.0, 'benchedGames': 0}
               for p in players}
    for player in players:
        team = gs.primary_team(player.get('teamAbbrevs'))
        summary[str(player['playerId'])]['games'] = sum(
            1 for night in nights if team in context['facing'].get(night['date'], {}))

    days = []
    for night in nights:
        lineup = lineups.get(night['date']) or {}
        seated = set()
        for seats in lineup.values():
            for player in seats:
                player_id = str(player['playerId'])
                seated.add(player_id)
                summary[player_id]['starts'] += player.get('startProbability', 1.0)

        # By id, not object identity: optimise_week re-values its players into
        # new dicts, so `lineup_utils.benched` would find nobody seated.
        bench = [p for p in night[key] if str(p['playerId']) not in seated]
        for player in bench:
            summary[str(player['playerId'])]['benchedGames'] += 1

        days.append({
            'date': night['date'],
            'nhlGames': context['gamesOn'].get(night['date'], 0),
            'slots': _seat_list(lineup, slots),
            'bench': [_public(p) for p in bench],
        })

    totals = mw.project_totals(lineups.values(), categories)
    return {
        'days': days,
        'players': [
            {**_public(p), 'out': str(p['playerId']) in out,
             **_rounded(summary[str(p['playerId'])])}
            for p in players
        ],
        'totals': _rounded({c: v for c, v in totals.items() if c not in dv.RATE_COLUMNS}),
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


def _normalised(weights):
    """
    Flat weights rescaled to a mean absolute value of one.

    Changes no lineup - the matcher only sees relative value - but
    `optimise_week` blends these with its matchup weights, which it normalises
    the same way. Unscaled, z weights run several times larger (1/σ of a
    per-game rate), so the blend would lean on the flat weights far more than
    its damping says.
    """
    magnitudes = [abs(w) for c, w in weights.items() if w and c not in dv.RATE_COLUMNS]
    if not magnitudes:
        return dict(weights)
    scale = sum(magnitudes) / len(magnitudes)
    return {c: w / scale for c, w in weights.items()}


def _clean_banked(banked, categories):
    """{category: {'mine': float, 'theirs': float}} for counting categories only."""
    clean = {}
    for category in categories:
        if category in dv.RATE_COLUMNS:
            continue
        entry = (banked or {}).get(category) or {}
        mine, theirs = _number(entry.get('mine')), _number(entry.get('theirs'))
        if mine or theirs:
            clean[category] = {'mine': mine, 'theirs': theirs}
    return clean


def _resolve(ids, by_id):
    """(valued rows, ids not in the pool) for a list of playerIds."""
    players, unknown = [], []
    for player_id in _unique(ids):
        row = by_id.get(str(player_id))
        (players if row else unknown).append(row or player_id)
    return players, unknown


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


def _normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number


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
