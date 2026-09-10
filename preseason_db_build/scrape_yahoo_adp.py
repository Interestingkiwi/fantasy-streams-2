"""
Current-season Average Draft Position from Yahoo, into `player_adp`.

ADP is the one number on the draft board that is still moving. Every mock and
every real draft between now and yours changes it, which is why this is a
standalone script rather than a pipeline step: run it as often as you like,
right up to draft morning, without rebuilding a single projection.

**It is not a page scrape.** `hockey.fantasysports.yahoo.com/hockey/draftanalysis`
renders its table client-side and paginates without touching the URL, so
scraping it would mean driving a browser and watching rows change. It does not
have to: the page feeds off Yahoo's *public read-only* fantasy API, and that
takes `start`, `count` and `sort` as ordinary parameters. Same numbers, no
browser, no DOM, and pagination that cannot silently repeat a page.

This is `pub-api-ro`, not the gated `pub-api-rw` API that Phase 2 is waiting on
- no OAuth, no crumb, no login. It is the endpoint the public page uses to
serve anonymous visitors, and it is read-only.

The list is sorted by `average_pick`, so it arrives best-pick-first and the
players nobody drafts are at the end carrying `"-"` instead of a number. That
dash is the stopping signal: the first one means every row after it is
undrafted too, so paging stops there rather than walking a few thousand rows
to collect nothing. Roughly 265 players have a real ADP in the preseason.

The season is read from `/game/nhl` rather than written down. Yahoo keys each
season with a game id - 477 is 2026-27 - and hardcoding it would quietly scrape
last season a year from now.

Yahoo identifies players by its own id, so each row is crosswalked onto the NHL
`playerId` everything else here uses, by name and team against
`player_directory`. Rows that fail to match are still stored with a null
`playerId`, because an unmatched name is worth seeing rather than dropping.

    cd preseason_db_build && python scrape_yahoo_adp.py

Author - Jason Druckenmiller
Created - 9/10/2026
Updated - 9/10/2026
"""

import time
from datetime import datetime

import pandas as pd
import requests

from db_config import engine
from player_utils import build_indexes, normalise

BASE = "https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2"
TABLE = "player_adp"

# The page asks for 30 at a time; the API is happy with far more, and every
# extra round trip is another chance to be rate-limited.
PAGE_SIZE = 100

# What Yahoo puts in average_pick for a player no one has drafted.
NO_ADP = "-"

# Paging stops on the first dash, so this only matters if Yahoo ever stops
# sorting - without it a sort change would walk the whole player pool.
MAX_PAGES = 40

PAUSE_SECONDS = 0.5

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept": "application/json",
    "Referer": "https://hockey.fantasysports.yahoo.com/",
}

# Yahoo and the NHL disagree on a handful of tricodes. Only the differences are
# listed; anything absent is already identical.
TEAM_FIXES = {
    "LA": "LAK", "SJ": "SJS", "TB": "TBL", "NJ": "NJD",
    "MON": "MTL", "WAS": "WSH", "CLS": "CBJ", "ANH": "ANA",
    "WPG": "WPG", "VGK": "VGK", "UTA": "UTA",
}


def game_key(session):
    """Yahoo's id for the current NHL season, e.g. 477 for 2026-27."""
    response = session.get(f"{BASE}/game/nhl?format=json_f", headers=HEADERS, timeout=30)
    response.raise_for_status()
    game = response.json()["fantasy_content"]["game"]
    print(f" -> Yahoo game {game['game_key']} ({game['season']}-"
          f"{int(game['season']) + 1}).")
    return game["game_key"]


def fetch_page(session, key, start):
    """One page of players, best average pick first."""
    url = (f"{BASE}/league/{key}.l.public/players;position=ALL;"
           f"start={start};count={PAGE_SIZE};sort=average_pick;"
           f"out=draft_analysis?format=json_f")
    response = session.get(url, headers=HEADERS, timeout=45)
    response.raise_for_status()
    league = response.json()["fantasy_content"]["league"]
    return league.get("players") or []


def to_number(value):
    """Yahoo sends numbers as strings, and absences as a dash."""
    if value is None or value == NO_ADP or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse(entry):
    """One API player -> one flat row, or None if it has no ADP yet."""
    player = entry.get("player") or {}
    analysis = player.get("draft_analysis") or {}

    adp = to_number(analysis.get("average_pick"))
    if adp is None:
        return None

    team = str(player.get("editorial_team_abbr") or "").upper()

    return {
        "yahooPlayerId": player.get("player_id"),
        "playerName": (player.get("name") or {}).get("full"),
        "teamAbbrev": TEAM_FIXES.get(team, team),
        "displayPosition": player.get("display_position"),
        "adp": adp,
        "adpRound": to_number(analysis.get("average_round")),
        "percentDrafted": to_number(analysis.get("percent_drafted")),
        "preseasonAdp": to_number(analysis.get("preseason_average_pick")),
        "averageCost": to_number(analysis.get("average_cost")),
    }


def collect(session, key):
    """Every player with an ADP, stopping at the first one without."""
    rows = []

    for page in range(MAX_PAGES):
        start = page * PAGE_SIZE
        entries = fetch_page(session, key, start)

        if not entries:
            print(f" -> Page {page + 1}: empty, the pool ends here.")
            break

        page_rows = []
        exhausted = False
        for entry in entries:
            row = parse(entry)
            if row is None:
                exhausted = True
                break
            page_rows.append(row)

        rows.extend(page_rows)
        print(f" -> Page {page + 1} (start={start}): {len(page_rows)} with an ADP"
              + (", then the first player nobody drafts." if exhausted else "."))

        if exhausted:
            break

        time.sleep(PAUSE_SECONDS)
    else:
        print(f" -> [WARN] Stopped at the {MAX_PAGES}-page ceiling without finding "
              "an undrafted player. Yahoo may have changed the sort; check the data.")

    return rows


# The NHL's one-letter wings against Yahoo's two-letter ones, so the two can be
# compared when a name and a team are not enough on their own.
POSITION_WIDENING = {"L": "LW", "R": "RW"}


def widen(position):
    return POSITION_WIDENING.get(str(position).upper(), str(position).upper())


def crosswalk(rows):
    """Attach the NHL playerId each row belongs to, where one can be found.

    Matched against `final_projections` rather than `player_directory`, because
    that is the set that can actually display an ADP - and it is the only one
    holding the imported rookies, who are added two steps after the directory is
    built. Matching on the directory silently lost every prospect with a real
    ADP and no NHL history.
    """
    targets = pd.read_sql(
        'SELECT "playerId", "fullName", "teamAbbrevs", "positionCode" FROM final_projections',
        con=engine)
    aliases = pd.read_sql("SELECT player_id, alias_name FROM player_aliases", con=engine)
    return match_rows(rows, targets, aliases)


def match_rows(rows, targets, aliases):
    """The matching itself, against frames rather than the database.

    Split out so the disambiguation can be tested on its own - the cases that
    matter are rare in the wild and awkward to arrange in a real table.
    """
    by_name, by_surname_team = build_indexes(targets)
    by_alias = {normalise(name): pid
                for pid, name in zip(aliases["player_id"], aliases["alias_name"])}

    counts = {"exact": 0, "team": 0, "position": 0, "surname": 0,
              "alias": 0, "unmatched": 0}
    unmatched = []

    for row in rows:
        name = normalise(row["playerName"])
        team = row["teamAbbrev"]
        candidates = by_name.get(name, [])
        kind = "exact"

        # Two players share a name often enough to matter; the team splits most
        if len(candidates) > 1:
            narrowed = [c for c in candidates
                        if team in str(c.teamAbbrevs).split(",")]
            if narrowed:
                candidates, kind = narrowed, "team"

        # ...and where it does not, the position does. Vancouver really does
        # carry two Elias Petterssons, a centre and a defenceman.
        if len(candidates) > 1:
            wanted = {p.strip() for p in str(row["displayPosition"]).upper().split(",")}
            narrowed = [c for c in candidates if widen(c.positionCode) in wanted]
            if narrowed:
                candidates, kind = narrowed, "position"

        if len(candidates) == 1:
            row["playerId"] = int(candidates[0].playerId)
            counts[kind] += 1
            continue

        if name in by_alias:
            row["playerId"] = int(by_alias[name])
            counts["alias"] += 1
            continue

        # Nickname or transliteration gap: same surname, same team is specific
        surname = name.split(" ")[-1] if name else ""
        fallbacks = by_surname_team.get((surname, team), [])
        if len(fallbacks) == 1:
            row["playerId"] = int(fallbacks[0].playerId)
            counts["surname"] += 1
            continue

        row["playerId"] = None
        counts["unmatched"] += 1
        unmatched.append(f"{row['playerName']} ({team})")

    return counts, unmatched


def main():
    print("--- FETCHING YAHOO ADP ---")
    session = requests.Session()

    key = game_key(session)
    rows = collect(session, key)

    if not rows:
        raise SystemExit("No ADP rows returned. Yahoo's public API may have moved; "
                         "nothing was written.")

    counts, unmatched = crosswalk(rows)

    frame = pd.DataFrame(rows)
    frame["scrapedAt"] = datetime.now()
    frame = frame[["playerId", "yahooPlayerId", "playerName", "teamAbbrev",
                   "displayPosition", "adp", "adpRound", "percentDrafted",
                   "preseasonAdp", "averageCost", "scrapedAt"]]
    frame.to_sql(TABLE, con=engine, if_exists="replace", index=False)

    print(f"\n{len(frame)} players with an ADP written to '{TABLE}' "
          f"(best {frame['adp'].min():.1f}, worst {frame['adp'].max():.1f}).")
    print(f" -> Crosswalked to NHL ids: {counts['exact']} on name, "
          f"{counts['team']} on name + team, {counts['position']} on name + position, "
          f"{counts['surname']} on surname + team, {counts['alias']} on a stored alias.")

    if unmatched:
        print(f" -> [WARN] {len(unmatched)} without an NHL id (stored, but they will "
              "not show an ADP on the board):")
        for name in unmatched[:20]:
            print(f"      {name}")
        if len(unmatched) > 20:
            print(f"      ... and {len(unmatched) - 20} more")
        print("    Fix a genuine miss with player_utils.add_player_alias(nhl_id, "
              "'Yahoo Name').")


if __name__ == "__main__":
    main()
