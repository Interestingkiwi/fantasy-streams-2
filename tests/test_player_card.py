"""
Tests for the player card: season-to-date stats (season_stats.py) and the
card built from them (player_card.py), then its route.

No database for the maths - games are dicts shaped like `player_game_stats`
rows. What is pinned is what a reader could be misled by:

- a ratio over a window is rebuilt from its summed parts, never an average of
  per-game ratios, and a goalie's GAA is over his own seconds;
- a stat a window's games were scraped without is missing, not zero;
- a trend window appears only once he has played that many games, and its
  arrows are `player_form`'s standard-error test, counting stats only;
- a goalie's starts count only team games already scraped, his rest is days
  since his previous appearance, and his first game is in no rest split;
- an opponent's rank 1 is the kindest to this player - most goals allowed for
  a skater; fewest scored but most shots for a goalie.

Author - Jason Druckenmiller
Created - 10/1/2026
Updated - 10/1/2026
"""

import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("FLASK_SECRET_KEY", "player-card-test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import player_card as pc                                    # noqa: E402
import player_form                                          # noqa: E402
import season_stats as ss                                   # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def skater_game(day, home=True, **stats):
    base = {'gameId': int(day.replace('-', '')), 'gameDate': day, 'positionCode': 'C',
            'homeRoad': 'H' if home else 'R', 'opponentTeamAbbrev': 'MTL', 'teamAbbrev': 'TOR',
            'goals': 0, 'assists': 0, 'points': 0, 'shots': 0, 'missedShots': 0,
            'shotAttemptsBlocked': 0, 'timeOnIce': 1000, 'ppTimeOnIce': 0, 'teamPpTimeOnIce': 120}
    base.update(stats)
    return base


def goalie_game(day, home=True, **stats):
    base = {'gameId': int(day.replace('-', '')), 'gameDate': day, 'positionCode': None,
            'homeRoad': 'H' if home else 'R', 'opponentTeamAbbrev': 'MTL', 'teamAbbrev': 'BOS',
            'gamesStarted': 1, 'wins': 0, 'goalsAgainst': 2, 'shotsAgainst': 30, 'saves': 28,
            'timeOnIce': 3600}
    base.update(stats)
    return base


# --------------------------------------------------------------------------
print("\n=== 1. a window's line ===")

two = [skater_game('2026-10-01', goals=1, shots=3), skater_game('2026-10-03', goals=0, shots=0)]
cells = ss.line(two, ss.SKATER_STATS)
check("counting stats total and divide by games",
      cells['G'] == {'total': 1, 'perGame': 0.5}, cells['G'])
check("a ratio is rebuilt from summed parts - one goal in three shots is 33%, not an average with a shotless game",
      abs(cells['SH%']['total'] - 100 / 3) < 1e-9 and cells['SH%']['perGame'] == cells['SH%']['total'], cells['SH%'])
check("a ratio with nothing under it is missing, not zero",
      ss.line([skater_game('2026-10-01')], ss.SKATER_STATS)['SH%']['total'] is None)
check("shot attempts are shots, misses and attempts blocked",
      ss.line([skater_game('2026-10-01', shots=2, missedShots=3, shotAttemptsBlocked=1)],
              ss.SKATER_STATS)['ATT']['total'] == 6)
old = [{k: v for k, v in g.items() if k not in ('missedShots', 'shotAttemptsBlocked')} for g in two]
check("a stat the window's games were scraped without is None, not a misleading zero",
      ss.line(old, ss.SKATER_STATS)['MISS'] is None and ss.line(old, ss.SKATER_STATS)['ATT'] is None)
check("PP share is his PP seconds over his team's",
      ss.line([skater_game('2026-10-01', ppTimeOnIce=60)], ss.SKATER_STATS)['PP%']['total'] == 50)

pulled = [goalie_game('2026-10-01', goalsAgainst=4, shotsAgainst=16, saves=12, timeOnIce=1800),
          goalie_game('2026-10-03', goalsAgainst=0, shotsAgainst=30, saves=30, timeOnIce=3600)]
gcells = ss.line(pulled, ss.GOALIE_STATS)
check("a goalie's GAA is over his own seconds, so a pulled start counts its real half hour",
      abs(gcells['GAA']['total'] - 4 * 3600 / 5400) < 1e-9, gcells['GAA'])
check("save percentage is saves over shots, summed", abs(gcells['SVpct']['total'] - 42 / 46) < 1e-9)
check("a goalie row is read as a goalie", ss.is_goalie(pulled) and not ss.is_goalie(two))
check("one game's line is its totals", ss.game_line(two[0])['G'] == 1 and ss.game_line(pulled[0])['GA'] == 4)


# --------------------------------------------------------------------------
print("\n=== 2. the windows ===")

def season(n, recent_goals=0, recent=0):
    games = []
    for i in range(n):
        day = date.fromordinal(date(2026, 10, 1).toordinal() + i).isoformat()
        hot = i >= n - recent
        games.append(skater_game(day, home=i % 2 == 0, goals=recent_goals if hot else 0,
                                 shots=3, points=recent_goals if hot else 0))
    return games

four = ss.windows(season(4))
check("four games: no trend window yet, only season, home and road",
      [c['key'] for c in four['columns']] == ['season', 'home', 'road'], [c['key'] for c in four['columns']])
six = ss.windows(season(6))
check("six games: the last five appears", [c['key'] for c in six['columns']] == ['season', 'L5', 'home', 'road'])
check("home and road carry their game counts",
      [c['games'] for c in six['columns'] if c['key'] in ('home', 'road')] == [3, 3])

streak = ss.windows(season(30, recent_goals=1, recent=5))
rows = {r['code']: r for r in streak['rows']}
check("a scoring streak past the standard-error test is an up arrow on the last five",
      rows['G']['marks'].get('L5') == 'up', rows['G']['marks'])
check("the test is player_form's own",
      rows['G']['marks'].get('L5') == player_form.trend([ss._n(g['goals']) for g in season(30, 1, 5)], 5))
check("ratios carry no arrows - five games of shooting percentage is mostly noise",
      rows['SH%']['marks'] == {}, rows['SH%']['marks'])
check("a steady stat has none either", rows['SOG']['marks'] == {})
check("a window as long as his season is not compared with itself",
      'L20' not in ss.windows(season(20, recent_goals=1, recent=5))['rows'][0]['marks'])
check("lower-is-better stats say so, for the arrow's colour",
      {r['code'] for r in ss.windows(pulled)['rows'] if r['lowerIsBetter']} >= {'GA', 'GAA', 'L'})

totals, _season = ss.totals([])
check("season totals for nobody are empty, not an error", totals == {})


# --------------------------------------------------------------------------
print("\n=== 3. the projection beside them ===")

skater = pc.projection_line({'positionCode': 'C', 'projectedGames': 80, 'proj_goals': 40, 'proj_shots': 200,
                             'proj_ppPoints': 30, 'proj_ppGoals': 10,
                             'proj_totalFaceoffWins': 600, 'proj_totalFaceoffLosses': 400})
check("a skater's projection is per game", skater['G'] == 0.5 and skater['SOG'] == 2.5, skater)
check("PPA is PPP less PPG", skater['PPA'] == 0.25)
check("ratios come from the projection's own parts", skater['SH%'] == 20 and skater['FO%'] == 60)
goalie = pc.projection_line({'positionCode': 'G', 'projectedGames': 60, 'proj_gamesStarted': 50,
                             'proj_wins': 30, 'proj_savePct': 0.91, 'proj_goalsAgainstAverage': 2.7})
check("a goalie's is per start, as daily_value rates him", goalie['W'] == 0.6, goalie)
check("and his ratios are carried as projected", goalie['SVpct'] == 0.91 and goalie['GAA'] == 2.7)
check("no projection, no line", pc.projection_line(None) == {})


# --------------------------------------------------------------------------
print("\n=== 4. a goalie's starts ===")

schedule = [('2026-09-29', 'BOS', 'NYR'), ('2026-10-01', 'MTL', 'BOS'), ('2026-10-02', 'BOS', 'TOR'),
            ('2026-10-04', 'BOS', 'OTT'), ('2026-10-05', 'WPG', 'BOS'), ('2026-10-01', 'NYR', 'TBL')]
games = [goalie_game('2026-09-29', wins=1), goalie_game('2026-10-01', home=False),
         goalie_game('2026-10-02', wins=1)]
usage = pc.goalie_usage(games, 'BOS', schedule, date(2026, 10, 4), through='2026-10-02')
check("starts are counted against his team's games, not the league's",
      usage['season'] == {'teamGames': 3, 'starts': 3}, usage['season'])
early = pc.goalie_usage(games[:1], 'BOS', schedule, date(2026, 10, 4), through='2026-09-29')
check("a team game not yet scraped is not one he missed", early['season']['teamGames'] == 1, early['season'])
check("his next game is his team's next, with the rest since his last appearance",
      usage['next']['date'] == '2026-10-04' and usage['next']['rest'] == 1 and usage['next']['opponent'] == 'OTT',
      usage['next'])
check("the second night of a back-to-back is flagged",
      pc.goalie_usage(games, 'BOS', schedule, date(2026, 10, 5))['next']['teamBackToBack'])
check("rest splits leave out his first game, which has no previous one",
      usage['byRest']['0']['games'] == 1 and usage['byRest']['1']['games'] == 1
      and usage['byRest']['2+']['games'] == 0, usage['byRest'])
check("venue splits are his appearances at each",
      usage['byVenue']['home']['games'] == 2 and usage['byVenue']['road']['games'] == 1)


# --------------------------------------------------------------------------
print("\n=== 5. opponents, lines and the log ===")

teams = [{'statWindow': 'season', 'teamCode': 'AAA', 'gamesPlayed': 10, 'goalsAgainstPerGame': 4.0,
          'goalsForPerGame': 2.0, 'shotsForPerGame': 35, 'shotsAgainstPerGame': 30,
          'penaltyKillPct': 0.75, 'powerPlayPct': 0.15},
         {'statWindow': 'season', 'teamCode': 'BBB', 'gamesPlayed': 10, 'goalsAgainstPerGame': 2.0,
          'goalsForPerGame': 4.0, 'shotsForPerGame': 25, 'shotsAgainstPerGame': 25,
          'penaltyKillPct': 0.85, 'powerPlayPct': 0.25},
         {'statWindow': 'season', 'teamCode': 'CCC', 'gamesPlayed': 0, 'goalsAgainstPerGame': None}]
as_skater = pc.opponent_table(teams, goalie=False)['season']
check("for a skater, rank 1 is the team that allows the most goals",
      as_skater['teams']['AAA']['gaRank'] == 1 and as_skater['teams']['BBB']['gaRank'] == 2)
check("and the weakest penalty kill", as_skater['teams']['AAA']['pkRank'] == 1)
check("a team that has not played is not ranked", 'CCC' not in as_skater['teams'] and as_skater['count'] == 2)
as_goalie = pc.opponent_table(teams, goalie=True)['season']['teams']
check("for a goalie, rank 1 scores least...", as_goalie['AAA']['gfRank'] == 1)
check("...but shoots most, since shots are saves", as_goalie['AAA']['sfRank'] == 1)

week = pc.week_games('BOS', schedule, '2026-10-01', '2026-10-04', [], goalie=True)
check("the week lists his team's games in it and no one else's",
      [g['date'] for g in week['games']] == ['2026-10-01', '2026-10-02', '2026-10-04'], week['games'])
check("with home and road the right way round", [g['home'] for g in week['games']] == [False, True, True])
check("no week asked for, no schedule", pc.week_games('BOS', schedule, None, None, [], False) is None)

line_rows = [
    {'gameId': 3, 'gameDate': '2026-10-04', 'unit': 'F', 'line': 2, 'lineMates': [11, 12], 'lineSeconds': 400,
     'mates': [[11, 500], [12, 450], [13, 90]], 'evenSeconds': 700, 'ppUnit': None, 'ppMates': [],
     'ppSeconds': 0, 'teamPpSeconds': 0},
    {'gameId': 2, 'gameDate': '2026-10-02', 'unit': 'F', 'line': 1, 'lineMates': [11, 14], 'lineSeconds': 500,
     'mates': [], 'evenSeconds': 800, 'ppUnit': 1, 'ppMates': [11, 15, 16, 17], 'ppSeconds': 150,
     'teamPpSeconds': 240},
]
summary = pc.lines_summary(line_rows, {11: 'Eleven', 12: 'Twelve', 13: 'Thirteen'}, {3: 'OTT', 2: 'TOR'})
check("the line is his last game's", summary['line'] == 2 and summary['opponent'] == 'OTT')
check("linemates carry their time with him",
      [(m['name'], m['seconds']) for m in summary['lineMates']] == [('Eleven', 500), ('Twelve', 450)])
check("others he skated with leave out his linemates", [m['name'] for m in summary['others']] == ['Thirteen'])
check("his PP unit is from his last game with a power play",
      summary['pp']['unit'] == 1 and summary['pp']['date'] == '2026-10-02')
check("an unnamed mate is still listed, by number", summary['pp']['mates'][1]['name'] == '#15')
check("a game his team had no power play says so rather than claiming no unit",
      summary['history'][0]['ppUnit'] is None and summary['history'][0]['teamPp'] is False)
check("no lines, no summary", pc.lines_summary([], {}, {}) is None)

log = pc.game_log(season(12), {})
check("the log is his last ten, newest first",
      len(log) == 10 and log[0]['date'] > log[-1]['date'] and log[0]['date'] == season(12)[-1]['gameDate'])


# --------------------------------------------------------------------------
print("\n=== 6. the route ===")

try:
    from app import app
    from db import fetch_all

    client = app.test_client()
    check("an unknown player is a 404", client.get('/standalone/api/player/1').status_code == 404)
    check("a malformed week is a 400",
          client.get('/standalone/api/player/8478402?start=2026-10-01&end=soon').status_code == 400)
    played = fetch_all('SELECT "playerId" FROM player_game_stats WHERE "positionCode" IS NOT NULL '
                       'ORDER BY "gameId" DESC, "playerId" LIMIT 1')
    if played:
        pid = played[0]['playerId']
        data = client.get(f'/standalone/api/player/{pid}?start=2026-09-28&end=2026-10-04').get_json()
        check("a skater who has played gets a card",
              data.get('status') == 'success' and data['player']['playerId'] == str(pid), data.get('message'))
        check("with his stats table, log and season label",
              data['stats']['columns'][0]['key'] == 'season' and data['log'] and data['season'], data.get('season'))
        check("a skater's card has lines and no starts", data['starts'] is None and 'lines' in data)
        check("and the week's schedule when one is asked for", data['week'] is not None)
    goalie = fetch_all('SELECT "playerId" FROM final_projections WHERE "positionCode" = \'G\' LIMIT 1')
    if goalie:
        data = client.get(f'/standalone/api/player/{goalie[0]["playerId"]}').get_json()
        check("a goalie's card has starts and no lines",
              data.get('status') == 'success' and data['lines'] is None and data['player']['goalie'],
              data.get('message'))
        check("without a week there is no schedule", data['week'] is None)
except Exception as exc:                                    # noqa: BLE001
    check("route checks ran", False, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------
print("\n==============================================")
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for label in FAILURES:
        print(f"  - {label}")
    sys.exit(1)
print("All checks passed.")
