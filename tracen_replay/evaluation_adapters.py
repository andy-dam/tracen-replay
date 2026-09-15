"""Shared source/report adapters for occurrence evaluation.

This does not import local graders or use prediction amounts to select a join.
Original labels and unsupported fields remain in the output for audit.
"""
from copy import deepcopy
import math
import re
from pathlib import PureWindowsPath

from .evaluation_labels import normalize
from .training_gain_resolution import candidate_recovery_policy


_MISSING = object()


def evidence_ids(value, root=None):
    """Normalize separators and an explicit root, never reduce to basename."""
    if isinstance(value, str):
        value = [value]
    result = []
    for item in value or []:
        path = PureWindowsPath(item)
        if path.is_absolute() and root is not None:
            path = path.relative_to(PureWindowsPath(root))
        if '..' in path.parts:
            raise ValueError('Evidence path escapes its declared root')
        result.append(path.as_posix())
    return list(dict.fromkeys(result))


def _payload(category, expected):
    keys = {'effect': ('kind', 'field', 'name', 'amount', 'value', 'direction', 'context_title',
                       'values', 'sections', 'item_names_visible', 'list_complete'),
            'action': ('kind', 'training_option', 'training_name', 'result', 'name', 'grade', 'placing', 'course',
                       'companion', 'choices', 'selected_choice'),
            'state': ('channel', 'values', 'caps', 'calendar_text', 'turns_remaining_to_goal'),
            'purchase': ('kind', 'name', 'cost', 'visible_confirmation_names',
                         'skill_points_after', 'confirmation_names_complete',
                         'identity_status', 'post_learn_obtained_names',
                         'price_status', 'precommit_price_visibility', 'confirmation_text',
                         'visible_precommit_obtained_names', 'visible_card_prices',
                         'selected_draft_names', 'selection_status',
                         'confirm_available')}.get(category, ())
    result = {k: deepcopy(expected[k]) for k in keys if k in expected}
    unscored = {k: deepcopy(v) for k, v in expected.items() if k not in keys}
    if (category == 'effect' and result.get('kind') == 'max_energy_change'
            and result.get('field') in (None, 'energy')):
        # Both schemas describe a change to energy capacity, not recovered
        # energy. Preserve the source amount (including unknown) unchanged.
        unscored['original_kind'] = result['kind']
        result.update(kind='stat_cap_change', field='energy')
    if category == 'action' and isinstance(result.get('result'), str):
        value = result['result'].lower().strip()
        if value == 'friendship training success':
            result['result'] = 'success'
            unscored['result_detail'] = expected['result']
        else:
            result['result'] = {'success!':'success','failure!':'failure'}.get(value,value)
    if category == 'purchase' and result.get('kind') == 'skill':
        result.pop('name', None)  # Batch caption is not a purchased skill identity.
    return result, unscored


def _known_after_skill_points(purchase):
    """Return an unambiguous post-purchase balance and its evidence.

    Transaction workers may have more than one balance reading.  A value is
    safe to project only when every explicitly marked ``after`` reading agrees
    and carries a concrete integer.  In particular, missing data is not a
    zero balance.
    """
    direct = purchase.get('skill_points_after', _MISSING)
    if type(direct) is int:
        return direct, []
    rows = purchase.get('balance_evidence', [])
    if not isinstance(rows, list):
        return _MISSING, []
    after = [row for row in rows
             if isinstance(row, dict) and row.get('role') == 'after'
             and type(row.get('skill_points')) is int]
    values = {row['skill_points'] for row in after}
    if len(values) != 1:
        return _MISSING, []
    proofs = []
    for row in after:
        evidence = row.get('evidence', [])
        proofs.extend(evidence if isinstance(evidence, list) else [evidence])
    return next(iter(values)), evidence_ids(proofs)


_SKILL_IDENTITY_BASES = frozenset({'incomplete_skill_confirmation_list'})
_SKILL_OBTAINED_BASES = frozenset({
    'post_receipt_inventory_obtained_or_selected_card',
    'repeated_post_confirmation_obtained_skill_card',
})
_SKILL_COMMITTED_PRICE_BASES = frozenset({'explicit_source_price_status'})
_SKILL_PRECOMMIT_PRICE_BASES = frozenset({
    'explicit_source_price_status',
    'visible_skill_card_prices_with_incomplete_item_list',
})
_SKILL_RECEIPT_BASES = frozenset({'skill_receipt_exact_text'})
_SKILL_RECEIPT_TEXTS = frozenset({
    'our trainee learned new skills!',
    'your trainee learned new skills!',
})


def _validated_skill_proofs(reading_index, proofs, screens, *, lower=None, upper=None):
    """Bind metadata evidence to unique gameplay readings in this report."""
    if not isinstance(reading_index, dict):
        return None
    try:
        paths = evidence_ids(proofs)
    except (TypeError, ValueError):
        return None
    if not paths:
        return None
    rows = []
    for path in paths:
        candidates = reading_index.get(path, ())
        # Reused/copied paths cannot establish which source observation a
        # metadata field came from.
        if len(candidates) != 1:
            return None
        row = candidates[0]
        timestamp = row.get('source_timestamp_ms')
        if (type(timestamp) is not int or row.get('screen') not in screens
                or (lower is not None and timestamp < lower)
                or (upper is not None and timestamp > upper)):
            return None
        rows.append(row)
    return paths, rows


def _skill_batch_metadata(purchase, reading_index):
    """Project only source-bound skill-batch metadata and its proof paths."""
    if not isinstance(reading_index, dict) or not reading_index:
        return {}, []
    metadata = {}
    proofs = []

    identity_status = purchase.get('identity_status')
    if isinstance(identity_status, str) and identity_status.strip():
        identity_proofs = _validated_skill_proofs(
            reading_index, purchase.get('identity_status_evidence'),
            {'skill_confirmation'},
            lower=purchase.get('confirmation_first_seen_ms'),
            upper=purchase.get('confirmation_last_seen_ms'))
        if (purchase.get('identity_status_basis') in _SKILL_IDENTITY_BASES
                and identity_proofs is not None):
            metadata['identity_status'] = identity_status
            proofs.extend(identity_proofs[0])

    names = purchase.get('post_learn_obtained_names')
    name_evidence = purchase.get('post_learn_obtained_name_evidence')
    provenance = purchase.get('post_learn_obtained_name_provenance')
    obtained_ok = (isinstance(names, list) and bool(names)
                   and all(isinstance(name, str) and name.strip() for name in names)
                   and len(set(names)) == len(names)
                   and isinstance(name_evidence, dict)
                   and isinstance(provenance, list)
                   and len(provenance) == len(names))
    if obtained_ok:
        obtained_proofs = []
        lower = purchase.get('last_seen_ms')
        upper = lower + 1500 if type(lower) is int else None
        provenance_by_name = {}
        for item in provenance:
            if (not isinstance(item, dict) or not isinstance(item.get('name'), str)
                    or item['name'] in provenance_by_name):
                obtained_ok = False
                break
            provenance_by_name[item['name']] = item
        for name in names:
            item = provenance_by_name.get(name)
            item_proofs = _validated_skill_proofs(
                reading_index, name_evidence.get(name), {'skill_selection'},
                lower=lower, upper=upper)
            item_paths = None
            if item_proofs is not None:
                item_paths = item_proofs[0]
            if (item is None or item.get('basis') not in _SKILL_OBTAINED_BASES
                    or item.get('observed_menu_status') != 'obtained_or_selected'
                    or item.get('ownership_status') != 'post_receipt_inventory_observed'
                    or item.get('acquisition_verified') is not False
                    or type(item.get('first_seen_ms')) is not int
                    or type(item.get('last_seen_ms')) is not int
                    or item['first_seen_ms'] > item['last_seen_ms']
                    or type(item.get('observation_count')) is not int
                    or item['observation_count'] < 1
                    or item_paths is None):
                obtained_ok = False
                break
            try:
                provenance_paths = evidence_ids(item.get('evidence'))
            except (TypeError, ValueError):
                obtained_ok = False
                break
            observed_times = {row.get('source_timestamp_ms') for row in item_proofs[1]}
            if (provenance_paths != item_paths
                    or item['first_seen_ms'] < (lower if type(lower) is int else item['first_seen_ms'])
                    or (upper is not None and item['last_seen_ms'] > upper)
                    or not observed_times
                    or min(observed_times) < item['first_seen_ms']
                    or max(observed_times) > item['last_seen_ms']):
                obtained_ok = False
                break
            obtained_proofs.extend(item_paths)
        if obtained_ok:
            metadata['post_learn_obtained_names'] = deepcopy(names)
            proofs.extend(obtained_proofs)

    def validated_price_record(record, *, screens, bases, lower=None, upper=None):
        if not isinstance(record, dict):
            return None
        status = record.get('status')
        basis = record.get('basis')
        if status not in ('partially_visible', 'unreadable', 'unknown') or basis not in bases:
            return None
        price_proofs = _validated_skill_proofs(
            reading_index, record.get('evidence'), screens, lower=lower, upper=upper)
        timestamps = record.get('observed_timestamps')
        count = record.get('observed_card_count')
        timestamp_ok = (isinstance(timestamps, list)
                        and all(type(value) is int for value in timestamps)
                        and len(set(timestamps)) == len(timestamps)
                        and price_proofs is not None
                        and set(row.get('source_timestamp_ms') for row in price_proofs[1])
                        .issubset(set(timestamps)))
        if (type(count) is not int or count < 0 or not timestamp_ok):
            return None
        return dict(status=status, basis=basis, observed_card_count=count,
                    observed_timestamps=sorted(timestamps), evidence=price_proofs[0])

    committed_record = validated_price_record(
        dict(status=purchase.get('price_status'),
             basis=purchase.get('price_status_basis'),
             evidence=purchase.get('price_status_evidence'),
             observed_card_count=purchase.get('price_status_observed_card_count'),
             observed_timestamps=purchase.get('price_status_observed_timestamps')),
        screens={'skill_confirmation', 'skill_receipt'},
        bases=_SKILL_COMMITTED_PRICE_BASES,
        lower=purchase.get('confirmation_first_seen_ms'),
        upper=purchase.get('last_seen_ms'))
    if committed_record is not None:
        metadata['price_status'] = committed_record['status']
        proofs.extend(committed_record['evidence'])

    precommit = purchase.get('precommit_price_visibility')
    if isinstance(precommit, dict):
        confirmation_start = purchase.get('confirmation_first_seen_ms')
        upper = confirmation_start - 1 if type(confirmation_start) is int else None
        precommit_record = validated_price_record(
            precommit, screens={'skill_selection'},
            bases=_SKILL_PRECOMMIT_PRICE_BASES, upper=upper)
        if (precommit_record is not None
                and precommit.get('phase') == 'preview'):
            # Keep proof paths on the observation row, as with the other
            # purchase metadata.  The nested value describes the preview
            # scope without turning card prices into committed fields.
            metadata['precommit_price_visibility'] = dict(
                phase='preview', status=precommit_record['status'],
                basis=precommit_record['basis'],
                observed_card_count=precommit_record['observed_card_count'],
                observed_timestamps=precommit_record['observed_timestamps'])
            proofs.extend(precommit_record['evidence'])

    confirmation_text = purchase.get('confirmation_text')
    text_proofs = _validated_skill_proofs(
        reading_index, purchase.get('confirmation_text_evidence'),
        {'skill_receipt'},
        lower=purchase.get('first_seen_ms'), upper=purchase.get('last_seen_ms'))
    text_observations = purchase.get('confirmation_text_observations')
    text_variants = purchase.get('confirmation_text_variants')
    observation_ok = isinstance(text_observations, list) and bool(text_observations)
    observed_texts = []
    if observation_ok:
        for item in text_observations:
            if (not isinstance(item, dict) or not isinstance(item.get('text'), str)
                    or item['text'].strip().casefold() not in _SKILL_RECEIPT_TEXTS
                    or type(item.get('source_timestamp_ms')) is not int
                    or type(item.get('confidence')) not in (int, float)
                    or item['confidence'] < 90):
                observation_ok = False
                break
            item_proofs = _validated_skill_proofs(
                reading_index, item.get('evidence'), {'skill_receipt'},
                lower=purchase.get('first_seen_ms'), upper=purchase.get('last_seen_ms'))
            if (item_proofs is None
                    or item['source_timestamp_ms'] not in {
                        row.get('source_timestamp_ms') for row in item_proofs[1]}):
                observation_ok = False
                break
            observed_texts.append(item['text'])
    variants_ok = (isinstance(text_variants, list)
                   and all(isinstance(value, str) and value.strip() for value in text_variants)
                   and set(observed_texts).issubset(set(text_variants)))
    canonical_text = (confirmation_text.strip().casefold()
                      if isinstance(confirmation_text, str) else None)
    if (canonical_text in _SKILL_RECEIPT_TEXTS
            and purchase.get('confirmation_text_basis') in _SKILL_RECEIPT_BASES
            and text_proofs is not None and observation_ok and variants_ok
            and confirmation_text in observed_texts):
        metadata['confirmation_text'] = confirmation_text
        proofs.extend(text_proofs[0])
    return metadata, list(dict.fromkeys(proofs))


def _skill_purchase_payload(purchase, readings=None, *, reading_index=None):
    """Project only source-backed fields the transaction worker observed."""
    payload = {'kind': 'skill'}
    if reading_index is None and readings is not None:
        reading_index = _reading_index(readings)
    # These fields are optional in older reports.  Do not manufacture an
    # unknown value from a receipt or from a guessed card-price assignment.
    names = purchase.get('visible_confirmation_names')
    if (isinstance(names, list)
            and all(isinstance(name, str) and name.strip() for name in names)):
        payload['visible_confirmation_names'] = deepcopy(names)
    for key in ('visible_card_prices',):
        if purchase.get(key) is not None:
            payload[key] = deepcopy(purchase[key])
    if type(purchase.get('purchased_list_complete')) is bool:
        payload['confirmation_names_complete'] = purchase['purchased_list_complete']
    after, proofs = _known_after_skill_points(purchase)
    if after is not _MISSING:
        payload['skill_points_after'] = after
    metadata, metadata_proofs = _skill_batch_metadata(purchase, reading_index)
    payload.update(metadata)
    return payload, list(dict.fromkeys(proofs + metadata_proofs))


def _race_reward_payload(snapshot):
    """Project every accepted quantity from one stable reward snapshot.

    The generic ``item_quantities`` observation keeps the visible order across
    Items and Bonus sections.  Section labels are retained alongside the
    values so consumers can apply section-aware semantics without silently
    dropping a visible quantity.
    """
    if not isinstance(snapshot, dict):
        return None
    items = snapshot.get('items')
    if not isinstance(items, list):
        return None
    item_rows = [row for row in items
                 if isinstance(row, dict) and type(row.get('quantity')) is int]
    quantities = [row.get('quantity') for row in item_rows
                  if type(row.get('quantity')) is int]
    if not quantities:
        return None
    payload = {'kind': 'race_reward', 'field': 'item_quantities',
               'values': quantities,
               'item_names_visible': any(
                   isinstance(row.get('name'), str) and row['name'].strip()
                   for row in item_rows)}
    sections = [row.get('section') for row in item_rows]
    if any(section is not None for section in sections):
        payload['sections'] = sections
    if type(snapshot.get('list_complete')) is bool:
        payload['list_complete'] = snapshot['list_complete']
    return payload


def _same_frame_stat_caps(checkpoint, readings):
    """Project caps only from an accepted reading owned by this checkpoint.

    ``facts.stat_caps`` is attached to a reading, while checkpoints own their
    evidence through ``supporting_frames``.  Requiring both the exact frame
    membership and an exact stat-value match prevents a cap read on a nearby
    or result screen from being borrowed by an otherwise similar checkpoint.
    Conflicting values remain unknown per field.
    """
    if not isinstance(checkpoint, dict) or not isinstance(readings, list):
        return {}, []
    owned = set(evidence_ids(checkpoint.get('supporting_frames',
                                             checkpoint.get('evidence'))))
    values = checkpoint.get('values')
    if not owned or not isinstance(values, dict):
        return {}, []
    candidates = {}
    proofs = {}
    for reading in readings:
        if not isinstance(reading, dict):
            continue
        evidence = set(evidence_ids(reading.get('evidence')))
        if not owned.intersection(evidence):
            continue
        reading_values = (reading.get('stats') or {}).get('values')
        if reading_values != values:
            continue
        facts = reading.get('facts')
        caps = facts.get('stat_caps') if isinstance(facts, dict) else None
        if not isinstance(caps, dict):
            continue
        for field, cap in caps.items():
            if field not in ('speed', 'stamina', 'power', 'guts', 'wit'):
                continue
            if type(cap) is not int or cap <= 0:
                continue
            candidates.setdefault(field, set()).add(cap)
            proofs.setdefault(field, set()).update(evidence.intersection(owned))
    projected = {field: next(iter(options)) for field, options in candidates.items()
                 if len(options) == 1 and proofs.get(field)}
    evidence = sorted({path for field in projected for path in proofs[field]})
    return projected, evidence


def _persisted_rows(value):
    """Read a worker observation list or its envelope without deriving facts."""
    if isinstance(value, dict):
        value = value.get('observations', [])
    return value if isinstance(value, list) else []


def _persisted_evidence(row):
    proofs = row.get('evidence', []) if isinstance(row, dict) else []
    if isinstance(proofs, str):
        proofs = [proofs]
    if isinstance(proofs, list):
        proofs = [proof for proof in proofs if isinstance(proof, str)]
    else:
        proofs = []
    if proofs:
        return evidence_ids(proofs)
    source_observations = row.get('source_observations', []) if isinstance(row, dict) else []
    if not isinstance(source_observations, list):
        return []
    return evidence_ids([
        item.get('evidence') for item in source_observations
        if isinstance(item, dict) and isinstance(item.get('evidence'), str)
    ])


def _same_state_snapshot(persisted, payload, start, end, proofs):
    """Identify a checkpoint duplicate without merging partial snapshots."""
    if not isinstance(persisted, dict) or not isinstance(payload, dict):
        return False
    persisted_payload = persisted.get('payload')
    if not isinstance(persisted_payload, dict):
        return False
    if persisted_payload.get('channel') != payload.get('channel'):
        return False
    persisted_start, persisted_end = persisted.get('start_ms'), persisted.get('end_ms')
    if (type(start) is not int or type(end) is not int
            or type(persisted_start) is not int or type(persisted_end) is not int
            or start > persisted_end or persisted_start > end):
        return False
    persisted_values = persisted_payload.get('values')
    values = payload.get('values')
    if not isinstance(persisted_values, dict) or not isinstance(values, dict):
        return False
    if any(persisted_values.get(field, _MISSING) != value
           for field, value in values.items()):
        return False
    persisted_proofs = set(evidence_ids(persisted.get('evidence', [])))
    if proofs and persisted_proofs and not set(proofs).intersection(persisted_proofs):
        return False
    return True


def _context_title_for_evidence(readings, evidence):
    """Return one accepted context title owned by the supplied frames."""
    wanted = set(evidence_ids(evidence))
    if not wanted or not isinstance(readings, list):
        return None, []
    titles = {}
    for reading in readings:
        if not isinstance(reading, dict):
            continue
        title = reading.get('context_title')
        if not isinstance(title, str) or not title.strip():
            continue
        owned = set(evidence_ids(reading.get('evidence')))
        overlap = owned.intersection(wanted)
        if overlap:
            titles.setdefault(title.strip(), set()).update(overlap)
    if len(titles) != 1:
        return None, []
    title, proof = next(iter(titles.items()))
    return title, sorted(proof)


def _explicit_interval(row, start_keys, end_keys):
    """Read a bounded canonical interval without deriving one from neighbors."""
    start = next((row.get(key) for key in start_keys if type(row.get(key)) is int), None)
    end = next((row.get(key) for key in end_keys if type(row.get(key)) is int), None)
    if start is None or end is None or start > end:
        return None
    return start, end


def _canonical_action_phase(action, default='committed'):
    """Project a receipt's phase without conflating previews and results.

    Race receipts are emitted from the completed-result collection and older
    reports do not carry a phase field.  Their canonical observation is the
    visible race result, so the legacy default is ``observed`` for races.  An
    explicit phase still wins: callers can retain a committed entry when the
    receipt is intentionally documenting the action submission itself.
    """
    for key in ('phase', 'observation_phase', 'action_phase'):
        value = action.get(key)
        if value in ('preview', 'committed', 'applied', 'observed', 'context'):
            return value
    if action.get('result_only') is True:
        return 'observed'
    if action.get('kind') == 'race':
        return 'observed'
    return default


_RACE_METADATA_UNKNOWN_STATES = frozenset({
    'unknown', 'ambiguous', 'conflicted', 'conflicting', 'unresolved',
    'unknown_missing', 'unknown_conflicting_readings', 'unknown_alias_conflict',
    'unknown_action_conflict',
})
_RACE_CONFLICT_FIELDS = frozenset({
    'race_name', 'name', 'grade', 'race_grade', 'placing', 'course', 'fans', 'fans_gained',
})


def _normalized_race_grade(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r'\s+', ' ', value.strip()).upper()
    if value == 'PRE OP':
        value = 'PRE-OP'
    return value if re.fullmatch(r'(?:DEBUT|G[123]|OP|PRE-OP|EX)', value, re.I) else None


def _race_grade_from_record(record):
    values = []
    for key in ('race_grade', 'grade'):
        if key not in record or record.get(key) is None:
            continue
        value = _normalized_race_grade(record.get(key))
        if value is None:
            return None
        values.append(value)
    return values[0] if values and len(set(values)) == 1 else None


def _race_grade_conflicted(record):
    """Keep grades unknown when conflict metadata is malformed or unscoped."""

    if not isinstance(record, dict) or 'conflicting_readings' not in record:
        return False
    conflicts = record.get('conflicting_readings')
    if not isinstance(conflicts, dict):
        return True
    if any(key not in _RACE_CONFLICT_FIELDS for key in conflicts):
        return True
    return any(key in conflicts for key in ('race_grade', 'grade'))


def _race_metadata_unknown(value):
    """Recognize canonical unresolved race metadata states exactly."""
    if not isinstance(value, str):
        return False
    return value in _RACE_METADATA_UNKNOWN_STATES or value.startswith((
        'unknown_', 'ambiguous_', 'conflicted_', 'conflicting_', 'unresolved_'))


def _race_action_fallback_metadata(action):
    """Read source-bound race fields carried by a completed action receipt.

    ``race_action_receipts`` copies independently bound result fields onto the
    action.  This fallback is used only when the report's race-result map is
    unavailable; the action still needs its explicit identity, timestamp, and
    source evidence.  Field conflicts stay field-local so a readable placing
    is not discarded merely because the name is unresolved.
    """
    if not isinstance(action, dict) or action.get('kind') != 'race':
        return {}, [], None
    if (_canonical_action_phase(action) == 'preview'
            or action.get('is_preview') is True
            or action.get('preview') is True):
        return {}, [], None
    race_id = action.get('race_id')
    if not isinstance(race_id, str) or not race_id.strip():
        return {}, [], None
    if type(action.get('source_timestamp_ms')) is not int:
        return {}, [], None
    try:
        action_evidence = evidence_ids(action.get('evidence'))
    except (TypeError, ValueError):
        return {}, [], None
    if not action_evidence:
        return {}, [], None

    # A persisted binding diagnostic, when present, must itself be successful.
    # The normal assembled action omits this envelope, so this remains
    # compatible with existing reports while rejecting an explicit unknown
    # binding rather than trusting its copied fields.
    binding = action.get('race_action_binding', action.get('source_binding'))
    if binding is not None:
        if not isinstance(binding, dict) or binding.get('status') != 'bound':
            return {}, [], None

    metadata = action.get('metadata_status')
    if metadata is None:
        metadata = action.get('race_metadata_status')
    if metadata is None and isinstance(binding, dict):
        metadata = binding.get('metadata_status')
    if isinstance(metadata, str) and _race_metadata_unknown(metadata):
        return {}, [], None
    if metadata is not None and not isinstance(metadata, (dict, str)):
        return {}, [], None

    conflicts = action.get('conflicting_readings')

    def field_unknown(field):
        aliases = (('race_name', 'name') if field == 'race_name'
                   else ('race_grade', 'grade') if field == 'race_grade'
                   else (field,))
        if isinstance(metadata, dict):
            statuses = [metadata[key] for key in aliases if key in metadata]
            if any(_race_metadata_unknown(status) for status in statuses):
                return True
        conflict_keys = [f'{alias}_{suffix}' for alias in aliases
                         for suffix in ('conflict', 'ambiguous')]
        for key in conflict_keys:
            if action.get(key) is True:
                return True
        if 'conflicting_readings' in action and not isinstance(conflicts, dict):
            # A present non-mapping container has no field scope, including
            # an empty list.  It cannot certify any copied race field.
            return True
        if isinstance(conflicts, dict):
            if any(key in conflicts for key in aliases):
                return True
            if any(key not in _RACE_CONFLICT_FIELDS for key in conflicts):
                return True
        elif conflicts:
            # An unscoped conflict cannot certify either field.
            return True
        return False

    name_values = []
    for key in ('race_name', 'name'):
        value = action.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            return {}, [], None
        name_values.append(value.strip())
    if field_unknown('race_name') or len(set(name_values)) > 1:
        name_values = []

    placing = action.get('placing')
    if field_unknown('placing') or (placing is not None and (
            type(placing) is not int or placing < 1)):
        placing = None

    grade = _race_grade_from_record(action)
    if field_unknown('race_grade'):
        grade = None

    if name_values:
        metadata_payload = {'name': name_values[0]}
    else:
        metadata_payload = {}
    if grade is not None:
        metadata_payload['grade'] = grade
    if placing is not None:
        metadata_payload['placing'] = placing
    if not metadata_payload:
        return {}, [], None
    return metadata_payload, action_evidence, 'source_bound_race_action_metadata'


# These are the exact bases emitted by the transaction worker for a gain that
# was read from a source badge.  Keep this list exact: a substring check would
# incorrectly treat names such as ``indirect_balance_match`` as direct proof.
_DIRECT_GAIN_BASES = frozenset({
    'repeated_training_gain_badge',
    'training_gain_full_phase',
    'training_gain_prefix_recovered',
    'training_gain_source_phase',
    'candidate_only_training_gain',
})
_SOURCE_CLIPPED_GAIN_BASIS = 'source_clipped_training_gain'
_SOURCE_CLIPPED_RESOLUTION_BASES = frozenset({
    # The original cross-frame resolver requires a broad and result-family
    # pair.  Source-pixel localization is a stricter resolver branch: its
    # accepted observations still carry the same source/timestamp contract,
    # but the localized connected component is the independent tight proof.
    'cross_frame_source_clipped_gain_overlay_and_broad_agreement',
    'source_pixel_localized_training_badge_with_tight_agreement',
    'source_pixel_refined_training_gain_phase',
})

_CANDIDATE_DIRECT_BASES = frozenset({
    'repeated_source_gain_badge_same_phase',
    'same_frame_nested_source_gain_crop_agreement',
})
_CANDIDATE_DIRECT_FAMILIES = frozenset({'gain', 'wide_gain', 'expanded_gain'})
def _reading_index(readings):
    """Index report-owned evidence paths by their complete reading rows."""
    result = {}
    if not isinstance(readings, list):
        return result
    for row in readings:
        if not isinstance(row, dict):
            continue
        try:
            proofs = evidence_ids(row.get('evidence'))
        except (TypeError, ValueError):
            continue
        for proof in proofs:
            result.setdefault(proof, []).append(row)
    return result


def _receipt_effect_key(effect):
    """Return the atomic semantic identity used by outcome field evidence."""

    if not isinstance(effect, dict):
        return None
    return tuple(effect.get(key) for key in (
        'kind', 'field', 'name', 'amount', 'direction', 'value',
    ))


def _valid_receipt_line_box(value):
    """Validate a gameplay receipt line box without estimating its location."""

    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    if any(type(part) not in (int, float) or isinstance(part, bool)
           for part in value):
        return False
    try:
        left, top, right, bottom = (float(part) for part in value)
    except (OverflowError, TypeError, ValueError):
        return False
    if not all(math.isfinite(part) for part in (left, top, right, bottom)):
        return False
    # Receipt text is in the lower gameplay pane.  These bounds validate the
    # recorded box; they do not construct one from sentence length or a name.
    return 0 <= left < right <= 810 and 770 <= top < bottom <= 1000


def _valid_receipt_confidence(value):
    """Accept only finite OCR confidence percentages in the trusted range."""

    if type(value) not in (int, float) or isinstance(value, bool):
        return False
    try:
        confidence = float(value)
    except (OverflowError, TypeError, ValueError):
        return False
    return math.isfinite(confidence) and 95 <= confidence <= 100


def _source_bound_occluded_effect_proofs(event, effect, reading_index):
    """Add only independently validated parent-frame proof for one effect.

    Outcome assembly can retain an occluded receipt line in the source
    reading while the parsed effect is supplied by a later dense reread.  The
    adapter must preserve that source frame for the matching field, but a
    parent event frame cannot be copied to every effect.  A frame qualifies
    only when its own ``occluded_receipt_lines`` contains exactly one valid,
    high-confidence line whose parsed semantics and raw text match this
    effect, and the line belongs to the event's timestamp/context interval.
    """

    if not isinstance(event, dict) or not isinstance(effect, dict):
        return [], False
    field = '|'.join(str(effect.get(key) or '') for key in (
        'kind', 'field', 'name',
    ))
    try:
        existing = evidence_ids(event.get('field_evidence', {}).get(field, []))
        parent_paths = evidence_ids(event.get('evidence'))
    except (TypeError, ValueError):
        return [], False
    if not parent_paths:
        return list(dict.fromkeys(existing)), False
    # Most effects already cite their parent frame.  Avoid reparsing those
    # rows; the source-bound branch exists only for a missing field proof.
    parent_paths = [path for path in parent_paths if path not in existing]
    if not parent_paths:
        return list(dict.fromkeys(existing)), False
    start, end = event.get('first_seen_ms'), event.get('last_seen_ms')
    if (type(start) is not int or type(end) is not int or start > end
            or _receipt_effect_key(effect) is None):
        return list(dict.fromkeys(existing)), False

    from .gameplay import effects_from_lines

    effect_key = _receipt_effect_key(effect)
    raw_text = effect.get('raw_text')
    normalized_raw = ' '.join(raw_text.split()) if isinstance(raw_text, str) else None
    recovered = []
    for proof in parent_paths:
        rows = reading_index.get(proof, [])
        if not isinstance(rows, list) or len(rows) != 1:
            continue
        row = rows[0]
        if not isinstance(row, dict):
            continue
        timestamp = row.get('source_timestamp_ms')
        if (row.get('screen') != 'event_outcome'
                or type(timestamp) is not int or not start <= timestamp <= end):
            continue
        event_context = event.get('context_title')
        row_context = row.get('context_title')
        if (not isinstance(event_context, str) or not event_context.strip()
                or not isinstance(row_context, str) or not row_context.strip()
                or event_context.strip() != row_context.strip()):
            continue
        facts = row.get('facts')
        lines = facts.get('occluded_receipt_lines') if isinstance(facts, dict) else None
        if not isinstance(lines, list):
            continue
        matches = []
        for line in lines:
            if not isinstance(line, dict):
                continue
            text = line.get('text')
            confidence = line.get('confidence')
            if (not isinstance(text, str) or not text.strip()
                    or not _valid_receipt_confidence(confidence)
                    or not _valid_receipt_line_box(line.get('box'))):
                continue
            if normalized_raw is not None and ' '.join(text.split()) != normalized_raw:
                continue
            try:
                parsed = effects_from_lines([line])
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if any(_receipt_effect_key(candidate) == effect_key for candidate in parsed):
                matches.append(line)
        if len(matches) == 1:
            recovered.append(proof)
    proofs = list(dict.fromkeys(existing + recovered))
    return proofs, bool(set(recovered) - set(existing))


def _reading_at(reading_index, proof, timestamp, option, *, field=None, amount=None):
    """Return the unique source reading compatible with one training event."""
    rows = [row for row in reading_index.get(proof, ())
            if row.get('source_timestamp_ms') == timestamp]
    # Do not select the first convenient duplicate.  An equal path/timestamp
    # with competing parser facts is itself unresolved source identity.
    if len(rows) != 1:
        return None
    row = rows[0]
    if row.get('screen') != 'training_result':
        return None
    reading_option = row.get('training_option')
    if reading_option is not None and reading_option != option:
        return None
    if field is not None:
        facts = row.get('facts')
        gains = facts.get('training_gains') if isinstance(facts, dict) else None
        observed = gains.get(field, _MISSING) if isinstance(gains, dict) else _MISSING
        if observed is not _MISSING and observed != amount:
            return None
    return row


def _candidate_direct_box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(type(part) not in (int, float) or not math.isfinite(float(part))
           for part in value):
        return None
    try:
        box = tuple(float(part) for part in value)
    except (TypeError, ValueError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _candidate_direct_policy(value):
    """Accept only the producer policy or bounds that are strictly tighter."""

    reference = candidate_recovery_policy()
    if not isinstance(value, dict) or set(value) != set(reference):
        return None
    result = dict(reference)
    minimums = {
        'minimum_tight_confidence',
        'minimum_broad_confidence',
        'minimum_repeated_frames',
    }
    maximums = {'maximum_span_ms', 'maximum_gap_ms'}
    for key, expected in reference.items():
        actual = value.get(key)
        if key in minimums or key in maximums:
            if (type(actual) not in (int, float)
                    or not math.isfinite(float(actual))):
                return None
            if key in minimums and float(actual) < float(expected):
                return None
            if key in maximums and float(actual) > float(expected):
                return None
            result[key] = actual
        elif type(actual) is not type(expected) or actual != expected:
            return None
    return result


def _candidate_direct_metadata(provenance, field, amount, option, event_start, event_end):
    """Validate the crop-level proof behind candidate direct provenance."""

    basis = provenance.get('candidate_recovery_basis')
    if basis not in _CANDIDATE_DIRECT_BASES:
        return False
    phase_key = provenance.get('candidate_phase_key')
    if not isinstance(phase_key, (str, int)) or (isinstance(phase_key, str) and not phase_key.strip()):
        return False
    declared_policy = provenance.get('candidate_policy')
    policy = _candidate_direct_policy(declared_policy)
    if policy is None:
        return False
    candidates = provenance.get('candidate_observations')
    normalized = provenance.get('observations')
    if not isinstance(candidates, list) or not candidates or not isinstance(normalized, list):
        return False
    metadata_keys = []
    by_identity = {}
    families_by_identity = {}
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get('field') != field:
            return False
        if candidate.get('amount') != amount:
            return False
        timestamp = candidate.get('source_timestamp_ms')
        evidence = candidate.get('evidence')
        if (type(timestamp) is not int or timestamp < event_start or timestamp > event_end
                or not isinstance(evidence, str) or not evidence.strip()):
            return False
        phase = candidate.get('phase_signature')
        if (not isinstance(phase, list) or len(phase) != 3
                or phase[0] != 'training_result' or phase[1] != option
                or phase[2] is not False):
            return False
        family = candidate.get('crop_family')
        region = candidate.get('region')
        if family not in _CANDIDATE_DIRECT_FAMILIES or region != f'{family}.{field}':
            return False
        role = candidate.get('source_role')
        if family == 'gain' and role != 'amount_crop_candidate':
            return False
        if family in {'wide_gain', 'expanded_gain'} and role not in {
            'amount_crop_candidate', 'unparsed_crop_candidate',
        }:
            return False
        box = _candidate_direct_box(candidate.get('box'))
        if box is None:
            return False
        confidence = candidate.get('confidence')
        if (type(confidence) not in (int, float)
                or not math.isfinite(float(confidence))):
            return False
        if family == 'gain':
            minimum = policy['minimum_tight_confidence']
            text = re.fullmatch(r'\s*\+\s*(\d{1,3})\s*', str(candidate.get('raw_text', '')))
        else:
            minimum = policy['minimum_broad_confidence']
            text = re.fullmatch(r"\s*\+\s*(\d{1,3})([:.,'’)\]}])?\s*",
                                str(candidate.get('raw_text', '')))
        if not text or float(confidence) < minimum or int(text.group(1)) != amount:
            return False
        identity = (timestamp, evidence.strip())
        prior = by_identity.get(identity)
        if prior is not None:
            families = families_by_identity.setdefault(identity, set())
            if (basis != 'same_frame_nested_source_gain_crop_agreement'
                    or family in families):
                return False
        else:
            metadata_keys.append(identity)
            families_by_identity[identity] = set()
        families_by_identity[identity].add(family)
        by_identity[identity] = candidate
    all_candidates = provenance.get('candidate_all_observations')
    if not isinstance(all_candidates, list) or not all_candidates:
        return False
    accepted_phase = tuple(candidates[0]['phase_signature'])
    for candidate in all_candidates:
        if not isinstance(candidate, dict) or candidate.get('field') != field:
            return False
        all_amount = candidate.get('amount')
        timestamp = candidate.get('source_timestamp_ms')
        evidence = candidate.get('evidence')
        phase = candidate.get('phase_signature')
        family = candidate.get('crop_family')
        region = candidate.get('region')
        if (type(all_amount) is not int or all_amount < 0 or all_amount > 999
                or type(timestamp) is not int or timestamp < event_start or timestamp > event_end
                or not isinstance(evidence, str) or not evidence.strip()
                or not isinstance(phase, list) or len(phase) != 3
                or phase[0] != 'training_result' or phase[1] != option
                or phase[2] is not False
                or family not in _CANDIDATE_DIRECT_FAMILIES
                or region != f'{family}.{field}'
                or _candidate_direct_box(candidate.get('box')) is None):
            return False
        role = candidate.get('source_role')
        if (family == 'gain' and role != 'amount_crop_candidate') or (
                family in {'wide_gain', 'expanded_gain'}
                and role not in {'amount_crop_candidate', 'unparsed_crop_candidate'}):
            return False
        confidence = candidate.get('confidence')
        if type(confidence) not in (int, float) or not math.isfinite(float(confidence)):
            return False
        if family == 'gain':
            minimum = policy['minimum_tight_confidence']
            text = re.fullmatch(r'\s*\+\s*(\d{1,3})\s*', str(candidate.get('raw_text', '')))
        else:
            minimum = policy['minimum_broad_confidence']
            text = re.fullmatch(r"\s*\+\s*(\d{1,3})([:.,'’)\]}])?\s*",
                                str(candidate.get('raw_text', '')))
        if not text or int(text.group(1)) != all_amount:
            return False
        if (float(confidence) >= float(minimum)
                and (all_amount != amount or tuple(phase) != accepted_phase)):
            return False
    normalized_keys = []
    for item in normalized:
        if (not isinstance(item, dict) or type(item.get('source_timestamp_ms')) is not int
                or not isinstance(item.get('evidence'), str)
                or item.get('value') != amount):
            return False
        normalized_keys.append((item['source_timestamp_ms'], item['evidence'].strip()))
    if normalized_keys != [
        (item['source_timestamp_ms'], item['evidence'].strip())
        for item in candidates
    ]:
        return False
    if basis == 'repeated_source_gain_badge_same_phase':
        tight = [item for item in candidates if item.get('crop_family') == 'gain']
        times = sorted({identity[0] for identity in metadata_keys})
        minimum_frames = int(policy['minimum_repeated_frames'])
        if (len(tight) < minimum_frames or len(metadata_keys) < minimum_frames
                or len(times) < minimum_frames
                or times[-1] - times[0] > int(policy['maximum_span_ms'])
                or any(later - earlier > int(policy['maximum_gap_ms'])
                       for earlier, later in zip(times, times[1:]))):
            return False
    else:
        tight = [item for item in candidates if item.get('crop_family') == 'gain']
        broad = [item for item in candidates
                 if item.get('crop_family') in {'wide_gain', 'expanded_gain'}]
        if not tight or not broad:
            return False
        if not any(
            same['source_timestamp_ms'] == wide['source_timestamp_ms']
            and same['evidence'].strip() == wide['evidence'].strip()
            and _candidate_direct_box(wide['box'])[0] <= _candidate_direct_box(same['box'])[0]
            and _candidate_direct_box(wide['box'])[1] <= _candidate_direct_box(same['box'])[1]
            and _candidate_direct_box(wide['box'])[2] >= _candidate_direct_box(same['box'])[2]
            and _candidate_direct_box(wide['box'])[3] >= _candidate_direct_box(same['box'])[3]
            for same in tight for wide in broad
        ):
            return False
    return True


def _source_resolution_item_matches(stored, rebuilt):
    """Compare source-proof fields while allowing additive producer metadata."""
    if not isinstance(stored, dict) or not isinstance(rebuilt, dict):
        return False
    for key, value in stored.items():
        if key == 'evidence':
            try:
                if evidence_ids(value) != evidence_ids(rebuilt.get(key)):
                    return False
            except (TypeError, ValueError):
                return False
        elif key not in rebuilt or rebuilt.get(key) != value:
            return False
    return True


def _source_resolution_items_compatible(stored, rebuilt, *, subset):
    """Match persisted resolver items one-to-one against current source items."""
    if not isinstance(stored, list) or not isinstance(rebuilt, list):
        return False
    if not subset and len(stored) != len(rebuilt):
        return False
    unused = list(rebuilt)
    for item in stored:
        matches = [
            index for index, candidate in enumerate(unused)
            if _source_resolution_item_matches(item, candidate)
        ]
        if len(matches) != 1:
            return False
        unused.pop(matches[0])
    return subset or not unused


def _source_clipping_resolutions_compatible(stored, rebuilt):
    """Accept resolver-shape drift only when source identities still agree.

    Older reports persisted only the two physical proof crops in
    ``accepted_observations`` and left ``full_observations`` empty.  Newer
    producers may retain every repeated tight crop there.  The report's raw
    crop observations, decision core, and stored proof items must still bind
    one-to-one to the fresh source-only reconstruction; only additive resolver
    metadata and a current accepted superset are tolerated.
    """
    if not isinstance(stored, dict) or not isinstance(rebuilt, dict):
        return False
    for key in (
        'status', 'accepted_amount', 'field', 'phase_key', 'basis', 'reason',
        'observed_amounts',
    ):
        if stored.get(key) != rebuilt.get(key):
            return False
    if not _source_resolution_items_compatible(
        stored.get('observations'), rebuilt.get('observations'), subset=False
    ):
        return False
    if not _source_resolution_items_compatible(
        stored.get('short_observations', []),
        rebuilt.get('short_observations', []),
        subset=True,
    ):
        return False
    if not _source_resolution_items_compatible(
        stored.get('accepted_observations'),
        rebuilt.get('accepted_observations'),
        subset=True,
    ):
        return False
    stored_full = stored.get('full_observations', [])
    rebuilt_full = rebuilt.get('full_observations', [])
    if not isinstance(stored_full, list) or not isinstance(rebuilt_full, list):
        return False
    # A legacy localized resolution may have no separate full-observation
    # list.  When present, each stored full item must remain source-accepted;
    # current producers may reclassify it into their broader accepted list.
    if (
        not _source_resolution_items_compatible(stored_full, rebuilt_full, subset=True)
        and not _source_resolution_items_compatible(
            stored_full, rebuilt.get('accepted_observations', []), subset=True
        )
    ):
        return False
    stored_policy = stored.get('policy')
    rebuilt_policy = rebuilt.get('policy')
    if not isinstance(stored_policy, dict) or not isinstance(rebuilt_policy, dict):
        return False
    if any(
        key not in rebuilt_policy or rebuilt_policy[key] != value
        for key, value in stored_policy.items()
    ):
        return False
    return True


def _source_clipped_gain_proof(event, field, amount, provenance, reading_index,
                               event_start, event_end, option):
    """Validate a source-clipped gain against the report's result readings.

    The crop resolver's serialized decision is diagnostic metadata until it is
    rebuilt from the report-owned readings.  In particular, the ordinary
    ``facts.training_gains`` value may remain the short prefix (for example
    ``6``), so this branch deliberately does not compare that raw field to the
    accepted amount.  The accepted amount comes only from a fresh resolver
    result whose members are bound by physical evidence and timestamp.
    """
    resolution = provenance.get('source_clipping_resolution')
    resolutions = event.get('source_clipped_gain_resolutions')
    if (not isinstance(resolution, dict) or not isinstance(resolutions, dict)
            or resolutions.get(field) != resolution
            or resolution.get('status') != 'accepted'
            or resolution.get('basis') not in _SOURCE_CLIPPED_RESOLUTION_BASES
            or resolution.get('field') != field):
        return None

    group = event.get('result_group')
    if not isinstance(group, dict) or group.get('training_option') != option:
        return None
    interval = group.get('interval_ms')
    if (not isinstance(interval, list) or len(interval) != 2
            or type(interval[0]) is not int or type(interval[1]) is not int
            or interval[0] > interval[1]
            or interval[0] < event_start or interval[1] > event_end):
        return None
    phase_key = f'{option}:{interval[0]}:{interval[1]}'
    if resolution.get('phase_key') != phase_key:
        return None

    # Map every result-group member to exactly one report reading.  A group
    # claim or a matching suffix cannot substitute for this identity join.
    members = group.get('observations')
    if not isinstance(members, list) or not members:
        return None
    member_rows = []
    member_identities = set()
    for member in members:
        if not isinstance(member, dict):
            return None
        timestamp = member.get('source_timestamp_ms')
        if (type(timestamp) is not int or timestamp < interval[0]
                or timestamp > interval[1]
                or member.get('screen') != 'training_result'):
            return None
        member_option = member.get('training_option')
        if member_option is not None and member_option != option:
            return None
        try:
            proofs = evidence_ids(member.get('evidence'))
        except (TypeError, ValueError):
            return None
        # The production group carries one source path per reading.  Refuse
        # a multi-path member rather than choosing one convenient path.
        if len(proofs) != 1:
            return None
        proof = proofs[0]
        identity = (timestamp, proof)
        if identity in member_identities:
            return None
        row = _reading_at(reading_index, proof, timestamp, option)
        if row is None:
            return None
        member_identities.add(identity)
        member_rows.append(row)
    member_times = [row.get('source_timestamp_ms') for row in member_rows]
    if (not member_times or interval != [min(member_times), max(member_times)]):
        return None

    # Every crop observation, including the short prefix diagnostics, must be
    # inside the same bounded result group.  This prevents a copied nested
    # resolution from importing a frame outside the event.
    raw_observations = resolution.get('observations')
    accepted_observations = resolution.get('accepted_observations')
    if (not isinstance(raw_observations, list)
            or not isinstance(accepted_observations, list)
            or not accepted_observations):
        return None
    raw_identities = set()
    for item in raw_observations:
        if not isinstance(item, dict):
            return None
        timestamp = item.get('source_timestamp_ms')
        evidence = item.get('evidence')
        if type(timestamp) is not int or not isinstance(evidence, str):
            return None
        try:
            proof = evidence_ids(evidence)
        except (TypeError, ValueError):
            return None
        identity = (timestamp, proof[0]) if len(proof) == 1 else None
        if identity is None or identity not in member_identities:
            return None
        raw_identities.add(identity)

    expected_amount = resolution.get('accepted_amount')
    if type(expected_amount) is not int or expected_amount < 0 or expected_amount != amount:
        return None
    expected_proof = []
    accepted_identities = set()
    accepted_families: dict[tuple[int, str], set[tuple[str, str]]] = {}
    for item in accepted_observations:
        if not isinstance(item, dict):
            return None
        timestamp = item.get('source_timestamp_ms')
        evidence = item.get('evidence')
        if type(timestamp) is not int or not isinstance(evidence, str):
            return None
        try:
            proof = evidence_ids(evidence)
        except (TypeError, ValueError):
            return None
        if len(proof) != 1:
            return None
        identity = (timestamp, proof[0])
        if identity not in member_identities:
            return None
        family = item.get('crop_family')
        region = item.get('region')
        if (not isinstance(family, str) or not family.strip()
                or not isinstance(region, str) or not region.strip()):
            return None
        family_key = (family, region)
        prior_families = accepted_families.setdefault(identity, set())
        # A localized badge intentionally contributes two crop families on
        # one physical frame (the localized component and the tight gain
        # crop).  Deduplicate that same-frame corroboration for the canonical
        # provenance while rejecting an actual duplicate crop claim.
        if family_key in prior_families:
            return None
        prior_families.add(family_key)
        if identity not in accepted_identities:
            accepted_identities.add(identity)
            expected_proof.append({
                'source_timestamp_ms': timestamp,
                'evidence': evidence,
                'value': expected_amount,
            })
    if not accepted_identities <= raw_identities:
        return None

    # Re-run the source-only resolver using the actual report rows.  No event
    # amount, balance, or frozen expected amount is passed to the resolver.
    from .training_gain_resolution import resolve_source_clipped_gain
    rebuilt = resolve_source_clipped_gain(member_rows, field, phase_key=phase_key)
    if not _source_clipping_resolutions_compatible(resolution, rebuilt):
        return None

    if provenance.get('value') != expected_amount:
        return None
    if provenance.get('observations') != expected_proof:
        return None
    if provenance.get('observation_count') != len(expected_proof):
        return None
    timestamps = [item['source_timestamp_ms'] for item in expected_proof]
    proofs = [item['evidence'] for item in expected_proof]
    try:
        declared_proofs = evidence_ids(provenance.get('evidence'))
    except (TypeError, ValueError):
        return None
    if (provenance.get('source_timestamps_ms') != timestamps
            or declared_proofs != list(dict.fromkeys(proofs))):
        return None
    try:
        field_proofs = evidence_ids(event.get('field_evidence', {}).get(field, []))
    except (TypeError, ValueError):
        return None
    if not field_proofs or not set(proofs) <= set(field_proofs):
        return None
    return dict(evidence=list(dict.fromkeys(proofs)), timestamps=timestamps,
                option=option)


def _direct_gain_proof(event, field, amount, reading_index, event_start, event_end):
    """Validate the independent amount proof for one training field.

    The amount comes only from the transaction worker's canonical direct gain
    provenance.  Result balances and the event's parent span cannot substitute
    for this proof.  Every provenance observation must point back to a reading
    owned by this report at the declared timestamp and option.
    """
    if type(amount) is not int or amount < 0:
        return None
    if type(event_start) is not int or type(event_end) is not int or event_start > event_end:
        return None
    provenance = event.get('direct_gain_provenance', {}).get(field)
    if not isinstance(provenance, dict):
        return None
    basis = provenance.get('basis')
    if basis != _SOURCE_CLIPPED_GAIN_BASIS and basis not in _DIRECT_GAIN_BASES:
        return None
    if basis == 'candidate_only_training_gain' and not _candidate_direct_metadata(
            provenance, field, amount, event.get('training_option'), event_start, event_end):
        return None
    if provenance.get('independent_effect_verification') is False:
        return None
    for key in ('status', 'conflict_state', 'resolution_status'):
        state = provenance.get(key)
        if (isinstance(state, str)
                and (state in ('ambiguous', 'conflicted', 'unresolved', 'unknown')
                     or state.startswith(('ambiguous_', 'conflicted_',
                                          'unresolved_', 'unknown_')))):
            return None
    if provenance.get('value') != amount:
        return None
    option = event.get('training_option')
    if not isinstance(option, str) or not option.strip():
        return None
    if _conflict_applies(event, {'kind': 'stat_change', 'field': field},
                         f'stat_change|{field}|'):
        return None
    if basis == _SOURCE_CLIPPED_GAIN_BASIS:
        return _source_clipped_gain_proof(
            event, field, amount, provenance, reading_index,
            event_start, event_end, option,
        )
    rows = provenance.get('observations')
    if not isinstance(rows, list) or not rows:
        return None
    declared_count = provenance.get('observation_count', len(rows))
    if type(declared_count) is not int or declared_count != len(rows):
        return None
    field_proofs = evidence_ids(event.get('field_evidence', {}).get(field, []))
    if not field_proofs:
        return None
    paths = []
    timestamps = []
    for row in rows:
        if not isinstance(row, dict) or row.get('value') != amount:
            return None
        timestamp = row.get('source_timestamp_ms')
        if type(timestamp) is not int or not event_start <= timestamp <= event_end:
            return None
        try:
            row_proofs = evidence_ids(row.get('evidence'))
        except (TypeError, ValueError):
            return None
        if not row_proofs:
            return None
        for proof in row_proofs:
            if (proof not in field_proofs
                    or _reading_at(reading_index, proof, timestamp, option,
                                   field=field, amount=amount) is None):
                return None
            paths.append(proof)
        timestamps.append(timestamp)

    # Optional denormalized provenance arrays must agree with the canonical
    # observation records; they may not introduce an unbound source path.
    declared_timestamps = provenance.get('source_timestamps_ms')
    if declared_timestamps is not None:
        if (not isinstance(declared_timestamps, list)
                or declared_timestamps != timestamps):
            return None
    declared_proofs = provenance.get('evidence')
    if declared_proofs is not None:
        try:
            if evidence_ids(declared_proofs) != list(dict.fromkeys(paths)):
                return None
        except (TypeError, ValueError):
            return None
    return dict(evidence=list(dict.fromkeys(paths)), timestamps=timestamps,
                option=option)


def _accepted_training_result_phase(event, field, amount, reading_index):
    """Return a validated result phase that follows a direct gain proof.

    ``result_group`` is accepted only as a source-backed group attached to the
    event: its interval must equal the observed member bounds, every member
    must be a report-owned training-result reading with the event option, and a
    member must share the direct amount evidence before a later phase member is
    used.  This prevents a result preview, another option, or an arbitrary
    event window from projecting an applied amount.
    """
    start = event.get('first_seen_ms')
    end = event.get('last_seen_ms')
    direct = _direct_gain_proof(event, field, amount, reading_index, start, end)
    if direct is None:
        return None
    group = event.get('result_group')
    if not isinstance(group, dict) or group.get('training_option') != direct['option']:
        return None
    interval = group.get('interval_ms')
    if (not isinstance(interval, list) or len(interval) != 2
            or type(interval[0]) is not int or type(interval[1]) is not int
            or interval[0] > interval[1] or interval[0] < start or interval[1] > end):
        return None
    members = group.get('observations')
    if not isinstance(members, list) or not members:
        return None
    member_keys = set()
    validated = []
    for member in members:
        if not isinstance(member, dict):
            return None
        timestamp = member.get('source_timestamp_ms')
        if type(timestamp) is not int or not interval[0] <= timestamp <= interval[1]:
            return None
        if member.get('screen') != 'training_result':
            return None
        member_option = member.get('training_option')
        if member_option is not None and member_option != direct['option']:
            return None
        try:
            proofs = evidence_ids(member.get('evidence'))
        except (TypeError, ValueError):
            return None
        if not proofs:
            return None
        for proof in proofs:
            key = (proof, timestamp)
            if key in member_keys:
                return None
            member_keys.add(key)
            reading = _reading_at(reading_index, proof, timestamp, direct['option'])
            if reading is None:
                return None
        validated.append((timestamp, proofs))
    member_timestamps = [timestamp for timestamp, _ in validated]
    if interval != [min(member_timestamps), max(member_timestamps)]:
        return None

    direct_paths = set(direct['evidence'])
    direct_timestamps = set(direct['timestamps'])
    if not any(timestamp in direct_timestamps and direct_paths.intersection(proofs)
               for timestamp, proofs in validated):
        return None
    phase = [(timestamp, proofs) for timestamp, proofs in validated
             if timestamp > max(direct['timestamps'])]
    if not phase:
        return None
    phase_proofs = list(dict.fromkeys(
        proof for _, proofs in phase for proof in proofs))
    if not phase_proofs:
        return None
    return dict(amount_evidence=direct['evidence'], phase_evidence=phase_proofs,
                phase_timestamps=[timestamp for timestamp, _ in phase],
                basis='accepted_training_result_group_after_direct_gain')


_CONFLICT_KINDS = frozenset({
    'condition_acquired', 'condition_removed', 'energy_change', 'energy_status',
    'fan_change', 'friendship_change', 'friendship_status', 'mood_change',
    'mood_status', 'performance_change', 'skill_hint_change', 'stat_change',
})
_CONFLICT_FIELDS = frozenset({
    'speed', 'stamina', 'power', 'guts', 'wit', 'skill_points', 'energy',
})


def _conflict_target(name, effect, key):
    """Classify one conflict target as matching, unrelated, or unscoped."""
    if name in (key, effect.get('field'), effect.get('kind')):
        return True
    if not isinstance(name, str) or not name:
        return None
    if name in _CONFLICT_FIELDS or name in _CONFLICT_KINDS:
        return False
    parts = name.split('|')
    if len(parts) == 3 and parts[0] in _CONFLICT_KINDS:
        return False
    return None


def _conflict_applies(event, effect, key):
    """Scope a conflict to the effect it names, conservatively."""
    conflicts = event.get('conflicting_readings')
    if not conflicts:
        return False
    field = effect.get('field')
    if isinstance(conflicts, dict):
        names = set(conflicts)
        if not names:
            return True
        unknown = False
        for name in names:
            target = _conflict_target(name, effect, key)
            if target is True:
                return True
            if target is None:
                unknown = True
        return unknown
    rows = conflicts if isinstance(conflicts, list) else [conflicts]
    matched = False
    unknown = False
    for row in rows:
        if isinstance(row, dict):
            name = row.get('field')
            target = _conflict_target(name, effect, key)
            if target is True:
                matched = True
            elif target is None:
                unknown = True
        elif isinstance(row, str) and row:
            target = _conflict_target(row, effect, key)
            if target is True:
                matched = True
            elif target is None:
                unknown = True
        else:
            return True
    return matched or unknown


def source_document(document, *, evidence_root=None, amendments=()):
    """Adapt sealed baseline labels with explicit, value-checked amendments.

    Amendments are separate records: label_id, field (top-level label field),
    before, after, reason and evidence. Originals are retained verbatim.
    """
    original_document = deepcopy(document)
    if 'labels' not in document:
        labels = []
        for i, group in enumerate(document.get('groups', [])):
            for j, effect in enumerate(group['effects']):
                proof = effect.get('evidence',group.get('evidence',[]))
                labels.append(dict(id=f'group-{i}/effect-{j}',category='effect',
                    first_seen_ms=effect.get('start_ms',group['start_ms']),last_seen_ms=effect.get('end_ms',group['end_ms']),
                    evidence=[p['path'] if isinstance(p,dict) else p for p in proof],expected=deepcopy(effect)))
        for i, action in enumerate(document.get('actions', [])):
            labels.append(dict(id=f'action-{i}',category='action',first_seen_ms=action['start_ms'],last_seen_ms=action['end_ms'],
                evidence=[p['path'] if isinstance(p,dict) else p for p in action.get('evidence',[])],
                expected={k:deepcopy(v) for k,v in action.items() if k not in ('start_ms','end_ms','evidence')}))
        if not any(k in document for k in ('groups','actions')):
            raise ValueError('Unsupported source reference layout')
        document=dict(document,labels=labels)
    original = deepcopy(document['labels'])
    labels = deepcopy(original)
    by_id = {r['id']: r for r in labels}
    if len(by_id) != len(labels):
        raise ValueError('Duplicate original label IDs')
    for amendment in amendments:
        if not amendment.get('reason') or not amendment.get('evidence'):
            raise ValueError('Source amendment requires a reason and evidence')
        row = by_id[amendment['label_id']]
        key = amendment['field']
        if key not in ('expected', 'first_seen_ms', 'last_seen_ms', 'evidence'):
            raise ValueError('Unsupported amendment field')
        if row.get(key) != amendment['before']:
            raise ValueError('Amendment does not match the original source value')
        row[key] = deepcopy(amendment['after'])
    observations = []
    for row in normalize(labels):
        category = row['category']
        payload, unscored = _payload(category, row['expected'])
        status = 'ungraded' if row.get('adapter_ungraded_reason') or not payload else 'observed'
        observations.append(dict(id=row['id'], category=category if category in ('action','effect','state','purchase') else 'context',
            phase={'action':'committed','effect':'applied','state':'observed','purchase':'committed'}.get(category,'context'),
            start_ms=row['first_seen_ms'], end_ms=row['last_seen_ms'],
            evidence=evidence_ids(row.get('evidence'), evidence_root), payload=payload or {'kind':'unadapted'},
            status=status, source_label_id=row.get('source_label_id',row['id']),
            source_expected_pointer=row.get('source_expected_pointer','/expected'),
            unscored_expected_fields=unscored, adapter_note=row.get('adapter_ungraded_reason')))
    return dict(source_sha256=document['source_sha256'], scope_ms=[document['start_ms'],document['end_ms']],
        reference_complete=False, observations=observations, original_labels=original, original_document=original_document,
        amendments=deepcopy(list(amendments)),
        coverage_note='Existing sampled source labels; unsupported fields are retained as unscored. No exhaustive precision claim.')


def report_document(report):
    data = report['gameplay_tracking']
    times = {}
    for row in data['readings']:
        proof = evidence_ids(row['evidence'])[0]
        if proof in times and times[proof] != row['source_timestamp_ms']:
            raise ValueError('Evidence timestamp conflict')
        times[proof] = row['source_timestamp_ms']
    reading_index = _reading_index(data.get('readings', []))
    events = {e['id']: e for e in data['events']}
    observations = []

    def add(ref, category, phase, payload, start, end, proofs, *, uncertain=False,
            field_timing=False, timing_proofs=None, amount_evidence=None,
            phase_evidence=None, observation_basis=None, uncertainty_fields=None):
        proof = evidence_ids(proofs)
        timing = proof if timing_proofs is None else evidence_ids(timing_proofs)
        seen = [times[p] for p in timing if p in times and start <= times[p] <= end]
        if field_timing:
            # Missing field evidence must not inherit a convenient parent window.
            if not seen:
                uncertain = True
            else:
                start, end = min(seen), max(seen)
        row = dict(id=ref, source_ref=ref, category=category, phase=phase,
                   payload=payload, start_ms=start, end_ms=end, evidence=proof,
                   uncertain=uncertain)
        if amount_evidence is not None:
            row['amount_evidence'] = evidence_ids(amount_evidence)
        if phase_evidence is not None:
            row['phase_evidence'] = evidence_ids(phase_evidence)
        if observation_basis is not None:
            row['observation_basis'] = observation_basis
        if uncertainty_fields is not None:
            row['uncertainty_fields'] = deepcopy(uncertainty_fields)
        observations.append(row)

    # The preview worker persists the helper's accepted envelope before the
    # report is written.  Consume those rows as-is; raw reading facts and
    # candidate collections are deliberately outside this adapter boundary.
    persisted_previews = []
    for collection in ('preview_observations', 'skill_menu_observations'):
        collection_rows = data.get(collection, [])
        if isinstance(collection_rows, dict):
            collection_rows = collection_rows.get('observations', [])
        if isinstance(collection_rows, list):
            persisted_previews.extend((collection, i, row) for i, row in enumerate(collection_rows))
    if isinstance(persisted_previews, list):
        for collection, i, preview in persisted_previews:
            if not isinstance(preview, dict) or preview.get('status') != 'observed':
                continue
            category, phase = preview.get('category'), preview.get('phase')
            payload = preview.get('payload')
            start, end = preview.get('start_ms'), preview.get('end_ms')
            proofs = evidence_ids(preview.get('evidence'))
            if (category not in ('effect', 'purchase') or phase != 'preview'
                    or not isinstance(payload, dict) or not payload
                    or type(start) is not int or type(end) is not int or start > end
                    or not proofs):
                continue
            row = dict(id=f'/gameplay_tracking/{collection}/{i}',
                       source_ref=f'/gameplay_tracking/{collection}/{i}',
                       category=category, phase='preview', payload=deepcopy(payload),
                       start_ms=start, end_ms=end, evidence=proofs,
                       uncertain=preview.get('uncertain') is True)
            if 'uncertainty_fields' in preview:
                row['uncertainty_fields'] = deepcopy(preview['uncertainty_fields'])
            if isinstance(preview.get('occurrence_key'), str) and preview['occurrence_key']:
                row['occurrence_key'] = preview['occurrence_key']
            for key in ('option', 'context', 'source_fact_keys', 'source_effect_indices',
                        'observation_basis'):
                if key in preview:
                    row[key] = deepcopy(preview[key])
            observations.append(row)

    # State workers persist accepted same-frame snapshots separately from
    # reconciliation checkpoints.  Keep their source reading refs and field
    # unknowns intact; checkpoint rows below remain a legacy fallback for
    # coverage the worker did not observe.
    persisted_states = _persisted_rows(data.get('state_observations', []))
    persisted_state_rows = []
    for i, state in enumerate(persisted_states):
        if not isinstance(state, dict):
            continue
        payload = state.get('payload')
        if not isinstance(payload, dict) or not payload.get('values'):
            continue
        payload = deepcopy(payload)
        payload.setdefault('kind', 'state')
        channel = payload.get('channel')
        start, end = state.get('start_ms'), state.get('end_ms')
        if (channel not in ('stats', 'performance') or type(start) is not int
                or type(end) is not int or start > end
                or state.get('status', 'observed') != 'observed'):
            continue
        proofs = _persisted_evidence(state)
        ref = state.get('source_ref', state.get('id'))
        if not isinstance(ref, str) or not ref:
            ref = f'/gameplay_tracking/state_observations/{i}'
        row = dict(id=ref, source_ref=ref, category='state', phase='observed',
                   payload=payload, start_ms=start, end_ms=end, evidence=proofs,
                   uncertain=state.get('uncertain') is True)
        for key in ('source_observations', 'cross_frame_values_merged',
                    'reconciliation_endpoint_inferred', 'observation_basis'):
            if key in state:
                row[key] = deepcopy(state[key])
        observations.append(row)
        persisted_state_rows.append(row)

    # Status badges are observations of the visible current status.  Require
    # the typed value so a kind-only row cannot certify mood/hype semantics.
    persisted_statuses = _persisted_rows(data.get('status_observations', []))
    for i, status in enumerate(persisted_statuses):
        if not isinstance(status, dict):
            continue
        payload = status.get('payload')
        if (not isinstance(payload, dict)
                or payload.get('kind') not in ('mood_status', 'hype_status')
                or not isinstance(payload.get('value'), str)
                or not payload['value'].strip()
                or status.get('status', 'observed') != 'observed'):
            continue
        start, end = status.get('start_ms'), status.get('end_ms')
        if type(start) is not int or type(end) is not int or start > end:
            continue
        ref = status.get('source_ref', status.get('id'))
        if not isinstance(ref, str) or not ref:
            ref = f'/gameplay_tracking/status_observations/{i}'
        row = dict(id=ref, source_ref=ref, category='effect',
                   phase=status.get('phase') if status.get('phase') in (
                       'preview', 'committed', 'applied', 'observed', 'context')
                   else 'observed', payload=deepcopy(payload), start_ms=start,
                   end_ms=end, evidence=_persisted_evidence(status),
                   uncertain=status.get('uncertain') is True)
        for key in ('source_observations', 'observation_basis'):
            if key in status:
                row[key] = deepcopy(status[key])
        if isinstance(status.get('occurrence_key'), str) and status['occurrence_key']:
            row['occurrence_key'] = status['occurrence_key']
        observations.append(row)

    for i, event in enumerate(data['events']):
        ref = f'/gameplay_tracking/events/{i}'
        start, end = event['first_seen_ms'], event['last_seen_ms']
        if event['kind'] == 'training':
            for key, kind, proof_key in (('deltas','stat_change','field_evidence'),
                                          ('performance_deltas','performance_change','performance_evidence')):
                for field, amount in event.get(key, {}).items():
                    amount_proofs = event.get(proof_key, {}).get(field, [])
                    proofs = amount_proofs
                    timing_proofs = amount_proofs
                    amount_evidence = None
                    phase_evidence = None
                    observation_basis = None
                    # A result group supplies phase/location evidence only
                    # after the direct badge proof has established the amount.
                    # It never selects or changes that amount.
                    if key == 'deltas':
                        result_phase = _accepted_training_result_phase(
                            event, field, amount, reading_index)
                        if result_phase is not None:
                            amount_evidence = result_phase['amount_evidence']
                            phase_evidence = result_phase['phase_evidence']
                            proofs = amount_evidence + phase_evidence
                            timing_proofs = phase_evidence
                            observation_basis = result_phase['basis']
                    add(f'{ref}/{key}/{field}','effect','applied',dict(kind=kind,field=field,amount=amount),
                        start,end,proofs,field_timing=True,timing_proofs=timing_proofs,
                        amount_evidence=amount_evidence,phase_evidence=phase_evidence,
                        observation_basis=observation_basis)
        else:
            for j, effect in enumerate(event.get('effects', [])):
                payload, _ = _payload('effect',effect)
                if event.get('context_title'):
                    payload['context_title'] = event['context_title']
                key = '|'.join(str(effect.get(f) or '') for f in ('kind','field','name'))
                effect_proofs, source_bound_proof = (
                    _source_bound_occluded_effect_proofs(event, effect, reading_index)
                )
                add(f'{ref}/effects/{j}','effect','applied',payload,start,end,
                    effect_proofs,field_timing=True,
                    observation_basis=(
                        'source_bound_occluded_receipt_line'
                        if source_bound_proof else None
                    ),
                    uncertain=_conflict_applies(event, effect, key))

    # Dialogue rows are accepted menu observations.  A row without an
    # explicit committed phase remains a preview offer; selected text is not
    # copied into that preview.  The title is used only when the same report
    # evidence frame owns one unambiguous accepted context title.
    for i, choice in enumerate(data.get('dialogue_choices', [])):
        if not isinstance(choice, dict) or choice.get('kind') != 'dialogue_choice':
            continue
        options = choice.get('options')
        if (not isinstance(options, list)
                or any(not isinstance(option, str) or not option.strip() for option in options)):
            continue
        proofs = evidence_ids(choice.get('evidence'))
        start = choice.get('first_seen_ms')
        end = choice.get('last_seen_ms', choice.get('selection_observed_ms', start))
        if type(start) is not int or type(end) is not int or start > end:
            timestamps = [times[path] for path in proofs if path in times]
            if not timestamps:
                continue
            start = end = min(timestamps)
        payload = {'kind': 'event_choice', 'choices': deepcopy(options)}
        name = choice.get('name', choice.get('context_title'))
        title_proofs = []
        if not isinstance(name, str) or not name.strip():
            name, title_proofs = _context_title_for_evidence(data.get('readings', []), proofs)
        if isinstance(name, str) and name.strip():
            payload['name'] = name.strip()
        phase = _canonical_action_phase(choice, default='preview')
        if phase == 'committed':
            selected = choice.get('selected_choice', choice.get('selected_text'))
            if isinstance(selected, str) and selected.strip():
                payload['selected_choice'] = selected.strip()
        add(f'/gameplay_tracking/dialogue_choices/{i}', 'action', phase, payload,
            start, end, proofs + title_proofs, field_timing=True)

    races = {r['id']: r for r in data.get('races',[])}
    for i, action in enumerate(data['turn_action_receipts']):
        start = action['source_timestamp_ms']; end = start
        payload = {'kind':action['kind']}
        proofs = evidence_ids(action.get('evidence'))
        action_phase = _canonical_action_phase(action)
        if action['kind'] == 'training':
            event = events[action['event_id']]
            start, end = event['first_seen_ms'], event['last_seen_ms']
            payload.update(training_option=action['training_option'],result=action.get('training_outcome','unknown'))
            proofs += evidence_ids(action.get('action_identity_evidence'))
            # A training identity is gradeable only after the transaction
            # worker promotes a same-action canonical field.  Preview names,
            # context titles, and neighboring readings are not substitutes.
            if isinstance(action.get('training_name'), str) and action['training_name'].strip():
                payload['training_name'] = action['training_name'].strip()
                proofs += evidence_ids(action.get('training_name_evidence'))
        if action['kind'] == 'infirmary':
            # The accepted Infirmary reconstruction has independently
            # evidenced confirmation/result phases.  Preserve that wider
            # action interval and expose success only when the result proof
            # and an explicit recovery effect are both present.
            confirmation = action.get('confirmation_timestamp_ms')
            result_end = action.get('result_last_seen_ms')
            if type(confirmation) is int and type(result_end) is int and confirmation <= result_end:
                start, end = confirmation, result_end
            recovery = action.get('recovery_effects')
            result_proofs = evidence_ids(action.get('result_evidence'))
            if isinstance(recovery, list) and recovery and result_proofs:
                payload['result'] = 'success'
                proofs += result_proofs
        if action['kind'] == 'race':
            race = races.get(action.get('race_id'),{})
            if not isinstance(race, dict):
                race = {}
            race_name = race.get('name', race.get('race_name'))
            if race_name is not None:
                payload['name'] = race_name
            grade_conflicted = _race_grade_conflicted(race)
            race_grade = _race_grade_from_record(race)
            if race_grade is not None and not grade_conflicted:
                payload['grade'] = race_grade
            if 'placing' in race:
                payload['placing'] = race['placing']
            if race.get('course') is not None:
                payload['course'] = deepcopy(race['course'])
            race_action_basis = None
            if not race:
                fallback, fallback_proofs, race_action_basis = _race_action_fallback_metadata(action)
                payload.update(fallback)
                proofs += fallback_proofs
            result_interval = _explicit_interval(
                action,
                ('result_first_seen_ms', 'completion_first_seen_ms', 'first_seen_ms'),
                ('result_last_seen_ms', 'completion_last_seen_ms', 'last_seen_ms'))
            if result_interval is not None:
                start, end = result_interval
        else:
            race_action_basis = None
        if action['kind'] == 'outing':
            event = events.get(action.get('event_id'), {})
            if action.get('companion') is not None:
                payload['companion'] = deepcopy(action['companion'])
            if event.get('context_title') is not None:
                payload['name'] = event['context_title']
        action_ref = f'/gameplay_tracking/turn_action_receipts/{i}'
        add(action_ref, 'action', action_phase, payload, start, end, proofs,
            observation_basis=race_action_basis)
        if action['kind'] == 'infirmary':
            # An explicit recovery line is a canonical applied effect as well
            # as evidence for the action's success.  Avoid repeating a typed
            # event effect that is already represented above.
            for j, recovery_effect in enumerate(recovery if isinstance(recovery, list) else []):
                effect_payload, _ = _payload('effect', recovery_effect)
                if (effect_payload.get('kind') != 'condition_removed'
                        or not isinstance(effect_payload.get('name'), str)
                        or not effect_payload['name'].strip()):
                    continue
                duplicate = any(
                    row.get('category') == 'effect'
                    and row.get('phase') == 'applied'
                    and row.get('payload', {}).get('kind') == effect_payload.get('kind')
                    and row.get('payload', {}).get('name') == effect_payload.get('name')
                    and set(row.get('evidence', [])) & set(result_proofs)
                    for row in observations)
                if duplicate:
                    continue
                effect_proofs = evidence_ids(recovery_effect.get('evidence')) or result_proofs
                add(f'{action_ref}/recovery_effects/{j}', 'effect', 'applied',
                    effect_payload, start, end, effect_proofs, field_timing=True)
    for i, race in enumerate(data.get('races', [])):
        race_start, race_end = race.get('first_seen_ms'), race.get('last_seen_ms')
        fans_gained = race.get('fans_gained')
        if (type(fans_gained) is int and type(race_start) is int
                and type(race_end) is int and race_start <= race_end):
            fan_payload = {'kind': 'fan_change', 'amount': fans_gained}
            race_name = race.get('name', race.get('race_name'))
            if race_name is not None:
                fan_payload['name'] = race_name
            add(f'/gameplay_tracking/races/{i}/fans_gained', 'effect', 'applied',
                fan_payload, race_start, race_end, race.get('evidence'),
                field_timing=True)
        for j, snapshot in enumerate(race.get('visible_item_reward_snapshots', [])):
            payload = _race_reward_payload(snapshot)
            start, end = snapshot.get('first_seen_ms'), snapshot.get('last_seen_ms')
            if payload is None or type(start) is not int or type(end) is not int or start > end:
                continue
            proofs = snapshot.get('evidence', [])
            if not proofs:
                proofs = [item.get('evidence') for item in snapshot.get('item_observations', [])
                           if isinstance(item, dict) and item.get('evidence')]
            add(f'/gameplay_tracking/races/{i}/visible_item_reward_snapshots/{j}',
                'effect', 'applied', payload, start, end, proofs, field_timing=True)
    for channel, checkpoints, prefix in (
        ('stats', data.get('checkpoints', []), 'checkpoints'),
        ('performance', data.get('performance_accounting', {}).get('checkpoints', []),
         'performance_accounting/checkpoints')):
        for i, checkpoint in enumerate(checkpoints):
            payload = {'channel':channel,'values':deepcopy(checkpoint['values'])}
            checkpoint_proofs = checkpoint.get('supporting_frames', checkpoint.get('evidence'))
            checkpoint_proofs = evidence_ids(checkpoint_proofs)
            if any(_same_state_snapshot(state, payload,
                                        checkpoint['first_seen_ms'],
                                        checkpoint['last_seen_ms'],
                                        checkpoint_proofs)
                   for state in persisted_state_rows):
                continue
            # Main stat caps are accepted only when the cap fact and the
            # checkpoint's values come from the same supporting frame.  The
            # performance channel has no equivalent canonical cap fact here.
            if channel == 'stats':
                caps, cap_evidence = _same_frame_stat_caps(
                    checkpoint, data.get('readings', []))
                if caps:
                    payload['caps'] = caps
                    checkpoint_proofs = list(checkpoint_proofs) + cap_evidence
            payload.update({k:checkpoint[k] for k in ('calendar_text','turns_remaining_to_goal') if k in checkpoint})
            add(f'/gameplay_tracking/{prefix}/{i}','state','observed',payload,
                checkpoint['first_seen_ms'],checkpoint['last_seen_ms'],
                checkpoint_proofs)
    for collection, kind in (('lesson_purchases','lesson'),('skill_purchases','skill')):
        for i, purchase in enumerate(data.get(collection,[])):
            payload = {'kind':kind}
            if kind == 'lesson':
                payload.update(name=purchase.get('name'),cost=deepcopy(purchase.get('performance_cost')))
            else:
                payload, balance_proofs = _skill_purchase_payload(
                    purchase, reading_index=reading_index)
                spent = purchase.get('spent_skill_points')
                if type(spent) is int and spent >= 0:
                    payload['cost'] = {'skill_points': spent}
            start = purchase.get('source_timestamp_ms',purchase.get('first_seen_ms'))
            proofs = purchase.get('evidence')
            if kind == 'skill':
                proofs = evidence_ids(proofs) + balance_proofs
            add(f'/gameplay_tracking/{collection}/{i}','purchase','committed',payload,start,
                purchase.get('last_seen_ms',start),proofs)
    return dict(source_sha256=report['source']['sha256'], auxiliary_log_used=data.get('auxiliary_log_used'),
                observations=observations)
