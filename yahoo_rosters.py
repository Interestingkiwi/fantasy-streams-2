"""
Every team's roster in a Yahoo league, read off its Starting Rosters page.

Standalone mode needs every roster in the league - a free agent is anyone on
none of them - and typing in twelve rosters is the chore this removes. It reads
`/hockey/<league id>/startingrosters`, a single page listing every team and
every roster slot, and matches each name onto a `final_projections` playerId.

**Two ways the page arrives, one parser.** A league whose rosters are public
is fetched here, server-side, with no sign-in at all - verified with an empty
cookie jar, and a public page shows a "Sign in" link rather than a user. A
private league redirects that same request to `login.yahoo.com`, and nothing
server-side can get past it: the server has no Yahoo session, and the user
signing in to Yahoo in their own browser does not give it one. For those, the
page is read in the user's browser by a bookmarklet and posted here as HTML.
Either way `parse()` sees the same document.

**What the page does and does not carry.** Per team: its name and team number.
Per player: the roster slot (`C`, `BN`, `IR+`...), the name, and Yahoo's own
player id. *Not* the player's NHL team or positions - so a name that matches
two projected players (Vancouver carries two Elias Petterssons) or none is
looked up by Yahoo id on the public read-only API (`pub-api-ro`, the one the
ADP scrape uses), which returns both.

Author - Jason Druckenmiller
Created - 9/16/2026
Updated - 9/16/2026
"""

import re
import unicodedata
from collections import defaultdict
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROSTERS_URL = "https://hockey.fantasysports.yahoo.com/hockey/{league_id}/startingrosters"

# A completed public league, for building and testing against real markup
# without depending on anyone's current league or sign-in. The 2025-26 season
# is under /2025/, which is how Yahoo addresses a past season.
TEST_LEAGUE_ID = "22705"
TEST_ROSTERS_URL = "https://hockey.fantasysports.yahoo.com/2025/hockey/22705/startingrosters"

PLAYER_API = "https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2/players;player_keys={keys}?format=json_f"
PLAYER_API_BATCH = 25

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT_SECONDS = 30

# Roster slots whose players are not playing. IR and IR+ are Yahoo's injured
# reserve; NA holds players not on an NHL roster. All three are kept on the team
# - they are still rostered, so still not free agents - but marked out.
OUT_SLOTS = frozenset({"IR", "IR+", "NA"})

# Copies of the pipeline's `player_utils.normalise` and
# `scrape_yahoo_adp.TEAM_FIXES` / `POSITION_WIDENING`. The pipeline modules
# import its own database module at load and cannot be imported from the web
# app; `test_yahoo_rosters.py` fails if these copies drift from the originals.
TEAM_FIXES = {
    "LA": "LAK", "SJ": "SJS", "TB": "TBL", "NJ": "NJD",
    "MON": "MTL", "WAS": "WSH", "CLS": "CBJ", "ANH": "ANA",
    "WPG": "WPG", "VGK": "VGK", "UTA": "UTA",
}
POSITION_WIDENING = {"L": "LW", "R": "RW"}


class RosterPageError(Exception):
    """
    The page could not be read as a league's rosters. `code` is one of:
    `invalid_id`, `private`, `not_found`, `unrecognised`, `unreachable` - the
    page shows each differently, and `private` is the one with a way forward.
    """

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalise(name):
    """Lowercase, strip accents and punctuation so two sources line up."""
    name = unicodedata.normalize("NFKD", str(name))
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.lower().replace(".", "").replace("'", "").replace("-", " ")
    return re.sub(r"\s+", " ", name).strip()


def league_id_from(raw):
    """
    The numeric League ID from what a user typed.

    Accepts the bare number or any pasted Yahoo league URL, since the address
    bar is where most people will copy it from.
    """
    text = str(raw or "").strip()
    if re.fullmatch(r"\d{1,10}", text):
        return text
    found = re.search(r"/hockey/(?:\d{4}/hockey/)?(\d{1,10})(?:/|$)", text)
    if found:
        return found.group(1)
    raise RosterPageError(
        "invalid_id",
        "A League ID is a number - find it in Yahoo under League > Settings.")


def rosters_url(league_id, test=False):
    """The Starting Rosters address: the test league, or the user's current one."""
    return TEST_ROSTERS_URL if test else ROSTERS_URL.format(league_id=league_id_from(league_id))


def fetch(url, session=None):
    """
    The page's HTML, or a RosterPageError saying why it is not a rosters page.

    No cookies are sent, so this only ever sees what Yahoo shows the public.
    """
    session = session or requests.Session()
    try:
        response = session.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS,
                               allow_redirects=True)
    except requests.RequestException as exc:
        raise RosterPageError("unreachable", f"Could not reach Yahoo: {exc}") from exc

    if (urlparse(response.url).hostname or "").startswith("login."):
        raise RosterPageError(
            "private",
            "This league's rosters are private, so Yahoo asks for a sign-in that "
            "Fantasy Streams cannot make for you.")
    if response.status_code >= 400:
        raise RosterPageError("unreachable", f"Yahoo answered {response.status_code}.")
    return response.text


def parse(html):
    """
    {teams: [{name, yahooTeamId, players: [{name, yahooId, slot, out}]}]}.

    One table per team, `id="Tst-team-N"`, preceded by the team's name linked to
    `/.../hockey/<league>/<team number>`. Raises a RosterPageError when the
    document is one of Yahoo's other pages instead.
    """
    soup = BeautifulSoup(html or "", "lxml")
    title = soup.title.get_text(strip=True) if soup.title else ""
    tables = soup.select('table[id^="Tst-team-"]')

    if not tables:
        if "sign in" in title.lower() or "login" in title.lower():
            raise RosterPageError("private", "Yahoo showed a sign-in page instead of the rosters.")
        if "problem" in title.lower() or "not found" in title.lower():
            raise RosterPageError(
                "not_found",
                "Yahoo has no league with that ID - check it under League > Settings.")
        raise RosterPageError(
            "unrecognised",
            "That page has no team rosters on it. Open your league's Starting Rosters page.")

    teams = []
    for table in tables:
        link = table.find_previous("a", href=re.compile(r"/hockey/\d+/\d+/?$"))
        number = re.search(r"/(\d+)/?$", link["href"]).group(1) if link else None
        team = {
            "name": link.get_text(strip=True) if link else f"Team {len(teams) + 1}",
            "yahooTeamId": number,
            "players": [],
        }
        for row in table.select("tbody tr"):
            slot_cell = row.select_one("td.pos")
            name_link = row.select_one(".ysf-player-name a.name")
            if not (slot_cell and name_link):
                continue      # an empty slot
            slot = slot_cell.get_text(strip=True).upper()
            yahoo_id = re.search(r"/players/(\d+)", name_link.get("href", ""))
            team["players"].append({
                "name": name_link.get("title") or name_link.get_text(strip=True),
                "yahooId": yahoo_id.group(1) if yahoo_id else None,
                "slot": slot,
                "out": slot in OUT_SLOTS,
            })
        teams.append(team)
    return {"teams": teams}


def match(parsed, pool, aliases=None, lookup=None):
    """
    Attach a `playerId` to every scraped player that has a projection.

    `pool` is `final_projections` rows (`playerId`, `fullName`, `teamAbbrevs`,
    `positionCode`); `aliases` is {alias name: playerId}. A name matching
    exactly one player is taken; anything else is settled with the player's
    NHL team and positions from `lookup` - {yahooId: {team, positions}}, a
    callable given the ids that need it - first by team, then by position, then
    by alias, then by surname on the same team.

    Returns the teams with each player's `playerId` (None if unmatched) and the
    list of names that could not be matched, which are usually players no
    longer projected - retired, or in the minors.
    """
    by_name, by_surname_team = defaultdict(list), defaultdict(list)
    for row in pool:
        norm = normalise(row["fullName"])
        by_name[norm].append(row)
        for team in str(row.get("teamAbbrevs") or "").split(","):
            by_surname_team[(norm.split(" ")[-1], team.strip())].append(row)
    by_alias = {normalise(name): pid for name, pid in (aliases or {}).items()}

    players = [p for team in parsed["teams"] for p in team["players"]]
    needs_detail = [p["yahooId"] for p in players
                    if p["yahooId"] and len(by_name.get(normalise(p["name"]), [])) != 1]
    details = lookup(needs_detail) if (lookup and needs_detail) else {}

    unmatched = []
    for player in players:
        name = normalise(player["name"])
        candidates = by_name.get(name, [])
        detail = details.get(player["yahooId"]) or {}
        team = detail.get("team")

        if len(candidates) > 1 and team:
            narrowed = [c for c in candidates
                        if team in str(c.get("teamAbbrevs") or "").split(",")]
            candidates = narrowed or candidates
        if len(candidates) > 1 and detail.get("positions"):
            narrowed = [c for c in candidates
                        if POSITION_WIDENING.get(str(c.get("positionCode")).upper(),
                                                 str(c.get("positionCode")).upper())
                        in detail["positions"]]
            candidates = narrowed or candidates

        if len(candidates) == 1:
            player["playerId"] = candidates[0]["playerId"]
        elif name in by_alias:
            player["playerId"] = by_alias[name]
        else:
            fallback = by_surname_team.get((name.split(" ")[-1], team), []) if team else []
            player["playerId"] = fallback[0]["playerId"] if len(fallback) == 1 else None

        if player["playerId"] is None:
            unmatched.append(player["name"])

    return {"teams": parsed["teams"], "unmatched": unmatched}


def yahoo_details(yahoo_ids, session=None):
    """
    {yahooId: {team, positions}} from Yahoo's public read-only player API.

    Only asked for the few players a name alone cannot settle. A failure here
    costs those players their match, not the whole import, so errors are
    swallowed and the players simply stay unmatched.
    """
    session = session or requests.Session()
    found = {}
    ids = [i for i in dict.fromkeys(yahoo_ids) if i]
    for start in range(0, len(ids), PLAYER_API_BATCH):
        keys = ",".join(f"nhl.p.{i}" for i in ids[start:start + PLAYER_API_BATCH])
        try:
            response = session.get(PLAYER_API.format(keys=keys), headers=HEADERS,
                                   timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            entries = response.json()["fantasy_content"]["players"]
        except (requests.RequestException, ValueError, KeyError, TypeError):
            continue
        for entry in entries:
            player = (entry or {}).get("player") or {}
            key = str(player.get("player_key", ""))
            yahoo_id = key.rsplit(".", 1)[-1] if key else None
            if not yahoo_id:
                continue
            abbr = str(player.get("editorial_team_abbr") or "").upper()
            found[yahoo_id] = {
                "team": TEAM_FIXES.get(abbr, abbr),
                "positions": {p.strip().upper()
                              for p in str(player.get("display_position") or "").split(",")},
            }
    return found
