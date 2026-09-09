"""
Re-measures the ageing curve in aging.py from the historic tables.

Not part of build_database.py - it produces constants, not rows. Run it when a
season has been added to the historic tables and the numbers hardcoded in
aging.py deserve a look:

    cd preseason_db_build && python derive_aging_curve.py

It prints the fitted coefficients, the measurements they were fitted to, and
the two robustness checks. Copy the coefficients into aging.py by hand rather
than having the pipeline read them live: a curve that quietly moves every time
a season lands would make two runs of the same projection incomparable, and the
tail is thin enough that it deserves a person looking at it.

The method is the delta method. Pair each player's consecutive seasons, compare
his per-game rate against his own from the year before, and average those
ratios across everyone making the same age transition. Comparing a player only
against himself is what keeps the answer from being a description of which
players are young rather than of what ageing does.

Three things it corrects for, each printed so it can be judged rather than
trusted:

  * **Weighting.** A pair is weighted by the harmonic mean of its two game
    counts, so a 12-game season cannot swing an age bucket.
  * **League drift.** Each pair is divided by the league-wide ratio for that
    same season transition, so a year the whole league scored more does not
    read as everyone getting younger.
  * **Survivorship.** Reported, not corrected - it cannot be. The share of
    players who appear at all the following season is printed by age; where it
    collapses, the measured decline is too gentle and the fit should be
    extrapolated instead.

Author - Jason Druckenmiller
Created - 9/9/2026
Updated - 9/9/2026
"""

import numpy as np
import pandas as pd

import aging
from db_config import engine

# Both seasons of a pair need enough games for the rate to mean anything. The
# robustness check below varies this on purpose.
MIN_GAMES = 20

# The fit is over the range the sample actually supports. Below 24 the curve is
# growth rather than decline and does not belong to the same line; above 38 the
# buckets fall into single figures.
FIT_FROM, FIT_TO = 24, 38

SCORING_STATS = ["goals", "assists", "points", "ppPoints", "shots"]
PERIPHERAL_STATS = ["hits", "blockedShots", "penaltyMinutes"]


def with_age(frame, name_column):
    """Add season age and start year, dropping anyone with no birthdate."""
    if "birthDate" not in frame.columns:
        raise SystemExit(
            f"No birthDate column in the {name_column} history. Run "
            "historic_data_skaters.py / historic_data_goalies.py first - they "
            "collect it from the NHL 'bios' report."
        )

    frame = frame.dropna(subset=["birthDate"]).copy()
    frame["birthDate"] = pd.to_datetime(frame["birthDate"])
    frame["age"] = [aging.season_age(born, season)
                    for born, season in zip(frame["birthDate"], frame["seasonId"])]
    frame["startYear"] = frame["seasonId"].astype(str).str[:4].astype(int)
    return frame.dropna(subset=["age"])


def pair_seasons(frame, min_games=MIN_GAMES, min_games_next=None):
    """Every (season, next season) pair for the same player.

    `_a` columns are the earlier season, `_b` the later one.
    """
    earlier = frame[frame["gamesPlayed"] >= min_games]
    later = frame[frame["gamesPlayed"] >= (min_games_next or min_games)].copy()
    later["startYear"] -= 1
    return earlier.merge(later, on=["playerId", "startYear"], suffixes=("_a", "_b"))


def league_rates(frame, stats):
    """League-wide per-game rate for each stat, by season start year."""
    return frame.groupby("startYear").apply(
        lambda season: pd.Series({stat: season[stat].sum() / season["gamesPlayed"].sum()
                                  for stat in stats}),
        include_groups=False,
    )


def measure(pairs, stats, league, detrend=True):
    """Year-over-year rate ratio by age, averaged in logs across the stats."""
    rows = []
    for age in range(18, 45):
        bucket = pairs[pairs["age_a"] == age]
        if len(bucket) < 5:
            continue

        # Harmonic mean of the two game counts: a pair is only as trustworthy
        # as its shorter season.
        weight = 2 / (1 / bucket["gamesPlayed_a"] + 1 / bucket["gamesPlayed_b"])

        logs = []
        for stat in stats:
            before = (bucket[f"{stat}_pg_a"] * weight).sum()
            after = (bucket[f"{stat}_pg_b"] * weight).sum()
            if before <= 0 or after <= 0:
                continue
            ratio = after / before
            if detrend:
                drift = np.average(
                    [league.loc[year + 1, stat] / league.loc[year, stat]
                     for year in bucket["startYear"]],
                    weights=weight,
                )
                ratio /= drift
            logs.append(np.log(ratio))

        if logs:
            rows.append((age, float(np.exp(np.mean(logs))), len(bucket)))

    return pd.DataFrame(rows, columns=["age", "ratio", "pairs"])


def fit(measured, label):
    """Least squares on log(ratio) against age, weighted by pair count."""
    window = measured[(measured["age"] >= FIT_FROM) & (measured["age"] <= FIT_TO)]
    slope, intercept = np.polyfit(window["age"], np.log(window["ratio"]),
                                  1, w=np.sqrt(window["pairs"]))

    print(f"\n=== {label} ===")
    print(f"  slope {slope:+.5f}   intercept {intercept:+.5f}"
          f"   (fitted on ages {FIT_FROM}-{FIT_TO}, {int(window['pairs'].sum())} pairs)")
    print("  age    measured   pairs   fitted")
    for _, row in measured.iterrows():
        fitted = np.exp(np.polyval([slope, intercept], row["age"]))
        flag = "  <- fitted here" if FIT_FROM <= row["age"] <= FIT_TO else ""
        print(f"  {int(row['age'])}->{int(row['age']) + 1}   {row['ratio']:.4f}"
              f"   {int(row['pairs']):5d}   {fitted:.4f}{flag}")
    return slope, intercept


def report_survival(skaters):
    """Share of players who play again the next season, by age."""
    last_year = skaters["startYear"].max()
    played = skaters[(skaters["gamesPlayed"] >= MIN_GAMES) & (skaters["startYear"] < last_year)]
    again = skaters[skaters["gamesPlayed"] >= MIN_GAMES][["playerId", "startYear"]].copy()
    again["startYear"] -= 1
    again["survived"] = 1

    joined = played.merge(again, on=["playerId", "startYear"], how="left")
    joined["survived"] = joined["survived"].fillna(0)

    survival = joined.groupby("age").agg(players=("survived", "size"),
                                         survived=("survived", "mean"))
    print("\n=== survivorship: played again the next season ===")
    print("  Where this collapses, the measured decline above is too gentle - "
          "only the players who held up are in the sample.")
    for age, row in survival[survival["players"] >= 10].iterrows():
        print(f"  {int(age)}: {row['survived']:.0%}  (n={int(row['players'])})")


def report_threshold_sensitivity(skaters, league):
    """The same slope at three different game-count cuts."""
    print("\n=== robustness: does the games cut change the answer? ===")
    for min_a, min_b, label in [(20, 20, "20 games in both seasons"),
                                (10, 1, "10 games, then any appearance"),
                                (40, 1, "40 games, then any appearance")]:
        pairs = pair_seasons(skaters, min_a, min_b)
        measured = measure(pairs, SCORING_STATS, league)
        window = measured[(measured["age"] >= FIT_FROM) & (measured["age"] <= FIT_TO)]
        slope, _ = np.polyfit(window["age"], np.log(window["ratio"]),
                              1, w=np.sqrt(window["pairs"]))
        print(f"  {label:<34} slope {slope:+.5f}  ({len(pairs)} pairs)")


def report_drift(pairs, league, stats):
    """The slope with and without the league-drift correction."""
    print("\n=== robustness: is it ageing or is it the league? ===")
    for detrend, label in [(False, "raw"), (True, "league-detrended")]:
        measured = measure(pairs, stats, league, detrend=detrend)
        window = measured[(measured["age"] >= FIT_FROM) & (measured["age"] <= FIT_TO)]
        slope, _ = np.polyfit(window["age"], np.log(window["ratio"]),
                              1, w=np.sqrt(window["pairs"]))
        print(f"  {label:<20} slope {slope:+.5f}")


def report_growth_confidence(pairs):
    """The two checks behind aging.GROWTH_CONFIDENCE.

    Damping the growth side is a judgement call, but it is not an unexamined
    one. These are the numbers it was set against - re-read them before moving
    the dial.
    """
    print()
    print("=== growth damping: is the pooled ratio too generous? ===")
    print("  median/pooled below 1 means the pooled ratio is pulled up by "
          "breakouts and the typical player gains less.")
    for label, lo, hi in [("growth 19-23", 19, 23), ("peak 26-32", 26, 32),
                          ("decline 34+", 34, 44)]:
        ratios, weights = [], []
        for age in range(lo, hi + 1):
            bucket = pairs[pairs["age_a"] == age]
            if len(bucket) < 8:
                continue
            weight = 2 / (1 / bucket["gamesPlayed_a"] + 1 / bucket["gamesPlayed_b"])
            pooled, median = [], []
            for stat in SCORING_STATS:
                before = (bucket[f"{stat}_pg_a"] * weight).sum()
                after = (bucket[f"{stat}_pg_b"] * weight).sum()
                if before <= 0 or after <= 0:
                    continue
                pooled.append(np.log(after / before))
                usable = bucket[f"{stat}_pg_a"] > 0
                median.append(np.median(np.log(
                    bucket.loc[usable, f"{stat}_pg_b"].clip(lower=1e-4)
                    / bucket.loc[usable, f"{stat}_pg_a"])))
            if pooled and median:
                ratios.append(np.exp(np.mean(median) - np.mean(pooled)))
                weights.append(len(bucket))
        if ratios:
            print(f"  {label:14s} median/pooled = {np.average(ratios, weights=weights):.3f}")

    print()
    print("=== growth damping: is growth more variable than decline? ===")
    print("  If it were, shrinking it harder would be justified. It is not - "
          "the spread is flat with age.")
    scoring = pairs[pairs["points_pg_a"] > 0.15].copy()
    scoring["logRatio"] = np.log(scoring["points_pg_b"].clip(lower=1e-3)
                                 / scoring["points_pg_a"])
    for label, lo, hi in [("growth 19-23", 19, 23), ("peak 26-32", 26, 32),
                          ("decline 34-39", 34, 39)]:
        window = scoring[(scoring["age_a"] >= lo) & (scoring["age_a"] <= hi)]
        print(f"  {label:14s} n={len(window):4d}  sd={window['logRatio'].std():.3f}")

    print()
    print(f"  aging.GROWTH_CONFIDENCE is {aging.GROWTH_CONFIDENCE} "
          f"- growth credited at {aging.GROWTH_CONFIDENCE:.0%}, decline in full.")


def report_goalies():
    """Why goalie rates are left alone."""
    goalies = pd.read_sql("SELECT * FROM historic_goalies_baseline", con=engine)
    if "birthDate" not in goalies.columns:
        print("\n=== goalies ===\n  No birthDate column yet; run historic_data_goalies.py.")
        return

    goalies = with_age(goalies, "goalie")
    goalies["savePct"] = goalies["saves"] / goalies["shotsAgainst"]
    pairs = pair_seasons(goalies, 15)

    print(f"\n=== goalies: change in save percentage, by age ({len(pairs)} pairs) ===")
    print("  A real ageing signal would sit near zero when young and go negative "
          "late. If every age is negative by about the same amount, that is the "
          "league's save percentage falling, not goalies ageing.")
    for age in range(20, 45):
        bucket = pairs[pairs["age_a"] == age]
        if len(bucket) < 3:
            continue
        weight = 2 / (1 / bucket["gamesPlayed_a"] + 1 / bucket["gamesPlayed_b"])
        change = ((bucket["savePct_b"] - bucket["savePct_a"]) * weight).sum() / weight.sum()
        print(f"  {age}->{age + 1}: {change * 1000:+6.2f} pts  (n={len(bucket)})")


def report_games_played(skaters):
    """Whether older players actually miss more games."""
    regulars = skaters[skaters["gamesPlayed"] >= 40]
    following = skaters[["playerId", "startYear", "gamesPlayed"]].rename(
        columns={"gamesPlayed": "nextGamesPlayed"}).copy()
    following["startYear"] -= 1
    joined = regulars.merge(following, on=["playerId", "startYear"])

    print("\n=== games played the season after a 40-game season ===")
    print("  This is why projectedGames carries no age penalty: among players "
          "who were regulars, it does not fall with age.")
    by_age = joined.groupby("age").agg(nextGamesPlayed=("nextGamesPlayed", "mean"),
                                       players=("nextGamesPlayed", "size"))
    for age, row in by_age[by_age["players"] >= 8].iterrows():
        print(f"  {int(age)}: {row['nextGamesPlayed']:.1f} games  (n={int(row['players'])})")


def main():
    skaters = with_age(pd.read_sql("SELECT * FROM historic_skaters_baseline", con=engine),
                       "skater")
    for stat in SCORING_STATS + PERIPHERAL_STATS:
        skaters[f"{stat}_pg"] = skaters[stat] / skaters["gamesPlayed"]

    league = league_rates(skaters, SCORING_STATS + PERIPHERAL_STATS)
    pairs = pair_seasons(skaters)
    print(f"{len(skaters)} skater-seasons, {len(pairs)} consecutive-season pairs.")

    scoring = fit(measure(pairs, SCORING_STATS, league), "scoring")
    peripheral = fit(measure(pairs, PERIPHERAL_STATS, league), "peripheral")

    report_drift(pairs, league, SCORING_STATS)
    report_threshold_sensitivity(skaters, league)
    report_survival(skaters)
    report_games_played(skaters)
    report_growth_confidence(pairs)
    report_goalies()

    print("\n=== what aging.py currently uses ===")
    print(f"  SCORING_SLOPE, SCORING_INTERCEPT       = {aging.SCORING_SLOPE}, "
          f"{aging.SCORING_INTERCEPT}    (measured now: {scoring[0]:.5f}, {scoring[1]:.4f})")
    print(f"  PERIPHERAL_SLOPE, PERIPHERAL_INTERCEPT = {aging.PERIPHERAL_SLOPE}, "
          f"{aging.PERIPHERAL_INTERCEPT}    (measured now: {peripheral[0]:.5f}, {peripheral[1]:.4f})")
    print("\n  Growth before 24 is not fitted - read it off the measured column "
          "above and update SCORING_GROWTH_RATIOS by hand.")


if __name__ == "__main__":
    main()
