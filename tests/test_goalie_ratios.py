"""
Tests for `goalie_ratios`: the Matchup tab's projected GAA and save
percentage, and the odds of winning each.

No database. The checks lean on the claims the module makes: the week so far
is read the way Goalie Planning reads it (minutes from GA and GAA, else from
the shots a week of shutouts faced), a seated goalie's line is per start once
his start odds are divided back out, a start that may not happen matters only
as far as it differs from the side's running ratio, banked minutes dilute
what is to come, and the spread the odds come from matches a simulation of
the same model.

Author - Jason Druckenmiller
Created - 10/7/2026
Updated - 10/7/2026
"""

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import goalie_planning as gp                                # noqa: E402
import goalie_ratios as gr                                  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def seated(p, ga, sa, sv=None, position="G"):
    """A goalie as the planner seats him: his per-start line times his start odds."""
    sv = sa - ga if sv is None else sv
    return {"positionCode": position, "startProbability": p,
            "perGame": {"GA": ga * p, "SA": sa * p, "SV": sv * p}}


# --------------------------------------------------------------------------
print("\n=== 1. the week so far ===")

week = gr.so_far({"GA": "5", "SA": "60", "SV": "55", "GAA": "2.50"})
check("minutes come from GA and GAA, exactly, strings and all",
      abs(week["minutes"] - 120.0) < 1e-9 and week["minutesFrom"] == "gaa", week)
shutouts = gr.so_far({"GA": 0, "SA": 31, "SV": 31, "GAA": 0})
check("a week of shutouts is measured by the shots faced, not left at no minutes",
      abs(shutouts["minutes"] - 31 * gp.MINUTES_PER_SHOT) < 1e-9 and shutouts["minutesFrom"] == "shots",
      shutouts)
check("nothing entered is nothing played",
      gr.so_far({}) == {"GA": 0.0, "SA": 0.0, "SV": 0.0, "minutes": 0.0, "minutesFrom": "none"}
      and gr.so_far("junk")["minutesFrom"] == "none")


# --------------------------------------------------------------------------
print("\n=== 2. the seats still to come ===")

lineup = {"G": [seated(0.5, 2.8, 30)], "C": [{"positionCode": "C", "perGame": {"G": 0.4}}]}
found = gr.seats([lineup])
check("only goalies are read, per start, with the odds he starts",
      len(found) == 1 and found[0][0] == 0.5
      and all(abs(found[0][1][c] - v) < 1e-9 for c, v in {"GA": 2.8, "SA": 30, "SV": 27.2}.items()),
      found)
check("a goalie with no chance of starting, or no goals against projected, is left out",
      gr.seats([{"G": [seated(0.0, 2.8, 30),
                       {"positionCode": "G", "startProbability": 0.7, "perGame": {"SA": 20, "SV": 18}}]}]) == [])


# --------------------------------------------------------------------------
print("\n=== 3. where a side finishes ===")

banked = {"GA": 5, "SA": 60, "SV": 55, "GAA": 2.5}
alone = gr.side(banked, [])
check("with nothing to come the ratios are the week so far, and settled",
      abs(alone["GAA"]["value"] - 2.5) < 1e-9 and abs(alone["SVpct"]["value"] - 55 / 60) < 1e-9
      and alone["GAA"]["variance"] == 0 and alone["SVpct"]["variance"] == 0, alone)

seats = [(0.9, 2.6, 30), (0.6, 3.2, 28)]
side = gr.side(banked, [{"G": [seated(*s) for s in seats]}])
goals = 5 + sum(p * ga for p, ga, _ in seats)
minutes = 120 + sum(p for p, _, _ in seats) * gp.AVERAGE_START_MINUTES
shots = 60 + sum(p * sa for p, _, sa in seats)
saves = 55 + sum(p * (sa - ga) for p, ga, sa in seats)
check("GAA is goals over minutes, the starts weighted by their odds at 58.6 minutes each",
      abs(side["GAA"]["value"] - 60 * goals / minutes) < 1e-9, side["GAA"])
check("save percentage is saves over shots, the same way",
      abs(side["SVpct"]["value"] - saves / shots) < 1e-9, side["SVpct"])
check("the week so far rides along, and the expected starts to come",
      abs(side["GAA"]["soFar"] - 2.5) < 1e-9 and abs(side["starts"] - 1.5) < 1e-9
      and side["minutesFrom"] == "gaa", side)
check("a side with no goaltending at all has nothing to divide, not a zero",
      gr.side({}, [])["GAA"]["value"] is None and gr.side({}, [])["SVpct"]["value"] is None)


# --------------------------------------------------------------------------
print("\n=== 4. how sure ===")

# Only the doubt over whether he starts: a start at exactly the side's running
# GAA cannot move it, however unlikely it is.
real = gr.GA_DISPERSION
try:
    gr.GA_DISPERSION = 0.0
    running = 2.5 / 60 * gp.AVERAGE_START_MINUTES
    at_par = gr.side(banked, [{"G": [seated(0.5, running, 30)]}])
    worse = gr.side(banked, [{"G": [seated(0.5, running + 1.5, 30)]}])
    check("a start that may not happen adds nothing when it would play to the side's GAA",
          at_par["GAA"]["variance"] < 1e-12, at_par["GAA"])
    check("and something when it would not",
          worse["GAA"]["variance"] > 1e-4, worse["GAA"])
finally:
    gr.GA_DISPERSION = real

light = gr.side({"GA": 2, "SA": 30, "SV": 28, "GAA": 2.0}, [{"G": [seated(0.9, 2.8, 30)]}])
heavy = gr.side({"GA": 10, "SA": 150, "SV": 140, "GAA": 2.0}, [{"G": [seated(0.9, 2.8, 30)]}])
check("the more already banked, the less one more start can move either ratio",
      heavy["GAA"]["variance"] < light["GAA"]["variance"] / 4
      and heavy["SVpct"]["variance"] < light["SVpct"]["variance"] / 4,
      (light["GAA"]["variance"], heavy["GAA"]["variance"]))


def simulated(banked, seats, runs=15000, seed=3):
    """The same model played out: each start happens or not, Poisson goals, binomial saves."""
    rng = random.Random(seed)
    start = gr.so_far(banked)
    gaas, saves_pct = [], []

    def poisson(mean):
        limit, k, product = math.exp(-mean), 0, 1.0
        while True:
            product *= rng.random()
            if product < limit:
                return k
            k += 1

    for _ in range(runs):
        g, m, a, s = start["GA"], start["minutes"], start["SA"], start["SV"]
        for p, ga, sa in seats:
            if rng.random() < p:
                g += poisson(ga)
                m += gp.AVERAGE_START_MINUTES
                a += sa
                s += sum(1 for _ in range(sa) if rng.random() < (sa - ga) / sa)
        if m > 0:                    # a fresh week where nobody started has no ratio
            gaas.append(60 * g / m)
            saves_pct.append(s / a)

    def sd(values):
        mean = sum(values) / len(values)
        return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    return sd(gaas), sd(saves_pct)


real = (gr.GA_DISPERSION, gr.SV_DISPERSION)
try:
    gr.GA_DISPERSION = gr.SV_DISPERSION = 1.0       # the simulation is pure Poisson and binomial
    for label, banked_week, seats in [
            ("two banked starts and three in doubt", banked, [(0.9, 2.6, 30), (0.6, 3.2, 28), (0.3, 2.9, 31)]),
            ("a fresh week of likely starts", {}, [(0.9, 2.6, 30), (0.6, 3.2, 28), (0.95, 2.9, 31)])]:
        side = gr.side(banked_week, [{"G": [seated(p, ga, sa) for p, ga, sa in seats]}])
        gaa_sd, save_sd = simulated(banked_week, seats)
        check(f"{label}: the spread matches a simulation of the model to 10%",
              abs(math.sqrt(side["GAA"]["variance"]) / gaa_sd - 1) < 0.10
              and abs(math.sqrt(side["SVpct"]["variance"]) / save_sd - 1) < 0.10,
              (math.sqrt(side["GAA"]["variance"]), gaa_sd,
               math.sqrt(side["SVpct"]["variance"]), save_sd))
finally:
    gr.GA_DISPERSION, gr.SV_DISPERSION = real


# --------------------------------------------------------------------------
print("\n=== 5. the odds ===")

low = {"value": 2.40, "variance": 0.04}
high = {"value": 2.90, "variance": 0.05}
won, contested = gr.odds(low, high, -1)
check("a lower GAA is the better one", won > 0.9 and 0 < contested < 1, (won, contested))
check("and the same margin in save percentage terms runs the other way",
      gr.odds(low, high, 1)[0] < 0.1)
check("level is a coin flip", gr.odds(low, dict(low), -1) == (0.5, 1.0))
check("with nothing left to play, a lead is decided",
      gr.odds({"value": 0.912, "variance": 0}, {"value": 0.911, "variance": 0}, 1)[0] > 0.999)
check("a side with no goaltending gets no odds rather than a guess",
      gr.odds({"value": None, "variance": None}, high, -1) is None and gr.odds(None, high, -1) is None)


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
