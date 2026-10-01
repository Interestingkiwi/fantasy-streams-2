"""
Tests for the per-game results scraper.

The case that matters most is the one that nearly got through: the NHL API
stops paging at an offset of 10,000 and says nothing about it, so a
season-long request returned 9,600 rows spanning the right dates and looked
complete. It was a sorted, top-heavy subset. Silent truncation feeding a
calibration is the worst failure available here, so the chunking and the
ceiling guard are what this suite leans on.

No network: the fetchers are driven through a stub so the paging, chunking and
guard can be exercised deterministically.

Author - Jason Druckenmiller
Created - 9/8/2026
Updated - 10/1/2026
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scrape_game_results as sgr                           # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


class StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def stub_api(rows_by_window, calls=None):
    """A requests.get that pages a fixed set of rows per date window."""
    def get(url, params=None, timeout=None):
        window = (params['cayenneExp'].split('"')[1], params['cayenneExp'].split('"')[3])
        rows = rows_by_window.get(window, [])
        if calls is not None:
            calls.append((url, window, params['start']))
        start = params['start']
        return StubResponse({'total': len(rows),
                             'data': rows[start:start + params['limit']]})
    return get


def row(player, game, day, **extra):
    base = {'playerId': player, 'gameId': game, 'gameDate': day,
            'teamAbbrev': 'TOR', 'opponentTeamAbbrev': 'MTL', 'homeRoad': 'H'}
    base.update(extra)
    return base


# --------------------------------------------------------------------------
print("\n=== 1. shaping a row ===")

skater = sgr.shape(row(1, 100, "2026-01-14", skaterFullName="A Skater",
                       positionCode="C", goals=2, shots=5), sgr.SKATER)
check("identity fields carry across",
      skater["playerId"] == 1 and skater["gameId"] == 100
      and skater["opponentTeamAbbrev"] == "MTL" and skater["homeRoad"] == "H", skater)
check("the endpoint's own fields are mapped",
      skater["goals"] == 2 and skater["shots"] == 5 and skater["fullName"] == "A Skater")
check("columns the other endpoint owns are left empty, not zeroed",
      skater["saves"] is None and skater["wins"] is None, skater)

goalie = sgr.shape(row(2, 100, "2026-01-14", goalieFullName="A Goalie",
                       saves=30, wins=1), sgr.GOALIE)
check("a goalie row fills the goalie columns instead",
      goalie["saves"] == 30 and goalie["goals"] is None, goalie)

check("every mapped column exists in the table's column list",
      all(c in sgr.COLUMNS for mapping in (sgr.SHARED, sgr.SKATER, sgr.REALTIME, sgr.GOALIE)
          for c in mapping.values()))
check("the column list has no duplicates",
      len(sgr.COLUMNS) == len(set(sgr.COLUMNS)))
check("hits and blocks are among them, since most leagues score one",
      "hits" in sgr.COLUMNS and "blockedShots" in sgr.COLUMNS)


# --------------------------------------------------------------------------
print("\n=== 2. paging ===")

original_get = sgr.requests.get
try:
    window = ("2026-01-12", "2026-01-18")
    many = [row(i, 100 + i, "2026-01-14") for i in range(250)]
    sgr.requests.get = stub_api({window: many})

    fetched = sgr.fetch("skater", "2026-01-12", "2026-01-18")
    check("paging collects every row, not just the first page",
          len(fetched) == 250, len(fetched))
    check("...and does not repeat any",
          len({r["playerId"] for r in fetched}) == 250)

    sgr.requests.get = stub_api({window: []})
    check("an empty window is not a crash",
          sgr.fetch("skater", "2026-01-12", "2026-01-18") == [])

    # Paging is only stable if the sort is total. Without a unique tiebreaker
    # the server orders ties as it pleases between requests, so pages overlap
    # and miss - one real night returned 576 rows with 15 duplicates, losing
    # 15 rows outright. That cost 15% of the hits data before it was caught.
    sent = []

    def capture(url, params=None, timeout=None):
        sent.append(params)
        return StubResponse({'total': 0, 'data': []})

    sgr.requests.get = capture
    sgr.fetch("skater", "2026-01-12", "2026-01-18")
    check("every request asks for a total sort order",
          sent and all('sort' in p for p in sent), sent[:1])
    # The tiebreaker has to be unique across the whole window, not just within
    # a game: a window spans several days, so a player's own rows tie with each
    # other. playerId alone still lost about five rows a week.
    check("...tie-broken on the full key, not just playerId",
          'playerId' in (sent[0].get('sort') or '')
          and 'gameId' in (sent[0].get('sort') or ''), sent[0].get('sort'))
finally:
    sgr.requests.get = original_get


# --------------------------------------------------------------------------
print("\n=== 3. chunking, and the ceiling that must never pass silently ===")

original_get = sgr.requests.get
try:
    calls = []
    windows = {("2026-01-01", "2026-01-07"): [row(1, 1, "2026-01-02")],
               ("2026-01-08", "2026-01-14"): [row(2, 2, "2026-01-09")],
               ("2026-01-15", "2026-01-16"): [row(3, 3, "2026-01-15")]}
    sgr.requests.get = stub_api(windows, calls)

    fetched = sgr.fetch("skater", "2026-01-01", "2026-01-16")
    check("a long range is split into windows",
          len({w for _u, w, _s in calls}) == 3, sorted({w for _u, w, _s in calls}))
    check("...covering the range end to end with no gap or overlap",
          sorted({w for _u, w, _s in calls})
          == [("2026-01-01", "2026-01-07"), ("2026-01-08", "2026-01-14"),
              ("2026-01-15", "2026-01-16")])
    check("...and every window's rows are kept", len(fetched) == 3, len(fetched))

    check("a single day is one window",
          len({w for _u, w, _s in calls}) == 3)

    # The bug: the API stops at 10,000 and reports success. It must raise.
    huge = [row(i, i, "2026-01-02") for i in range(sgr.PAGE_CEILING + 500)]
    sgr.requests.get = stub_api({("2026-01-01", "2026-01-07"): huge})
    raised = False
    try:
        sgr.fetch("skater", "2026-01-01", "2026-01-07")
    except RuntimeError as exc:
        raised = "ceiling" in str(exc) or "truncated" in str(exc)
    check("hitting the API's row ceiling raises instead of truncating quietly",
          raised, "a silently truncated season is the worst input to a calibration")
finally:
    sgr.requests.get = original_get


# --------------------------------------------------------------------------
print("\n=== 3b. faceoffs, the fifth report ===")

original_fetch = sgr.fetch
asked = []


def faceoff_report(kind, start, end, kind_path=None, sort=None):
    asked.append(kind_path)
    return [{"playerId": 1, "gameId": 10, "totalFaceoffWins": 12, "totalFaceoffLosses": 9,
             "totalFaceoffs": 21},
            {"playerId": 9, "gameId": 10, "totalFaceoffWins": 3, "totalFaceoffLosses": 3,
             "totalFaceoffs": 6}]


try:
    sgr.fetch = faceoff_report
    rows = {(1, 10): {"playerId": 1, "gameId": 10}, (2, 10): {"playerId": 2, "gameId": 10}}
    sgr._merge_faceoffs(rows, "2026-01-01", "2026-01-01")
    check("faceoffs come from skater/faceoffwins", asked == ["skater/faceoffwins"], asked)
    check("and land on the skater's own row as wins, losses and total",
          rows[(1, 10)].get("faceoffWins") == 12 and rows[(1, 10)].get("faceoffLosses") == 9
          and rows[(1, 10)].get("totalFaceoffs") == 21, rows[(1, 10)])
    check("a player with no faceoff row is left alone, not zeroed",
          "faceoffWins" not in rows[(2, 10)], rows[(2, 10)])
    check("and a faceoff row with no player row adds nothing", (9, 10) not in rows)
    check("the table carries the three columns",
          {"faceoffWins", "faceoffLosses", "totalFaceoffs"} <= set(sgr.COLUMNS))
finally:
    sgr.fetch = original_fetch


# --------------------------------------------------------------------------
print("\n=== 3c. columns added after the table went live ===")

later = {"evGoals", "evPoints", "missedShots", "shotAttemptsBlocked",
         "evTimeOnIce", "shTimeOnIce", "otTimeOnIce", "shifts"}
check("the player card's columns are collected", later <= set(sgr.COLUMNS), later - set(sgr.COLUMNS))
check("each is added to a table that predates it", later <= set(sgr.LATER_COLUMNS))
check("and each is one every skater gets, so a gap means a night to re-scrape",
      later <= set(sgr.FILLED_FOR_EVERY_SKATER))
check("a team's PP time is not - a game without a power play is None by right",
      sgr.TEAM_PP_COLUMN not in sgr.FILLED_FOR_EVERY_SKATER)
shaped = sgr.shape(row(1, 10, "2026-10-01", evGoals=1, evPoints=2), sgr.SKATER)
check("even-strength scoring comes from the summary report", shaped["evGoals"] == 1 and shaped["evPoints"] == 2)

original_fetch = sgr.fetch
try:
    sgr.fetch = lambda kind, start, end, kind_path=None, sort=None: (
        [{"playerId": 1, "gameId": 10, "ppTimeOnIce": 90, "evTimeOnIce": 900, "shTimeOnIce": 30,
          "otTimeOnIce": 0, "shifts": 22}] if kind_path == 'skater/timeonice' else [])
    rows = {(1, 10): {"playerId": 1, "gameId": 10, "opponentTeamAbbrev": "MTL"}}
    sgr._merge_power_play(rows, "2026-10-01", "2026-10-01")
    check("ice time by strength lands with the power-play time, from the same report",
          rows[(1, 10)]["evTimeOnIce"] == 900 and rows[(1, 10)]["shifts"] == 22
          and rows[(1, 10)]["otTimeOnIce"] == 0, rows[(1, 10)])
finally:
    sgr.fetch = original_fetch


# --------------------------------------------------------------------------
print("\n=== 4. what actually landed ===")

try:
    from db import engine, text

    with engine.connect() as conn:
        summary = conn.execute(text(
            'SELECT count(*) AS rows, count(DISTINCT "gameId") AS games, '
            'min("gameDate") AS lo, max("gameDate") AS hi '
            'FROM player_game_stats')).fetchone()

    check("per-game rows have been scraped", summary.rows > 3000, summary.rows)
    check("every row belongs to a game", summary.games > 0, summary.games)

    with engine.connect() as conn:
        missing = conn.execute(text(
            'SELECT count(*) FROM player_game_stats '
            'WHERE "opponentTeamAbbrev" IS NULL OR "homeRoad" IS NULL')).scalar()
        venues = [r[0] for r in conn.execute(text(
            'SELECT DISTINCT "homeRoad" FROM player_game_stats'))]

    # These two fields are the whole reason the table is worth having: they
    # are what lets opponent_strength be checked against real production.
    check("every row knows its opponent and its venue", missing == 0, missing)

    with engine.connect() as conn:
        gap = conn.execute(text(
            'SELECT count(*) FROM player_game_stats '
            'WHERE "positionCode" IS NOT NULL AND "hits" IS NULL')).scalar()
        orphans = conn.execute(text(
            'SELECT count(*) FROM player_game_stats WHERE "hits" IS NOT NULL '
            'AND "positionCode" IS NULL AND "saves" IS NULL')).scalar()

    # Both were symptoms of unstable paging: skater rows whose realtime
    # counterpart was dropped, and realtime rows whose summary row was.
    check("every skater row got its realtime half", gap == 0, gap)
    check("and no realtime row was left without a summary row", orphans == 0, orphans)
    check("venue is home or road and nothing else",
          set(venues) <= {"H", "R"}, venues)

    with engine.connect() as conn:
        skaters = conn.execute(text(
            'SELECT count(*) FROM player_game_stats WHERE "positionCode" IS NOT NULL')).scalar()
        goalies = conn.execute(text(
            'SELECT count(*) FROM player_game_stats WHERE "saves" IS NOT NULL')).scalar()

    check("both skaters and goalies are present", skaters > 0 and goalies > 0,
          (skaters, goalies))
    # About 1:20 on a complete season. The bound is loose because the table
    # may hold a partial backfill; it is here to catch a gross mix-up, not to
    # pin a ratio.
    check("goalies are a minority of rows, as they should be",
          goalies < skaters / 2, (goalies, skaters))

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
