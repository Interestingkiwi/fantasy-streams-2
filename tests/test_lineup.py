"""
Tests for the daily lineup matcher.

The centrepiece is the brute-force comparison: for hundreds of randomised
rosters small enough to enumerate exhaustively, the matcher's total value must
equal the best any assignment can achieve. That is what distinguishes this
implementation from the old app's four-pass greedy, so it is what gets tested
hardest - the hand-written cases below only pin the behaviours a human would
want to read back.

Also runs every league in the imported `lineup_settings` fixtures against real
projections, which is the check that the slot vocabulary is right.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import itertools
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lineup_utils import (                                  # noqa: E402
    benched, lineup_value, optimal_lineup, slots_for, starting_slots,
)

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def player(name, positions, value):
    return {"fullName": name, "eligiblePositions": positions, "value": value}


# --------------------------------------------------------------------------
print("\n=== 1. slot vocabulary ===")

check("bench and IR are not starting slots",
      starting_slots({"C": 2, "BN": 4, "IR": 2, "IR+": 1, "NA": 1}) == {"C": 2},
      starting_slots({"C": 2, "BN": 4, "IR": 2, "IR+": 1, "NA": 1}))
check("zero-count slots are dropped",
      starting_slots({"C": 2, "W": 0}) == {"C": 2})
check("no settings is not a crash", starting_slots(None) == {})

LEAGUE = {"C": 2, "LW": 2, "RW": 2, "D": 4, "Util": 1, "G": 2}

check("a centre fills C and Util",
      sorted(slots_for("C", LEAGUE)) == ["C", "Util"],
      slots_for("C", LEAGUE))
check("a C,RW fills both plus Util",
      sorted(slots_for("C,RW", LEAGUE)) == ["C", "RW", "Util"],
      slots_for("C,RW", LEAGUE))
check("a goalie never reaches Util",
      sorted(slots_for("G", LEAGUE)) == ["G"], slots_for("G", LEAGUE))
check("NHL primary codes still land",
      sorted(slots_for("L,R", LEAGUE)) == ["LW", "RW", "Util"], slots_for("L,R", LEAGUE))
check("F takes any forward",
      sorted(slots_for("LW", {"F": 3, "D": 2})) == ["F"], slots_for("LW", {"F": 3, "D": 2}))
check("W takes wingers but not centres",
      slots_for("C", {"W": 2}) == [] and slots_for("RW", {"W": 2}) == ["W"])
check("unknown eligibility seats nowhere", slots_for("", LEAGUE) == [])


# --------------------------------------------------------------------------
print("\n=== 2. the case the old greedy got wrong ===")

# One C slot, one RW slot. The C,RW is worth more, so a naive pass seats him
# at C and benches the C-only player, wasting the RW slot.
lineup = optimal_lineup(
    [player("Flexible", "C,RW", 10.0), player("Centre only", "C", 8.0)],
    {"C": 1, "RW": 1},
)
check("the flexible player yields the C slot",
      [p["fullName"] for p in lineup["C"]] == ["Centre only"], lineup)
check("...and takes the RW slot himself",
      [p["fullName"] for p in lineup["RW"]] == ["Flexible"], lineup)
check("both players start", lineup_value(lineup) == 18.0, lineup_value(lineup))

# Two moves deep: the displaced player must himself displace someone.
lineup = optimal_lineup(
    [player("A", "C", 9.0), player("B", "C,LW", 8.0), player("C", "C,LW,RW", 7.0)],
    {"C": 1, "LW": 1, "RW": 1},
)
check("a two-step reshuffle still seats everyone",
      lineup_value(lineup) == 24.0, lineup)


# --------------------------------------------------------------------------
print("\n=== 3. brute force ===")

POSITION_POOL = ["C", "LW", "RW", "D", "C,LW", "C,RW", "LW,RW", "C,LW,RW", "D", "G"]
SLOT_SHAPES = [
    {"C": 1, "LW": 1, "RW": 1},
    {"C": 2, "LW": 1, "D": 1},
    {"C": 1, "W": 1, "D": 1, "Util": 1},
    {"F": 2, "D": 1, "G": 1},
    {"C": 1, "LW": 1, "RW": 1, "D": 2, "Util": 1, "G": 1},
]


def brute_force(players, roster_slots):
    """The best total value and start count over every legal assignment."""
    slots = starting_slots(roster_slots)
    options = [[None] + slots_for(p["eligiblePositions"], slots) for p in players]

    best_value, best_starts = 0.0, 0
    for assignment in itertools.product(*options):
        used = {}
        for slot in assignment:
            if slot is not None:
                used[slot] = used.get(slot, 0) + 1
        if any(count > slots[slot] for slot, count in used.items()):
            continue
        value = sum(players[i]["value"] for i, slot in enumerate(assignment) if slot)
        starts = sum(1 for slot in assignment if slot)
        if starts > best_starts or (starts == best_starts and value > best_value):
            best_starts = starts
        if value > best_value:
            best_value = value
    return round(best_value, 6), best_starts


random.seed(20260907)
value_failures, start_failures, trials = 0, 0, 0

for _ in range(400):
    shape = random.choice(SLOT_SHAPES)
    roster = [
        player(f"P{i}", random.choice(POSITION_POOL), round(random.uniform(0.5, 20.0), 2))
        for i in range(random.randint(1, 7))
    ]

    got = optimal_lineup(roster, shape)
    best_value, best_starts = brute_force(roster, shape)
    starts = sum(len(seats) for seats in got.values())
    trials += 1

    if round(lineup_value(got), 6) != best_value:
        value_failures += 1
    if starts != best_starts:
        start_failures += 1

check(f"maximum value on all {trials} random rosters", value_failures == 0,
      f"{value_failures} short of optimal")
check(f"maximum starts on all {trials} random rosters", start_failures == 0,
      f"{start_failures} short of optimal")


# --------------------------------------------------------------------------
print("\n=== 4. invariants ===")

random.seed(11)
over_capacity, double_seated, ineligible = 0, 0, 0

for _ in range(300):
    shape = random.choice(SLOT_SHAPES)
    roster = [
        player(f"P{i}", random.choice(POSITION_POOL), round(random.uniform(-5.0, 20.0), 2))
        for i in range(random.randint(0, 12))
    ]
    got = optimal_lineup(roster, shape)
    slots = starting_slots(shape)

    for slot, seats in got.items():
        if len(seats) > slots[slot]:
            over_capacity += 1
        for seated in seats:
            if slot not in slots_for(seated["eligiblePositions"], slots):
                ineligible += 1

    seated_ids = [id(p) for seats in got.values() for p in seats]
    if len(seated_ids) != len(set(seated_ids)):
        double_seated += 1

check("no slot is over-filled", over_capacity == 0, over_capacity)
check("nobody is seated twice", double_seated == 0, double_seated)
check("nobody is seated somewhere they are not eligible", ineligible == 0, ineligible)
check("every slot appears in the result even when unfilled",
      set(optimal_lineup([], LEAGUE)) == set(LEAGUE))
# benched() matches on object identity, so it must be handed the same list
# optimal_lineup was given - which is the normal way to hold a roster.
roster = [player("A", "C", 5.0), player("B", "C", 4.0)]
bench = benched(roster, optimal_lineup(roster, {"C": 1}))
check("bench holds exactly the unseated",
      [p["fullName"] for p in bench] == ["B"], bench)


# --------------------------------------------------------------------------
print("\n=== 5. negative values ===")

roster = [player("Good", "C", 5.0), player("Harmful", "C", -3.0)]
seated_all = optimal_lineup(roster, {"C": 2})
check("seat_all starts everyone who fits, even at a loss",
      sum(len(s) for s in seated_all.values()) == 2, seated_all)

selective = optimal_lineup(roster, {"C": 2}, seat_all=False)
check("seat_all=False benches the negative",
      [p["fullName"] for p in selective["C"]] == ["Good"], selective)
check("...and so scores higher", lineup_value(selective) == 5.0)


# --------------------------------------------------------------------------
print("\n=== 6. real league shapes and real players ===")

try:
    from db import fetch_all

    shapes = {}
    for row in fetch_all("SELECT league_id, position, position_count FROM lineup_settings"):
        shapes.setdefault(row["league_id"], {})[row["position"]] = row["position_count"]

    pool = fetch_all('''
        SELECT "fullName", "eligiblePositions", "proj_points" AS value
        FROM final_projections
        WHERE "eligiblePositions" IS NOT NULL
        LIMIT 400
    ''')
    pool = [dict(row) for row in pool]

    check("lineup_settings fixtures are loaded", len(shapes) > 0, len(shapes))
    check("projections are loaded", len(pool) > 0, len(pool))

    random.seed(7)
    bad_shape = None
    for league_id, shape in shapes.items():
        slots = starting_slots(shape)
        if not slots:
            continue
        roster = random.sample(pool, min(20, len(pool)))
        got = optimal_lineup(roster, shape)

        for slot, seats in got.items():
            if len(seats) > slots[slot]:
                bad_shape = f"league {league_id} over-filled {slot}"
            for seated in seats:
                if slot not in slots_for(seated["eligiblePositions"], slots):
                    bad_shape = f"league {league_id} seated {seated['fullName']} at {slot}"

    check(f"all {len(shapes)} imported league shapes seat legally", bad_shape is None, bad_shape)

    # The one shape most likely to break the vocabulary: grouped forwards.
    grouped = [lid for lid, shape in shapes.items() if 'F' in starting_slots(shape)]
    check("grouped-forward leagues are present in the fixtures", len(grouped) > 0, len(grouped))
    if grouped:
        shape = shapes[grouped[0]]
        forwards = [p for p in pool
                    if set(str(p["eligiblePositions"]).split(',')) & {"C", "LW", "RW"}]
        got = optimal_lineup(forwards[:20], shape)
        check("forwards fill an F slot", len(got.get("F", [])) > 0, got.get("F"))

except Exception as exc:                                    # noqa: BLE001
    check("database-backed checks ran", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
