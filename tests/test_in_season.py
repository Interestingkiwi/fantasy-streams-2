"""
Tests for the in-season pool (in_season.py) and goalies' starts read from
their usage (goalie_usage.py).

No database for the maths: a four-team league over four nights, in the shapes
`in_season.season_facts` reads, with the numbers worked out by hand below.
Then the routes on the real local data.

What is pinned is what a reader could be misled by: a player who has played
for another team is moved to it, and only then; a player nobody projected
gets Combined's blend against his position's league rate at his own ice time
- power-play stats per second of power-play time, plus/minus against zero -
and `daily_value` reads that blend back; his projected games are his share of
his team's games since his first, not a full season; a goalie with relief and
no start gets no row; Combined leaves a built row alone and Season to date
reads it back to his season rate; a goalie ESPN has out, on IR or suspended
is out from the note to his return, day-to-day and stale notes not, and one
on no NHL roster is out until he plays. A goalie's starts are blended with his
usage on his current team from his first game for it - a trade counts only
the new team - after the team's projections are reconciled, his per-start
line never moving; and the free agents' share of starts is of his team's
games, not his own appearances. An imported rookie projected under a stand-in
id (-2) is one player once he has played: his projection under his NHL id,
valued on it plus his games under every Stat Sourcing, never a second "no
projection" row; matched by name, team settling look-alikes, never to a
projected player or a skater for a goalie; found once by the name pool; and
a stand-in a browser still sends read as the NHL id, card included.

Author - Jason Druckenmiller
Created - 10/12/2026
Updated - 10/19/2026
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "in-season-test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import daily_value as dv                                    # noqa: E402
import goalie_usage as gu                                   # noqa: E402
import in_season                                            # noqa: E402
import stat_sourcing as ss                                  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def close(a, b, tol=1e-9):
    return a is not None and b is not None and abs(a - b) < tol


# Ten nights, every team playing every night: a season of ten games each
SCHEDULE = [(f"2026-10-{d:02d}", home, away) for d in range(1, 11)
            for home, away in (('AAA', 'BBB'), ('CCC', 'DDD'))]
TEAM_GAMES = gu.team_game_counts(SCHEDULE)
# Four nights played so far
GAMES = [{'teamAbbrev': team, 'gameId': gid + n, 'gameDate': f"2026-10-0{n}"}
         for n in range(1, 5) for team, gid in (('AAA', 100), ('BBB', 100), ('CCC', 200), ('DDD', 200))]


def agg(pid, team, name, pos, first, last, appearances, games, starts=0, **stats):
    """One player's games for one team, as `season_facts` reads them."""
    row = {'playerId': pid, 'teamAbbrev': team, 'fullName': name, 'positionCode': pos,
           'first': first, 'last': last, 'appearances': appearances, 'games': games, 'starts': starts}
    for column in in_season.STAT_COLUMNS + list(in_season.SKATER_EXTRA):
        row[column] = stats.get(column)
    return row


ROWS = [
    # Projected for AAA, now plays for BBB
    agg(11, 'AAA', 'Mover', 'C', '2026-10-01', '2026-10-02', 2, 2, timeOnIce=2400),
    agg(11, 'BBB', 'Mover', 'C', '2026-10-03', '2026-10-04', 2, 2, timeOnIce=2400),
    # Projected and where he was projected: 4 goals, 2 PP points in 20 min a night
    agg(12, 'CCC', 'Veteran', 'C', '2026-10-01', '2026-10-04', 4, 4, goals=4, ppPoints=2,
        timeOnIce=4800, ppTimeOnIce=720),
    # Nobody projected him: every DDD game since his first, 15 minutes, 2 on the PP
    agg(20, 'DDD', 'New Kid', 'C', '2026-10-02', '2026-10-04', 3, 3, goals=3, ppGoals=1, ppPoints=1,
        plusMinus=3, shots=9, timeOnIce=2700, ppTimeOnIce=360),
    # Nobody projected him either, and he has played one of DDD's three since
    agg(21, 'DDD', 'Spare', 'R', '2026-10-02', '2026-10-02', 1, 1, timeOnIce=600, ppTimeOnIce=0),
    # Unprojected goalie: two starts and a relief outing in CCC's four
    agg(30, 'CCC', 'Call-up', None, '2026-10-01', '2026-10-04', 3, 2, 2, wins=2, goalsAgainst=4,
        shotsAgainst=60, saves=56, timeOnIce=7200, shutouts=0, losses=0, otLosses=0),
    # A projected goalie with the other two
    agg(31, 'CCC', 'Starter', None, '2026-10-02', '2026-10-03', 2, 2, 2, wins=0, goalsAgainst=8,
        shotsAgainst=60, saves=52, timeOnIce=7200, shutouts=0, losses=2, otLosses=0),
    # Unprojected, relief only: nothing to value
    agg(32, 'DDD', 'Mop-up', None, '2026-10-03', '2026-10-03', 1, 0, 0),
]
FACTS = in_season.facts_from(ROWS, GAMES)

PROJECTIONS = [
    {'playerId': 11, 'fullName': 'Mover', 'positionCode': 'C', 'teamAbbrevs': 'AAA', 'projectedGames': 10,
     'proj_goals': 2},
    {'playerId': 12, 'fullName': 'Veteran', 'positionCode': 'C', 'teamAbbrevs': 'CCC', 'projectedGames': 10,
     'proj_goals': 5},
    {'playerId': 31, 'fullName': 'Starter', 'positionCode': 'G', 'teamAbbrevs': 'CCC', 'projectedGames': 9,
     'proj_gamesStarted': 8, 'proj_wins': 4, 'proj_goalsAgainst': 20, 'proj_shotsAgainst': 240,
     'proj_saves': 220, 'proj_timeOnIce': 28800},
]

# --------------------------------------------------------------------------
print("\n=== 1. where a player plays now ===")

moved = {str(r['playerId']): r for r in in_season.current_teams(PROJECTIONS, FACTS)}
check("a player whose latest game was for another team is moved to it, his projected team kept",
      moved['11']['teamAbbrevs'] == 'BBB' and moved['11']['projectedTeam'] == 'AAA', moved['11'])
check("one still where he was projected is left alone, and the projections are not edited",
      'projectedTeam' not in moved['12'] and PROJECTIONS[0]['teamAbbrevs'] == 'AAA')

# --------------------------------------------------------------------------
print("\n=== 2. the players nobody projected ===")

built = {str(r['playerId']): r for r in in_season.unprojected_rows(FACTS, {'11', '12', '31'}, TEAM_GAMES)}
check("everyone with a game and no projection gets a row - but not a goalie with no start",
      set(built) == {'20', '21', '30'}, sorted(built))
kid = built['20']
check("marked as built in season, on his team, at his position in Yahoo's words",
      kid['projectionSource'] == ss.IN_SEASON_SOURCE and kid['teamAbbrevs'] == 'DDD'
      and kid['eligiblePositions'] == 'C' and built['21']['eligiblePositions'] == 'RW')
check("projected games: his share of his team's games since his first, over the season",
      kid['projectedGames'] == 10.0 and built['21']['projectedGames'] == round(1 / 3 * 10, 1),
      (kid['projectedGames'], built['21']['projectedGames']))

# League centres: 7 goals in 12,300 seconds (the mover's 4,800 count, goalless);
# 3 PP points in 1,080 PP seconds (his PP time was never collected)
prior_goals = 7 / 12300 * 900
expected = (60 * prior_goals + 3 * 1.0) / 63
check("goals: Combined's blend of the league's rate at his 15 minutes and his own",
      close(kid['proj_goals'] / kid['projectedGames'], expected), (kid['proj_goals'] / 10, expected))
check("PP points against power-play seconds, not all of them - his 2 PP minutes a night",
      close(kid['proj_ppPoints'] / 10, 1 / 3), kid['proj_ppPoints'] / 10)
check("plus/minus against zero, not a league rate",
      close(kid['proj_plusMinus'] / 10, 3 / 103), kid['proj_plusMinus'] / 10)
check("points are his blended goals and assists, and a skater has no goalie column",
      close(kid['proj_points'], kid['proj_goals'] + kid['proj_assists']) and 'proj_timeOnIce' not in kid
      and 'proj_wins' not in kid)
valued = dv.value_players([kid], ['G', 'PPP'])[0]['perGame']
check("daily_value reads the blend back per game", close(valued['G'], expected) and close(valued['PPP'], 1 / 3),
      valued)

goalie = built['30']
check("a goalie's starts are his share of his team's games, appearances his relief too",
      goalie['proj_gamesStarted'] == 5.0 and goalie['projectedGames'] == 7.5, goalie)
# League per start: 2 wins and 12 goals against in 4 starts
check("a goalie's line is per start, blended with the league's average start",
      close(goalie['proj_wins'] / 5, (30 * 0.5 + 2 * 1.0) / 32)
      and close(goalie['proj_goalsAgainst'] / 5, (30 * 3.0 + 2 * 2.0) / 32), goalie)
check("save percentage and GAA from the blended parts",
      close(goalie['proj_savePct'], 1 - goalie['proj_goalsAgainst'] / goalie['proj_shotsAgainst'])
      and close(goalie['proj_saves'], goalie['proj_shotsAgainst'] - goalie['proj_goalsAgainst']))

combined = ss.combined_rows([kid], {'20': {'games': 3, 'goals': 3, 'plusMinus': 3}})[0]
check("Combined leaves a built row as it is - it is that blend already",
      combined['proj_goals'] == kid['proj_goals'] and combined['statSource'] == 'combined')
todate = ss.season_rows([kid], {'20': {'games': 3, 'goals': 3, 'plusMinus': 3}})[0]
check("Season to date reads it back to his season rate", close(todate['proj_goals'] / 10, 1.0), todate)

pool = in_season.pool(PROJECTIONS, SCHEDULE, facts=FACTS)
check("the pool: the projections, moved, plus the built rows",
      len(pool) == 6 and next(r for r in pool if r['playerId'] == 11)['teamAbbrevs'] == 'BBB')
check("no game data: the projections as they are", in_season.pool(PROJECTIONS, SCHEDULE, facts={}) == PROJECTIONS)

# --------------------------------------------------------------------------
print("\n=== 2b. an imported rookie's stand-in id ===")

# Projected from outside before he had an NHL id: a stand-in, -2, and 0.3
# goals a game. His three games (3 goals) are under his real id, 20.
ROOKIE = {'playerId': -2, 'fullName': 'New Kid', 'positionCode': 'C', 'teamAbbrevs': 'DDD',
          'projectedGames': 10, 'proj_goals': 3, 'projectionSource': 'imported'}
UNPLAYED = {'playerId': -5, 'fullName': 'Prospect', 'positionCode': 'L', 'teamAbbrevs': 'AAA',
            'projectedGames': 10, 'proj_goals': 2, 'projectionSource': 'imported'}
with_rookies = PROJECTIONS + [ROOKIE, UNPLAYED]
rookie_pool = in_season.pool(with_rookies, SCHEDULE, facts=FACTS)
his = [r for r in rookie_pool if r['playerId'] in (20, -2)]
check("he is one player, under his NHL id - not his projection plus a row built from his games",
      len(his) == 1 and his[0]['playerId'] == 20 and len(rookie_pool) == 7, [r['playerId'] for r in rookie_pool])
check("valued on his rookie projection, the stand-in kept as formerId",
      his[0]['projectionSource'] == 'imported' and his[0]['proj_goals'] == 3 and his[0]['formerId'] == -2, his[0])
check("one who has not played keeps his stand-in",
      any(r['playerId'] == -5 and 'formerId' not in r for r in rookie_pool))
check("the page and routes are told which stand-in became which id",
      in_season.former_ids(rookie_pool) == {'-2': 20}, in_season.former_ids(rookie_pool))
check("and the projections are not edited", ROOKIE['playerId'] == -2 and 'formerId' not in ROOKIE)

# Stat Sourcing reads his games, keyed by the id they are under
sums = {'20': {'games': 3, 'goals': 3}}
blended = ss.combined_rows(his, sums)[0]
k = ss.PRIOR_GAMES['G']
check("Combined: his rookie projection blended with his own games",
      blended['statSource'] == 'combined' and blended['seasonGames'] == 3
      and close(blended['proj_goals'] / 10, (k * 0.3 + 3 * 1.0) / (k + 3)), blended)
check("Season to date: his games, not 'proj'",
      ss.season_rows(his, sums)[0]['statSource'] == 'season'
      and close(ss.season_rows(his, sums)[0]['proj_goals'] / 10, 1.0))

matched = in_season.placeholder_ids(
    [{'playerId': 12, 'fullName': 'Veteran', 'positionCode': 'C', 'teamAbbrevs': 'CCC'},
     {'playerId': -6, 'fullName': 'Veteran', 'positionCode': 'C', 'teamAbbrevs': 'CCC'},
     {'playerId': -7, 'fullName': 'Spare', 'positionCode': 'G', 'teamAbbrevs': 'DDD'},
     {'playerId': -8, 'fullName': 'Twin', 'positionCode': 'C', 'teamAbbrevs': 'BBB'},
     {'playerId': -9, 'fullName': 'Elias Pettersson', 'positionCode': 'D', 'teamAbbrevs': 'VAN'},
     {'playerId': -10, 'fullName': 'Dup', 'positionCode': 'C', 'teamAbbrevs': 'AAA'},
     {'playerId': -11, 'fullName': 'Dup', 'positionCode': 'C', 'teamAbbrevs': 'AAA'},
     {'playerId': -12, 'fullName': 'Patrik Laine', 'positionCode': 'L', 'teamAbbrevs': 'MTL'}],
    [(12, 'Veteran', 'C', 'CCC'), (21, 'Spare', 'R', 'DDD'),
     (102, 'Twin', 'C', 'AAA'), (103, 'Twin', 'C', 'BBB'),
     (100, 'Elias Pettersson', 'C', 'VAN'), (101, 'Elias Pettersson', 'D', 'VAN'),
     (104, 'Dup', 'C', 'AAA'), (105, 'Pátrik Laine', 'L', 'MTL')])
check("never a projected player's games, nor a skater's for a goalie",
      -6 not in matched and -7 not in matched, matched)
check("two of one name: his team settles it, then his position",
      matched.get(-8) == 103 and matched.get(-9) == 101, matched)
check("two stand-ins after one player: neither takes him", -10 not in matched and -11 not in matched, matched)
check("names matched as the roster scrape matches them, accents and all", matched.get(-12) == 105, matched)

import player_pool                                          # noqa: E402

real_fetch = player_pool.fetch_all
player_pool.fetch_all = lambda sql, params=None: (
    [{'playerId': -2, 'fullName': 'New Kid', 'teamAbbrevs': 'DDD', 'positionCode': 'C'},
     {'playerId': 12, 'fullName': 'Veteran', 'teamAbbrevs': 'CCC', 'positionCode': 'C'}]
    if 'final_projections' in sql else
    [{'playerId': 20, 'fullName': 'New Kid', 'teamAbbrevs': 'DDD', 'positionCode': 'C'},
     {'playerId': 12, 'fullName': 'Veteran', 'teamAbbrevs': 'CCC', 'positionCode': 'C'}])
try:
    names = player_pool.pool()
finally:
    player_pool.fetch_all = real_fetch
check("the name pool (ESPN, Yahoo's lineups) finds him once, under the id his games are under",
      [r['playerId'] for r in names if r['fullName'] == 'New Kid'] == [20] and len(names) == 2, names)

# --------------------------------------------------------------------------
print("\n=== 3. goalies' starts from their usage ===")

traded = gu.usage_from(
    [{'playerId': 40, 'teamAbbrev': 'AAA', 'first': '2026-10-01', 'last': '2026-10-02', 'starts': 2},
     {'playerId': 40, 'teamAbbrev': 'BBB', 'first': '2026-10-03', 'last': '2026-10-04', 'starts': 1}], GAMES)
check("a traded goalie is counted on his new team only, from his first game for it",
      traded['40'] == {'team': 'BBB', 'starts': 1, 'games': 2, 'first': '2026-10-03'}, traded)
check("the facts give the same for a goalie who stayed",
      FACTS['usage']['31'] == {'team': 'CCC', 'starts': 2, 'games': 3, 'first': '2026-10-02'}, FACTS['usage'])

tandem = [
    {'playerId': 1, 'positionCode': 'G', 'teamAbbrevs': 'AAA', 'projectedGames': 7, 'proj_gamesStarted': 6,
     'proj_wins': 3, 'proj_goalsAgainst': 15, 'proj_shotsAgainst': 180, 'proj_saves': 165, 'proj_timeOnIce': 21600},
    {'playerId': 2, 'positionCode': 'G', 'teamAbbrevs': 'AAA', 'projectedGames': 6, 'proj_gamesStarted': 6,
     'proj_wins': 3, 'proj_goalsAgainst': 18, 'proj_shotsAgainst': 180, 'proj_saves': 162, 'proj_timeOnIce': 21600},
    {'playerId': 3, 'positionCode': 'G', 'teamAbbrevs': 'BBB', 'projectedGames': 5, 'proj_gamesStarted': 5},
]
used = {'1': {'team': 'AAA', 'starts': 4, 'games': 4}, '2': {'team': 'AAA', 'starts': 0, 'games': 2},
        '3': {'team': 'CCC', 'starts': 3, 'games': 3}}
after = {r['playerId']: r for r in gu.apply(tandem, used, TEAM_GAMES)}
# 12 projected starts in 10 games: each share 0.5 once reconciled
check("shares reconciled to the team's games, then blended: (10 x 0.5 + 4) / (10 + 4)",
      close(after[1]['proj_gamesStarted'], 9 / 14 * 10) and after[1]['usage']['projectedShare'] == 0.5,
      after[1])
check("the backup who has relieved and not started loses starts: (10 x 0.5 + 0) / (10 + 2)",
      close(after[2]['proj_gamesStarted'], 5 / 12 * 10), after[2])
check("his per-start line does not move, only how many starts",
      close(after[1]['proj_wins'] / after[1]['proj_gamesStarted'], 0.5)
      and close(after[2]['proj_goalsAgainst'] / after[2]['proj_gamesStarted'], 3.0))
check("appearances move with starts", close(after[1]['projectedGames'], 7 * (9 / 14 * 10) / 6))
check("usage for a team his row does not name yet is not his - he is left as projected",
      after[3] is tandem[2] and 'usage' not in after[3])
check("k of zero is his usage alone",
      close(gu.apply(tandem[:1], used, TEAM_GAMES, prior_games=0)[0]['proj_gamesStarted'], 10.0))
rookie = {'playerId': 5, 'positionCode': 'G', 'teamAbbrevs': 'AAA', 'projectedGames': 4,
          'proj_gamesStarted': None, 'proj_wins': 2}
moved_rookie = gu.apply([rookie], {'5': {'team': 'AAA', 'starts': 2, 'games': 2}}, TEAM_GAMES)[0]
check("an imported rookie with no projected starts: his appearances stood in, and still per start",
      close(moved_rookie['proj_wins'] / moved_rookie['proj_gamesStarted'], 0.5)
      and moved_rookie['projectedGames'] == moved_rookie['proj_gamesStarted'], moved_rookie)

# --------------------------------------------------------------------------
print("\n=== 3b. goalies ESPN has out ===")

hurt_facts = in_season.facts_from(ROWS, GAMES, [
    {'playerId': 31, 'injuryStatus': 'Injured Reserve', 'injuryDate': '2026-10-04T15:00Z', 'returnDate': '2026-10-09'},
    {'playerId': 30, 'injuryStatus': 'Day-To-Day', 'injuryDate': '2026-10-04T15:00Z', 'returnDate': '2026-10-05'},
    {'playerId': 12, 'injuryStatus': 'Out', 'injuryDate': '2026-10-04T15:00Z', 'returnDate': '2026-10-20'},
])
marked = {str(r['playerId']): r for r in in_season.unavailable_goalies(
    in_season.unprojected_rows(hurt_facts, {'11', '12', '31'}, TEAM_GAMES) + PROJECTIONS, hurt_facts)}
check("a goalie on IR is out from ESPN's note to his return",
      marked['31']['outFrom'] == '2026-10-04' and marked['31']['outUntil'] == '2026-10-09', marked['31'])
check("day-to-day is a doubt, not an absence; and only goalies are marked",
      'outFrom' not in marked['30'] and 'outFrom' not in marked['12'])
stale = in_season.facts_from(ROWS, GAMES, [
    {'playerId': 31, 'injuryStatus': 'Out', 'injuryDate': '2026-10-02T15:00Z', 'returnDate': '2026-10-20'}])
check("a note older than his last game is stale - he has played since",
      'outFrom' not in in_season.unavailable_goalies(PROJECTIONS, stale)[2])
unsigned = [{'playerId': 50, 'positionCode': 'G', 'teamAbbrevs': 'CCC', 'onNhlRoster': False},
            {'playerId': 31, 'positionCode': 'G', 'teamAbbrevs': 'CCC', 'onNhlRoster': False},
            {'playerId': 51, 'positionCode': 'G', 'teamAbbrevs': 'CCC', 'onNhlRoster': True}]
off = in_season.unavailable_goalies(unsigned, FACTS, '2026-10-01')
check("a goalie on no NHL roster who has not played is out from opening night - "
      "one who has played since, or on a roster, is not",
      off[0]['outFrom'] == '2026-10-01' and off[0]['outUntil'] is None
      and 'outFrom' not in off[1] and 'outFrom' not in off[2], off)
check("the pool marks them", any(r.get('outFrom') for r in in_season.pool(PROJECTIONS, SCHEDULE, facts=hurt_facts)))

# --------------------------------------------------------------------------
print("\n=== 4. the routes, on the real data ===")

try:
    import week_planner
    from app import app
    from db import fetch_all

    week = week_planner.Week(tandem, ['W'], {'G': 1}, ['2026-10-05'], SCHEDULE)
    check("a free agent's share of starts is of his team's games, not his own appearances",
          close(week.start_share(tandem[0]), 0.6) and close(week.start_share(tandem[1]), 0.6))

    schedule = [(r['gameDate'], r['homeTeam'], r['awayTeam'])
                for r in fetch_all('SELECT "gameDate", "homeTeam", "awayTeam" FROM nhl_schedule')]
    facts = in_season.season_facts(ss.schedule_season(schedule), fresh=True)
    rows = in_season.rekeyed(fetch_all('SELECT * FROM final_projections'), facts) if facts else []
    projected = {str(r['playerId']) for r in rows}
    new = [p for k, p in (facts or {}).get('players', {}).items()
           if k not in projected and p['position'] and p['games']]
    if not new:
        print("  (no unprojected skater with games in this database - route checks skipped)")
    else:
        client = app.test_client()
        kid = max(new, key=lambda p: p['games'])
        setup = client.get('/standalone/api/setup').get_json()
        check("the player search lists a player nobody projected",
              any(str(p['playerId']) == str(kid['playerId']) for p in setup['players']), kid['name'])
        body = {'roster': [kid['playerId']], 'league_mode': 'categories',
                'categories': ['proj_goals', 'proj_shots'], 'slots': {'C': 1, 'LW': 1, 'RW': 1, 'D': 1},
                'start': '2026-10-12', 'end': '2026-10-18'}
        plan = client.post('/standalone/api/week', json=body).get_json()
        mine = [p for p in plan.get('players', []) if str(p['playerId']) == str(kid['playerId'])]
        check("a plan values him, marked as not projected",
              plan.get('status') == 'success' and mine and mine[0].get('unprojected') is True,
              (plan.get('message'), mine))
        pool_reply = client.post('/standalone/api/free-agents/pool', json=body).get_json()
        goalies = [p for p in pool_reply.get('players', []) if p.get('positionCode') == 'G']
        check("a free-agent goalie's share of starts is of his team's games - none near 90%+ by default",
              goalies and all(0 <= (p.get('startShare') or 0) <= 1 for p in goalies)
              and sorted(p.get('startShare') or 0 for p in goalies)[len(goalies) // 2] < 0.6,
              (pool_reply.get('message'), [(p['fullName'], p.get('startShare')) for p in goalies[:5]]))
        check("and those who have played carry their usage", any(p.get('usage') for p in goalies),
              [p['fullName'] for p in goalies[:5]])

    from routes.standalone_routes import _adopt_nhl_ids
    sent = {'roster': [-2, 8478402], 'out': ['-2'], 'opponent': [], 'rostered': [-2.0, None],
            'moves': [{'add': -2, 'drop': 8478402, 'date': '2026-10-20'}],
            'lineups': {'2026-10-20': ['-2', None]}, 'evaluate': {'add': -2, 'drop': None}}
    _adopt_nhl_ids(sent, {'-2': 8486067})
    check("a tab still sending a stand-in is read as the NHL id, wherever the id is",
          sent['roster'] == [8486067, 8478402] and sent['out'] == [8486067] and sent['rostered'] == [8486067, None]
          and sent['moves'][0]['add'] == 8486067 and sent['moves'][0]['drop'] == 8478402
          and sent['lineups']['2026-10-20'] == [8486067, None] and sent['evaluate']['add'] == 8486067, sent)

    rookies = [r for r in rows if r.get('formerId') is not None]
    if not rookies:
        print("  (no imported rookie has played in this database - rookie route checks skipped)")
    else:
        client = app.test_client()
        rookie = max(rookies, key=lambda r: facts['players'][str(r['playerId'])]['games'])
        nhl_id, stand_in = rookie['playerId'], rookie['formerId']
        games = facts['players'][str(nhl_id)]['games']
        setup = client.get('/standalone/api/setup').get_json()
        listed = [p['playerId'] for p in setup['players'] if p['fullName'] == rookie['fullName']]
        check(f"{rookie['fullName']} is listed once, under his NHL id, and setup says what his stand-in became",
              listed == [nhl_id] and setup['formerIds'].get(str(stand_in)) == nhl_id, (listed, setup.get('formerIds')))

        for source in ('combined', 'todate'):
            body = {'roster': [stand_in], 'league_mode': 'categories', 'source': source,
                    'categories': ['proj_goals', 'proj_shots'], 'slots': {'C': 1, 'LW': 1, 'RW': 1, 'D': 1},
                    'start': '2026-10-19', 'end': '2026-10-25'}
            plan = client.post('/standalone/api/week', json=body).get_json()
            mine = plan.get('players') or [{}]
            check(f"a plan sent his stand-in values him on his projection and his {games} games ({source})",
                  plan.get('status') == 'success' and mine[0].get('playerId') == nhl_id
                  and not mine[0].get('unprojected') and mine[0].get('seasonGames') == games
                  and mine[0].get('form') is not None, (plan.get('message'), mine[0]))

        other = next(r['playerId'] for r in rows if r.get('positionCode') == 'C' and r['playerId'] != nhl_id)
        body = {'roster': [other], 'league_mode': 'categories', 'source': 'combined',
                'categories': ['proj_goals', 'proj_shots'], 'slots': {'C': 1, 'LW': 1, 'RW': 1, 'D': 1},
                'start': '2026-10-19', 'end': '2026-10-25'}
        free = client.post('/standalone/api/free-agents/pool', json=body).get_json()
        found = [p for p in free.get('players', []) if p['fullName'] == rookie['fullName']]
        check("the free agents list him once, with his games, his form and no 'No proj'",
              len(found) == 1 and found[0]['playerId'] == nhl_id and found[0].get('seasonGames') == games
              and found[0].get('form') is not None and not found[0].get('unprojected'),
              (free.get('message'), found))
        free = client.post('/standalone/api/free-agents/pool',
                           json={**body, 'rostered': [stand_in]}).get_json()
        check("and rostered under his stand-in, he is not a free agent",
              not any(p['playerId'] == nhl_id for p in free.get('players', [])))

        cards = [client.get(f'/standalone/api/player/{i}').get_json() for i in (stand_in, nhl_id)]
        check("his card, asked for by either id, is one card: his projection and his games",
              all(c.get('status') == 'success' and c['player']['playerId'] == str(nhl_id)
                  and c.get('projection') and c.get('stats') for c in cards),
              [(c.get('status'), c.get('message')) for c in cards])
except Exception as exc:                                    # noqa: BLE001
    import traceback
    traceback.print_exc()
    check("route checks ran", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
