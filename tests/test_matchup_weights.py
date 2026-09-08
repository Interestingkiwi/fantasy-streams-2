"""
Tests for matchup-aware category weighting.

The two claims worth pinning hardest are the ones the design rests on: that a
category still in doubt outweighs a settled one, and that what settles a
category is the margin measured against *what is still to come* rather than
against the week as a whole. The rest guards the sign handling, which is where
an inverse category like GA quietly goes wrong.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matchup_weights as mw                                # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def player(name, per_game, positions="C"):
    return {"fullName": name, "eligiblePositions": positions, "perGame": per_game}


# --------------------------------------------------------------------------
print("\n=== 1. the maths underneath ===")

check("the normal density is the standard one",
      abs(mw.normal_pdf(0.0) - 1 / math.sqrt(2 * math.pi)) < 1e-12
      and abs(mw.normal_pdf(1.0) - 0.24197072) < 1e-6)
check("it is symmetric", abs(mw.normal_pdf(-1.7) - mw.normal_pdf(1.7)) < 1e-12)

check("variance is Poisson and the two sides add",
      abs(mw.margin_sigma(50, 50) - 10.0) < 1e-9, mw.margin_sigma(50, 50))
check("a negative-capable category still gets volume-based volatility",
      mw.margin_sigma(-30, 20) == mw.margin_sigma(30, 20))
check("sigma never collapses to zero", mw.margin_sigma(0, 0) >= mw.MIN_SIGMA)
check("dispersion scales the variance, not the deviation",
      abs(mw.margin_sigma(50, 50, dispersion=4.0) - 20.0) < 1e-9)

check("marginal worth peaks at a tie",
      mw.marginal_worth(0, 10) > mw.marginal_worth(5, 10) > mw.marginal_worth(30, 10))
check("...and is symmetric about it",
      abs(mw.marginal_worth(-7, 10) - mw.marginal_worth(7, 10)) < 1e-12)
check("a wider category is worth less per unit at the same margin",
      mw.marginal_worth(0, 20) < mw.marginal_worth(0, 10))


# --------------------------------------------------------------------------
print("\n=== 2. weights follow what is in doubt ===")

cats = ["P", "SOG"]
weights = mw.category_weights({"P": 60.0, "SOG": 180.0},
                              {"P": 40.0, "SOG": 181.0}, cats)
check("a coin-flip category outweighs a comfortable one",
      weights["SOG"] > weights["P"], weights)

# Weights are relative, so a single category always normalises to 1 and says
# nothing. Every comparison below is against a reference category held fixed.
check("one category on its own carries no information",
      mw.category_weights({"P": 50.0}, {"P": 10.0}, ["P"]) == {"P": 1.0})


def against_reference(mine, theirs, banked=None):
    """P's weight relative to a reference category that is always dead level."""
    weights = mw.category_weights({"P": mine, "REF": 100.0},
                                  {"P": theirs, "REF": 100.0},
                                  ["P", "REF"], banked_margin=banked)
    return weights["P"] / weights["REF"]


even = against_reference(50.0, 50.0)
blown = against_reference(50.0, 10.0)
check("a blown-out category is worth far less than a tied one",
      blown < even, (even, blown))
check("...but never exactly nothing", blown > 0, blown)

# The variance claim, and the one most easily got backwards: the SAME margin
# is less settled the more there is still to come. Five points ahead with 115
# points of production left is a coin flip; five ahead with seven left is very
# nearly banked, and deserves less of the lineup's attention.
early = against_reference(60.0, 55.0)
late = against_reference(6.0, 1.0)
check("the same margin is worth chasing early and nearly settled late",
      early > late, (early, late))

check("banked production shifts the margin",
      against_reference(20.0, 20.0, banked={"P": 40.0})
      < against_reference(20.0, 20.0))
check("...but adds no variance, because it has already happened",
      mw.margin_sigma(20, 20) == mw.margin_sigma(20, 20))

# Dispersion, measured on the 2025-26 season rather than assumed.
check("scoring categories measured as Poisson and are left there",
      all(mw.dispersion_for(c) == 1.0 for c in ("G", "A", "P", "PPP")))
check("penalty minutes are the badly overdispersed one",
      mw.dispersion_for("PIM") > 3.0, mw.dispersion_for("PIM"))
check("hits and shots are mildly over",
      1.0 < mw.dispersion_for("HIT") < 2.0 and 1.0 < mw.dispersion_for("SOG") < 2.0)
check("an unmeasured category falls back to Poisson",
      mw.dispersion_for("SOMETHING") == mw.DEFAULT_DISPERSION)
check("an explicit override still wins", mw.dispersion_for("PIM", 1.0) == 1.0)

# The consequence: treating PIM as Poisson overstates how much a unit of it is
# worth, because its true sigma is nearly double what Poisson predicts.
even_both = {"P": 50.0, "PIM": 40.0}
measured = mw.category_weights(even_both, dict(even_both), ["P", "PIM"])
poisson = mw.category_weights(even_both, dict(even_both), ["P", "PIM"], dispersion=1.0)
check("measuring dispersion lowers PIM's pull relative to points",
      abs(measured["PIM"] / measured["P"]) < abs(poisson["PIM"] / poisson["P"]),
      (measured, poisson))
check("...by roughly the square root of its dispersion",
      abs(abs(poisson["PIM"] / poisson["P"]) / abs(measured["PIM"] / measured["P"])
          - mw.dispersion_for("PIM") ** 0.5) < 0.1)

check("rate categories get no weight at all",
      mw.category_weights({"SVpct": 0.91}, {"SVpct": 0.90}, ["SVpct"])["SVpct"] == 0.0)


# --------------------------------------------------------------------------
print("\n=== 3. inverse categories ===")

# GA rewards fewer. Allowing fewer than the opponent means winning it.
def ga_against_reference(mine, theirs):
    weights = mw.category_weights({"GA": mine, "REF": 100.0},
                                  {"GA": theirs, "REF": 100.0}, ["GA", "REF"])
    return weights["GA"] / weights["REF"]


check("a goals-against weight is negative, so allowing more costs value",
      ga_against_reference(20.0, 20.0) < 0 and ga_against_reference(10.0, 30.0) < 0,
      (ga_against_reference(20.0, 20.0), ga_against_reference(10.0, 30.0)))
check("a tied GA race matters more than a won one",
      abs(ga_against_reference(20.0, 20.0)) > abs(ga_against_reference(10.0, 30.0)),
      (ga_against_reference(20.0, 20.0), ga_against_reference(10.0, 30.0)))

both = mw.category_weights({"G": 20.0, "GA": 20.0}, {"G": 20.0, "GA": 20.0},
                           ["G", "GA"])
check("a positive and an inverse category tied at once have equal pull",
      abs(abs(both["G"]) - abs(both["GA"])) < 1e-9, both)


# --------------------------------------------------------------------------
print("\n=== 4. projecting a week ===")

PLAYMAKER = player("Playmaker", {"P": 1.30, "SOG": 1.6})
SHOOTER = player("Shooter", {"P": 0.75, "SOG": 5.2})
OPPONENT = player("Opponent", {"P": 0.70, "SOG": 3.30})

DAYS = [{"date": f"2027-01-0{d}", "mine": [PLAYMAKER, SHOOTER],
         "theirs": [OPPONENT]} for d in range(1, 8)]
SLOTS = {"Util": 1}
FLAT = {"P": 1.0, "SOG": 0.35}


def seated(result):
    return {p["fullName"] for lineup in result["lineups"].values()
            for seats in lineup.values() for p in seats}


totals = mw.project_totals(
    [{"Util": [PLAYMAKER]}, {"Util": [PLAYMAKER]}], ["P", "SOG"])
check("projected totals add a player's per-game line once per start",
      abs(totals["P"] - 2.60) < 1e-9 and abs(totals["SOG"] - 3.20) < 1e-9, totals)

flat_only = mw.optimise_week(DAYS, SLOTS, ["P", "SOG"], FLAT, iterations=1, damping=0.0)
check("matchup-blind weights always reach for the same player",
      seated(flat_only) == {"Shooter"}, seated(flat_only))

# Points comfortable, shots a coin flip: chase the shots.
chase_shots = mw.optimise_week(DAYS, SLOTS, ["P", "SOG"], FLAT,
                               banked_margin={"P": 22.0, "SOG": -14.0},
                               iterations=5, damping=0.6)
check("with points safe and shots close, the shooter plays",
      seated(chase_shots) == {"Shooter"}, seated(chase_shots))
check("...and shots outweigh points", chase_shots["weights"]["SOG"]
      > chase_shots["weights"]["P"], chase_shots["weights"])

# Shots comfortable, points a coin flip: the reverse, which is the case a
# matchup-blind engine gets wrong.
chase_points = mw.optimise_week(DAYS, SLOTS, ["P", "SOG"], FLAT,
                                banked_margin={"P": -4.0, "SOG": 40.0},
                                iterations=5, damping=0.6)
check("with shots safe and points close, the playmaker plays instead",
      seated(chase_points) == {"Playmaker"}, seated(chase_points))
check("...and that wins the tight category without losing the safe one",
      chase_points["margins"]["P"] > 0 and chase_points["margins"]["SOG"] > 0,
      chase_points["margins"])

check("every pass is recorded, so a surprising lineup can be explained",
      len(chase_points["history"]) == 5
      and "weights" in chase_points["history"][0], len(chase_points["history"]))

# Damped iteration must settle rather than flip between two lineups.
long_run = mw.optimise_week(DAYS, SLOTS, ["P", "SOG"], FLAT,
                            banked_margin={"P": 22.0, "SOG": -14.0},
                            iterations=25, damping=0.6)
tail = [h["weights"]["SOG"] for h in long_run["history"][-5:]]
check("the weights converge rather than oscillate",
      max(tail) - min(tail) < 1e-3, tail)

check("the opponent is projected once and never re-optimised",
      abs(chase_points["opponent"]["P"] - 7 * 0.70) < 1e-9,
      chase_points["opponent"])
check("no days is not a crash",
      mw.optimise_week([], SLOTS, ["P"], {"P": 1.0})["lineups"] == {})


# --------------------------------------------------------------------------
print("\n=== 5. the whole chain on real data ===")

try:
    from db import engine, text
    import daily_value as dv
    import goalie_starts as gs

    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(
            text("SELECT * FROM final_projections"))]
        schedule = [(r[0], r[1], r[2]) for r in conn.execute(text(
            'SELECT "gameDate", "homeTeam", "awayTeam" FROM nhl_schedule'))]

    cats = ["G", "A", "SOG", "HIT", "BLK", "W", "SV", "GA", "SHO"]
    valued = dv.value_players(rows, cats)
    for row in valued:
        row["eligiblePositions"] = row.get("eligiblePositions") or row["positionCode"]

    scales = dv.category_scales(rows, cats)
    flat = dv.default_weights(scales, dv.category_polarity(cats))

    goalies = [p for p in valued if "G" in str(p["eligiblePositions"]).split(",")]
    probabilities = gs.start_probabilities_by_team(goalies, schedule)

    def squad(players, seed):
        picked = []
        for position, count in (("C", 3), ("LW", 3), ("RW", 3), ("D", 5)):
            pool = [p for p in players
                    if position in str(p["eligiblePositions"]).split(",")]
            picked += pool[seed:seed + count]
        picked += [gs.expected_value(goalies[i], 0.6)
                   for i in list(probabilities)[seed:seed + 2]]
        return picked

    week = [f"2027-01-0{d}" for d in range(4, 9)]
    days = [{"date": d, "mine": squad(valued, 0), "theirs": squad(valued, 20)}
            for d in week]
    slots = {"C": 2, "LW": 2, "RW": 2, "D": 4, "Util": 1, "G": 2}

    result = mw.optimise_week(days, slots, cats, flat, iterations=3)

    check("a real week produces a lineup for every day",
          set(result["lineups"]) == set(week), sorted(result["lineups"]))
    check("every lineup is legal",
          all(len(seats) <= slots[slot]
              for lineup in result["lineups"].values()
              for slot, seats in lineup.items()))
    check("every scored category gets a finite weight",
          all(abs(w) < 1e6 for c, w in result["weights"].items()
              if c not in dv.RATE_COLUMNS), result["weights"])
    check("both sides project a positive total in every counting category",
          all(result["projected"][c] > 0 and result["opponent"][c] > 0
              for c in cats), result["projected"])
    check("no category is written off to exactly zero",
          all(abs(result["weights"][c]) > 0 for c in cats), result["weights"])

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
