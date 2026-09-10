"""
Tests for the Yahoo ADP scrape.

Two things here can fail silently and convincingly, which is why they are
pinned hardest. The first is the stopping rule: the feed is sorted by average
pick and trails off into hundreds of undrafted players carrying a dash, so
paging has to stop at the first one - stopping late wastes requests, stopping
*early* would quietly truncate the board. The second is the crosswalk, because
an unmatched player does not error, he just silently has no ADP on the board.

Vancouver really does carry two Elias Petterssons, a centre and a defenceman,
and they are the reason position is a matching pass rather than a nicety.

Pure logic against stubs - no database and no network.

Author - Jason Druckenmiller
Created - 9/10/2026
Updated - 9/10/2026
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "preseason_db_build"))

import scrape_yahoo_adp as adp                              # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def entry(name, team="EDM", position="C", pick="1.5", pid="1"):
    """One player as the API shapes them."""
    return {"player": {
        "player_id": pid,
        "name": {"full": name},
        "editorial_team_abbr": team,
        "display_position": position,
        "draft_analysis": {
            "average_pick": pick,
            "average_round": "1.0",
            "average_cost": "63.3",
            "percent_drafted": "1.00",
            "preseason_average_pick": "1.6",
        },
    }}


class StubSession:
    """Serves canned pages, and records what was asked for."""

    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def get(self, url, headers=None, timeout=None):
        self.requested.append(url)
        page = self.pages.pop(0) if self.pages else []
        return StubResponse({"fantasy_content": {"league": {"players": page}}})


class StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


# --------------------------------------------------------------------------
print("\n=== 1. reading Yahoo's numbers ===")

check("a decimal string becomes a number", adp.to_number("12.4") == 12.4)
check("a dash is an absence, not a zero", adp.to_number("-") is None)
check("so is an empty string", adp.to_number("") is None)
check("so is nothing at all", adp.to_number(None) is None)
check("and so is something unparseable", adp.to_number("n/a") is None)
check("zero survives as zero", adp.to_number("0") == 0.0)

row = adp.parse(entry("Connor McDavid", team="EDM", position="C", pick="1.5"))
check("a drafted player parses", row is not None)
check("...carrying his ADP", row["adp"] == 1.5)
check("...his name and position", row["playerName"] == "Connor McDavid"
      and row["displayPosition"] == "C")
check("...and the preseason figure separately", row["preseasonAdp"] == 1.6)
check("an undrafted player parses to nothing",
      adp.parse(entry("Nobody", pick="-")) is None)

check("Yahoo's tricodes are translated to the NHL's",
      adp.parse(entry("A Kings Player", team="LA"))["teamAbbrev"] == "LAK")
check("...and one that already agrees is left alone",
      adp.parse(entry("An Oiler", team="EDM"))["teamAbbrev"] == "EDM")


# --------------------------------------------------------------------------
print("\n=== 2. knowing when to stop paging ===")

full_page = [entry(f"Player {i}", pick=str(i + 1.0), pid=str(i)) for i in range(adp.PAGE_SIZE)]
tail = ([entry("Last Drafted", pick="150.0", pid="900")]
        + [entry("Undrafted One", pick="-", pid="901")]
        + [entry("Undrafted Two", pick="-", pid="902")])

session = StubSession([list(full_page), list(tail), list(full_page)])
rows = adp.collect(session, "477")

check("every drafted player is kept", len(rows) == adp.PAGE_SIZE + 1,
      f"{len(rows)}")
check("the last one before the dash is the last one kept",
      rows[-1]["playerName"] == "Last Drafted")
check("no undrafted player is kept",
      not any(r["playerName"].startswith("Undrafted") for r in rows))
check("paging stops at the dash rather than reading the next page",
      len(session.requested) == 2, f"{len(session.requested)} requests")

check("the pages asked for step by the page size",
      f"start=0;count={adp.PAGE_SIZE}" in session.requested[0]
      and f"start={adp.PAGE_SIZE};count={adp.PAGE_SIZE}" in session.requested[1])
check("and they are sorted by average pick, or the stopping rule means nothing",
      all("sort=average_pick" in url for url in session.requested))

check("an empty page also ends the walk",
      len(adp.collect(StubSession([list(tail[:1]), []]), "477")) == 1)


# --------------------------------------------------------------------------
print("\n=== 3. finding the same player in our own tables ===")

targets = pd.DataFrame([
    {"playerId": 8478402, "fullName": "Connor McDavid", "teamAbbrevs": "EDM", "positionCode": "C"},
    {"playerId": 8480012, "fullName": "Elias Pettersson", "teamAbbrevs": "VAN", "positionCode": "C"},
    {"playerId": 8483678, "fullName": "Elias Pettersson", "teamAbbrevs": "VAN", "positionCode": "D"},
    {"playerId": 8477969, "fullName": "Marcus Pettersson", "teamAbbrevs": "NYR", "positionCode": "D"},
    {"playerId": 8471214, "fullName": "Alex Ovechkin", "teamAbbrevs": "WSH", "positionCode": "L"},
    {"playerId": 8484144, "fullName": "Connor Bedard", "teamAbbrevs": "CHI", "positionCode": "C"},
])
no_aliases = pd.DataFrame({"player_id": [], "alias_name": []})


def matched(name, team, position, targets=targets, aliases=no_aliases):
    rows = [{"playerName": name, "teamAbbrev": team, "displayPosition": position}]
    counts, unmatched = adp.match_rows(rows, targets, aliases)
    return rows[0]["playerId"], counts, unmatched


pid, counts, _ = matched("Connor McDavid", "EDM", "C")
check("a unique name matches on the name alone", pid == 8478402 and counts["exact"] == 1)

# The case the position pass exists for.
pid, counts, _ = matched("Elias Pettersson", "VAN", "C")
check("two players, one name, one team - the centre is found by position",
      pid == 8480012 and counts["position"] == 1, f"{pid}")

pid, counts, _ = matched("Elias Pettersson", "VAN", "D")
check("...and so is the defenceman", pid == 8483678 and counts["position"] == 1, f"{pid}")

check("a shared surname on another team does not collide",
      matched("Marcus Pettersson", "NYR", "D")[0] == 8477969)

# Yahoo says LW where the NHL says L; the widening is what makes them comparable
pid, _, _ = matched("Alex Ovechkin", "WSH", "LW")
check("Yahoo's LW is matched against the NHL's L", pid == 8471214)

pid, counts, unmatched = matched("Nobody At All", "TOR", "C")
check("a player we do not carry is left unmatched, not guessed at",
      pid is None and counts["unmatched"] == 1)
check("...and is reported by name so it can be looked at",
      unmatched == ["Nobody At All (TOR)"], f"{unmatched}")

# An alias is the escape hatch for a name Yahoo spells differently
aliases = pd.DataFrame({"player_id": [8484144], "alias_name": ["Connor Bédard"]})
pid, counts, _ = matched("Connor Bedard", "CHI", "C", aliases=aliases)
check("an exact name still wins over an alias", pid == 8484144 and counts["exact"] == 1)

pid, counts, _ = matched("Some Other Spelling", "CHI", "C",
                         aliases=pd.DataFrame({"player_id": [8484144],
                                               "alias_name": ["Some Other Spelling"]}))
check("and an alias catches a name we would otherwise miss",
      pid == 8484144 and counts["alias"] == 1)

# Accents and punctuation must not decide whether a player has an ADP
pid, _, _ = matched("Connor  McDavid", "EDM", "C")
check("doubled whitespace does not break the match", pid == 8478402)


# --------------------------------------------------------------------------
print(f"\n{'=' * 60}")
if FAILURES:
    print(f"FAILED {len(FAILURES)}: {', '.join(FAILURES)}")
    sys.exit(1)
print("All ADP checks passed.")
