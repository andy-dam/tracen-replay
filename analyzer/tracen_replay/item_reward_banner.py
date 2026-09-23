"""Read the awarded-item banner in the raffle event layout.

The event context and banner region establish the presentation's meaning.
Names are transcribed literally; they do not select an item catalog entry or
imply any energy/stat effect. Other item presentation layouts abstain.
"""

from copy import deepcopy
import math

from .layout import place


def _inside(line, bounds, minimum=97):
    if not isinstance(line, dict) or not isinstance(line.get('text'), str):
        return False
    box = line.get('box')
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    try:
        left, top, right, bottom = map(float, box)
        confidence = float(line.get('confidence', 0))
    except (ValueError, TypeError):
        return False
    if not all(math.isfinite(v) for v in (left, top, right, bottom, confidence)):
        return False
    x1, y1, x2, y2 = bounds
    return confidence >= minimum and x1 <= left < right <= x2 and y1 <= top < bottom <= y2


def read_reward(raw):
    """Return one observed item reward, or None when its proof is incomplete."""
    if not isinstance(raw, dict) or not isinstance(raw.get('header'), str):
        return None
    if raw['header'].strip().casefold() != 'career':
        return None
    lines = raw.get('lines', [])
    if not isinstance(lines, list):
        return None
    # The event and title rows are pinned to the top left.
    event = [line for line in lines if _inside(line, place((225, 160, 480, 200), 'tl'))
             and line['text'].strip().casefold() == 'trainee event']
    title = [line for line in lines if _inside(line, place((225, 200, 610, 245), 'tl'))
             and line['text'].strip().casefold() == 'raffle time!']
    # This is the separate reward-name banner above the speaker/dialogue area;
    # it is centred, so the band covers both centres.
    names = [line for line in lines if _inside(line, place((330, 660, 810, 735), 'mc', 'sc'))
             and line['text'].strip() and any(c.isalpha() for c in line['text'])]
    if len(event) != 1 or len(title) != 1 or len(names) != 1:
        return None
    name = ' '.join(names[0]['text'].split())
    return dict(kind='item_reward', name=name, raw_text=names[0]['text'],
                observation_basis='visible_raffle_reward_banner',
                source_proof=dict(event=deepcopy(event[0]), title=deepcopy(title[0]),
                                  banner=deepcopy(names[0])),
                quantity=None, inferred_numeric_effects=False)
