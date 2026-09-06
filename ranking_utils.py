"""
Calculates Player Ranks
Author - Jason Druckenmiller
Created - 7/5/2026
Updated - 9/6/2026
"""


import pandas as pd
import math

# Yahoo's slot vocabulary, plus the NHL primary codes the older rows still use
YAHOO_POSITIONS = ['C', 'LW', 'RW', 'D', 'G']
FORWARD_POSITIONS = ['C', 'LW', 'RW']
PRIMARY_ALIASES = {'L': 'LW', 'R': 'RW'}

# How hard each ranking mode leans on positional scarcity. 1.0 subtracts the
# full replacement level (roster-shape ranking), 0.0 subtracts none of it and
# leaves the raw projection board, and Balanced sits between the two so that
# neither scarcity nor raw production runs away with the rankings.
SCARCITY_WEIGHTS = {'roster': 1.0, 'balanced': 0.5, 'projection': 0.0}


def calculate_player_ranks(players_data, active_stats, league_mode='categories',
                           goalie_stat_keywords=[], num_teams=None,
                           roster_slots=None, roster_mode='split',
                           rank_mode='roster'):
    """
    Routes the request to the correct math engine based on league mode, then
    shifts every player against the replacement level for their position by as
    much as the chosen ranking mode calls for.
    """

    scarcity = SCARCITY_WEIGHTS.get(str(rank_mode).lower(), 1.0)

    # Without a league shape there is nothing to measure replacement level against
    if not (num_teams and roster_slots):
        scarcity = 0.0

    if league_mode == 'points':
        ranked = calculate_points_ranks(players_data, active_stats)
        value_key = 'fantasy_points_per_game'
    else:
        # Replacement level prices positional scarcity properly, so the blunt
        # goalie multiplier fades out as scarcity fades in - leaving both at full
        # strength counts goalie scarcity twice and floats backups into round one.
        ranked = calculate_capped_z_scores(
            players_data, active_stats, goalie_stat_keywords,
            goalie_multiplier_power=1.0 - scarcity,
        )
        value_key = 'total_value'

    if scarcity <= 0:
        return ranked

    return apply_replacement_level(ranked, value_key, num_teams, roster_slots,
                                   roster_mode, scarcity)


def eligible_slots(eligibility, roster_mode='split'):
    """
    'C,LW' -> {'C', 'LW'}, or {'F'} when the league groups its forwards.

    Accepts the NHL primary codes too, so a row that predates the eligibility
    import still lands in the right pool.
    """
    parts = set()
    for raw in str(eligibility or '').split(','):
        code = raw.strip().upper()
        if not code:
            continue
        parts.add(PRIMARY_ALIASES.get(code, code))

    if roster_mode == 'group':
        slots = set()
        if parts & set(FORWARD_POSITIONS):
            slots.add('F')
        if 'D' in parts:
            slots.add('D')
        if 'G' in parts:
            slots.add('G')
        return slots

    return parts & set(YAHOO_POSITIONS)


def replacement_levels(records, value_key, num_teams, roster_slots, roster_mode='split'):
    """
    The value of the best freely available player at each position.

    Starters at a position are teams x slots; bench spots are shared out in
    proportion to those starters, since a bench is filled with whichever
    positions the league starts most of. The player sitting one past that
    cutoff is what you can always fall back on, so that is the baseline every
    player at the position gets measured against.
    """
    positions = ['F', 'D', 'G'] if roster_mode == 'group' else YAHOO_POSITIONS

    starters = {}
    for pos in positions:
        try:
            count = int(roster_slots.get(pos, 0) or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            starters[pos] = count

    if not starters:
        return {}

    try:
        bench = max(0, int(roster_slots.get('B', 0) or 0))
    except (TypeError, ValueError):
        bench = 0

    total_starters = sum(starters.values())
    levels = {}

    for pos, count in starters.items():
        spots_per_team = count + (bench * count / total_starters)
        cutoff = int(round(num_teams * spots_per_team))

        pool = [r for r in records
                if pos in eligible_slots(r.get('eligiblePositions') or r.get('positionCode'),
                                         roster_mode)]
        if not pool:
            continue

        pool.sort(key=lambda r: _numeric(r.get(value_key)), reverse=True)

        # cutoff starters fill 0..cutoff-1, so the next one down is replacement
        index = min(max(cutoff, 0), len(pool) - 1)
        levels[pos] = _numeric(pool[index].get(value_key))

    return levels


def apply_replacement_level(records, value_key, num_teams, roster_slots,
                            roster_mode='split', scarcity_weight=1.0):
    """
    Re-rank on value over replacement instead of raw value.

    A multi-eligible player is measured against the thinnest position they can
    fill, because that is where a manager would actually slot them - which is
    what makes the 25th-best centre worth less than the 15th-best winger when
    the league starts more centres than wingers.

    scarcity_weight scales how much of the replacement level gets subtracted, so
    a half weight lands exactly between the raw projection board and the full
    roster-shape one.
    """
    if not records or not num_teams or not roster_slots:
        return records

    try:
        num_teams = int(num_teams)
    except (TypeError, ValueError):
        return records

    if num_teams <= 0:
        return records

    levels = replacement_levels(records, value_key, num_teams, roster_slots, roster_mode)
    if not levels:
        return records

    # Someone eligible only at a position the league does not start is as
    # replaceable as it gets
    worst = max(levels.values())

    for player in records:
        slots = eligible_slots(player.get('eligiblePositions') or player.get('positionCode'),
                               roster_mode)
        mine = [levels[pos] for pos in slots if pos in levels]
        baseline = min(mine) if mine else worst

        player['replacement_level'] = round(baseline, 3)
        player['value_over_replacement'] = round(
            _numeric(player.get(value_key)) - (baseline * scarcity_weight), 3)

    records.sort(key=lambda r: r.get('value_over_replacement', 0.0), reverse=True)

    for index, player in enumerate(records):
        player['overall_rank'] = index + 1

    return records


def _numeric(value):
    """Floats that survive Nones, blanks and stray strings."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(number) else number

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

def calculate_capped_z_scores(players_list, active_stats, goalie_stat_keywords,
                              goalie_multiplier_power=1.0):
    """
    Ranks players based on Asymmetric Capped Z-Scores with Goalie Normalization.

    goalie_multiplier_power fades the multiplier out as the caller takes over
    positional scarcity with replacement level: 1.0 applies it in full, 0.0
    disables it, and 0.5 applies its square root.
    """
    if not players_list or not active_stats:
        return players_list

    df = pd.DataFrame(players_list)
    baseline_df = df[df['projectedGames'] >= 40].copy()
    df['total_value'] = 0.0

    # 1. Define Stat Categories
    peripheral_stats = ['proj_hits', 'proj_blockedShots', 'proj_penaltyMinutes', 'proj_plusMinus',]
    volume_stats = ['proj_shots', 'proj_totalFaceoffs', 'proj_totalFaceoffWins', 'proj_totalFaceoffLosses', 'proj_saves', 'proj_gamesStarted', ]

    #Skater starts at 1 to boost goalies ranking as they fill a niche role but getting pushed down due to overall effectiveness.
    skater_cats_count = 1
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
    if goalie_multiplier_power > 0 and goalie_cats_count > 0 and skater_cats_count > 0:
        goalie_multiplier = (skater_cats_count / goalie_cats_count) ** goalie_multiplier_power

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
