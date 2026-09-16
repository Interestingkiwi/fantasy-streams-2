"""
Shared NHL-schedule maths.

Both the draft-prep playoff column and the Schedules page ask the same
questions of `nhl_schedule` - how many games does each team play in a
window, and which of those fall on a night when most of the league is
idle - so the answers live here rather than in either route module.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/16/2026
"""

from collections import Counter
from datetime import date, timedelta

# Eight games or fewer is the standard fantasy-hockey definition of a light
# night: enough of the league is idle that a manager can start players who
# would otherwise sit. This is a domain convention, not a tunable - leave it.
#
# The comparison is inclusive (`<=`): an 8-game night IS light. It read `<`
# until 9/7/2026, which silently moved the line to seven and threw away the
# most common light night of all.
LIGHT_NIGHT_MAX_GAMES = 8

# Yahoo fantasy weeks run Monday to Sunday.
WEEK_END_WEEKDAY = 6   # Sunday, in Python's Monday=0 numbering

# A league-wide stoppage this long is a break, not a quiet patch. Yahoo folds a
# week a break guts into its neighbour, so no matchup is decided on the three
# nights left over. In 2026-27 the All-Star break leaves Feb 4-7 idle - the
# back end of the Feb 1-7 week - and Yahoo's Week 19 runs Feb 1-14. Christmas
# is three idle days, so it stays short of this and its week is left alone.
BREAK_MIN_IDLE_DAYS = 4


def light_nights(rows, threshold=LIGHT_NIGHT_MAX_GAMES):
    """
    The set of dates in `rows` carrying `threshold` games or fewer.
    `rows` is any iterable of (game_date, home, away).
    """
    per_date = Counter(row[0] for row in rows)
    return {game_date for game_date, count in per_date.items() if count <= threshold}


def games_per_date(rows):
    """{date: game count}, for a calendar view."""
    return dict(Counter(row[0] for row in rows))


def team_game_counts(rows, threshold=LIGHT_NIGHT_MAX_GAMES):
    """
    {team: {'games': n, 'lightNights': n}} across `rows`.

    A team gets one entry per appearance, home or away, so the totals are
    games played rather than games scheduled at a venue.
    """
    light = light_nights(rows, threshold)

    teams = {}
    for game_date, home, away in rows:
        for team in (home, away):
            entry = teams.setdefault(team, {"games": 0, "lightNights": 0})
            entry["games"] += 1
            if game_date in light:
                entry["lightNights"] += 1
    return teams


def summarise(teams):
    """Average/min/max games across teams, for colour-coding a table."""
    counts = [entry["games"] for entry in teams.values()]
    if not counts:
        return {"average": 0, "min": 0, "max": 0}
    return {
        "average": round(sum(counts) / len(counts), 1),
        "min": min(counts),
        "max": max(counts),
    }


def derive_weeks(first_date, last_date, game_dates=None):
    """
    Fantasy weeks spanning the season, as [{week, start, end}].

    Yahoo weeks end on a Sunday, so the first week is short whenever the
    season opens mid-week - which it usually does. Used when no synced
    league is available to supply real Yahoo weeks; `weeks` from a synced
    league should win over this, since a league's own weeks are what its
    matchups are scored against.

    Pass `game_dates` (every date with a game) and a week holding a break of
    `BREAK_MIN_IDLE_DAYS` is merged with a neighbour, marked `combined: True`,
    and everything after is renumbered - which is how Yahoo numbers them. Read
    from the schedule rather than pinned to a date, so next season's break is
    found the same way.
    """
    start = date.fromisoformat(first_date)
    last = date.fromisoformat(last_date)

    weeks = []
    week_start = start
    while week_start <= last:
        days_to_sunday = (WEEK_END_WEEKDAY - week_start.weekday()) % 7
        week_end = min(week_start + timedelta(days=days_to_sunday), last)
        weeks.append({"start": week_start, "end": week_end})
        week_start = week_end + timedelta(days=1)

    for first_idle, last_idle in breaks(game_dates or []):
        _merge_across(weeks, first_idle, last_idle)

    return [
        {"week": number, "start": w["start"].isoformat(), "end": w["end"].isoformat(),
         **({"combined": True} if w.get("combined") else {})}
        for number, w in enumerate(weeks, start=1)
    ]


def breaks(game_dates, min_idle=BREAK_MIN_IDLE_DAYS):
    """
    [(first idle date, last idle date)] for every stretch of at least
    `min_idle` days with no game anywhere in the league.
    """
    played = sorted({date.fromisoformat(str(d)) for d in game_dates})
    found = []
    for before, after in zip(played, played[1:]):
        idle = (after - before).days - 1
        if idle >= min_idle:
            found.append((before + timedelta(days=1), after - timedelta(days=1)))
    return found


def _merge_across(weeks, first_idle, last_idle):
    """
    Fold a break's week into the neighbour it leaves stranded nights beside.

    A break across a week boundary joins the two weeks it touches. One inside
    a week joins that week to the neighbour on the break's side: idle through
    Sunday means the week's few games came first, so they belong with the week
    that follows - the All-Star case - and idle from Monday means the reverse.
    """
    def holding(day):
        return next((i for i, w in enumerate(weeks) if w["start"] <= day <= w["end"]), None)

    first, last = holding(first_idle), holding(last_idle)
    if first is None or last is None:
        return
    if first == last:
        week = weeks[first]
        played_before = (first_idle - week["start"]).days
        played_after = (week["end"] - last_idle).days
        if played_after <= played_before and first + 1 < len(weeks):
            last = first + 1
        elif first > 0:
            first -= 1
        else:
            return

    weeks[first] = {"start": weeks[first]["start"], "end": weeks[last]["end"],
                    "combined": True}
    del weeks[first + 1:last + 1]
