"""Fold a name the recognizer read a glyph off into the name the run knows.

The text reader returns a supporter's or a skill's name a letter or two off
on some frames: ``Daivg Scarlet``, ``Direct r Akikawa``, ``Cprner Adept``,
``Blucarole of Blessings``. Every such spelling is rare in its run, and the
name it stands for is not: a supporter is named on every receipt that
concerns them, ten to thirty times a career, and a skill is named on its
hint receipt and on the skill menu's cards, which are read again and again.
The run's own sightings are therefore the vocabulary. A name read once or
twice that lies within two edits of exactly one well-sighted name, and near
no other, is that name; a rare name near nothing, or near two, is left as
read. Distance is measured on the letters alone, so a dropped space or a
stray mark counts as one edit, and a name is never folded into a name that
is itself rare.

Supporter names come from the friendship receipts; skill names from the hint
receipts, the skill menu's cards and the owned-skill summary. Within one
outcome, a repaired effect that now duplicates another (the same award read
under two spellings on consecutive frames) merges into it and keeps its
frames as evidence.
"""
import re
from collections import Counter
from copy import deepcopy

SUPPORTER_KINDS = ('friendship_change', 'friendship_status')
SKILL_KINDS = ('skill_hint_change',)
# A name is known at these sightings or more, and rare below them.
KNOWN_MIN = {'supporter': 3, 'skill': 2}
# The circle grades of a skill name ("Corner Adept" and "Corner Adept ○"
# are two skills); a repair never crosses them.
CIRCLE_MARKERS = '○◎'
# A name within this many edits of a known name is that name, when no other
# known name is within the margin. These are the fallback numbers when the
# learned confusion table (confusions.py, data/confusions.json) is absent;
# with it, the limits and the margin are the learned ones and the distance
# is the recognizer's own likelihood of the damage.
MAX_EDITS = 2
SHORT_NAME_LETTERS = 6
SHORT_MAX_EDITS = 1
MARGIN_EDITS = 3


# An uppercase O after the last letter, spaced or glued ("Rainy Days O",
# "RacecourseO"): the recognizer's reading of the circle marker.
_TRAILING_O = re.compile(r'(?<=[a-z])\s*O$')


def letters(name):
    """The name's letters and digits alone, lower case: the shape OCR gets wrong.

    A trailing " O" is a circle marker, not a letter, and is left out.
    """
    text = _TRAILING_O.sub('', str(name or ''))
    return re.sub(r'[^0-9a-zÀ-ɏ぀-ヿ一-鿿]+', '', text.casefold())


def circles(name):
    """The circle grade markers in a skill name, in order.

    A trailing " O" is the recognizer's reading of the circle marker and
    counts as one.
    """
    text = _TRAILING_O.sub(CIRCLE_MARKERS[0], str(name or ''))
    return ''.join(c for c in text if c in CIRCLE_MARKERS)


def edits(a, b):
    """Levenshtein distance."""
    if abs(len(a) - len(b)) > MARGIN_EDITS:
        return MARGIN_EDITS + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _names(box):
    supporters, skills = set(), set()
    for effect in box.get('effects') or []:
        name = effect.get('name') if isinstance(effect, dict) else None
        if not name:
            continue
        if effect.get('kind') in SUPPORTER_KINDS:
            supporters.add(name)
        elif effect.get('kind') in SKILL_KINDS:
            skills.add(name)
    return supporters, skills


def build_vocabulary(readings, owned_cards=(), events=None):
    """Sightings of supporter and skill names across a run.

    Supporter sightings come from the friendship receipts and skill sightings
    from the hint receipts, each counted once per box (per outcome event when
    ``events`` is given, else per frame), so a misreading that lasted the
    frames of one box is still one sighting; the skill menu's cards and the
    owned-skill summary add to the skill sightings once per frame.
    """
    supporters, skills = Counter(), Counter()
    together = {}
    for box in (events if events is not None else readings):
        seen_supporters, seen_skills = _names(box)
        supporters.update(seen_supporters)
        skills.update(seen_skills)
    for row in readings:
        seen_supporters, seen_skills = _names(row)
        facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
        for name in {card['name'] for card in facts.get('skill_cards') or [] if isinstance(card, dict) and card.get('name')}:
            skills[name] += 1
        # Two names on one frame are two names: the game never prints the
        # same supporter or skill twice on a box, so neither is the other.
        for group in (seen_supporters, seen_skills):
            for name in group:
                together.setdefault(name, set()).update(other for other in group if other != name)
    for card in owned_cards or ():
        if isinstance(card, dict) and card.get('name'):
            skills[card['name']] += 1
    return {'supporter': supporters, 'skill': skills, 'together': together}


def repair(name, sightings, group, together=()):
    """The known name a rare spelling stands for, or None.

    ``sightings`` is the run's counter for the group; ``name`` must be rare
    in it. The answer is the one known name within MAX_EDITS (one edit for a
    short name) with no other known name within MARGIN_EDITS, with the same
    circle grade markers, and never one of the names in ``together``, the
    names read on the same frame as this one.
    """
    if not name or (sightings.get(name) or 0) >= KNOWN_MIN[group]:
        return None
    shape = letters(name)
    if not shape:
        return None
    from . import confusions
    limit, short_limit, short_letters, margin = confusions.limits()
    limit = short_limit if len(shape) <= short_letters else limit
    near = []
    for known, count in sightings.items():
        if known == name or count < KNOWN_MIN[group] or known in together:
            continue
        distance = confusions.distance(shape, letters(known))
        if distance <= limit + margin:
            # A known name of the other circle grade within reach means the
            # grade of this spelling cannot be told: the reader may have
            # dropped the marker along with the letters it got wrong.
            if circles(known) != circles(name):
                return None
            near.append((distance, known))
    if len(near) != 1 or near[0][0] > limit:
        return None
    return near[0][1]


def _group(effect):
    kind = effect.get('kind')
    if kind in SUPPORTER_KINDS:
        return 'supporter'
    if kind in SKILL_KINDS:
        return 'skill'
    return None


def _key(effect):
    return '|'.join(str(effect.get(k) or '') for k in ('kind', 'field', 'name'))


def repair_event_names(event, vocabulary):
    """Rename the rare spellings among an outcome's effects and merge duplicates.

    A renamed effect keeps its read spelling under ``alternate_name_evidence``
    with the frames it was read on and a ``name_resolution`` of
    ``vocabulary_repair``; its evidence key follows the name. When the event
    already holds the same award under the known name, the renamed effect
    merges into it and its frames join that award's evidence.
    """
    effects = event.get('effects')
    if not isinstance(effects, list):
        return []
    evidence = event.setdefault('field_evidence', {})
    repaired = []
    decisions = []
    for effect in effects:
        group = _group(effect)
        decisions.append((effect, repair(effect.get('name'), vocabulary.get(group, Counter()), group,
                                         vocabulary.get('together', {}).get(effect.get('name'), ())) if group else None))
    # The effects read right come first, so a repaired one finds its twin
    # whichever of the two the recognizer produced first.
    kept = [effect for effect, target in decisions if target is None]
    by_key = {}
    for effect in kept:
        by_key.setdefault(_key(effect), effect)
    for effect, target in decisions:
        if target is None:
            continue
        old_key = _key(effect)
        frames = evidence.pop(old_key, [])
        renamed = deepcopy(effect)
        read = renamed.get('name')
        renamed['name'] = target
        renamed['name_resolution'] = 'vocabulary_repair'
        renamed.setdefault('alternate_name_evidence', []).append(dict(name=read, evidence=list(frames)))
        repaired.append(dict(read=read, name=target, kind=effect.get('kind'), evidence=list(frames)))
        new_key = _key(renamed)
        twin = by_key.get(new_key)
        if twin is not None and twin.get('amount') == renamed.get('amount') and twin.get('value') == renamed.get('value'):
            twin.setdefault('alternate_name_evidence', []).append(dict(name=read, evidence=list(frames)))
            twin['name_resolution'] = twin.get('name_resolution') or 'vocabulary_repair'
            proofs = evidence.setdefault(new_key, [])
            proofs.extend(p for p in frames if p not in proofs)
            continue
        proofs = evidence.setdefault(new_key, [])
        proofs.extend(p for p in frames if p not in proofs)
        kept.append(renamed)
        by_key.setdefault(new_key, renamed)
    event['effects'] = kept
    if repaired:
        event.setdefault('vocabulary_repairs', []).extend(repaired)
    return repaired
