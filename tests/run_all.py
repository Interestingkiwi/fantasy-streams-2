"""
Run every test suite and summarise.

    python tests/run_all.py

Each suite is a standalone script rather than a pytest module: the repo has
no test runner dependency and these need none. They start their own stub
Yahoo server on a local port and talk to the real Postgres from
DATABASE_URL, creating and deleting rows under a test guid, so a database
must be reachable.

Exits non-zero if any suite fails, so it is usable as a CI step.

Author - Jason Druckenmiller
Created - 9/7/2026
Updated - 9/7/2026
"""

import subprocess
import sys
from pathlib import Path

TESTS = [
    "test_oauth_flow.py",
    "test_guid_resolution.py",
    "test_scope.py",
    "test_league_viewer.py",
    "test_schedules.py",
    "test_lineup.py",
    "test_daily_value.py",
    "test_goalie_starts.py",
    "test_matchup_weights.py",
    "test_manager_profiles.py",
    "test_opponent_strength.py",
    "test_game_results.py",
    "test_nightly.py",
    "test_aging.py",
    "test_adp.py",
]


def main():
    here = Path(__file__).resolve().parent
    failed = []

    for name in TESTS:
        # flush before handing stdout to the child, or the headers land
        # after the output they label.
        print(f"\n{'=' * 60}\n{name}\n{'=' * 60}", flush=True)
        result = subprocess.run([sys.executable, str(here / name)])
        if result.returncode != 0:
            failed.append(name)

    print(f"\n{'=' * 60}", flush=True)
    if failed:
        print(f"FAILED ({len(failed)}/{len(TESTS)}): {', '.join(failed)}")
        return 1
    print(f"All {len(TESTS)} suites passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
