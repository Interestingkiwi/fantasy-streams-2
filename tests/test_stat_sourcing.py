"""
Tests for Stat Sourcing (stat_sourcing.py) - Season to date and Combined -
and the plan routes that take it.

What is pinned is what would be easy to get quietly wrong:

- Season to date opens the day after the last team plays its fifth game, read
  from the schedule, and the routes refuse it before then unless the preview
  setting is on;
- a rewritten row reads back, through `daily_value`, as exactly the player's
  rate so far - per start for a goalie, with GAA over his own seconds;
- a player with no games keeps his projection and says so, and a skater never
  picks up a goalie column;
- the category weights and the draft board's rank stay on the projections
  whatever the source.

Author - Jason Druckenmiller
Created - 10/1/2026
Updated - 10/3/2026
"""

import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "stat-sourcing-test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import daily_value as dv                                    # noqa: E402
import stat_sourcing as ss                                  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


# --------------------------------------------------------------------------
print("\n=== 1. when it opens ===")

# A and B play every day from Oct 1; C only every other day.
schedule = []
for day in range(1, 11):
    schedule.append((f"2026-10-{day:02d}", 'AAA', 'BBB'))
    if day % 2:
        schedule.append((f"2026-10-{day:02d}", 'CCC', 'DDD'))
check("the day after the last team plays its fifth game",
      ss.opens_on(schedule) == '2026-10-10', ss.opens_on(schedule))
check("the number of games is the dial", ss.opens_on(schedule, games=3) == '2026-10-06')
check("a team that never gets there means it does not open", ss.opens_on(schedule, games=8) is None)
check("no schedule, no date", ss.opens_on([]) is None)

check("closed before the date",
      ss.status(schedule, date(2026, 10, 9)) == {'open': False, 'opensOn': '2026-10-10', 'preview': False})
check("open from it", ss.status(schedule, date(2026, 10, 10))['open'])
preview = ss.status(schedule, date(2026, 10, 1), preview=True)
check("the preview setting opens it early, and says it is a preview",
      preview['open'] and preview['preview'] and preview['opensOn'] == '2026-10-10')
check("once it has opened it is no longer a preview",
      ss.status(schedule, date(2026, 10, 12), preview=True)['preview'] is False)
check("the season is read from the schedule", ss.schedule_season(schedule) == 2026
      and ss.schedule_season([('2027-01-05', 'A', 'B')]) == 2026 and ss.schedule_season([]) is None)


# --------------------------------------------------------------------------
print("\n=== 2. a season's rows ===")

skater = {'playerId': 1, 'positionCode': 'C', 'projectedGames': 80, 'proj_goals': 40,
          'proj_assists': 40, 'proj_shots': 240, 'proj_hits': 80, 'proj_timeOnIce': None,
          'proj_totalFaceoffWins': 800, 'proj_totalFaceoffLosses': 700}
goalie = {'playerId': 2, 'positionCode': 'G', 'projectedGames': 60, 'proj_gamesStarted': 55,
          'proj_wins': 30, 'proj_saves': 1400, 'proj_shotsAgainst': 1540, 'proj_goalsAgainst': 140,
          'proj_savePct': 0.909, 'proj_goalsAgainstAverage': 2.6, 'proj_timeOnIce': 190000, 'proj_goals': None}
idle = {'playerId': 3, 'positionCode': 'D', 'projectedGames': 70, 'proj_goals': 7, 'proj_hits': 140}
sums = {'1': {'games': 4, 'goals': 3, 'assists': 1, 'shots': 14, 'hits': 2, 'timeOnIce': 4800,
              'faceoffWins': 40, 'faceoffLosses': 30, 'wins': None, 'saves': None},
        '2': {'games': 2, 'wins': 1, 'saves': 55, 'shotsAgainst': 60, 'goalsAgainst': 5,
              'timeOnIce': 5400, 'goals': None}}
rows = {r['playerId']: r for r in ss.season_rows([skater, goalie, idle], sums)}
cats = ['G', 'A', 'SOG', 'HIT', 'FW', 'W', 'SV', 'SVpct', 'GAA', 'TOI']
per_game = dv.per_game(rows[1], cats)
check("a skater reads back as his rate so far, through daily_value",
      per_game['G'] == 0.75 and per_game['SOG'] == 3.5 and per_game['FW'] == 10, per_game)
check("...and records no goalie category - least of all time on ice, a goalie category",
      'TOI' not in per_game and 'W' not in per_game and rows[1]['proj_timeOnIce'] is None, per_game)
goalie_line = dv.per_game(rows[2], cats)
check("a goalie reads back per start", goalie_line['W'] == 0.5 and goalie_line['SV'] == 27.5, goalie_line)
check("his save percentage is saves over shots", abs(goalie_line['SVpct'] - 55 / 60) < 1e-9)
check("his GAA is over his own seconds, not an assumed hour",
      abs(goalie_line['GAA'] - 5 * 3600 / 5400) < 1e-9, goalie_line['GAA'])
check("projected games and starts are left alone - start odds are balanced against them",
      rows[2]['proj_gamesStarted'] == 55 and rows[1]['projectedGames'] == 80)
check("each says where its line came from",
      rows[1]['statSource'] == 'season' and rows[1]['seasonGames'] == 4)
check("a player with no games keeps his projection, and says so",
      rows[3]['statSource'] == 'projection' and rows[3]['proj_goals'] == 7 and rows[3]['seasonGames'] == 0)
check("the projections themselves are not changed", skater['proj_goals'] == 40 and 'statSource' not in skater)
unprojected = ss.season_rows([{'playerId': 4, 'positionCode': 'L', 'projectedGames': 0}],
                             {'4': {'games': 2, 'goals': 1}})[0]
check("with no projected games to scale by, his own games stand in",
      dv.per_game(unprojected, ['G'])['G'] == 0.5, unprojected)
relief = ss.season_rows([goalie], {'2': {'games': 0, 'saves': None}})[0]
check("a goalie with only relief outings (no counted games) keeps his projection",
      relief['statSource'] == 'projection' and relief['proj_wins'] == 30)


# --------------------------------------------------------------------------
print("\n=== 2b. Combined ===")

blended = {r['playerId']: r for r in ss.combined_rows([skater, goalie, idle], sums)}
line = dv.per_game(blended[1], ['G', 'A', 'P', 'SOG', 'FW'])
expected_g = (ss.PRIOR_GAMES['G'] * 0.5 + 4 * 0.75) / (ss.PRIOR_GAMES['G'] + 4)
check("a stat is its projection and his season weighted k to n",
      abs(line['G'] - expected_g) < 1e-9, (line['G'], expected_g))
check("four games barely move goals, whose k is large",
      0.5 < line['G'] < 0.55, line['G'])
moved = ss.combined_rows([dict(skater, proj_totalFaceoffWins=400)],
                         {'1': {'games': 4, 'goals': 3, 'faceoffWins': 60}})[0]
faceoffs = dv.per_game(moved, ['FW'])['FW']
check("but move faceoffs, whose projection is weak, a good part of the way",
      abs(faceoffs - (ss.PRIOR_GAMES['FW'] * 5 + 4 * 15) / (ss.PRIOR_GAMES['FW'] + 4)) < 1e-9
      and (faceoffs - 5) / (15 - 5) > 0.3, faceoffs)
check("points are the blended goals and assists, so they cannot disagree",
      abs(line['P'] - (line['G'] + line['A'])) < 1e-9, line)
long_run = ss.combined_rows([skater], {'1': {'games': 5000, 'goals': 5000, 'assists': 0,
                                             'shots': 0, 'hits': 0}})[0]
check("a long enough season outweighs any projection",
      abs(dv.per_game(long_run, ['G'])['G'] - 1.0) < 0.02)
gline = dv.per_game(blended[2], ['W', 'GA', 'SA', 'SV', 'SVpct', 'GAA'])
check("a goalie's saves are his blended shots less his blended goals against",
      abs(gline['SV'] - (gline['SA'] - gline['GA'])) < 1e-9, gline)
check("and his save percentage and GAA are built from the same parts",
      abs(gline['SVpct'] - (1 - gline['GA'] / gline['SA'])) < 1e-9
      and abs(blended[2]['proj_goalsAgainstAverage']
              - blended[2]['proj_goalsAgainst'] * 3600 / blended[2]['proj_timeOnIce']) < 1e-9)
check("each row says it is the blend", blended[1]['statSource'] == 'combined'
      and blended[3]['statSource'] == 'projection' and blended[3]['proj_goals'] == 7)
check("every measured stat is used, and every blended column has its k",
      set(ss.BLEND_CODES.values()) == set(ss.PRIOR_GAMES)
      and set(ss.BLEND_CODES) <= set(ss.SOURCE_COLUMNS))
season_only = ss.combined_rows([{'playerId': 9, 'positionCode': 'C', 'projectedGames': 60,
                                 'proj_goals': None}], {'9': {'games': 2, 'goals': 1}})[0]
check("with no projected value for a stat, the season stands alone",
      dv.per_game(season_only, ['G'])['G'] == 0.5)


# --------------------------------------------------------------------------
print("\n=== 3. the weights stay on the projections ===")

import week_planner                                         # noqa: E402

pool = [dict(skater, playerId=i, teamAbbrevs='TOR', proj_goals=10 + i, projectedGames=70)
        for i in range(10, 70)]
noisy = [dict(p, proj_goals=(p['playerId'] % 7) * 20) for p in pool]
plain = week_planner.Week(pool, ['G', 'SOG'], {'C': 2}, ['2026-10-05'], [('2026-10-05', 'TOR', 'MTL')])
sourced = week_planner.Week(pool, ['G', 'SOG'], {'C': 2}, ['2026-10-05'], [('2026-10-05', 'TOR', 'MTL')],
                            values=noisy)
check("a few weeks' spread does not re-weight the league", plain.flat == sourced.flat,
      (plain.flat, sourced.flat))
check("but the players are valued on the source's rows",
      sourced.by_id['13']['perGame']['G'] != plain.by_id['13']['perGame']['G'])
check("and the projections are kept for the draft board", sourced.projections is pool)


# --------------------------------------------------------------------------
print("\n=== 4. against the database and the routes ===")

try:
    from app import app
    from db import fetch_all

    season = ss.schedule_season([(r['gameDate'], None, None) for r in fetch_all(
        'SELECT "gameDate" FROM nhl_schedule ORDER BY "gameDate" LIMIT 1')])
    sums = ss.season_sums(season)
    goalie_rows = fetch_all(
        'SELECT "playerId", count(*) FILTER (WHERE COALESCE("gamesStarted", 0) >= 1) AS starts '
        'FROM player_game_stats WHERE "positionCode" IS NULL AND "gameId" >= :first GROUP BY "playerId"',
        {'first': season * 1_000_000})
    check("a goalie's games are his starts, relief outings left out",
          all(int(sums.get(str(g['playerId']), {}).get('games') or 0) == g['starts'] for g in goalie_rows),
          goalie_rows[:3])

    client = app.test_client()
    page = client.get('/standalone/').get_data(as_text=True)
    check("the page carries whether it is open and when", 'window.FS_STAT_SOURCING = {' in page
          and '"opensOn"' in page)

    ids = [r['playerId'] for r in fetch_all(
        'SELECT DISTINCT s."playerId" FROM player_game_stats s JOIN final_projections f '
        'ON f."playerId" = s."playerId" WHERE s."positionCode" IS NOT NULL AND s."gameId" >= :first LIMIT 12',
        {'first': season * 1_000_000})]
    body = {'roster': ids, 'league_mode': 'categories',
            'categories': ['proj_goals', 'proj_shots', 'proj_hits'],
            'slots': {'C': 2, 'LW': 2, 'RW': 2, 'D': 4}, 'start': '2026-10-05', 'end': '2026-10-11'}

    app.config['STAT_SOURCING_PREVIEW'] = True
    projected = client.post('/standalone/api/week', json=body).get_json()
    todate = client.post('/standalone/api/week', json={**body, 'source': 'todate'}).get_json()
    check("with the preview on, a Season to date plan is made", todate.get('status') == 'success',
          todate.get('message'))
    players = {p['playerId']: p for p in todate.get('players', [])}
    check("every player says where his line came from",
          players and all(p.get('statSource') in ('season', 'projection') for p in players.values()))
    check("projected plans carry no such mark",
          all('statSource' not in p for p in projected.get('players', [])))
    check("the draft board's rank is the same whichever the source",
          {p['playerId']: p['seasonRank'] for p in projected.get('players', [])}
          == {p['playerId']: p['seasonRank'] for p in todate.get('players', [])})
    pool_reply = client.post('/standalone/api/free-agents/pool', json={**body, 'source': 'todate'})
    check("the free agent pool takes the source too", pool_reply.status_code == 200
          and all('statSource' in p for p in pool_reply.get_json()['players'][:20]))

    app.config['STAT_SOURCING_PREVIEW'] = False
    original = ss.opens_on
    ss.opens_on = lambda schedule, games=ss.OPEN_AFTER_TEAM_GAMES: '2099-01-01'
    try:
        refused = client.post('/standalone/api/week', json={**body, 'source': 'todate'})
        check("before it opens, Season to date is refused with the date",
              refused.status_code == 400 and 'January 1' in refused.get_json()['message'],
              refused.get_json())
        check("and Projected still plans", client.post('/standalone/api/week', json=body).status_code == 200)
        combined = client.post('/standalone/api/week', json={**body, 'source': 'combined'})
        check("Combined plans from the start - a game or two barely moves it",
              combined.status_code == 200
              and all(p.get('statSource') in ('combined', 'projection')
                      for p in combined.get_json()['players']), combined.get_json().get('message'))
    finally:
        ss.opens_on = original
        app.config['STAT_SOURCING_PREVIEW'] = True
    odd = client.post('/standalone/api/week', json={**body, 'source': 'astrology'})
    check("an unknown source is a 400, not a silent fallback", odd.status_code == 400)
except Exception as exc:                                    # noqa: BLE001
    check("database and route checks ran", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
