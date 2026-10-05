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
dropped and a drop with nothing added stand alone. Trades are not moves here.

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
Updated - 10/5/2026
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


def swing(totals, rival, minus, plus, categories):
    """
    What a move did to a week: `minus` (the added player's started lines) off
    the team's real totals, `plus` (the dropped player's open-spot lines) on,
    every category scored against the opponent again. {with, without, gains,
    costs, net}, each gain or cost [category, with the move, without it], and
    `net` the categories the move was worth, a tie counting half.
    """
    before, results = bp.record(totals, rival, categories)
    after, after_results = bp.record(bp.swapped_totals(totals, minus, plus, categories), rival, categories)
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
    cost and were worth (`gained`, `lost`, `net`). A week's adds are those
    made in it; its starts and swings come from moves made then or before.
    """
    return {"moves": 0, "adds": 0, "starts": 0, "dropGames": 0, "gained": 0, "lost": 0, "net": 0.0}


def _rounded(tally):
    return {**tally, "net": round(tally["net"], 1)}


def _slim(record):
    """Without the fields that say nothing - None, False, [] - which a season
    of moves would otherwise repeat a thousand times. Zeroes stay."""
    return {k: v for k, v in record.items() if v is not None and v is not False and v != []}


def results(info, days, weeks, transactions, players, outside, slots, detail_team=None):
    """
    {categories, swingsScored, teams, players, asOf, weeks, moves, season,
    weekly} - `season` a `_tally` per team, `weekly` one per team per week.

    `players` is {yahooId: [name, nhlTeam, positions]}; `outside` {yahooId:
    {date: game line}} - the dropped players' NHL games, matched by the route;
    `slots` the league's starting slot counts. Every move by `detail_team` is
    listed, with both players' totals and their nights ([date, his slot, his
    line, the kind of spot open to the dropped player, its slot, the dropped
    player's line, [the bench stand-in, his line] or None]); another team's
    only when it changed a week, with games,
    starts and those weeks. `season` and `weekly` count every move of every
    team. Fields that say nothing (None, False, []) are left out.
    """
    categories = bp.scored_categories(info)
    swings_scored = info.get("scoring") == "head"
    season = Season(days)
    last = season.dates[-1] if season.dates else None

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

    def week_of(when):
        return next((w for w in week_list if w["start"] <= when <= w["end"]), None)

    names = {}
    for d in days:
        names.update(d.get("names") or {})
    for week in weeks:
        for matchup in week.get("matchups") or []:
            names.update({k: v for k, v in (matchup.get("names") or {}).items() if v})

    moves = pair(transactions)
    out, summary = [], defaultdict(_tally)
    weekly = defaultdict(lambda: defaultdict(_tally))     # {week: {team: tally}}
    for n, move in enumerate(moves):
        team = move["team"]
        added, dropped = move["added"], move["dropped"]
        made_in = week_of(move["date"])
        summary[team]["moves"] += 1
        if made_in:
            weekly[made_in["week"]][team]["moves"] += 1
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
        a_games = a_starts = d_games = d_open = covered = 0
        by_week = defaultdict(lambda: ({}, {}))
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
                    weekly[week["week"]][team]["starts"] += 1

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
                            weekly[week["week"]][team]["dropGames"] += 1
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
            if team == detail_team and (a_played or d_line):
                nights.append([when, row[0] if row else None,
                               _compact_line(row[2], categories) if a_played else None,
                               spot[0] if spot else None, spot[1] if spot else None,
                               _compact_line(d_line, categories) if d_line else None,
                               [cover[0], _compact_line(cover[1], categories)] if cover else None])

        # Only the weeks a move changed: a season of every week of every move
        # is most of a response otherwise
        week_out = []
        for number, (minus, plus) in sorted(by_week.items()):
            rival = opponent.get((number, team))
            if swings_scored and rival and (number, team) in totals and (number, rival) in totals:
                entry = swing(totals[(number, team)], totals[(number, rival)], minus, plus, categories)
                if entry["gains"] or entry["costs"]:
                    week_out.append({"week": number, "opponent": rival, **entry})

        tally = summary[team]
        tally["starts"] += a_starts
        tally["dropGames"] += d_open
        for entry in week_out:
            for each in (tally, weekly[entry["week"]][team]):
                each["gained"] += len(entry["gains"])
                each["lost"] += len(entry["costs"])
                each["net"] += entry["net"]

        # The team being looked at gets every move, with its lines and nights.
        # The rest of the league only feeds the summary and the weeks a move
        # changed, so a move of theirs that changed none is left out.
        detail = team == detail_team
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
            "nights": nights if detail else None,
        })
        out.append(record)

    out.reverse()          # newest first, as the transactions are listed
    named = {p for m in out for p in ((m.get("added") or {}).get("player"),
                                      (m.get("dropped") or {}).get("player")) if p}
    named |= {n[6][0] for m in out for n in m.get("nights") or [] if n[6]}
    return {
        "categories": [{"id": c["id"], "name": c["name"], "higherBetter": c["higherBetter"],
                        "ratio": c["id"] in bp.RATIOS, "goalie": bool(c.get("goalie"))} for c in categories],
        "swingsScored": swings_scored,
        "teams": names,
        "players": {k: v for k, v in players.items() if k in named},
        "asOf": last,
        "weeks": week_list,
        "moves": out,
        "season": {team: _rounded(tally) for team, tally in summary.items()},
        "weekly": {str(number): {team: _rounded(tally) for team, tally in teams.items()}
                   for number, teams in sorted(weekly.items())},
        "detailTeam": detail_team,
    }
