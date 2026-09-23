"""Lesson costs confirmed from the performance-point balance drop.

``observed_lesson_debit`` recovers a purchase cost from balances repeated on
the lesson menu before and after the receipt.  A player who leaves the menu
immediately never shows that second menu balance; the next repeated
performance panel on the home screen or the training menu is the only
after-balance available.  This helper accepts it only for the cleanest
sequence: after the receipt the frames run continuously (no sampling gap),
show nothing but the home screen, the training menu or the lesson menu with
no effects of any kind, and the first performance panel seen repeats identically on the next
frame.  Any confirmation projection recorded on the request frames must agree
with that panel.  The cost is then the drop, recorded with its own basis so
the accounting counts it as state-derived rather than observed.
"""
from __future__ import annotations

from .gameplay import CURRENCIES
from .source_clock import elapsed

BASIS = 'state_confirmed_balance_drop'
AFTER_WINDOW_MS = 20000
_QUIET_SCREENS = ('unknown', 'training_preview', 'lesson_selection')
_PANEL_SCREENS = ('unknown', 'training_preview')
_RECEIPT_KINDS = ('named_acquisition', 'song_learned')


def _panel(row):
    facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
    values = facts.get('performance_points')
    if isinstance(values, dict) and all(type(values.get(k)) is int and values[k] >= 0 for k in CURRENCIES):
        return {k: values[k] for k in CURRENCIES}
    return None


def _repeated(rows):
    """The balance shown identically on two consecutive frames within 500 ms."""
    if len(rows) < 2 or not 0 < elapsed(rows[0]['source_timestamp_ms'], rows[1]['source_timestamp_ms']) <= 500:
        return None
    first, second = _panel(rows[0]), _panel(rows[1])
    if first is None or first != second:
        return None
    return first


def _projection_conflicts(group, final):
    for row in group or ():
        facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
        projection = facts.get('projected_performance_points')
        if projection is None:
            continue
        if not isinstance(projection, dict):
            return True
        for field, value in projection.items():
            if field in CURRENCIES and value is not None and (type(value) is not int or value != final[field]):
                return True
    return False


def state_confirmed_lesson_debit(readings, event, group, before_rows, name, initial=None):
    """Return ``{'cost', 'matched', 'basis', 'proofs'}`` or ``None``.

    ``initial`` is the caller's menu balance before the request (one value per
    currency, already required to be consistent across the last menu visit);
    without it two consecutive identical before rows are required.
    """
    if isinstance(initial, dict) and all(type(initial.get(k)) is int and initial[k] >= 0 for k in CURRENCIES):
        initial = {k: initial[k] for k in CURRENCIES}
        before_proof = before_rows[-2:] if before_rows else []
    else:
        if len(before_rows) < 2:
            return None
        initial = _repeated(before_rows[-2:])
        if initial is None:
            return None
        before_proof = before_rows[-2:]
    # The receipt itself must be a single acquisition of this lesson with no
    # performance award of its own; anything else belongs to another path.
    effects = [e for e in event.get('effects') or () if isinstance(e, dict)]
    acquisitions = [e for e in effects if e.get('kind') in _RECEIPT_KINDS]
    if (len(acquisitions) != 1 or acquisitions[0].get('name') != name
            or any(e.get('kind') == 'performance_change' for e in effects)
            or (event.get('performance_deltas') or {})):
        return None
    start = event['last_seen_ms']
    later = [r for r in readings if start < r['source_timestamp_ms'] <= start + AFTER_WINDOW_MS]
    previous = start
    for index, row in enumerate(later):
        time = row['source_timestamp_ms']
        if not 0 < elapsed(previous, time) <= 500:
            return None
        previous = time
        if row.get('screen') not in _QUIET_SCREENS or row.get('effects'):
            return None
        facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
        if facts.get('awarded_performance_gains') or facts.get('projected_performance_points'):
            return None
        if _panel(row) is None:
            continue
        if row.get('screen') not in _PANEL_SCREENS:
            # A balance back on the lesson menu is the observed-debit path's
            # evidence, not this fallback's.
            return None
        final = _repeated(later[index:index + 2])
        if final is None or _projection_conflicts(group, final):
            return None
        cost = {k: initial[k] - final[k] for k in CURRENCIES}
        if any(v < 0 for v in cost.values()) or not any(v > 0 for v in cost.values()):
            return None
        after_rows = later[index:index + 2]
        return dict(cost=cost, matched=after_rows[-1], basis=BASIS, proofs=[
            dict(role=role, values=values, evidence=[r['evidence'] for r in rows],
                 source_timestamps_ms=[r['source_timestamp_ms'] for r in rows])
            for role, values, rows in (('before', initial, before_proof), ('after', final, after_rows))])
    return None
