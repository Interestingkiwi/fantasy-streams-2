"""
Tests for `goalie_planning`: what one more start risks.

The rules worth pinning are the ones a reader would otherwise assume wrong.
Minutes are never a flat hour: they are recovered exactly from GA and GAA
where those exist, and otherwise come from the measured curve, which is why a
pulled goalie costs GAA out of all proportion to what he costs save percentage
- he is worse on both, but the minutes halve while the shots do not. A limit is
the most goals that still keeps a category, so it has to move with the
opponent rather than sit at some fixed "bad game".

Author - Jason Druckenmiller
Created - 9/22/2026
Updated - 9/22/2026
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "goalie-planning-test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import goalie_planning as gp                                # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


# --------------------------------------------------------------------------
print("\n=== 1. minutes ===")

minutes, source = gp.minutes_played(goals_against=5, gaa=2.5)
check("minutes come back exactly from GA and GAA, pulls and overtime included",
      abs(minutes - 120.0) < 1e-9 and source == 'gaa', (minutes, source))
minutes, source = gp.minutes_played(goals_against=0, gaa=0, starts=3)
check("with nothing conceded they fall back to starts at the measured average",
      abs(minutes - 3 * gp.AVERAGE_START_MINUTES) < 1e-9 and source == 'starts', (minutes, source))
check("and a start is not assumed to be sixty minutes",
      gp.AVERAGE_START_MINUTES < 60.0 and gp.projected_minutes(2) < 120.0)
check("nothing played at all is nothing, not an hour",
      gp.minutes_played(0, 0) == (0.0, 'none'))
check("minutes move with how the night went, from the measured curve",
      gp.start_minutes(1) > gp.start_minutes(5) and gp.start_minutes(0) < gp.start_minutes(2),
      {g: gp.start_minutes(g) for g in range(6)})


# --------------------------------------------------------------------------
print("\n=== 2. outcomes ===")

rows = gp.outcomes(shots=30, save_pct=0.9)
spread = [r for r in rows if not r['variant']]
check("the distribution covers a shutout through the tail and sums to one",
      spread[0]['goals'] == 0 and abs(sum(r['probability'] for r in spread) - 1.0) < 1e-6,
      sum(r['probability'] for r in spread))
check("three goals on thirty shots at .900 is the most likely single outcome",
      max(spread, key=lambda r: r['probability'])['goals'] == 3,
      [(r['goals'], r['probability']) for r in spread])
check("a shot-heavy opponent makes the same goalie riskier",
      gp.outcomes(40, 0.9)[0]['probability'] < gp.outcomes(20, 0.9)[0]['probability'])
pull = [r for r in rows if r['variant']][0]
check("the pull is a variant beside the distribution, not one more outcome in it",
      pull['pulled'] and pull['minutes'] == gp.PULL_MINUTES
      and pull['probability'] == gp.PULL_SHARE, pull)

# 4 goals in 30 minutes against 4 in a full game: the asymmetry the page exists for
base = {'GA': 6.0, 'SA': 150.0, 'SV': 138.0}
full = [r for r in spread if r['goals'] == 4][0]
after_full, minutes_full = gp.apply_start(base, 300.0, full)
after_pull, minutes_pull = gp.apply_start(base, 300.0, pull)
rates_full = gp.rates(after_full, minutes_full)
rates_pull = gp.rates(after_pull, minutes_pull)
check("four goals in a pulled half-game cost more GAA than four in a full one",
      rates_pull['GAA'] > rates_full['GAA'], (rates_pull['GAA'], rates_full['GAA']))
# A pull is worse on both - the same goals with fewer saves under them - but
# the GAA damage is out of all proportion, because the minutes it is divided
# by halve while the shots only fall by a third. That is the asymmetry.
gaa_damage = (rates_pull['GAA'] - rates_full['GAA']) / rates_full['GAA']
save_damage = (rates_full['SVpct'] - rates_pull['SVpct']) / rates_full['SVpct']
check("and hurts GAA out of all proportion to what it does to save percentage",
      gaa_damage > 5 * save_damage, (gaa_damage, save_damage))


# --------------------------------------------------------------------------
print("\n=== 3. how bad a game can be ===")

totals = {'GA': 6.0, 'SA': 150.0, 'SV': 144.0}
minutes = 300.0
easy = gp.worst_start(totals, minutes, shots=30, target={'GAA': 3.5, 'SVpct': 0.900})
hard = gp.worst_start(totals, minutes, shots=30, target={'GAA': 1.5, 'SVpct': 0.975})
check("a soft opponent allows more goals than a hard one",
      easy['GAA']['maxGoals'] > hard['GAA']['maxGoals'], (easy['GAA'], hard['GAA']))
check("a category already lost says so rather than naming a number",
      hard['SVpct']['maxGoals'] is None and 'reason' in hard['SVpct'], hard['SVpct'])
check("a limit carries the save percentage it implies",
      abs(easy['GAA']['savePct'] - (30 - easy['GAA']['maxGoals']) / 30) < 1e-3, easy['GAA'])
check("no opponent number yet is said, not guessed",
      gp.worst_start(totals, minutes, 30, {})['GAA']['reason'] == 'no opponent GAA yet')

check("the odds of staying under a limit rise with the limit",
      gp.chance_of_at_most(30, 0.9, 4) > gp.chance_of_at_most(30, 0.9, 1) > 0)
check("and a better goalie is likelier to stay under the same one",
      gp.chance_of_at_most(30, 0.93, 2) > gp.chance_of_at_most(30, 0.88, 2))
check("no limit at all is no chance at all, not a certainty",
      gp.chance_of_at_most(30, 0.9, None) == 0.0)


# --------------------------------------------------------------------------
print("\n=== 4. totals ===")

check("rates are worked out, never summed",
      gp.add({'GA': 2, 'GAA': 2.0}, {'GA': 3, 'GAA': 9.9}) == {'GA': 5.0})
check("a week with no shots has no save percentage rather than zero",
      gp.rates({'SA': 0, 'SV': 0, 'GA': 0}, 0)['SVpct'] is None)
check("GAA is per sixty minutes of the minutes actually played",
      abs(gp.rates({'GA': 4}, 120.0)['GAA'] - 2.0) < 1e-9)


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
