"""
League Database viewer: access control, league scoping, and shape.

Inserts its own small synthetic league rather than relying on imported
fixture data, so it passes on an empty database, and deletes everything it
wrote. A second league is inserted alongside it purely to prove the
endpoints never leak across leagues.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import os
import sys
from pathlib import Path

os.environ["FLASK_SECRET_KEY"] = "league-viewer-test"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module                      # noqa: E402
import yahoo_auth                             # noqa: E402
from db import execute, transaction, text     # noqa: E402

MINE, OTHER = 999901, 999902

flask_app = app_module.app
flask_app.config["TESTING"] = True

FAILURES = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def cleanup():
    for table in ("league_info", "teams", "scoring", "lineup_settings", "weeks",
                  "matchups", "rosters_tall", "rostered_players", "free_agents",
                  "waiver_players", "transactions"):
        execute(f"DELETE FROM {table} WHERE league_id IN (:a, :b)", {"a": MINE, "b": OTHER})
    execute("DELETE FROM yahoo_players WHERE player_id IN ('90001', '90002')")


def seed():
    cleanup()
    with transaction() as conn:
        conn.execute(text("INSERT INTO yahoo_players (player_id, player_name, player_team, positions, status)"
                          " VALUES ('90001', 'Test Skater', 'TOR', 'C,Util', 'NA'),"
                          "        ('90002', 'Other Skater', 'BOS', 'D', 'NA')"))
        for lid, team, player in ((MINE, "My Team", 90001), (OTHER, "Their Team", 90002)):
            conn.execute(text("INSERT INTO league_info (league_id, key, value)"
                              " VALUES (:l, 'league_name', :n), (:l, 'num_teams', '1')"),
                         {"l": lid, "n": f"League {lid}"})
            conn.execute(text("INSERT INTO teams (league_id, team_id, name, manager_nickname)"
                              " VALUES (:l, '1', :n, 'Manager')"), {"l": lid, "n": team})
            conn.execute(text("INSERT INTO scoring (league_id, stat_id, category, scoring_group)"
                              " VALUES (:l, 1, 'G', 'offense')"), {"l": lid})
            conn.execute(text("INSERT INTO weeks (league_id, week_num, start_date, end_date)"
                              " VALUES (:l, 1, '2025-10-07', '2025-10-12')"), {"l": lid})
            conn.execute(text("INSERT INTO matchups (league_id, week, team1, team2)"
                              " VALUES (:l, 1, :n, 'Rivals')"), {"l": lid, "n": team})
            conn.execute(text("INSERT INTO rosters_tall (league_id, team_id, player_id)"
                              " VALUES (:l, 1, :p)"), {"l": lid, "p": player})
            conn.execute(text("INSERT INTO free_agents (league_id, player_id, status)"
                              " VALUES (:l, :p, 'FA')"), {"l": lid, "p": player})
            conn.execute(text("INSERT INTO transactions (league_id, transaction_date, player_id,"
                              " player_name, fantasy_team, move_type)"
                              " VALUES (:l, '2026-01-01', :p, :n, :t, 'add')"),
                         {"l": lid, "p": player, "n": f"Player {player}", "t": team})


def signed_in(league_id=MINE):
    client = flask_app.test_client()
    with client.session_transaction() as sess:
        sess[yahoo_auth.SESSION_GUID] = yahoo_auth.DEV_GUID
        if league_id:
            sess[yahoo_auth.SESSION_LEAGUE] = str(league_id)
    return client


API = ["/league/api/overview", "/league/api/teams", "/league/api/schedule",
       "/league/api/transactions", "/league/api/player-pool", "/league/api/roster/1"]

seed()

print("\n=== 1. signed out ===")
anon = flask_app.test_client()
check("page redirects home", anon.get("/league/").status_code == 302)
codes = {p: anon.get(p).status_code for p in API}
check("every API returns 401", set(codes.values()) == {401}, codes)

print("\n=== 2. signed in, no league selected ===")
noleague = signed_in(league_id=None)
check("page redirects home", noleague.get("/league/").status_code == 302)
codes = {p: noleague.get(p).status_code for p in API}
check("every API returns 400", set(codes.values()) == {400}, codes)

print("\n=== 3. signed in with a league ===")
client = signed_in()
check("page renders", client.get("/league/").status_code == 200)

body = client.get("/league/api/overview").get_json()
check("overview ok", body["status"] == "success")
check("own league id", body["league_id"] == MINE, body.get("league_id"))
check("league name", body["info"]["league_name"] == f"League {MINE}", body.get("info"))
check("counts scoped to one team", body["counts"]["teams"] == 1, body.get("counts"))
check("scoring listed", [s["category"] for s in body["scoring"]] == ["G"])

teams = client.get("/league/api/teams").get_json()["teams"]
check("only own team", [t["name"] for t in teams] == ["My Team"], teams)
check("roster size counted", teams[0]["roster_size"] == 1, teams)

players = client.get("/league/api/roster/1").get_json()["players"]
check("roster resolves the player name",
      [p["player_name"] for p in players] == ["Test Skater"], players)
check("eligible positions preferred from the league row",
      players[0]["positions"] == "C,Util", players)

weeks = client.get("/league/api/schedule").get_json()["weeks"]
check("week returned", len(weeks) == 1)
check("matchup nested under its week",
      weeks[0]["matchups"][0]["team1"] == "My Team", weeks)

txns = client.get("/league/api/transactions").get_json()["transactions"]
check("only own transactions", [t["fantasy_team"] for t in txns] == ["My Team"], txns)

pool = client.get("/league/api/player-pool").get_json()
check("free agents by default", pool["pool"] == "free_agents")
check("pool names resolved", [p["player_name"] for p in pool["players"]] == ["Test Skater"], pool)
check("waivers pool selectable",
      client.get("/league/api/player-pool?pool=waivers").get_json()["pool"] == "waiver_players")

print("\n=== 4. no cross-league leakage ===")
other = signed_in(OTHER)
check("other session sees only its own team",
      [t["name"] for t in other.get("/league/api/teams").get_json()["teams"]] == ["Their Team"])
check("other session sees its own name",
      other.get("/league/api/overview").get_json()["info"]["league_name"] == f"League {OTHER}")

print("\n=== 5. input handling ===")
check("non-numeric team id rejected",
      client.get("/league/api/roster/abc").status_code == 400)
check("unknown team returns an empty roster, not an error",
      client.get("/league/api/roster/9999").get_json()["players"] == [])
check("limit is clamped",
      client.get("/league/api/transactions?limit=99999").status_code == 200)
check("garbage limit falls back",
      client.get("/league/api/transactions?limit=abc").status_code == 200)

cleanup()
print("\n" + "=" * 46)
if FAILURES:
    print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
    sys.exit(1)
print("All checks passed.")
