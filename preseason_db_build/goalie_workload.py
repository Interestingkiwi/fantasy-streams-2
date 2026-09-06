"""
Shared goalie workload settings.

A goalie's games played is decided by the depth chart, not by his own
history, so starts are assigned editorially. These constants live here so
the projection engine and the rookie import can't drift apart.

Author - Jason Druckenmiller
Created - 9/6/2026
Updated - 9/6/2026
"""

# Pull assigned starts toward the middle: fewer 60-game workhorses, more even
# tandems. 0.0 uses the assigned number as-is, 1.0 gives everyone ANCHOR.
FLATTEN_STRENGTH = 0.15
FLATTEN_ANCHOR = 40


def flatten_starts(games):
    """Compress an assigned start total toward FLATTEN_ANCHOR."""
    return round(FLATTEN_ANCHOR + (games - FLATTEN_ANCHOR) * (1 - FLATTEN_STRENGTH))
