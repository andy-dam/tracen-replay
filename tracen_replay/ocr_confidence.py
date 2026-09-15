"""Strict percentage confidence at persisted OCR evidence boundaries."""
import math


def confidence_percent(value):
    """Return a finite percentage, or None for malformed source evidence."""
    if type(value) not in (int, float):
        return None
    try:
        result = float(value)
    except (OverflowError, ValueError):
        return None
    return result if math.isfinite(result) and 0 <= result <= 100 else None
