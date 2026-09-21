"""
A head-to-head matchup's score so far, read off Yahoo's Matchup page.

Standalone mode already takes the score so far typed in by hand - it moves the
projected final margin without adding variance (see *Which categories are
worth chasing*). This reads the same numbers off
`/hockey/<league id>/matchup`, so the user does not have to copy fourteen of
them across every morning.

**Whose matchup Yahoo shows depends on who is asking.** Signed in, the page
opens on the user's own matchup; signed out - which a server-side fetch always
is - it opens on team 1's. So the request names the team with `mid1=<Yahoo
team number>`, which the roster scrape records per team. Without one the page
still answers, just with somebody else's matchup, and the caller has to check
the team names it gets back.

**The page and the parser.** The score is the one `table.Datatable` on the
page: a header of category codes (`title` holds the full name) and one row per
team - its name linked to `/hockey/<league>/<team number>`, then a cell per
category, then the categories won. A code ending `*` is shown but not scored
(Yahoo's GA*, SV*, SA* beside GAA and SV%). A dash is a category nobody has
recorded yet, not a zero. The week comes from the week picker's selected
option, whose value is `?week=N&module=matchup&mid1=K`.

Public leagues are fetched here with no sign-in, and private ones come in
through the same bookmarklet as the rosters, exactly as `yahoo_rosters`
describes. Its errors and fetch are reused rather than copied.

Author - Jason Druckenmiller
Created - 9/21/2026
Updated - 9/21/2026
"""

import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from yahoo_rosters import RosterPageError, fetch, league_id_from  # noqa: F401 - re-exported

MATCHUP_URL = "https://hockey.fantasysports.yahoo.com/hockey/{league_id}/matchup"

# Week 2 of the same completed public 2025-26 league the roster scrape tests
# against, from team 6's side - a finished week, so every category has a value.
TEST_MATCHUP_URL = ("https://hockey.fantasysports.yahoo.com/2025/hockey/22705/matchup"
                    "?week=2&module=matchup&mid1=6")

# Yahoo's column codes that differ from the ones `daily_value` uses.
CODE_FIXES = {"SV%": "SVpct"}


def matchup_url(league_id, week=None, team=None, test=False):
    """The Matchup page for a week, from one team's side."""
    if test:
        return TEST_MATCHUP_URL
    params = {}
    if week:
        params["week"] = int(week)
    if team:
        params["mid1"] = int(team)
    if params:
        params["module"] = "matchup"
    url = MATCHUP_URL.format(league_id=league_id_from(league_id))
    return f"{url}?{urlencode(params)}" if params else url


def _number(text):
    """A cell's value, or None for Yahoo's dash (nothing recorded yet)."""
    text = str(text or "").strip().replace(",", "")
    if text in ("", "-", "--"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse(html):
    """
    {week, categories: [{code, name, scored}], teams: [{name, yahooTeamId, stats, won}]}.

    `stats` is {code: value or None}, codes as `daily_value` spells them.
    Raises a RosterPageError when the document is one of Yahoo's other pages.
    """
    soup = BeautifulSoup(html or "", "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    table = soup.select_one("table.Datatable")

    if table is None or not table.select("thead th"):
        lowered = title.lower()
        if "sign in" in lowered or "login" in lowered:
            raise RosterPageError("private", "Yahoo showed a sign-in page instead of the matchup.")
        if "problem" in lowered or "not found" in lowered:
            raise RosterPageError(
                "not_found",
                "Yahoo has no league with that ID - check it under League > Settings.")
        raise RosterPageError(
            "unrecognised",
            "That page has no matchup score on it. Open your league's Matchup page.")

    # The first heading is "Team" and the last, untitled, is categories won
    headings = table.select("thead th")[1:]
    categories = []
    for th in headings:
        raw = th.get_text(strip=True)
        if not raw:
            categories.append(None)
            continue
        code = raw.rstrip("*")
        categories.append({
            "code": CODE_FIXES.get(code, code),
            "name": th.get("title") or code,
            "scored": not raw.endswith("*"),
        })

    teams = []
    for row in table.select("tbody tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        # Two links to the team - its logo, then its name - so take the one with text
        named = [a for a in cells[0].find_all("a", href=re.compile(r"/hockey/\d+/\d+/?$"))
                 if a.get_text(strip=True)]
        link = named[0] if named else None
        number = re.search(r"/(\d+)/?$", link["href"]).group(1) if link else None
        stats, won = {}, None
        for category, cell in zip(categories, cells[1:]):
            value = _number(cell.get_text(strip=True))
            if category is None:
                won = value
            else:
                stats[category["code"]] = value
        teams.append({
            "name": link.get_text(strip=True) if link else f"Team {len(teams) + 1}",
            "yahooTeamId": number,
            "stats": stats,
            "won": won,
        })

    if len(teams) != 2:
        raise RosterPageError("unrecognised", "That matchup does not show two teams.")

    week = None
    for option in soup.select("select option[selected]"):
        found = re.search(r"[?&]week=(\d+)", option.get("value", ""))
        if found:
            week = int(found.group(1))
            break

    return {
        "week": week,
        "categories": [c for c in categories if c is not None],
        "teams": teams,
    }
