"""
Shared NHL-schedule maths.

Both the draft-prep playoff column and the Schedules page ask the same
questions of `nhl_schedule` - how many games does each team play in a
window, and which of those fall on a night when most of the league is
idle - so the answers live here rather than in either route module.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

from collections import Counter
from datetime import date, timedelta

# A date carrying fewer than this many games is a light night: enough of the
# league is idle that a manager can start players who would otherwise sit.
#
# NOTE: 8 games is 16 of 32 teams, which flags about 56% of an 84-game season.
# The original comment described "under a quarter of the league", which would
# be 4. Left at 8 because it is what the draft-prep playoff column has always
# used and changing it moves numbers users have seen; revisit deliberately.
LIGHT_NIGHT_MAX_GAMES = 8

# Yahoo fantasy weeks run Monday to Sunday.
WEEK_END_WEEKDAY = 6   # Sunday, in Python's Monday=0 numbering


def light_nights(rows, threshold=LIGHT_NIGHT_MAX_GAMES):
    """
    The set of dates in `rows` carrying fewer than `threshold` games.
    `rows` is any iterable of (game_date, home, away).
    """
    per_date = Counter(row[0] for row in rows)
    return {game_date for game_date, count in per_date.items() if count < threshold}


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


def derive_weeks(first_date, last_date):
    """
    Fantasy weeks spanning the season, as [{week, start, end}].

    Yahoo weeks end on a Sunday, so the first week is short whenever the
    season opens mid-week - which it usually does. Used when no synced
    league is available to supply real Yahoo weeks; `weeks` from a synced
    league should win over this, since a league's own weeks are what its
    matchups are scored against.
    """
    start = date.fromisoformat(first_date)
    last = date.fromisoformat(last_date)

    weeks = []
    week_start = start
    while week_start <= last:
        days_to_sunday = (WEEK_END_WEEKDAY - week_start.weekday()) % 7
        week_end = min(week_start + timedelta(days=days_to_sunday), last)
        weeks.append({
            "week": len(weeks) + 1,
            "start": week_start.isoformat(),
            "end": week_end.isoformat(),
        })
        week_start = week_end + timedelta(days=1)

    return weeks
