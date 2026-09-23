"""Read the visible training label without deciding whether it was selected."""

from bisect import bisect_left, bisect_right
from copy import deepcopy
import re

from .layout import place, place_y
from .source_clock import elapsed


_TRAINING_OPTIONS = ('Speed', 'Stamina', 'Power', 'Guts', 'Wit')
# ``training_events`` already uses this continuity bound when it builds a
# result group.  The identity bridge uses the same bound for the short
# transition immediately next to that group; it does not search an
# unbounded recording for a matching name.
_MAX_RESULT_CONTINUITY_GAP_MS = 500

# The name the game prints under the heading belongs to one option only.
# Every pair here was read, heading and name together, in earlier careers,
# and no name appeared under two options. A name that is not listed proves
# nothing; it is never guessed.
_TRAINING_NAMES = {
    'Turf': 'Speed', 'Treadmill': 'Speed', 'Running': 'Speed', 'Exercise Bike': 'Speed',
    'Floor Cleaning': 'Speed', 'Volleyball Dive': 'Speed',
    'Breaststroke': 'Stamina', 'Freestyle': 'Stamina', 'Long-Distance Swimming': 'Stamina',
    'Dirt': 'Power', 'Squats': 'Power', 'Resistance Training': 'Power',
    'Incline': 'Guts', 'Bunny-Hop': 'Guts', 'Dance Practice': 'Guts', 'Stair Dash': 'Guts',
    'Beach Tire Pull': 'Guts', 'Tire Pull': 'Guts',
    'Shogi': 'Wit', 'Reading': 'Wit', 'Video Research': 'Wit', 'Studying': 'Wit',
    'Push-Button Quiz': 'Wit', 'Quiz': 'Wit',
}
# The small "Lvl" is the part of the heading the text reader gets wrong most:
# "Lvi", "LvI", "Lv1". Those spellings cannot be any other word on this line.
_HEADING_RE = re.compile(r'([A-Za-z]{2,8})\s*Lv[lIi1|!]\s*(\d{1,2})?')
# Where the heading's centre lies on the PC pane; its banner is pinned to the
# top left, so across nothing moves.
_HEADING_BOX = (210, 160, 420, 200)


def _one_edit(a, b):
    """True when ``a`` and ``b`` differ by at most one letter."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def parse_heading(text, exact=True):
    """``(option, level)`` of an "Option Lvl N" heading, or ``None``.

    ``exact`` requires the option word as printed; without it one wrong
    letter is allowed ("Wil"), which the caller must back with other proof.
    """
    match = _HEADING_RE.fullmatch(str(text or '').strip())
    if not match:
        return None
    word = match[1].casefold()
    options = [item for item in _TRAINING_OPTIONS
               if (word == item.casefold() if exact else _one_edit(word, item.casefold()))]
    if len(options) != 1:
        return None
    return options[0], int(match[2]) if match[2] else None


def _names_below(lines, heading, minimum_confidence):
    left, _, _, bottom = heading['box']
    names = []
    for line in lines:
        if (not isinstance(line, dict) or line is heading or len(line.get('box', [])) != 4
                or line.get('confidence', 0) < minimum_confidence):
            continue
        x, y, xx, yy = line['box']
        if (bottom - 6 <= y <= bottom + 24 and 0 < yy - y <= 50 and abs(x - left) <= 24
                and xx > x and xx <= 650):
            names.append(line)
    return names


def heading_option(lines):
    """The option the training heading names, lower-case, or ``None``.

    A heading read with confidence names its option by itself. A weaker or
    one-letter-off heading counts only when the name printed under it was
    read clearly and is a known name of that same option.
    """
    found = set()
    heading_box = place(_HEADING_BOX, 'tl')
    for line in lines:
        if not isinstance(line, dict) or len(line.get('box', [])) != 4:
            continue
        left, top, right, bottom = line['box']
        if not (heading_box[0] <= (left + right) / 2 <= heading_box[2]
                and heading_box[1] <= (top + bottom) / 2 <= heading_box[3]):
            continue
        confidence = line.get('confidence', 0)
        parsed = parse_heading(line.get('text'), exact=True)
        if parsed and confidence >= 90:
            found.add(parsed[0])
            continue
        parsed = parse_heading(line.get('text'), exact=False)
        if parsed and confidence >= 70 and any(
                _TRAINING_NAMES.get(name.get('text', '').strip()) == parsed[0]
                for name in _names_below(lines, line, 97)):
            found.add(parsed[0])
    return found.pop().lower() if len(found) == 1 else None


def _read_identity(lines, screen, option, minimum_confidence, corroborated=False):
    """Attach a nearby name to a matching option/level heading in one frame.

    The caller owns screen classification and action commitment. This reader
    cannot promote a preview or transfer a name between frames/options.
    """
    if screen not in ('training_result', 'training_preview', 'training'):
        return {}
    if option not in _TRAINING_OPTIONS:
        return {}
    eligible = [line for line in lines if isinstance(line, dict)
                and line.get('confidence', 0) >= minimum_confidence
                and len(line.get('box', [])) == 4]
    headings = []
    for line in eligible:
        parsed = parse_heading(line.get('text'), exact=not corroborated)
        x, y, xx, yy = line['box']
        if parsed and parsed[0] == option and 200 <= x <= 330 and place_y(155, 't') <= y <= place_y(200, 't') and yy > y:
            headings.append((line, parsed[1]))
    if len(headings) != 1:
        return {}
    heading, level = headings[0]
    left, _, right, bottom = heading['box']
    names = []
    for line in eligible:
        x, y, xx, yy = line['box']
        text = line.get('text', '').strip()
        if (bottom <= y <= bottom + 24 and 0 < yy - y <= 50
                and abs(x - left) <= 24 and xx > x and xx <= 650
                and re.fullmatch(r"[A-Za-z][A-Za-z '&’\-]*", text)):
            names.append(line)
    if len(names) != 1:
        return {}
    basis = 'same_frame_training_heading_and_name'
    if corroborated:
        # A weak heading stands only on a clearly read, known name of the
        # same option.
        if names[0].get('confidence', 0) < 97 or _TRAINING_NAMES.get(names[0]['text'].strip()) != option:
            return {}
        basis = 'weak_heading_corroborated_by_known_training_name'
    return {'training_name': names[0]['text'].strip(), 'training_level': level,
            'training_identity_evidence': {'heading': deepcopy(heading), 'name': deepcopy(names[0]),
                                           'basis': basis}}


def read_identity(lines, screen, option):
    """Keep weak complete identities as candidates until result corroboration."""
    accepted = _read_identity(lines, screen, option, 97)
    if accepted:
        return accepted
    candidate = _read_identity(lines, screen, option, 90)
    if candidate:
        return {'training_identity_candidate': candidate}
    return _read_identity(lines, screen, option, 70, corroborated=True)


def _canonical_option(value):
    """Return the canonical lower-case option, or ``None`` for no option."""

    if not isinstance(value, str):
        return None
    value = value.strip().casefold()
    return value if value in {item.casefold() for item in _TRAINING_OPTIONS} else None


def _source_options(row):
    """Collect explicit option markers from one parsed source observation.

    ``preview_option`` is never sufficient to bind an action.  It is retained
    here only as a consistency check against the selected result option and
    the same-frame heading.  The result group supplies the commitment signal.
    """

    if not isinstance(row, dict):
        return set()
    facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
    stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
    values = [
        row.get('training_option'),
        facts.get('training_option'),
        facts.get('preview_option'),
        stats.get('preview_option'),
    ]
    return {option for value in values if (option := _canonical_option(value)) is not None}


def _source_has_invalid_option(row):
    """Return whether an explicitly populated option marker is unreadable."""

    if not isinstance(row, dict):
        return True
    facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
    stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
    values = [
        row.get('training_option'),
        facts.get('training_option'),
        facts.get('preview_option'),
        stats.get('preview_option'),
    ]
    return any(isinstance(value, str) and value.strip()
               and _canonical_option(value) is None for value in values)


def _source_is_explicit_preview(row):
    """Return whether a row is explicitly a preview/option-selection view.

    A result card can carry ``preview_overlay_proven`` while the gameplay
    animation is being inspected.  That fact describes visible overlay
    effects; it is not a phase marker.  Only explicit preview screen/phase
    markers block identity bridging, which allows the T053 transition frame
    to be used without promoting preview amounts.
    """

    if not isinstance(row, dict):
        return True
    facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
    stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
    if row.get('screen') == 'training_preview' or stats.get('training_preview') is True:
        return True
    if facts.get('preview') is True or facts.get('preview_only') is True:
        return True
    markers = [row.get('phase'), row.get('training_phase'), row.get('source_phase')]
    markers.extend(facts.get(key) for key in ('phase', 'training_phase', 'source_phase'))
    for marker in markers:
        if not isinstance(marker, str):
            continue
        marker = marker.strip().casefold().replace('-', '_').replace(' ', '_')
        if marker in ('preview', 'training_preview', 'projected', 'projection'):
            return True
    return False


def _source_physical_identity(row):
    """Validate the source row's timestamp/path/digest identity."""

    try:
        from .training_outcome import _source_identity
        return _source_identity(row)
    except (ImportError, TypeError, ValueError):
        return None


def _same_frame_identity(row, option):
    """Validate a complete, strong same-frame identity in a source row.

    The row's supplied fact is re-read from its own heading/name proof.  This
    prevents a mutable ``training_name`` or ``context_title`` value from
    becoming identity evidence.  In particular, the auxiliary right-hand
    trainee/profile panel is never consulted.
    """

    if not isinstance(row, dict) or _source_is_explicit_preview(row):
        return None
    if row.get('screen') not in ('unknown', 'training'):
        # Result rows already in the group are handled by ``summarize``.  An
        # unrelated result row adjacent in time must not be borrowed.
        return None
    expected = _canonical_option(option)
    if expected is None:
        return None
    if _source_has_invalid_option(row):
        return None
    options = _source_options(row)
    if options and options != {expected}:
        return None
    timestamp = row.get('source_timestamp_ms')
    evidence = row.get('evidence')
    if (type(timestamp) is not int or timestamp < 0
            or not isinstance(evidence, str) or not evidence.strip()):
        return None
    source_identity = _source_physical_identity(row)
    if source_identity is None:
        return None
    facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
    name = facts.get('training_name')
    proof = facts.get('training_identity_evidence')
    if not isinstance(name, str) or not name.strip() or not isinstance(proof, dict):
        return None
    # The heading itself is the explicit option evidence.  It must agree with
    # the result group's selected option, even when the parser had no
    # ``training_option`` field on this transition row.
    verified = _read_identity(
        [proof.get('heading'), proof.get('name')],
        'training',
        expected.capitalize(),
        97,
    )
    if not verified or verified.get('training_name') != name.strip():
        return None
    return dict(
        name=name.strip(),
        source_timestamp_ms=timestamp,
        evidence=evidence,
        proof=deepcopy(verified['training_identity_evidence']),
        source_identity=list(source_identity),
        basis='adjacent_gameplay_transition_same_frame_identity',
    )


def _group_interval(rows, option):
    """Return a continuous selected-option result interval, or ``None``."""

    expected = _canonical_option(option)
    if expected is None:
        return None
    matching = []
    explicit_options = set()
    for row in rows:
        if not isinstance(row, dict) or row.get('screen') != 'training_result':
            continue
        row_option = _canonical_option(row.get('training_option'))
        if row.get('training_option') is not None and row_option is None:
            return None
        if row_option is not None:
            explicit_options.add(row_option)
        if row_option != expected:
            continue
        timestamp = row.get('source_timestamp_ms')
        if type(timestamp) is int and timestamp >= 0:
            matching.append(timestamp)
    if not matching or explicit_options != {expected}:
        return None
    matching.sort()
    if any(elapsed(left, right) > _MAX_RESULT_CONTINUITY_GAP_MS
           for left, right in zip(matching, matching[1:])):
        return None
    return matching[0], matching[-1]


def _bridge_observations(rows, option, source_rows):
    """Read identity rows adjacent to one continuous result group.

    The source row must be a gameplay transition observation, carry a valid
    timestamp/evidence identity, and contain a revalidated same-frame
    option-heading/name pair.  It can be immediately before or after the
    result interval, but never across an explicit preview or option-switch
    row.  This is a bounded bridge, rather than a nearest-name join.
    """

    interval = _group_interval(rows, option)
    expected = _canonical_option(option)
    if interval is None or expected is None or not isinstance(source_rows, (list, tuple)):
        return []
    start, end = interval
    group_identities = set()
    for row in rows:
        identity = _source_physical_identity(row)
        if identity is not None:
            group_identities.add(identity)
    candidates = []
    # A row without an integer timestamp has no physical identity and can
    # never bridge; the timestamped rows keep their time order, and only the
    # rows within one continuity gap of the interval can be candidates.
    ordered = sorted(
        (row for row in source_rows if isinstance(row, dict) and type(row.get('source_timestamp_ms')) is int),
        key=lambda row: row['source_timestamp_ms'],
    )
    times = [row['source_timestamp_ms'] for row in ordered]
    reach = _MAX_RESULT_CONTINUITY_GAP_MS
    for row in ordered[bisect_left(times, start - reach):bisect_right(times, end + reach)]:
        timestamp = row.get('source_timestamp_ms')
        identity = _source_physical_identity(row)
        if identity is None or identity in group_identities:
            continue
        if start <= timestamp <= end:
            # A non-result row at the exact first result timestamp can be a
            # second gameplay view of that frame.  Other in-group rows are
            # already covered by the normal result-row path.
            if timestamp != start:
                continue
            side_start, side_end = timestamp, start
        elif timestamp < start and start - timestamp <= _MAX_RESULT_CONTINUITY_GAP_MS:
            side_start, side_end = timestamp, start
        elif timestamp > end and timestamp - end <= _MAX_RESULT_CONTINUITY_GAP_MS:
            side_start, side_end = end, timestamp
        else:
            continue
        if _same_frame_identity(row, expected) is None:
            continue
        # Any explicit alternate option or preview in the bridge window is a
        # boundary.  We do not rely on a source row's position alone to infer
        # that it belongs to this action.
        obstructed = False
        for other in ordered[bisect_left(times, side_start):bisect_right(times, side_end)]:
            other_time = other.get('source_timestamp_ms')
            if type(other_time) is not int or not side_start <= other_time <= side_end:
                continue
            if other is row:
                continue
            if _source_has_invalid_option(other):
                obstructed = True
                break
            declared = _source_options(other)
            if declared and declared != {expected}:
                obstructed = True
                break
            if _source_is_explicit_preview(other):
                obstructed = True
                break
        if obstructed:
            continue
        observation = _same_frame_identity(row, expected)
        if observation is not None:
            candidates.append(observation)
    # One physical source observation is one observation for one literal name,
    # even if its timestamp/path/digest is repeated in a copied sidecar.
    # Conflicting names from the same physical source remain separate so the
    # caller can quarantine the conflict instead of silently choosing one.
    unique = []
    seen = set()
    for observation in candidates:
        timestamp, path, digest = observation['source_identity']
        physical = ('digest', digest) if digest else ('timestamp_path', timestamp, path)
        key = (physical, observation['name'])
        if key in seen:
            continue
        seen.add(key)
        unique.append(observation)
    return unique


def summarize(rows, option, source_rows=None):
    """Summarize identity inside an already established training result group."""
    option_key = option.casefold() if isinstance(option, str) else None
    observations = []
    candidates = []
    for row in rows:
        row_option = row.get('training_option')
        if (row.get('screen') != 'training_result'
                or _source_is_explicit_preview(row)
                or not isinstance(row_option, str)
                or row_option.casefold() != option_key):
            continue
        facts = row.get('facts', {})
        candidate = facts.get('training_identity_candidate')
        if isinstance(candidate, dict):
            from .training_outcome import _source_identity
            identity = _source_identity(row)
            proof = candidate.get('training_identity_evidence', {})
            if identity and isinstance(proof, dict):
                verified = _read_identity([proof.get('heading'), proof.get('name')],
                                          'training_result', option.capitalize(), 90)
                if verified and verified == candidate:
                    candidates.append((row, verified, identity))
        name = facts.get('training_name')
        proof = facts.get('training_identity_evidence')
        timestamp = row.get('source_timestamp_ms')
        evidence = row.get('evidence')
        if (not isinstance(name, str) or not name.strip() or not isinstance(proof, dict)
                or proof.get('basis') != 'same_frame_training_heading_and_name'
                or type(timestamp) is not int or timestamp < 0
                or not isinstance(evidence, str) or not evidence):
            continue
        # Revalidate the supplied proof; an unrelated same-frame string cannot
        # inherit the confidence or identity of another OCR observation.
        parsed = read_identity(
            [proof.get('heading'), proof.get('name')],
            'training_result',
            option.capitalize() if isinstance(option, str) else option,
        )
        if parsed.get('training_name') != name.strip():
            continue
        observations.append(dict(name=name.strip(), source_timestamp_ms=timestamp,
                                 evidence=evidence, proof=deepcopy(proof)))
    # Each frame contains the complete same heading/name pair. Repetition
    # corroborates that literal pair; it never assembles a name from pieces
    # or borrows the identity of a browsed option.
    grouped = {}
    for row, candidate, identity in candidates:
        # The level digit can be hidden on a result frame; the name is the identity.
        key = candidate['training_name']
        grouped.setdefault(key, []).append((row, candidate, identity))
    for name, members in grouped.items():
        timestamps, paths, hashes = set(), set(), set()
        distinct = []
        for row, candidate, (timestamp, path, digest) in members:
            if timestamp in timestamps or path in paths or (digest and digest in hashes):
                continue
            timestamps.add(timestamp)
            paths.add(path)
            if digest:
                hashes.add(digest)
            distinct.append((row, candidate))
        if len(distinct) < 2:
            continue
        proofs = [candidate['training_identity_evidence'] for _, candidate in distinct]
        if not all(any(proof[field]['confidence'] >= 97 for proof in proofs)
                   for field in ('heading', 'name')):
            continue
        for row, candidate in distinct:
            observations.append(dict(name=name, source_timestamp_ms=row['source_timestamp_ms'],
                                     evidence=row['evidence'],
                                     proof=deepcopy(candidate['training_identity_evidence']),
                                     basis='repeated_complete_training_identity'))
    bridge = _bridge_observations(rows, option, source_rows)
    observations.extend(bridge)
    if not observations:
        return {}
    names = sorted({row['name'] for row in observations})
    result = {'training_name_observations': observations}
    if bridge:
        interval = _group_interval(rows, option)
        result['training_identity_bridge'] = dict(
            basis='continuous_result_interval_adjacent_gameplay_identity',
            selected_option=_canonical_option(option),
            result_interval_ms=list(interval) if interval is not None else None,
            observations=deepcopy(bridge),
        )
    if len(names) == 1:
        result.update(training_name=names[0],
                      training_name_evidence=list(dict.fromkeys(row['evidence'] for row in observations)))
    else:
        result['training_name_conflicts'] = names
    return result
