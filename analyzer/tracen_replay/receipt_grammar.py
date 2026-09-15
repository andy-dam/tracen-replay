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


def normalize(text, *, allow_boundary_repair=False):
    receipt = friendship_receipt(text)
    if receipt and receipt['boundary_repaired'] and not allow_boundary_repair:
        return text
    return receipt['normalized'] if receipt else text
