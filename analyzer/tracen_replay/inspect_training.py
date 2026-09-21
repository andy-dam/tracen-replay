"""Inspect every detected training result at 30 FPS using only gameplay pixels."""
import argparse
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from collections.abc import Mapping
from .pipeline import clear_partial_capture, decode_frames, PipelineError
from .proof_writer import save_while
from .vision import NeuralReader, parse
from .transactions import training_events
from .full_recording import save_json


# Training inspection is a second view of a frame, rather than a replacement
# for the base reading.  These maps contain state that is consumed by the
# accounting pipeline, so they must be reconciled field by field.  In
# particular, a dense inspection often has a readable subset of a result card
# while the base reading has a source-bound numeric supplement for the other
# fields.
_STATE_FACT_MAPS = (
    'result_values',
    'stat_caps',
    'performance_points',
    'performance_caps',
    'training_gains',
)

_CANDIDATE_FACT_MAPS = (
    'result_value_candidates',
    'training_gain_candidates',
    'performance_gain_candidates',
    'result_numerator_candidates',
)

_PROVENANCE_FACT_MAPS = (
    'result_value_provenance',
    'result_snapshot_refinement',
    'stat_cap_provenance',
    'performance_cap_provenance',
    'performance_panel_provenance',
    'training_gain_crop_provenance',
)

# A recording identity is optional on the parsed row for backwards
# compatibility.  When both views declare one, however, silently merging
# different recordings would make an otherwise plausible state transition
# impossible to audit.  Source-frame IDs are checked when present as well;
# the normal cache does not expose them on the parsed row, so this remains a
# strict guard for explicitly annotated callers rather than an assumption
# about image paths.
_SOURCE_ID_KEYS = (
    'source_sha256',
    'source_video_sha256',
    'recording_sha256',
    'source_id',
    'recording_id',
    'source_frame_sha256',
    'source_frame_id',
)


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _row_facts(row):
    return _mapping(row.get('facts')) if isinstance(row, dict) else {}


def _declared_source_ids(row):
    """Return explicit source identities carried by a parsed reading.

    Parsed rows from older caches do not have these fields, hence an absent
    identity does not reject a merge.  Sidecar metadata is inspected because
    source-bound numeric refinements retain the recording identity there even
    though the common row envelope predates that field.
    """

    if not isinstance(row, dict):
        return {}
    containers = [row, _row_facts(row)]
    for name in ('source', 'source_identity', 'provenance'):
        value = row.get(name)
        if isinstance(value, dict):
            containers.append(value)
    for name in ('source', 'source_identity', 'provenance'):
        value = _row_facts(row).get(name)
        if isinstance(value, dict):
            containers.append(value)
    for name in ('numeric_cap_refinement', 'weak_state_recovery'):
        value = row.get(name)
        if isinstance(value, dict):
            containers.append(value)
    result = {}
    for container in containers:
        for key in _SOURCE_ID_KEYS:
            value = container.get(key)
            if isinstance(value, (str, int)) and value not in ('', None):
                result.setdefault(key, value)
    return result


def _source_mismatch(old, new):
    """Return whether two explicitly identified views cannot be same-frame."""

    old_ids = _declared_source_ids(old)
    new_ids = _declared_source_ids(new)
    for key in _SOURCE_ID_KEYS:
        if key in old_ids and key in new_ids and old_ids[key] != new_ids[key]:
            return True
    # If each row has a source identity but they use different keys from the
    # same identity family, there is no safe equality proof.  Do not infer a
    # mismatch when one side is an older row with no declaration.
    recording_keys = frozenset(('source_sha256', 'source_video_sha256',
                                'recording_sha256', 'source_id', 'recording_id'))
    frame_keys = frozenset(('source_frame_sha256', 'source_frame_id'))
    for keys in (recording_keys, frame_keys):
        old_values = {old_ids[key] for key in keys if key in old_ids}
        new_values = {new_ids[key] for key in keys if key in new_ids}
        if old_values and new_values and old_values.isdisjoint(new_values):
            return True
    return False


def _row_phase(row):
    """Classify only explicit preview/result phase markers.

    ``preview_option`` and ``preview_overlay_proven`` are facts that can be
    present on a result card while the overlay is still being inspected; they
    are intentionally not phase markers.  The explicit ``training_preview``
    stats flag and phase fields are the only markers that can block a result
    merge.
    """

    if not isinstance(row, dict):
        return None
    stats = _mapping(row.get('stats'))
    facts = _row_facts(row)
    explicit = row.get('phase')
    if explicit is None:
        explicit = row.get('training_phase')
    if explicit is None:
        for key in ('phase', 'training_phase', 'source_phase'):
            if facts.get(key) is not None:
                explicit = facts[key]
                break
    if stats.get('training_preview') is True or facts.get('preview') is True:
        return 'preview'
    if isinstance(explicit, str):
        marker = explicit.strip().casefold().replace('-', '_').replace(' ', '_')
        if marker in ('preview', 'training_preview', 'projected', 'projection'):
            return 'preview'
        if marker in ('result', 'training_result', 'result_candidate',
                      'training_result_candidate'):
            return 'result'
    screen = row.get('screen')
    if screen == 'training_preview':
        return 'preview'
    if screen in ('training_result', 'training_result_candidate'):
        return 'result'
    return None


def _same_frame_rejection(old, new):
    """Return a stable reason when two same-timestamp views cannot merge."""

    if _source_mismatch(old, new):
        return 'source_identity_mismatch'
    old_phase, new_phase = _row_phase(old), _row_phase(new)
    if old_phase and new_phase and old_phase != new_phase:
        return 'training_phase_mismatch'
    return None


def _field_descriptor(row, origin):
    """Describe the physical reading that supplied a state field."""

    descriptor = {'origin': origin}
    if isinstance(row, dict):
        timestamp = row.get('source_timestamp_ms')
        if type(timestamp) is int:
            descriptor['source_timestamp_ms'] = timestamp
        evidence = row.get('evidence')
        if isinstance(evidence, str) and evidence:
            descriptor['evidence'] = evidence
        source_ids = _declared_source_ids(row)
        if source_ids:
            descriptor['source_identity'] = deepcopy(source_ids)
    return descriptor


def _add_provenance(provenance, map_name, field, row, origin):
    fields = provenance.setdefault(map_name, {})
    if field not in fields:
        fields[field] = _field_descriptor(row, origin)


def _append_unique(target, value):
    if value not in target:
        target.append(deepcopy(value))


def _merge_evidence_map(map_name, old_value, new_value):
    """Merge evidence without allowing a partial view to erase base proof."""

    old_map = old_value if isinstance(old_value, dict) else {}
    new_map = new_value if isinstance(new_value, dict) else {}
    result = deepcopy(old_map)
    for field, value in new_map.items():
        if value is None:
            continue
        if field not in result or result[field] is None:
            result[field] = deepcopy(value)
            continue
        if result[field] == value:
            continue
        # Crop provenance is itself a source-bound view.  The specialized
        # inspection is the more specific evidence for a field, and the old
        # implementation intentionally retained it.  Keep that behavior while
        # making all other evidence maps base-preserving and deterministic.
        if map_name == 'training_gain_crop_provenance':
            result[field] = deepcopy(value)
        elif isinstance(result[field], list) and isinstance(value, list):
            for item in value:
                _append_unique(result[field], item)
        elif isinstance(result[field], dict) and isinstance(value, dict):
            merged = deepcopy(result[field])
            for key, item in value.items():
                if key not in merged or merged[key] is None:
                    merged[key] = deepcopy(item)
            result[field] = merged
        # A scalar disagreement remains diagnostic evidence only.  Canonical
        # resolution is handled by _merge_state_map, never by this fallback.
    return result


def _merge_state_map(map_name, old_value, new_value, old, new,
                     provenance, conflicts, conflict_provenance):
    """Reconcile one state map without cross-frame or residual value filling."""

    old_map = old_value if isinstance(old_value, dict) else {}
    new_map = new_value if isinstance(new_value, dict) else {}
    result = deepcopy(old_map)
    switched = set()
    conflicted = set()
    field_provenance = provenance.setdefault(map_name, {})
    for field in old_map:
        if old_map[field] is not None:
            _add_provenance(provenance, map_name, field, old, 'base')
    for field, value in new_map.items():
        # A null or omitted scalar is a partial inspection, not evidence that
        # the previously accepted value should be erased.
        if value is None:
            continue
        path = field if map_name == 'training_gains' else f'{map_name}.{field}'
        prior_conflict = conflicts.get(path)
        prior_detail = conflict_provenance.get(map_name, {}).get(field)
        if prior_conflict is not None or (
                isinstance(prior_detail, dict)
                and prior_detail.get('status') == 'unresolved'):
            # Once two same-frame views disagree, a later ordinary crop is
            # still another view of that disagreement.  Do not let the
            # absent-field branch below resurrect a canonical scalar.  An
            # explicit adjudication step can clear the conflict record before
            # calling merge again.
            if isinstance(prior_detail, dict):
                candidates = prior_detail.setdefault('additional_candidates', [])
                if value not in candidates:
                    candidates.append(deepcopy(value))
            conflicted.add(field)
            continue
        if field not in old_map or old_map[field] is None:
            result[field] = deepcopy(value)
            field_provenance[field] = _field_descriptor(new, 'supplemental')
            switched.add(field)
            continue
        prior = old_map[field]
        if prior == value:
            _add_provenance(provenance, map_name, field, old, 'base')
            continue
        # Explicit disagreement is quarantined.  Keeping either scalar would
        # let a later transaction stage mistake one OCR view for truth.
        result.pop(field, None)
        field_provenance.pop(field, None)
        # Preserve the historical ``inspection_conflicts`` key for training
        # gains; state maps use their qualified path to avoid collisions.
        conflicts[path] = [deepcopy(prior), deepcopy(value)]
        conflict_provenance.setdefault(map_name, {})[field] = {
            'status': 'unresolved',
            'base': dict(_field_descriptor(old, 'base'), value=deepcopy(prior)),
            'supplemental': dict(_field_descriptor(new, 'supplemental'), value=deepcopy(value)),
        }
        conflicted.add(field)
    # Drop empty per-map provenance so the output remains compact and callers
    # can distinguish no accepted fields from a populated proof map.
    if not field_provenance:
        provenance.pop(map_name, None)
    return result, switched, conflicted


def _prune_field(mapping, field):
    if isinstance(mapping, dict):
        mapping.pop(field, None)


def _prune_result_evidence(facts, row, fields, *, reapply=None):
    """Remove numeric proof that no longer certifies a canonical result field."""

    fields = set(fields)
    if not fields:
        return
    for name in ('result_value_provenance', 'result_snapshot_refinement',
                 'result_numerator_candidates', 'result_value_candidates',
                 'stat_cap_provenance'):
        value = facts.get(name)
        for field in fields:
            _prune_field(value, field)
        if isinstance(value, dict) and not value:
            facts.pop(name, None)

    metadata = row.get('numeric_cap_refinement') if isinstance(row, dict) else None
    if isinstance(metadata, dict):
        refined = deepcopy(metadata)
        refined_fields = refined.get('fields')
        applied = refined.get('applied_fields')
        if isinstance(refined_fields, dict):
            for field in fields:
                refined_fields.pop(f'result_total.{field}', None)
            if not refined_fields:
                refined.pop('fields', None)
        if isinstance(applied, list):
            refined['applied_fields'] = [
                item for item in applied
                if item not in {f'result_total.{field}' for field in fields}
            ]
        if isinstance(refined.get('fields'), dict) and not refined['fields']:
            refined.pop('fields', None)
        if not refined.get('fields'):
            row.pop('numeric_cap_refinement', None)
        else:
            row['numeric_cap_refinement'] = refined

    # ``reapply`` is used when a new canonical field replaces an absent base
    # field: old proof is stale, but proof explicitly carried by the new view
    # remains valid for that new field.
    if isinstance(reapply, dict):
        for name, values in reapply.items():
            if not isinstance(values, dict):
                continue
            target = facts.setdefault(name, {})
            for field, value in values.items():
                target[field] = deepcopy(value)


def _record_rejection(row, reason, supplemental):
    result = dict(row)
    rejections = deepcopy(row.get('inspection_merge_rejections', []))
    entry = {'reason': reason}
    timestamp = supplemental.get('source_timestamp_ms') if isinstance(supplemental, dict) else None
    if type(timestamp) is int:
        entry['source_timestamp_ms'] = timestamp
    evidence = supplemental.get('evidence') if isinstance(supplemental, dict) else None
    if isinstance(evidence, str) and evidence:
        entry['evidence'] = evidence
    if entry not in rejections:
        rejections.append(entry)
    result['inspection_merge_rejections'] = rejections
    return result


def _initialize_merge_provenance(row, origin='supplemental'):
    """Attach per-field source descriptors to a replacement reading."""

    result = deepcopy(row)
    facts = result.setdefault('facts', {})
    if not isinstance(facts, dict):
        facts = {}
        result['facts'] = facts
    provenance = deepcopy(facts.get('inspection_merge_provenance', {}))
    if not isinstance(provenance, dict):
        provenance = {}
    for map_name in _STATE_FACT_MAPS:
        values = facts.get(map_name)
        if not isinstance(values, dict):
            continue
        for field, value in values.items():
            if value is not None:
                _add_provenance(provenance, map_name, field, result, origin)
    if provenance:
        facts['inspection_merge_provenance'] = provenance
    return result


def _merge_numeric_metadata(row, supplemental, fields):
    """Copy only new source-bound numeric fields after a canonical switch."""

    if not fields or not isinstance(supplemental, dict):
        return
    metadata = supplemental.get('numeric_cap_refinement')
    if not isinstance(metadata, dict) or not isinstance(metadata.get('fields'), dict):
        return
    selected = {
        f'result_total.{field}': deepcopy(metadata['fields'][f'result_total.{field}'])
        for field in fields
        if f'result_total.{field}' in metadata['fields']
    }
    if not selected:
        return
    target = deepcopy(row.get('numeric_cap_refinement', {}))
    if not isinstance(target, dict):
        target = {}
    if not isinstance(target.get('fields'), dict):
        target['fields'] = {}
    target['fields'].update(selected)
    prior_applied = target.get('applied_fields')
    if not isinstance(prior_applied, list):
        prior_applied = []
    applied = [item for item in prior_applied if isinstance(item, str)]
    for name in selected:
        if name not in applied:
            applied.append(name)
    target['applied_fields'] = applied
    # The source proof is copied with the accepted field.  These fields are
    # identity metadata, not expected values or an inference from a neighbor.
    for key in ('version', 'stage', 'source_frame_id', 'source_timestamp_ms',
                'evidence', 'evidence_sha256', 'source_frame_evidence',
                'source_frame_sha256', 'gameplay_sha256', 'raw_sha256',
                'source_model_sha256', 'source_engine_fingerprint',
                'refinement_model_sha256', 'refinement_engine_fingerprint'):
        if key not in target and key in metadata:
            target[key] = deepcopy(metadata[key])
    row['numeric_cap_refinement'] = target


def _merge_training_facts(old, supplemental):
    """Merge same-frame training facts and return facts plus merge details."""

    old_facts = _row_facts(old)
    new_facts = _row_facts(supplemental)
    facts = deepcopy(old_facts)
    provenance = deepcopy(old_facts.get('inspection_merge_provenance', {}))
    if not isinstance(provenance, dict):
        provenance = {}
    conflict_provenance = deepcopy(old_facts.get('inspection_conflict_provenance', {}))
    if not isinstance(conflict_provenance, dict):
        conflict_provenance = {}
    conflicts = deepcopy(old.get('inspection_conflicts', {}))
    if not isinstance(conflicts, dict):
        conflicts = {}
    switched_fields = {name: set() for name in _STATE_FACT_MAPS}
    conflicted_fields = {name: set() for name in _STATE_FACT_MAPS}

    for map_name in _STATE_FACT_MAPS:
        old_value = old_facts.get(map_name)
        new_value = new_facts.get(map_name)
        if not isinstance(old_value, dict) and not isinstance(new_value, dict):
            continue
        merged, switched, conflicted = _merge_state_map(
            map_name, old_value, new_value, old, supplemental,
            provenance, conflicts, conflict_provenance)
        switched_fields[map_name].update(switched)
        conflicted_fields[map_name].update(conflicted)
        if merged:
            facts[map_name] = merged
        elif map_name in facts:
            # An explicit conflict can leave a previously populated map with
            # no canonical fields.  Preserve an originally empty map for
            # compatibility, but remove a map emptied by a conflict.
            if (not switched and not conflicted
                    and isinstance(old_value, dict) and not old_value):
                facts[map_name] = deepcopy(old_value)
            else:
                facts.pop(map_name, None)

    # A result numerator disagreement invalidates the paired cap as a
    # canonical result-card snapshot.  Retaining that cap would let state
    # assembly publish a value/cap pair whose numerator has already been
    # quarantined.  A cap-only disagreement is handled independently below,
    # so a readable numerator can still remain when its cap is unresolved.
    value_conflicts = conflicted_fields['result_values']
    if value_conflicts:
        for map_name in ('stat_caps', 'stat_cap_provenance'):
            value = facts.get(map_name)
            if isinstance(value, dict):
                for field in value_conflicts:
                    value.pop(field, None)
                if not value:
                    facts.pop(map_name, None)
        paired_provenance = provenance.get('stat_caps')
        if isinstance(paired_provenance, dict):
            for field in value_conflicts:
                paired_provenance.pop(field, None)
            if not paired_provenance:
                provenance.pop('stat_caps', None)
        # Keep the paired cap quarantined for subsequent same-frame
        # supplements as well, even when the first supplement happened to
        # agree on the cap.  Otherwise a later crop could repopulate a cap
        # beside an unresolved numerator.
        cap_conflicts = conflict_provenance.setdefault('stat_caps', {})
        for field in value_conflicts:
            detail = cap_conflicts.get(field)
            if not (isinstance(detail, dict) and detail.get('status') == 'unresolved'):
                cap_conflicts[field] = {
                    'status': 'unresolved',
                    'reason': 'paired_result_value_conflict',
                    'base': _field_descriptor(old, 'base'),
                    'supplemental': _field_descriptor(supplemental, 'supplemental'),
                }
            conflicted_fields['stat_caps'].add(field)

    # Candidate and proof maps are supplemental evidence.  They are merged
    # without replacing already accepted base evidence; canonical state
    # conflicts are pruned below so these maps cannot reintroduce a value.
    for map_name in (*_CANDIDATE_FACT_MAPS, *_PROVENANCE_FACT_MAPS):
        if map_name not in new_facts:
            continue
        merged = _merge_evidence_map(map_name, old_facts.get(map_name), new_facts.get(map_name))
        if merged:
            facts[map_name] = merged
        elif map_name in facts and not old_facts.get(map_name):
            facts.pop(map_name, None)

    # Keep non-state facts from the base reading and add facts introduced by
    # the dense crop.  This preserves the old context/title and avoids a
    # partial OCR result replacing unrelated state.
    special = frozenset((*_STATE_FACT_MAPS, *_CANDIDATE_FACT_MAPS,
                         *_PROVENANCE_FACT_MAPS, 'inspection_merge_provenance',
                         'inspection_conflict_provenance'))
    for name, value in new_facts.items():
        if name in special or value is None:
            continue
        if name not in old_facts or old_facts[name] is None:
            if value != {} and value != []:
                facts[name] = deepcopy(value)
            continue
        prior = old_facts[name]
        if prior == value:
            continue
        if isinstance(prior, list) and isinstance(value, list):
            merged = deepcopy(prior)
            for item in value:
                _append_unique(merged, item)
            facts[name] = merged
        elif isinstance(prior, dict) and isinstance(value, dict):
            facts[name] = _merge_evidence_map(name, prior, value)
        else:
            # Preserve the base scalar and make disagreement visible to the
            # audit.  A caller can adjudicate it without a silent overwrite.
            path = f'facts.{name}'
            conflicts.setdefault(path, [deepcopy(prior), deepcopy(value)])
            conflict_provenance.setdefault('facts', {})[name] = {
                'status': 'unresolved',
                'base': dict(_field_descriptor(old, 'base'), value=deepcopy(prior)),
                'supplemental': dict(_field_descriptor(supplemental, 'supplemental'),
                                     value=deepcopy(value)),
            }

    # A result-total refinement is valid only for the canonical field it
    # actually supplied.  Clear old proof when a supplemental view supplies a
    # previously absent field, or when either view disagrees, then reapply
    # explicitly carried proof for newly accepted supplemental fields.
    result_changed = switched_fields['result_values'] | switched_fields['stat_caps']
    result_conflicts = conflicted_fields['result_values'] | conflicted_fields['stat_caps']
    stale_result_fields = result_changed | result_conflicts
    new_result_proof = {}
    if stale_result_fields:
        for map_name in ('result_value_provenance', 'result_snapshot_refinement',
                         'result_numerator_candidates'):
            source = new_facts.get(map_name)
            if isinstance(source, dict):
                selected = {field: source[field] for field in stale_result_fields
                            if field in source and field not in result_conflicts}
                if selected:
                    new_result_proof[map_name] = selected
        # Do not carry a disputed result candidate forward even if it was
        # present in the base row or the inspection row.
        _prune_result_evidence(
            facts, {}, stale_result_fields,
            reapply={name: values for name, values in new_result_proof.items()
                     if values},
        )
        # _prune_result_evidence accepts a row for top-level metadata.  The
        # row copy is handled by merge after this function returns.
    # Never leave a field descriptor behind for a field that is no longer a
    # canonical state value.  This also cleans legacy rows whose earlier
    # parser wrote provenance for a null field.
    for map_name in _STATE_FACT_MAPS:
        fields = provenance.get(map_name)
        canonical = facts.get(map_name)
        if not isinstance(fields, dict):
            continue
        for field in list(fields):
            if not isinstance(canonical, dict) or canonical.get(field) is None:
                fields.pop(field, None)
        if not fields:
            provenance.pop(map_name, None)
    if provenance:
        facts['inspection_merge_provenance'] = provenance
    else:
        facts.pop('inspection_merge_provenance', None)
    if conflict_provenance:
        facts['inspection_conflict_provenance'] = conflict_provenance
    else:
        facts.pop('inspection_conflict_provenance', None)
    return facts, conflicts, switched_fields, conflicted_fields


def windows(readings,duration_ms):
    result=[]
    candidates=[dict(first_seen_ms=r['source_timestamp_ms']) for r in readings if r['screen']=='training_result_candidate']
    for event in sorted(training_events(readings)+candidates,key=lambda e:e['first_seen_ms']):
        start=max(0,event['first_seen_ms']-500);end=min(duration_ms,event['first_seen_ms']+1500)
        if result and start<=result[-1]['end_ms']:
            result[-1]['end_ms']=max(result[-1]['end_ms'],end)
        else:result.append(dict(start_ms=start,end_ms=end,reason='training_result_gain_animation'))
    return result


def inspect(source,root,readings):
    root=Path(root);capture=json.loads((root/'capture.json').read_text(encoding='utf-8'))
    with Path(source).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=capture['source']['sha256']:raise PipelineError('Inspection source differs from the base recording.')
    reader=NeuralReader();completed=[]
    for window in windows(readings,capture['source']['duration_ms']):
        directory=root/'training-inspection'/str(window['start_ms'])
        existing=directory/'frames.json'
        if existing.exists():
            prior=json.loads(existing.read_text(encoding='utf-8'))
            if not prior or prior[-1]['source_timestamp_ms']+35<window['end_ms']:
                directory=root/'training-inspection'/f'{window["start_ms"]}-{window["end_ms"]}'
        directory.mkdir(parents=True,exist_ok=True)
        manifest=directory/'frames.json';dest=directory/'frames';dest.mkdir(exist_ok=True)
        if manifest.exists():frames=json.loads(manifest.read_text(encoding='utf-8'))
        else:
            clear_partial_capture(dest)
            frames=decode_frames(source,dest,window['start_ms']/1000,(window['end_ms']-window['start_ms'])/1000,30,capture['source']['timeline_origin_seconds'])
            save_json(manifest,frames)
        for frame in frames:
            cache=directory/(frame['id']+'.v2.json');evidence=directory/(frame['id']+'.png')
            image_path=directory/frame['evidence'];image_digest=hashlib.sha256(image_path.read_bytes()).hexdigest()
            if cache.exists():
                raw=json.loads(cache.read_text(encoding='utf-8'))
                if raw.get('source_frame_sha256')!=image_digest:raise PipelineError('Inspection evidence changed.')
            else:
                with reader.Image.open(image_path) as image:pane=image.convert('RGB').crop((148,0,958,1080))
                saved=save_while(pane,evidence)
                try:raw=reader.read_training(pane)
                finally:saved()
                raw.update(source_timestamp_ms=frame['source_timestamp_ms'],source_frame_sha256=image_digest,
                           evidence=evidence.relative_to(root).as_posix(),source_sha256=digest)
                save_json(cache,raw)
            completed.append(dict(parse(raw),source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
        print(json.dumps(dict(stage='training_inspection',window=window,frames=len(frames))),flush=True)
    save_json(root/'training-inspection.json',dict(source_sha256=digest,readings=completed,windows=windows(readings,capture['source']['duration_ms'])))
    return completed


def merge(base,supplemental):
    """Merge dense training-result views into the base timeline.

    A training inspection is a same-frame supplement.  It can add a field
    that the base view did not resolve, and it can add evidence for an equal
    field, but it cannot overwrite a source-backed scalar.  An explicit
    disagreement is removed from the canonical state and retained as a
    structured conflict so downstream accounting abstains.  Rows at other
    timestamps stay independent; no neighbor or expected-value inference is
    performed here.
    """

    # Keep every base row.  A dict keyed directly by timestamp would silently
    # choose whichever duplicate happened to be last, which is an unsafe
    # source decision for a timeline audit.
    rows = {}
    for item in base:
        if not isinstance(item, dict) or type(item.get('source_timestamp_ms')) is not int:
            continue
        rows.setdefault(item['source_timestamp_ms'], []).append(deepcopy(item))
    for row in supplemental:
        if not isinstance(row, dict) or row.get('screen') != 'training_result':
            continue
        timestamp = row.get('source_timestamp_ms')
        if type(timestamp) is not int:
            continue
        if timestamp not in rows:
            rows[timestamp] = [_initialize_merge_provenance(row)]
            continue

        candidates = rows[timestamp]
        if len(candidates) != 1:
            # Retain every ambiguous base view and quarantine the supplement
            # on each one.  No arbitrary base row becomes canonical.
            rows[timestamp] = [
                _record_rejection(item, 'duplicate_base_timestamp', row)
                for item in candidates
            ]
            continue
        old = candidates[0]
        if old.get('screen') in ('unknown', 'training_result_candidate'):
            rejection = _same_frame_rejection(old, row)
            if rejection:
                rows[timestamp] = [_record_rejection(old, rejection, row)]
            else:
                # Keep the base OCR/evidence identity while using the result
                # parser's richer facts, as the previous replacement path did.
                replacement = _initialize_merge_provenance(row)
                replacement['ocr'] = deepcopy(old.get('ocr', replacement.get('ocr')))
                if old.get('evidence') is not None:
                    replacement['base_evidence'] = old['evidence']
                rows[timestamp] = [replacement]
            continue
        # Specialized samples cannot replace a general event/resource row.
        if old.get('screen') != 'training_result':
            continue
        rejection = _same_frame_rejection(old, row)
        if rejection:
            rows[timestamp] = [_record_rejection(old, rejection, row)]
            continue

        merged = deepcopy(old)
        facts, conflicts, switched_fields, conflicted_fields = _merge_training_facts(old, row)
        # ``_merge_training_facts`` has already pruned stale fact-level proof.
        # Apply the same pruning to the row-level sidecar metadata, then copy
        # new source-bound fields only when they supplied an absent canonical
        # result.  A disputed field never receives supplemental proof.
        changed = switched_fields['result_values'] | switched_fields['stat_caps']
        disputes = conflicted_fields['result_values'] | conflicted_fields['stat_caps']
        stale = changed | disputes
        if stale:
            # Fact-level proof was pruned in _merge_training_facts.  Pass an
            # empty facts mapping here so this second call only updates the
            # row-level numeric sidecar metadata.
            _prune_result_evidence({}, merged, stale)
            _merge_numeric_metadata(merged, row, changed - disputes)
            # ``facts`` may have been updated by the row-level pruning helper
            # only through its own map; keep its canonical result merge.
        elif 'numeric_cap_refinement' not in merged and 'numeric_cap_refinement' in row:
            merged['numeric_cap_refinement'] = deepcopy(row['numeric_cap_refinement'])
        merged['facts'] = facts
        merged['supplemental_evidence'] = row.get('evidence')
        merged['inspection_conflicts'] = conflicts
        rows[timestamp] = [merged]
    return [row for timestamp in sorted(rows) for row in rows[timestamp]]


_LOCALIZED_SIDECAR_KEY = 'training_badge_localization_sidecars'
_LOCALIZED_SIDECAR_SCHEMA = 'tracen-replay/training-inspection-localized-sidecars-v1'
_LOCALIZED_SIDECAR_KEYS = frozenset({
    'path', 'sidecar_sha256', 'source_timestamp_ms', 'evidence',
    'raw_path', 'source_frame', 'source_frame_id',
})
_SOURCE_REFINEMENT_SIDECAR_KEY = 'training_gain_source_refinement_sidecars'
_SOURCE_REFINEMENT_SIDECAR_SCHEMA = 'tracen-replay/training-gain-source-refinement-v1'
_SOURCE_REFINEMENT_SIDECAR_KEYS = frozenset({
    'path', 'sidecar_sha256', 'source_timestamp_ms', 'evidence',
    'raw_path', 'source_frame', 'source_frame_id',
})
_SHA256_RE = re.compile(r'^[0-9a-fA-F]{64}$')


def _safe_inspection_path(root, value, field):
    """Resolve one declared sidecar path without leaving the inspection root."""

    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise PipelineError(f'Training inspection {field} is not a relative path.')
    normalized=value.replace('\\','/')
    posix=PurePosixPath(normalized)
    windows=PureWindowsPath(value)
    if (posix.is_absolute() or windows.is_absolute() or windows.drive
            or windows.root or '..' in posix.parts):
        raise PipelineError(f'Training inspection {field} leaves its source root.')
    root=Path(root).resolve()
    path=root.joinpath(*posix.parts)
    try:
        path.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PipelineError(f'Training inspection {field} leaves its source root.') from exc
    cursor=root
    for part in posix.parts:
        cursor=cursor/part
        if cursor.is_symlink():
            raise PipelineError(f'Training inspection {field} contains a reparse point.')
    return path


def _inspection_hash(path, field):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise PipelineError(f'Training inspection {field} is unreadable.') from exc


def _localized_sidecar_entries(inspection, root):
    """Index explicitly declared localized badge sidecars by source identity.

    Dense inspection caches use a different namespace from the base ``neural``
    cache.  The sidecars therefore have to be declared by the inspection
    manifest and checked against the exact raw/evidence/frame identity before
    ``reparse_inspection`` can consume them.  The helper returns only validated
    paths and never reads an amount from the declaration itself.
    """

    entries=inspection.get(_LOCALIZED_SIDECAR_KEY)
    if entries is None:
        return {}
    if not isinstance(entries,list):
        raise PipelineError(f'Training inspection {_LOCALIZED_SIDECAR_KEY} must be an array.')
    source_sha=inspection.get('source_sha256')
    if not isinstance(source_sha,str) or not _SHA256_RE.fullmatch(source_sha):
        raise PipelineError('Training inspection source identity is missing.')
    result={};paths=set()
    # Entries are validated against the manifest rows below.  Keep a strict
    # source identity index so a sidecar cannot select a convenient row at a
    # duplicate timestamp.
    rows_by_identity={}
    for row in inspection.get('readings',[]):
        if not isinstance(row,dict):
            continue
        timestamp=row.get('source_timestamp_ms');evidence=row.get('evidence')
        if type(timestamp) is int and isinstance(evidence,str):
            rows_by_identity.setdefault((timestamp,evidence),[]).append(row)
    for index,entry in enumerate(entries):
        if not isinstance(entry,Mapping):
            raise PipelineError(f'Training inspection sidecar {index} is not an object.')
        unknown=set(entry)-_LOCALIZED_SIDECAR_KEYS
        if unknown:
            raise PipelineError(f'Training inspection sidecar {index} has unsupported keys.')
        sidecar_path=_safe_inspection_path(root,entry.get('path'),f'{_LOCALIZED_SIDECAR_KEY}[{index}].path')
        sidecar_name=sidecar_path.relative_to(Path(root).resolve()).as_posix()
        if sidecar_name in paths:
            raise PipelineError('Training inspection localized sidecar is duplicated.')
        paths.add(sidecar_name)
        if not sidecar_path.is_file():
            raise PipelineError('Training inspection localized sidecar is missing.')
        declared_hash=entry.get('sidecar_sha256')
        if (not isinstance(declared_hash,str) or not _SHA256_RE.fullmatch(declared_hash)
                or declared_hash.lower()!=_inspection_hash(sidecar_path,'localized sidecar')):
            raise PipelineError('Training inspection localized sidecar hash mismatch.')
        try:
            sidecar=json.loads(sidecar_path.read_text(encoding='utf-8'))
        except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
            raise PipelineError('Training inspection localized sidecar is invalid JSON.') from exc
        if not isinstance(sidecar,Mapping):
            raise PipelineError('Training inspection localized sidecar is not an object.')
        if (sidecar.get('schema_version')!='tracen-replay/training-badge-localization-v1'
                or sidecar.get('version')!=1 or sidecar.get('status')!='localized'):
            raise PipelineError('Training inspection localized sidecar schema is unsupported.')
        metadata=sidecar.get('metadata') if isinstance(sidecar.get('metadata'),Mapping) else sidecar
        timestamp=entry.get('source_timestamp_ms',metadata.get('source_timestamp_ms',sidecar.get('source_timestamp_ms')))
        evidence=entry.get('evidence',metadata.get('evidence',sidecar.get('evidence')))
        if type(timestamp) is not int or not isinstance(evidence,str) or not evidence.strip():
            raise PipelineError('Training inspection localized sidecar source identity is missing.')
        if metadata.get('source_timestamp_ms',sidecar.get('source_timestamp_ms'))!=timestamp:
            raise PipelineError('Training inspection localized sidecar timestamp mismatch.')
        if metadata.get('evidence',sidecar.get('evidence'))!=evidence:
            raise PipelineError('Training inspection localized sidecar evidence mismatch.')
        matching=rows_by_identity.get((timestamp,evidence),[])
        if len(matching)!=1:
            raise PipelineError('Training inspection localized sidecar has no unique reading.')
        evidence_path=_safe_inspection_path(root,evidence,f'{_LOCALIZED_SIDECAR_KEY}[{index}].evidence')
        if not evidence_path.is_file():
            raise PipelineError('Training inspection localized sidecar evidence is missing.')
        raw_path=evidence_path.with_suffix('.v2.json')
        if not raw_path.is_file():
            raw_path=evidence_path.with_suffix('.json')
        if 'raw_path' in entry and entry['raw_path'] is not None:
            declared_raw=_safe_inspection_path(root,entry['raw_path'],f'{_LOCALIZED_SIDECAR_KEY}[{index}].raw_path')
            if declared_raw != raw_path:
                raise PipelineError('Training inspection localized sidecar raw path mismatch.')
        try:
            raw=json.loads(raw_path.read_text(encoding='utf-8'))
        except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
            raise PipelineError('Training inspection localized sidecar raw input is unreadable.') from exc
        from .training_badge_localization import fingerprint
        if raw.get('source_sha256')!=source_sha or raw.get('source_timestamp_ms')!=timestamp or raw.get('evidence')!=evidence:
            raise PipelineError('Training inspection localized sidecar raw source identity mismatch.')
        if sidecar.get('raw_sha256')!=fingerprint(raw):
            raise PipelineError('Training inspection localized sidecar raw hash mismatch.')
        declared_sidecar_source=sidecar.get('source_sha256',metadata.get('source_sha256'))
        if declared_sidecar_source!=source_sha:
            raise PipelineError('Training inspection localized sidecar recording mismatch.')
        source_frame_sha=raw.get('source_frame_sha256')
        source_frame_evidence=metadata.get('source_frame_evidence',sidecar.get('source_frame_evidence'))
        source_frame_id=metadata.get('source_frame_id',sidecar.get('source_frame_id'))
        if not isinstance(source_frame_sha,str) or not _SHA256_RE.fullmatch(source_frame_sha):
            raise PipelineError('Training inspection localized sidecar source frame hash is missing.')
        if not isinstance(source_frame_evidence,str) or not source_frame_evidence.strip():
            raise PipelineError('Training inspection localized sidecar source frame evidence is missing.')
        if 'source_frame' in entry and entry['source_frame'] is not None and entry['source_frame']!=source_frame_evidence:
            raise PipelineError('Training inspection localized sidecar source frame path mismatch.')
        frame_path=_safe_inspection_path(root,source_frame_evidence,f'{_LOCALIZED_SIDECAR_KEY}[{index}].source_frame')
        if not frame_path.is_file() or _inspection_hash(frame_path,'source frame')!=source_frame_sha:
            raise PipelineError('Training inspection localized sidecar source frame mismatch.')
        if 'source_frame_id' in entry and entry['source_frame_id'] is not None and entry['source_frame_id']!=source_frame_id:
            raise PipelineError('Training inspection localized sidecar source frame identity mismatch.')
        # The source frame manifest is in the evidence directory.  Match by
        # stem and timestamp instead of trusting a sidecar's frame label.
        frames_path=evidence_path.parent/'frames.json'
        try:
            frames=json.loads(frames_path.read_text(encoding='utf-8'))
        except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
            raise PipelineError('Training inspection localized sidecar frame manifest is unreadable.') from exc
        matches=[frame for frame in frames if isinstance(frame,Mapping) and frame.get('id')==evidence_path.stem]
        if len(matches)!=1 or matches[0].get('source_timestamp_ms')!=timestamp:
            raise PipelineError('Training inspection localized sidecar frame identity mismatch.')
        frame=matches[0]
        frame_evidence=frame.get('evidence')
        if not isinstance(frame_evidence,str) or not frame_evidence.strip():
            raise PipelineError('Training inspection localized sidecar source frame path is missing.')
        expected_frame_path=evidence_path.parent/Path(frame_evidence)
        try:
            if expected_frame_path.resolve()!=frame_path.resolve():
                raise PipelineError('Training inspection localized sidecar source frame path mismatch.')
        except (OSError,RuntimeError) as exc:
            raise PipelineError('Training inspection localized sidecar source frame path is unreadable.') from exc
        # Validate the decoded gameplay pane and every localized crop before
        # returning the index.  The same loader is called again while the raw
        # row is consumed, binding the sidecar to that exact in-memory object.
        from .training_badge_localization import load as load_training_badges
        try:
            load_training_badges(
                raw,sidecar_path,evidence_path=evidence_path,
                source_frame_path=frame_path,source_frame_id=frame.get('id'),
                source_frame_evidence=source_frame_evidence,original=raw,
            )
        except (TypeError,ValueError,OSError,RuntimeError) as exc:
            raise PipelineError('Training inspection localized sidecar proof is invalid.') from exc
        result_key=(timestamp,evidence)
        if result_key in result:
            raise PipelineError('Training inspection localized sidecar reading identity is duplicated.')
        result[result_key]=dict(
            path=sidecar_path,evidence_path=evidence_path,raw_path=raw_path,
            source_frame_path=frame_path,source_frame_id=frame.get('id'),
            source_frame_evidence=source_frame_evidence,
        )
    return result


def _training_gain_source_refinement_sidecar_entries(inspection, root):
    """Index validated expanded training-gain envelopes by source identity.

    Expanded source refinements are produced from the gameplay pane but may
    be stored outside the dense inspection directory.  The inspection
    manifest is the explicit bridge: each entry names the envelope and all
    source identities needed to validate it.  No amount in an entry is read;
    the envelope is checked against its declared raw row, gameplay pixels,
    decoded frame and sibling frame manifest before it can be attached.
    """

    entries = inspection.get(_SOURCE_REFINEMENT_SIDECAR_KEY)
    if entries is None:
        return {}
    if not isinstance(entries, list):
        raise PipelineError(
            f'Training inspection {_SOURCE_REFINEMENT_SIDECAR_KEY} must be an array.'
        )
    source_sha = inspection.get('source_sha256')
    if not isinstance(source_sha, str) or _SHA256_RE.fullmatch(source_sha) is None:
        raise PipelineError('Training inspection source identity is missing.')

    rows_by_identity = {}
    for row in inspection.get('readings', []):
        if not isinstance(row, Mapping):
            continue
        timestamp = row.get('source_timestamp_ms')
        evidence = row.get('evidence')
        if type(timestamp) is int and timestamp >= 0 and isinstance(evidence, str):
            rows_by_identity.setdefault((timestamp, evidence), []).append(row)

    result = {}
    paths = set()
    from .training_badge_localization import fingerprint as raw_fingerprint
    from .training_gain_source_refinement import (
        SCHEMA as refinement_schema,
        attach_training_gain_refinement,
    )

    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise PipelineError(
                f'Training inspection source refinement sidecar {index} is not an object.'
            )
        unknown = set(entry) - _SOURCE_REFINEMENT_SIDECAR_KEYS
        if unknown:
            raise PipelineError(
                f'Training inspection source refinement sidecar {index} has unsupported keys.'
            )
        sidecar_path = _safe_inspection_path(
            root,
            entry.get('path'),
            f'{_SOURCE_REFINEMENT_SIDECAR_KEY}[{index}].path',
        )
        sidecar_name = sidecar_path.relative_to(Path(root).resolve()).as_posix()
        if sidecar_name in paths:
            raise PipelineError('Training inspection source refinement sidecar is duplicated.')
        paths.add(sidecar_name)
        if not sidecar_path.is_file():
            raise PipelineError('Training inspection source refinement sidecar is missing.')
        declared_hash = entry.get('sidecar_sha256')
        if (
            not isinstance(declared_hash, str)
            or _SHA256_RE.fullmatch(declared_hash) is None
            or declared_hash.casefold() != _inspection_hash(sidecar_path, 'source refinement sidecar')
        ):
            raise PipelineError('Training inspection source refinement sidecar hash mismatch.')
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineError('Training inspection source refinement sidecar is invalid JSON.') from exc
        if not isinstance(sidecar, Mapping):
            raise PipelineError('Training inspection source refinement sidecar is not an object.')
        if sidecar.get('schema_version') != refinement_schema or sidecar.get('version') != 1:
            raise PipelineError('Training inspection source refinement sidecar schema is unsupported.')
        if sidecar.get('preview') is True:
            raise PipelineError('Training inspection source refinement sidecar is a preview.')

        metadata = sidecar.get('metadata')
        if not isinstance(metadata, Mapping):
            metadata = {}
        timestamp = entry.get(
            'source_timestamp_ms',
            metadata.get('source_timestamp_ms', sidecar.get('source_timestamp_ms')),
        )
        evidence = entry.get(
            'evidence', metadata.get('evidence', sidecar.get('evidence'))
        )
        if type(timestamp) is not int or timestamp < 0 or not isinstance(evidence, str) or not evidence.strip():
            raise PipelineError('Training inspection source refinement sidecar source identity is missing.')
        declared_timestamp = metadata.get('source_timestamp_ms', sidecar.get('source_timestamp_ms'))
        declared_evidence = metadata.get('evidence', sidecar.get('evidence'))
        if declared_timestamp != timestamp or declared_evidence != evidence:
            raise PipelineError('Training inspection source refinement sidecar source identity mismatch.')
        matching = rows_by_identity.get((timestamp, evidence), [])
        if len(matching) != 1:
            raise PipelineError('Training inspection source refinement sidecar has no unique reading.')

        evidence_path = _safe_inspection_path(
            root, evidence, f'{_SOURCE_REFINEMENT_SIDECAR_KEY}[{index}].evidence'
        )
        if not evidence_path.is_file():
            raise PipelineError('Training inspection source refinement sidecar evidence is missing.')
        try:
            from PIL import Image

            with Image.open(evidence_path) as image:
                pane = image.convert('RGB')
                if pane.size != (810, 1080):
                    raise ValueError('source evidence is not a gameplay pane')
                evidence_file_sha = _inspection_hash(evidence_path, 'source refinement evidence')
        except (OSError, ValueError) as exc:
            raise PipelineError('Training inspection source refinement sidecar evidence is invalid.') from exc
        declared_evidence_sha = metadata.get('evidence_sha256', sidecar.get('evidence_sha256'))
        if declared_evidence_sha is not None and (
            not isinstance(declared_evidence_sha, str)
            or _SHA256_RE.fullmatch(declared_evidence_sha) is None
            or declared_evidence_sha.casefold() != evidence_file_sha
        ):
            raise PipelineError('Training inspection source refinement sidecar evidence hash mismatch.')

        raw_path = evidence_path.with_suffix('.v2.json')
        if not raw_path.is_file():
            raw_path = evidence_path.with_suffix('.json')
        if 'raw_path' in entry and entry['raw_path'] is not None:
            declared_raw = _safe_inspection_path(
                root, entry['raw_path'], f'{_SOURCE_REFINEMENT_SIDECAR_KEY}[{index}].raw_path'
            )
            if declared_raw != raw_path:
                raise PipelineError('Training inspection source refinement sidecar raw path mismatch.')
        if not raw_path.is_file():
            raise PipelineError('Training inspection source refinement sidecar raw input is missing.')
        try:
            raw = json.loads(raw_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineError('Training inspection source refinement sidecar raw input is unreadable.') from exc
        if not isinstance(raw, Mapping):
            raise PipelineError('Training inspection source refinement sidecar raw input is invalid.')
        if (
            raw.get('source_sha256') != source_sha
            or raw.get('source_timestamp_ms') != timestamp
            or raw.get('evidence') != evidence
        ):
            raise PipelineError('Training inspection source refinement sidecar raw source identity mismatch.')
        declared_raw_sha = sidecar.get('raw_sha256', metadata.get('raw_sha256'))
        if not isinstance(declared_raw_sha, str) or declared_raw_sha != raw_fingerprint(raw):
            raise PipelineError('Training inspection source refinement sidecar raw hash mismatch.')
        declared_sidecar_source = sidecar.get('source_sha256', metadata.get('source_sha256'))
        if declared_sidecar_source != source_sha:
            raise PipelineError('Training inspection source refinement sidecar recording mismatch.')
        gameplay_sha = sidecar.get('gameplay_sha256', metadata.get('gameplay_sha256'))
        if not isinstance(gameplay_sha, str) or _SHA256_RE.fullmatch(gameplay_sha) is None:
            raise PipelineError('Training inspection source refinement sidecar gameplay hash is missing.')
        if raw.get('gameplay_sha256') != gameplay_sha:
            raise PipelineError('Training inspection source refinement sidecar gameplay hash mismatch.')

        source_frame_sha = raw.get('source_frame_sha256')
        source_frame_evidence = metadata.get(
            'source_frame_evidence', sidecar.get('source_frame_evidence')
        )
        source_frame_id = metadata.get('source_frame_id', sidecar.get('source_frame_id'))
        if (
            not isinstance(source_frame_sha, str)
            or _SHA256_RE.fullmatch(source_frame_sha) is None
            or not isinstance(source_frame_evidence, str)
            or not source_frame_evidence.strip()
            or not isinstance(source_frame_id, str)
            or not source_frame_id.strip()
        ):
            raise PipelineError('Training inspection source refinement sidecar source frame identity is missing.')
        declared_entry_frame = entry.get('source_frame')
        if declared_entry_frame is not None and not isinstance(declared_entry_frame, str):
            raise PipelineError('Training inspection source refinement sidecar source frame path is invalid.')
        if 'source_frame_id' in entry and entry['source_frame_id'] is not None and entry['source_frame_id'] != source_frame_id:
            raise PipelineError('Training inspection source refinement sidecar source frame id mismatch.')

        frames_path = evidence_path.parent / 'frames.json'
        try:
            frames = json.loads(frames_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineError('Training inspection source refinement sidecar frame manifest is unreadable.') from exc
        if not isinstance(frames, list):
            raise PipelineError('Training inspection source refinement sidecar frame manifest is invalid.')
        matches = [
            frame for frame in frames
            if isinstance(frame, Mapping) and frame.get('id') == evidence_path.stem
        ]
        if len(matches) != 1 or matches[0].get('source_timestamp_ms') != timestamp:
            raise PipelineError('Training inspection source refinement sidecar frame identity mismatch.')
        frame = matches[0]
        frame_evidence = frame.get('evidence')
        if not isinstance(frame_evidence, str) or not frame_evidence.strip():
            raise PipelineError('Training inspection source refinement sidecar frame path is missing.')
        expected_frame_path = evidence_path.parent / Path(frame_evidence)
        try:
            expected_frame_relative = expected_frame_path.resolve().relative_to(Path(root).resolve()).as_posix()
        except (OSError, RuntimeError, ValueError) as exc:
            raise PipelineError('Training inspection source refinement sidecar frame path is unreadable.') from exc
        # Producer metadata historically used the frame path relative to the
        # inspection window (``frames/000015.jpg``), while manifest entries
        # use a cache-root-relative path.  Accept either spelling, but bind
        # the actual file through the sibling frame manifest before hashing.
        if source_frame_evidence not in (frame_evidence, expected_frame_relative):
            raise PipelineError('Training inspection source refinement sidecar source frame path mismatch.')
        if declared_entry_frame is not None:
            entry_frame_path = _safe_inspection_path(
                root, declared_entry_frame, f'{_SOURCE_REFINEMENT_SIDECAR_KEY}[{index}].source_frame'
            )
            if entry_frame_path.resolve() != expected_frame_path.resolve():
                raise PipelineError('Training inspection source refinement sidecar source frame path mismatch.')
        frame_path = expected_frame_path
        try:
            if expected_frame_path.resolve() != frame_path.resolve():
                raise PipelineError('Training inspection source refinement sidecar frame path mismatch.')
        except (OSError, RuntimeError) as exc:
            raise PipelineError('Training inspection source refinement sidecar frame path is unreadable.') from exc
        if not frame_path.is_file() or _inspection_hash(frame_path, 'source refinement source frame') != source_frame_sha:
            raise PipelineError('Training inspection source refinement sidecar source frame mismatch.')

        try:
            attached = attach_training_gain_refinement(raw, sidecar, pane)
        except (TypeError, ValueError, OSError, RuntimeError) as exc:
            raise PipelineError('Training inspection source refinement sidecar proof is invalid.') from exc
        result_key = (timestamp, evidence)
        if result_key in result:
            raise PipelineError('Training inspection source refinement sidecar reading identity is duplicated.')
        result[result_key] = dict(
            path=sidecar_path,
            evidence_path=evidence_path,
            raw_path=raw_path,
            source_frame_path=frame_path,
            source_frame_id=frame.get('id'),
            source_frame_evidence=source_frame_evidence,
            refinement=sidecar,
            validated_raw=attached,
            source_frame_sha256=source_frame_sha,
        )
    return result


def _project_refined_frame_paths(reading, source_refinement, root):
    """Expose validated window-relative frame proof in the report namespace.

    Raw sidecars keep their original path spelling and hashes. Only the parsed
    provenance is projected, using the exact frame already resolved through
    the inspection manifest, never a basename search or a copied image.
    """
    root = Path(root).resolve()
    source_path = Path(source_refinement['source_frame_path']).resolve()
    try:
        relative = source_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise PipelineError('Refined source frame is outside the inspection root.') from exc
    expected_sha = source_refinement['source_frame_sha256']
    if (not source_path.is_file()
            or _inspection_hash(source_path, 'projected source frame') != expected_sha):
        raise PipelineError('Refined source frame changed before projection.')
    declared = source_refinement['source_frame_evidence']

    def project(value):
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: project(item) for key, item in value.items()}
        if 'source_frame_evidence' in value:
            if (value['source_frame_evidence'] not in (declared, relative)
                    or value.get('source_frame_sha256') != expected_sha):
                raise PipelineError('Refined candidate source frame identity disagrees.')
            result['source_frame_evidence'] = relative
        return result

    return project(reading)


def reparse_inspection(inspection,root):
    result=[];root=Path(root);manifests={}
    localized_sidecars=_localized_sidecar_entries(inspection,root)
    source_refinement_sidecars=_training_gain_source_refinement_sidecar_entries(inspection,root)
    for row in inspection['readings']:
        evidence=root/row['evidence'];path=evidence.with_suffix('.v2.json')
        if not path.exists():path=evidence.with_suffix('.json')
        raw=json.loads(path.read_text(encoding='utf-8'))
        if raw.get('source_sha256')!=inspection['source_sha256'] or raw['source_timestamp_ms']!=row['source_timestamp_ms']:
            raise PipelineError('Training inspection provenance mismatch.')
        if evidence.parent not in manifests:
            manifests[evidence.parent]={f['id']:f for f in json.loads((evidence.parent/'frames.json').read_text(encoding='utf-8'))}
        frame=manifests[evidence.parent].get(evidence.stem)
        if not frame or frame['source_timestamp_ms']!=raw['source_timestamp_ms']:
            raise PipelineError('Inspection capture timestamp mismatch.')
        if hashlib.sha256((evidence.parent/frame['evidence']).read_bytes()).hexdigest()!=raw['source_frame_sha256']:
            raise PipelineError('Inspection source frame changed.')
        proof_hash=hashlib.sha256(evidence.read_bytes()).hexdigest()
        for suffix in ('totals','contrast','performance','awards','receipt'):
            extra_path=path.with_suffix('.'+suffix+'.json')
            if extra_path.exists() and json.loads(extra_path.read_text(encoding='utf-8'))['evidence_sha256']!=proof_hash:
                raise PipelineError('Inspection refinement image changed.')
        # Every legacy dense refinement hashes the immutable raw record.  Apply
        # those refinements first, then attach the localized badge regions with
        # ``original`` as their binding anchor.  Reversing the order makes a
        # valid localized sidecar change the raw fingerprint before
        # ``.totals.json``/``.receipt.json`` can validate it.
        original=raw
        refinement=path.with_suffix('.totals.json')
        if refinement.exists():
            from .refine_results import apply_result_refinement
            raw=apply_result_refinement(raw,json.loads(refinement.read_text(encoding='utf-8')))
        contrast=path.with_suffix('.contrast.json')
        if contrast.exists():
            from .refine_contrast import apply_contrast_refinement
            raw=apply_contrast_refinement(raw,json.loads(contrast.read_text(encoding='utf-8')),original)
        performance=path.with_suffix('.performance.json')
        if performance.exists():
            from .refine_contrast import fingerprint
            extra=json.loads(performance.read_text(encoding='utf-8'))
            if extra['raw_sha256']!=fingerprint(original):raise PipelineError('Performance refinement source mismatch.')
            raw=dict(raw,regions=dict(raw['regions'],**extra['regions']))
        awards=path.with_suffix('.awards.json')
        if awards.exists():
            from .refine_contrast import fingerprint
            extra=json.loads(awards.read_text(encoding='utf-8'))
            if extra['raw_sha256']!=fingerprint(original):raise PipelineError('Award refinement source mismatch.')
            raw=dict(raw,regions=dict(raw['regions'],**extra['regions']))
        receipt=path.with_suffix('.receipt.json')
        if receipt.exists():
            from .refine_receipts import apply
            raw=apply(raw,json.loads(receipt.read_text(encoding='utf-8')))
        source_refinement=source_refinement_sidecars.get((raw['source_timestamp_ms'],raw.get('evidence')))
        if source_refinement is not None:
            from PIL import Image
            from .training_gain_source_refinement import attach_training_gain_refinement
            try:
                with Image.open(source_refinement['evidence_path']) as image:
                    pane=image.convert('RGB')
                raw=attach_training_gain_refinement(raw,source_refinement['refinement'],pane)
            except (TypeError,ValueError,OSError,RuntimeError) as exc:
                raise PipelineError('Training inspection source refinement sidecar could not be attached.') from exc
        localized=localized_sidecars.get((raw['source_timestamp_ms'],raw.get('evidence')))
        if localized is not None:
            from .training_badge_localization import load as load_training_badges
            raw=load_training_badges(
                raw,localized['path'],
                evidence_path=localized['evidence_path'],
                source_frame_path=localized['source_frame_path'],
                source_frame_id=localized['source_frame_id'],
                source_frame_evidence=localized['source_frame_evidence'],
                original=original,
            )
        from .full_recording import parse_receipt_pixels
        source_frame=dict(frame,evidence=(evidence.parent/frame['evidence']).relative_to(root).as_posix())
        parsed = parse_receipt_pixels(raw,root,source_frame,original)
        if source_refinement is not None:
            parsed = _project_refined_frame_paths(parsed, source_refinement, root)
        result.append(dict(parsed,source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    rows=[]
    for path in sorted((args.output/'neural').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf-8'));rows.append(dict(parse(raw),source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
    inspect(args.source,args.output,rows)


if __name__=='__main__':main()
