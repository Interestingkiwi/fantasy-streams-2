"""
Tests for lines and power-play units from shift charts (game_lines.py).

No network: a game is built shift by shift, laid out so each decision the
module makes can be seen to be made:

- the checking line plays the most 5-on-5 minutes together, but the top line
  gets the power play - so line 1 has to be the offensive line, not the
  longest-serving one;
- an empty net (six skaters against five) is neither 5-on-5 nor a power play;
- power-play units are who shared power-play time, seeded by the most of it;
- a skater with no known position is counted on the ice but put on no line.

Then, against the database, what the nightly job has actually written.

Author - Jason Druckenmiller
Created - 10/1/2026
Updated - 10/1/2026
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import game_lines as gl                                     # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def mmss(seconds):
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def shifts(team, period, start, end, players):
    return [{'typeCode': gl.SHIFT, 'playerId': p, 'teamAbbrev': team, 'period': period,
             'startTime': mmss(start), 'endTime': mmss(end)} for p in players]


F1, F2, F3, F4 = (1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)
D1, D2, D3 = (21, 22), (23, 24), (25, 26)
THEM_F, THEM_D, THEM_PK = (31, 32, 33), (41, 42), (31, 32, 41, 42)
GOALIES = {98, 99}

game = []
# Period 1, all 5-on-5. F3, the checking line, is out longest together (500s).
for start, end, line, pair in [(0, 300, F3, D1), (300, 500, F1, D2), (500, 700, F2, D3),
                               (700, 850, F4, D1), (850, 1050, F3, D2), (1050, 1200, F1, D3)]:
    game += shifts('AAA', 1, start, end, line + pair)
game += shifts('AAA', 1, 0, 1200, [99]) + shifts('BBB', 1, 0, 1200, THEM_F + THEM_D + (98,))
# Period 2: a four-minute power play, the first unit for 160s and the second
# for 80, then 50s more of 5-on-5 for F1 - 400s together, less than F3.
game += shifts('AAA', 2, 0, 160, (1, 2, 3, 4, 21)) + shifts('AAA', 2, 160, 240, (5, 6, 7, 22, 23))
game += shifts('BBB', 2, 0, 240, THEM_PK)
game += shifts('AAA', 2, 240, 290, F1 + D1) + shifts('BBB', 2, 240, 290, THEM_F + THEM_D)
game += shifts('AAA', 2, 0, 290, [99]) + shifts('BBB', 2, 0, 290, [98])
# Period 3: a minute of empty net, six against five - neither strength.
game += shifts('AAA', 3, 0, 60, (1, 2, 3, 4, 5, 21)) + shifts('BBB', 3, 0, 60, THEM_F + THEM_D + (98,))
# A goal event shares the chart and must be ignored
game.append({'typeCode': 505, 'playerId': 1, 'teamAbbrev': 'AAA', 'period': 1,
             'startTime': '05:00', 'endTime': '05:00'})

POSITIONS = {**{p: 'C' for p in (1, 4, 7, 10, 31)}, **{p: 'L' for p in (2, 5, 8, 11, 32)},
             **{p: 'R' for p in (3, 6, 9, 12, 33)}, **{p: 'D' for p in D1 + D2 + D3 + THEM_D}}


# --------------------------------------------------------------------------
print("\n=== 1. reading the chart ===")

check("a shift clock reads as seconds into the period", gl.clock('12:34') == 754)
check("and anything else as nothing", gl.clock(None) is None and gl.clock('soon') is None)

strengths = gl.on_ice(game, GOALIES)
even = sum(strengths['AAA']['even'].values())
check("5-on-5 seconds are every second both sides had five skaters",
      even == 1250, even)
check("the other side sees the same 5-on-5 time", sum(strengths['BBB']['even'].values()) == 1250)
check("a power play is counted for the side with the extra skater",
      sum(strengths['AAA']['pp'].values()) == 240 and sum(strengths['BBB']['pp'].values()) == 0,
      (sum(strengths['AAA']['pp'].values()), sum(strengths['BBB']['pp'].values())))
check("goalies are never in an on-ice set",
      not any(GOALIES & on for side in strengths.values() for kind in side.values() for on in kind))
check("an empty net is neither 5-on-5 nor a power play",
      not any(len(on) == 6 for side in strengths.values() for kind in side.values() for on in kind))


# --------------------------------------------------------------------------
print("\n=== 2. lines ===")

rows = {r['playerId']: r for r in gl.game_lines(game, POSITIONS, GOALIES)}
check("every skater with a shift gets a row, and no goalie does",
      set(rows) == set(F1 + F2 + F3 + F4 + D1 + D2 + D3 + THEM_F + THEM_D), sorted(rows))
check("the three forwards who played together are a line",
      sorted([1] + rows[1]['lineMates']) == [1, 2, 3] and sorted([7] + rows[7]['lineMates']) == [7, 8, 9])
check("line 1 is the offensive line, not the one out longest together",
      rows[1]['line'] == 1 and rows[7]['line'] == 2,
      {p: rows[p]['line'] for p in (1, 7, 4, 10)})
check("...so the checking line, together longest, is line 2",
      rows[7]['lineSeconds'] > rows[1]['lineSeconds'], (rows[7]['lineSeconds'], rows[1]['lineSeconds']))
check("every forward line is numbered, 1 to 4",
      sorted({rows[p]['line'] for p in F1 + F2 + F3 + F4}) == [1, 2, 3, 4])
check("pairs are numbered the same way", [rows[p]['line'] for p in (21, 23, 25)] == [1, 2, 3],
      [rows[p]['line'] for p in (21, 23, 25)])
check("a line's time together is all of it on at once",
      rows[1]['lineSeconds'] == 400 and rows[2]['lineSeconds'] == 400, rows[1]['lineSeconds'])
check("his mates are teammates of his kind, most shared first, nobody unshared",
      rows[1]['mates'] == [[2, 400], [3, 400]] and rows[21]['mates'][0] == [22, 500],
      (rows[1]['mates'], rows[21]['mates']))
check("5-on-5 seconds leave out the power play and the empty net",
      rows[1]['evenSeconds'] == 400, rows[1]['evenSeconds'])
check("a forward is unit F and a defenceman D", rows[1]['unit'] == 'F' and rows[21]['unit'] == 'D')

without = {r['playerId']: r for r in gl.game_lines(game, {k: v for k, v in POSITIONS.items() if k != 12}, GOALIES)}
check("a skater with no known position is on no line and has no unit",
      without[12]['line'] is None and without[12]['unit'] is None, without[12])
check("...and his linemates, a forward short, are on no line either",
      without[10]['line'] is None and without[11]['line'] is None)

shared = {frozenset((1, 2)): 400, frozenset((1, 3)): 400, frozenset((2, 3)): 100}
check("a group whose weakest pair barely played together is not a line",
      gl.form_groups([1, 2, 3], shared, 3, 4) == [], gl.form_groups([1, 2, 3], shared, 3, 4))
shared[frozenset((2, 3))] = gl.MIN_LINE_SECONDS
check("...and at the threshold it is", gl.form_groups([1, 2, 3], shared, 3, 4) == [(1, 2, 3)])


# --------------------------------------------------------------------------
print("\n=== 3. power-play units ===")

check("the top unit is the five who shared the most power-play time",
      {p for p, r in rows.items() if r['ppUnit'] == 1} == {1, 2, 3, 4, 21},
      {p: r['ppUnit'] for p, r in rows.items() if r['ppUnit']})
check("and the second unit the next five", {p for p, r in rows.items() if r['ppUnit'] == 2} == {5, 6, 7, 22, 23})
check("a unit's mates are the other four", sorted(rows[1]['ppMates']) == [2, 3, 4, 21], rows[1]['ppMates'])
check("each row carries his and his team's power-play seconds",
      rows[1]['ppSeconds'] == 160 and rows[1]['teamPpSeconds'] == 240 and rows[5]['ppSeconds'] == 80)
check("a team with no power play has no units",
      all(rows[p]['ppUnit'] is None and rows[p]['teamPpSeconds'] == 0 for p in THEM_F + THEM_D))

from collections import Counter                              # noqa: E402
check("a few seconds of power play make no units",
      gl.pp_units(Counter({frozenset((1, 2, 3, 4, 21)): gl.MIN_TEAM_PP_SECONDS - 1})) == [])


# --------------------------------------------------------------------------
print("\n=== 4. the summary the roster view shows ===")

original = gl.recent
try:
    gl.recent = lambda ids, season, games=None: {'5': [
        {'gameDate': '2026-10-03', 'line': 3, 'unit': 'F', 'ppUnit': None, 'teamPpSeconds': 0},
        {'gameDate': '2026-10-01', 'line': 2, 'unit': 'F', 'ppUnit': 2, 'teamPpSeconds': 200},
    ]}
    summary = gl.latest(['5'], 2026)['5']
    check("his line is the one he played on last", summary['line'] == 3 and summary['date'] == '2026-10-03')
    check("his unit is from his last game that had a power play - a night without one says nothing",
          summary['ppUnit'] == 2 and summary['ppDate'] == '2026-10-01', summary)
finally:
    gl.recent = original


# --------------------------------------------------------------------------
print("\n=== 5. what the nightly job has written ===")

try:
    from db import fetch_all

    season = fetch_all('SELECT MAX("gameId") / 1000000 AS season FROM player_game_stats')[0]['season']
    first = int(season) * 1_000_000
    missing = fetch_all(
        'SELECT count(DISTINCT s."gameId") AS n FROM player_game_stats s WHERE s."gameId" >= :first '
        'AND NOT EXISTS (SELECT 1 FROM player_game_lines l WHERE l."gameId" = s."gameId")',
        {'first': first})[0]['n']
    check("every game this season with results has lines", missing == 0, missing)

    groups = fetch_all(
        'SELECT "gameId", "teamAbbrev", unit, count(DISTINCT line) AS lines, max(line) AS top '
        'FROM player_game_lines WHERE "gameId" >= :first AND line IS NOT NULL '
        'GROUP BY "gameId", "teamAbbrev", unit', {'first': first})
    check("no team-game has more than four lines or three pairs",
          all(g['lines'] <= gl.LINE_LIMIT[g['unit']] and g['top'] <= gl.LINE_LIMIT[g['unit']] for g in groups),
          [g for g in groups if g['lines'] > gl.LINE_LIMIT[g['unit']]][:3])
    sizes = fetch_all(
        'SELECT unit, count(*) AS players FROM player_game_lines WHERE "gameId" >= :first '
        'AND line IS NOT NULL GROUP BY "gameId", "teamAbbrev", unit, line', {'first': first})
    check("every line has exactly its size - nobody on two lines",
          all(s['players'] == gl.LINE_SIZE[s['unit']] for s in sizes),
          [s for s in sizes if s['players'] != gl.LINE_SIZE[s['unit']]][:3])
    units = fetch_all(
        'SELECT count(*) AS players FROM player_game_lines WHERE "gameId" >= :first '
        'AND "ppUnit" IS NOT NULL GROUP BY "gameId", "teamAbbrev", "ppUnit"', {'first': first})
    check("no power-play unit has more than five", all(u['players'] <= gl.PP_UNIT_SIZE for u in units))
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
