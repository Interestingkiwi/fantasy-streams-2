"""
Tests for opponent manager profiling.

The hold-pairing is where the subtlety is: managers re-add the same player
often, holds that never end have to be handled rather than dropped, and a
trade ends a hold without being a drop. The real-data section then checks the
claim the whole classifier rests on - that the managers it calls streamers
really do hold their pickups for less time than the ones it calls targeted.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import os
import statistics
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import manager_profiles as mp                                # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def move(day, player, kind, team="Alpha", league=1):
    return {"league_id": league, "transaction_date": f"2026-01-{day:02d}",
            "player_id": player, "fantasy_team": team, "move_type": kind}


def one(profiles, team="Alpha", league=1):
    return profiles[(league, team)]


# --------------------------------------------------------------------------
print("\n=== 1. pairing adds to the move that ends them ===")

simple = mp.profile_managers([
    move(1, 100, "add"), move(6, 100, "drop"),
    move(1, 200, "add"), move(3, 200, "drop"),
    move(20, 300, "add"), move(30, 300, "drop"),
])
check("a hold is measured to the drop that ends it",
      one(simple)["medianHoldDays"] == 5.0, one(simple)["medianHoldDays"])
check("adds and drops are counted separately",
      one(simple)["adds"] == 3 and one(simple)["drops"] == 3, one(simple))

# The same player added, dropped and added again: two holds, not one long one.
readd = mp.profile_managers([
    move(1, 100, "add"), move(3, 100, "drop"),
    move(10, 100, "add"), move(14, 100, "drop"),
])
check("re-adding the same player starts a second hold",
      one(readd)["holds"] == 2 and one(readd)["medianHoldDays"] == 3.0,
      (one(readd)["holds"], one(readd)["medianHoldDays"]))

# A duplicate add with no intervening drop must not open a second hold.
sloppy = mp.profile_managers([
    move(1, 100, "add"), move(2, 100, "add"), move(5, 100, "drop"),
])
check("a repeated add without a drop is still one hold",
      one(sloppy)["holds"] == 1 and one(sloppy)["medianHoldDays"] == 4.0,
      (one(sloppy)["holds"], one(sloppy)["medianHoldDays"]))

traded = mp.profile_managers([
    move(1, 100, "add"), move(9, 100, "trade"),
    move(1, 200, "add"), move(9, 200, "drop"),
])
check("a trade ends a hold too", one(traded)["holds"] == 2, one(traded))
check("...but is counted apart from drops",
      one(traded)["trades"] == 1 and one(traded)["drops"] == 1, one(traded))

# A drop with no matching add cannot invent a hold.
orphan = mp.profile_managers([move(4, 100, "drop"), move(6, 200, "add"),
                              move(9, 200, "drop")])
check("a drop with no add before it is ignored",
      one(orphan)["holds"] == 1, one(orphan))


# --------------------------------------------------------------------------
print("\n=== 2. holds that never end ===")

censored = mp.profile_managers([
    move(1, 100, "add"), move(5, 100, "drop"),
    move(2, 200, "add"),                       # still held when data stops
    move(20, 300, "add"), move(20, 300, "drop"),
])
check("an unfinished hold is kept, at its lower bound",
      one(censored)["holds"] == 3, one(censored))
check("...measured to the last day the league was active",
      one(censored)["censoredShare"] > 0, one(censored))

mostly_open = mp.profile_managers([
    move(1, 100, "add"), move(2, 100, "drop"),
    move(1, 200, "add"), move(1, 300, "add"), move(1, 400, "add"),
])
check("a manager whose holds are mostly unfinished is flagged",
      mostly_open[(1, "Alpha")]["medianIsLowerBound"] is True,
      mostly_open[(1, "Alpha")])
check("...and one whose holds mostly closed is not",
      one(censored)["medianIsLowerBound"] is False, one(censored))


# --------------------------------------------------------------------------
print("\n=== 3. rates and classification ===")

# Two adds across a four-week league span is half an add per week.
paced = mp.profile_managers([
    move(1, 100, "add"), move(2, 100, "drop"),
    move(29, 200, "add"), move(30, 200, "drop"),
])
check("adds per week is measured against the league's own span",
      abs(one(paced)["addsPerWeek"] - 2 / (29 / 7)) < 0.02, one(paced))

check("barely active is inactive whatever the holds look like",
      mp.classify(0.2, 3) == "inactive")
check("active with short holds is a streamer", mp.classify(2.5, 5) == "streamer")
check("active with long holds is targeted", mp.classify(2.5, 40) == "targeted")
check("busy but patient is targeted, not a streamer",
      mp.classify(3.3, 20) == "targeted")
check("short holds but not busy enough is targeted, not a streamer",
      mp.classify(0.8, 4) == "targeted")
check("no holds at all cannot be a streamer",
      mp.classify(2.0, None) == "targeted")

check("an inactive manager is simulated as making no moves",
      mp.simulation_plan({"style": "inactive", "simulatedAddsPerWeek": 0.3})["moves"] == 0.0)
check("a streamer is simulated as filling empty slots",
      mp.simulation_plan({"style": "streamer", "simulatedAddsPerWeek": 3.0})["aim"]
      == "fill-empty-slots")
check("a targeted manager is simulated as upgrading, not slot-filling",
      mp.simulation_plan({"style": "targeted", "simulatedAddsPerWeek": 1.2})["aim"]
      == "upgrade-weakest")

check("empty input is empty output, not a crash", mp.profile_managers([]) == {})
check("unusable rows are skipped",
      mp.profile_managers([{"league_id": 1, "transaction_date": "nonsense",
                            "player_id": 1, "fantasy_team": "A", "move_type": "add"}]) == {})
check("managers are keyed per league, so two leagues never merge",
      set(mp.profile_managers([move(1, 100, "add", league=1),
                               move(1, 100, "add", league=2)]))
      == {(1, "Alpha"), (2, "Alpha")})


# --------------------------------------------------------------------------
print("\n=== 4. the imported season ===")

try:
    from db import engine, text

    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(text(
            "SELECT league_id, transaction_date, player_id, fantasy_team, move_type "
            "FROM transactions"))]

    check("a season of transactions is loaded", len(rows) > 20000, len(rows))

    profiles = mp.profile_managers(rows)
    styles = Counter(p["style"] for p in profiles.values())

    check("every manager in the data gets a profile", len(profiles) > 200, len(profiles))
    check("all three styles are actually used, none swallowing the rest",
          all(styles[s] > 20 for s in mp.STYLES), dict(styles))

    # The claim the classifier rests on. If streamers do not hold their
    # pickups for less time than targeted managers, the split means nothing.
    def median_hold(style):
        holds = [p["medianHoldDays"] for p in profiles.values()
                 if p["style"] == style and p["medianHoldDays"] is not None]
        return statistics.median(holds)

    check("streamers really do hold their pickups for less time",
          median_hold("streamer") < median_hold("targeted"),
          (median_hold("streamer"), median_hold("targeted")))
    check("...and by a wide enough margin to be worth splitting on",
          median_hold("targeted") > 2 * median_hold("streamer"),
          (median_hold("streamer"), median_hold("targeted")))

    streamer_short = statistics.median(
        [p["shortHoldShare"] for p in profiles.values()
         if p["style"] == "streamer" and p["shortHoldShare"] is not None])
    targeted_short = statistics.median(
        [p["shortHoldShare"] for p in profiles.values()
         if p["style"] == "targeted" and p["shortHoldShare"] is not None])
    check("streamers drop a larger share of pickups within days",
          streamer_short > targeted_short, (streamer_short, targeted_short))

    # The rule from OPTIMIZER.md: never simulate more than they have done.
    check("a simulation is never allowed more moves than the manager makes",
          all(p["simulatedAddsPerWeek"] <= p["addsPerWeek"] + 1e-9
              for p in profiles.values()))
    check("an inactive manager is never handed moves to make",
          all(mp.simulation_plan(p)["moves"] == 0.0
              for p in profiles.values() if p["style"] == "inactive"))

    check("every profile reports how much of it is guesswork",
          all(0.0 <= p["censoredShare"] <= 1.0 for p in profiles.values()))

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
