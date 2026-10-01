"""
Lines and power-play units, from the NHL's shift charts.

The old site's player modal showed a skater's line, his linemates and his
power-play unit. Nothing on NHL.com publishes those, so - as the old repo did -
they are worked out from `api.nhle.com/stats/rest/en/shiftcharts`, which lists
every shift of a game: who, which team, which period, from when to when.

    python game_lines.py                                # games missing lines, last 14 days
    python game_lines.py --start 2026-09-29 --end 2026-09-30

**Who was on the ice, second by second.** Laying every shift onto a per-second
grid gives both teams' skaters at each second of the game - and counting them
gives the strength, which the shift chart does not say. That is the change from
the old repo, which summed shared ice time across all strengths:

- **Lines come from 5-on-5 time only.** Summed across all strengths, a power
  play pulls together forwards from different lines and a penalty kill pairs
  two checkers, both of which blur who actually plays together.
- **Power-play units come from power-play time together.** The old repo called
  a team's top five by PP time PP1 and the next five PP2, which is right only
  when the units never mix; here PP1 is the player with the most power-play
  time and the four who shared the most of it with him, and PP2 the same again
  from those left.

A second where one side has 5 skaters and the other 3 or 4 is a power play;
both at 5 is 5-on-5. An empty net (6 against 5) is neither, and nor is a line
change caught mid-step, which only ever lasts a second or two.

**Forming lines.** Every trio of forwards (every pair of defencemen) is scored
by the 5-on-5 time its members shared, pair by pair; the best group is taken,
its members are removed, and so on - up to four lines and three pairs. A group
whose weakest pair shared under `MIN_LINE_SECONDS` is not a line. The extra
skater in an 11-and-7 game, or anyone shuffled all night, ends with no line
number but still with the teammates he played most with.

**Numbering them** is a separate question: line 1 is the line that plays the
offensive minutes, not the one that is on longest - a shutdown pair can lead
its team in 5-on-5 time together. Groups are numbered by their members' mean
5-on-5 plus power-play time, which leaves out the penalty kill.

Checked against Daily Faceoff's line combinations for all 16 teams that had
played by 10/1/2026 (both read the same last game): the same groups for 52 of
59 forward lines, 42 of 45 pairs and 25 of 30 PP units, and PP units in the
same slot 24 times. Line numbers agree less - 38 forward lines and 22 pairs -
and numbering by time together did worse still (30 of 51 and 16 of 42 on the
first 15 teams); their pair order looks partly editorial. Scoring trios by the
time all three were on at once grouped no better, and split an 11-forward
night differently from them.

One row per skater per game in `player_game_lines`, keyed like
`player_game_stats`. Only the result is stored, not the ~730 shifts a game
behind it. The nightly job fills any game in the look-back that has no lines,
so a game whose shift chart is not posted yet is simply tried again tomorrow.

Author - Jason Druckenmiller
Created - 10/1/2026
Updated - 10/1/2026
"""

import argparse
import itertools
import json
import logging
import time
from collections import Counter
from datetime import date, timedelta

import scrape_game_results
from db import engine, fetch_all, text

log = logging.getLogger(__name__)

SHIFT_URL = 'https://api.nhle.com/stats/rest/en/shiftcharts'
SHIFT = 517            # typeCode of a shift; goal events (505) share the chart
PAUSE = 0.15

FORWARDS = frozenset({'C', 'L', 'R'})
LINE_SIZE = {'F': 3, 'D': 2}
LINE_LIMIT = {'F': 4, 'D': 3}
MATES_KEPT = {'F': 4, 'D': 3}

# Every pair in a line must have shared this much 5-on-5 time. Real linemates
# spend six to twelve minutes together; two minutes keeps a few shuffled shifts
# from being called a line.
MIN_LINE_SECONDS = 120
# A team with less power-play time than this in a game has no units that game,
# and a skater needs this much of it to be on one.
MIN_TEAM_PP_SECONDS = 60
MIN_PP_SECONDS = 20
PP_UNIT_SIZE = 5
PP_UNITS = 2

CREATE = '''
CREATE TABLE IF NOT EXISTS player_game_lines (
    "playerId"      BIGINT NOT NULL,
    "gameId"        BIGINT NOT NULL,
    "gameDate"      TEXT,
    "teamAbbrev"    TEXT,
    "unit"          TEXT,
    "line"          INTEGER,
    "lineMates"     TEXT,
    "lineSeconds"   DOUBLE PRECISION,
    "mates"         TEXT,
    "evenSeconds"   DOUBLE PRECISION,
    "ppUnit"        INTEGER,
    "ppMates"       TEXT,
    "ppSeconds"     DOUBLE PRECISION,
    "teamPpSeconds" DOUBLE PRECISION,
    PRIMARY KEY ("playerId", "gameId")
)
'''
INDEX = ('CREATE INDEX IF NOT EXISTS player_game_lines_date '
         'ON player_game_lines ("gameDate")')
COLUMNS = ['playerId', 'gameId', 'gameDate', 'teamAbbrev', 'unit', 'line', 'lineMates',
           'lineSeconds', 'mates', 'evenSeconds', 'ppUnit', 'ppMates', 'ppSeconds',
           'teamPpSeconds']


# --- From shifts to lines (pure) --------------------------------------------

def clock(value):
    """'MM:SS' as seconds into the period, or None."""
    try:
        minutes, seconds = str(value).split(':')
        return int(minutes) * 60 + int(seconds)
    except (TypeError, ValueError):
        return None


def on_ice(shifts, goalies):
    """
    {team: {'even': Counter, 'pp': Counter}} - each a count of seconds per set
    of that team's skaters on the ice, at 5-on-5 and on its own power play.

    `goalies` are left out of every set: the strength is skaters against
    skaters. Each shift covers [start, end) of its period.
    """
    periods = {}
    for shift in shifts:
        if shift.get('typeCode') != SHIFT or shift.get('playerId') in goalies:
            continue
        start, end = clock(shift.get('startTime')), clock(shift.get('endTime'))
        if start is None or end is None or end <= start or not shift.get('teamAbbrev'):
            continue
        periods.setdefault(shift.get('period'), []).append(
            (shift['playerId'], shift['teamAbbrev'], start, end))

    teams = sorted({team for rows in periods.values() for _pid, team, _s, _e in rows})
    result = {team: {'even': Counter(), 'pp': Counter()} for team in teams}
    if len(teams) != 2:
        return result                       # a chart missing a side says nothing

    for rows in periods.values():
        length = max(end for _pid, _team, _start, end in rows)
        grid = {team: [set() for _ in range(length)] for team in teams}
        for pid, team, start, end in rows:
            for second in range(start, end):
                grid[team][second].add(pid)
        for second in range(length):
            counts = {team: len(grid[team][second]) for team in teams}
            for team, other in (teams, teams[::-1]):
                own, theirs = counts[team], counts[other]
                if own == 5 and theirs == 5:
                    result[team]['even'][frozenset(grid[team][second])] += 1
                elif theirs in (3, 4) and own > theirs:
                    result[team]['pp'][frozenset(grid[team][second])] += 1
    return result


def pair_seconds(sets):
    """({frozenset pair: seconds}, {player: seconds}) from a Counter of on-ice sets."""
    pairs, alone = Counter(), Counter()
    for members, seconds in sets.items():
        for player in members:
            alone[player] += seconds
        for pair in itertools.combinations(sorted(members), 2):
            pairs[frozenset(pair)] += seconds
    return pairs, alone


def form_groups(players, pairs, size, limit):
    """
    Up to `limit` disjoint groups of `size` from `players`, best first: every
    group scored by its members' pairwise shared seconds (`pairs`), taken
    greedily. A group whose weakest pair is under `MIN_LINE_SECONDS` is not a
    line. Returns the groups as sorted tuples, unnumbered - see `number_groups`.
    """
    candidates = []
    for combo in itertools.combinations(sorted(players), size):
        shared = [pairs.get(frozenset(p), 0) for p in itertools.combinations(combo, 2)]
        if min(shared) >= MIN_LINE_SECONDS:
            candidates.append((sum(shared), combo))
    candidates.sort(key=lambda c: (-c[0], c[1]))

    used, groups = set(), []
    for _score, combo in candidates:
        if used.isdisjoint(combo):
            groups.append(combo)
            used.update(combo)
            if len(groups) == limit:
                break
    return groups


def number_groups(groups, offence):
    """
    The groups in line order: by their members' mean `offence` - 5-on-5 plus
    power-play seconds - so line 1 is the one deployed to score.
    """
    def mean(group):
        return sum(offence.get(p, 0) for p in group) / len(group)
    return sorted(groups, key=lambda g: (-mean(g), g))


def together(sets, group):
    """Seconds every member of `group` was on the ice at once, in a Counter of sets."""
    members = set(group)
    return sum(seconds for on, seconds in sets.items() if members <= on)


def pp_units(sets):
    """
    [members of PP1, members of PP2] from a Counter of power-play sets. Each
    unit is seeded by the player with the most power-play time left, joined by
    the four who shared the most of it with him.
    """
    if sum(sets.values()) < MIN_TEAM_PP_SECONDS:
        return []
    pairs, alone = pair_seconds(sets)
    left = {p for p, seconds in alone.items() if seconds >= MIN_PP_SECONDS}
    units = []
    while left and len(units) < PP_UNITS:
        seed = max(sorted(left), key=lambda p: alone[p])
        mates = sorted((p for p in left - {seed} if pairs.get(frozenset((seed, p)), 0) >= MIN_PP_SECONDS),
                       key=lambda p: (-pairs.get(frozenset((seed, p)), 0), p))[:PP_UNIT_SIZE - 1]
        unit = [seed, *mates]
        units.append(unit)
        left -= set(unit)
    return units


def game_lines(shifts, positions, goalies):
    """
    One row per skater who had a shift: his line, linemates and the time they
    shared, his most frequent 5-on-5 teammates, and his power-play unit.

    `positions` is {playerId: 'C' | 'L' | 'R' | 'D'}; a skater missing from it
    is counted on the ice but put on no line. `goalies` is the set of goalie ids.
    """
    strengths = on_ice(shifts, set(goalies))
    rows = []
    for team, sets in strengths.items():
        even_pairs, even_alone = pair_seconds(sets['even'])
        skaters = {s['playerId'] for s in shifts
                   if s.get('typeCode') == SHIFT and s.get('teamAbbrev') == team
                   and s.get('playerId') not in goalies}
        unit_of = {p: ('F' if positions.get(p) in FORWARDS else 'D' if positions.get(p) == 'D' else None)
                   for p in skaters}

        team_pp = sum(sets['pp'].values())
        _pp_pairs, pp_alone = pair_seconds(sets['pp'])
        offence = {p: even_alone.get(p, 0) + pp_alone.get(p, 0) for p in skaters}

        line_of = {}
        for unit in ('F', 'D'):
            members = [p for p in skaters if unit_of[p] == unit]
            groups = form_groups(members, even_pairs, LINE_SIZE[unit], LINE_LIMIT[unit])
            for number, group in enumerate(number_groups(groups, offence), start=1):
                shared = together(sets['even'], group)
                for player in group:
                    line_of[player] = (number, [p for p in group if p != player], shared)

        pp_of = {}
        for number, unit in enumerate(pp_units(sets['pp']), start=1):
            for player in unit:
                pp_of[player] = (number, [p for p in unit if p != player])

        for player in sorted(skaters):
            unit = unit_of[player]
            mates = []
            if unit:
                mates = sorted(((p, even_pairs.get(frozenset((player, p)), 0))
                                for p in skaters if p != player and unit_of[p] == unit),
                               key=lambda m: (-m[1], m[0]))
                mates = [[p, s] for p, s in mates if s > 0][:MATES_KEPT[unit]]
            line, line_mates, line_seconds = line_of.get(player, (None, [], None))
            pp_unit, pp_mates = pp_of.get(player, (None, []))
            rows.append({
                'playerId': player, 'teamAbbrev': team, 'unit': unit,
                'line': line, 'lineMates': line_mates, 'lineSeconds': line_seconds,
                'mates': mates, 'evenSeconds': even_alone.get(player, 0),
                'ppUnit': pp_unit, 'ppMates': pp_mates,
                'ppSeconds': pp_alone.get(player, 0), 'teamPpSeconds': team_pp,
            })
    return rows


# --- Fetching and storing ----------------------------------------------------

def fetch_shifts(game_id):
    """Every row of one game's shift chart."""
    response = scrape_game_results._get(SHIFT_URL, {'cayenneExp': f'gameId={int(game_id)}'})
    return response.json().get('data', [])


def _ensure_table(conn):
    conn.execute(text(CREATE))
    conn.execute(text(INDEX))


def _game_players(game_ids):
    """{gameId: (gameDate, {playerId: positionCode}, {goalie ids})} from player_game_stats."""
    games = {}
    for row in fetch_all('SELECT "gameId", "gameDate", "playerId", "positionCode" '
                         'FROM player_game_stats WHERE "gameId" = ANY(:ids)',
                         {'ids': [int(g) for g in game_ids]}):
        day, positions, goalies = games.setdefault(row['gameId'], (row['gameDate'], {}, set()))
        if row['positionCode']:
            positions[row['playerId']] = row['positionCode']
        else:
            goalies.add(row['playerId'])
    return games


def scrape_games(game_ids):
    """Fetch, work out and write lines for these games. Returns the rows written."""
    players = _game_players(game_ids)
    written = 0
    for game_id in sorted(players):
        day, positions, goalies = players[game_id]
        shifts = fetch_shifts(game_id)
        rows = game_lines(shifts, positions, goalies)
        if not rows:
            # Not posted yet, most likely; the next night's look-back retries it
            log.warning('No shifts for game %s (%s) - left for a later run.', game_id, day)
            continue
        for row in rows:
            row.update(gameId=game_id, gameDate=day)
            for key in ('lineMates', 'mates', 'ppMates'):
                row[key] = json.dumps(row[key])
        with engine.begin() as conn:
            _ensure_table(conn)
            conn.execute(text('DELETE FROM player_game_lines WHERE "gameId" = :id'), {'id': game_id})
            conn.execute(text(
                f'INSERT INTO player_game_lines ({", ".join(chr(34) + c + chr(34) for c in COLUMNS)}) '
                f'VALUES ({", ".join(":" + c for c in COLUMNS)})'), rows)
        written += len(rows)
        time.sleep(PAUSE)
    return written


def fill_missing(since, until):
    """
    Lines for every game between `since` and `until` that has results but no
    lines. Returns the rows written.
    """
    with engine.begin() as conn:
        _ensure_table(conn)
        missing = [r[0] for r in conn.execute(text(
            'SELECT DISTINCT s."gameId" FROM player_game_stats s '
            'WHERE s."gameDate" BETWEEN :since AND :until AND NOT EXISTS '
            '(SELECT 1 FROM player_game_lines l WHERE l."gameId" = s."gameId")'),
            {'since': str(since), 'until': str(until)})]
    if not missing:
        return 0
    log.info('%d game(s) %s..%s without lines - working them out.', len(missing), since, until)
    return scrape_games(missing)


# --- Reading -------------------------------------------------------------------

def _decode(row):
    row = dict(row)
    for key in ('lineMates', 'mates', 'ppMates'):
        try:
            row[key] = json.loads(row.get(key) or '[]')
        except (TypeError, ValueError):
            row[key] = []
    return row


def recent(player_ids, season, games=None):
    """
    {playerId: [rows, newest first]} for one season - every game, or the last
    `games`. [] for a player with none, and {} when the table does not exist yet.
    """
    ids = sorted({int(i) for i in player_ids if str(i).lstrip('-').isdigit()})
    if not ids or not season:
        return {}
    try:
        rows = fetch_all(
            'SELECT * FROM player_game_lines WHERE "playerId" = ANY(:ids) '
            'AND "gameId" >= :first AND "gameId" < :next ORDER BY "gameDate" DESC, "gameId" DESC',
            {'ids': ids, 'first': season * 1_000_000, 'next': (season + 1) * 1_000_000})
    except Exception:                             # noqa: BLE001
        log.warning('No player_game_lines yet - no lines to show.')
        return {}
    found = {}
    for row in rows:
        kept = found.setdefault(str(row['playerId']), [])
        if games is None or len(kept) < games:
            kept.append(_decode(row))
    return found


def latest(player_ids, season):
    """
    {playerId: {line, unit, date, ppUnit, ppDate}}: his line in his last game,
    and his power-play unit in his last game his team had a power play - a
    night without one says nothing about which unit he is on.
    """
    summary = {}
    for player, rows in recent(player_ids, season).items():
        last = rows[0]
        powered = next((r for r in rows if (r.get('teamPpSeconds') or 0) >= MIN_TEAM_PP_SECONDS), None)
        summary[player] = {
            'line': last.get('line'), 'unit': last.get('unit'), 'date': last.get('gameDate'),
            'ppUnit': powered.get('ppUnit') if powered else None,
            'ppDate': powered.get('gameDate') if powered else None,
        }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', help='First game date (YYYY-MM-DD).')
    parser.add_argument('--end', help='Last game date (YYYY-MM-DD).')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=14)
    log.info('Lines written: %d rows.', fill_missing(start, end))


if __name__ == '__main__':
    main()
