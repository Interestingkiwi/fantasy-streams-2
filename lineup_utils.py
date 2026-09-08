"""
Seating a day's players into a league's starting slots, exactly.

This is the daily lineup problem: given the players on a roster who have an
NHL game tonight, each with a value and a set of eligible positions, fill the
league's starting slots so the total value is as high as possible. Multi-slot
eligibility is the whole difficulty - a C,RW must not occupy the last C slot
when a C-only player is sitting on the bench and the RW slot is open.

Replaces the old app's four-pass greedy (`app.py get_optimal_lineup`), which
approximated this with a scarcity heuristic plus two repair passes and got it
wrong often enough to matter. See docs/OPTIMIZER.md for why the objective and
the search both changed.

**Why a greedy is exact here.** The sets of players that can be seated
simultaneously form a transversal matroid, and the greedy algorithm returns a
maximum-weight basis of a matroid. So taking players in descending value and
seating each one whenever the rest can still be seated is not an approximation
- it is optimal, in both total value and number of starts. The part the old
code was missing is the independence test: deciding whether a player fits is
not "is one of his slots free", it is "can the players already seated be
rearranged to make room", which is an augmenting-path search.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

from ranking_utils import PRIMARY_ALIASES, YAHOO_POSITIONS

# Yahoo's generic slots, and the concrete positions each will accept. Util
# takes any skater but never a goalie.
GENERIC_SLOTS = {
    'F': frozenset({'C', 'LW', 'RW'}),
    'W': frozenset({'LW', 'RW'}),
    'Util': frozenset({'C', 'LW', 'RW', 'D'}),
}

# Slots that are not a start. NA is Yahoo's not-in-NHL slot; the IR variants
# and the bench are self-explanatory. Filtered rather than assumed absent,
# because `lineup_settings` stores them alongside the starting slots.
NON_STARTING_SLOTS = frozenset({'BN', 'IR', 'IR+', 'NA'})


def starting_slots(roster_slots):
    """
    {slot: count} narrowed to slots a player actually starts in.

    Drops the bench and the IR variants, and drops any slot the league has
    set to zero, so callers can hand over `lineup_settings` unmodified.
    """
    return {
        slot: int(count)
        for slot, count in (roster_slots or {}).items()
        if slot not in NON_STARTING_SLOTS and int(count or 0) > 0
    }


def slots_for(eligibility, slot_names):
    """
    Which of this league's slots a player is eligible for.

    'C,RW' in a league with C/LW/RW/D/Util/G gives ['C', 'RW', 'Util']. Takes
    the NHL primary codes as well, via `ranking_utils.PRIMARY_ALIASES`, so a
    row predating the eligibility import still lands somewhere sensible.
    """
    positions = set()
    for raw in str(eligibility or '').split(','):
        code = raw.strip().upper()
        if not code:
            continue
        code = PRIMARY_ALIASES.get(code, code)
        if code in YAHOO_POSITIONS:
            positions.add(code)

    available = set(slot_names)
    slots = [slot for slot in available if slot in positions]
    slots += [
        slot for slot, accepts in GENERIC_SLOTS.items()
        if slot in available and positions & accepts
    ]
    return slots


def optimal_lineup(players, roster_slots, value_key='value',
                   eligibility_key='eligiblePositions', seat_all=True):
    """
    Seat `players` into `roster_slots`, maximising total value.

    `players` is a list of dicts; identity is the position in that list, so no
    player id is needed and duplicate or missing ids cannot corrupt the
    result. Returns {slot: [player, ...]} covering every starting slot, with
    an empty list for slots that could not be filled.

    `seat_all=True` (the default, and the old app's behaviour) starts every
    player who fits, so the result also carries the most possible starts.
    Set it False once values can go negative - under §5 of docs/OPTIMIZER.md a
    category-weighted value can be worse than not playing at all - and players
    valued at or below zero are then left on the bench.
    """
    slots = starting_slots(roster_slots)
    if not slots or not players:
        return {slot: [] for slot in slots}

    candidates = list(range(len(players)))
    if not seat_all:
        candidates = [i for i in candidates if _value(players[i], value_key) > 0]

    # Descending value is what makes the greedy a maximum-weight basis rather
    # than merely a maximal one.
    candidates.sort(key=lambda i: _value(players[i], value_key), reverse=True)

    eligible = {i: slots_for(players[i].get(eligibility_key), slots)
                for i in candidates}

    seated = {slot: [] for slot in slots}     # slot -> [player index, ...]
    for index in candidates:
        _seat(index, eligible, seated, slots, set())

    return {slot: [players[i] for i in indexes] for slot, indexes in seated.items()}


def benched(players, lineup):
    """The players `optimal_lineup` could not seat, in their original order."""
    started = {id(player) for seats in lineup.values() for player in seats}
    return [player for player in players if id(player) not in started]


def lineup_value(lineup, value_key='value'):
    """Total value of a seated lineup."""
    return sum(_value(player, value_key)
               for seats in lineup.values() for player in seats)


def _value(player, value_key):
    try:
        return float(player.get(value_key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _seat(index, eligible, seated, capacity, visited):
    """
    Try to seat player `index`, displacing others only into free slots.

    The augmenting-path search: if every slot the player is eligible for is
    full, each occupant gets a chance to move elsewhere, recursively. `visited`
    stops a slot being reconsidered inside one attempt, which both terminates
    the recursion and keeps it linear in the slots.
    """
    for slot in eligible[index]:
        if slot in visited:
            continue
        visited.add(slot)

        if len(seated[slot]) < capacity[slot]:
            seated[slot].append(index)
            return True

        for position, occupant in enumerate(seated[slot]):
            seated[slot].pop(position)
            if _seat(occupant, eligible, seated, capacity, visited):
                seated[slot].append(index)
                return True
            seated[slot].insert(position, occupant)

    return False
