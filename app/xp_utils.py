"""
XP calculation utilities
Mirrors the frontend getLevel / getLevelTitle logic exactly
"""

LEVEL_THRESHOLDS = [0, 50, 120, 220, 350, 500, 700, 950, 1250, 1600, 2000]
LEVEL_TITLES = [
    "Newcomer", "Starter", "Explorer", "Contributor", "Performer",
    "Achiever", "Champion", "Expert", "Elite", "Legend", "Master"
]

XP_BY_PRIORITY = {"low": 5, "medium": 10, "high": 20, "urgent": 35}


def get_level(xp: int) -> dict:
    level = 1
    for i in range(1, len(LEVEL_THRESHOLDS)):
        if xp >= LEVEL_THRESHOLDS[i]:
            level = i + 1
        else:
            break
    level = min(level, 10)
    current = LEVEL_THRESHOLDS[level - 1]
    next_threshold = LEVEL_THRESHOLDS[level] if level < 10 else LEVEL_THRESHOLDS[10]
    if level >= 10:
        progress = 100
        to_next = 0
    else:
        progress = round(((xp - current) / (next_threshold - current)) * 100)
        to_next = next_threshold - xp
    return {
        "level": level,
        "progress": progress,
        "to_next": to_next,
        "title": LEVEL_TITLES[min(level, 10) - 1],
    }


def xp_for_task(priority: str, on_time: bool) -> int:
    base = XP_BY_PRIORITY.get(priority, 10)
    multiplier = 1.5 if on_time else 0.5
    return round(base * multiplier)