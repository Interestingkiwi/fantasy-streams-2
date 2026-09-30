"""
What a league left on its benches: every benched player who played, and the
swaps that would have changed a week's result.

The old site's "points earned by players you left on the bench", all season
and week by week, where the week view "pours salt in the wound" by naming the
swap that would have won or tied a category. Pure: `yahoo_league_api` reads
the league (settings, each day's rosters with stats, each week's scoreboard)
and this works out the rest.

**A bench appearance** is a player in a BN slot on a day he has stats - any
value where Yahoo shows `-` for no game. IR, IR+ and NA are not the bench: a
player there could not have been started.

**A swap is one bench player for one starter, on one day** - "played X over
Y". X must be able to fill Y's slot: listed there, or the slot is a generic
he fits (F: any forward, W: a winger, Util: any skater). A starter with no game
that day is the worst case and is included - the swap is then pure gain. The
week's totals are Yahoo's own, from the scoreboard; a swap takes the starter's
line off and puts the bench player's on, and each scored category is compared
with the opponent's total again. Only swaps that win or tie at least one
category more are kept, with anything they would have cost shown beside it.

**Ratio categories are recomputed, not skipped.** SV% from saves over shots
against; GAA from goals against over minutes, where a goalie's minutes come
back out of his own GAA (`GA x 60 / GAA`, exact - see `goalie_planning`) and a
team's out of its week's GA and GAA. A shutout has no GAA to invert and counts
as 60 minutes. When a league does not carry the counts a ratio needs, that
category is left as it was rather than guessed.

**Points leagues** get the bench totals but no swaps: there the question is
fantasy points, not categories, and this does not score them yet.

Author - Jason Druckenmiller
Created - 9/30/2026
Updated - 9/30/2026
"""

from collections import defaultdict

NOT_STARTING = frozenset({"BN", "IR", "IR+", "NA"})
# Distinct outcomes kept per bench appearance - the best, and one alternative
MAX_OUTCOMES = 2
OUTCOME = {1: "win", 0: "tie", -1: "loss"}
FORWARDS = frozenset({"C", "LW", "RW"})
GENERIC = {"F": FORWARDS, "W": frozenset({"LW", "RW"}), "Util": FORWARDS | {"D"}}

# Yahoo stat ids for the goalie counts a ratio is rebuilt from
GA, GAA, SA, SV, SV_PCT = "22", "23", "24", "25", "26"
RATIOS = frozenset({GAA, SV_PCT})


def played(stats):
    return any(value is not None for value in (stats or {}).values())


def can_fill(positions, slot):
    """Whether a player eligible at `positions` could have played `slot`."""
    positions = set(positions or [])
    if slot in positions:
        return True
    return bool(GENERIC.get(slot, frozenset()) & positions)


def is_goalie(positions):
    return "G" in set(positions or [])


def minutes(ga, gaa):
    """Minutes played, back out of goals against and GAA. 60 for a shutout."""
    if ga and gaa:
        return ga * 60.0 / gaa
    return 60.0 if ga is not None or gaa is not None else 0.0


def compare(mine, theirs, higher_better):
    """1 a win, 0 a tie, -1 a loss; None when either side has no value."""
    if mine is None or theirs is None:
        return None
    if abs(mine - theirs) < 1e-9:
        return 0
    return 1 if (mine > theirs) == higher_better else -1


def _compact(value):
    """A whole number as an int, so JSON says 3 rather than 3.0."""
    return int(value) if float(value).is_integer() else round(value, 3)


def _value(stats, stat_id):
    value = (stats or {}).get(stat_id)
    return value if value is not None else 0.0


def swapped_totals(totals, starter, bench, categories):
    """
    The team's week totals with `starter`'s line off and `bench`'s on. Counting
    categories move by the difference; the two ratios are rebuilt from their
    parts, or left as they were when the parts are missing.
    """
    after = dict(totals)
    for category in categories:
        stat = category["id"]
        if stat in RATIOS:
            continue
        if totals.get(stat) is None and not (starter.get(stat) or bench.get(stat)):
            continue
        after[stat] = _value(totals, stat) - _value(starter, stat) + _value(bench, stat)

    ids = {c["id"] for c in categories}
    if SV_PCT in ids and totals.get(SV) is not None and totals.get(SA) is not None:
        saves = _value(totals, SV) - _value(starter, SV) + _value(bench, SV)
        shots = _value(totals, SA) - _value(starter, SA) + _value(bench, SA)
        after[SV_PCT] = saves / shots if shots else None
    if GAA in ids and totals.get(GA) is not None and totals.get(GAA):
        team_minutes = _value(totals, GA) * 60.0 / totals[GAA]
        new_minutes = (team_minutes - minutes(starter.get(GA), starter.get(GAA))
                       + minutes(bench.get(GA), bench.get(GAA)))
        goals = _value(totals, GA) - _value(starter, GA) + _value(bench, GA)
        after[GAA] = goals * 60.0 / new_minutes if new_minutes > 0 else None
    return after


def record(mine, theirs, categories):
    """
    (wins, losses, ties) and each category's result. A counting category
    nobody has recorded yet is zero - Yahoo's dash - while a ratio with
    nothing under it has no value and decides nothing.
    """
    def value(totals, stat):
        found = totals.get(stat)
        return found if (found is not None or stat in RATIOS) else 0.0
    results = {c["id"]: compare(value(mine, c["id"]), value(theirs, c["id"]), c["higherBetter"])
               for c in categories}
    counted = [r for r in results.values() if r is not None]
    return [counted.count(1), counted.count(-1), counted.count(0)], results


def scored_categories(info):
    return [c for c in info["categories"] if not c["displayOnly"]]


def summarise(info, days, weeks):
    """
    {categories, teams, players, season: {team: {appearances, totals}},
     weeks: [{week, start, end, status, teams: {team: {opponent, appearances,
     swaps}}}]} - see the module docstring for what each means.
    """
    categories = scored_categories(info)
    counting = [c for c in categories if c["id"] not in RATIOS]
    swaps_scored = info.get("scoring") == "head"
    names, players = {}, {}
    for d in days:
        names.update(d.get("names") or {})
        players.update(d.get("players") or {})
    for week in weeks:
        for matchup in week["matchups"]:
            names.update({k: v for k, v in matchup["names"].items() if v})

    season = defaultdict(lambda: {"appearances": 0, "totals": defaultdict(float)})
    weeks_out = []
    for week in weeks:
        if not (week.get("start") and week.get("end")):
            continue
        opponent, totals = {}, {}
        for matchup in week["matchups"]:
            if len(matchup["teams"]) == 2:
                a, b = matchup["teams"]
                opponent[a], opponent[b] = b, a
            totals.update(matchup["totals"])

        teams_out = {}
        for d in days:
            if not (week["start"] <= d["date"] <= week["end"]):
                continue
            for team, rows in d["teams"].items():
                entry = teams_out.setdefault(team, {"opponent": opponent.get(team),
                                                    "appearances": [], "swaps": []})
                starters = [r for r in rows if r[1] not in NOT_STARTING]
                for yahoo_id, slot, positions, stats in rows:
                    if slot != "BN" or not played(stats):
                        continue
                    # Only what he did: a season of lines is most of the response
                    line = {c["id"]: _compact(stats[c["id"]]) for c in categories if stats.get(c["id"])}
                    entry["appearances"].append({"date": d["date"], "player": yahoo_id, "stats": line})
                    season[team]["appearances"] += 1
                    for c in counting:
                        season[team]["totals"][c["id"]] += _value(stats, c["id"])

                    rival = opponent.get(team)
                    if not (swaps_scored and rival and team in totals and rival in totals):
                        continue
                    before, results = record(totals[team], totals[rival], categories)
                    # Starters whose swap comes out the same are one outcome:
                    # "over Fox, Jones or Gavrikov", not three rows
                    outcomes = {}
                    for s_id, s_slot, _s_positions, s_stats in starters:
                        if is_goalie(positions) != (s_slot == "G") or not can_fill(positions, s_slot):
                            continue
                        after_totals = swapped_totals(totals[team], s_stats, stats, categories)
                        after, after_results = record(after_totals, totals[rival], categories)
                        # Each as [category, what it becomes]: "win", "tie" or "loss"
                        gains = tuple((c["id"], OUTCOME[after_results[c["id"]]]) for c in categories
                                      if after_results[c["id"]] is not None
                                      and (after_results[c["id"]]) > (results[c["id"]] or 0))
                        losses = tuple((c["id"], OUTCOME[after_results[c["id"]] or 0]) for c in categories
                                       if results[c["id"]] is not None
                                       and (after_results[c["id"]] or 0) < results[c["id"]])
                        if not gains:
                            continue
                        outcome = outcomes.setdefault((gains, losses, tuple(after)), {
                            "date": d["date"], "bench": yahoo_id, "starters": [],
                            "gains": [list(g) for g in gains], "losses": [list(l) for l in losses],
                            "before": before, "after": after,
                        })
                        outcome["starters"].append({"id": s_id, "slot": s_slot, "played": played(s_stats)})
                    best = sorted(outcomes.values(), key=lambda o: -(len(o["gains"]) - len(o["losses"])))
                    entry["swaps"].extend(best[:MAX_OUTCOMES])
        for entry in teams_out.values():
            entry["swaps"].sort(key=lambda s: (-(len(s["gains"]) - len(s["losses"])), s["date"]))
        weeks_out.append({"week": week["week"], "start": week["start"], "end": week["end"],
                          "status": week.get("status"), "playoffs": week.get("playoffs", False),
                          "teams": teams_out})

    return {
        "categories": [{"id": c["id"], "name": c["name"], "higherBetter": c["higherBetter"],
                        "ratio": c["id"] in RATIOS} for c in categories],
        "swapsScored": swaps_scored,
        "teams": names,
        "players": players,
        "season": {team: {"appearances": v["appearances"],
                          "totals": {k: _compact(t) for k, t in v["totals"].items() if t}}
                   for team, v in season.items()},
        "weeks": weeks_out,
    }
