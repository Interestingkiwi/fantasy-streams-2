"""
Calculates Player Ranks
Author - Jason Druckenmiller
Created - 7/5/2026
Updated - 7/5/2026
"""


import pandas as pd
import math


def calculate_player_ranks(players_data, active_stats, league_mode='categories', goalie_stat_keywords=[]):
    """
    Routes the request to the correct math engine based on league mode.
    """

    if league_mode == 'points':
        return calculate_points_ranks(players_data, active_stats)
    else:
        return calculate_capped_z_scores(players_data, active_stats, goalie_stat_keywords)

def calculate_points_ranks(players_data, active_stats):
    """
    Calculates Fantasy Points Per Game for Points Leagues.
    """
    for player in players_data:
        total_fp = 0

        for stat, weight in active_stats.items():
            val = player.get(stat)
            if val is not None:
                try:
                    total_fp += float(val) * float(weight)
                except ValueError:
                    pass

        games = float(player.get('projectedGames', 1) or 1)
        if games <= 0:
            games = 1

        fpg = total_fp / games
        player['fantasy_points_per_game'] = round(fpg, 2)

    players_data.sort(key=lambda x: x.get('fantasy_points_per_game', 0), reverse=True)

    for index, player in enumerate(players_data):
        player['overall_rank'] = index + 1

    return players_data

def calculate_capped_z_scores(players_list, active_stats, goalie_stat_keywords):
    """
    Ranks players based on Asymmetric Capped Z-Scores with Goalie Normalization.
    """
    if not players_list or not active_stats:
        return players_list

    df = pd.DataFrame(players_list)
    baseline_df = df[df['projectedGames'] >= 40].copy()
    df['total_value'] = 0.0

    # 1. Define Stat Categories
    peripheral_stats = ['proj_hits', 'proj_blockedShots', 'proj_penaltyMinutes', 'proj_plusMinus',]
    volume_stats = ['proj_shots', 'proj_totalFaceoffs', 'proj_totalFaceoffWins', 'proj_totalFaceoffLosses', 'proj_saves', 'proj_gamesStarted', ]

    skater_cats_count = 0
    goalie_cats_count = 0

    # 2. Loop through active stats, calculate Z-Scores, and count categories
    for stat, polarity in active_stats.items():
        if stat in df.columns:
            if stat in goalie_stat_keywords:
                goalie_cats_count += 1
            else:
                skater_cats_count += 1

            df[stat] = pd.to_numeric(df[stat], errors='coerce')
            baseline_df[stat] = pd.to_numeric(baseline_df[stat], errors='coerce')

            mean = baseline_df[stat].mean()
            std = baseline_df[stat].std()

            if pd.notna(std) and std > 0:
                z_score = (df[stat] - mean) / std

                if stat in peripheral_stats:
                    capped_z = z_score.clip(lower=-2.0, upper=2.5)
                elif stat in volume_stats:
                    capped_z = z_score.clip(lower=-2.0, upper=3.0)
                else:
                    capped_z = z_score.clip(lower=-2.0, upper=4.0)

                value_added = capped_z.fillna(0) * polarity
                df['total_value'] += value_added

    # 3. Apply the Positional Category Multiplier
    if goalie_cats_count > 0 and skater_cats_count > 0:
        goalie_multiplier = skater_cats_count / goalie_cats_count

        df.loc[df['positionCode'].astype(str).str.contains('G', na=False), 'total_value'] *= goalie_multiplier

    # 4. Sort and assign ranks
    df = df.sort_values(by='total_value', ascending=False).reset_index(drop=True)
    df['overall_rank'] = df.index + 1

    records = df.to_dict(orient='records')

    # Convert NaNs to None for safe JSON transport to Javascript
    for row in records:
        for key, value in row.items():
            if isinstance(value, float) and math.isnan(value):
                row[key] = None

    return records
