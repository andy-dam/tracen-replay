"""Read bounded receipt windows more densely, with no expected labels as OCR input."""
import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from .vision import NeuralReader,parse
from .pipeline import decode_frames,frame_scale,PipelineError
from .layout import current as current_layout
from .full_recording import save_json
from .proof_writer import save_while
from .worker_memory import frame_done


_SOURCE_FRAME_KEYS = ('source_frame_sha256', 'source_frame_id')


def _nonempty_text(value):
    return isinstance(value, str) and bool(value.strip())


def _model_provenance(value):
    """Return a stable model namespace, or ``None`` for an unbound row."""
    if _nonempty_text(value):
        return value.strip()
    if not isinstance(value, Mapping) or not value:
        return None
    if not all(_nonempty_text(key) and _nonempty_text(item) for key, item in value.items()):
        return None
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError):
        return None


def _source_binding(row):
    """Extract the source/model envelope used by receipt-row reconciliation.

    ``source_timestamp_ms`` is deliberately absent here.  It selects a
    candidate row, but it is not a physical identity: separate decoded frames
    and separate recordings can share a timestamp.  Producers validate the
    hashes before creating rows; this helper only requires that the validated
    namespace is propagated to both rows being reconciled.
    """
    if not isinstance(row, Mapping):
        return None, 'invalid_row'
    source = row.get('source_sha256')
    if not _nonempty_text(source):
        return None, 'missing_source_namespace'
    engine = row.get('engine_fingerprint')
    if not _nonempty_text(engine):
        return None, 'missing_engine_provenance'
    models = _model_provenance(row.get('model_sha256'))
    if models is None:
        return None, 'missing_model_provenance'
    frame_values = {
        key: row.get(key) for key in _SOURCE_FRAME_KEYS
        if _nonempty_text(row.get(key))
    }
    if not frame_values:
        return None, 'missing_source_frame_identity'
    return dict(source=source, engine=engine, models=models, frames=frame_values), None


def _same_source_binding(base, extra):
    """Check that two same-time receipt rows describe one physical source.

    A source-frame digest is stable across the base and dense inspection
    extraction, while the local frame id can differ because each inspection
    window has its own manifest.  A shared digest is stronger than an id; if
    no digest is available, equal ids are required.  This accepts a legitimate
    alternate crop and rejects a coincidental timestamp or a copied identity.
    """
    left, reason = _source_binding(base)
    if left is None:
        return False, 'base_' + reason
    right, reason = _source_binding(extra)
    if right is None:
        return False, 'supplement_' + reason
    if left['source'] != right['source']:
        return False, 'source_namespace_mismatch'
    # The alternate crop may have been produced by a reader revision or a
    # training-specific reader.  Both model envelopes are still mandatory and
    # are retained on the supplemental observation; physical-source binding,
    # rather than model equality, decides whether the rows can be reconciled.
    common = []
    frame_hash = left['frames'].get('source_frame_sha256'), right['frames'].get('source_frame_sha256')
    if all(value is not None for value in frame_hash):
        common.append('source_frame_sha256')
        if frame_hash[0] != frame_hash[1]:
            return False, 'source_frame_identity_mismatch'
    frame_ids = left['frames'].get('source_frame_id'), right['frames'].get('source_frame_id')
    if all(value is not None for value in frame_ids):
        common.append('source_frame_id')
        # Window-local ids legitimately differ between a base recording and a
        # dense inspection.  A matching frame digest is the stronger proof;
        # when no digest is available, equal ids are the only usable proof.
        if frame_hash[0] is None and frame_ids[0] != frame_ids[1]:
            return False, 'source_frame_identity_mismatch'
    if not common:
        return False, 'source_frame_identity_unshared'
    return True, None


def _unresolved_supplement(base, extra, reason):
    """Retain rejected alternate evidence without making its effects active."""
    result = deepcopy(base)
    unresolved = deepcopy(result.get('unresolved_supplemental_receipt_observations', []))
    observation = deepcopy(extra)
    observation['merge_rejection_reason'] = reason
    if observation not in unresolved:
        unresolved.append(observation)
    result['unresolved_supplemental_receipt_observations'] = unresolved
    return result


def _detached_unresolved(extra, reason):
    """Keep an ambiguous supplemental row reviewable but inert downstream."""
    result = deepcopy(extra)
    original_effects = deepcopy(result.get('effects', []))
    result['effects'] = []
    result['screen'] = 'unknown'
    result['unresolved_receipt_observation'] = dict(
        source_timestamp_ms=extra.get('source_timestamp_ms'),
        evidence=extra.get('evidence'),
        effects=original_effects,
        merge_rejection_reason=reason,
        source_sha256=extra.get('source_sha256'),
        source_frame_sha256=extra.get('source_frame_sha256'),
        source_frame_id=extra.get('source_frame_id'),
    )
    return result


def merge(base,extra):
    # Keep duplicate source timestamps visible.  A timestamp alone is not a
    # physical-frame identity, so silently retaining the last row could attach
    # a reread to the wrong source proof.  Extra rows are merged only when one
    # canonical base row owns that timestamp; ambiguous timestamps remain
    # untouched and are carried forward for review.
    rows={}
    for row in base:
        rows.setdefault(row['source_timestamp_ms'],[]).append(row)
    for row in extra:
        if row['screen']!='event_outcome' or not row['effects']:continue
        time=row['source_timestamp_ms'];matches=rows.get(time,[])
        if len(matches)>1:
            # There is no owner to which this reread can safely attach.  Keep
            # its source/effects as inert review evidence instead of silently
            # dropping it or exposing a duplicate transaction.
            rows.setdefault(time, []).append(
                _detached_unresolved(row, 'ambiguous_base_timestamp'))
            continue
        old=matches[0] if matches else None
        if old and old['screen'] not in ('unknown','event_outcome'):continue
        if old:
            bound, reason = _same_source_binding(old, row)
            if not bound:
                rows[time] = [_unresolved_supplement(old, row, reason)]
                continue
            effects=list(old['effects'])
            signatures={json.dumps({k:e.get(k) for k in ('kind','field','name','amount','direction','value')},sort_keys=True) for e in effects}
            for effect in row['effects']:
                key=json.dumps({k:effect.get(k) for k in ('kind','field','name','amount','direction','value')},sort_keys=True)
                if key not in signatures:
                    effects.append(deepcopy(effect));signatures.add(key)
                    continue
                # Preserve a source-bound proof even when the canonical base
                # row already contains the same semantic effect.  Event
                # assembly consumes the canonical effect list; retaining the
                # proof only in supplemental evidence would make a valid
                # adjacent reread invisible to receipt_names.
                proof = effect.get('source_bound_identity_proof')
                if not isinstance(proof, dict):
                    continue
                existing = next(
                    (
                        candidate for candidate in effects
                        if json.dumps({k:candidate.get(k) for k in
                                       ('kind','field','name','amount','direction','value')},
                                      sort_keys=True) == key
                    ),
                    None,
                )
                if isinstance(existing, dict) and not isinstance(
                    existing.get('source_bound_identity_proof'), dict
                ):
                    existing['source_bound_identity_proof'] = deepcopy(proof)
            # A second OCR view is additional evidence, not a replacement for
            # the original title, state, OCR, or unrelated facts.
            facts=deepcopy(old.get('facts',{}))
            for name in ('effect_candidates','animated_stat_candidates','animated_performance_candidates'):
                for candidate in row.get('facts',{}).get(name,[]):
                    if candidate not in facts.setdefault(name,[]):facts[name].append(deepcopy(candidate))
            recovery=row.get('facts',{}).get('numeric_receipt_recovery')
            if recovery:facts['numeric_receipt_recovery']=deepcopy(recovery)
            recovery=row.get('facts',{}).get('occluded_receipt_recovery')
            if recovery:
                prior=facts.get('occluded_receipt_recovery')
                if prior is None:
                    facts['occluded_receipt_recovery']=deepcopy(recovery)
                elif prior != recovery:
                    proofs=facts.setdefault('occluded_receipt_recovery_proofs',[])
                    if isinstance(proofs,list):
                        if prior not in proofs:proofs.append(deepcopy(prior))
                        if recovery not in proofs:proofs.append(deepcopy(recovery))
            supplements=deepcopy(old.get('supplemental_receipt_observations',[]))
            observation={key:deepcopy(row[key]) for key in
                         ('source_timestamp_ms','evidence','effects','facts','ocr','context_title',
                          'source_sha256','source_frame_sha256','source_frame_id',
                          'engine_fingerprint','model_sha256') if key in row}
            if observation not in supplements:supplements.append(observation)
            row=dict(old,effects=effects,facts=facts,supplemental_receipt_observations=supplements)
        if old:
            rows[time]=[row]
        else:
            rows.setdefault(time,[]).append(row)
    return [row for timestamp in sorted(rows) for row in rows[timestamp]]


def _window_setup(source,root,start,end,fps):
    """Validate the window request and return (capture, digest, directory, frames_dir, manifest)."""
    root=Path(root);capture=json.loads((root/'capture.json').read_text(encoding='utf-8'))
    if not 0<=start<end<=capture['source']['duration_ms'] or end-start>5000:raise PipelineError('Use a bounded window of at most five seconds within the source.')
    if not 4<=fps<=60:raise PipelineError('Receipt sampling must be 4 to 60 FPS.')
    with Path(source).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if digest!=capture['source']['sha256']:raise PipelineError('Receipt source mismatch.')
    directory=root/'receipt-inspection'/f'{start}-{end}-{fps}';directory.mkdir(parents=True,exist_ok=True)
    frames_dir=directory/'frames';frames_dir.mkdir(exist_ok=True);manifest=directory/'frames.json'
    return capture,digest,directory,frames_dir,manifest


def _window_frames(source,capture,directory,frames_dir,manifest,start,end,fps,completed):
    if completed and not manifest.exists():raise PipelineError('Completed receipt window is missing its frame manifest.')
    if manifest.exists():return json.loads(manifest.read_text(encoding='utf-8'))
    # A window is decoded to the analysis's own working frame; the capture
    # a recovery stage keeps for its windows records only the source.
    scale=frame_scale(current_layout(),capture['source']['width'],capture['source']['height'])
    frames=decode_frames(source,frames_dir,start/1000,(end-start)/1000,fps,capture['source'].get('timeline_origin_seconds',0),scale=scale);save_json(manifest,frames)
    return frames


def _may_wrap(raw):
    """Whether wrapped receipt enrichment could add anything: it needs exactly one prefix line."""
    from .receipt_wrapping import prefix
    lines=raw.get('lines')
    return isinstance(lines,list) and sum(prefix(line) is not None for line in lines)==1


def _frame_cache(frame,directory,root,digest,reader,completed):
    """Return the validated raw OCR record for one window frame, creating it when absent."""
    image_path=directory/frame['evidence'];cache=directory/(frame['id']+'.v2.json');proof=directory/(frame['id']+'.png')
    frame_hash=hashlib.sha256(image_path.read_bytes()).hexdigest()
    if completed and (not cache.exists() or not proof.exists()):raise PipelineError('Completed receipt window is missing OCR or gameplay proof.')
    if cache.exists():
        raw=json.loads(cache.read_text(encoding='utf-8'))
        if raw['source_frame_sha256']!=frame_hash:raise PipelineError('Receipt frame changed.')
        if raw.get('source_sha256')!=digest or raw.get('source_timestamp_ms')!=frame['source_timestamp_ms']:
            raise PipelineError('Receipt cache source identity changed.')
        if not raw.get('engine_fingerprint') or not raw.get('model_sha256'):
            raise PipelineError('Receipt OCR cache lacks model provenance.')
        if reader is not None and (raw['engine_fingerprint']!=reader.fingerprint or raw['model_sha256']!=reader.models):
            raise PipelineError('Receipt OCR model changed; use a separate output directory to preserve cached evidence.')
        if not proof.exists():raise PipelineError('Receipt OCR cache is missing its gameplay proof.')
        from .frame_cache import open_rgb,rgb_sha256
        if rgb_sha256(proof)!=raw['gameplay_sha256']:
            raise PipelineError('Receipt gameplay proof changed.')
        # Existing receipt windows may predate the wrapped-friendship
        # source crop.  A fresh producer pass can add that supplemental
        # proof from the already validated gameplay PNG without changing
        # the detector's raw lines.  Replay/reparse leaves the cache
        # untouched and consumes only the persisted proof.  The pane is
        # decoded only for a frame the enrichment could use: one showing
        # exactly one wrapped friendship prefix line.
        if reader is not None and not raw.get('wrapped_receipt_observations') and _may_wrap(raw):
            from .receipt_wrapping import enrich as enrich_wrapped_receipts
            enriched=enrich_wrapped_receipts(raw, open_rgb(proof), reader)
            if enriched.get('wrapped_receipt_observations'):
                raw=enriched
                save_json(cache, raw)
        return raw
    if reader is None:reader=NeuralReader()
    with reader.Image.open(image_path) as image:pane=image.convert('RGB').crop(current_layout().pane)
    saved=save_while(pane,proof)
    try:raw=reader.read(pane)
    finally:saved()
    raw.update(source_timestamp_ms=frame['source_timestamp_ms'],source_frame_sha256=frame_hash,
               source_sha256=digest,source_frame_id=frame['id'],
               evidence=proof.relative_to(root).as_posix())
    from .receipt_wrapping import enrich as enrich_wrapped_receipts
    raw=enrich_wrapped_receipts(raw, pane, reader)
    save_json(cache,raw)
    return raw


def prepare_window(source,root,start,end,fps=16,*,reader=None):
    """Decode and OCR one window's frames into their caches without recording the window.

    This is the parallelizable part of :func:`inspect`; a later sequential
    ``inspect`` call finds the caches and only parses and records.  Returns
    the number of frames in the window.
    """
    root=Path(root)
    capture,digest,directory,frames_dir,manifest=_window_setup(source,root,start,end,fps)
    frames=_window_frames(source,capture,directory,frames_dir,manifest,start,end,fps,False)
    for frame in frames:
        _frame_cache(frame,directory,root,digest,reader,False)
        frame_done()
    return len(frames)


def inspect(source,root,start,end,fps=16,*,reader=None):
    root=Path(root)
    capture,digest,directory,frames_dir,manifest=_window_setup(source,root,start,end,fps)
    path=root/'receipt-inspection.json'
    result=json.loads(path.read_text(encoding='utf-8')) if path.exists() else dict(source_sha256=digest,windows=[],readings=[])
    if result['source_sha256']!=digest:raise PipelineError('Existing receipt inspection belongs to another source.')
    completed=any(w['start_ms']==start and w['end_ms']==end and w['fps']==fps for w in result['windows'])
    frames=_window_frames(source,capture,directory,frames_dir,manifest,start,end,fps,completed)
    for frame in frames:
        raw=_frame_cache(frame,directory,root,digest,reader,completed)
        if not completed:
            parsed = parse(raw)
            parsed.update(
                source_timestamp_ms=raw['source_timestamp_ms'],
                evidence=raw['evidence'],
            )
            # Keep the source/model envelope on the persisted reading index as
            # well as in each raw cache.  Reparse validates the raw cache, but
            # merge callers may receive this index directly in bounded tools.
            for key in ('source_sha256', 'source_frame_sha256',
                        'engine_fingerprint', 'model_sha256', 'gameplay_sha256'):
                if raw.get(key) is not None:
                    parsed[key] = deepcopy(raw[key])
            parsed['source_frame_id'] = frame['id']
            result['readings'].append(parsed)
    if completed:
        # A completed inspection index is an immutable summary.  Reader-aware
        # enrichment above may update its validated raw cache, but this call
        # returns no stale rows and never rewrites the preserved index.  The
        # normal reparse path consumes the enriched raw cache directly.
        return
    result['windows'].append(dict(start_ms=start,end_ms=end,fps=fps,reason='unparsed_receipt_review'))
    save_json(path,result);print(json.dumps(dict(stage='receipt_inspection',start_ms=start,frames=len(frames))),flush=True)
