"""Constrained repair of fixed receipt words, never names or amounts."""
import re


def fixed_word(observed, expected):
    """Allow at most one character deletion/substitution in a fixed UI word."""
    if observed == expected:
        return True
    if len(observed) == len(expected):
        return sum(a != b for a, b in zip(observed, expected)) == 1
    if len(observed) == len(expected) - 1:
        return any(observed == expected[:i] + expected[i + 1:] for i in range(len(expected)))
    return False


def friendship_receipt(text):
    """Return a full past-tense receipt with at most one damaged fixed word.

    The name, direction, numeric token, punctuation and word boundaries must
    be present. Incomplete typewriter text and future/negative statements do
    not satisfy this grammar. Matching is case sensitive like the UI.
    """
    match = re.fullmatch(r'(?P<keyword>[A-Za-z]+) (?P<with>[A-Za-z]+) (?P<name>\S(?:.*?\S)?) '
                         r'(?P<went>[A-Za-z]+) up by (?P<amount>\d+)(?P<stop>[.!])', text)
    if not match:
        return None
    words = [(match['keyword'], 'Friendship'), (match['with'], 'with'), (match['went'], 'went')]
    if sum(a != b for a, b in words) > 1 or not all(fixed_word(a, b) for a, b in words):
        return None
    # A recipient is a noun phrase, not another clause whose words happen to
    # precede the fixed receipt suffix. No attempt is made to repair it.
    if any(mark in match['name'] for mark in '.!?\n'):
        return None
    return dict(name=match['name'], amount=match['amount'], boundary_repaired=match['with'] != 'with',
                normalized=f"Friendship with {match['name']} went up by {match['amount']}{match['stop']}")


def boundary_repair_supported(line, overlay):
    """Require a localized obstruction and agreement on the unchanged name.

    Repairing the separator next to a name is unsafe when click particles can
    erase the beginning/end of that name. A high line confidence alone cannot
    establish it. The existing source-validated alignment must isolate the
    obstruction within the fixed separator and independently retain the same
    recipient and amount.
    """
    if not isinstance(overlay, dict) or not isinstance(overlay.get('provenance'),dict) or overlay['provenance'].get('validated_by') != 'annotate_path':
        return False
    boxes = overlay.get('overlay_boxes', [])
    if len(boxes) != 1:
        return False
    receipt = friendship_receipt(line['text'])
    if receipt is None:
        return False
    for aligned in overlay.get('alignments', []):
        other = friendship_receipt(aligned.get('recognized_text',''))
        words, columns = aligned.get('words',[]), aligned.get('columns',[])
        length = aligned.get('line_length',0)
        if (aligned.get('line_box') != line['box'] or aligned.get('confidence',0) < 95 or not other
            or other['name'] != receipt['name'] or other['amount'] != receipt['amount']
            or ' '.join(words) != aligned['recognized_text'] or len(columns) != len(words) or len(words) < 3
            or length <= 0 or any(not c for c in columns)):
            continue
        if any(v < 0 or v >= length for c in columns for v in c):
            continue
        left, top, right, bottom = aligned['line_box']
        scale = (right-left)/length
        separator_start = left+(max(columns[0])+.75)*scale
        name_start = left+(min(columns[2])-.5)*scale
        box = boxes[0]
        if separator_start <= box[0] < box[2] <= name_start and top <= box[1] < box[3] <= bottom:
            return True
    return False


def hint_wording(text):
    """Return a hint receipt whose two fixed words are repaired, or None.

    "Gained 4 hint level(s) for Pace Chaser Corners" keeps its data in the
    number and the skill name; the two words between them are fixed UI text.
    Each may carry one damaged glyph, the tolerance this module allows
    everywhere. The number, the name and the punctuation come back exactly as
    they were read.

    Repairing those words is only safe where something proves the damage came
    from an obstruction rather than from the text; see
    ``receipt_occlusion.hint_wording_obstructed``.
    """
    match = re.fullmatch(r'(?P<head>Gained \d+ hint )(?P<level>\S+) (?P<preposition>\S+) (?P<name>\S.*)', text)
    if not match or (match['level'], match['preposition']) == ('level(s)', 'for'):
        return None
    if not fixed_word(match['level'], 'level(s)') or not fixed_word(match['preposition'], 'for'):
        return None
    return f"{match['head']}level(s) for {match['name']}"


_FIXED_SUBJECTS = ('Speed', 'Stamina', 'Power', 'Guts', 'Wit', 'Energy',
                   'Dance', 'Passion', 'Vocals', 'Visuals', 'Composure')
_READ_SUBJECTS = _FIXED_SUBJECTS + ('Vocal', 'Visual')


def subject_receipt(text):
    """Return a one-word receipt whose fixed subject carries one damaged glyph, or None.

    "Yocals went up by 20." with the cursor over the V: the subject of a stat,
    performance or energy receipt is one of a few fixed UI words, and within
    one substitution or deletion exactly one of them fits. The direction, the
    number and the punctuation come back exactly as they were read. A subject
    that already reads as one of those words needs nothing; one that fits
    none, or two, is left alone.

    Repairing the word is only safe where something proves the damage came
    from an obstruction rather than from the text; see
    ``receipt_occlusion.subject_word_obstructed``.
    """
    match = re.fullmatch(r'(?P<subject>[A-Za-z]+) (?P<tail>went (?:up|down) by \d+[.!])', text)
    if not match or match['subject'] in _READ_SUBJECTS:
        return None
    names = [name for name in _FIXED_SUBJECTS if _subject_within_reach(match['subject'], name)]
    if len(names) != 1:
        return None
    return f"{names[0]} {match['tail']}"


def _subject_within_reach(observed, expected):
    """One damaged glyph in a short subject, two in a word of seven letters or more.

    The cursor covers about two letters of a word ("Slumina" for Stamina);
    the fixed subjects are far enough apart that two edits still name one.
    """
    if fixed_word(observed, expected):
        return True
    if len(expected) < 7 or abs(len(observed) - len(expected)) > 1:
        return False
    from .gameplay import _edit_distance
    return _edit_distance(observed, expected) <= 2


_DIRECTIONS = ('up', 'down')


def direction_receipt(text, direction):
    """Return a one-word receipt whose direction word was hidden, read as ``direction``, or None.

    "Speed went  by 5." with the cursor parked over "up", or "went uby 5."
    with the cursor over most of it: the subject, "went", "by", the number
    and the punctuation come back exactly as they were read; the one word
    between "went" and "by" is missing or a remnant. A remnant must be
    letters of ``direction`` in their order ("u", "dwn"), and the subject
    must already read as a field: this repair touches the direction alone.

    Which direction it was must come from elsewhere, the gain popup over the
    scene whose sign and amount state it; see
    ``receipt_occlusion.direction_word_obstructed``.
    """
    if direction not in _DIRECTIONS:
        return None
    match = re.fullmatch(r'(?P<subject>[A-Za-z]+) went (?P<remnant>[A-Za-z]*?) ?by (?P<amount>\d+)(?P<stop>[.!])',
                         re.sub(r' {2,}', ' ', text))
    if not match or match['subject'] not in _READ_SUBJECTS:
        return None
    position = 0
    for letter in match['remnant']:
        position = direction.find(letter, position)
        if position < 0:
            return None
        position += 1
    return f"{match['subject']} went {direction} by {match['amount']}{match['stop']}"


def normalize(text, *, allow_boundary_repair=False):
    receipt = friendship_receipt(text)
    if receipt and receipt['boundary_repaired'] and not allow_boundary_repair:
        return text
    return receipt['normalized'] if receipt else text
