"""
Tests for `goalie_minimum`: each side's chance of reaching the league's goalie
minimum, and the forfeit when it does not.

No database. Pinned: appearances so far are Yahoo's when given and otherwise
estimated (and said to be), two goalies from one NHL team on one night are a
single chance at an appearance, the chance is exact against brute force, and
the forfeit follows Yahoo's rule - short of the minimum, the opponent takes
every goalie category, and both short is a tie.

Author - Jason Druckenmiller
Created - 10/7/2026
Updated - 10/7/2026
"""

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import goalie_minimum as gm                                 # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def goalie(team, p):
    return {"positionCode": "G", "teamAbbrevs": team, "startProbability": p, "perGame": {}}


# --------------------------------------------------------------------------
print("\n=== 1. appearances so far ===")

check("Yahoo's count is taken as given, strings and all",
      gm.appearances_so_far({"GP": "2", "GA": 9, "GAA": 1.0}) == (2, "yahoo"))
check("without it, estimated from the minutes - and said to be",
      gm.appearances_so_far({"GA": 6, "GAA": 3.0}) == (2, "estimated"))
check("nothing played is none, not a guess",
      gm.appearances_so_far({}) == (0, "none") and gm.appearances_so_far(None) == (0, "none")
      and gm.appearances_so_far({"GP": ""}) == (0, "none"))


# --------------------------------------------------------------------------
print("\n=== 2. appearances to come ===")

lineups = {
    "2027-02-01": {"G": [goalie("TOR", 0.6), goalie("TOR", 0.4)],
                   "C": [{"positionCode": "C", "teamAbbrevs": "TOR"}]},
    "2027-02-02": {"G": [goalie("TOR", 0.7), goalie("MTL", 0.9)]},
    "2027-02-03": {"G": [goalie("SEA", 0.0)]},
}
found = sorted(round(p, 6) for p in gm.chances(lineups))
check("a tandem seated on one night is one chance, since only one can start",
      found == [0.7, 0.9, 1.0], found)
check("skaters and goalies with no chance of starting are no chance at all",
      len(gm.chances({"d": {"G": [goalie("SEA", 0)], "C": [{"positionCode": "C"}]}})) == 0)
check("a team's odds that run over one are capped at a certain appearance",
      gm.chances({"d": {"G": [goalie("TOR", 0.7), goalie("TOR", 0.6)]}}) == [1.0])


def brute(so_far, upcoming, minimum):
    total = 0.0
    for outcome in itertools.product([0, 1], repeat=len(upcoming)):
        weight = 1.0
        for hit, p in zip(outcome, upcoming):
            weight *= p if hit else 1 - p
        if so_far + sum(outcome) >= minimum:
            total += weight
    return total


upcoming = [0.9, 0.55, 0.3, 0.8, 0.15]
check("the chance is exact: every way the starts can fall, counted",
      all(abs(gm.reach_chance(s, upcoming, 3) - brute(s, upcoming, 3)) < 1e-12 for s in range(0, 4)),
      [(gm.reach_chance(s, upcoming, 3), brute(s, upcoming, 3)) for s in range(0, 4)])
check("already there is certain, out of reach is impossible",
      gm.reach_chance(3, [], 3) == 1.0 and gm.reach_chance(0, [0.9, 0.9], 3) == 0.0)

side = gm.side({"GP": 1}, lineups, 3)
check("a side is its appearances so far, those to come, and its chance",
      side["soFar"] == 1 and side["soFarFrom"] == "yahoo" and abs(side["toCome"] - 2.6) < 1e-9
      and abs(side["chance"] - brute(1, [1.0, 0.7, 0.9], 3)) < 1e-12, side)


# --------------------------------------------------------------------------
print("\n=== 3. the forfeit ===")

check("both through: the category is decided by the goalies", gm.category_odds(0.3, 1.0, 1.0) == 0.3)
check("only the opponent short: you win it whatever your goalies did", gm.category_odds(0.3, 1.0, 0.0) == 1.0)
check("only you short: you lose it whatever your goalies did", gm.category_odds(0.9, 0.0, 1.0) == 0.0)
check("both short: neither can win it, a tie", gm.category_odds(0.9, 0.0, 0.0) == 0.5)
mixed = gm.category_odds(0.6, 0.8, 0.5)
check("in between, every way it can fall, weighted",
      abs(mixed - (0.8 * 0.5 * 0.6 + 0.8 * 0.5 + 0.5 * 0.2 * 0.5)) < 1e-12, mixed)
check("only goalie categories forfeit",
      gm.is_goalie_category("GAA") and gm.is_goalie_category("W") and not gm.is_goalie_category("SOG"))

check("a minimum from the page: a whole number, 0 for none, absent for not sent",
      gm.minimum_from("3") == 3 and gm.minimum_from(0) == 0 and gm.minimum_from(None) is None
      and gm.minimum_from("x") is None and gm.minimum_from(-2) == 0)


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
