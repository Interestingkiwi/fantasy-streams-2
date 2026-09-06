"""
Projects Goalie Data using up to 5 years of NHL Data
Author - Jason Druckenmiller
Created - 7/4/2026
Updated - 9/6/2026
"""

import os

import pandas as pd
from db_config import engine
from goalie_workload import (FLATTEN_ANCHOR, FLATTEN_STRENGTH, flatten_starts,
                             season_scale)
from season_config import season_game_count
import numpy as np

# --- PROJECTED GAMES ---
# A goalie's workload is decided by the depth chart, not by his own history, and
# history gets it backwards exactly where it matters: the goalies about to take
# a job (Wallstedt, Hart) look worst because they barely played. A team-budget
# model ranked on past GP was measured and came out worse still. So starts come
# from an editorial table, with the historical weighted average as the fallback
# for anyone not listed.
OVERRIDES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "goalie_gp_overrides.csv")


def load_gp_overrides():
    """{playerId: projected games}, flattened toward the tandem anchor."""
    if not os.path.exists(OVERRIDES_FILE):
        print(f" -> No {os.path.basename(OVERRIDES_FILE)}; using historical GP for all goalies.")
        return {}

    table = pd.read_csv(OVERRIDES_FILE)
    flattened = table["sourceGamesPlayed"].map(flatten_starts).astype(int)
    print(f" -> Loaded {len(table)} goalie GP overrides "
          f"(flattened {FLATTEN_STRENGTH:.0%} toward {FLATTEN_ANCHOR}, "
          f"scaled x{season_scale():.3f} to a {season_game_count()}-game season).")
    return dict(zip(table["playerId"].astype(int), flattened))

# --- SMALL-SAMPLE REGRESSION ---
# A goalie's per-game rates are taken at face value no matter how little he has
# played, which lets a backup with a lucky 30-game run out-project established
# starters (Bussi at .795 wins per start against Vasilevskiy's .642). Each rate
# is pulled toward the league average with a weight set by how much the goalie
# has actually played: REGRESSION_GAMES is the number of league-average games
# mixed in, so a 20-game sample lands near the mean and a 150-game one barely
# moves.
REGRESSION_GAMES = 25

# Volume follows the assigned workload rather than the goalie's own history, so
# only the rates that describe quality or luck are regressed.
REGRESSED_STATS = ('wins', 'losses', 'otLosses', 'shutouts',
                   'goalsAgainst', 'saves', 'shotsAgainst')


def league_per_game_rates(frame, stats):
    """League-wide per-game rate for each stat, pooled across every goalie."""
    total_gp = frame['gamesPlayed'].sum()
    if not total_gp:
        return {stat: 0.0 for stat in stats}

    rates = {}
    for stat in stats:
        total = pd.to_numeric(frame[stat], errors='coerce').fillna(0).sum()
        rates[stat] = total / total_gp
    return rates


def calculate_goalie_trend(y1_sv_pct, y2_sv_pct):
    if pd.isna(y1_sv_pct) or pd.isna(y2_sv_pct) or y2_sv_pct == 0:
        return "Not Enough Data"

    change = y1_sv_pct - y2_sv_pct

    if change >= 0.008:
        return "Trending Up"
    elif change <= -0.008:
        return "Trending Down"
    else:
        return "Stable"

print("--- INITIATING GOALIE PROJECTION ENGINE ---")

GP_OVERRIDES = load_gp_overrides()

df = pd.read_sql("SELECT * FROM historic_goalies_baseline", con=engine)

seasons = sorted(df['seasonId'].unique(), reverse=True)
y1, y2, y3 = seasons[0], seasons[1], seasons[2]
print(f"Target Seasons: Y1={y1} (60%), Y2={y2} (30%), Y3={y3} (10%)")

df_3yr = df[df['seasonId'].isin([y1, y2, y3])].copy()
df_3yr = df_3yr.drop_duplicates(subset=['playerId', 'seasonId'], keep='first')

# --- 20-GAME GLOBAL THRESHOLD CHECK ---
gp_totals = df_3yr.groupby('playerId')['gamesPlayed'].sum().reset_index()
valid_goalies = gp_totals[gp_totals['gamesPlayed'] >= 20]['playerId']
df_3yr = df_3yr[df_3yr['playerId'].isin(valid_goalies)]


MIN_GAMES_PER_SEASON = 5

LEAGUE_RATES = league_per_game_rates(df_3yr, REGRESSED_STATS)
print(f" -> League baseline: {LEAGUE_RATES['wins']:.3f} wins/game, "
      f"regressing rates over {REGRESSION_GAMES} games.")

projected_data = []

for pid in df_3yr['playerId'].unique():
    player_data = df_3yr[df_3yr['playerId'] == pid]
    latest = player_data.iloc[0]

    # ---- TREND LOGIC ----
    y1_data = player_data[player_data['seasonId'] == y1]
    y2_data = player_data[player_data['seasonId'] == y2]

    y1_sv_pct = np.nan
    y2_sv_pct = np.nan

    # Derive Y1 SV% only if they meet the per-season threshold
    if not y1_data.empty and y1_data['gamesPlayed'].values[0] >= MIN_GAMES_PER_SEASON and y1_data['shotsAgainst'].values[0] > 0:
        y1_sv_pct = y1_data['saves'].values[0] / y1_data['shotsAgainst'].values[0]

    # Derive Y2 SV% only if they meet the per-season threshold
    if not y2_data.empty and y2_data['gamesPlayed'].values[0] >= MIN_GAMES_PER_SEASON and y2_data['shotsAgainst'].values[0] > 0:
        y2_sv_pct = y2_data['saves'].values[0] / y2_data['shotsAgainst'].values[0]

    trend = calculate_goalie_trend(y1_sv_pct, y2_sv_pct)
    # -------------------------


    gp_weighted_sum = 0
    total_weight = 0
    for i, year in enumerate([y1, y2, y3]):
        weight = [0.6, 0.3, 0.1][i]
        year_data = player_data[player_data['seasonId'] == year]
        if not year_data.empty:
            gp_weighted_sum += (year_data['gamesPlayed'].values[0] * weight)
            total_weight += weight

    # Past workload is a share of an 82-game season, so it stretches with the new one
    historical_gp = (int(round(gp_weighted_sum / total_weight * season_scale()))
                     if total_weight > 0 else 0)
    proj_gp = GP_OVERRIDES.get(int(pid), historical_gp)

    proj = {
        'playerId': int(pid),
        'goalieFullName': latest['goalieFullName'],
        'positionCode': 'G',
        'teamAbbrevs': latest['teamAbbrevs'],
        'projectedGames': proj_gp,
        'productionTrend': trend
    }

    # Step 1: weight each season's PER-GAME rate, then pace to projected games.
    # Weighting season totals instead would leave the counting stats stuck at the
    # old workload whenever an override moves a goalie's starts.
    stats_to_weight = [
        'wins', 'losses', 'shutouts', 'gamesStarted',
        'shotsAgainst', 'goalsAgainst', 'saves', 'timeOnIce', 'otLosses'
    ]

    # How much NHL evidence this goalie actually has behind those rates
    sample_games = pd.to_numeric(player_data['gamesPlayed'], errors='coerce').fillna(0).sum()

    for stat in stats_to_weight:
        weighted_sum = 0
        total_weight = 0

        for i, year in enumerate([y1, y2, y3]):
            weight = [0.6, 0.3, 0.1][i]
            year_data = player_data[player_data['seasonId'] == year]

            if year_data.empty:
                continue
            season_gp = year_data['gamesPlayed'].values[0]
            value = year_data[stat].values[0]
            if pd.notna(value) and pd.notna(season_gp) and season_gp > 0:
                weighted_sum += (value / season_gp) * weight
                total_weight += weight

        per_game = weighted_sum / total_weight if total_weight > 0 else 0

        if stat in REGRESSED_STATS and sample_games > 0:
            league_rate = LEAGUE_RATES.get(stat, 0.0)
            per_game = ((per_game * sample_games + league_rate * REGRESSION_GAMES)
                        / (sample_games + REGRESSION_GAMES))

        proj[f"proj_{stat}"] = round(per_game * proj_gp, 1)

    # Step 2: Algebra for DERIVED RATE stats
    proj['proj_savePct'] = round(proj['proj_saves'] / proj['proj_shotsAgainst'], 3) if proj.get('proj_shotsAgainst', 0) > 0 else 0.0
    proj['proj_goalsAgainstAverage'] = round((proj['proj_goalsAgainst'] * 3600) / proj['proj_timeOnIce'], 2) if proj.get('proj_timeOnIce', 0) > 0 else 0.0
    proj['proj_winPct'] = round(proj['proj_wins'] / proj['proj_gamesStarted'], 3) if proj.get('proj_gamesStarted', 0) > 0 else 0.0
    proj['proj_startPct'] = round(proj['proj_gamesStarted'] / proj['projectedGames'], 3) if proj.get('projectedGames', 0) > 0 else 0.0

    projected_data.append(proj)

final_df = pd.DataFrame(projected_data)
final_df.to_sql("projected_goalies_baseline", con=engine, if_exists='replace', index=False)

overridden = sum(1 for pid in final_df["playerId"] if int(pid) in GP_OVERRIDES)
print(f"{len(final_df)} goalie projections saved "
      f"({overridden} using assigned starts, {len(final_df) - overridden} from historical GP).")
