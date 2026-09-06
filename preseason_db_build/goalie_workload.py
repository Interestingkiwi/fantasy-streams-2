"""
Shared goalie workload settings.

A goalie's games played is decided by the depth chart, not by his own
history, so starts are assigned editorially. These constants live here so
the projection engine and the rookie import can't drift apart.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

from season_config import HISTORICAL_SEASON_GAMES, season_game_count

# Pull assigned starts toward the middle: fewer 60-game workhorses, more even
# tandems. 0.0 uses the assigned number as-is, 1.0 gives everyone ANCHOR.
FLATTEN_STRENGTH = 0.15

# Both the anchor and the assigned start totals are written in 82-game terms -
# a 60-start workhorse and a 40-start anchor are shares of that season, not
# absolutes - so both scale to whatever length the coming season is.
FLATTEN_ANCHOR = 40


def season_scale():
    """How much longer the coming season is than the ones starts were written for."""
    return season_game_count() / HISTORICAL_SEASON_GAMES


def flatten_starts(games):
    """Compress an assigned start total toward the anchor, in this season's units."""
    scale = season_scale()
    anchor = FLATTEN_ANCHOR * scale
    scaled = games * scale
    return round(anchor + (scaled - anchor) * (1 - FLATTEN_STRENGTH))
