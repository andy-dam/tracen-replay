"""Concert bonus bookkeeping from the Concert Info dialog.

The Concert Info dialog lists three concert bonuses in a "Concert Bonus
Changes" block: Friendship Training Effectiveness (percent), Specialty
Priority (points) and Support Chain Event Frequency (level).  Each column
shows the current value and, when songs learned since the last concert will
raise it, the next value ("bonuses will update after the concert").  The
change itself is applied at the concert and acknowledged by the "Concert
bonuses updated!" screen.

This module reads the block from OCR lines and turns a read current/next
pair followed by the bonus-update screen into one applied
``training_modifier_change`` per changed bonus, with both frames as
evidence.  Values are read from the dialog only; nothing is inferred from
song purchases.
"""
from __future__ import annotations

import re

from .layout import place

VERSION = 1
BASIS = 'concert_info_bonus_changes'
FIELDS = ('friendship_training_effectiveness', 'specialty_priority', 'support_chain_event_frequency')
# PC pane positions; the dialog is a popup at the screen's centre.
_COLUMNS ={'friendship_training_effectiveness': (265, 465), 'specialty_priority': (470, 635), 'support_chain_event_frequency': (640, 850)}
_VALUE_BAND = (365, 440)
_HEADER = 'concert bonus changes'
_PERCENT = re.compile(r'\+\s*(\d{1,3})\s*%')
_POINTS = re.compile(r'\+\s*(\d{1,3})(?!\s*%)(?!\d)')
_LEVEL = re.compile(r'lvl\s*([0-9O])', re.I)
UPDATE_LOOKAHEAD_MS = 20 * 60 * 1000


def _lines(row):
    ocr = row.get('ocr') if isinstance(row.get('ocr'), dict) else {}
    for key in ('lines', 'neural'):
        if isinstance(ocr.get(key), list):
            return ocr[key]
    return row.get('lines') if isinstance(row.get('lines'), list) else []


def read_concert_bonus(lines):
    """Return ``{field: {'current': int, 'next': int | None}}`` or ``{}``.

    The block header must be present.  A column with no readable value is
    omitted; a column reading two values in order gives ``current`` and
    ``next``; a single value is the current bonus with no pending change.
    """
    if not any(isinstance(l, dict) and _HEADER in str(l.get('text', '')).lower() for l in lines or ()):
        return {}
    result = {}
    for field, columns in _COLUMNS.items():
        left, top, right, bottom = place((columns[0], _VALUE_BAND[0], columns[1], _VALUE_BAND[1]), 'sc')
        texts = []
        for line in lines or ():
            if not isinstance(line, dict) or line.get('confidence', 0) < 85:
                continue
            box = line.get('box')
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                continue
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            if left <= cx <= right and top <= cy <= bottom:
                texts.append((box[1], box[0], str(line.get('text', ''))))
        if not texts:
            continue
        joined = ' '.join(t for _, _, t in sorted(texts))
        if field == 'friendship_training_effectiveness':
            values = [int(v) for v in _PERCENT.findall(joined)]
        elif field == 'specialty_priority':
            values = [int(v) for v in _POINTS.findall(joined)]
        else:
            values = [int(v.replace('O', '0')) for v in _LEVEL.findall(joined)]
        if not values:
            continue
        current = values[0]
        following = [v for v in values[1:] if v != current]
        result[field] = dict(current=current, next=following[0] if following else None)
    return result


def _bonus_facts(row):
    facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
    parsed = facts.get('concert_bonus')
    if isinstance(parsed, dict) and parsed:
        return parsed
    if row.get('screen') != 'concert_info':
        return {}
    return read_concert_bonus(_lines(row))


def concert_bonus_events(readings):
    """Applied bonus changes: a repeated Concert Info reading, then the update screen."""
    ordered = sorted((r for r in readings if type(r.get('source_timestamp_ms')) is int), key=lambda r: r['source_timestamp_ms'])
    events = []
    pending = None   # (values, [rows]) of the latest consistent Concert Info block with a change
    seen = {}
    for row in ordered:
        time = row['source_timestamp_ms']
        if row.get('screen') == 'concert_info':
            parsed = _bonus_facts(row)
            if not parsed:
                continue
            key = tuple(sorted((f, v['current'], v['next']) for f, v in parsed.items()))
            seen.setdefault(key, []).append(row)
            if len(seen[key]) >= 2 and any(v['next'] is not None for v in parsed.values()):
                pending = (parsed, seen[key])
            continue
        if row.get('screen') == 'concert_bonus_update' and pending is not None:
            parsed, rows = pending
            if time - rows[-1]['source_timestamp_ms'] > UPDATE_LOOKAHEAD_MS:
                pending = None
                continue
            effects = []
            for field in FIELDS:
                value = parsed.get(field)
                if not value or value['next'] is None:
                    continue
                effects.append({'kind': 'training_modifier_change', 'field': field, 'amount': value['next'] - value['current'],
                                'from': value['current'], 'to': value['next'], 'basis': BASIS,
                                'concert_info_evidence': [r.get('evidence') for r in rows[:2]],
                                'concert_info_timestamps_ms': [r['source_timestamp_ms'] for r in rows[:2]]})
            if effects:
                events.append(dict(id=f'concert-bonus-{len(events) + 1:04d}', kind='outcome', context_title='Concert bonuses updated!',
                                   first_seen_ms=time, last_seen_ms=time, evidence=row.get('evidence'), effects=effects,
                                   deltas={}, field_evidence={}, conflicting_readings=[], action_time_ms=None,
                                   pending_effects={}, effect_observations={}, basis=BASIS, version=VERSION))
            pending = None
            seen = {}
    return events
