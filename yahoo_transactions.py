"""
A league's transactions - every add, drop and trade - from Yahoo.

The raw material for Season History: who picked up whom and when, which the
old site's transaction pages and "left on the bench" views were built from.
Two sources, because Yahoo serves public and private leagues differently, and
one shape out of both.

**Public leagues: Yahoo's public read-only API.** `pub-api-ro` - the anonymous
API behind Yahoo's own signed-out pages, which the ADP scrape already uses -
serves a public league's transactions as JSON: exact epoch timestamps, Yahoo's
team keys, and where each player came from and went to. 500 a request with
`start`/`count`, so a season is one or two. Checked against the Transactions
page on a completed 12-team season: 1,136 transactions, the page's 1,132
exactly plus 4 `commish` entries, which are commissioner settings changes with
no players and are dropped here. A private league answers 401, "You must be
logged in to view this league"; a league ID that does not exist answers 400.

**Private leagues: the Transactions page, read in the user's browser.** The
bookmarklet fetches every page (`?transactionsfilter=all&count=N`, 25 to a
page) from inside the signed-in Yahoo tab, keeps only each page's
`table.Tst-transaction-table` - about 20 KB of a 1 MB page - and posts them
together. `parse_pages` reads them:

- One row per add, drop or add/drop: an icon per player titled "Added Player"
  or "Dropped Player", paired in order with a `div.Pbot-xs` per player (link
  to `/nhl/players/<Yahoo id>`, "BOS - LW,RW", and an `h6` saying "Free Agent",
  "Waiver", "To Waivers" or "To Free Agent"), then the team and the time.
- A trade is **two** rows: the first carries a trade icon in a `rowspan=2`
  cell. Each row lists what one side received - players, and draft picks as
  "Round N" - then "Traded to" and that side's team.

**Page times have no year and no zone** ("Sep 29, 8:31 am"). Signed out, the
page shows US Eastern: all 1,132 matched the API's epochs to the minute in
America/New_York and in no other zone tried. Signed in it is presumably the
user's own zone, so the bookmarklet sends the browser's - not verified, for
want of a private league to check against. Years are inferred walking back
from the newest row: a month and day later than the row before it means New
Year was crossed.

**The shape**, compact because it is kept in localStorage and synced to
accounts - a busy season is 1,100+ transactions:

    {league: {name, season}, teams: {number: name},
     players: {yahooId: [name, nhlTeam, positions]},
     transactions: [{id, type, time, moves: [[yahooId, from, to]],
                     picks: [[round, from, to]]}]}

`type` is add, drop, add/drop or trade; `time` epoch seconds; `from` and `to`
a Yahoo team number, "freeagents" or "waivers". Team numbers match the roster
scrape's `yahooTeamId`. `id` is Yahoo's transaction id from the API, and None
from the page, which does not show one.

Author - Jason Druckenmiller
Created - 9/29/2026
Updated - 10/3/2026
"""

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from bs4 import BeautifulSoup

from yahoo_rosters import HEADERS, TIMEOUT_SECONDS, RosterPageError, league_id_from

API_BASE = "https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2"
API_PAGE = 500
MAX_API_PAGES = 20
PAGE_URL = "https://hockey.fantasysports.yahoo.com/hockey/{league_id}/transactions"

# The completed public 2025-26 league the roster and matchup scrapes test
# against. 465 is Yahoo's game key for the 2025-26 NHL season.
TEST_LEAGUE_KEY = "465.l.22705"
TEST_PAGE_URL = "https://hockey.fantasysports.yahoo.com/2025/hockey/22705/transactions"

# What a signed-out Transactions page is shown in - see the module docstring.
PAGE_TIME_ZONE = "America/New_York"

MONTHS = {name: n for n, name in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
TIME = re.compile(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{1,2}):(\d{2})\s*([ap]m)", re.I)
TEAM_HREF = re.compile(r"/hockey/(?:\d{4}/hockey/)?\d+/(\d+)(?:[/?#]|$)")
PLAYER_HREF = re.compile(r"/nhl/players/(\d+)")
SEASON_URL = re.compile(r"/(\d{4})/hockey/")


def page_url(league_id, test=False):
    """The Transactions page to open on Yahoo - for the bookmarklet's link."""
    return TEST_PAGE_URL if test else PAGE_URL.format(league_id=league_id_from(league_id))


def league_key(league_id, test=False):
    """Yahoo's key for the league. `nhl.l.N` is the current season's."""
    return TEST_LEAGUE_KEY if test else f"nhl.l.{league_id_from(league_id)}"


# --------------------------------------------------------------------- the API

def api_error(response):
    if response.status_code == 401:
        return RosterPageError(
            "private",
            "This league is private, so Yahoo only shows its transactions to someone signed in.")
    if response.status_code in (400, 404):
        # Yahoo words a league that does not exist as "a temporary problem
        # with the server", so its description would only mislead here
        return RosterPageError(
            "not_found",
            "Yahoo could not find that league this season. Check the League ID - if it is right, "
            "Yahoo may be having a moment, so try again shortly.")
    return RosterPageError("unreachable", f"Yahoo answered {response.status_code}.")


def fetch_api(league_id, test=False, session=None, since=None):
    """
    ({name, season}, [raw transaction]) for a public league, every page of it
    - or, given `since` (a transaction id already stored), only the pages down
    to the one holding it. Newest first, so an update reads one page however
    long the season has run, and the caller merges by id. Every transaction on
    those pages comes back, not just the ones newer than `since`, so a claim
    Yahoo settled late within them is not missed.
    Raises RosterPageError: `private`, `not_found`, `unreachable`.
    """
    session = session or requests.Session()
    key = league_key(league_id, test)
    league, found = {}, []
    for page in range(MAX_API_PAGES):
        url = (f"{API_BASE}/league/{key}/transactions;start={page * API_PAGE};"
               f"count={API_PAGE}?format=json_f")
        try:
            response = session.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise RosterPageError("unreachable", f"Could not reach Yahoo: {exc}") from exc
        if response.status_code != 200:
            raise api_error(response)
        try:
            content = response.json()["fantasy_content"]["league"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RosterPageError("unrecognised", "Yahoo sent something other than a league.") from exc
        league = league or {"name": content.get("name"), "season": content.get("season")}
        batch = content.get("transactions") or []
        found.extend(batch)
        if len(batch) < API_PAGE or (since and since in transaction_ids(batch)):
            break
    return league, found


def transaction_ids(raw):
    """The transaction ids in a list of the API's raw transactions, as strings."""
    return {str((w.get("transaction") or {}).get("transaction_id")) for w in raw}


def _team_number(team_key):
    """'477.l.5848.t.6' -> '6'."""
    found = re.search(r"\.t\.(\d+)$", str(team_key or ""))
    return found.group(1) if found else None


def _end(kind, team_key):
    """One end of a move: a team number, or 'freeagents' / 'waivers'."""
    if kind == "team":
        return _team_number(team_key)
    return kind if kind in ("freeagents", "waivers") else None


def from_api(league, raw):
    """The compact shape from the API's transactions, newest first."""
    teams, players, transactions = {}, {}, []
    for wrapper in raw:
        item = wrapper.get("transaction") or {}
        kind = item.get("type")
        if item.get("status") != "successful" or kind not in ("add", "drop", "add/drop", "trade"):
            continue
        moves = []
        for entry in item.get("players") or []:
            player = entry.get("player") or {}
            data = player.get("transaction_data") or {}
            if isinstance(data, list):
                data = data[0] if data else {}
            yahoo_id = str(player.get("player_id") or "")
            if not yahoo_id:
                continue
            players[yahoo_id] = [(player.get("name") or {}).get("full") or "",
                                 player.get("editorial_team_abbr") or "",
                                 player.get("display_position") or ""]
            for side in ("source", "destination"):
                number = _team_number(data.get(f"{side}_team_key"))
                if number and data.get(f"{side}_team_name"):
                    teams[number] = data[f"{side}_team_name"]
            moves.append([yahoo_id,
                          _end(data.get("source_type"), data.get("source_team_key")),
                          _end(data.get("destination_type"), data.get("destination_team_key"))])
        for side in ("trader", "tradee"):
            number = _team_number(item.get(f"{side}_team_key"))
            if number and item.get(f"{side}_team_name"):
                teams[number] = item[f"{side}_team_name"]
        record = {"id": str(item.get("transaction_id") or "") or None, "type": kind,
                  "time": int(item.get("timestamp") or 0) or None, "moves": moves}
        picks = [[int(p["pick"].get("round") or 0), _team_number(p["pick"].get("source_team_key")),
                  _team_number(p["pick"].get("destination_team_key"))]
                 for p in item.get("picks") or [] if p.get("pick")]
        if picks:
            record["picks"] = picks
        transactions.append(record)
    return {"league": league, "teams": teams, "players": players, "transactions": transactions}


# -------------------------------------------------------------------- the page

def zone(name):
    """A ZoneInfo for the name the bookmarklet sent, or Eastern if it is not one."""
    try:
        return ZoneInfo(str(name)) if name else ZoneInfo(PAGE_TIME_ZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(PAGE_TIME_ZONE)


def season_from_url(url):
    """2025 for a /2025/hockey/ page, None for the current season's."""
    found = SEASON_URL.search(str(url or ""))
    return int(found.group(1)) if found else None


def _times(texts, tz, anchor):
    """
    Epoch seconds for the page's "Mon D, h:mm am" stamps, newest first.

    Years come from walking back from `anchor` (today, or the summer after a
    past season): a month and day later than the one before means the year
    turned. A stamp that does not parse is None and leaves the walk alone.
    """
    year, previous = anchor.year, (anchor.month, anchor.day)
    out = []
    for text in texts:
        found = TIME.search(text or "")
        month = MONTHS.get(found.group(1).title()) if found else None
        if not month:
            out.append(None)
            continue
        day, hour, minute = int(found.group(2)), int(found.group(3)) % 12, int(found.group(4))
        if found.group(5).lower() == "pm":
            hour += 12
        if (month, day) > previous:
            year -= 1
        previous = (month, day)
        try:
            local = datetime(year, month, day, hour, minute, tzinfo=tz)
        except ValueError:                        # Feb 29 in the wrong year
            out.append(None)
            continue
        out.append(int(local.timestamp()))
    return out


def _player(anchor, span, players):
    found = PLAYER_HREF.search(anchor.get("href", ""))
    if not found:
        return None
    team, _, positions = (span.get_text(" ", strip=True) if span else "").partition(" - ")
    players[found.group(1)] = [anchor.get_text(strip=True), team.strip(), positions.strip()]
    return found.group(1)


def _team_of(row, teams):
    """The team a row belongs to, from the link in its last cell."""
    cell = row.find_all("td", recursive=False)[-1]
    for link in cell.find_all("a", href=True):
        found = TEAM_HREF.search(link["href"])
        if found and link.get_text(strip=True):
            teams[found.group(1)] = link.get_text(strip=True)
            return found.group(1)
    return None


def _stamp(row):
    stamp = row.select_one(".F-timestamp")
    return stamp.get_text(" ", strip=True) if stamp else ""


def _trade_side(row, players, teams):
    """(team number, [yahoo ids], [rounds]) that one trade row's team received."""
    team = _team_of(row, teams)
    received, rounds = [], []
    for para in row.select("td.No-pstart p"):
        link = para.find("a", href=PLAYER_HREF)
        if link:
            yahoo_id = _player(link, para.select_one(".F-position"), players)
            if yahoo_id:
                received.append(yahoo_id)
            continue
        found = re.search(r"Round\s+(\d+)", para.get_text(" ", strip=True))
        if found:
            rounds.append(int(found.group(1)))
    return team, received, rounds


def parse_pages(html, tz_name=None, url=None, today=None):
    """
    The compact shape from Transactions page tables, newest first - every
    `table.Tst-transaction-table` in `html`, in order. Raises RosterPageError
    `unrecognised` when there are none.
    """
    soup = BeautifulSoup(html or "", "lxml")
    tables = soup.select("table.Tst-transaction-table")
    if not tables:
        raise RosterPageError("unrecognised", "That is not a Yahoo Transactions page.")

    tz = zone(tz_name)
    season = season_from_url(url)
    anchor = date(season + 1, 7, 1) if season else (today or datetime.now(tz).date())

    teams, players, pending, stamps = {}, {}, [], []
    rows = [row for table in tables for row in table.find_all("tr")]
    index = 0
    while index < len(rows):
        row = rows[index]
        index += 1
        if row.select_one(".F-trade"):
            # A trade: this row and the next are the two sides
            team_a, got_a, picks_a = _trade_side(row, players, teams)
            team_b, got_b, picks_b = (None, [], [])
            if index < len(rows) and not rows[index].select_one("td span.F-icon"):
                team_b, got_b, picks_b = _trade_side(rows[index], players, teams)
                index += 1
            moves = ([[p, team_b, team_a] for p in got_a] + [[p, team_a, team_b] for p in got_b])
            record = {"id": None, "type": "trade", "moves": moves}
            picks = [[r, team_b, team_a] for r in picks_a] + [[r, team_a, team_b] for r in picks_b]
            if picks:
                record["picks"] = picks
            pending.append(record)
            stamps.append(_stamp(row))
            continue

        icons = [span.get("title", "") for span in row.select("td:first-child span.F-icon[title]")]
        entries = row.select("div.Pbot-xs")
        if not icons or not entries:
            continue
        team = _team_of(row, teams)
        moves, actions = [], set()
        for icon, entry in zip(icons, entries):
            link = entry.find("a", href=PLAYER_HREF)
            yahoo_id = _player(link, entry.select_one(".F-position"), players) if link else None
            if not yahoo_id:
                continue
            note = (entry.find("h6").get_text(" ", strip=True) if entry.find("h6") else "").lower()
            if icon.startswith("Added"):
                actions.add("add")
                moves.append([yahoo_id, "waivers" if note.startswith("waiver") else "freeagents", team])
            elif icon.startswith("Dropped"):
                actions.add("drop")
                moves.append([yahoo_id, team, "freeagents" if "free agent" in note else "waivers"])
        if not moves:
            continue
        kind = "add/drop" if actions == {"add", "drop"} else actions.pop()
        pending.append({"id": None, "type": kind, "moves": moves})
        stamps.append(_stamp(row))

    for record, when in zip(pending, _times(stamps, tz, anchor)):
        record["time"] = when
    return {"league": {"name": None, "season": str(season or _season_of(anchor))},
            "teams": teams, "players": players, "transactions": pending}


def _season_of(day):
    """The NHL season a date falls in, by its starting year."""
    return day.year if day.month >= 7 else day.year - 1


def utc(epoch):
    """An epoch as an aware UTC datetime, for tests and logs."""
    return datetime.fromtimestamp(epoch, timezone.utc)
