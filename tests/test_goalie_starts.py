"""
Tests for goalie start probabilities.

Two invariants carry the suite: exactly one start per team game, and a
goalie's season total staying near what he was projected for. They pull
against each other, so the tests pin which one wins where - the nightly
constraint is exact, the season one is approximate, and a team with more
projected starts than games is the case where the season totals must give.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import goalie_starts as gs                                  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def goalie(name, starts, team="TOR"):
    return {"fullName": name, "teamAbbrevs": team, "proj_gamesStarted": starts}


# Mon, Tue (back-to-back), then Thu, Fri (back-to-back), then Sun.
DATES = ["2027-01-04", "2027-01-05", "2027-01-07", "2027-01-08", "2027-01-10"]

# --------------------------------------------------------------------------
print("\n=== 1. back-to-backs ===")

check("the second night of each pair is found",
      gs.second_nights(DATES) == {_d for _d in
                                  [__import__("datetime").date(2027, 1, 5),
                                   __import__("datetime").date(2027, 1, 8)]},
      gs.second_nights(DATES))
check("an isolated game is not a second night",
      gs.second_nights(["2027-01-04"]) == set())
check("three in three days gives two second nights",
      len(gs.second_nights(["2027-01-04", "2027-01-05", "2027-01-06"])) == 2)
check("unparseable dates are ignored, not fatal",
      gs.second_nights(["not-a-date", "2027-01-04"]) == set())


# --------------------------------------------------------------------------
print("\n=== 2. one start per game ===")

tandem = [goalie("Starter", 3.5), goalie("Backup", 1.5)]
rows = gs.start_probabilities(tandem, DATES)

nightly = [sum(row[date] for row in rows) for date in sorted(rows[0])]
check("every night has exactly one start between them",
      all(abs(total - 1.0) < 1e-9 for total in nightly), nightly)
check("no probability exceeds one", all(p <= 1.0 + 1e-9 for row in rows for p in row.values()))
check("no probability is negative", all(p >= 0.0 for row in rows for p in row.values()))
check("season totals land on the projections",
      abs(sum(rows[0].values()) - 3.5) < 0.01 and abs(sum(rows[1].values()) - 1.5) < 0.01,
      [round(sum(r.values()), 3) for r in rows])


# --------------------------------------------------------------------------
print("\n=== 3. back-to-backs move the tandem ===")

starter, backup = rows
seconds = gs.second_nights(DATES)
starter_normal = [p for d, p in starter.items() if d not in seconds]
starter_second = [p for d, p in starter.items() if d in seconds]

check("the starter is likelier on a normal night than a second night",
      min(starter_normal) > max(starter_second),
      (starter_normal, starter_second))
check("the backup picks up exactly what the starter gives back",
      all(abs(starter[d] + backup[d] - 1.0) < 1e-9 for d in starter))
check("the starter still starts some second nights",
      all(0.2 < p < 0.65 for p in starter_second), starter_second)

flat = gs.start_probabilities(tandem, ["2027-01-04", "2027-01-07", "2027-01-10"])
check("with no back-to-backs every night is the same",
      len({round(p, 9) for p in flat[0].values()}) == 1, flat[0])


# --------------------------------------------------------------------------
print("\n=== 4. teams that do not add up ===")

# More projected starts than games: real competition, so everyone scales down.
crowded = [goalie("A", 50), goalie("B", 40), goalie("C", 30)]
crowded_rows = gs.start_probabilities(crowded, DATES)
check("an over-projected team scales its goalies down",
      abs(sum(sum(r.values()) for r in crowded_rows) - len(DATES)) < 1e-9,
      sum(sum(r.values()) for r in crowded_rows))
check("...proportionally", abs(sum(crowded_rows[0].values()) / sum(crowded_rows[2].values())
                               - 50 / 30) < 0.05)

# Fewer projected starts than games - PIT, whose second goalie was pruned.
# Scaling up would hand the survivor every game; the residual absorbs it.
lone = gs.start_probabilities([goalie("Only one", 2.0)], DATES)
check("a lone goalie keeps his projected total instead of starting everything",
      abs(sum(lone[0].values()) - 2.0) < 1e-9, sum(lone[0].values()))
check("...so his nightly probability stays under one",
      max(lone[0].values()) < 1.0, max(lone[0].values()))

check("a team projected for no starts shares the work evenly",
      all(abs(p - 0.5) < 1e-9
          for row in gs.start_probabilities([goalie("X", 0), goalie("Y", 0)], DATES)
          for p in row.values()))
# The tilt encodes "the number one rests"; with no number one it must not fire,
# or list order silently becomes a depth chart.
level = gs.start_probabilities([goalie("X", 3), goalie("Y", 3)], DATES)
check("a level tandem gets no invented back-to-back hierarchy",
      all(abs(p - 0.5) < 1e-9 for row in level for p in row.values()),
      [{str(k): round(v, 3) for k, v in r.items()} for r in level])
check("no games is not a crash", gs.start_probabilities(tandem, []) == [{}, {}])
check("no goalies is not a crash", gs.start_probabilities([], DATES) == [])


# --------------------------------------------------------------------------
print("\n=== 5. scaling a value by the probability ===")

player = {"fullName": "G", "value": 10.0,
          "perGame": {"W": 0.6, "SV": 25.0, "SVpct": 0.909, "GAA": 2.5}}
scaled = gs.expected_value(player, 0.5)
check("the scalar scales", scaled["value"] == 5.0, scaled["value"])
check("counting categories scale",
      scaled["perGame"]["W"] == 0.3 and scaled["perGame"]["SV"] == 12.5, scaled["perGame"])
check("rate categories do not",
      scaled["perGame"]["SVpct"] == 0.909 and scaled["perGame"]["GAA"] == 2.5,
      scaled["perGame"])
check("the probability is recorded on the row", scaled["startProbability"] == 0.5)
check("the input is not modified", player["value"] == 10.0)


# --------------------------------------------------------------------------
print("\n=== 6. the real schedule and real projections ===")

try:
    from db import engine, text

    with engine.connect() as conn:
        goalies = [dict(r._mapping) for r in conn.execute(text(
            'SELECT "fullName", "teamAbbrevs", "proj_gamesStarted" '
            'FROM final_projections WHERE "positionCode" = :g'), {"g": "G"})]
        schedule = [(r[0], r[1], r[2]) for r in conn.execute(text(
            'SELECT "gameDate", "homeTeam", "awayTeam" FROM nhl_schedule'))]

    check("goalies and schedule are loaded", len(goalies) > 50 and len(schedule) > 1000,
          (len(goalies), len(schedule)))

    probabilities = gs.start_probabilities_by_team(goalies, schedule)

    per_team_night = defaultdict(lambda: defaultdict(float))
    for index, row in probabilities.items():
        team = gs.primary_team(goalies[index].get("teamAbbrevs"))
        for game_date, probability in row.items():
            per_team_night[team][game_date] += probability

    over = [(t, d, s) for t, nights in per_team_night.items()
            for d, s in nights.items() if s > 1.0 + 1e-9]
    check("no team-night is ever over one start", not over, over[:3])

    total = sum(sum(row.values()) for row in probabilities.values())
    check("expected starts never exceed the games actually played",
          total <= 32 * 84 + 1e-6, total)
    check("...and the shortfall is the unprojected goalies, not a rounding bug",
          2300 < total < 2688, total)

    peak = max(p for row in probabilities.values() for p in row.values())
    check("nobody is projected to start every night", peak < 0.95, peak)

    # PIT is the case that motivated the residual: one projected goalie, 84 games.
    for index, row in probabilities.items():
        if gs.primary_team(goalies[index].get("teamAbbrevs")) == "PIT" and row:
            projected = float(goalies[index]["proj_gamesStarted"] or 0)
            check(f"PIT's {goalies[index]['fullName']} keeps his projected total",
                  abs(sum(row.values()) - projected) < 0.5,
                  (sum(row.values()), projected))
            break

    check("every goalie has a row, even one whose team never plays",
          len(probabilities) == len(goalies))

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
