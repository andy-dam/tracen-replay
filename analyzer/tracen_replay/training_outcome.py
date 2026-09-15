"""Explicit training-result banners and their source-supported outcomes."""
from copy import deepcopy
from pathlib import PureWindowsPath
import re
from typing import Any, Mapping


SOURCE_BOUND_BASIS = 'source_bound_same_frame_crop_consensus'
SOURCE_BOUND_REGION = 'weak_state_recovery.training_result_banner'
CLIPPED_SUCCESS_BASIS = 'training_result_banner_final_glyph_clipped'
_SHA256 = re.compile(r'^[0-9a-fA-F]{64}$')


def _exact_banner_value(text):
    """Parse only the literal result word used by the result banner."""

    if not isinstance(text, str):
        return None
    normalized = text.strip().upper()
    if normalized in ('SUCCESS', 'SUCCESS!'):
        return 'success'
    if normalized in ('FAILURE', 'FAILURE!'):
        return 'failure'
    return None


def _banner_letters(text):
    return re.sub(r'[^A-Za-z]', '', str(text)).upper()


def _banner_edit_distance(left, right, limit=2):
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        row_min = current[0]
        for right_index, right_char in enumerate(right, start=1):
            value = min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_char != right_char),
            )
            current.append(value)
            row_min = min(row_min, value)
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _valid_sha256(value):
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _valid_source_box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    if not all(type(item) in (int, float) for item in value):
        return False
    left, top, right, bottom = [float(item) for item in value]
    return (148 <= left < right <= 958 and 0 <= top < bottom <= 1080)


def _safe_source_path(value):
    """Return a canonical relative evidence path, or ``None``.

    Source-bound facts are consumed after a sidecar has been validated, but
    the applied metadata is still ordinary mutable data.  Keep this reader
    strict so a caller cannot turn a copied proof into a path alias or a
    namespace escape before it reaches the transaction summary.
    """

    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if any(character in value for character in ('?', '#', '\x00')):
        return None
    if any(ord(character) < 32 for character in value):
        return None
    path = PureWindowsPath(value)
    if path.is_absolute() or path.drive or '..' in path.parts:
        return None
    normalized = path.as_posix().casefold()
    return normalized if normalized and normalized not in ('.', '..') else None


def _safe_source_id(value):
    """Accept a frame identifier without allowing it to act as a path."""

    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if len(value) > 256 or value in ('.', '..'):
        return False
    return not any(character in value for character in ('/', '\\', '?', '#', '\x00')) \
        and all(ord(character) >= 32 for character in value)


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if value == value and value not in (float('inf'), float('-inf')) else None


def _valid_banner_geometry(value):
    if not _valid_source_box(value):
        return False
    left, top, right, bottom = [float(item) for item in value]
    width, height = right - left, bottom - top
    return (250 <= (left + right) / 2 <= 850
            and 635 <= (top + bottom) / 2 <= 815
            and 40 <= width <= 600 and 20 <= height <= 180)


def _training_result_scaffold(lines):
    """Require the visible result panel around a clipped SUCCESS prefix.

    ``SUCCES`` is a prefix produced while the final animated glyph is still
    clipped.  It is useful evidence only when the same OCR frame also shows
    the training result's own header, controls, and stat-card layout.  The
    scaffold keeps an arbitrary word or a positive gain from becoming a
    result outcome.
    """

    if not isinstance(lines, list):
        return False

    def eligible_line(line, *, minimum_confidence=90):
        return (isinstance(line, Mapping)
                and _finite_number(line.get('confidence')) is not None
                and line.get('confidence', 0) >= minimum_confidence
                and _valid_source_box(line.get('box')))

    header = False
    controls = set()
    labels = set()
    totals = 0
    for line in lines:
        if not eligible_line(line):
            continue
        text = re.sub(r'\s+', ' ', str(line.get('text', '')).strip())
        box = [float(value) for value in line['box']]
        left, top, right, bottom = box
        center_y = (top + bottom) / 2
        if text.casefold() == 'training' and top < 120:
            header = True
        if (text.casefold() in ('skip', 'quick')
                and 960 <= center_y <= 1080
                and 300 <= (left + right) / 2 <= 850):
            controls.add(text.casefold())
        if (text.casefold() in ('speed', 'stamina', 'power', 'guts', 'wit', 'skill pts')
                and 760 <= center_y <= 1030
                and 260 <= (left + right) / 2 <= 850):
            labels.add(text.casefold())
        compact = re.sub(r'\s+', '', text)
        if (re.fullmatch(r'[A-Za-z]?\d{1,4}/\d{3,4}', compact)
                and 760 <= center_y <= 1030
                and 260 <= (left + right) / 2 <= 850):
            totals += 1
    return header and controls == {'skip', 'quick'} and len(labels) >= 2 and totals >= 2


def training_result_scaffold_visible(lines):
    """Public view of the same-frame result scaffold check."""
    return _training_result_scaffold(lines)


def _clipped_success_line(line):
    """Return a normalized proof for the one-glyph-clipped SUCCESS prefix."""

    if not isinstance(line, Mapping):
        return None
    text = str(line.get('text', '')).strip().upper()
    if text not in ('SUCCES', 'SUCCES!'):
        return None
    confidence = _finite_number(line.get('confidence'))
    if confidence is None or confidence < 97:
        return None
    box = line.get('box')
    if not _valid_banner_geometry(box):
        return None
    left, top, right, bottom = [float(value) for value in box]
    if not (300 <= (left + right) / 2 <= 800
            and 600 <= (top + bottom) / 2 <= 790
            and right > left and bottom - top >= 40):
        return None
    observed = deepcopy(dict(line))
    observed.update(
        parsed_value='SUCCESS',
        clipped_final_glyph='S',
        observation_basis=CLIPPED_SUCCESS_BASIS,
    )
    return observed


def _same_box(value, expected):
    return isinstance(value, (list, tuple)) and list(value) == list(expected)


def _verified_source_status(value, source_hash, source_evidence):
    """Validate the nested verified-source record copied into applied facts."""

    if not isinstance(value, dict) or set(value) != {'status', 'sha256', 'path'}:
        return False
    return (value.get('status') == 'verified'
            and _valid_sha256(source_hash)
            and value.get('sha256') == source_hash
            and isinstance(source_evidence, str)
            and value.get('path') == source_evidence
            and _safe_source_path(source_evidence) is not None)


def _source_bound_recovery(observation):
    """Read and validate the applied exact-banner proof.

    The return value contains only the trusted fields needed to create normal
    training facts.  Returning ``None`` is deliberate: an unbound, preview,
    conflicting, or mutated proof must remain unknown to downstream code.
    """

    if not isinstance(observation, Mapping):
        return None
    if observation.get('screen') not in (None, 'training_result'):
        return None
    recovery = observation.get('weak_state_recovery')
    if not isinstance(recovery, Mapping):
        return None
    if (not isinstance(observation.get('header'), str)
            or observation['header'].strip().casefold() != 'training'):
        return None
    if (observation.get('result_grid') is not True
            or observation.get('current_grid') is not False):
        return None
    if (recovery.get('version') != 1
            or recovery.get('schema_version') != 'tracen-replay/weak-state-recovery-v1'
            or recovery.get('stage') != 'weak_state_recovery'):
        return None
    if recovery.get('independent_observations') is not False:
        return None
    source_id = recovery.get('source_frame_id')
    timestamp = recovery.get('source_timestamp_ms')
    evidence = recovery.get('evidence')
    source_hash = recovery.get('source_frame_sha256')
    gameplay_hash = recovery.get('gameplay_sha256')
    raw_hash = recovery.get('raw_sha256')
    if (not _safe_source_id(source_id) or type(timestamp) is not int or timestamp < 0
            or _safe_source_path(evidence) is None
            or not _valid_sha256(source_hash)
            or not _valid_sha256(gameplay_hash)
            or not _valid_sha256(raw_hash)):
        return None
    proofs = recovery.get('source_bound_observations')
    if not isinstance(proofs, Mapping):
        return None
    proof = proofs.get(SOURCE_BOUND_REGION)
    if not isinstance(proof, Mapping):
        return None
    if (proof.get('request_id') != 'training-result-banner'
            or proof.get('region') != SOURCE_BOUND_REGION
            or proof.get('kind') != 'training_result_banner'
            or proof.get('field') != 'outcome'
            or proof.get('basis') != SOURCE_BOUND_BASIS
            or proof.get('source_frame_id') != source_id
            or type(proof.get('source_timestamp_ms')) is not int
            or proof.get('source_timestamp_ms') != timestamp
            or proof.get('evidence') != evidence
            or proof.get('source_frame_sha256') != source_hash
            or proof.get('gameplay_sha256') != gameplay_hash
            or proof.get('raw_sha256') != raw_hash):
        return None
    source_evidence = proof.get('source_frame_evidence')
    if (_safe_source_path(source_evidence) is None
            or not _verified_source_status(recovery.get('source_frame_verification'),
                                           source_hash, source_evidence)
            or proof.get('source_frame_verification') != recovery.get('source_frame_verification')
            or not _verified_source_status(proof.get('source_frame_verification'),
                                           source_hash, source_evidence)):
        return None
    minimum_confidence = _finite_number(proof.get('minimum_confidence'))
    minimum_consensus = proof.get('minimum_consensus')
    if (minimum_confidence != 90.0 or type(minimum_consensus) is not int
            or not 2 <= minimum_consensus <= 5
            or not _valid_banner_geometry(proof.get('box'))):
        return None
    box = list(proof['box'])
    selected = proof.get('selected')
    views = proof.get('views')
    if not isinstance(selected, Mapping) or not isinstance(views, list):
        return None
    selected_value = _exact_banner_value(selected.get('text'))
    if (selected_value not in ('success', 'failure')
            or selected.get('parsed_value') != selected_value.upper()
            or selected.get('eligible') is not True):
        return None
    selected_confidence = _finite_number(selected.get('confidence'))
    if (selected_confidence is None or selected_confidence < minimum_confidence
            or selected_confidence < 90.0
            or ('box' in selected and not _same_box(selected.get('box'), box))):
        return None
    variants = set()
    eligible = []
    for view in views:
        if not isinstance(view, Mapping):
            return None
        variant = view.get('variant')
        if (not isinstance(variant, str) or not variant.strip()
                or variant != variant.strip() or variant in variants):
            return None
        variants.add(variant)
        confidence = _finite_number(view.get('confidence'))
        if confidence is None or view.get('eligible') not in (True, False):
            return None
        if 'box' in view and not _same_box(view.get('box'), box):
            return None
        outcome = _exact_banner_value(view.get('text'))
        if outcome in ('success', 'failure') and outcome != selected_value:
            return None
        if view.get('eligible') is True:
            if (outcome != selected_value
                    or view.get('parsed_value') != selected_value.upper()
                    or confidence < minimum_confidence or confidence < 90.0):
                return None
            eligible.append(view)
        elif outcome in ('success', 'failure') and view.get('parsed_value') not in (None, outcome.upper()):
            return None
    if len(eligible) < minimum_consensus:
        return None
    selected_matches = [view for view in eligible
                        if view.get('variant') == selected.get('variant')
                        and view.get('text') == selected.get('text')
                        and view.get('parsed_value') == selected.get('parsed_value')]
    if len(selected_matches) != 1 or selected_matches[0].get('confidence') != selected_confidence:
        return None
    lines = observation.get('lines')
    if not isinstance(lines, list):
        return None
    raw_match = any(
        isinstance(line, Mapping)
        and line.get('input_eligible') is not False
        and _same_box(line.get('box'), box)
        and _banner_edit_distance(
            _banner_letters(line.get('text')), selected_value.upper(), limit=2) <= 2
        for line in lines
    )
    if not raw_match:
        return None
    regions = observation.get('regions')
    if not isinstance(regions, Mapping):
        return None
    region = regions.get(SOURCE_BOUND_REGION)
    if not isinstance(region, Mapping):
        return None
    region_confidence = _finite_number(region.get('confidence'))
    if (not _same_box(region.get('box'), box)
            or region.get('source_request_id') != 'training-result-banner'
            or region.get('source_variant') != selected.get('variant')
            or region.get('geometry_basis') != 'same_frame_training_result_banner_geometry'
            or region.get('input_eligible') is not True
            or _exact_banner_value(region.get('text')) != selected_value
            or region.get('parsed_value') != selected_value.upper()
            or region_confidence != selected_confidence):
        return None
    status = selected_value
    return {
        'outcome': status,
        'source_frame_id': source_id,
        'source_timestamp_ms': timestamp,
        'evidence': evidence,
        'source_frame_evidence': source_evidence,
        'source_frame_sha256': source_hash,
        'gameplay_sha256': gameplay_hash,
        'raw_sha256': raw_hash,
        'box': box,
        'minimum_confidence': minimum_confidence,
        'minimum_consensus': minimum_consensus,
        'selected': deepcopy(dict(selected)),
        'views': deepcopy([dict(view) for view in views]),
        'source_frame_verification': deepcopy(dict(proof['source_frame_verification'])),
        'proof': deepcopy(dict(proof)),
    }


def source_bound_banner_facts(observation, screen=None):
    """Promote one validated same-frame result crop into normal outcome facts.

    ``apply`` and ``load`` intentionally keep the result word observational;
    this explicit hook is the semantic boundary used by the transaction
    pipeline.  It emits one physical source observation even when several
    preprocessing variants agree.  Preview screens, fuzzy reads, missing
    source bindings, and conflicting/duplicated views return an empty mapping.
    """

    if screen is None:
        screen = observation.get('screen') if isinstance(observation, Mapping) else None
    if screen != 'training_result':
        return {}
    proof = _source_bound_recovery(observation)
    if proof is None:
        return {}
    status = proof['outcome']
    selected = deepcopy(proof['selected'])
    one_observation = {
        'outcome': status,
        'source_timestamp_ms': proof['source_timestamp_ms'],
        'evidence': proof['evidence'],
        'banner': [deepcopy(selected)],
        'basis': SOURCE_BOUND_BASIS,
        'exact_word': True,
        'source_frame_id': proof['source_frame_id'],
        'source_frame_sha256': proof['source_frame_sha256'],
        'gameplay_sha256': proof['gameplay_sha256'],
        'physical_source_count': 1,
        'source_bound_proof': deepcopy(proof['proof']),
    }
    bound = {
        'outcome': status,
        'basis': SOURCE_BOUND_BASIS,
        'source_frame_id': proof['source_frame_id'],
        'source_timestamp_ms': proof['source_timestamp_ms'],
        'evidence': proof['evidence'],
        'source_frame_evidence': proof['source_frame_evidence'],
        'source_frame_sha256': proof['source_frame_sha256'],
        'gameplay_sha256': proof['gameplay_sha256'],
        'raw_sha256': proof['raw_sha256'],
        'box': proof['box'],
        'minimum_confidence': proof['minimum_confidence'],
        'minimum_consensus': proof['minimum_consensus'],
        'selected': deepcopy(proof['selected']),
        'views': deepcopy(proof['views']),
        'source_frame_verification': deepcopy(proof['source_frame_verification']),
        'physical_source_count': 1,
        'observation': deepcopy(one_observation),
    }
    return {
        'training_outcome': status,
        status + '_banner': [selected],
        'outcome_observations': [one_observation],
        'source_bound_training_outcome': bound,
    }


def _valid_bound_views(selected, views, box, minimum_confidence,
                       minimum_consensus, status):
    """Validate the compact view copy carried into normal parsed facts."""

    if not isinstance(selected, Mapping) or not isinstance(views, list):
        return False
    selected_value = _exact_banner_value(selected.get('text'))
    if (selected_value != status
            or selected.get('parsed_value') != status.upper()
            or selected.get('eligible') is not True):
        return False
    selected_confidence = _finite_number(selected.get('confidence'))
    if (selected_confidence is None or selected_confidence < minimum_confidence
            or selected_confidence < 90.0
            or ('box' in selected and not _same_box(selected.get('box'), box))):
        return False
    variants = set()
    eligible = []
    for view in views:
        if not isinstance(view, Mapping):
            return False
        variant = view.get('variant')
        if (not isinstance(variant, str) or not variant.strip()
                or variant != variant.strip() or variant in variants):
            return False
        variants.add(variant)
        confidence = _finite_number(view.get('confidence'))
        if confidence is None or view.get('eligible') not in (True, False):
            return False
        if 'box' in view and not _same_box(view.get('box'), box):
            return False
        outcome = _exact_banner_value(view.get('text'))
        if outcome in ('success', 'failure') and outcome != status:
            return False
        if view.get('eligible') is True:
            if (outcome != status
                    or view.get('parsed_value') != status.upper()
                    or confidence < minimum_confidence or confidence < 90.0):
                return False
            eligible.append(view)
        elif outcome in ('success', 'failure') and view.get('parsed_value') not in (None, outcome.upper()):
            return False
    if len(eligible) < minimum_consensus:
        return False
    matches = [view for view in eligible
               if view.get('variant') == selected.get('variant')
               and view.get('text') == selected.get('text')
               and view.get('parsed_value') == selected.get('parsed_value')]
    return len(matches) == 1 and matches[0].get('confidence') == selected_confidence


def _source_bound_summary_observation(facts, row, status):
    """Validate one source-bound marker before ``summarize`` accepts it."""

    bound = facts.get('source_bound_training_outcome') if isinstance(facts, Mapping) else None
    if not isinstance(bound, Mapping) or bound.get('outcome') != status:
        return None
    if (bound.get('basis') != SOURCE_BOUND_BASIS
            or type(bound.get('physical_source_count')) is not int
            or bound.get('physical_source_count') != 1):
        return None
    source_id = bound.get('source_frame_id')
    timestamp = bound.get('source_timestamp_ms')
    evidence = bound.get('evidence')
    source_evidence = bound.get('source_frame_evidence')
    source_hash = bound.get('source_frame_sha256')
    gameplay_hash = bound.get('gameplay_sha256')
    raw_hash = bound.get('raw_sha256')
    if (not _safe_source_id(source_id) or type(timestamp) is not int or timestamp < 0
            or _safe_source_path(evidence) is None
            or _safe_source_path(source_evidence) is None
            or not _valid_sha256(source_hash)
            or not _valid_sha256(gameplay_hash)
            or not _valid_sha256(raw_hash)
            or not _verified_source_status(bound.get('source_frame_verification'),
                                           source_hash, source_evidence)):
        return None
    minimum_confidence = _finite_number(bound.get('minimum_confidence'))
    minimum_consensus = bound.get('minimum_consensus')
    box = bound.get('box')
    if (minimum_confidence != 90.0 or type(minimum_consensus) is not int
            or not 2 <= minimum_consensus <= 5
            or not _valid_banner_geometry(box)
            or not _valid_bound_views(bound.get('selected'), bound.get('views'), box,
                                      minimum_confidence, minimum_consensus, status)):
        return None
    identity = _source_identity(row)
    if identity is None or identity[0] != timestamp or identity[1] != _safe_source_path(evidence):
        return None
    for key, expected in (('source_frame_id', source_id),
                          ('source_frame_sha256', source_hash),
                          ('gameplay_sha256', gameplay_hash)):
        if key in row and row.get(key) is not None and row.get(key) != expected:
            return None
    if 'source_frame_evidence' in row and row.get('source_frame_evidence') is not None:
        if _safe_source_path(row.get('source_frame_evidence')) != _safe_source_path(source_evidence):
            return None
    observation = bound.get('observation')
    if not isinstance(observation, Mapping):
        return None
    if (observation.get('outcome') != status
            or observation.get('basis') != SOURCE_BOUND_BASIS
            or observation.get('exact_word') is not True
            or type(observation.get('physical_source_count')) is not int
            or observation.get('physical_source_count') != 1
            or observation.get('source_frame_id') != source_id
            or observation.get('source_timestamp_ms') != timestamp
            or observation.get('evidence') != evidence
            or observation.get('source_frame_sha256') != source_hash
            or observation.get('gameplay_sha256') != gameplay_hash
            or observation.get('banner') != [bound.get('selected')]):
        return None
    proof = observation.get('source_bound_proof')
    if (not isinstance(proof, Mapping)
            or proof.get('basis') != SOURCE_BOUND_BASIS
            or proof.get('selected') != bound.get('selected')
            or proof.get('views') != bound.get('views')
            or proof.get('box') != box
            or proof.get('source_frame_id') != source_id
            or proof.get('source_timestamp_ms') != timestamp
            or proof.get('evidence') != evidence
            or proof.get('source_frame_sha256') != source_hash
            or proof.get('gameplay_sha256') != gameplay_hash
            or proof.get('raw_sha256') != raw_hash):
        return None
    return deepcopy(dict(observation))


def classify_result_screen(lines, header, screen):
    """A complete result banner can identify a frame whose stat grid faded.

    This only resolves uncertain screen classes. A preview failure-rate label
    cannot qualify as the large, exact result word in the animation region.
    """
    if (screen not in ('unknown', 'training_result_candidate')
            or not isinstance(header, str)
            or header.strip().casefold() != 'training'):
        return screen
    eligible = []
    for line in lines:
        box = line.get('box', [])
        if (len(box) == 4 and all(type(value) in (int, float) for value in box)
                and 300 <= box[0] < box[2] <= 800
                and 600 <= box[1] < box[3] <= 800
                and 40 <= box[3] - box[1] <= 160):
            eligible.append(line)
    # Keep the banner candidate geometry filter above, while giving clipped
    # prefixes access to the complete same-frame result scaffold.
    facts = banner_facts(eligible, 'training_result', context_lines=lines)
    return ('training_result' if facts.get('training_outcome') in ('success', 'failure')
            else screen)


def _source_identity(row):
    evidence = row.get('evidence')
    timestamp = row.get('source_timestamp_ms')
    if (type(timestamp) is not int or timestamp < 0
            or not isinstance(evidence, str) or not evidence
            or any(character in evidence for character in ('?', '#', '\x00'))):
        return None
    path = PureWindowsPath(evidence)
    if path.is_absolute() or path.drive or '..' in path.parts:
        return None
    digest = row.get('source_frame_sha256')
    if digest is not None and (not isinstance(digest, str)
                               or re.fullmatch(r'[0-9a-fA-F]{64}', digest) is None):
        return None
    return timestamp, path.as_posix().casefold(), digest.lower() if digest else None


def banner_facts(lines, screen, *, context_lines=None):
    """Recognize the large result banner, never a preview failure percentage."""
    if screen != 'training_result':
        return {}
    found = {'success': [], 'failure': []}
    candidates = []
    clipped_success = []
    for line in lines:
        text = line.get('text', '').strip().upper()
        word = text.rstrip('!')
        outcome = {'SUCCESS': 'success', 'SUCCESS!': 'success',
                   'FAILURE': 'failure', 'FAILURE!': 'failure'}.get(text)
        exact = outcome is not None
        if outcome is None:
            matches = [label.lower() for label in ('SUCCESS', 'FAILURE')
                       if len(word) == len(label)
                       and sum(a != b for a, b in zip(word, label)) == 1]
            outcome = matches[0] if len(matches) == 1 else None
        if outcome is None:
            clipped = _clipped_success_line(line)
            if clipped is not None:
                clipped_success.append(clipped)
            continue
        if line.get('confidence', 0) < 90:
            continue
        box = line.get('box', [])
        if len(box) != 4:
            continue
        left, top, right, bottom = box
        if not (300 <= (left + right) / 2 <= 800 and 600 <= (top + bottom) / 2 <= 790
                and right > left and bottom - top >= 40):
            continue
        if exact and line['confidence'] >= 97:
            found[outcome].append(deepcopy(line))
        elif not exact:
            candidates.append(dict(outcome=outcome, exact=exact, line=deepcopy(line)))
        else:
            candidates.append(dict(outcome=outcome, exact=exact, line=deepcopy(line)))

    # A clipped final ``S`` is accepted as one high-confidence observation
    # only when the same frame proves the result panel.  Exact failure or
    # fuzzy failure evidence wins over this positive prefix, preserving
    # conflicts instead of turning a failure screen into success.
    if (not found['success'] and not found['failure']
            and not any(candidate.get('outcome') == 'failure' for candidate in candidates)
            and len(clipped_success) == 1
            and _training_result_scaffold(lines if context_lines is None else context_lines)):
        found['success'] = clipped_success
    present = [kind for kind, rows in found.items() if rows]
    if not present:
        return {'training_outcome_candidates': candidates} if candidates else {}
    result = {'training_outcome': present[0] if len(present) == 1 else 'unknown'}
    result.update({kind + '_banner': rows for kind, rows in found.items() if rows})
    if candidates:
        result['training_outcome_candidates'] = candidates
    if len(present) > 1:
        result['training_outcome_conflicts'] = present
    return result


def summarize(rows):
    """Link banner outcomes only to the already established training group."""
    missing = object()
    observations = []
    proofs = {'success': [], 'failure': []}
    weak = {'success': [], 'failure': []}
    for row in rows:
        facts = row.get('facts', {})
        statuses = set(facts.get('training_outcome_conflicts', []))
        if facts.get('training_outcome') in proofs:
            statuses.add(facts['training_outcome'])
        source_bound = facts.get('source_bound_training_outcome', missing)
        if source_bound is not missing:
            # Source-bound facts are a closed semantic path.  Do not let a
            # malformed marker fall through to fuzzy corroboration, and do
            # not accept a marker alongside an opposing outcome conflict.
            bound_status = source_bound.get('outcome') if isinstance(source_bound, Mapping) else None
            if bound_status not in proofs or statuses != {bound_status}:
                continue
            bound_observation = _source_bound_summary_observation(facts, row, bound_status)
            if bound_observation is None:
                continue
            proofs[bound_status].append(row['evidence'])
            observations.append(bound_observation)
            continue
        for status in sorted(statuses & proofs.keys()):
            proofs[status].append(row['evidence'])
            observations.append({'outcome': status, 'source_timestamp_ms': row['source_timestamp_ms'],
                                 'evidence': row['evidence'], 'banner': deepcopy(facts.get(status + '_banner', []))})
        for candidate in facts.get('training_outcome_candidates', []):
            if candidate.get('outcome') in weak:
                weak[candidate['outcome']].append((row, candidate))
    for status, candidates in weak.items():
        # Two separately timed source frames, including one exact spelling,
        # can corroborate a weak fixed-word reading. Repeated OCR variants of
        # one frame cannot supply this proof, and no stat amount is consulted.
        unique = {}
        seen_hashes = set()
        for row, candidate in candidates:
            identity = _source_identity(row)
            if identity is None:
                continue
            timestamp, evidence, digest = identity
            if digest and digest in seen_hashes:
                continue
            if digest:
                seen_hashes.add(digest)
            unique.setdefault((timestamp, evidence), (row, candidate))
        accepted = list(unique.values())
        if (len({row['source_timestamp_ms'] for row, _ in accepted}) < 2
                or len({_source_identity(row)[1] for row, _ in accepted}) < 2
                or not any(c['exact'] for _, c in accepted)):
            continue
        for row, candidate in accepted:
            proofs[status].append(row['evidence'])
            observations.append(dict(outcome=status, source_timestamp_ms=row['source_timestamp_ms'],
                                     evidence=row['evidence'], banner=[deepcopy(candidate['line'])],
                                     basis='corroborated_fixed_result_word', exact_word=candidate['exact']))
    present = [kind for kind, paths in proofs.items() if paths]
    result = {'training_outcome': present[0] if len(present) == 1 else 'unknown',
              'outcome_observations': observations}
    result.update({kind + '_evidence': list(dict.fromkeys(paths)) for kind, paths in proofs.items() if paths})
    if len(present) > 1:
        result['training_outcome_conflicts'] = present
    return result
