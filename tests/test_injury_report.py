"""
Tests for the nightly injury report (injury_report.py).

ESPN is stubbed; the matching and the page's reading need no database, and the
write runs against a throwaway table, never `current_injuries`.

What is pinned: ESPN's JSON read into one entry per player with NHL tricodes
(its UTAH and LA are not the NHL's); two players sharing a name told apart by
ESPN's team and position; the table written with the pipeline's columns, so a
preseason re-run of `apply_injury_adjustments` still reads it; a failed or
empty read changing nothing; and on the page side, a note older than the
player's last game marked stale - the October bug, where Matthews was still
"Out" from March after playing both opening nights - and the report dated by
when it was read, not by its newest note.

Author - Jason Druckenmiller
Created - 10/5/2026
Updated - 10/5/2026
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "injury-report-test")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import injury_report as ir                                  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def espn_player(name, team, position, status, date, kind=None, back=None):
    details = {"fantasyStatus": {"abbreviation": "IR"}}
    if kind:
        details["type"] = kind
    if back:
        details["returnDate"] = back
    return {"status": status, "date": date, "details": details,
            "athlete": {"displayName": name, "position": {"abbreviation": position},
                        "team": {"abbreviation": team}}}


FEED = {"timestamp": "2026-10-05T13:00Z", "injuries": [
    {"id": "1", "displayName": "Vancouver Canucks", "injuries": [
        espn_player("Elias Pettersson", "VAN", "D", "Day-To-Day", "2026-10-04T18:00Z", "Upper Body"),
    ]},
    {"id": "2", "displayName": "Utah Mammoth", "injuries": [
        espn_player("Logan Cooley", "UTAH", "C", "Injured Reserve", "2026-10-01T15:00Z", "Lower Body", "2026-10-20"),
    ]},
    {"id": "3", "displayName": "Los Angeles Kings", "injuries": [
        espn_player("Nobody Projected", "LA", "LW", "Out", "2026-09-20T12:00Z"),
        {"status": "Out", "athlete": {}},           # no name: skipped, not a crash
    ]},
]}

POOL = [
    {"playerId": 1, "fullName": "Elias Pettersson", "teamAbbrevs": "VAN", "positionCode": "C"},
    {"playerId": 2, "fullName": "Elias Pettersson", "teamAbbrevs": "VAN", "positionCode": "D"},
    {"playerId": 3, "fullName": "Logan Cooley", "teamAbbrevs": "UTA", "positionCode": "C"},
]

# --------------------------------------------------------------------------
print("\n=== 1. reading ESPN ===")

entries = ir.parse(FEED)
check("one entry per named player", [e["name"] for e in entries]
      == ["Elias Pettersson", "Logan Cooley", "Nobody Projected"], [e["name"] for e in entries])
check("ESPN's UTAH and LA become the NHL's UTA and LAK",
      entries[1]["team"] == "UTA" and entries[2]["team"] == "LAK", [e["team"] for e in entries])
check("type and return date come out of ESPN's details",
      entries[1]["type"] == "Lower Body" and entries[1]["returnDate"] == "2026-10-20", entries[1])
check("a note without them has neither", entries[2]["type"] is None and entries[2]["returnDate"] is None)

unmatched = ir.match_ids(entries, POOL)
check("the defenceman Pettersson, by ESPN's position, not the centre",
      entries[0]["playerId"] == 2, entries[0].get("playerId"))
check("Cooley matched on UTA", entries[1]["playerId"] == 3)
check("a player nobody projects is kept unmatched and named",
      entries[2]["playerId"] is None and unmatched == ["Nobody Projected"], unmatched)

# --------------------------------------------------------------------------
print("\n=== 2. what the page reads ===")

fetched = datetime(2026, 10, 5, 8, 31, tzinfo=timezone.utc)
rows = [
    {"playerId": 34, "injuryStatus": "Out", "injuryDetails": "{'type': 'Knee', 'returnDate': '2026-04-01'}",
     "injuryDate": "2026-03-19T14:00Z", "fetchedAt": fetched},          # a March note, no new columns
    {"playerId": 3.0, "injuryStatus": "Injured Reserve", "injuryDetails": "{}",
     "injuryDate": "2026-10-01T15:00Z", "injuryType": "Lower Body", "returnDate": "2026-10-20",
     "fetchedAt": fetched},
    {"playerId": 2, "injuryStatus": "Day-To-Day", "injuryDetails": "{}",
     "injuryDate": "2026-10-04T18:00Z", "fetchedAt": fetched},
    {"playerId": None, "injuryStatus": "Out", "injuryDetails": "{}", "injuryDate": "2026-10-04T18:00Z",
     "fetchedAt": fetched},
]
last_games = {"34": "2026-09-30", "2": "2026-10-04"}
found, as_of = ir.by_player(rows, last_games)
check("a note older than his last game is stale - played through it",
      found["34"]["stale"] is True, found["34"])
check("a note with no game since is not", found["3"]["stale"] is False)
check("a note from the day of his last game is not stale - it may be about that game",
      found["2"]["stale"] is False, found["2"])
check("the old table's type and return date are still read from the details text",
      found["34"]["type"] == "Knee" and found["34"]["returnDate"] == "2026-04-01")
check("the new columns are read directly", found["3"]["type"] == "Lower Body"
      and found["3"]["returnDate"] == "2026-10-20")
check("a float player id from pandas still keys as an integer", "3" in found)
check("an unmatched row is not keyed", len(found) == 3)
check("the report is dated by when it was read, in Eastern", as_of == "2026-10-05", as_of)

legacy, legacy_as_of = ir.by_player([{k: v for k, v in r.items() if k != "fetchedAt"} for r in rows])
check("a table from the preseason pipeline is dated by its newest note",
      legacy_as_of == "2026-10-04", legacy_as_of)
check("with no game data nothing is stale", not any(v["stale"] for v in legacy.values()))


# --------------------------------------------------------------------------
print("\n=== 3. writing it, and failing safe ===")


class Response:
    def __init__(self, status, payload):
        self.status_code, self.payload = status, payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, response):
        self.response = response

    def get(self, *_args, **_kwargs):
        return self.response


TEST_TABLE = "zztest_current_injuries"
try:
    from db import execute, fetch_all

    execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
    stored, missed = ir.refresh(Session(Response(200, FEED)), table=TEST_TABLE)
    written = fetch_all(f'SELECT * FROM {TEST_TABLE} ORDER BY "playerName"')
    check("every player is written, matched or not", stored == 3 and len(written) == 3, written)
    columns = set(written[0]) if written else set()
    check("with the pipeline's columns, which apply_injury_adjustments reads",
          {"playerId", "playerName", "injuryStatus", "injuryDetails", "injuryDate"} <= columns, columns)
    check("and the four new ones", {"injuryType", "returnDate", "team", "fetchedAt"} <= columns, columns)
    cooley = next((r for r in written if r["playerName"] == "Logan Cooley"), {})
    check("the details are the dict's text, as the pipeline writes them",
          "'returnDate': '2026-10-20'" in str(cooley.get("injuryDetails")), cooley.get("injuryDetails"))

    for label, session in [
        ("a 403 from ESPN", Session(Response(403, {}))),
        ("something other than JSON", Session(Response(200, ValueError("not json")))),
        ("a report naming nobody", Session(Response(200, {"injuries": []}))),
    ]:
        try:
            ir.refresh(session, table=TEST_TABLE)
            raised = False
        except ir.InjuryFeedError:
            raised = True
        kept = len(fetch_all(f"SELECT 1 FROM {TEST_TABLE}"))
        check(f"{label} raises and keeps yesterday's report", raised and kept == 3, (raised, kept))
except Exception as exc:                                    # noqa: BLE001
    check("database-backed checks ran", False, f"{type(exc).__name__}: {exc}")
finally:
    try:
        execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
    except Exception:                                       # noqa: BLE001
        pass


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
