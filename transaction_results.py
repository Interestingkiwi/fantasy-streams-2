"""
Transaction Results: what each pickup brought a team, what the player dropped
for him would have brought instead, and the weeks a move decided.

Season History's third view, beside the transactions themselves and bench
points. Pure: the route reads the league's days, weeks and transactions (the
same shapes bench points uses - `yahoo_league_api` for a public league,
`bench_lineups` for a private one) and the dropped players' games from our own
NHL game data, and this works out the rest.

**A move is an add and the drop it paid for.** Usually one transaction
(add/drop, or a waiver claim with its drop). A manager who drops first and
adds after makes two, so a lone add and a lone drop by the same team within a
day of each other are paired too (`paired: 'nearby'`). An add with nothing
dropped and a drop with nothing added stand alone. Trades are not moves here;
`trade_results` judges them, on the same nights and spots.

**What the added player brought is what Yahoo counted:** his line on the days
he sat in a starting slot and played, from the day he first appears on the
team's roster (the transaction day, or a day or two later for one effective
tomorrow) until the day he is gone. Days he played on the bench are counted
as games, not as production.

**What the dropped player would have brought** is his NHL game line, over the
same days, on the nights a lineup spot he could fill was open - judged from
what the team actually did that night:

- `own` - the added player's own starting slot, if he could play it: without
  the move, it was his.
- `empty` - a starting slot left empty (the slot counts say how many there
  are; Yahoo lists only the filled ones).
- `idle` - a starter in a slot he could play who had no game that night.

A night he played with every spot he fits filled by someone who also played
counts as a game but adds nothing: he would have sat. A lone drop is judged
until the team's next pickup, which filled the spot he left.

**A week's swing** takes the added player's started lines that week off the
team's real totals, puts the dropped player's open-spot lines on, and scores
every category against the opponent again - the same arithmetic as a bench
swap (`bench_points.swapped_totals`), ratios rebuilt from their parts. A
category the real week won or tied that the counterfactual would not have is
a gain; the reverse a cost, a tie counting half (`net`). Points leagues get
contributions but no swings, as with bench points.

**A move counts in its own week only** (10/19/2026): the week it was made
in, and - for a pickup made on the Saturday or Sunday ending a week (its last
two days), kept into the next week and started there - that next week too
(`carriedInto`); a move made before the season, the first week the same way.
Its starts, its dropped player's open games and its categories go to those
weeks and no others. A pickup kept longer is simply the team's player after
that: the weeks he changed later are still worked out, for the team being
looked at, and marked `later`, but never counted. Counted every week of his
stay, a pickup from opening night kept piling up a manager's weeks all
season, which is no measure of the moves made in any one of them.

**A team's week is scored once, with all its moves undone together**
(`undone`, `together`). Each move's own swing says what that move alone
decided, so two moves that each alone won assists both say so - and summed,
the team was credited with two categories for one. Its categories won, cost
and net are therefore the week with every one of the week's own moves
undone at once on each night, each spot and each bench stand-in used once,
and scored against the opponent a single time. On the 2025-26 test league a
category was claimed by more than one move in 172 of the 236 team-weeks a
move changed. Undone together, a chain of them is followed back (`kept`):
X for Y on Monday and Z for X on Thursday keep Y, not X, since X was never
there - until 10/19/2026 X's games after his drop came back, and Sauce Boss's
week 3 in league 5848 lost the assists category to two of Trenin's. A
quarter of the test league's moves drop a player the team picked up earlier
the same week.

**A start the dropped player could not have made is not left empty.** On a
night the pickup started and played and the dropped player could not take his
slot, a teammate who played on the bench that night and could fill it would
have - the busiest of them in the league's counting categories - and his line
goes into the week without the move (`stand_in`, `covered`). Without this,
every pickup was credited with his whole line on nights the team had someone
else ready: on the 2025-26 test league every one of twelve teams came out
strongly ahead on its moves, streamers most of all.

Author - Jason Druckenmiller
Created - 10/5/2026
Updated - 10/22/2026
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import bench_points as bp

EASTERN = ZoneInfo("America/New_York")
POOLS = frozenset({"freeagents", "waivers"})
# A lone add and a lone drop this close together are one move made in two steps
PAIR_SECONDS = 24 * 3600
# An add effective tomorrow first shows on the roster a day later; a claim
# processed overnight, the same day
ARRIVAL_DAYS = 2
# The end of a week a pickup can be made in for the next one: its last two
# days, Saturday and Sunday in a Monday-Sunday week
WEEKEND_DAYS = 2
OUTCOME = bp.OUTCOME
# A category's share of a week, as head-to-head standings count it: a tie is half
CATEGORY_SHARE = {1: 1.0, 0: 0.5, -1: 0.0}


def tx_date(seconds):
    """A transaction's day, as Yahoo keeps it: US Eastern."""
    return datetime.fromtimestamp(int(seconds), EASTERN).date().isoformat()


def _plus(iso, days):
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def positions_of(text_value):
    """'C,LW' -> ['C', 'LW']."""
    return [p.strip() for p in str(text_value or "").split(",") if p.strip()]


# ------------------------------------------------------------------- pairing

def pair(transactions):
    """
    Every move, oldest first: {team, time, date, added, dropped, dropDate,
    claim, paired} - `added` / `dropped` a Yahoo player id or None, `claim`
    whether the add came off waivers, `paired` 'transaction' (one add/drop),
    'nearby' (an add and a drop made separately within a day) or None.
    """
    moves, lone_adds, lone_drops = [], [], []
    ordered = sorted((t for t in transactions or []
                      if t.get("type") != "trade" and t.get("time")), key=lambda t: t["time"])
    for t in ordered:
        adds = [(pid, frm, to) for pid, frm, to in t.get("moves") or []
                if frm in POOLS and to and to not in POOLS]
        drops = [(pid, frm, to) for pid, frm, to in t.get("moves") or []
                 if to in POOLS and frm and frm not in POOLS]
        for (a_id, a_from, a_team), (d_id, _d_team, _to) in zip(adds, drops):
            moves.append({"team": str(a_team), "time": t["time"], "added": str(a_id),
                          "dropped": str(d_id), "dropTime": t["time"],
                          "claim": a_from == "waivers", "paired": "transaction"})
        for a_id, a_from, a_team in adds[len(drops):]:
            lone_adds.append({"team": str(a_team), "time": t["time"], "added": str(a_id),
                              "claim": a_from == "waivers"})
        for d_id, d_team, _to in drops[len(adds):]:
            lone_drops.append({"team": str(d_team), "time": t["time"], "dropped": str(d_id)})

    used = set()
    for add in lone_adds:
        near = [(abs(add["time"] - drop["time"]), n) for n, drop in enumerate(lone_drops)
                if n not in used and drop["team"] == add["team"]
                and abs(add["time"] - drop["time"]) <= PAIR_SECONDS]
        if near:
            n = min(near)[1]
            used.add(n)
            moves.append({**add, "dropped": lone_drops[n]["dropped"], "dropTime": lone_drops[n]["time"],
                          "paired": "nearby"})
        else:
            moves.append({**add, "dropped": None, "dropTime": None, "paired": None})
    for n, drop in enumerate(lone_drops):
        if n not in used:
            moves.append({"team": drop["team"], "time": drop["time"], "added": None, "claim": False,
                          "dropped": drop["dropped"], "dropTime": drop["time"], "paired": None})

    moves.sort(key=lambda m: m["time"])
    for move in moves:
        move["date"] = tx_date(move["time"])
        move["dropDate"] = tx_date(move["dropTime"]) if move.get("dropTime") else None
    return moves


# ------------------------------------------------------------------ the days

class Season:
    """The league's days, indexed: who sat where on each date, with his line."""

    def __init__(self, days):
        self.dates = sorted(d["date"] for d in days)
        self.rows = {}         # {date: {team: {yahooId: (slot, positions, stats)}}}
        self.team_of = {}      # {date: {yahooId: team}}
        for d in days:
            teams, where = {}, {}
            for team, rows in (d.get("teams") or {}).items():
                teams[str(team)] = {str(r[0]): (r[1], r[2] or [], r[3] or {}) for r in rows}
                for r in rows:
                    where[str(r[0])] = str(team)
            self.rows[d["date"]] = teams
            self.team_of[d["date"]] = where

    def roster(self, when, team):
        return self.rows.get(when, {}).get(team, {})

    def on_team(self, when, player, team):
        return self.team_of.get(when, {}).get(player) == team

    def tenure(self, player, team, since):
        """The days `player` was on `team` from the move on `since`: from his
        first day there (within ARRIVAL_DAYS) until the first day he was not."""
        start = max(since, self.dates[0]) if self.dates else since
        latest = _plus(start, ARRIVAL_DAYS)
        days, arrived = [], False
        for when in self.dates:
            if when < start:
                continue
            if self.on_team(when, player, team):
                arrived = True
                days.append(when)
            elif arrived or when > latest:
                break
        return days


def open_spot(roster, positions, own_slot, slots, added=None):
    """
    (kind, slot) of a lineup spot a player eligible at `positions` could have
    taken in a team's real lineup that night, or None. See the docstring.
    """
    goalie = bp.is_goalie(positions)

    def fits(slot):
        return (slot == "G") == goalie and bp.can_fill(positions, slot)

    if own_slot and own_slot not in bp.NOT_STARTING and fits(own_slot):
        return "own", own_slot
    filled = Counter(slot for slot, _p, _s in roster.values() if slot not in bp.NOT_STARTING)
    for slot, count in (slots or {}).items():
        if slot not in bp.NOT_STARTING and filled.get(slot, 0) < (count or 0) and fits(slot):
            return "empty", slot
    for player, (slot, _p, stats) in roster.items():
        if player != added and slot not in bp.NOT_STARTING and not bp.played(stats) and fits(slot):
            return "idle", slot
    return None


def undone(roster, active, slots, categories):
    """
    One night of a team with every move active that night undone at once:
    (the pickups' started lines, the lines that would have been played
    instead). `active` is [(added, his row or None, the dropped player's
    positions, his line or None)], in the order the moves were made.

    Each spot is filled once, which judging the moves one at a time cannot
    promise: a dropped player takes his own pickup's slot, an empty one, an
    idle starter's or another pickup's, in that order, and a pickup's start
    nobody dropped could take goes to a teammate who played on the bench,
    each teammate once. Another pickup is never that teammate - his move is
    undone too. With one move active it is exactly `open_spot` and `stand_in`.
    """
    pickups = {added for added, *_ in active if added}
    vacated, minus = {}, []
    for added, row, _positions, _line in active:
        if row and row[0] not in bp.NOT_STARTING:
            vacated[added] = row[0]
            if bp.played(row[2]):
                minus.append(row[2])
    filled = Counter(slot for slot, _p, _s in roster.values() if slot not in bp.NOT_STARTING)
    empty = {slot: (count or 0) - filled.get(slot, 0) for slot, count in (slots or {}).items()
             if slot not in bp.NOT_STARTING}
    idle = {player: slot for player, (slot, _p, stats) in roster.items()
            if player not in pickups and slot not in bp.NOT_STARTING and not bp.played(stats)}

    def fitter(positions):
        goalie = bp.is_goalie(positions)
        return lambda slot: (slot == "G") == goalie and bp.can_fill(positions, slot)

    # Every dropped player's claim on his own pickup's slot first, so another's
    # last resort never takes it from him
    plus, refilled, waiting, seen = [], set(), [], set()
    for added, _row, positions, line in active:
        # Two chains of moves leading back to one player bring him back once
        if not line or id(line) in seen:
            continue
        seen.add(id(line))
        if added in vacated and fitter(positions)(vacated[added]):
            refilled.add(added)
            plus.append(line)
        else:
            waiting.append((positions, line))
    for positions, line in waiting:
        fits = fitter(positions)
        if (slot := next((s for s, n in empty.items() if n > 0 and fits(s)), None)):
            empty[slot] -= 1
        elif (player := next((p for p, s in idle.items() if fits(s)), None)):
            del idle[player]
        elif (other := next((a for a, s in vacated.items() if a not in refilled and fits(s)), None)):
            refilled.add(other)
        else:
            continue
        plus.append(line)

    bench = {p: r for p, r in roster.items() if p not in pickups}
    for added, slot in vacated.items():
        if added in refilled or not bp.played(roster[added][2]):
            continue
        cover = stand_in(bench, slot, categories)
        if cover:
            del bench[cover[0]]
            plus.append(cover[1])
    return minus, plus


def stand_in(roster, slot, categories):
    """
    (yahooId, line) of the teammate who would have taken `slot` on a night the
    pickup started in it and the dropped player could not: one who played
    from the bench and could fill it, the busiest in the league's counting
    categories if there were several. None when nobody could.
    """
    best = None
    for player, (bench_slot, positions, stats) in roster.items():
        if bench_slot != "BN" or not bp.played(stats):
            continue
        if bp.is_goalie(positions) != (slot == "G") or not bp.can_fill(positions, slot):
            continue
        busy = sum(float(stats.get(c["id"]) or 0) for c in categories if c["id"] not in bp.RATIOS)
        if best is None or busy > best[0]:
            best = (busy, player, stats)
    return (best[1], best[2]) if best else None


# ------------------------------------------------------------------- the lines

def _line_minutes(line):
    """Goaltending minutes in one game's line: game data carries its seconds."""
    if line.get("toi"):
        return line["toi"] / 60.0
    if line.get(bp.GA) is None and line.get(bp.GAA) is None:
        return 0.0
    return bp.minutes(line.get(bp.GA), line.get(bp.GAA))


def add_line(total, line):
    """Adds one game's line into a running total: counts summed, ratios left to
    be rebuilt, goaltending minutes carried as `min`."""
    for stat, value in (line or {}).items():
        if stat in bp.RATIOS or stat == "toi" or value is None:
            continue
        try:
            total[stat] = total.get(stat, 0.0) + float(value)
        except (TypeError, ValueError):
            continue
    total["min"] = total.get("min", 0.0) + _line_minutes(line or {})
    return total


def shown(total, categories):
    """A total in the league's categories, ratios rebuilt, zeroes left out."""
    out = {}
    for c in categories:
        stat = c["id"]
        if stat == bp.SV_PCT:
            value = total[bp.SV] / total[bp.SA] if total.get(bp.SA) and bp.SV in total else None
        elif stat == bp.GAA:
            value = total[bp.GA] * 60.0 / total["min"] if total.get("min") and bp.GA in total else None
        elif stat == bp.SH_PCT:
            value = total.get(bp.GOALS, 0.0) / total[bp.SHOTS] if total.get(bp.SHOTS) else None
        else:
            value = total.get(stat)
        if value is not None and (value or stat in bp.RATIOS):
            out[stat] = bp._compact(value)
    return out


def _compact_line(stats, categories):
    """One night, as bench points shows one: only what he did."""
    return {c["id"]: bp._compact(stats[c["id"]]) for c in categories if stats.get(c["id"])}


def swing(totals, rival, minus, plus, categories, rival_without=None):
    """
    What a move did to a week: `minus` (the added player's started lines) off
    the team's real totals, `plus` (the dropped player's open-spot lines) on,
    every category scored against the opponent again. {with, without, gains,
    costs, net}, each gain or cost [category, with the move, without it], and
    `net` the categories the move was worth, a tie counting half.
    `rival_without` is the opponent's totals without it, when the move
    changed his side too (a trade between the two); otherwise his real ones.
    """
    before, results = bp.record(totals, rival, categories)
    after, after_results = bp.record(bp.swapped_totals(totals, minus, plus, categories),
                                     rival if rival_without is None else rival_without, categories)
    gains, costs, net = [], [], 0.0
    for c in categories:
        real, other = results[c["id"]], after_results[c["id"]]
        if real is None and other is None:
            continue
        real, other = real or 0, other or 0
        if real != other:
            (gains if real > other else costs).append([c["id"], OUTCOME[real], OUTCOME[other]])
            net += CATEGORY_SHARE[real] - CATEGORY_SHARE[other]
    return {"with": before, "without": after, "gains": gains, "costs": costs, "net": net}


# ----------------------------------------------------------------- the season

def _tally():
    """
    One team's moves over a season or a week: `moves` (every pairing, a lone
    drop included), `adds` (only those that picked someone up - what the
    table counts), the pickups' `starts` and the dropped players' open games
    (`dropGames`) on that span's nights, and the categories its moves won,
    cost and were worth together (`gained`, `lost`, `net` - each week scored
    once with all of them undone, never each move's swing summed). A week's
    moves are those made in it, and those made the weekend before whose
    pickup started for the team in it; everything a week counts comes from
    them alone, and a season is its weeks added up (adds aside: a carried
    pickup is one add in the season and one in each of his two weeks).
    """
    return {"moves": 0, "adds": 0, "starts": 0, "dropGames": 0, "gained": 0, "lost": 0, "net": 0.0}


def _rounded(tally):
    return {**tally, "net": round(tally["net"], 1)}


def _slim(record):
    """Without the fields that say nothing - None, False, [] - which a season
    of moves would otherwise repeat a thousand times. Zeroes stay."""
    return {k: v for k, v in record.items() if v is not None and v is not False and v != []}


def index_weeks(weeks):
    """(the weeks [{week, start, end, status}], {(week, team): opponent},
    {(week, team): Yahoo's totals}) from the league's scoreboards."""
    week_list, opponent, totals = [], {}, {}
    for week in weeks:
        if not (week.get("start") and week.get("end")):
            continue
        number = week["week"]
        week_list.append({"week": number, "start": week["start"], "end": week["end"],
                          "status": week.get("status")})
        for matchup in week.get("matchups") or []:
            if len(matchup.get("teams") or []) == 2:
                a, b = matchup["teams"]
                opponent[(number, a)], opponent[(number, b)] = b, a
            for team, values in (matchup.get("totals") or {}).items():
                totals[(number, team)] = values
    return week_list, opponent, totals


def team_names(days, weeks):
    """{team number: name}, from the days and the scoreboards."""
    names = {}
    for d in days:
        names.update(d.get("names") or {})
    for week in weeks:
        for matchup in week.get("matchups") or []:
            names.update({k: v for k, v in (matchup.get("names") or {}).items() if v})
    return names


def category_list(categories):
    """The league's scored categories as the page reads them."""
    return [{"id": c["id"], "name": c["name"], "higherBetter": c["higherBetter"],
             "ratio": c["id"] in bp.RATIOS, "goalie": bool(c.get("goalie"))} for c in categories]


def results(info, days, weeks, transactions, players, outside, slots, detail_team=None):
    """
    {categories, swingsScored, teams, players, asOf, weeks, moves, season,
    weekly, together} - `season` a `_tally` per team, `weekly` one per team
    per week, `together` {week: {team: swing with its opponent}} for each week
    a team's own moves for it, undone at once, changed. A move's `weeks` are
    those its swing changed; one after its own weeks is `later` (shown, not
    counted), and only the detail team's moves carry those.

    `players` is {yahooId: [name, nhlTeam, positions]}; `outside` {yahooId:
    {date: game line}} - the dropped players' NHL games, matched by the route;
    `slots` the league's starting slot counts. Every move by `detail_team` is
    listed, with both players' totals and their nights ([date, his slot, his
    line, the kind of spot open to the dropped player, its slot, the dropped
    player's line, [the bench stand-in, his line] or None]); another team's
    only when it changed a week, with games,
    starts and those weeks. `season` and `weekly` count every move of every
    team. A weekend move for the week ahead carries that week as
    `carriedInto`. Fields that say nothing (None, False, []) are left out.
    """
    categories = bp.scored_categories(info)
    swings_scored = info.get("scoring") == "head"
    season = Season(days)
    last = season.dates[-1] if season.dates else None

    week_list, opponent, totals = index_weeks(weeks)

    def week_of(when):
        return next((w for w in week_list if w["start"] <= when <= w["end"]), None)

    names = team_names(days, weeks)

    moves = pair(transactions)
    out, summary = [], defaultdict(_tally)
    weekly = defaultdict(lambda: defaultdict(_tally))     # {week: {team: tally}}
    nightly = defaultdict(list)     # {(team, date): every move active that night, for `undone`}
    own_weeks = {}                  # {move index: the weeks it counts in}

    def kept(n, number):
        """
        Whom undoing every one of week `number`'s own moves would have kept
        in place of move n's dropped player. Usually that player; but if one
        of those moves had picked him up, undoing it too means he was never
        there, so the chain is followed back - Z for X after X for Y keeps Y,
        and a chain that began with a pickup who dropped nobody keeps nobody.
        Undone one at a time, Z for X keeps X; together, X's games after he
        was dropped are not the team's to have had.
        """
        player, k = moves[n]["dropped"], n
        while player:
            earlier = next((i for i in range(k - 1, -1, -1)
                            if moves[i]["team"] == moves[n]["team"] and moves[i]["added"] == player), None)
            if earlier is None or number not in own_weeks.get(earlier, ()):
                return player
            player, k = moves[earlier]["dropped"], earlier
        return None

    for n, move in enumerate(moves):
        team = move["team"]
        added, dropped = move["added"], move["dropped"]
        made_in = week_of(move["date"])
        summary[team]["moves"] += 1
        if made_in:
            weekly[made_in["week"]][team]["moves"] += 1
        # Made on the weekend ending a week: the next week's too, if he was
        # kept into it and started there (counted once the nights are read).
        # One made before the season is the first week's the same way.
        following = week_of(_plus(made_in["end"], 1)) if made_in and added else None
        if following and _plus(move["date"], WEEKEND_DAYS) < following["start"]:
            following = None
        if not made_in and added and week_list and move["date"] < week_list[0]["start"]:
            following = week_list[0]
        if added:
            summary[team]["adds"] += 1
            if made_in:
                weekly[made_in["week"]][team]["adds"] += 1
            window = season.tenure(added, team, move["date"])
        else:
            # A lone drop: until the team's next pickup filled the spot he left
            refill = next((m["date"] for m in moves[n + 1:] if m["team"] == team and m["added"]), None)
            window = [d for d in season.dates if d >= move["date"] and (refill is None or d < refill)]

        drop_positions = positions_of((players.get(dropped) or ["", "", ""])[2]) if dropped else []
        a_total, d_total = {}, {}
        a_games = a_starts = d_games = d_open = covered = following_starts = 0
        by_week = defaultdict(lambda: ({}, {}))
        # Per week, until the move's own weeks are known: its starts, its
        # dropped player's open games, and its nights for `undone`
        week_starts, week_open, week_nights = Counter(), Counter(), defaultdict(list)
        nights = []
        for when in window:
            roster = season.roster(when, team)
            row = roster.get(added) if added else None
            started = bool(row and row[0] not in bp.NOT_STARTING)
            a_played = bool(row and bp.played(row[2]))
            if a_played:
                a_games += 1
            week = week_of(when)
            if started and a_played:
                a_starts += 1
                add_line(a_total, row[2])
                if week:
                    add_line(by_week[week["week"]][0], row[2])
                    week_starts[week["week"]] += 1
                    if following and week["week"] == following["week"]:
                        following_starts += 1

            d_line, spot = None, None
            if dropped and not season.on_team(when, dropped, team):
                d_line = (outside.get(dropped) or {}).get(when)
                if d_line:
                    d_games += 1
                    spot = open_spot(roster, drop_positions, row[0] if started else None, slots, added)
                    if spot:
                        d_open += 1
                        add_line(d_total, d_line)
                        if week:
                            add_line(by_week[week["week"]][1], d_line)
                            week_open[week["week"]] += 1
            # A start the dropped player could not have made would not have
            # gone unfilled: a teammate who played on the bench takes it, and
            # his line belongs to the week without the move
            cover = None
            if started and a_played and not (spot and spot[0] == "own"):
                cover = stand_in(roster, row[0], categories)
                if cover:
                    covered += 1
                    if week:
                        add_line(by_week[week["week"]][1], cover[1])
            if week:
                # A pickup on the roster, started or not: undone with the
                # rest, he is no one's bench stand-in. Who comes back in his
                # place is settled once the week's other moves are known.
                week_nights[week["week"]].append((when, added if row else None, row if started else None))
            if team == detail_team and (a_played or d_line):
                nights.append([when, row[0] if row else None,
                               _compact_line(row[2], categories) if a_played else None,
                               spot[0] if spot else None, spot[1] if spot else None,
                               _compact_line(d_line, categories) if d_line else None,
                               [cover[0], _compact_line(cover[1], categories)] if cover else None])

        carried = following["week"] if following and following_starts else None
        if carried is not None:
            weekly[carried][team]["moves"] += 1
            weekly[carried][team]["adds"] += 1

        # A move counts in its own weeks only - the one it was made in and the
        # one a weekend pickup was carried into. A pickup kept longer is the
        # team's player after that: his later weeks are shown, never counted,
        # so an old move cannot keep piling up a manager's weeks.
        own = {w for w in (made_in["week"] if made_in else None, carried) if w is not None}
        own_weeks[n] = own
        for number in own:
            weekly[number][team]["starts"] += week_starts[number]
            weekly[number][team]["dropGames"] += week_open[number]
            summary[team]["starts"] += week_starts[number]
            summary[team]["dropGames"] += week_open[number]
            back = kept(n, number)
            back_positions = positions_of((players.get(back) or ["", "", ""])[2]) if back else []
            for when, pickup, start_row in week_nights[number]:
                line = (outside.get(back) or {}).get(when) \
                    if back and not season.on_team(when, back, team) else None
                if pickup or line:
                    nightly[(team, when)].append((pickup, start_row, back_positions, line))

        # The team being looked at gets every move, with its lines and nights,
        # and every week it changed, those after its own marked `later`. The
        # rest of the league gets only moves that changed one of their own
        # weeks - the only weeks the league's views show.
        detail = team == detail_team
        week_out = []
        for number, (minus, plus) in sorted(by_week.items()):
            rival = opponent.get((number, team))
            if (number in own or detail) and swings_scored and rival \
                    and (number, team) in totals and (number, rival) in totals:
                entry = swing(totals[(number, team)], totals[(number, rival)], minus, plus, categories)
                if entry["gains"] or entry["costs"]:
                    week_out.append({"week": number, "opponent": rival, **entry,
                                     **({} if number in own else {"later": True})})
        if not (detail or week_out):
            continue
        record = _slim({
            "team": team, "date": move["date"], "claim": move["claim"], "paired": move["paired"],
            "dropDate": move["dropDate"] if move["dropDate"] != move["date"] else None,
            "added": None if not added else _slim({
                "player": added, "first": window[0] if window else None,
                "last": window[-1] if window else None,
                "current": bool(window and window[-1] == last),
                "games": a_games, "starts": a_starts, "covered": covered,
                "totals": shown(a_total, categories) if detail else None,
            }),
            "dropped": None if not dropped else _slim({
                "player": dropped, "games": d_games, "open": d_open,
                "unmatched": dropped not in outside,
                "nowOn": season.team_of.get(last, {}).get(dropped) if last else None,
                "totals": shown(d_total, categories) if detail else None,
            }),
            "weeks": week_out,
            "carriedInto": carried,
            "nights": nights if detail else None,
        })
        out.append(record)

    # A team's categories won and cost are its week with every one of its
    # moves undone at once, scored once. Summing each move's own swing counted
    # a category twice when two moves each decided it alone, and missed one
    # only two together decided.
    together = defaultdict(dict)        # {week: {team: swing}}, the weeks changed
    if swings_scored:
        joint = defaultdict(lambda: ({}, {}))
        for (team, when), active in nightly.items():
            week = week_of(when)
            if week:
                minus, plus = undone(season.roster(when, team), active, slots, categories)
                for line in minus:
                    add_line(joint[(week["week"], team)][0], line)
                for line in plus:
                    add_line(joint[(week["week"], team)][1], line)
        for (number, team), (minus, plus) in sorted(joint.items()):
            rival = opponent.get((number, team))
            if not (rival and (number, team) in totals and (number, rival) in totals):
                continue
            entry = swing(totals[(number, team)], totals[(number, rival)], minus, plus, categories)
            if entry["gains"] or entry["costs"]:
                together[number][team] = {"opponent": rival, **entry}
                for each in (summary[team], weekly[number][team]):
                    each["gained"] += len(entry["gains"])
                    each["lost"] += len(entry["costs"])
                    each["net"] += entry["net"]

    out.reverse()          # newest first, as the transactions are listed
    named = {p for m in out for p in ((m.get("added") or {}).get("player"),
                                      (m.get("dropped") or {}).get("player")) if p}
    named |= {n[6][0] for m in out for n in m.get("nights") or [] if n[6]}
    return {
        "categories": category_list(categories),
        "swingsScored": swings_scored,
        "teams": names,
        "players": {k: v for k, v in players.items() if k in named},
        "asOf": last,
        "weeks": week_list,
        "moves": out,
        "season": {team: _rounded(tally) for team, tally in summary.items()},
        "weekly": {str(number): {team: _rounded(tally) for team, tally in teams.items()}
                   for number, teams in sorted(weekly.items())},
        "together": {str(number): teams for number, teams in sorted(together.items())},
        "detailTeam": detail_team,
    }
