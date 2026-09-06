"""
Fills final_projections."eligiblePositions" with Yahoo multi-position eligibility.

The NHL API hands out one primary position per player (C / L / R / D / G), but
fantasy rosters run on Yahoo's eligibility, where a player can fill several
slots - Draisaitl is C,LW, Peterka is LW,RW. Replacement-level ranking needs
that full set, because a multi-eligible player slots in wherever they help most.

position_eligibility.csv is the Yahoo export (playerName, team,
eligiblePositions). Players it does not cover - depth guys, most goalies - fall
back to their NHL primary widened to Yahoo's vocabulary (L -> LW, R -> RW), so
every row ends up with a usable value.

Matching is exact on normalised name first, then surname + team, which is enough
for the nickname and transliteration gaps between the two sources
(JJ Peterka / John-Jason Peterka, Freddy / Frederick Gaudreau).

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

import os
import re
import unicodedata
from collections import defaultdict

import pandas as pd
from sqlalchemy import text

from db_config import engine

ELIGIBILITY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "position_eligibility.csv")
TABLE = "final_projections"

# The source writes four teams with dots; the rest of the database uses NHL tricodes.
TEAM_FIX = {"L.A": "LAK", "N.J": "NJD", "S.J": "SJS", "T.B": "TBL"}

# NHL primary -> Yahoo slot vocabulary
PRIMARY_TO_YAHOO = {"C": "C", "L": "LW", "R": "RW", "D": "D", "G": "G"}

VALID_POSITIONS = ["C", "LW", "RW", "D", "G"]


def normalise(name):
    """Lowercase, strip accents and punctuation so the two sources line up."""
    name = unicodedata.normalize("NFKD", str(name))
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = name.lower().replace(".", "").replace("'", "").replace("-", " ")
    return re.sub(r"\s+", " ", name).strip()


def split_disambiguator(name):
    """
    'Elias Pettersson (D)' -> ('Elias Pettersson', 'D').

    The source appends a position in brackets when two players share a name.
    """
    match = re.match(r"^(.*?)\s*\(([A-Z]{1,2})\)\s*$", str(name).strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return str(name).strip(), None


def clean_positions(raw):
    """'C, LW' -> 'C,LW', dropping anything outside the Yahoo vocabulary."""
    parts = [p.strip().upper() for p in str(raw).split(",")]
    keep = [p for p in parts if p in VALID_POSITIONS]
    # Keep a stable C / LW / RW / D / G ordering rather than the source's
    return ",".join(sorted(set(keep), key=VALID_POSITIONS.index))


def fallback_positions(primary):
    """Widen an NHL primary code to the Yahoo vocabulary."""
    return PRIMARY_TO_YAHOO.get(str(primary).strip().upper(), str(primary).strip().upper())


def ensure_column(conn):
    exists = conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :table AND column_name = 'eligiblePositions'
    """), {"table": TABLE}).scalar()

    if not exists:
        print(' -> Adding "eligiblePositions" column.')
        conn.execute(text(f'ALTER TABLE {TABLE} ADD COLUMN "eligiblePositions" TEXT'))


def build_indexes(players):
    """Name -> rows, and (surname, team) -> rows, for the two matching passes."""
    by_name = defaultdict(list)
    by_surname_team = defaultdict(list)

    for row in players.itertuples():
        norm = normalise(row.fullName)
        by_name[norm].append(row)

        surname = norm.split(" ")[-1] if norm else ""
        # teamAbbrevs can hold several teams for a player who moved mid-season
        for team in str(row.teamAbbrevs).split(","):
            by_surname_team[(surname, team.strip())].append(row)

    return by_name, by_surname_team


def resolve(name, team, positions, by_name, by_surname_team):
    """Return the matching DB row, or None. (row, match_type)"""
    bare_name, hint = split_disambiguator(name)
    norm = normalise(bare_name)

    candidates = by_name.get(norm, [])

    if len(candidates) > 1 and team:
        narrowed = [c for c in candidates if team in str(c.teamAbbrevs).split(",")]
        if narrowed:
            candidates = narrowed

    # Two players, same name, same team - the source's bracketed position breaks
    # the tie, and where it left the brackets off its eligibility list will do
    if len(candidates) > 1:
        wanted = {hint.upper()} if hint else set(positions.split(","))
        narrowed = [c for c in candidates
                    if fallback_positions(c.positionCode) in wanted]
        if narrowed:
            candidates = narrowed

    if len(candidates) == 1:
        return candidates[0], "exact"
    if len(candidates) > 1:
        return None, "ambiguous"

    # Nickname or transliteration gap: same surname, same team is specific enough
    surname = norm.split(" ")[-1] if norm else ""
    fallbacks = by_surname_team.get((surname, team), [])

    if len(fallbacks) > 1 and positions:
        wanted = set(positions.split(","))
        narrowed = [f for f in fallbacks
                    if fallback_positions(f.positionCode) in wanted]
        if narrowed:
            fallbacks = narrowed

    if len(fallbacks) == 1:
        return fallbacks[0], "surname"

    return None, "unmatched"


def main():
    print("--- APPLYING POSITION ELIGIBILITY ---")

    if not os.path.exists(ELIGIBILITY_FILE):
        print(f" -> No {os.path.basename(ELIGIBILITY_FILE)}; nothing to apply.")
        return

    source = pd.read_csv(ELIGIBILITY_FILE)
    source["team"] = source["team"].replace(TEAM_FIX)

    players = pd.read_sql(
        f'SELECT "playerId", "fullName", "teamAbbrevs", "positionCode" FROM {TABLE}',
        con=engine,
    )

    if players.empty:
        print(f" -> {TABLE} is empty; run the projection steps first.")
        return

    by_name, by_surname_team = build_indexes(players)

    updates = {}
    counts = {"exact": 0, "surname": 0, "ambiguous": 0, "unmatched": 0}
    unresolved = []

    for row in source.itertuples():
        positions = clean_positions(row.eligiblePositions)
        if not positions:
            continue

        match, how = resolve(row.playerName, row.team, positions,
                             by_name, by_surname_team)
        counts[how] += 1

        if match is None:
            unresolved.append((row.playerName, row.team, positions, how))
            continue

        # A player listed twice keeps the widest eligibility rather than the last row
        existing = updates.get(match.playerId)
        if existing:
            positions = clean_positions(existing + "," + positions)
        updates[match.playerId] = positions

    # Everyone the source did not cover still needs a value to rank against
    matched_ids = set(updates)
    derived = 0
    for row in players.itertuples():
        if row.playerId not in matched_ids:
            updates[row.playerId] = fallback_positions(row.positionCode)
            derived += 1

    payload = [{"pid": int(pid), "pos": pos} for pid, pos in updates.items()]

    with engine.begin() as conn:
        ensure_column(conn)
        conn.execute(
            text(f'UPDATE {TABLE} SET "eligiblePositions" = :pos WHERE "playerId" = :pid'),
            payload,
        )

    print(f" -> Source rows: {len(source)}")
    print(f" -> Matched exactly: {counts['exact']}")
    print(f" -> Matched on surname + team: {counts['surname']}")
    print(f" -> Derived from NHL primary: {derived}")

    if unresolved:
        print(f" -> Unresolved ({len(unresolved)}):")
        for name, team, positions, how in unresolved:
            print(f"      {how:<10} {name} ({team}) {positions}")

    multi = sum(1 for pos in updates.values() if "," in pos)
    print(f" -> {multi} players carry multi-position eligibility.")
    print(f" -> Updated {len(payload)} rows in {TABLE}.")


if __name__ == "__main__":
    main()
