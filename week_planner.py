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

**`Week` holds everything that does not depend on who is on a roster** - the
valued pool, the weights, who plays whom - so the free-agent search can score
dozens of roster variations without redoing it. `plan_week` and
`free_agents` are the two entry points built on it.

**With an opponent roster, the categories that are in doubt get the weight.**
A category league hands the nights to `matchup_weights.optimise_week`, which
re-weights toward categories whose projected final margin is close and seats
the week against those weights, while the opponent plays his best team on flat
weights. Without an opponent there is nothing to be in doubt against, and the
flat weights are the honest answer.

**A points league is never re-weighted.** Its score is one number, so there is
no category to chase at another's expense - every point is worth a point. It
still gets the opponent's projection and a win probability.

**GAA and save percentage are projected from their parts** (`goalie_ratios`):
goals against, shots against and saves ride on every goalie's line, unscored,
whether or not the league scores them, and each side's ratios are built from
its goalie seats on top of its goaltending so far. They get odds and count in
expected categories won, but do not set lineups yet - the optimiser's weights
are per unit of a counting stat, and a ratio has none.

**The goalie minimum can forfeit all of that** (`goalie_minimum`): a side short
of the league's minimum appearances loses every goalie category, or its goalie
points in a points league. Each side's chance of reaching it - appearances so
far plus its goalie seats still to come - is folded into the goalie
categories' odds and into the expected points.

**Goalie start odds are balanced across the whole team, not the roster.** The
one-start-per-game invariant holds over a team's goalies, rostered or not -
owning only the backup does not make him the starter. So the balancing runs
over every projected goalie on each rostered goalie's team, across the full
season schedule, and the week is read out of that.

**Moves** are planned add/drops: `{add, drop, date}`. The added player counts
from `date`, and the dropped one plays up to the night before - which is how an
add made before tonight's games lock behaves.

**Manual lineups** replace the optimiser's choice for the nights a user set by
hand: `{date: [playerId or None per seat]}`, seats in `seat_order`. A manual
night is fixed - the matchup iteration projects it but never re-seats it - so
its production feeds the category weights the other nights are chosen under.
A seat that can no longer be honoured (the player was dropped, is out, has no
game, or is not eligible for it) is left empty and reported, never silently
refilled: the user set that night, and a quiet substitution would be the
optimiser overruling them.

Author - Jason Druckenmiller
Created - 9/16/2026
Updated - 10/7/2026
"""

import bisect
import math
from collections import defaultdict
from datetime import date

import daily_value as dv
import goalie_minimum as gm
import goalie_ratios as gr
import goalie_starts as gs
import matchup_weights as mw
import opponent_strength as ops
from lineup_utils import optimal_lineup, slots_for, starting_slots

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

# The free-agent search, in three narrowing passes (see `free_agents`).
SCREEN_SHORTLIST = 40      # by unadjusted weighted value over the nights left
DROP_CANDIDATES = 3        # the roster's weakest players over the rest of the season
EXACT_CANDIDATES = 10      # re-planned in full, on every date

# Projected games (starts, for a goalie) to count in the pool a heat colour is
# measured against.
HEAT_MIN_GAMES = 20


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


class Week:
    """
    The part of a plan that does not depend on who is on a roster.

    `pool` is every `final_projections` row. `schedule` is the full season as
    (game_date, home, away) - the whole season, not the week, because goalie
    start odds are balanced against season totals. `points` is {category:
    points} for a points league; omitted, the league is scored as categories.

    `team_stats` (every window) and `peripheral` (from
    `opponent_strength.peripheral_venue`) are optional. Without them there is
    no opponent adjustment - the right fallback before the first nightly
    scrape rather than an error - but home ice still applies, at its long-run
    size (`opponent_strength.VENUE_PRIOR`), since that is known before a game
    is played.

    `values` are the rows players are valued on, when that is not the
    projections themselves - Season to date's (`stat_sourcing.season_rows`).
    The category scales always come from `pool`, the projections: a few weeks'
    rates spread wider than the true ones and would re-weight the league.
    """

    def __init__(self, pool, categories, roster_slots, dates, schedule,
                 team_stats=None, peripheral=None, points=None, pim_positive=False,
                 values=None):
        self.slots = starting_slots(roster_slots)
        self.schedule = schedule
        self.projections = pool

        if points:
            self.flat = dv.points_weights(points, categories)
            categories = [c for c in categories if self.flat.get(c)]
            self.points = dict(self.flat)
        else:
            scored, rates, _missing = dv.supported(categories)
            active = scored + rates
            self.flat = dv.default_weights(
                dv.category_scales(pool, active),
                dv.category_polarity(active, pim_positive=pim_positive))
            self.points = None

        self.categories = categories
        _scored, self.rates, self.missing = dv.supported(categories)
        self.polarity = dv.category_polarity(categories, pim_positive=pim_positive)
        # GAA and save percentage are built from goals and shots against and
        # saves, which a league scoring only the ratios would never project.
        # Carried on each line unscored: no weight, no column on the page.
        carried = ([c for c in gr.PARTS if c not in categories]
                   if self.rates and not self.points else [])
        self.valued = dv.value_players(pool if values is None else values,
                                       categories + carried,
                                       weights=self.flat, pim_positive=pim_positive)
        self.by_id = {str(row.get('playerId')): row for row in self.valued}

        self.dates = sorted({str(d) for d in dates})
        wanted = set(self.dates)
        week_rows = [(str(d), h, a) for d, h, a in schedule if str(d) in wanted]
        self.facing = ops.opponents_on(week_rows)
        self.home_teams, self.games_on = defaultdict(set), defaultdict(int)
        for game_date, home, _away in week_rows:
            self.home_teams[game_date].add(home)
            self.games_on[game_date] += 1

        self.splits = ops.blended_z_scores(team_stats) if team_stats else {}
        self.venue = {**ops.venue_multipliers(team_stats or []),
                      **(ops.peripheral_venue([]) if peripheral is None else peripheral)}
        self._probabilities = {}
        self._rows = {}
        self._heat_pools = None

    @property
    def adjusted(self):
        return bool(self.splits)

    def heat(self, player):
        """
        {category: 0..1} - where his per-game line sits among players of his
        kind (skaters or goalies), 1 the best. Polarity is respected, so a low
        goals-against average is hot. The pool is regulars only
        (`HEAT_MIN_GAMES`), or a 3-game call-up's rates would set the scale.
        """
        if self._heat_pools is None:
            pools = defaultdict(list)
            for row in self.valued:
                goalie = _is_goalie(row)
                games = _number(row.get('proj_gamesStarted' if goalie else 'projectedGames'))
                if games < HEAT_MIN_GAMES:
                    continue
                for category, value in (row.get('perGame') or {}).items():
                    if value is not None:
                        pools[(goalie, category)].append(float(value))
            self._heat_pools = {key: sorted(values) for key, values in pools.items()}

        goalie = _is_goalie(player)
        result = {}
        for category, value in (player.get('perGame') or {}).items():
            pool = self._heat_pools.get((goalie, category))
            if not pool or value is None or category not in self.categories:
                continue
            share = bisect.bisect_left(pool, float(value)) / len(pool)
            result[category] = round(share if self.polarity.get(category, 1.0) >= 0
                                     else 1.0 - share, 3)
        return result

    def resolve(self, ids):
        """(valued rows, ids not in the pool), duplicates dropped."""
        players, unknown = [], []
        for player_id in _unique(ids):
            row = self.by_id.get(str(player_id))
            (players if row else unknown).append(row or player_id)
        return players, unknown

    def games_for(self, player, nights=None):
        team = gs.primary_team(player.get('teamAbbrevs'))
        return sum(1 for night in (nights or self.dates) if team in self.facing.get(night, {}))

    def row(self, player, night):
        """
        One player's line for one night - adjusted, re-scored, start-weighted -
        or None if his team is idle. Cached, because the free-agent search asks
        for the same player on the same night many times over.
        """
        key = (str(player['playerId']), night)
        if key in self._rows:
            return self._rows[key]

        team = gs.primary_team(player.get('teamAbbrevs'))
        opponent = self.facing.get(night, {}).get(team)
        if opponent is None:
            self._rows[key] = None
            return None

        is_home = team in self.home_teams[night]
        per_game = player.get('perGame') or {}
        if self.splits:
            per_game = ops.adjust(per_game, opponent,
                                  ops.opponent_z_for(is_home, self.splits),
                                  is_home=is_home, venue=self.venue)

        row = dict(player)
        row['perGame'] = per_game
        row['value'] = dv.score(per_game, self.flat)
        row['opponent'] = opponent
        row['home'] = is_home
        if not row.get('eligiblePositions'):
            row['eligiblePositions'] = row.get('positionCode')
        if _is_goalie(player):
            probability = (self.probabilities([player]).get(str(player['playerId']), {})
                           .get(date.fromisoformat(night)))
            row = gs.expected_value(row, probability or 0.0)

        self._rows[key] = row
        return row

    def per_start(self, player, night):
        """
        A goalie's line for one night **per start**, not per scheduled game:
        the adjusted projection before the start probability is applied, with
        that probability alongside. None if his team is idle.

        `row` returns the expected line, which is what a lineup is worth.
        Goalie planning needs the other one - what happens *if* he starts.
        """
        team = gs.primary_team(player.get('teamAbbrevs'))
        opponent = self.facing.get(night, {}).get(team)
        if opponent is None:
            return None

        is_home = team in self.home_teams[night]
        per_game = player.get('perGame') or {}
        if self.splits:
            per_game = ops.adjust(per_game, opponent,
                                  ops.opponent_z_for(is_home, self.splits),
                                  is_home=is_home, venue=self.venue)
        probability = (self.probabilities([player]).get(str(player['playerId']), {})
                       .get(date.fromisoformat(night))) if _is_goalie(player) else 1.0
        return {'date': night, 'opponent': opponent, 'home': is_home,
                'startProbability': probability or 0.0, 'perStart': per_game}

    def probabilities(self, players):
        """
        {playerId: {date: probability}} for the goalies among `players`.

        Balanced over every projected goalie on each of their teams (see the
        module docstring), and cached per team: all 32 at once costs ~110ms,
        which a plan for two rosters has no need to pay.
        """
        teams = {gs.primary_team(p.get('teamAbbrevs')) for p in players if _is_goalie(p)}
        missing = teams - set(self._probabilities)
        if missing:
            goalies = [row for row in self.valued
                       if _is_goalie(row) and gs.primary_team(row.get('teamAbbrevs')) in missing]
            by_team = gs.start_probabilities_by_team(goalies, self.schedule)
            for team in missing:
                self._probabilities[team] = {}
            for index, row in by_team.items():
                team = gs.primary_team(goalies[index].get('teamAbbrevs'))
                self._probabilities[team][str(goalies[index]['playerId'])] = row

        merged = {}
        for team in teams:
            merged.update(self._probabilities.get(team, {}))
        return merged

    def nights(self, ids, out=(), moves=()):
        """
        [{date, ids, players}] for a roster across the week: who is on it that
        night once moves are applied, and the ones with a game who are not out.
        """
        out = {str(i) for i in out}
        result = []
        for night in self.dates:
            on_roster = roster_on(ids, moves, night)
            players = []
            for player_id in on_roster:
                player = self.by_id.get(player_id)
                if player is None or player_id in out:
                    continue
                row = self.row(player, night)
                if row is not None:
                    players.append(row)
            result.append({'date': night, 'ids': on_roster, 'players': players})
        return result


def roster_on(ids, moves, night):
    """The playerIds on a roster on `night`, after every move dated on or before it."""
    roster = [str(i) for i in _unique(ids)]
    for move in sorted(moves or (), key=lambda m: str(m.get('date'))):
        if str(move.get('date')) > night:
            continue
        drop, add = move.get('drop'), move.get('add')
        if drop is not None and str(drop) in roster:
            roster.remove(str(drop))
        if add is not None and str(add) not in roster:
            roster.append(str(add))
    return roster


def plan_week(pool, roster, categories, roster_slots, dates, schedule,
              team_stats=None, peripheral=None, points=None, pim_positive=False,
              out=None, opponent=None, opponent_out=None, banked=None, moves=None,
              week=None, lineups=None, next_dates=None, goalie_stats=None,
              goalie_minimum=None):
    """
    The best lineup for each date in `dates`, plus a per-player summary.

    `roster` is the playerIds on the team, and `out` any of them to leave
    unseated (injured, suspended). `opponent` and `opponent_out` are the same
    for the other side, and turn on the matchup. `banked` is the score so far
    as {category: {'mine': n, 'theirs': n}}, for planning the rest of a week
    already under way, and `goalie_stats` each side's goaltending so far
    ({mine, theirs}, each {GA, SA, SV, GAA, GP}) - what GAA and save
    percentage are built on, since a ratio cannot be banked, and the
    appearances the goalie minimum counts. `goalie_minimum` is the league's
    minimum appearances a week (None or 0 for none). `moves` are planned
    add/drops on your roster. `lineups` are manual nights (see the module
    docstring), and `next_dates` the following week's dates, for listing each
    player's games in it.

    Pass a prepared `week` to skip rebuilding it; the other setup arguments
    are then ignored.
    """
    week = week or Week(pool, categories, roster_slots, dates, schedule,
                        team_stats, peripheral, points, pim_positive)
    moves = list(moves or [])
    banked = _clean_banked(banked, week.categories)

    _mine, unknown = week.resolve(roster)
    _theirs, unknown_theirs = week.resolve(opponent)
    my_nights = week.nights(roster, out or (), moves)
    their_nights = week.nights(opponent or (), opponent_out or ())
    run = _run(week, my_nights, their_nights, banked, has_opponent=bool(_theirs),
               manual=lineups, goalies=goalie_stats, minimum=goalie_minimum)

    next_games = games_by_team(week.schedule, next_dates or [])
    my_side = _side(week, roster, moves, my_nights, run['mine'], out,
                    manual=run['manual'], next_games=next_games)
    response = {
        'categories': week.categories,
        'rateCategories': week.rates,
        'missingCategories': week.missing,
        'weights': {c: round(w, 6) for c, w in run['weights'].items()
                    if c not in dv.RATE_COLUMNS},
        'seats': seat_order(week.slots),
        'adjusted': week.adjusted,
        'homeIce': bool(week.venue),
        **my_side,
        'unknownPlayers': unknown,
        'moves': moves,
        'opponent': None,
        'matchup': None,
        # Where each side stands against the goalie minimum, opponent or not
        'goalieMinimum': (_minimum_summary(run['minimum'], lambda x, p=DISPLAY_PLACES: round(x, p))
                          if run['minimum'] else None),
    }

    if _theirs:
        their_side = _side(week, opponent, (), their_nights, run['theirs'], opponent_out,
                           next_games=next_games)
        response['opponent'] = {**their_side, 'unknownPlayers': unknown_theirs}
        response['matchup'] = run['matchup']
    elif unknown_theirs:
        response['opponent'] = {'days': [], 'players': [], 'totals': {},
                                'unknownPlayers': unknown_theirs}

    return response


def free_agents(week, roster, rostered, out=None, opponent=None, opponent_out=None,
                banked=None, moves=None, evaluate=None, season=None,
                limit=EXACT_CANDIDATES, lineups=None, goalie_stats=None,
                goalie_minimum=None):
    """
    The adds that would help this week's matchup most, each with a drop and
    the date that gets the most out of the pair.

    `rostered` is every playerId on any team in the league; a free agent is
    anyone in the pool who is not in it. `moves` already planned are applied
    first, so a second add is judged against the roster the first leaves.

    Three narrowing passes, because re-planning the week for every free agent,
    drop and date - ~670 x 16 x 7 - would take minutes:

    1. **Screen** every free agent on unadjusted weighted value over the nights
       he plays, and keep `SCREEN_SHORTLIST`.
    2. **Shortlist** each against the `DROP_CANDIDATES` weakest rostered
       players, night by night, under the current plan's weights - how much
       tonight's optimal lineup improves with him in and the drop out - and
       sum from each possible date onward.
    3. **Exact**: the best `limit` pairs are re-planned in full on every date,
       matchup weights and all, so the gains shown are the real ones and a
       different date can be picked without asking again.

    **Drops come from the season, not the week.** A one-week gain says nothing
    about what a player is worth in March, so a suggested drop is one of the
    roster's weakest on `season` - {playerId: {value, rank}}, the draft board's
    value over replacement - and never a player marked out, since injured
    players are often stashed rather than cut. It has to be the draft board's
    number: lineup values are per game and not comparable between a goalie and
    a skater, and ranking drops on them suggested cutting Ilya Sorokin, the
    13th player on the board. Any other drop can be scored with `evaluate`.

    `evaluate` ({add, drop}) skips the search and scores that one pair on every
    date - the path a user takes when they disagree with the suggestion.

    `lineups` are the nights set by hand, held fixed exactly as `plan_week`
    holds them, so the baseline is the plan the page is showing. An add is
    worth nothing on a manual night he is not seated in - the user set that
    night - and a drop seated in one leaves the seat empty.

    `goalie_stats` is the goaltending so far and `goalie_minimum` the league's
    minimum, as `plan_week` takes them, so the metric counts GAA, save
    percentage and the minimum the same way - a goalie who gets you to the
    minimum is worth the categories (or points) it saves. Only the exact pass
    sees them: the screen and the shortlist score lines on counting stats.
    """
    moves = list(moves or [])
    out = {str(i) for i in (out or [])}
    banked = _clean_banked(banked, week.categories)
    rostered = {str(i) for i in (rostered or [])}
    for move in moves:
        rostered.add(str(move.get('add')))

    base_nights = week.nights(roster, out, moves)
    their_nights = week.nights(opponent or (), opponent_out or ())
    has_opponent = bool(week.resolve(opponent)[0])
    base = _run(week, base_nights, their_nights, banked, has_opponent, manual=lineups,
                goalies=goalie_stats, minimum=goalie_minimum)
    baseline = _metric(week, base, has_opponent)

    def exact(add_id, drop_id):
        """{date: gain} for one pair, re-planned in full on each date."""
        gains = {}
        for night in week.dates:
            trial = moves + [{'add': add_id, 'drop': drop_id, 'date': night}]
            run = _run(week, week.nights(roster, out, trial), their_nights, banked,
                       has_opponent, manual=lineups, goalies=goalie_stats,
                       minimum=goalie_minimum)
            gains[night] = _metric(week, run, has_opponent) - baseline
        return gains

    final_roster = roster_on(roster, moves, week.dates[-1]) if week.dates else []

    if evaluate:
        add_id, drop_id = str(evaluate.get('add')), evaluate.get('drop')
        drop_id = None if drop_id in (None, '') else str(drop_id)
        player = week.by_id.get(add_id)
        if player is None:
            raise ValueError("That player is not in the projections.")
        if add_id in rostered:
            raise ValueError(f"{player.get('fullName')} is already on a roster.")
        if drop_id is not None and drop_id not in final_roster:
            raise ValueError("The drop has to be on your roster.")
        gains = exact(add_id, drop_id)
        return _baseline_payload(week, baseline, has_opponent) | {
            'candidates': [_candidate(week, player, drop_id, gains, season)],
        }

    weights = base['weights']
    drops = _drop_candidates(week, final_roster, out, moves, season)

    # 1. Screen, on unadjusted value - adjusting 670 players for every night
    #    is the expensive part, and the shortlist only has to be roughly right.
    screened = []
    for player in week.valued:
        player_id = str(player['playerId'])
        if player_id in rostered:
            continue
        games = week.games_for(player)
        if not games:
            continue
        per_game = dv.score(player.get('perGame') or {}, weights)
        if _is_goalie(player):
            per_game *= min(1.0, _number(player.get('proj_gamesStarted'))
                            / max(1.0, _number(player.get('projectedGames'))))
        screened.append((per_game * games, player))
    screened.sort(key=lambda item: item[0], reverse=True)
    shortlist = [player for _score, player in screened[:SCREEN_SHORTLIST]]

    # 2. Shortlist: exact lineups, fixed weights, every drop and date at once.
    base_values = {n['date']: _lineup_value(n['players'], week.slots, weights)
                   for n in base_nights}
    pairs = []
    for player in shortlist:
        for drop in drops:
            nightly = []
            for night in base_nights:
                tonight = [p for p in night['players'] if str(p['playerId']) != drop]
                row = week.row(player, night['date'])
                if row is not None:
                    tonight = tonight + [row]
                nightly.append(_lineup_value(tonight, week.slots, weights)
                               - base_values[night['date']])
            # A pickup on date d earns every night from d on: suffix sums.
            best_gain, running = None, 0.0
            for gain in reversed(nightly):
                running += gain
                best_gain = running if best_gain is None else max(best_gain, running)
            pairs.append((best_gain, str(player['playerId']), drop))

    # One suggestion per free agent - his best drop - then the best of those.
    best_per_player = {}
    for gain, add_id, drop in sorted(pairs, key=lambda item: item[0], reverse=True):
        best_per_player.setdefault(add_id, (gain, drop))
    ranked = sorted(best_per_player.items(), key=lambda item: item[1][0], reverse=True)

    # 3. Exact, on every date, for the best few.
    candidates = []
    for add_id, (_gain, drop) in ranked[:limit]:
        gains = exact(add_id, drop)
        candidates.append(_candidate(week, week.by_id[add_id], drop, gains, season))
    candidates.sort(key=lambda c: c['gain'], reverse=True)

    return _baseline_payload(week, baseline, has_opponent) | {
        'candidates': candidates,
        'dropCandidates': drops,
        'freeAgents': sum(1 for p in week.valued if str(p['playerId']) not in rostered),
    }


def available(week, rostered, next_games=None):
    """
    Every free agent, as a player line the page can show beside a roster's.

    A free agent is anyone in the pool on no team in the league. Each carries
    what the roster view carries - his games this week and whom against, his
    games next week, his per-game line and where it sits in the pool - so one
    table renders both. No ranking here: the route adds the draft board's,
    and the page sorts.

    Goalies also carry `startShare`, their projected starts over their team's
    games, because a per-start line beside a skater's per-game one would
    otherwise read as far better than it is.
    """
    rostered = {str(i) for i in (rostered or ())}
    next_games = next_games or {}
    rows = []
    for player in week.valued:
        if str(player['playerId']) in rostered:
            continue
        team = gs.primary_team(player.get('teamAbbrevs'))
        nights = [{'date': night, 'opponent': week.facing[night][team],
                   'home': team in week.home_teams[night]}
                  for night in week.dates if team in week.facing.get(night, {})]
        row = {
            **_public(player),
            'games': len(nights),
            'nights': nights,
            'nextWeek': next_games.get(team, []),
            'perGame': _rounded({c: v for c, v in (player.get('perGame') or {}).items()
                                 if c in week.categories}),
            'heat': week.heat(player),
        }
        if _is_goalie(player):
            games = _number(player.get('projectedGames'))
            row['startShare'] = round(min(1.0, _number(player.get('proj_gamesStarted'))
                                          / games), 3) if games else None
        rows.append(row)
    return rows


def matchup(categories, mine, theirs, banked=None, polarity=None, points_per=None,
            places=DISPLAY_PLACES, ratios=None, minimum=None):
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

    Rate categories (GAA, SVpct) take their projection and odds from `ratios`,
    {mine, theirs} each a `goalie_ratios.side`; without it, or in a points
    league, they are listed but not scored. A rate row with no odds - a side
    with no goaltending to divide - says why in `note`, and is not counted in
    `scoredCategories`.

    `minimum` is the goalie minimum, {minimum, mine, theirs} each side a
    `goalie_minimum.side`. A side that falls short forfeits: every goalie
    category's odds and `contested` take in both sides' chances of reaching it
    (`goalie_minimum.category_odds`), and in a points league each side's goalie
    points count only as often as it gets there. `places=None` leaves every
    number unrounded, for comparing two plans whose difference is small.
    """
    banked = banked or {}
    polarity = polarity or dv.category_polarity(categories)
    r = (lambda x, p=places: round(x, p)) if places is not None else (lambda x, p=None: x)
    reach = (minimum['mine']['chance'], minimum['theirs']['chance']) if minimum else (1.0, 1.0)

    def settle(category, odds, contested):
        """A category's odds and contest once a side might forfeit its goalie categories."""
        if minimum and gm.is_goalie_category(category):
            return gm.category_odds(odds, *reach), contested * reach[0] * reach[1]
        return odds, contested

    rows, variance, points_margin = [], 0.0, 0.0
    goalie_points = {'mine': 0.0, 'theirs': 0.0, 'soFarMine': 0.0, 'soFarTheirs': 0.0}
    for category in categories:
        if category in dv.RATE_COLUMNS:
            rows.append(_ratio_row(category, ratios, polarity, r, settle)
                        if ratios and points_per is None
                        else {'category': category, 'rate': True})
            continue

        rm, rt = mine.get(category, 0.0), theirs.get(category, 0.0)
        bm = banked.get(category, {}).get('mine', 0.0)
        bt = banked.get(category, {}).get('theirs', 0.0)
        dispersion = mw.dispersion_for(category)
        row = {
            'category': category,
            'mine': r(bm + rm),
            'theirs': r(bt + rt),
            'bankedMine': bm,
            'bankedTheirs': bt,
        }

        if points_per is not None:
            per = points_per.get(category, 0.0)
            row['points'] = per
            points_margin += per * ((bm + rm) - (bt + rt))
            variance += per * per * dispersion * (abs(rm) + abs(rt))
            if gm.is_goalie_category(category):
                goalie_points['mine'] += per * (bm + rm)
                goalie_points['theirs'] += per * (bt + rt)
                goalie_points['soFarMine'] += per * bm
                goalie_points['soFarTheirs'] += per * bt
        else:
            sign = polarity.get(category, 1.0)
            margin = sign * ((bm + rm) - (bt + rt))
            sigma = mw.margin_sigma(rm, rt, dispersion)
            z = margin / sigma
            odds, contested = settle(category, _normal_cdf(z), math.exp(-0.5 * z * z))
            row['winProbability'] = r(odds)
            row['contested'] = r(contested)
        rows.append(row)

    if points_per is not None:
        mine_points = sum(r_['mine'] * r_['points'] for r_ in rows if not r_.get('rate'))
        theirs_points = sum(r_['theirs'] * r_['points'] for r_ in rows if not r_.get('rate'))
        result = {'mode': 'points', 'categories': rows}
        if minimum:
            # A side short of the minimum scores no goalie points at all
            lost_mine = (1 - reach[0]) * goalie_points['mine']
            lost_theirs = (1 - reach[1]) * goalie_points['theirs']
            mine_points -= lost_mine
            theirs_points -= lost_theirs
            points_margin += lost_theirs - lost_mine
            variance += (reach[0] * (1 - reach[0]) * goalie_points['mine'] ** 2
                         + reach[1] * (1 - reach[1]) * goalie_points['theirs'] ** 2)
            result['goaliePoints'] = {k: r(v, 2) for k, v in goalie_points.items()}
        sigma = max(math.sqrt(variance), mw.MIN_SIGMA)
        result.update({
            'minePoints': r(mine_points, 2) if places is not None else mine_points,
            'theirsPoints': r(theirs_points, 2) if places is not None else theirs_points,
            'winProbability': r(_normal_cdf(points_margin / sigma)),
        })
        if minimum:
            result['goalieMinimum'] = _minimum_summary(minimum, r)
        return result

    scored = [r_ for r_ in rows if r_.get('winProbability') is not None]
    wins = sum(r_['winProbability'] for r_ in scored)
    result = {
        'mode': 'categories',
        'categories': rows,
        'expectedWins': round(wins, 2) if places is not None else wins,
        'scoredCategories': len(scored),
    }
    if ratios:
        # What each side's ratios rest on, for the page to say so
        result['goaltending'] = {
            who: {'minutes': r(ratios[who]['minutes'], 1),
                  'minutesFrom': ratios[who]['minutesFrom'],
                  'starts': r(ratios[who]['starts'], 2)}
            for who in ('mine', 'theirs')}
    if minimum:
        result['goalieMinimum'] = _minimum_summary(minimum, r)
    return result


def _minimum_summary(minimum, r):
    """The goalie minimum and where each side stands, rounded for the page."""
    summary = {'minimum': minimum['minimum']}
    for who in ('mine', 'theirs'):
        side = minimum.get(who)
        if side:
            summary[who] = {'soFar': side['soFar'], 'soFarFrom': side['soFarFrom'],
                            'toCome': r(side['toCome'], 2), 'chance': r(side['chance'])}
    return summary


def _ratio_row(category, ratios, polarity, r, settle):
    """One rate category's matchup row, from both sides' `goalie_ratios.side`."""
    mine = ratios['mine'][category]
    theirs = ratios['theirs'][category]
    places = DISPLAY_PLACES + 1          # a save percentage is read to three

    def rounded(value):
        return None if value is None else r(value, places)

    row = {
        'category': category,
        'rate': True,
        'mine': rounded(mine['value']),
        'theirs': rounded(theirs['value']),
        'soFarMine': rounded(mine['soFar']),
        'soFarTheirs': rounded(theirs['soFar']),
    }
    found = gr.odds(mine, theirs, polarity.get(category, 1.0))
    if found is None:
        nobody = [who for who, side in (('you', mine), ('the opponent', theirs))
                  if side['value'] is None]
        row['note'] = f"No goaltending projected for {' or '.join(nobody)}"
        return row
    odds, contested = settle(category, *found)
    row['winProbability'], row['contested'] = r(odds), r(contested)
    return row


def _run(week, my_nights, their_nights, banked, has_opponent, manual=None, goalies=None,
         minimum=None):
    """
    Lineups for both sides and, with an opponent, the matchup.

    `manual` ({date: seats}) fixes those nights of mine as the user set them;
    `run['manual']` reports, per such night, the seats that could not be kept.
    `goalies` is each side's goaltending so far ({mine, theirs}), which GAA and
    save percentage are built on, and the goalie minimum is read against.
    `minimum` is the league's goalie minimum (None or 0 for none); with one
    and any goalie category, `run['minimum']` is each side's chance of
    reaching it.

    Returns {mine, theirs} as {date: lineup}, the weights my lineups were set
    under, both sides' projected totals, and the matchup twice over - rounded
    for the page and exact for comparing plans.
    """
    mine = [{'date': n['date'], 'mine': n['players'],
             'theirs': t['players']} for n, t in zip(my_nights, their_nights)]

    fixed, kept = {}, {}
    for night in my_nights:
        seats = (manual or {}).get(night['date'])
        if seats is not None:
            fixed[night['date']], problems, placed = manual_lineup(
                night['players'], week.slots, seats)
            kept[night['date']] = {'problems': problems, 'seats': placed}

    if has_opponent and not week.points:
        banked_margin = {c: v['mine'] - v['theirs'] for c, v in banked.items()}
        result = mw.optimise_week(mine, week.slots, week.categories, _normalised(week.flat),
                                  polarity=week.polarity, banked_margin=banked_margin,
                                  fixed=fixed)
        my_lineups, their_lineups = result['lineups'], result['opponentLineups']
        weights = result['weights']
    else:
        my_lineups = {n['date']: fixed[n['date']] if n['date'] in fixed
                      else optimal_lineup(n['players'], week.slots)
                      for n in my_nights}
        their_lineups = {n['date']: optimal_lineup(n['players'], week.slots)
                         for n in their_nights}
        weights = dict(week.flat)

    totals = _counting(mw.project_totals(my_lineups.values(), week.categories))
    their_totals = _counting(mw.project_totals(their_lineups.values(), week.categories))
    run = {'mine': my_lineups, 'theirs': their_lineups, 'weights': weights,
           'totals': totals, 'theirTotals': their_totals,
           'matchup': None, 'exact': None, 'manual': kept, 'minimum': None}

    goalies = goalies if isinstance(goalies, dict) else {}
    if minimum and any(gm.is_goalie_category(c) for c in week.categories):
        run['minimum'] = {'minimum': minimum,
                          'mine': gm.side(goalies.get('mine'), my_lineups, minimum)}
        if has_opponent:
            run['minimum']['theirs'] = gm.side(goalies.get('theirs'), their_lineups, minimum)
        if week.points:
            # Your goalie points, banked and to come - what falling short costs
            run['goaliePoints'] = sum(
                week.points.get(c, 0.0) * (totals.get(c, 0.0)
                                           + _number((banked.get(c) or {}).get('mine')))
                for c in week.categories if gm.is_goalie_category(c))

    if has_opponent:
        per = week.points if week.points else None
        ratios = None
        if week.rates and not week.points:
            ratios = {'mine': gr.side(goalies.get('mine'), my_lineups.values()),
                      'theirs': gr.side(goalies.get('theirs'), their_lineups.values())}
        run['matchup'] = matchup(week.categories, totals, their_totals, banked,
                                 week.polarity, points_per=per, ratios=ratios,
                                 minimum=run['minimum'])
        run['exact'] = matchup(week.categories, totals, their_totals, banked,
                               week.polarity, points_per=per, places=None, ratios=ratios,
                               minimum=run['minimum'])
    return run


def seat_order(slots):
    """Every starting seat's slot, in display order - the index a manual lineup uses."""
    ordered = [s for s in SLOT_ORDER if s in slots]
    ordered += sorted(s for s in slots if s not in SLOT_ORDER)
    return [slot for slot in ordered for _ in range(slots[slot])]


def manual_lineup(players, slots, seats):
    """
    ({slot: [player]}, problems, placed) for one night set by hand.

    `players` are tonight's available rows - on the roster, not out, with a
    game - and `seats` a playerId (or None) per seat in `seat_order`. A seat
    whose player is not among them, is seated twice, or is not eligible for
    that slot is left empty and reported as {seat, playerId, reason}.

    `placed` is the player (or None) in each seat, in seat order. The lineup
    dict cannot carry that - `{slot: [players]}` has no empty places - so
    without it, emptying the first of two C seats would slide the second C
    into it on the page.
    """
    order = seat_order(slots)
    tonight = {str(p['playerId']): p for p in players}
    lineup = {slot: [] for slot in slots}
    used, problems = set(), []
    placed = [None] * len(order)
    for index, slot in enumerate(order):
        player_id = seats[index] if index < len(seats or []) else None
        if player_id in (None, ''):
            continue
        player_id = str(player_id)
        player = tonight.get(player_id)
        if player is None:
            reason = 'unavailable'
        elif player_id in used:
            reason = 'twice'
        elif slot not in slots_for(player.get('eligiblePositions'), [slot]):
            reason = 'ineligible'
        else:
            lineup[slot].append(player)
            placed[index] = player
            used.add(player_id)
            continue
        problems.append({'seat': index, 'playerId': player_id, 'reason': reason})
    return lineup, problems, placed


def games_by_team(schedule, dates):
    """{team: [{date, opponent, home}]} for the games on `dates`, in date order."""
    wanted = {str(d) for d in dates}
    games = defaultdict(list)
    for game_date, home, away in sorted(schedule or [], key=lambda g: str(g[0])):
        game_date = str(game_date)
        if game_date in wanted:
            games[home].append({'date': game_date, 'opponent': away, 'home': True})
            games[away].append({'date': game_date, 'opponent': home, 'home': False})
    return games


def _metric(week, run, has_opponent):
    """
    The one number a move is judged on: expected categories won, projected
    fantasy points, or - with no opponent to be in doubt against - the total
    flat value of the week's lineups.
    """
    if week.points:
        points = sum(week.points.get(c, 0.0) * v for c, v in run['totals'].items())
        if run.get('minimum'):
            # Goalie points count only as often as the minimum is reached, so
            # a move that gets you there saves the ones already banked
            points -= (1 - run['minimum']['mine']['chance']) * run.get('goaliePoints', 0.0)
        return points
    if has_opponent:
        return run['exact']['expectedWins']
    return sum(_lineup_value([p for seats in lineup.values() for p in seats], None,
                             week.flat, seated=True)
               for lineup in run['mine'].values())


def _baseline_payload(week, baseline, has_opponent):
    if week.points:
        metric = 'points'
    elif has_opponent:
        metric = 'expectedWins'
    else:
        metric = 'value'
    return {'metric': metric, 'baseline': round(baseline, DISPLAY_PLACES),
            'dates': week.dates}


def _candidate(week, player, drop_id, gains, season=None):
    """
    One suggestion: the pair, the gain on every date, and the best date - the
    earliest of any tied for best, since an add that changes nothing on the
    first nights costs nothing by being made early.
    """
    recommended = max(week.dates, key=lambda d: (round(gains[d], 9), -week.dates.index(d)))
    drop = week.by_id.get(drop_id) if drop_id else None
    season = season or {}

    def with_rank(row):
        public = _public(row)
        public['seasonRank'] = (season.get(str(row['playerId'])) or {}).get('rank')
        return public

    return {
        'player': with_rank(player),
        'drop': with_rank(drop) if drop else None,
        'gamesLeft': week.games_for(player),
        'gains': {d: round(g, DISPLAY_PLACES) for d, g in gains.items()},
        'recommendedDate': recommended,
        'gain': round(gains[recommended], DISPLAY_PLACES),
    }


def _drop_candidates(week, roster_ids, out, moves, season=None):
    """
    The roster's weakest players over the season, weakest first.

    On `season` (the draft board's value over replacement) where it is given.
    Without it, flat per-game value times projected games - a fallback only,
    because per-game values are not comparable across positions. Players
    marked out and players a planned move adds are left off - the one is often
    stashed on IR rather than cut, the other was just picked up on purpose.
    """
    added = {str(m.get('add')) for m in moves}
    scored = []
    for player_id in roster_ids:
        player = week.by_id.get(player_id)
        if player is None or player_id in out or player_id in added:
            continue
        if season and player_id in season:
            value = _number(season[player_id].get('value'))
        else:
            games = (_number(player.get('proj_gamesStarted')) if _is_goalie(player)
                     else _number(player.get('projectedGames')))
            value = _number(player.get('value')) * games
        scored.append((value, player_id))
    scored.sort()
    return [player_id for _value, player_id in scored[:DROP_CANDIDATES]]


def _lineup_value(players, slots, weights, seated=False):
    """Total value of tonight's best lineup under `weights`."""
    rescored = []
    for player in players:
        row = dict(player)
        row['value'] = dv.score(player.get('perGame') or {}, weights)
        rescored.append(row)
    if seated:
        return sum(p['value'] for p in rescored)
    lineup = optimal_lineup(rescored, slots)
    return sum(p['value'] for seats in lineup.values() for p in seats)


def _side(week, ids, moves, nights, lineups, out, manual=None, next_games=None):
    """
    One roster's week: nightly seats and bench, per-player summary, totals.

    Each night also lists who is `playing` and which slots each could take, so
    the page can offer a manual edit without knowing the eligibility rules;
    `manual` marks the nights set by hand and what could not be kept. Each
    player carries his `nights` - start, bench or out, against whom - his
    unadjusted per-game line, and his games in the next week.
    """
    out = {str(i) for i in (out or [])}
    manual = manual or {}
    next_games = next_games or {}
    seat_slots = sorted(set(seat_order(week.slots)))
    player_nights = defaultdict(list)
    everyone = _unique([i for night in nights for i in night['ids']]
                       + [str(i) for i in _unique(ids)])
    players = [week.by_id[i] for i in everyone if i in week.by_id]
    summary = {str(p['playerId']): {'games': 0, 'starts': 0.0, 'benchedGames': 0}
               for p in players}

    days = []
    for night in nights:
        on_roster = set(night['ids'])
        for player in players:
            player_id = str(player['playerId'])
            if player_id in on_roster and week.games_for(player, [night['date']]):
                summary[player_id]['games'] += 1

        lineup = lineups.get(night['date']) or {}
        seated = set()
        for seats in lineup.values():
            for player in seats:
                player_id = str(player['playerId'])
                seated.add(player_id)
                summary[player_id]['starts'] += player.get('startProbability', 1.0)

        # By id, not object identity: optimise_week re-values its players into
        # new dicts, so `lineup_utils.benched` would find nobody seated.
        bench = [p for p in night['players'] if str(p['playerId']) not in seated]
        for player in bench:
            summary[str(player['playerId'])]['benchedGames'] += 1

        playing = {str(p['playerId']) for p in night['players']}
        for player in players:
            player_id = str(player['playerId'])
            team = gs.primary_team(player.get('teamAbbrevs'))
            opponent = week.facing.get(night['date'], {}).get(team)
            if player_id not in on_roster or opponent is None:
                continue
            status = ('start' if player_id in seated else
                      'bench' if player_id in playing else 'out')
            player_nights[player_id].append({
                'date': night['date'], 'opponent': opponent,
                'home': team in week.home_teams[night['date']], 'status': status,
            })

        day = {
            'date': night['date'],
            'nhlGames': week.games_on.get(night['date'], 0),
            'slots': _seat_list(lineup, week.slots),
            'bench': [_public(p) for p in bench],
            'playing': [{**_public(p),
                         'slots': slots_for(p.get('eligiblePositions'), seat_slots)}
                        for p in night['players']],
        }
        if night['date'] in manual:
            day['manual'] = True
            day['problems'] = manual[night['date']]['problems']
            # Seat for seat as the user set them, not repacked by slot
            day['slots'] = [{'slot': slot, 'player': _public(player) if player else None}
                            for slot, player in zip(seat_order(week.slots),
                                                    manual[night['date']]['seats'])]
        days.append(day)

    added = {str(m.get('add')) for m in moves}
    dropped = {str(m.get('drop')) for m in moves if m.get('drop') is not None}
    totals = mw.project_totals(lineups.values(), week.categories)
    return {
        'days': days,
        'players': [
            {**_public(p), 'out': str(p['playerId']) in out,
             'added': str(p['playerId']) in added,
             'dropped': str(p['playerId']) in dropped,
             **_rounded(summary[str(p['playerId'])]),
             'nights': player_nights.get(str(p['playerId']), []),
             'nextWeek': next_games.get(gs.primary_team(p.get('teamAbbrevs')), []),
             'perGame': _rounded({c: v for c, v in (p.get('perGame') or {}).items()
                                  if c in week.categories}),
             'heat': week.heat(p)}
            for p in players
        ],
        'totals': _rounded(_counting(totals)),
    }


def _counting(totals):
    return {c: v for c, v in totals.items() if c not in dv.RATE_COLUMNS}


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


def _seat_list(lineup, slots):
    """Every seat in display order, with None for one nobody could fill."""
    seats, taken = [], defaultdict(int)
    for slot in seat_order(slots):
        filled = lineup.get(slot, [])
        index = taken[slot]
        taken[slot] += 1
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
    # Season to date: how many games his line is over, and whether he has none
    # and is on his projection instead
    for key in ('statSource', 'seasonGames'):
        if key in player:
            fields[key] = player[key]
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
