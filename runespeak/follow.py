"""Timing lookup for saved, section-aligned narrations."""


def passage_at(position, durations):
    elapsed = 0.0
    for index, seconds in enumerate(durations):
        if seconds > 0 and elapsed <= position < elapsed + seconds:
            return index
        elapsed += max(0, seconds)
    return len(durations)-1 if durations and position >= elapsed else None
