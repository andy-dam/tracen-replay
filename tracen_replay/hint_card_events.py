"""Attach validated hint-card observations without reinterpreting source pixels.

The recognition loader owns image, model and capture validation. This stage
checks recording identity and correspondence to the supplied parsed rows. It
never invokes OCR, guesses a card name, or creates a new event boundary.
"""
from copy import deepcopy
import math
import re


def _field(effect):
    return 'skill_hint_change||'+effect['name']


def _logical_name(name):
    # OCR may render a rank as a separated O. This is used only to detect
    # conflicts, never to award a rank or to merge differently spelled names.
    return re.sub(r'(?:\s*[○◯◎]|\s+O)$', '', name).strip()


def _rank(name):
    if name.endswith('◎'):
        return 'double_circle'
    if name.endswith(('○', '◯')):
        return 'single_circle'
    return None


def _same_line(line, proof):
    if not isinstance(line, dict) or not isinstance(proof, dict):
        return False
    left, right = line.get('box'), proof.get('box')
    if (
            not isinstance(left, (list, tuple))
            or not isinstance(right, (list, tuple))
            or len(left) != 4
            or len(right) != 4
    ):
        return False
    return (
        line.get('text') == proof.get('text')
        and all(type(a) in (int, float) and type(b) in (int, float)
                and math.isfinite(float(a)) and math.isfinite(float(b))
                and float(a) == float(b)
                for a, b in zip(left, right))
    )


def _supported_legacy(candidate, rows, source_sha256):
    if (not isinstance(candidate, dict) or candidate.get('kind') != 'skill_hint_change'
            or candidate.get('source_sha256') != source_sha256
            or not isinstance(candidate.get('name'), str) or not candidate['name'].strip()
            or type(candidate.get('amount')) is not int or not 0 < candidate['amount'] <= 99):
        return None
    observations = candidate.get('observations')
    identity = candidate.get('identity_proof')
    if not isinstance(observations, list) or not isinstance(identity, dict):
        return None
    context = identity.get('same_context')
    if not isinstance(context, str) or not context:
        return None
    support = []
    seen_times = set()
    seen_paths = set()
    for observation in observations:
        if not isinstance(observation, dict):
            return None
        time, evidence = observation.get('timestamp_ms'), observation.get('evidence')
        if (type(time) is not int or time < 0 or not isinstance(evidence, str)
                or time in seen_times or evidence in seen_paths):
            return None
        seen_times.add(time)
        seen_paths.add(evidence)
        row = rows.get((time, evidence))
        card, receipt = observation.get('card'), observation.get('receipt')
        if (row is None or row.get('screen') != 'event_outcome'
                or (row.get('context_title') or row.get('context_title_candidate')) != context
                or not isinstance(card, dict) or not isinstance(receipt, dict)
                or card.get('text') != candidate['name']
                or receipt.get('amount') != candidate['amount']):
            return None
        lines = row.get('ocr', {}).get('neural', [])
        if sum(_same_line(line, card) for line in lines) != 1:
            return None
        if sum(_same_line(line, receipt) for line in lines) != 1:
            return None
        prefix = observation.get('prefix_amount_proof')
        if prefix is not None:
            if (not isinstance(prefix, dict) or type(prefix.get('amount')) is not int
                    or prefix['amount'] != candidate['amount']):
                return None
            support.append(observation)
    times = sorted(seen_times)
    if (len(support) < 2 or candidate.get('source_timestamps_ms') != times
            or any(b-a > 250 for a,b in zip(times,times[1:]))):
        return None
    # Even native samples between the supporting frames must keep the same
    # screen/context. A source-selected candidate cannot bridge another event.
    for (time, _), row in rows.items():
        if times[0] <= time <= times[-1] and (
                row.get('screen') != 'event_outcome'
                or (row.get('context_title') or row.get('context_title_candidate')) != context):
            return None
    suffix = identity.get('suffix')
    if suffix not in (None, 'single_circle', 'double_circle'):
        return None
    if suffix is not None and sum(o['card'].get('suffix') == suffix for o in observations) < 2:
        return None
    written = re.search(r'[○◯◎]$', candidate['name'])
    if written and suffix is not None and (written[0] == '◎') != (suffix == 'double_circle'):
        return None
    return context, times, seen_paths


_WRAPPED_BASIS = 'standalone_hint_card_with_wrapped_receipt_and_per_row_amount_ocr'
_WRAPPED_AMOUNT_BASIS = 'source_bound_amount_digit_crop_ocr'


def _wrapped_source_lines(row):
    """Return deduplicated neural and retained occlusion lines for one row."""
    # Keep this lookup shared with source preparation.  In particular, a
    # retained occlusion record can be the higher-confidence duplicate of the
    # neural line (the neural line may intentionally have confidence 0).  The
    # identity helper also carries overlay metadata forward instead of
    # treating the duplicate as a clear line.
    try:
        from .hint_card_identity import _wrapped_line_records
        return _wrapped_line_records(row)
    except (ImportError, OSError, TypeError, ValueError):
        return []


def _wrapped_row_context(row):
    values = []
    for key in ('context_title', 'context_title_candidate'):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    if values and len(set(values)) != 1:
        return False, None
    return True, values[0] if values else None


def _wrapped_relative_crop_box(receipt_box, left_offset, right_offset):
    """Return a wrapped amount crop derived from the saved receipt box."""
    if (not isinstance(receipt_box, (list, tuple)) or len(receipt_box) != 4
            or any(type(value) not in (int, float)
                   or not math.isfinite(float(value)) for value in receipt_box)):
        return None
    left, top, _right, bottom = (float(value) for value in receipt_box)
    crop = (left + left_offset, top - 2.0,
            left + right_offset, bottom + 2.0)
    if not (148.0 <= crop[0] < crop[2] <= 958.0
            and 0.0 <= crop[1] < crop[3] <= 1080.0):
        return None
    return crop


def _wrapped_identity_line_clear(line):
    """Require that a wrapped identity fragment is source-visible."""
    return (isinstance(line, dict)
            and line.get('overlay_occluded') is not True
            and not bool(line.get('overlay_boxes'))
            and line.get('recipient_name_occluded') is not True)


def _wrapped_identity_fragment_proof_supported(
    proof, card, receipt, receipt_parts, overlay
):
    """Validate the structure/geometry of a persisted fragment proof.

    Pixel hashes are rechecked by the source cache.  Direct event assembly
    still needs the same source-relative geometry and literal fragment rules
    so a caller cannot bypass them with a relocated or invented crop.
    """
    try:
        from .hint_card_identity import _wrapped_identity_fragment_proof_matches
    except (ImportError, OSError, TypeError, ValueError):
        return False
    return _wrapped_identity_fragment_proof_matches(
        proof,
        {
            'card': card,
            'receipt': receipt,
            'receipt_parts': receipt_parts,
            'overlay': overlay,
        },
    )


def _wrapped_amount_proof_supported(proof, amount, receipt_box):
    if not isinstance(proof, dict):
        return False
    confidence = proof.get('confidence')
    recognized = proof.get('recognized_text')
    crop_box = proof.get('crop_box')
    crop_values = None
    if isinstance(crop_box, (list, tuple)) and len(crop_box) == 4:
        if all(type(value) in (int, float) and math.isfinite(float(value))
               for value in crop_box):
            crop_values = tuple(float(value) for value in crop_box)
    if (
            type(proof.get('amount')) is not int
            or proof['amount'] != amount
            or not isinstance(recognized, str)
            or not re.fullmatch(r'\d{1,2}', recognized.strip())
            or int(recognized.strip()) != amount
            or type(confidence) not in (int, float)
            or not math.isfinite(float(confidence))
            or not 90.0 <= float(confidence) <= 100.0
            or proof.get('basis') != _WRAPPED_AMOUNT_BASIS
            or not isinstance(crop_box, (list, tuple))
            or len(crop_box) != 4
            or crop_values is None
            or crop_values != _wrapped_relative_crop_box(receipt_box, 79.0, 99.0)
            or not isinstance(proof.get('pixel_rgb_sha256'), str)
            or not re.fullmatch(r'[0-9a-fA-F]{64}', proof['pixel_rgb_sha256'])
            or not isinstance(proof.get('model_fingerprint'), str)
            or not re.fullmatch(r'[0-9a-fA-F]{64}', proof['model_fingerprint'])
    ):
        return False
    delimiter = proof.get('delimiter_proof')
    delimiter_box = delimiter.get('crop_box') if isinstance(delimiter, dict) else None
    delimiter_values = None
    if isinstance(delimiter_box, (list, tuple)) and len(delimiter_box) == 4:
        if all(type(value) in (int, float) and math.isfinite(float(value))
               for value in delimiter_box):
            delimiter_values = tuple(float(value) for value in delimiter_box)
    if (
            not isinstance(delimiter, dict)
            or not isinstance(delimiter.get('recognized_text'), str)
            or re.fullmatch(r'h\s*in(?:t)?', delimiter['recognized_text'].strip(), re.IGNORECASE) is None
            or type(delimiter.get('confidence')) not in (int, float)
            or not math.isfinite(float(delimiter['confidence']))
            or not 90.0 <= float(delimiter['confidence']) <= 100.0
            or delimiter.get('basis') != 'amount_digit_followed_by_hint_delimiter_ocr'
            or delimiter_values is None
            or delimiter_values != _wrapped_relative_crop_box(receipt_box, 94.0, 124.0)
            or not isinstance(delimiter.get('pixel_rgb_sha256'), str)
            or not re.fullmatch(r'[0-9a-fA-F]{64}', delimiter['pixel_rgb_sha256'])
            or delimiter.get('model_fingerprint') != proof.get('model_fingerprint')
    ):
        return False
    return True


def _wrapped_supported(candidate, rows, source_sha256):
    """Validate an already prepared wrapped candidate without OCR."""
    if (
            not isinstance(candidate, dict)
            or candidate.get('observation_kind') != 'wrapped_hint_receipt'
            or candidate.get('kind') != 'skill_hint_change'
            or candidate.get('source_sha256') != source_sha256
            or not isinstance(candidate.get('name'), str)
            or not candidate['name'].strip()
            or type(candidate.get('amount')) is not int
            or not 0 < candidate['amount'] <= 99
    ):
        return None
    observations = candidate.get('observations')
    identity = candidate.get('identity_proof')
    provenance = candidate.get('provenance')
    raw_names = candidate.get('raw_receipt_name_candidates')
    if (
            not isinstance(observations, list)
            or not isinstance(identity, dict)
            or not isinstance(provenance, dict)
            or not isinstance(raw_names, list)
            or len(observations) < 2
            or any(not isinstance(value, str) or not value.strip() for value in raw_names)
            or raw_names != sorted(set(raw_names))
            or identity.get('basis') != _WRAPPED_BASIS
            or identity.get('card_text') != candidate['name']
            or identity.get('rank_state') not in ('present', 'undetermined')
            or identity.get('geometry_stable') is not True
            or identity.get('distinct_timestamp_count') != len(observations)
            or type(identity.get('context_observed')) is not bool
            or (identity.get('context_observed') and
                (not isinstance(identity.get('same_context'), str)
                 or not identity.get('same_context')))
            or (not identity.get('context_observed') and identity.get('same_context') is not None)
            or provenance.get('independent_observations') is not False
            or provenance.get('multi_crop_not_counted_as_timestamp') is not True
            or provenance.get('source_evidence_type') != 'decoded_gameplay_png'
            or not isinstance(provenance.get('capture_manifest_sha256'), str)
            or not re.fullmatch(r'[0-9a-fA-F]{64}', provenance['capture_manifest_sha256'])
            or not isinstance(provenance.get('prefix_ocr_model_fingerprint'), str)
            or not re.fullmatch(r'[0-9a-fA-F]{64}', provenance['prefix_ocr_model_fingerprint'])
            or provenance.get('prefix_crop_count') != len(observations)
    ):
        return None
    rank_state = identity['rank_state']
    suffix = identity.get('suffix')
    if rank_state == 'present' and suffix not in ('single_circle', 'double_circle'):
        return None
    if rank_state == 'undetermined' and suffix is not None:
        return None
    written_rank = _rank(candidate['name'])
    if rank_state == 'undetermined' and written_rank is not None:
        return None
    if rank_state == 'present' and written_rank is not None and written_rank != suffix:
        return None
    context = identity.get('same_context')
    if context is not None and (not isinstance(context, str) or not context):
        return None
    seen_times = set()
    seen_paths = set()
    support = []
    observed_suffixes = set()
    for observation in observations:
        if not isinstance(observation, dict):
            return None
        time, evidence = observation.get('timestamp_ms'), observation.get('evidence')
        if (
                type(time) is not int or time < 0
                or not isinstance(evidence, str) or not evidence
                or time in seen_times or evidence in seen_paths
        ):
            return None
        seen_times.add(time)
        seen_paths.add(evidence)
        row = rows.get((time, evidence))
        card = observation.get('card')
        receipt = observation.get('receipt')
        parts = observation.get('receipt_parts')
        prefix = parts.get('prefix') if isinstance(parts, dict) else None
        continuation = parts.get('continuation') if isinstance(parts, dict) else None
        if (
                row is None
                or row.get('screen') != 'event_outcome'
                or not isinstance(card, dict)
                or not isinstance(receipt, dict)
                or not isinstance(parts, dict)
                or not isinstance(prefix, dict)
                or not isinstance(continuation, dict)
                or card.get('text') != candidate['name']
                or receipt.get('raw_name') != candidate['name']
                or receipt.get('amount') != candidate['amount']
                or receipt.get('text') != f"{prefix.get('text', '')} {continuation.get('text', '')}"
                or not _wrapped_amount_proof_supported(
                    observation.get('prefix_amount_proof'), candidate['amount'],
                    receipt.get('box')
                )
        ):
            return None
        if observation['prefix_amount_proof'].get('model_fingerprint') != provenance['prefix_ocr_model_fingerprint']:
            return None
        row_context_valid, row_context = _wrapped_row_context(row)
        if not row_context_valid or row_context != context:
            return None
        lines = _wrapped_source_lines(row)
        neural = row.get('ocr', {}).get('neural', [])
        if not isinstance(neural, list) or sum(_same_line(line, card) for line in neural) != 1:
            return None
        source_prefixes = [line for line in lines if _same_line(line, prefix)]
        source_continuations = [line for line in lines if _same_line(line, continuation)]
        source_cards = [line for line in neural if _same_line(line, card)]
        identity_fragment_proof = observation.get('identity_fragment_proof')
        prefix_requires_fragment_proof = (
            (
                not _wrapped_identity_line_clear(prefix)
                or (
                    len(source_prefixes) == 1
                    and not _wrapped_identity_line_clear(source_prefixes[0])
                )
            )
            or observation.get('overlay_box') is not None
        )
        if (
                len(source_cards) != 1
                or not _wrapped_identity_line_clear(card)
                or not _wrapped_identity_line_clear(source_cards[0])
                or len(source_prefixes) != 1
                or len(source_continuations) != 1
                or not _wrapped_identity_line_clear(source_continuations[0])
                or not _wrapped_identity_line_clear(continuation)
                or (
                    prefix_requires_fragment_proof
                    and not _wrapped_identity_fragment_proof_supported(
                        identity_fragment_proof,
                        card,
                        receipt,
                        parts,
                        observation.get('overlay_box'),
                    )
                )
                or (
                    not prefix_requires_fragment_proof
                    and identity_fragment_proof is not None
                )
        ):
            return None
        saved_suffix = card.get('suffix')
        suffix_state = card.get('suffix_state')
        if suffix_state not in ('present', 'undetermined'):
            return None
        if suffix_state == 'present' and saved_suffix not in ('single_circle', 'double_circle'):
            return None
        if suffix_state == 'undetermined' and saved_suffix is not None:
            return None
        if suffix_state == 'present':
            observed_suffixes.add(saved_suffix)
        if rank_state == 'present' and (suffix_state != 'present' or saved_suffix != suffix):
            return None
        support.append(observation)
    times = sorted(seen_times)
    if (
            candidate.get('source_timestamps_ms') != times
            or [observation.get('timestamp_ms') for observation in observations] != times
    ):
        return None
    if any(b - a > 250 for a, b in zip(times, times[1:])):
        return None
    if len(observed_suffixes) > 1:
        return None
    # Every observed row between the endpoints must stay on the outcome
    # screen.  Known context is checked exactly; unknown context remains
    # unknown and cannot be silently replaced with a guessed title.
    for (time, _), row in rows.items():
        if times[0] <= time <= times[-1]:
            valid, row_context = _wrapped_row_context(row)
            if not valid or row.get('screen') != 'event_outcome' or row_context != context:
                return None
    if len(support) != len(observations):
        return None
    return context, times, seen_paths


def _supported(candidate, rows, source_sha256):
    if isinstance(candidate, dict) and candidate.get('observation_kind') == 'wrapped_hint_receipt':
        return _wrapped_supported(candidate, rows, source_sha256)
    return _supported_legacy(candidate, rows, source_sha256)


def apply(events, readings, candidates, *, source_sha256):
    """Mutate matching outcome events; return an explicit integration audit.

Inputs must already have passed the source-bound recognition loader. An
otherwise valid observation that crosses an outcome boundary is retained as
unapplied. Existing hints with evidence outside the proven receipt episode are
not replaced. Raw readings and input candidates remain unchanged.
"""
    audit = {'accepted': [], 'rejected': []}
    if not isinstance(source_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', source_sha256):
        raise ValueError('Hint-card integration requires the report source SHA-256.')
    rows = {(row['source_timestamp_ms'], row['evidence']): row for row in readings}
    if len(rows) != len(readings):
        raise ValueError('Hint-card integration requires unique source observations.')
    prepared = []
    for index, candidate in enumerate(candidates):
        supported = _supported(candidate, rows, source_sha256)
        if supported is None:
            audit['rejected'].append({'index': index, 'reason': 'candidate_does_not_match_source_rows'})
            continue
        context, times, paths = supported
        if context is None:
            targets = [event for event in events if event.get('kind') == 'outcome'
                       and event['first_seen_ms'] <= times[0] <= times[-1] <= event['last_seen_ms']]
        else:
            targets = [event for event in events if event.get('kind') == 'outcome'
                       and event.get('context_title') == context
                       and event['first_seen_ms'] <= times[0] <= times[-1] <= event['last_seen_ms']]
        if len(targets) != 1:
            audit['rejected'].append({'index': index, 'reason': 'no_unique_existing_outcome'})
            continue
        event = targets[0]
        suffix = candidate['identity_proof'].get('suffix')
        name = candidate['name']
        if suffix is not None:
            name = re.sub(r'\s*[○◯◎]$', '', name) + {'single_circle': ' ○', 'double_circle': ' ◎'}[suffix]
        mode = 'wrapped' if candidate.get('observation_kind') == 'wrapped_hint_receipt' else 'legacy'
        prepared.append((index, candidate, event, paths, name, suffix, mode))
    # Inspect the whole batch before changing any event. Two individually
    # supported episodes can still disagree within one reconstructed outcome;
    # choosing whichever arrived first would hide that uncertainty.
    conflicts = set()
    groups = {}
    for index, candidate, event, paths, name, suffix, mode in prepared:
        groups.setdefault((id(event), _logical_name(name)), []).append(
            (index, candidate['amount'], suffix or _rank(name)))
    for group in groups.values():
        if len({amount for _, amount, _ in group}) > 1 or len({rank for _, _, rank in group if rank}) > 1:
            conflicts.update(index for index, _, _ in group)
    for index, candidate, event, paths, name, suffix, mode in prepared:
        if index in conflicts:
            audit['rejected'].append({'index': index, 'reason': 'conflicting_hint_candidates'})
            continue
        hints = [e for e in event['effects']
                 if isinstance(e, dict) and e.get('kind') == 'skill_hint_change'
                 and isinstance(e.get('name'), str)]
        same_skill = [e for e in hints if _logical_name(e['name']) == _logical_name(name)]
        if any(e.get('amount') != candidate['amount'] for e in same_skill):
            audit['rejected'].append({'index': index, 'reason': 'conflicting_hint_amount'})
            continue
        if any(_rank(e['name']) and _rank(name) and _rank(e['name']) != _rank(name) for e in same_skill):
            audit['rejected'].append({'index': index, 'reason': 'conflicting_hint_rank'})
            continue
        receipts = {o['receipt']['text'] for o in candidate['observations']}
        replace = [e for e in hints if e.get('amount') == candidate['amount']
                   and e.get('raw_text') in receipts
                   and event['field_evidence'].get(_field(e))
                   and set(event['field_evidence'][_field(e)]) <= paths]
        exact = [e for e in hints if e['name'] == name]
        affected = {_field(e) for e in replace+exact+same_skill}
        if (
                len(exact) > 1
                or (mode == 'wrapped' and suffix is None and len(same_skill) > 1)
                or any(c.get('field') in affected
                       for c in event.get('conflicting_readings', [])
                       if isinstance(c, dict))
        ):
            audit['rejected'].append({'index': index, 'reason': 'existing_hint_conflict'})
            continue
        # A wrapped source receipt proves the card name and amount while the
        # rank glyph is outside the readable evidence.  Reuse one existing
        # ranked variant when it is the only same-skill effect; creating a
        # base duplicate would make the one award look like two awards.
        retained = exact[0] if exact else (
            same_skill[0] if mode == 'wrapped' and suffix is None and len(same_skill) == 1
            else None
        )
        if retained is None:
            retained = dict(kind='skill_hint_change', name=name, amount=candidate['amount'],
                            raw_text=candidate['observations'][0]['receipt']['text'])
            event['effects'].append(retained)
        if mode == 'wrapped':
            retained['identity_basis'] = 'validated_repeated_hint_card_and_wrapped_receipt'
            retained['circle_variant_verified'] = suffix is not None
            rank_state = candidate['identity_proof'].get('rank_state')
            # Keep rank uncertainty explicit.  If a pre-existing ranked effect
            # is reused, its name remains intact and this separate field says
            # that the wrapped card itself did not expose the glyph.
            if rank_state == 'undetermined':
                if _rank(retained['name']) is None:
                    retained['rank_state'] = 'undetermined'
                else:
                    retained['hint_card_rank_state'] = 'undetermined'
            else:
                retained['rank_state'] = rank_state
        else:
            retained['identity_basis'] = 'validated_repeated_hint_card_and_receipt_prefix'
            retained['circle_variant_verified'] = suffix is not None
        card_evidence = retained.setdefault('hint_card_evidence', [])
        if candidate not in card_evidence:
            card_evidence.append(deepcopy(candidate))
        alternatives = retained.setdefault('observed_name_candidates', [name])
        for observed in candidate.get('raw_receipt_name_candidates', []):
            if observed not in alternatives:
                alternatives.append(observed)
        proofs = event['field_evidence'].setdefault(_field(retained), [])
        for observation in candidate['observations']:
            if observation['evidence'] not in proofs:
                proofs.append(observation['evidence'])
        removed = []
        for old in replace:
            if old is retained:
                continue
            archived = deepcopy(old)
            archived['field_evidence'] = event['field_evidence'].pop(_field(old), [])
            removed.append(archived)
            event['effects'].remove(old)
        if removed:
            retained.setdefault('replaced_receipt_readings', []).extend(removed)
        audit['accepted'].append({'index': index, 'event_id': event['id'], 'name': name,
                                  'amount': candidate['amount']})
    return audit
