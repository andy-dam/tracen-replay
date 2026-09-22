"""Resolve source-backed identity alternatives in inheritance spark receipts.

The OCR reader emits an ``inheritance_spark`` effect for every accepted line.
That is useful evidence, but a scrolling result can expose the same line more
than once and a partially obscured name can change between views.  This module
only deals with that identity question.  It never normalizes a name, consults
a game catalog, or chooses a longer-looking spelling.

``resolve`` mutates an outcome event in the same way as the other receipt
identity passes.  A proven repeated-name observation can remain an accepted
effect while the competing spelling is retained as an ambiguous candidate.
When the source cannot establish which observations share a receipt slot, all
names in that possible slot are moved to ``ambiguous_effect_candidates`` so a
consumer cannot count them as separate confirmed sparks.
"""

from copy import deepcopy
import math


MIN_CONFIDENCE = 95
MAX_ADJACENT_MS = 250
MIN_SCROLL_PIXELS = 8
MAX_SCROLL_PIXELS = 80
STATIONARY_PIXELS = 3


def _valid_box(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    if not all(type(value) in (int, float) for value in box):
        return False
    # Avoid passing arbitrarily large integers to math.isfinite: it can raise
    # OverflowError before the coordinate bounds below reject the value.
    if any(type(value) is float and not math.isfinite(value) for value in box):
        return False
    return 148 <= box[0] < box[2] <= 958 and 0 <= box[1] < box[3] <= 1080


def _center(box):
    return (box[1] + box[3]) / 2


def _height(box):
    return box[3] - box[1]


def _lines(row):
    """Return accepted receipt-area OCR lines from one source row."""
    if not isinstance(row, dict) or row.get('screen') != 'event_outcome':
        return []
    ocr = row.get('ocr', {})
    if not isinstance(ocr, dict) or not isinstance(ocr.get('neural'), list):
        return []
    result = []
    for line in ocr['neural']:
        if not isinstance(line, dict) or not isinstance(line.get('text'), str):
            continue
        box = line.get('box', [])
        confidence = line.get('confidence', 0)
        if (not _valid_box(box) or type(confidence) not in (int, float)
                or (type(confidence) is float and not math.isfinite(confidence))
                or confidence < MIN_CONFIDENCE
                or line.get('overlay_occluded') is True
                or not 780 <= _center(box) <= 960):
            continue
        result.append(line)
    return result


def _name_texts(effect):
    return {value for value in (effect.get('raw_text'), effect.get('original_text'))
            if isinstance(value, str) and value}


def _field(effect):
    return f"{effect.get('kind', '')}||{effect.get('name', '')}"


def _payload(effect):
    """Values that must agree before two lines can be one identity variant."""
    return tuple(effect.get(key) for key in ('field', 'amount', 'direction', 'value'))


def _unique(values):
    return list(dict.fromkeys(value for value in values if isinstance(value, str)))


def _observations(effect, field_evidence, rows_by_evidence):
    """Find exact accepted OCR observations for one effect."""
    proofs = field_evidence.get(_field(effect), [])
    if not isinstance(proofs, list):
        return []
    texts = _name_texts(effect)
    result = []
    for proof in _unique(proofs):
        row = rows_by_evidence.get(proof)
        if not isinstance(row, dict):
            continue
        matches = [line for line in _lines(row) if line.get('text') in texts]
        timestamp = row.get('source_timestamp_ms')
        if len(matches) != 1 or type(timestamp) is not int:
            continue
        result.append(dict(timestamp=timestamp, evidence=proof, row=row,
                           line=matches[0]))
    return result


def _same_context(event, first, second):
    if first.get('context_title') != second.get('context_title'):
        return False
    event_title = event.get('context_title')
    if event_title is not None and first.get('context_title') != event_title:
        return False
    return True


def _stable_box(first, second):
    """Allow detector jitter while retaining the same horizontal slot."""
    if not _valid_box(first) or not _valid_box(second):
        return False
    return (abs(first[0] - second[0]) <= 5
            and abs(first[2] - first[0] - (second[2] - second[0])) <= 35
            and abs(_height(first) - _height(second)) <= 8)


def _matching_anchors(first, second, excluded_texts):
    """Return exact, unique lines that can anchor one receipt track.

    The same text must occur exactly once in each row.  Geometry is checked by
    the caller because stationary and scrolling tracks have different motion
    requirements.
    """
    first_by_text = {}
    second_by_text = {}
    for line in _lines(first):
        if line.get('text') in excluded_texts:
            continue
        first_by_text.setdefault(line['text'], []).append(line)
    for line in _lines(second):
        if line.get('text') in excluded_texts:
            continue
        second_by_text.setdefault(line['text'], []).append(line)
    result = []
    for text in sorted(first_by_text.keys() & second_by_text.keys()):
        if len(first_by_text[text]) != 1 or len(second_by_text[text]) != 1:
            continue
        left, right = first_by_text[text][0], second_by_text[text][0]
        if _stable_box(left['box'], right['box']):
            result.append(dict(text=text, first_box=list(left['box']),
                               second_box=list(right['box']),
                               confidence=[left['confidence'], right['confidence']]))
    return result


def _shared_lines_moved(first, second, excluded_texts):
    """True when an exact line unique to each row sits at a different height in the two rows.

    Unlike an anchor, the line need not keep its box size: a line that moved
    while it was read taller or shorter still moved.
    """
    first_by_text, second_by_text = {}, {}
    for line in _lines(first):
        if line.get('text') not in excluded_texts:
            first_by_text.setdefault(line['text'], []).append(line)
    for line in _lines(second):
        if line.get('text') not in excluded_texts:
            second_by_text.setdefault(line['text'], []).append(line)
    for text in first_by_text.keys() & second_by_text.keys():
        if len(first_by_text[text]) != 1 or len(second_by_text[text]) != 1:
            continue
        if abs(_center(second_by_text[text][0]['box']) - _center(first_by_text[text][0]['box'])) > STATIONARY_PIXELS:
            return True
    return False


def _shared_anchor_texts(first, second, excluded_texts):
    """Return exact shared receipt-area text, including non-unique readings."""
    first_texts = {line.get('text') for line in _lines(first)
                   if line.get('text') not in excluded_texts}
    second_texts = {line.get('text') for line in _lines(second)
                    if line.get('text') not in excluded_texts}
    return first_texts & second_texts


def _adjacent_source_rows(first_timestamp, second_timestamp, source_successors):
    """Require endpoint observations to be consecutive source rows."""
    start, end = sorted((first_timestamp, second_timestamp))
    return source_successors.get(start) == end


def _track(event, left, right, first, second, source_successors):
    """Classify a pair of candidate observations as a possible same slot.

    ``proven`` is true only when a unique exact neighboring line shares the
    target's motion.  A same-slot pair without such an anchor is still a
    possible identity conflict, but must be reported as ambiguous.
    """
    if not isinstance(first, dict) or not isinstance(second, dict):
        return None
    if first['timestamp'] == second['timestamp']:
        return None
    start, end = sorted((first['timestamp'], second['timestamp']))
    if end - start > MAX_ADJACENT_MS:
        return None
    if not _adjacent_source_rows(start, end, source_successors):
        return None
    earlier, later = ((first, second) if first['timestamp'] < second['timestamp']
                      else (second, first))
    if not _same_context(event, earlier['row'], later['row']):
        return None
    first_box, second_box = earlier['line']['box'], later['line']['box']
    if not _stable_box(first_box, second_box):
        return None
    target_delta = _center(second_box) - _center(first_box)
    stationary = abs(target_delta) <= STATIONARY_PIXELS
    scrolling = (-MAX_SCROLL_PIXELS <= target_delta <= -MIN_SCROLL_PIXELS)
    if not stationary and not scrolling:
        return None

    excluded = _name_texts(left) | _name_texts(right)
    shared_texts = _shared_anchor_texts(earlier['row'], later['row'], excluded)
    anchors = _matching_anchors(earlier['row'], later['row'], excluded)
    compatible = []
    for anchor in anchors:
        anchor_delta = (_center(anchor['second_box'])
                        - _center(anchor['first_box']))
        if stationary:
            if abs(anchor_delta) > STATIONARY_PIXELS:
                continue
        elif not (-MAX_SCROLL_PIXELS <= anchor_delta <= -MIN_SCROLL_PIXELS
                  and abs(anchor_delta - target_delta) <= STATIONARY_PIXELS + 5):
            continue
        first_spacing = _center(first_box) - _center(anchor['first_box'])
        second_spacing = _center(second_box) - _center(anchor['second_box'])
        if abs(first_spacing - second_spacing) > STATIONARY_PIXELS + 5:
            continue
        compatible.append(anchor)

    if stationary and _shared_lines_moved(earlier['row'], later['row'], excluded):
        # An exact shared line moved while the target stayed put: the receipt
        # scrolled, or the bubble re-rendered with its next lines, and another
        # spark line now sits in the slot the target held. Only a bubble whose
        # every shared line stayed can show one line read two ways.
        return None
    # A second exact line that moves differently is evidence for another
    # receipt or a changed layout.  Do not choose the one anchor that happens
    # to fit and call the slot continuous.  For an upward-moving target, a
    # target-only match is not enough to make two different spark lines one
    # identity: leave those lines distinct.  A stationary target can still be
    # surfaced as ambiguous when no exact anchor exists, because it occupies
    # the same receipt slot in consecutive views.
    if anchors and len(compatible) != len(anchors):
        compatible = []
    if scrolling and not compatible:
        return None
    if stationary and not compatible and not shared_texts:
        # A target occupying a similar y-position in two frames is common
        # while a different spark line scrolls into view. Without even one
        # exact shared receipt line there is no identity relation to mark.
        return None

    return dict(
        mode='stationary_slot' if stationary else 'upward_scroll',
        proven=bool(compatible),
        evidence_pair=[earlier['evidence'], later['evidence']],
        timestamp_pair=[earlier['timestamp'], later['timestamp']],
        target_boxes=[list(first_box), list(second_box)],
        anchors=compatible,
    )


def _simultaneous(left, right, observations):
    """A row displaying both names proves two visible lines, not one variant."""
    left_texts, right_texts = _name_texts(left), _name_texts(right)
    for observation in observations:
        lines = _lines(observation['row'])
        for first in lines:
            for second in lines:
                # Neighboring receipt boxes include margins that can overlap.
                # Separate their text centers by most of a line height; one
                # line and duplicate OCR boxes cannot satisfy both identities.
                if (first['text'] in left_texts and second['text'] in right_texts
                        and abs(_center(first['box']) - _center(second['box']))
                        >= 0.6 * max(_height(first['box']), _height(second['box']))):
                    return True
    return False


def _relation(event, left, right, observations, source_successors):
    left_observed, right_observed = observations[id(left)], observations[id(right)]
    if not left_observed or not right_observed:
        return None
    if _simultaneous(left, right, left_observed + right_observed):
        return None
    possible, proven = [], []
    for first in left_observed:
        for second in right_observed:
            candidate = _track(event, left, right, first, second, source_successors)
            if candidate is None:
                continue
            possible.append(candidate)
            if candidate['proven']:
                proven.append(candidate)
    if not possible:
        return None
    # A proven window supersedes a weaker same-slot window for the same pair.
    # This matters when an earlier scrolling view lacks an exact anchor but a
    # later stationary view supplies one.
    selected = proven or possible
    unique = {}
    for item in selected:
        key=tuple(item['evidence_pair'])
        unique[key] = item
    selected = list(unique.values())
    if proven and len(selected) > 1:
        # Multiple disconnected tracks may be separate sparks.  They must not
        # be combined just because their OCR names happen to differ.
        anchor_texts = {tuple(anchor['text'] for anchor in item['anchors'])
                        for item in selected}
        if len(anchor_texts) > 1:
            return dict(possible=possible, proven=[], ambiguous=True)
    return dict(possible=possible, proven=proven,
                ambiguous=not bool(proven))


def _support(observations, excluded_evidence=()):
    """Return total and independent timestamp support for one spelling."""
    excluded = set(excluded_evidence)
    timestamps = {item['timestamp'] for item in observations}
    independent = {item['timestamp'] for item in observations
                   if item['evidence'] not in excluded}
    return len(timestamps), len(independent)


def _circle_variant(left, right):
    """Two names that differ only by the circle glyphs and the spaces around them."""
    strip = lambda name: ''.join(ch for ch in str(name) if ch not in '○◎' and not ch.isspace()).casefold()
    return left != right and strip(left) == strip(right) and bool(strip(left))


def _punctuation_variant(left, right):
    """Two names that differ only in punctuation and spaces."""
    strip = lambda name: ''.join(ch for ch in str(name) if ch.isalnum()).casefold()
    return left != right and strip(left) == strip(right) and bool(strip(left))


def _bounded_misread(variant, accepted):
    """True when a losing spelling is the accepted name within the receipt-fragment bound."""
    from .mechanics_audit import fragment_of
    return variant != accepted and fragment_of(str(variant), [str(accepted)]) is not None


def _append_candidate(event, effect, reason):
    field_evidence = event.get('field_evidence', {})
    key = _field(effect)
    for candidate in event.setdefault('ambiguous_effect_candidates', []):
        candidate_effect = candidate.get('effect') if isinstance(candidate, dict) else None
        if isinstance(candidate_effect, dict) and _field(candidate_effect) == key:
            return
    event['ambiguous_effect_candidates'].append(dict(
        effect=deepcopy(effect), reason=reason,
        evidence=list(field_evidence.get(key, []))))


def _conflict(event, left, right, relation, reason, accepted=None):
    names = [left.get('name'), right.get('name')]
    for effect in (left, right):
        detail = dict(
            field=_field(effect), reason=reason, name_candidates=names,
            evidence_pairs=[item['evidence_pair'] for item in relation.get('proven', [])],
            possible_evidence_pairs=[item['evidence_pair'] for item in relation.get('possible', [])],
            continuity=deepcopy(relation.get('proven', [None])[0])
            if relation.get('proven') else None)
        if accepted is not None:
            detail['accepted_name'] = accepted
            # The disagreement remains auditable, but it is no longer an
            # active unresolved conflict after a repeated source name was
            # retained.  Keep the geometry and both evidence sets intact.
            event.setdefault('resolved_reading_conflict_details', []).append(detail)
        else:
            event.setdefault('conflicting_readings', []).append(detail)


def resolve(event, rows_by_evidence):
    """Apply source-backed identity handling to inheritance spark effects.

    The event is mutated in place.  Name candidates and ``field_evidence`` are
    retained even when an effect is moved to the ambiguity list.  The function
    intentionally returns ``None`` to match the existing receipt passes.
    """
    if not isinstance(event, dict) or not isinstance(rows_by_evidence, dict):
        return
    effects = [effect for effect in event.get('effects', [])
               if isinstance(effect, dict)
               and effect.get('kind') == 'inheritance_spark'
               and isinstance(effect.get('name'), str) and effect.get('name')]
    field_evidence = event.get('field_evidence', {})
    if len(effects) < 2 or not isinstance(field_evidence, dict):
        return

    # Source adjacency is shared by every candidate pair. Avoid rescanning the
    # entire recording for each pair of spark observations.
    timestamps = sorted({row['source_timestamp_ms'] for row in rows_by_evidence.values()
                         if isinstance(row, dict) and type(row.get('source_timestamp_ms')) is int})
    source_successors = dict(zip(timestamps, timestamps[1:]))

    observations = {id(effect): _observations(effect, field_evidence, rows_by_evidence)
                    for effect in effects}
    relations = []
    for index, left in enumerate(effects):
        for right in effects[index + 1:]:
            if left.get('name') == right.get('name') or _payload(left) != _payload(right):
                continue
            relation = _relation(event, left, right, observations, source_successors)
            if relation is not None:
                relations.append((left, right, relation))
    if not relations:
        return

    # Build possible-slot components.  A component with multiple candidate
    # names is resolved only when one name has independent repeated support and
    # every relation used for that component has a proven source track.
    parent = {id(effect): id(effect) for effect in effects}

    def root(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def join(left, right):
        first, second = root(id(left)), root(id(right))
        if first != second:
            parent[second] = first

    for left, right, _ in relations:
        join(left, right)
    components = {}
    for effect in effects:
        components.setdefault(root(id(effect)), []).append(effect)

    remove = set()
    for component in components.values():
        edges = [(left, right, relation) for left, right, relation in relations
                 if left in component and right in component]
        if not edges:
            continue
        if any(not relation.get('proven') or relation.get('ambiguous') for _, _, relation in edges):
            # The geometry made the names plausible alternatives, but no exact
            # anchor proved the identity. Preserve both as auditable candidates.
            for left, right, relation in edges:
                _conflict(event, left, right, relation,
                          'inheritance_spark_identity_unproven')
            for effect in component:
                _append_candidate(event, effect, 'unresolved_inheritance_spark_identity')
                remove.add(id(effect))
            continue

        # Use source repetition, never spelling shape or confidence, to retain
        # one independently supported name.  Evidence from the relation itself
        # does not count as independent support for that candidate.
        relation_evidence = {proof for _, _, relation in edges
                             for item in relation.get('proven', [])
                             for proof in item['evidence_pair']}
        supports = {
            id(effect): _support(observations[id(effect)], relation_evidence)
            for effect in component
        }
        # The relation itself supplies the conflict pair.  Keeping a spelling
        # requires at least two distinct source timestamps and one observation
        # outside that pair; this is independent repetition, not confidence or
        # a string-shape preference.
        winners = [effect for effect in component
                   if supports[id(effect)][0] >= 2 and supports[id(effect)][1] >= 1]
        if len(winners) > 1 and all(_punctuation_variant(winners[0]['name'], other['name']) for other in winners[1:]):
            # Two supported spellings that differ only in punctuation ("TS
            # Climax Scenario" and "T'S Climax Scenario") are one name read
            # two ways; the spelling read on more frames stands, the longer
            # one when they tie.
            winners.sort(key=lambda effect: (-supports[id(effect)][0], -len(effect['name'])))
            for other in winners[1:]:
                winners[0].setdefault('punctuation_variants', []).append(other['name'])
            winners = winners[:1]
        if len(winners) != 1 and all(_circle_variant(component[0]['name'], other['name']) for other in component[1:]):
            # One slot read with and without its circle glyph, neither
            # spelling repeated: the glyph is only ever dropped, never added,
            # so the spelling that shows it is the spark.
            circled = [effect for effect in component if any(ch in '○◎' for ch in str(effect['name']))]
            if len(circled) == 1:
                winners = circled
        if len(winners) != 1:
            for left, right, relation in edges:
                _conflict(event, left, right, relation,
                          'inheritance_spark_identity_unresolved')
            for effect in component:
                _append_candidate(event, effect, 'unresolved_inheritance_spark_identity')
                remove.add(id(effect))
            continue

        winner = winners[0]
        winner.setdefault('observed_name_candidates', [winner['name']])
        if winner['name'] not in winner['observed_name_candidates']:
            winner['observed_name_candidates'].insert(0, winner['name'])
        alternatives = [effect['name'] for effect in component
                        if effect is not winner and effect['name'] not in winner['observed_name_candidates']]
        winner['observed_name_candidates'].extend(alternatives)
        winner['name_resolution'] = 'same_receipt_repeated_source_name'
        for left, right, relation in edges:
            _conflict(event, left, right, relation,
                      'inheritance_spark_identity_changes_in_tracked_receipt',
                      accepted=winner['name'])
        for effect in component:
            if effect is winner:
                continue
            if _circle_variant(effect['name'], winner['name']):
                # The same tracked slot read with and without the circle
                # glyph: one spark, its glyph unread on some frames.
                winner['name_resolution'] = 'same_slot_circle_glyph_unread'
                winner.setdefault('circle_glyph_variants', []).append(effect['name'])
                remove.add(id(effect))
                continue
            if effect['name'] in winner.get('punctuation_variants', []):
                remove.add(id(effect))
                continue
            if _bounded_misread(effect['name'], winner['name']):
                # The same tracked slot read with a glyph or two wrong (a
                # popup floating over a letter): the spark, misread on the
                # frames that lost to the repeated spelling. The same bound
                # folds a receipt line into the receipt it repeats.
                winner.setdefault('misread_variants', []).append(effect['name'])
                remove.add(id(effect))
                continue
            _append_candidate(event, effect, 'alternate_inheritance_spark_identity')
            remove.add(id(effect))

        event.setdefault('resolved_reading_conflicts', []).append(dict(
            field=_field(winner), observed_names=[effect['name'] for effect in component],
            accepted_name=winner['name'],
            evidence_pairs=[item['evidence_pair'] for _, _, relation in edges
                            for item in relation.get('proven', [])],
            basis='repeated_exact_name_observations_plus_source_tracked_slot'))

    if remove:
        event['effects'] = [effect for effect in event.get('effects', [])
                            if id(effect) not in remove]
    _repair_fixed_spark_names(event)


_FIXED_SPARK_NAMES = ('Speed', 'Stamina', 'Power', 'Guts', 'Wit', 'Turf', 'Dirt', 'Sprint', 'Mile',
                      'Medium', 'Long', 'Front Runner', 'Pace Chaser', 'Late Surger', 'End Closer')


def _fixed_spark_name(name):
    """The fixed stat or aptitude spark name one glyph from this spelling, or None."""
    from .receipt_grammar import fixed_word
    if not isinstance(name, str) or name in _FIXED_SPARK_NAMES:
        return None
    matches = [fixed for fixed in _FIXED_SPARK_NAMES if fixed_word(name, fixed)]
    return matches[0] if len(matches) == 1 else None


def _repair_fixed_spark_names(event):
    """A stat or aptitude spark read a glyph off ("Speud") is that fixed name.

    Repetition decides between spellings of a skill the run may not know,
    but the stat and aptitude sparks are fixed UI words: a spelling within
    one glyph of exactly one of them is that word however many frames read
    it, the read spelling kept beside it.
    """
    for effect in event.get('effects', []):
        if not isinstance(effect, dict) or effect.get('kind') != 'inheritance_spark':
            continue
        fixed = _fixed_spark_name(effect.get('name'))
        if fixed is None:
            continue
        candidates = effect.setdefault('observed_name_candidates', [effect['name']])
        if effect['name'] not in candidates:
            candidates.insert(0, effect['name'])
        key, fixed_key = _field(effect), 'inheritance_spark||' + fixed
        evidence = event.get('field_evidence')
        if isinstance(evidence, dict) and key in evidence and fixed_key not in evidence:
            evidence[fixed_key] = evidence.pop(key)
        effect['name'] = fixed
        effect['name_resolution'] = 'fixed_spark_name_repaired'
        # A candidate that read the fixed name outright was this spark.
        candidates_list = event.get('ambiguous_effect_candidates')
        if isinstance(candidates_list, list):
            event['ambiguous_effect_candidates'] = [
                c for c in candidates_list
                if not (isinstance(c, dict) and isinstance(c.get('effect'), dict)
                        and c['effect'].get('kind') == 'inheritance_spark' and c['effect'].get('name') == fixed)]
