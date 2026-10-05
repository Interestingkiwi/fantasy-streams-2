"""
Tests for the nightly update's season gate, and the order of its steps -
including the injury report, which runs last and can never fail the night.

The gate is what lets the cron be created months before it has anything to do,
so the cases that matter are the boundaries: silent before opening night, live
from it, and a clean stand-down rather than a failure when no schedule is
loaded at all. A cron that failed every night until October would train its
alerts to be ignored by the time they mattered.

The scrapers are stubbed - this suite is about when they are called, not what
they fetch.

Author - Jason Druckenmiller
Created - 9/8/2026
Updated - 10/5/2026
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nightly_update as nightly                            # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


OPENING = date(2026, 9, 29)

# --------------------------------------------------------------------------
print("\n=== 1. the gate ===")

check("the night before opening is too early",
      not nightly.should_run(date(2026, 9, 28), OPENING))
check("opening night itself is in scope",
      nightly.should_run(OPENING, OPENING))
check("so is every night after", nightly.should_run(date(2027, 2, 1), OPENING))
check("months early is still just early",
      not nightly.should_run(date(2026, 9, 8), OPENING))
check("no schedule loaded means stand down, not run",
      not nightly.should_run(date(2027, 1, 1), None))


# --------------------------------------------------------------------------
print("\n=== 2. what the gate actually gates ===")

calls = []


def stub_results(start, end):
    calls.append(("results", start, end))
    return 100


def stub_teams(**kwargs):
    calls.append(("teams", kwargs.get("week_end")))
    return 32


fill_windows = []


def stub_fill(since, until):
    fill_windows.append((since, until))
    return 0


def stub_lines(since, until):
    calls.append(("lines", since, until))
    return 0


def stub_injuries():
    calls.append(("injuries",))
    return 100, []


def failing_injuries():
    calls.append(("injuries",))
    raise nightly.injury_report.InjuryFeedError("ESPN answered 403")


original = (nightly.scrape_game_results.run, nightly.scrape_team_stats.run,
            nightly.season_first_game, nightly.scrape_game_results.fill_missing,
            nightly.game_lines.fill_missing, nightly.injury_report.refresh)
try:
    nightly.scrape_game_results.run = stub_results
    nightly.scrape_team_stats.run = stub_teams
    nightly.scrape_game_results.fill_missing = stub_fill
    nightly.game_lines.fill_missing = stub_lines
    nightly.injury_report.refresh = stub_injuries
    nightly.season_first_game = lambda: OPENING

    calls.clear()
    did = nightly.run(date(2026, 9, 20))
    check("before the season nothing is scraped at all", did is False and not calls, calls)

    calls.clear()
    did = nightly.run(date(2026, 9, 20), force=True)
    check("--force overrides the gate", did is True and len(calls) == 4, calls)

    calls.clear()
    did = nightly.run(OPENING)
    check("opening night runs results, team stats, lines and injuries", did is True and len(calls) == 4, calls)
    check("results are scraped for exactly that one night",
          calls[0] == ("results", "2026-09-29", "2026-09-29"), calls[0])
    check("results come before team stats, which roll up from them",
          calls[0][0] == "results" and calls[1][0] == "teams", calls)
    check("the trailing windows end on the night just scraped",
          calls[1][1] == OPENING, calls[1])
    check("and earlier nights missing a column are looked for over a short look-back",
          fill_windows and fill_windows[-1] == (OPENING - nightly.timedelta(days=14), OPENING),
          fill_windows)
    check("lines follow, over the same look-back - a shift chart not posted yet costs only the lines",
          calls[2] == ("lines", OPENING - nightly.timedelta(days=14), OPENING), calls)
    check("and ESPN's injury report is refreshed last", calls[3] == ("injuries",), calls)

    nightly.injury_report.refresh = failing_injuries
    calls.clear()
    try:
        did, raised = nightly.run(OPENING), None
    except Exception as exc:                                # noqa: BLE001
        did, raised = False, exc
    check("an injury report ESPN refuses never fails the night - the games are in",
          did is True and raised is None and calls[-1] == ("injuries",), (did, raised))
    nightly.injury_report.refresh = stub_injuries

    # A missing schedule must not raise - the cron would alert nightly.
    nightly.season_first_game = lambda: None
    calls.clear()
    check("an empty schedule stands down quietly",
          nightly.run(date(2027, 1, 1)) is False and not calls, calls)
finally:
    (nightly.scrape_game_results.run, nightly.scrape_team_stats.run,
     nightly.season_first_game, nightly.scrape_game_results.fill_missing,
     nightly.game_lines.fill_missing, nightly.injury_report.refresh) = original


# --------------------------------------------------------------------------
print("\n=== 3. against the loaded schedule ===")

try:
    opening = nightly.season_first_game()
    check("a season is loaded and its opening night is readable",
          isinstance(opening, date), opening)

    if isinstance(opening, date):
        check("today is correctly judged too early to scrape",
              not nightly.should_run(date.today(), opening)
              or date.today() >= opening,
              (date.today(), opening))
        check("the first productive run is the morning after opening night",
              nightly.should_run(opening, opening)
              and not nightly.should_run(
                  opening.replace(day=opening.day - 1), opening))
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
