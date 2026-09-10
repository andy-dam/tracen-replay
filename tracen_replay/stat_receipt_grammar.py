"""Strict repairs for fixed stat-receipt keywords.

These repairs are deliberately narrower than general OCR correction.  They
only recognize complete receipt sentences whose damaged token is the fixed
UI label ``Skill Pts``.  Amounts, names, and other receipt text are left
untouched so the caller can apply its normal geometry and confidence checks.
"""

import re


_FIXED_SKILL_POINTS_LABELS = ("Skijl Pts", "Skil! Pts")
_RECEIPT_TAIL = r" went (?:up|down) by \d+(?: to new heights)?[.!]?"


def normalize_fixed_stat_receipt(text):
    """Return a bounded fixed-label repair, or ``None`` when unsupported.

    The complete sentence shape is part of the guard.  In particular, this
    does not repair a standalone label, a cap/bonus sentence, a partial OCR
    prefix, or a near miss whose identity is ambiguous.
    """
    original = str(text).strip()
    for damaged in _FIXED_SKILL_POINTS_LABELS:
        if re.fullmatch(re.escape(damaged) + _RECEIPT_TAIL, original):
            return dict(
                text="Skill Pts" + original[len(damaged):],
                original_text=original,
                text_normalization="fixed_skill_points_label",
            )
    return None
