"""
Trade Results: what each trade brought each side, week by week and over the
season since, against keeping the players it sent away.

Season History's fourth view, beside Transaction Results and worked out the
same way. Pure: the route reads the league's days, weeks and transactions (the
shapes `transaction_results` takes) and the traded players' NHL games, and
this works out the rest.

**A side** is one team's half of a trade: the players it received and the
players it sent. Draft picks are listed, never scored.

**What the players received brought is what Yahoo counted**: each one's line
on the nights he sat in a starting slot and played, from his first night on
the team until he left it. A night on the bench is a game, not production.

**What the players sent would have brought** is each one's line on the nights
he played elsewhere - Yahoo's own line while he was on another fantasy roster,
NHL game data while he was on none - and a spot he could fill was open on the
team that night: a received player's starting slot he fits, an empty one, or
an idle starter's. Every player on the side is undone at once
(`transaction_results.undone`), so each spot is used once, and the busiest
sent player that night is the first to take a received player's slot. A
received player's start nobody sent could have made goes to a teammate who
played on the bench, as a pickup's does.

**Judged while what it brought is on the team**: from the trade until the last
player the side received is gone, or to the end of the season so far while one
is still there. Whatever happened after he left - a drop, another trade - is
that move's to answer for. A side that took only picks, or players it let go
before they spent a night on its roster (one in the test league was dropped
six hours after the trade), is judged to the end of the season so far: what
it sent away is all there is to judge. Unlike a
pickup, a trade is not held to the week it was made in: it is a bet on the
rest of the season, so every week since counts.

**A week's swing** is that week's matchup scored again without the trade: the
received players' started lines off the team's real totals, the sent players'
open-spot lines on (`transaction_results.swing`). In a week the two teams met,
both sides are undone, since neither would have had what it got.

**A team's trades are scored together** for its totals (`together`): a week
with more than one in effect is scored once with all of them undone, each spot
used once. A player who arrived in one of them and left in another is not
brought back - undoing both, he was never there - and one sent away and later
traded for again stays, since he never left. Each side's own numbers say what
its trade did alone.

Points leagues get the lines but no swings, as with bench points.

Author - Jason Druckenmiller
Created - 10/22/2026
Updated - 10/22/2026
"""

from collections import defaultdict

import bench_points as bp
import transaction_results as tr


def trades(transactions):
    """
    Every trade between two teams, oldest first: {time, date, sides: [{team,
    received, sent, picksIn, picksOut}, two of them]} - players as Yahoo ids,
    picks as rounds.
    """
    out = []
    ordered = sorted((t for t in transactions or []
                      if t.get("type") == "trade" and t.get("time")), key=lambda t: t["time"])
    for t in ordered:
        sides = {}

        def side(team):
            return sides.setdefault(str(team), {"team": str(team), "received": [], "sent": [],
                                                "picksIn": [], "picksOut": []})
        for pid, frm, to in t.get("moves") or []:
            if frm and to and frm not in tr.POOLS and to not in tr.POOLS and frm != to:
                side(to)["received"].append(str(pid))
                side(frm)["sent"].append(str(pid))
        for pick in t.get("picks") or []:
            if len(pick) == 3 and pick[1] and pick[2] and pick[1] != pick[2]:
                side(pick[2])["picksIn"].append(pick[0])
                side(pick[1])["picksOut"].append(pick[0])
        if len(sides) == 2:
            out.append({"time": t["time"], "date": tr.tx_date(t["time"]), "sides": list(sides.values())})
    return out


def _fits(positions, slot):
    return (slot == "G") == bp.is_goalie(positions) and bp.can_fill(positions, slot)


def _busy(line, categories):
    """How much a night's line did in the league's counting categories."""
    return sum(float(line.get(c["id"]) or 0) for c in categories if c["id"] not in bp.RATIOS)


def _outcome(record):
    """A week's matchup from its [won, lost, tied]: 1 won, 0 tied, -1 lost."""
    return (record[0] > record[1]) - (record[0] < record[1])


def _tally():
    """
    One team's trades over a season or a week: `trades` (made, over a season;
    in effect, in a week), the players received's `starts`, the players
    sent's open games (`open`), and the categories and matchups they won and
    cost together (`gained`, `lost`, `net`, `better`, `worse`) - each week
    scored once with all of them undone, never each trade's swing summed.
    """
    return {"trades": 0, "starts": 0, "open": 0, "gained": 0, "lost": 0, "net": 0.0,
            "better": 0, "worse": 0}


def results(info, days, weeks, transactions, players, outside, slots):
    """
    {categories, swingsScored, teams, players, asOf, weeks, trades, season,
    weekly, together} - `trades` newest first, each with its two sides (see
    `_side`); `season` a `_tally` per team, `weekly` one per team per week,
    `together` {week: {team: swing}} for each week a team's trades, undone at
    once, changed.

    `players` is {yahooId: [name, nhlTeam, positions]}; `outside` {yahooId:
    {date: game line}} - the sent players' NHL games, for nights they were on
    no fantasy roster; `slots` the league's starting slot counts.
    """
    categories = bp.scored_categories(info)
    swings_scored = info.get("scoring") == "head"
    season = tr.Season(days)
    last = season.dates[-1] if season.dates else None
    week_list, opponent, totals = tr.index_weeks(weeks)
    week_dates = {w["week"]: [d for d in season.dates if w["start"] <= d <= w["end"]] for w in week_list}

    eligible = {}
    for d in days:
        for rows in (d.get("teams") or {}).values():
            for r in rows:
                if r[2]:
                    eligible[str(r[0])] = list(r[2])

    def positions(pid):
        return eligible.get(pid) or tr.positions_of((players.get(pid) or ["", "", ""])[2])

    def line_of(pid, when):
        """His game that night wherever he was: Yahoo's line on a fantasy
        roster, NHL game data on none. None for no game."""
        team = season.team_of.get(when, {}).get(pid)
        stats = season.rows[when][team][pid][2] if team else (outside.get(pid) or {}).get(when)
        return stats if stats and bp.played(stats) else None

    trade_list = trades(transactions)
    sides = []
    for n, trade in enumerate(trade_list):
        for side, other in (trade["sides"], trade["sides"][::-1]):
            team = side["team"]
            tenure = {r: season.tenure(r, team, trade["date"]) for r in side["received"]}
            stays = [kept[-1] for kept in tenure.values() if kept]
            # Until the last player it brought in is gone. A side that took
            # only picks, or players gone before they spent a night with it,
            # got nothing to judge them by: to the end of the season so far.
            end = max(stays) if stays else last
            window = [d for d in season.dates if end and trade["date"] <= d <= end]
            sides.append({**side, "trade": n, "partner": other["team"], "tenure": tenure,
                          "window": set(window), "dates": window})

    def undo(team, group, number):
        """
        Week `number` of `team` with every side in `group` undone on the nights
        it was in effect: {minus, plus, received: {pid: [starts, games, line]},
        sent: {pid: [open spots, games, line]}, covered, nights}.
        """
        out = {"minus": {}, "plus": {}, "received": {}, "sent": {}, "covered": 0, "nights": 0}
        for when in week_dates.get(number, []):
            active = [s for s in group if when in s["window"]]
            if not active:
                continue
            out["nights"] += 1
            roster = season.roster(when, team)
            # A player one of them brought in and another sent away nets out:
            # undoing both, he either never came or never left
            netted = {p for s in active for p in s["sent"]
                      if any(p in o["received"] for o in active if o is not s)}
            entries, sent_of = [], {}
            for s in active:
                rows = {r: roster[r] for r in s["received"] if r in roster and r not in netted}
                for r, row in rows.items():
                    if bp.played(row[2]):
                        entry = out["received"].setdefault(r, [0, 0, {}])
                        entry[1] += 1
                        if row[0] not in bp.NOT_STARTING:
                            entry[0] += 1
                            tr.add_line(entry[2], row[2])
                lines = {}
                for p in s["sent"]:
                    if p in netted or season.on_team(when, p, team):
                        continue
                    line = line_of(p, when)
                    if line:
                        lines[p] = line
                        sent_of[id(line)] = p
                        out["sent"].setdefault(p, [0, 0, {}])[1] += 1
                # The busiest sent player first to a received player's slot he fits
                free = [r for r, row in rows.items() if row[0] not in bp.NOT_STARTING]
                paired = {}
                for p in sorted(lines, key=lambda p: -_busy(lines[p], categories)):
                    fit = next((r for r in free if _fits(positions(p), rows[r][0])), None)
                    if fit:
                        free.remove(fit)
                        paired[fit] = p
                for r, row in rows.items():
                    p = paired.get(r)
                    entries.append((r, row if row[0] not in bp.NOT_STARTING else None,
                                    positions(p) if p else [], lines[p] if p else None))
                entries += [(None, None, positions(p), line) for p, line in lines.items()
                            if p not in paired.values()]
            minus, plus = tr.undone(roster, entries, slots, categories)
            for line in minus:
                tr.add_line(out["minus"], line)
            for line in plus:
                tr.add_line(out["plus"], line)
                p = sent_of.get(id(line))
                if p:
                    out["sent"][p][0] += 1
                    tr.add_line(out["sent"][p][2], line)
                else:
                    out["covered"] += 1
        return out

    def scored(team, number, mine, group):
        """The week's swing with `mine` undone, the opponent's sides of the
        same trades undone too if he was the partner. None when unscored."""
        rival = opponent.get((number, team))
        if not (swings_scored and mine["nights"] and rival
                and (number, team) in totals and (number, rival) in totals):
            return None
        theirs = [o for s in group for o in sides
                  if o["trade"] == s["trade"] and o is not s and o["team"] == rival]
        rival_without = None
        if theirs:
            their_week = undo(rival, theirs, number)
            if their_week["nights"]:
                rival_without = bp.swapped_totals(totals[(number, rival)], their_week["minus"],
                                                  their_week["plus"], categories)
        entry = tr.swing(totals[(number, team)], totals[(number, rival)], mine["minus"], mine["plus"],
                         categories, rival_without)
        return {**entry, "met": bool(theirs)}

    def lines_out(found):
        return {p: [a, b, tr.shown(line, categories)] for p, (a, b, line) in found.items()}

    def _side(s):
        """
        One side: {team, partner, received: [{player, first, last, current,
        games, starts, totals}], sent: [{player, games, open, nowOn,
        unmatched, totals}], picksIn, picksOut, start, end, current, totals:
        {received, sent}, covered, weeks, gained, lost, net, better, worse}.
        A week is {week, opponent, received: {player: [starts, games, line]},
        sent: {player: [open spots, games, line]}, covered} and, scored, the
        swing (`met` when the opponent was the partner).
        """
        team = s["team"]
        got = defaultdict(lambda: [0, 0, {}])
        gave = defaultdict(lambda: [0, 0, {}])
        all_in, all_out = {}, {}
        record = {"covered": 0, "gained": 0, "lost": 0, "net": 0.0, "better": 0, "worse": 0}
        weeks_out = []
        for w in week_list:
            number = w["week"]
            if not any(d in s["window"] for d in week_dates[number]):
                continue
            u = undo(team, [s], number)
            for found, into in ((u["received"], got), (u["sent"], gave)):
                for p, (a, b, line) in found.items():
                    into[p][0] += a
                    into[p][1] += b
                    tr.add_line(into[p][2], line)
            for p, (_a, _b, line) in u["received"].items():
                tr.add_line(all_in, line)
            for p, (_a, _b, line) in u["sent"].items():
                tr.add_line(all_out, line)
            record["covered"] += u["covered"]
            entry = {"week": number, "opponent": opponent.get((number, team)),
                     "received": lines_out(u["received"]), "sent": lines_out(u["sent"]),
                     "covered": u["covered"] or None}
            swing = scored(team, number, u, [s])
            if swing:
                entry.update(swing)
                record["gained"] += len(swing["gains"])
                record["lost"] += len(swing["costs"])
                record["net"] += swing["net"]
                change = _outcome(swing["with"]) - _outcome(swing["without"])
                record["better"] += change > 0
                record["worse"] += change < 0
            weeks_out.append(tr._slim(entry))
        return tr._slim({
            "team": team, "partner": s["partner"],
            "received": [tr._slim({
                "player": r, "first": s["tenure"][r][0] if s["tenure"][r] else None,
                "last": s["tenure"][r][-1] if s["tenure"][r] else None,
                "current": bool(s["tenure"][r] and s["tenure"][r][-1] == last),
                "games": got[r][1], "starts": got[r][0], "totals": tr.shown(got[r][2], categories) or None,
            }) for r in s["received"]],
            "sent": [tr._slim({
                "player": p, "games": gave[p][1], "open": gave[p][0],
                "nowOn": season.team_of.get(last, {}).get(p) if last else None,
                # Only a night on no fantasy roster needs NHL game data
                "unmatched": p not in outside and any(p not in season.team_of.get(d, {}) for d in s["dates"]),
                "totals": tr.shown(gave[p][2], categories) or None,
            }) for p in s["sent"]],
            "picksIn": s["picksIn"], "picksOut": s["picksOut"],
            "start": s["dates"][0] if s["dates"] else None,
            "end": s["dates"][-1] if s["dates"] else None,
            "current": bool(s["dates"] and s["dates"][-1] == last),
            "totals": {"received": tr.shown(all_in, categories), "sent": tr.shown(all_out, categories)},
            "weeks": weeks_out,
            **record, "net": round(record["net"], 1),
        })

    trades_out = []
    for n, trade in enumerate(trade_list):
        trades_out.append({"date": trade["date"],
                           "sides": [_side(s) for s in sides if s["trade"] == n]})

    # A team's trades together: each week scored once with every one in
    # effect undone, so a category two trades each won alone counts once
    summary = defaultdict(_tally)
    weekly = defaultdict(lambda: defaultdict(_tally))
    together = defaultdict(dict)
    for s in sides:
        summary[s["team"]]["trades"] += 1
    for team in sorted({s["team"] for s in sides}):
        mine = [s for s in sides if s["team"] == team]
        for w in week_list:
            number = w["week"]
            group = [s for s in mine if any(d in s["window"] for d in week_dates[number])]
            if not group:
                continue
            u = undo(team, group, number)
            tally = weekly[number][team]
            tally["trades"] = len(group)
            tally["starts"] = sum(a for a, _b, _l in u["received"].values())
            tally["open"] = sum(a for a, _b, _l in u["sent"].values())
            swing = scored(team, number, u, group)
            if swing and (swing["gains"] or swing["costs"]):
                together[number][team] = {"opponent": opponent.get((number, team)), **swing}
                change = _outcome(swing["with"]) - _outcome(swing["without"])
                tally.update(gained=len(swing["gains"]), lost=len(swing["costs"]), net=swing["net"],
                             better=int(change > 0), worse=int(change < 0))
            for key in ("starts", "open", "gained", "lost", "net", "better", "worse"):
                summary[team][key] += tally[key]

    trades_out.reverse()        # newest first, as the transactions are listed
    named = {p for s in sides for p in s["received"] + s["sent"]}
    return {
        "categories": tr.category_list(categories),
        "swingsScored": swings_scored,
        "teams": tr.team_names(days, weeks),
        "players": {k: v for k, v in players.items() if k in named},
        "asOf": last,
        "weeks": week_list,
        "trades": trades_out,
        "season": {team: {**t, "net": round(t["net"], 1)} for team, t in summary.items()},
        "weekly": {str(number): {team: {**t, "net": round(t["net"], 1)} for team, t in teams.items()}
                   for number, teams in sorted(weekly.items())},
        "together": {str(number): teams for number, teams in sorted(together.items())},
    }
