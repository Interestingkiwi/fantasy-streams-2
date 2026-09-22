"""
Tests for `player_form`: PP share, trends and home/road splits from game rows.

Synthetic games, so each rule is pinned exactly: a trend needs the games and
the evidence (a standard-error test, not a percentage), recent PP share never
reaches back past the recent games for data they lack, a goalie is judged on
his starts, and a venue preference needs games at both.

Author - Jason Druckenmiller
Created - 9/21/2026
Updated - 9/21/2026
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "player-form-test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import player_form as pf                                    # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def game(goals, home=True, pp=None, team_pp=None, position="C", started=None):
    return {"goals": goals, "shots": 0, "homeRoad": "H" if home else "R",
            "positionCode": position, "gamesStarted": started,
            "ppTimeOnIce": pp, "teamPpTimeOnIce": team_pp}


WEIGHTS = {"G": 1.0}

# --------------------------------------------------------------------------
print("\n=== 1. trends ===")

steady = [0.0, 1.0] * 20
check("a player doing what he always does is flat",
      pf.trend([pf.game_value(game(g), WEIGHTS) for g in steady], 10) == "flat")
hot = [0.0, 1.0] * 15 + [2.0] * 10
check("ten games well above his season is up",
      pf.trend([pf.game_value(game(g), WEIGHTS) for g in hot], 10) == "up")
cold = [1.0, 2.0] * 15 + [0.0] * 10
check("ten well below is down",
      pf.trend([pf.game_value(game(g), WEIGHTS) for g in cold], 10) == "down")
check("fewer games than the window is no trend at all, not flat",
      pf.trend([1.0] * 8, 10) is None)
noisy = [0.0, 3.0] * 20 + [3.0, 0.0, 3.0, 3.0, 0.0]
check("a streak inside a noisy player's normal spread is not a trend",
      pf.trend(noisy, 5) == "flat", pf.trend(noisy, 5))
check("a stat the league scores negatively counts against him",
      pf.game_value({"goalsAgainst": 3}, {"GA": -1.0}) == -3.0)
check("a rate category is never summed per game",
      pf.game_value({"goals": 1}, {"G": 1.0, "GAA": -5.0}) == 1.0)


# --------------------------------------------------------------------------
print("\n=== 2. power-play share ===")

games = [game(0, pp=60, team_pp=120) for _ in range(10)] + [game(0, pp=90, team_pp=100)]
share = pf.pp_share(games)
check("recent share is his PP seconds over his team's, across his last five",
      abs(share["recent"] - (60 * 4 + 90) / (120 * 4 + 100)) < 1e-3 and share["last"] == 0.9, share)

gap = [game(0, pp=100, team_pp=100) for _ in range(10)] + [game(0) for _ in range(5)]
check("recent games without the data give no share - older ones never stand in",
      pf.pp_share(gap) is None, pf.pp_share(gap))
check("a team with no power play in the window gives no share rather than zero",
      pf.pp_share([game(0, pp=0, team_pp=0)])["recent"] is None)


# --------------------------------------------------------------------------
print("\n=== 3. venue and goalies ===")

venue = [game(2, home=True) for _ in range(5)] + [game(0, home=False) for _ in range(5)]
check("better at home says H", pf.form(venue, WEIGHTS)["venue"]["better"] == "H")
thin = [game(2, home=True) for _ in range(5)] + [game(0, home=False) for _ in range(4)]
check("without enough games at both venues there is no preference",
      pf.form(thin, WEIGHTS)["venue"]["better"] is None)

goalie = ([game(0, position="G", started=1) for _ in range(6)]
          + [game(0, position="G", started=0) for _ in range(3)])
form = pf.form(goalie, {"W": 1.0})
check("a goalie is judged on his starts only, and has no PP share",
      form["games"] == 6 and form["pp"] is None, form)


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
