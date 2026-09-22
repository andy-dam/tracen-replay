"""Resumable full-recording capture and local neural OCR. Processing is not verification."""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,ThreadPoolExecutor,as_completed
import threading
import time
import traceback
from .pipeline import probe,decode_frames,clear_partial_capture,frame_rate,frame_scale,PipelineError
from .proof_writer import save_while
from .worker_memory import frame_done
from .vision import NeuralReader,OCR_DEVICE,parse
from .reconcile import preview_segments
from .gameplay import screen_summary
from .viewer import render
from .transactions import reconstruct
from .recording_verification import audit
from .report_contract import FULL_RECORDING_SCHEMA, ReportContractError, validate as validate_report


def save_json(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    temporary.replace(path)


def validate_output(report, *, require_gameplay=False, source_root=None):
    """Convert contract failures into the pipeline's public error type."""
    try:
        return validate_report(
            report, require_gameplay=require_gameplay, source_root=source_root
        )
    except ReportContractError as exc:
        raise PipelineError(f'Full-recording report contract invalid: {exc}') from exc


def _load_cached_capture(path, source_sha256, fps):
    """Load a cache without rewriting it, adapting only unversioned captures."""
    try:
        raw = path.read_bytes()
        cached = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineError('Cached capture is not valid JSON.') from exc
    if not isinstance(cached, dict):
        raise PipelineError('Cached capture must be a JSON object.')

    # Older full-recording captures predate the contract and have no schema
    # field. They are safe to reuse only after adding the current schema to a
    # deep copy and validating the complete envelope. An explicit schema is
    # never relabeled: the generic validator must reject unknown producers.
    if 'schema_version' not in cached:
        report = copy.deepcopy(cached)
        report['schema_version'] = FULL_RECORDING_SCHEMA
    else:
        report = cached
    validate_output(report)
    if report['source']['sha256'] != source_sha256 or report['sampling']['requested_fps'] != fps:
        raise PipelineError('Capture does not match requested source.')
    if not all((path.parent / frame['evidence']).is_file() for frame in report['frames']):
        raise PipelineError('Captured source frames are missing.')
    return report


def _write_lesson_offer_refinement(root, frame_id, extra):
    """Persist one validated lesson cost reread beside the mutable cache."""

    destination = Path(root) / 'lesson-offer-refinement'
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f'{frame_id}.json'
    if target.exists():
        return target
    try:
        with target.open('x', encoding='utf-8') as stream:
            json.dump(extra, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    except FileExistsError:
        # A concurrent worker may have completed the same frame.  The cache
        # loader will validate whichever immutable sidecar won the race.
        pass
    return target


def _attach_lesson_offer_preview(reading, raw, root, frame, original, *,
                                 source_sha256=None, reader=None):
    """Attach one grouped lesson preview and optionally recover missing costs.

    Cost recovery is deliberately kept in the source adapter/refiner boundary:
    it can fill a missing card slot only after the crop sidecar proves the
    same raw row, gameplay pixels, and captured source frame.  The generic
    ``preview_effects`` projection remains one occurrence per card/effect and
    carries no cost fields, so the richer envelope cannot duplicate previews.
    """

    from .lesson_offer_adapter import (
        LessonOfferSourceError,
        adapt_lesson_offer_frame,
        merge_lesson_offer_cost_refinement,
        refine_lesson_offer_costs,
    )
    from .lesson_offer_refinement import apply as apply_offers

    root = Path(root)
    evidence_path = root / raw['evidence']
    source_frame_path = root / frame['evidence']
    source_row = original if original is not None else raw
    offers = adapt_lesson_offer_frame(
        source_row,
        gameplay_path=evidence_path,
        source_frame_path=source_frame_path,
        source_sha256=source_sha256,
    )
    extra = None
    sidecar_applied = False

    def refinement_unresolved(exc):
        # A legacy lesson sidecar can be source-valid under its historical
        # card detector while the current grouped adapter rejects one of its
        # cards (for example, because a title was temporarily occluded).  Do
        # not project that sidecar into the current offer envelope or let the
        # mismatch abort the whole recording.  The current adapter result is
        # retained with explicit unknown cost provenance; no card is inferred
        # from a neighboring slot or from a balance.
        facts = reading.setdefault('facts', {})
        facts['lesson_offer_cost_refinement'] = {
            'status': 'unresolved',
            'reason': str(exc),
            'source_bound': False,
        }
    if reader is not None and offers.get('offers'):
        sidecar = root / 'lesson-offer-refinement' / f"{frame['id']}.json"
        if sidecar.is_file():
            # A cached sidecar is itself source evidence.  Validate and reuse
            # it rather than running a nondeterministic second OCR pass.
            extra = json.loads(sidecar.read_text(encoding='utf-8'))
            validated_reading = apply_offers(
                reading,
                source_row,
                extra,
                evidence_path,
                source_frame_path=source_frame_path,
            )
            try:
                merged_offers = merge_lesson_offer_cost_refinement(
                    offers,
                    extra.get('cards'),
                    provenance=extra,
                    raw=source_row,
                    gameplay_path=evidence_path,
                    source_frame_path=source_frame_path,
                    source_sha256=source_sha256,
                )
            except LessonOfferSourceError as exc:
                refinement_unresolved(exc)
                # Keep the unrefined adapter envelope and avoid attaching the
                # historical card list from ``validated_reading``.
                extra = None
                sidecar_applied = True
            else:
                reading = validated_reading
                offers = merged_offers
                sidecar_applied = True
        else:
            try:
                offers, extra = refine_lesson_offer_costs(
                    source_row,
                    gameplay_path=evidence_path,
                    source_frame_path=source_frame_path,
                    source_sha256=source_sha256,
                    reader=reader,
                )
            except LessonOfferSourceError as exc:
                # A bounded reread is optional evidence.  Keep the direct adapter
                # result and explicit unknown slots when the crop reader cannot
                # produce a source-bound sidecar.
                facts = reading.setdefault('facts', {})
                facts['lesson_offer_cost_refinement'] = {
                    'status': 'unresolved',
                    'reason': str(exc),
                    'source_bound': False,
                }

    if extra is not None and not sidecar_applied:
        # ``apply`` validates every crop against the source pane and raw row,
        # then exposes the legacy card list for transaction consumers.  Merge
        # the same cards into the rich preview envelope without adding a
        # second generic preview occurrence.
        validated_reading = apply_offers(
            reading,
            source_row,
            extra,
            evidence_path,
            source_frame_path=source_frame_path,
        )
        try:
            merged_offers = merge_lesson_offer_cost_refinement(
                offers,
                extra.get('cards'),
                provenance=extra,
                raw=source_row,
                gameplay_path=evidence_path,
                source_frame_path=source_frame_path,
                source_sha256=source_sha256,
            )
        except LessonOfferSourceError as exc:
            refinement_unresolved(exc)
            extra = None
        else:
            reading = validated_reading
            offers = merged_offers
            _write_lesson_offer_refinement(root, frame['id'], extra)

    if not offers.get('offers'):
        return reading
    facts = reading.setdefault('facts', {})
    facts['lesson_offer_preview'] = offers
    projected = facts.setdefault('preview_effects', [])
    for offer in offers['offers']:
        shared = dict(
            phase='preview',
            source_semantics='lesson_offer',
            source_offer=offer['name'],
            context_title=offer['name'],
            offer_id=offer['offer_id'],
            source_evidence=[offers['source_proof']['evidence']],
        )
        projected.append(dict(kind='lesson', name=offer['name'], **shared))
        projected.extend(dict(effect, **shared) for effect in offer['effects'])
    return reading


def parse_receipt_pixels(raw,root,frame,original=None,*,source_sha256=None,
                         lesson_offer_reader=None):
    """Apply source-bound receipt checks before interpreting an OCR observation."""
    from .receipt_occlusion import annotate_path
    root=Path(root)
    original=raw if original is None else original
    raw=_load_training_gain_refinement(raw,root/frame['evidence'])
    # Bind the caller's recording identity into the OCR envelope before any
    # overlay or song annotation can create a visual proof.  A supplied hash
    # must agree with every already-bound raw envelope; otherwise a proof can
    # carry an arbitrary caller label while remaining detached from the row.
    if source_sha256 is not None:
        if not isinstance(source_sha256,str) or not re.fullmatch(r'[0-9a-f]{64}',source_sha256):
            raise PipelineError('Receipt source recording hash is malformed.')
        for envelope in (raw, original):
            if isinstance(envelope,dict) and envelope.get('source_sha256') is not None \
                    and envelope.get('source_sha256')!=source_sha256:
                raise PipelineError('Receipt source recording hash mismatch.')
        bound_lines=[]
        for line in raw.get('lines',()):
            if not isinstance(line,dict):
                bound_lines.append(line)
                continue
            observation=line.get('visual_symbol_observation')
            if isinstance(observation,dict):
                existing=observation.get('source_sha256')
                if existing is not None and existing!=source_sha256:
                    raise PipelineError('Receipt visual proof source recording hash mismatch.')
                line=dict(line,visual_symbol_observation=dict(
                    observation,source_sha256=source_sha256))
            bound_lines.append(line)
        raw=dict(raw,source_sha256=source_sha256,lines=bound_lines)
    evidence_path=root/raw['evidence']
    checked=annotate_path(raw,evidence_path,original,source_sha256=source_sha256)
    # Result-card glare is a source-pixel fact, so derive it on every normal
    # receipt parse as well as during weak-state sidecar generation.  A cached
    # sidecar is not required for a frame to remain explicitly unknown: the
    # immutable gameplay pane and the raw result-card geometry are sufficient.
    # Clear any carried assertion first so a stale sidecar cannot hide a field
    # when the current source pixels no longer support the proof.
    if checked.get('result_grid') is True:
        checked = dict(checked)
        checked.pop('result_card_occlusion', None)
        if checked.get('gameplay_sha256'):
            from PIL import Image
            from .stat_state_details import read_result_card_occlusion
            with Image.open(evidence_path) as pane:
                occlusion = read_result_card_occlusion(checked, pane)
            if occlusion:
                checked['result_card_occlusion'] = occlusion
    # Race-day totals occupy a lower ribbon. Verify its pixels before allowing
    # the original numeric OCR to supply a state during label/button animation.
    if not checked.get('current_grid'):
        from .race_hub_stats import observation as race_hub_observation, pixel_layout
        candidate=race_hub_observation(checked['lines'],grid_verified=True)
        if candidate:
            from PIL import Image
            with Image.open(evidence_path) as pane:
                if pixel_layout(pane):checked=dict(checked,race_hub_grid_verified=True)
    # Open pixels for suffix recovery only when an eligible receipt exists.
    # Occlusion runs first: a blocked line must not regain confidence here.
    from .receipt_symbols import annotate as annotate_receipt_symbols, has_eligible_receipt
    from .song_symbols import annotate as annotate_song_symbols, has_eligible_song_receipt
    has_receipt_symbol = has_eligible_receipt(checked['lines'])
    has_song_symbol = has_eligible_song_receipt(checked['lines'])
    if has_receipt_symbol or has_song_symbol:
        from PIL import Image
        if checked['source_timestamp_ms']!=frame['source_timestamp_ms']:
            raise PipelineError('Receipt-symbol timestamp differs from capture.')
        proof=dict(source_timestamp_ms=frame['source_timestamp_ms'],evidence=checked['evidence'],
                   gameplay_sha256=checked['gameplay_sha256'],
                   source_frame_sha256=checked['source_frame_sha256'],
                   source_frame_path=root/frame['evidence'],evidence_path=evidence_path,
                   evidence_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest())
        if checked.get('source_sha256') is not None:proof['source_sha256']=checked['source_sha256']
        with Image.open(evidence_path) as pane:
            if has_receipt_symbol:
                checked=annotate_receipt_symbols(checked,pane,proof)
            if has_song_symbol:
                checked=annotate_song_symbols(checked,pane,proof)
        # A terminal OCR letter may be a real part of the song name.  The
        # refinement path is allowed to replace it only after its independent
        # title-crop witness agrees with the source pixels.  Fresh recording
        # analysis has the reader available; cached replay continues to use
        # its already validated sidecar without invoking OCR here.
        if has_song_symbol and lesson_offer_reader is not None:
            from .song_symbol_refinement import _match as song_refinement_match
            if any(song_refinement_match(line) for line in checked.get('lines', ())):
                try:
                    from .song_symbol_refinement import apply as apply_song_refinement, prepare
                    extra = prepare(checked, evidence_path, lesson_offer_reader)
                    if extra.get('observations'):
                        checked = apply_song_refinement(
                            checked, extra, evidence_path, original=checked)
                except (OSError, RuntimeError, TypeError, ValueError, KeyError):
                    # Source refinement is deliberately fail-closed.  A
                    # missing reader/model or an unproven title keeps the raw
                    # OCR line and cannot create a song identity.
                    pass
    reading = parse(checked)
    # Parsed readings are merged with dense inspection views by source-frame
    # identity.  The base OCR envelope carries the frame hash and model
    # provenance; the recording hash is supplied by the source-bound caller.
    # Keep this envelope on the row so timestamp equality alone can never
    # attach an alternate frame to the base event.
    if source_sha256 is not None:
        reading['source_sha256'] = source_sha256
    if checked.get('source_frame_sha256') is not None:
        reading['source_frame_sha256'] = checked['source_frame_sha256']
    if frame.get('id') is not None:
        reading['source_frame_id'] = frame['id']
    for key in ('engine_fingerprint', 'model_sha256', 'gameplay_sha256'):
        if checked.get(key) is not None:
            reading[key] = copy.deepcopy(checked[key])
    if reading.get('screen') == 'skill_selection':
        from .skill_menu_observations import read_pixel_selection
        if checked['source_timestamp_ms'] != frame['source_timestamp_ms']:
            raise PipelineError('Skill-menu timestamp differs from capture.')
        selected = read_pixel_selection(
            checked, gameplay_path=evidence_path,
            source_frame_path=root/frame['evidence'], source_sha256=source_sha256)
        if selected is not None:
            reading['facts']['skill_menu_pixel_selection'] = selected
    if reading.get('screen') == 'lesson_selection':
        reading = _attach_lesson_offer_preview(
            reading,
            raw,
            root,
            frame,
            original,
            source_sha256=source_sha256,
            reader=lesson_offer_reader,
        )
    return reading


_STAGE_T0=None
_CURRENT=dict(report=None,output=None)


def _rss():
    """Working-set size of this process in MB (current and peak) when psutil is installed.

    Memory is reported through psutil only, so the probe is the same on every
    platform; without psutil the progress line carries wall-clock time alone.
    """
    try:
        import psutil
        process=psutil.Process()
        info=process.memory_info()
        result=dict(rss_mb=int(info.rss)//1048576)
        peak=getattr(info,'peak_wset',None)
        if peak is None:
            peak=getattr(info,'peak_rss',None)
        if peak is not None:
            result['peak_rss_mb']=int(peak)//1048576
        return result
    except Exception:
        return {}


def _progress(stage,**fields):
    """Emit one progress line with wall-clock and memory so stages can be costed."""
    global _STAGE_T0
    if _STAGE_T0 is None:_STAGE_T0=time.monotonic()
    fields.update(stage=stage,wall_s=round(time.monotonic()-_STAGE_T0,1),**_rss())
    print(json.dumps(fields),flush=True)


def _guarded(report,name,action,*,fallback):
    """Run an enrichment stage; on failure record it on the report and continue.

    Recognition input (capture, OCR, cached readings) and the report contract
    stay fatal.  Everything that only enriches an already valid report must
    not discard a run: the failure is recorded under ``stage_failures`` with
    its traceback, the stage's block is left out, and processing continues.
    """
    try:
        return action()
    except Exception as exc:
        record=dict(stage=name,error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc()[-4000:])
        if isinstance(report,dict):
            report.setdefault('stage_failures',[]).append(record)
        _progress('stage_failed',name=name,error=record['error'])
        return fallback


def _write_partial_report(exc):
    """Persist whatever was assembled before a fatal error, for diagnosis and reparse."""
    report,output=_CURRENT.get('report'),_CURRENT.get('output')
    if not isinstance(report,dict) or output is None:
        return
    try:
        partial=dict(report)
        partial['stage_failures']=list(report.get('stage_failures') or [])+[dict(
            stage='fatal',error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc()[-4000:])]
        partial['partial']=True
        path=Path(output)/'report-partial.json'
        save_json(path,partial)
        _progress('partial_report_written',path=str(path))
    except Exception:
        pass


def capture(source,root,fps):
    source=Path(source).resolve();root=Path(root)
    info,video,duration,origin=probe(source)
    scale=frame_scale(video['width'],video['height'])
    if not 1<=fps<=8:raise PipelineError('Base sampling must be 1 to 8 FPS.')
    with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    root.mkdir(parents=True,exist_ok=True)
    identity=dict(source_sha256=digest,fps=fps)
    identity_path=root/'identity.json'
    if identity_path.exists() and json.loads(identity_path.read_text(encoding='utf-8'))!=identity:raise PipelineError('Existing output belongs to a different source or sampling configuration.')
    save_json(identity_path,identity)
    if (root/'capture.json').exists():
        return _load_cached_capture(root/'capture.json', digest, fps)
    frames=[]
    for index,start in enumerate(range(0,int(duration)+1,120)):
        length=min(120,duration-start)
        if length<=0:continue
        # A recording a hair longer than a whole number of parts ends in a
        # part shorter than one sampling step. The part before it sampled up
        # to its start, so nothing the sampling rate promises is in it, and
        # the decoder can hand back no frame for it at all.
        if index and length<1/fps:continue
        part=root/f'part-{index:03d}';part.mkdir(exist_ok=True);dest=part/'frames';manifest=part/'frames.json'
        if manifest.exists():rows=json.loads(manifest.read_text(encoding='utf-8'))
        else:
            dest.mkdir(exist_ok=True)
            clear_partial_capture(dest)
            rows=decode_frames(source,dest,start,length,fps,origin,scale=scale)
            for row in rows:
                row['id']=f'part-{index:03d}-'+row['id'];row['evidence']=f'part-{index:03d}/'+row['evidence'];row['clip_timestamp_ms']=row['source_timestamp_ms']
            save_json(manifest,rows)
        frames.extend(rows)
        print(json.dumps(dict(stage='capture',part=index,through_seconds=start+length,frames=len(frames))),flush=True)
    report=dict(schema_version=FULL_RECORDING_SCHEMA,source=dict(name=source.name,sha256=digest,size_bytes=source.stat().st_size,duration_ms=round(duration*1000),timeline_origin_seconds=origin,width=video['width'],height=video['height'],frame_rate=frame_rate(video),codec=video['codec_name']),frames=frames,
                clip=dict(source_start_ms=0,duration_ms=round(duration*1000)),sampling=dict(requested_fps=fps,frame_count=len(frames),method='minimum_interval_on_decoded_pts',guarantees_all_events=False),observations=[],limitations=['Entire source sampled; sampling alone does not establish verification.'])
    validate_output(report)
    save_json(root/'capture.json',report)
    return report


_MANIFEST_FIELDS=('id','source_timestamp_ms','clip_timestamp_ms','source_pts','time_base','evidence')


def rehydrate_frames(source,root,fps):
    """Decode the frame images of a captured run again after they were pruned.

    A run pruned with ``--prune-frames`` keeps ``capture.json`` and each part's
    ``frames.json`` but none of the images, so it can no longer be reparsed:
    every cached OCR observation is checked against the sha256 of its own frame
    file.  Decoding the same source at the same sampling is reproducible, so
    the images can be produced again rather than stored.

    This never rewrites a manifest and never invents a frame.  Each part is
    decoded into a scratch directory and accepted only when it comes back as
    exactly the rows that part already recorded; anything else is a decoder or
    source difference and raises.  Byte equality is not asserted here, because
    the reparse itself rejects any frame whose hash no longer matches the
    observation that was taken from it.
    """
    source=Path(source).resolve();root=Path(root)
    info,video,duration,origin=probe(source)
    with source.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    identity_path=root/'identity.json'
    if not identity_path.exists():
        raise PipelineError('Cannot rehydrate a run without identity.json.')
    identity=json.loads(identity_path.read_text(encoding='utf-8'))
    if identity!=dict(source_sha256=digest,fps=fps):
        raise PipelineError('Existing output belongs to a different source or sampling configuration.')
    parts=0;decoded=0
    for manifest in sorted(root.glob('part-*/frames.json')):
        part=manifest.parent;prefix=part.name
        rows=json.loads(manifest.read_text(encoding='utf-8'))
        destination=part/'frames'
        if all((destination/Path(row['evidence']).name).is_file() for row in rows):
            continue
        index=int(prefix.rsplit('-',1)[1])
        start=index*120;length=min(120,duration-start)
        if length<=0:
            raise PipelineError(f'Part {prefix} lies outside the source duration.')
        scratch=part/'frames.rehydrate'
        if scratch.exists():
            for stale in scratch.iterdir():stale.unlink()
        else:
            scratch.mkdir(parents=True)
        produced=decode_frames(source,scratch,start,length,fps,origin,scale=frame_scale(video['width'],video['height']))
        for row in produced:
            row['id']=f'{prefix}-'+row['id'];row['evidence']=f'{prefix}/'+row['evidence']
            row['clip_timestamp_ms']=row['source_timestamp_ms']
        if [{field:row[field] for field in _MANIFEST_FIELDS} for row in produced]!=[
                {field:row[field] for field in _MANIFEST_FIELDS} for row in rows]:
            raise PipelineError(f'Rehydrated part {prefix} does not reproduce its recorded frames.')
        destination.mkdir(parents=True,exist_ok=True)
        for row in produced:
            name=Path(row['evidence']).name
            (scratch/name).replace(destination/name)
        scratch.rmdir()
        parts+=1;decoded+=len(produced)
        print(json.dumps(dict(stage='rehydrate',part=index,frames=decoded)),flush=True)
    return dict(parts=parts,frames=decoded,crops=_rehydrate_gameplay_crops(root))


def _rehydrate_gameplay_crops(root):
    """Cut each frame's gameplay pane again for the observations that cite it.

    An OCR observation names the pane it was read from, and later stages read
    that pane's pixels again, so a reparse needs the crops as much as the
    frames.  The pane is a fixed window on a frame that has just been proved to
    decode the same way, and the observation recorded the sha256 of its pixels,
    so each crop is checked against that hash before it is written; a pane that
    does not match is a difference this function must not paper over.
    """
    from PIL import Image

    capture=root/'capture.json'
    if not capture.exists():
        raise PipelineError('Cannot rehydrate panes without capture.json.')
    written=0
    for frame in json.loads(capture.read_text(encoding='utf-8')).get('frames',[]):
        observation=root/'neural'/(frame['id']+'.json')
        if not observation.exists():
            continue
        raw=json.loads(observation.read_text(encoding='utf-8'))
        relative=raw.get('evidence')
        if not relative or (root/relative).is_file():
            continue
        source=root/frame['evidence']
        if not source.is_file():
            raise PipelineError(f'Cannot cut a pane from a missing frame: {frame["id"]}')
        with Image.open(source) as image:
            pane=image.convert('RGB').crop((148,0,958,1080))
        recorded=raw.get('gameplay_sha256')
        if recorded and hashlib.sha256(pane.tobytes()).hexdigest()!=recorded:
            raise PipelineError(f'Rehydrated pane does not match its observation: {frame["id"]}')
        (root/relative).parent.mkdir(parents=True,exist_ok=True)
        pane.save(root/relative)
        written+=1
        if written%1000==0:
            print(json.dumps(dict(stage='rehydrate',panes=written)),flush=True)
    return written


_PROCESS_READER=None


def _initialize_process_reader(model_dir):
    global _PROCESS_READER
    _PROCESS_READER=NeuralReader(model_dir)


def _analyze_frame(reader,frame,root,source_sha256):
    """Read one gameplay frame, reusing a fingerprint-bound cache row when present."""
    cache=root/'neural'
    image_path=root/frame['evidence'];digest=hashlib.sha256(image_path.read_bytes()).hexdigest();path=cache/(frame['id']+'.json')
    if path.exists():
        saved=json.loads(path.read_text(encoding='utf-8'))
        if saved.get('engine_fingerprint')==reader.fingerprint and saved.get('source_frame_sha256')==digest:
            parsed=parse_receipt_pixels(
                saved,root,frame,
                source_sha256=source_sha256,
                lesson_offer_reader=reader,
            )
            parsed.update(source_timestamp_ms=saved['source_timestamp_ms'],evidence=saved['evidence'])
            return frame['id'],saved,True,parsed
    with reader.Image.open(image_path) as image:pane=image.convert('RGB').crop((148,0,958,1080))
    relative=f'gameplay/{frame["id"]}.png'
    saved=save_while(pane,root/relative)
    try:raw=reader.read(pane)
    finally:saved()
    raw.update(source_timestamp_ms=frame['source_timestamp_ms'],evidence=relative,source_frame_sha256=digest)
    save_json(path,raw)
    parsed=parse_receipt_pixels(
        raw,root,frame,
        source_sha256=source_sha256,
        lesson_offer_reader=reader,
    )
    parsed.update(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence'])
    return frame['id'],raw,False,parsed


def _analyze_frame_in_process(frame,root,source_sha256):
    try:return _analyze_frame(_PROCESS_READER,frame,Path(root),source_sha256)
    finally:frame_done()


def ocr_pool_for_device(device=None):
    """Executor kind for the OCR stage: DirectML readers need their own processes."""
    return 'process' if (OCR_DEVICE if device is None else device) in ('dml','cuda','coreml') else 'thread'


def analyze_frames(report,root,workers=4,model_dir='.local/models/rapidocr',pool='thread'):
    """Run neural OCR over every captured frame.

    ``pool`` selects the executor: ``thread`` (one reader per thread) or
    ``process`` (one reader per process).  The command line passes
    ``ocr_pool_for_device()`` so DirectML readers, whose sessions crash when
    driven from several threads of one process, each get a process.
    """
    if pool not in ('thread','process'):raise ValueError(f'unsupported OCR pool {pool!r}')
    root=Path(root);cache=root/'neural';cache.mkdir(exist_ok=True);evidence=root/'gameplay';evidence.mkdir(exist_ok=True)
    source_sha256=report.get('source',{}).get('sha256')
    local=threading.local()
    def initialize():local.reader=NeuralReader(model_dir)
    def process(frame):
        try:return _analyze_frame(local.reader,frame,root,source_sha256)
        finally:frame_done()
    started=time.monotonic();completed=0;cached=0;parsed_rows={}
    if pool=='process':
        executor=ProcessPoolExecutor(max_workers=workers,initializer=_initialize_process_reader,initargs=(str(model_dir),))
        submit=lambda frame:executor.submit(_analyze_frame_in_process,frame,str(root),source_sha256)
    else:
        executor=ThreadPoolExecutor(max_workers=workers,initializer=initialize)
        submit=lambda frame:executor.submit(process,frame)
    with executor:
        futures=[submit(frame) for frame in report['frames']]
        try:
            for future in as_completed(futures):
                key,_,hit,parsed=future.result();parsed_rows[key]=parsed;completed+=1;cached+=hit
                if completed%100==0 or completed==len(futures):
                    progress=dict(stage='ocr',processed=completed,total=len(futures),cached=cached,elapsed_seconds=round(time.monotonic()-started),device=OCR_DEVICE,pool=pool)
                    save_json(root/'progress.json',progress);print(json.dumps(progress),flush=True)
        except BaseException:
            # The executor context waits for its queue on exit. Cancel pending
            # frames so one failed observation does not drain an entire video
            # before the worker can report the original failure. Running tasks
            # finish normally, preserving any completed source-bound cache rows.
            for future in futures:
                future.cancel()
            raise
    readings=[]
    for frame in report['frames']:
        readings.append(parsed_rows[frame['id']])
    return readings


def _generate_race_quantity_refinement(root, *, model_dir):
    """Generate source-bound race quantity sidecars for a fresh recording.

    Quantity recovery is a normal post-OCR pass.  The generated sidecars are
    revalidated by ``cached_readings`` before assembly, so accepted quantities
    retain the capture, raw-frame, and source-pixel bindings enforced by the
    refinement module.  Replay/reparse callers intentionally skip generation
    and only consume already sealed sidecars.
    """
    from .race_quantity_refinement import generate

    try:
        return generate(
            root,
            model_dir=model_dir,
            fixed_quantity_windows=True,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PipelineError(f'Race quantity refinement generation failed: {exc}') from exc


def _generate_currency_refinement(root, *, model_dir):
    """Generate the wide-crop and padded lesson-balance sidecars for a fresh recording.

    The detector's box for a lesson balance can start on the currency label
    (``Vi157``) or lose a digit to the cursor; the recognizer's own reading of
    a wide fixed crop, and two padded crops for a slot that is still unread,
    are what ``vision.currencies`` prefers over the tight box.  The sidecars
    are revalidated by ``cached_readings`` on reload; replay and reparse
    callers only consume sidecars that already exist.
    """
    from .refine_currencies import refine

    try:
        return refine(root, model_dir=model_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PipelineError(f'Currency refinement generation failed: {exc}') from exc


def assemble(report,readings,choice_observations=(),race_reward_observations=(),hint_card_observations=None,
             *,committed_choices=(),event_choice_observations=None,source_root=None,learned_reader=None):
    from .inventory import summarize as inventory_summary
    from .vision import enrich_performance_panels
    from .preview_observations import build_preview_observations
    from .status_badges import build_observations as build_status_observations
    from .source_state_observations import build_observations as build_state_observations
    from .skill_menu_observations import build_observations as build_skill_menu_observations
    readings=enrich_performance_panels(readings)
    if learned_reader is not None and source_root is not None:
        # The learned reader's reads are observations on the training result
        # readings; the causal accounting uses one only where it equals a
        # difference the stat bars left unexplained.
        from .learned_reader import THRESHOLD, annotate
        annotate(readings,source_root,learned_reader)
        report['learned_reader']=dict(model_sha256=learned_reader.model_sha256,threshold=THRESHOLD)
    if hint_card_observations is None:
        hint_card_observations=report.get('gameplay_tracking',{}).get('hint_card_observations',[])
    if race_reward_observations:
        from .race_reward_inspection import refine_base_rows
        readings=refine_base_rows(readings,race_reward_observations)
    # These projections are derived from the finalized source readings and
    # remain observational metadata.  They are deliberately persisted beside
    # reconstructed events, without feeding event or causal reconstruction.
    if source_root is None:
        context = report.get('evaluation_context')
        if isinstance(context, dict):
            source_root = context.get('evidence_root')
    preview_kwargs = {} if source_root is None else {'source_root': source_root}
    preview_observations=build_preview_observations(readings, **preview_kwargs)
    status_observations=build_status_observations(readings)
    state_observations=build_state_observations(readings)
    # Targeted boundary probes establish states only. They must not split
    # existing event occurrences or introduce screen/action classifications.
    event_readings=[r for r in readings if r.get('screen')!='boundary_state_recovery']
    stat_rows=[dict(r['stats'],source_timestamp_ms=r['source_timestamp_ms'],evidence=r['evidence']) for r in event_readings]
    spans=screen_summary(event_readings)
    reconstructed = reconstruct(
        event_readings,choice_observations,race_reward_observations,
        hint_card_observations=hint_card_observations,
        source_sha256=report.get('source',{}).get('sha256'))
    if event_choice_observations is not None:
        # The strict source adapter is authoritative even when it abstains.
        # A legacy reconstruction must not resurrect a selection rejected for
        # conflicting physical proofs or a missing commitment witness.
        from .event_choice_adapter import merge_committed_choices
        reconstructed['dialogue_choices'] = merge_committed_choices(committed_choices)
    elif committed_choices:
        # The source adapter is stricter than the historical choice observer.
        # Merge before rebuilding the turn ledger and causal projection so a
        # verified commitment is represented once in every derived view.
        from .event_choice_adapter import merge_committed_choices
        reconstructed['dialogue_choices'] = merge_committed_choices(
            reconstructed.get('dialogue_choices', []), committed_choices)
    report['gameplay_tracking']=dict(method='neural_gameplay_v1',auxiliary_log_used=False,input_region=[148,0,958,1080],readings=readings,
        preview_observations=preview_observations,status_observations=status_observations,
        state_observations=state_observations,
        skill_menu_observations=build_skill_menu_observations(readings),
        **reconstructed,
        screens=spans,training_previews=preview_segments(stat_rows),skill_receipts=[s for s in spans if s['screen']=='skill_receipt'])
    if event_choice_observations is not None:
        report['gameplay_tracking']['event_choice_observations'] = copy.deepcopy(event_choice_observations)
    if hint_card_observations:
        report['gameplay_tracking']['hint_card_observations']=copy.deepcopy(hint_card_observations)
    if race_reward_observations:
        report['gameplay_tracking']['race_reward_observations']=race_reward_observations
    report['recognition']=dict(enabled=True,model='RapidOCR 3.9.2 / PP-OCRv6 detection + English PP-OCRv5 recognition',device=OCR_DEVICE)
    report['gameplay_tracking']['owned_skill_inventory']=inventory_summary(event_readings)
    report['verification']=audit(report)
    from .turn_ledger import build as build_turn_ledger
    report['turn_ledger']=build_turn_ledger(report)
    from .causal_accounting import build as build_causal_accounting
    accounting=_guarded(report,'causal_accounting',lambda: build_causal_accounting(report),fallback=None)
    if accounting is not None:report['causal_accounting']=accounting
    else:report.pop('causal_accounting',None)
    return report


def build_event_choice_observations(readings, root, existing=()):
    """Build the verified menu/commitment envelope before report assembly."""
    from .event_choice_adapter import build_choice_observations

    return build_choice_observations(readings, root, existing=existing)


def _hash_file(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    except OSError as exc:
        raise PipelineError(f'Replay sidecar evidence is unreadable: {path}') from exc
    return digest.hexdigest()


def _contains_reparse(root, path):
    root = Path(root).resolve()
    path = Path(path)
    try:
        relative = path.absolute().relative_to(root.absolute())
    except (OSError, RuntimeError, ValueError):
        return True
    cursor = root
    for component in relative.parts:
        cursor = cursor / component
        try:
            info = cursor.lstat()
        except OSError:
            return True
        if cursor.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            return True
    return False


def _source_frame_for_sidecar(sidecar, *, base_root, frame):
    """Find the cited decoded source frame below the run's root.

    The sidecar's provenance path is metadata only: the actual file is found
    by trying bounded suffixes of it below the root, and is accepted only
    when its declared SHA-256 matches.
    """

    declared = sidecar.get('source_frame_evidence')
    expected_hash = sidecar.get('source_frame_sha256')
    candidates = []
    if isinstance(declared, str) and declared:
        normalized = declared.replace('\\', '/')
        from pathlib import PurePosixPath, PureWindowsPath
        if PurePosixPath(normalized).is_absolute() or PureWindowsPath(declared).is_absolute() \
                or PureWindowsPath(declared).drive or PureWindowsPath(declared).root:
            raise PipelineError('Replay sidecar source-frame evidence must be relative.')
        parts = list(Path(normalized).parts)
        for index in range(len(parts)):
            suffix = Path(*parts[index:])
            candidates.append(Path(base_root) / suffix)
    # A capture frame is a valid source-frame candidate for sidecars generated
    # by a normal fresh run.  Its hash still has to agree with the sidecar.
    if isinstance(frame, dict) and isinstance(frame.get('evidence'), str):
        candidates.append(Path(base_root) / Path(frame['evidence']))
    seen = set()
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
            candidate.relative_to(Path(base_root).resolve())
        except (OSError, RuntimeError, ValueError):
            continue
        if candidate in seen or not candidate.is_file():
            continue
        if _contains_reparse(base_root, candidate):
            continue
        seen.add(candidate)
        if isinstance(expected_hash, str) and _hash_file(candidate) == expected_hash:
            return candidate
    raise PipelineError(
        f'Replay sidecar source-frame proof is missing or does not match '
        f'{sidecar.get("source_frame_id", frame.get("id"))}.'
    )


def _early_sidecars_for_frame(*, base_root, frame, raw_path):
    """Return the run's own source sidecars for one base frame in stable order."""

    entries = []
    seen = set()
    # A run publishes these bounded sidecar kinds directly below its root.
    # The lookup is closed to the registered names and never treats
    # arbitrary JSON as a parser input.
    for name, folder in (('weak_state_recovery', 'weak-state-recovery'),
                         ('numeric_cap_refinement', 'numeric-cap-refinement')):
        sidecar_path = Path(base_root) / folder / f"{frame['id']}.json"
        if sidecar_path.is_file() and sidecar_path.resolve() not in seen:
            try:
                raw_value = json.loads(Path(raw_path).read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PipelineError(f'Cached raw observation is not readable: {raw_path}') from exc
            raw_evidence = raw_value.get('evidence') if isinstance(raw_value, dict) else None
            if not isinstance(raw_evidence, str) or not raw_evidence:
                raise PipelineError(f'Cached raw observation has no evidence path: {raw_path}')
            entries.append((name, sidecar_path.resolve(), raw_path.resolve(),
                            (Path(base_root) / Path(raw_evidence)).resolve()))
            seen.add(sidecar_path.resolve())

    order = {'weak_state_recovery': 0, 'numeric_cap_refinement': 1}
    return sorted(entries, key=lambda item: (order[item[0]], item[1].as_posix()))


def _apply_early_sidecars(raw, *, sidecars, original, frame, base_root):
    """Apply source-bound sidecars before any other working-copy mutation."""

    for name, sidecar_path, raw_entry_path, evidence_path in sidecars:
        expected_raw_path = (Path(base_root) / 'neural' / f"{frame['id']}.json").resolve()
        if raw_entry_path.resolve() != expected_raw_path:
            raise PipelineError(
                f'Replay sidecar {name} raw input is outside the base neural namespace: {raw_entry_path}'
            )
        expected_evidence_path = (Path(base_root) / Path(original.get('evidence', ''))).resolve()
        if evidence_path.resolve() != expected_evidence_path:
            raise PipelineError(f'Replay sidecar {name} evidence path disagrees with the raw input: {sidecar_path}')
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding='utf-8'))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PipelineError(f'Replay sidecar {name} is not readable JSON: {sidecar_path}') from exc
        source_frame_path = _source_frame_for_sidecar(sidecar, base_root=base_root, frame=frame)
        kwargs = dict(
            original=original,
            evidence_path=evidence_path,
            source_frame_path=source_frame_path,
            source_frame_id=frame['id'],
            # The loader binds this metadata string to the sidecar's own
            # provenance namespace; the physical file was resolved and
            # hash-checked separately above.
            source_frame_evidence=sidecar.get('source_frame_evidence'),
        )
        try:
            if name == 'weak_state_recovery':
                from .weak_state_recovery import load as load_weak_state
                raw = load_weak_state(raw, sidecar_path, **kwargs)
            elif name == 'numeric_cap_refinement':
                from .numeric_cap_refinement import load as load_numeric
                raw = load_numeric(raw, sidecar_path, **kwargs)
            else:  # pragma: no cover - the closed set above makes this unreachable
                raise PipelineError(f'No loader is registered for raw sidecar group {name!r}.')
        except PipelineError:
            raise
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise PipelineError(f'Replay sidecar {name} evidence invalid: {sidecar_path}') from exc
    return raw


def _load_training_gain_refinement(raw,source_frame_path):
    """Validate refined OCR crops against the decoded source before parsing."""
    regions=raw.get('regions',{})
    names={name for name in regions if isinstance(name,str)
           and name.startswith('expanded_gain.') and name.count('.')==2}
    envelope=raw.get('training_gain_source_refinement')
    if envelope is None and not names:
        return raw
    if not isinstance(envelope,dict):
        raise PipelineError('Training gain refinement lacks its source record.')
    # Gated/no-result OCR attempts carry diagnostics but no amount candidates.
    if not envelope.get('regions') and not names and envelope.get('status') in ('gated','empty','unresolved'):
        return raw
    from PIL import Image
    from .training_gain_source_refinement import validate_training_gain_refinement
    from .frame_cache import open_rgb
    pane=open_rgb(source_frame_path).crop((148,0,958,1080))
    validation=validate_training_gain_refinement(raw,pane)
    if validation.get('status')!='validated' or names-set(validation['regions']):
        raise PipelineError('Training gain refinement evidence invalid: '+str(validation.get('reason','unlisted region')))
    return dict(raw,regions={**{key:value for key,value in regions.items() if key not in names},
                             **validation['regions']})


def _cached_reading_row(frame,*,root,source_sha256,allow_partial):
    """Reinterpret one frame's immutable OCR observation (see :func:`cached_readings`)."""
    root=Path(root)
    path=root/'neural'/(frame['id']+'.json')
    if not path.exists():
        if allow_partial:return None
        raise PipelineError(f'Missing OCR observation: {frame["id"]}')
    if _contains_reparse(root, path) or _contains_reparse(root, root / Path(frame['evidence'])):
        raise PipelineError(f'Replay source frame or OCR observation contains a reparse point: {frame["id"]}')
    raw=json.loads(path.read_text(encoding='utf-8'))
    if raw.get('source_timestamp_ms')!=frame['source_timestamp_ms']:
        raise PipelineError(f'OCR timestamp mismatch: {frame["id"]}')
    if raw.get('source_frame_sha256')!=hashlib.sha256((root/frame['evidence']).read_bytes()).hexdigest():
        raise PipelineError(f'OCR source evidence mismatch: {frame["id"]}')
    if (not raw.get('model_sha256') or not raw.get('engine_fingerprint')
            or not (root/raw['evidence']).is_file()
            or _contains_reparse(root, root / Path(raw['evidence']))):
        raise PipelineError(f'Incomplete OCR provenance: {frame["id"]}')
    original=raw
    raw=_apply_early_sidecars(
        raw,
        sidecars=_early_sidecars_for_frame(base_root=root, frame=frame, raw_path=path),
        original=original,
        frame=frame,
        base_root=root,
    )
    currency=root/'currency-refinement'/path.name
    if currency.exists():
        from .refine_contrast import fingerprint
        extra=json.loads(currency.read_text(encoding='utf-8'))
        if extra['raw_sha256']!=fingerprint(original) or extra['evidence_sha256']!=hashlib.sha256((root/raw['evidence']).read_bytes()).hexdigest():
            raise PipelineError('Currency refinement evidence changed.')
        raw=dict(raw,regions=dict(raw['regions'],**extra['regions']))
    padding=root/'currency-padding-refinement'/path.name
    if padding.exists():
        from .refine_contrast import fingerprint
        extra=json.loads(padding.read_text(encoding='utf-8'))
        if extra['raw_sha256']!=fingerprint(original) or extra['evidence_sha256']!=hashlib.sha256((root/raw['evidence']).read_bytes()).hexdigest():
            raise PipelineError('Currency padding evidence changed.')
        raw=dict(raw,currency_padding=extra['views'])
    performance_panel=root/'performance-panel-refinement'/path.name
    if performance_panel.exists():
        from .performance_panel_refinement import load as load_performance_panel
        try:
            raw=load_performance_panel(raw,performance_panel,original=original,
                evidence_path=root/original['evidence'],source_frame_path=root/frame['evidence'],
                source_frame_id=frame['id'],source_frame_evidence=frame['evidence'])
        except (ValueError,KeyError,TypeError,OSError) as exc:
            raise PipelineError(f'Performance panel refinement evidence invalid: {exc}') from exc
    status_badge=root/'status-badge-refinement'/path.name
    if status_badge.exists() and 'status_badge_refinement' not in raw:
        from .status_badge_refinement import load as load_status_badge
        try:
            raw=load_status_badge(raw,status_badge,original=original,
                evidence_path=root/original['evidence'],source_frame_id=frame['id'])
        except (ValueError,KeyError,TypeError,OSError) as exc:
            raise PipelineError(f'Status badge refinement evidence invalid: {exc}') from exc
    row=parse_receipt_pixels(raw,root,frame,original,source_sha256=source_sha256)
    if 'performance_panel_refinement' in raw:
        row['performance_panel_refinement']=raw['performance_panel_refinement']
    if 'status_badge_refinement' in raw:
        row['status_badge_refinement']=raw['status_badge_refinement']
    if 'weak_state_recovery' in raw:
        row['weak_state_recovery']=raw['weak_state_recovery']
    if 'preview_recovery' in raw:
        row['preview_recovery']=raw['preview_recovery']
    if 'numeric_cap_refinement' in raw:
        row['numeric_cap_refinement']=raw['numeric_cap_refinement']
    if 'training_badge_localization' in raw:
        row['training_badge_localization']=raw['training_badge_localization']
    race_quantities=root/'race-quantity-refinement'/path.name
    if race_quantities.exists():
        from .race_quantity_refinement import apply as apply_race_quantities
        try:
            row=apply_race_quantities(row,json.loads(race_quantities.read_text(encoding='utf-8')),
                                      raw=original,root=root)
        except ValueError as exc:
            raise PipelineError(f'Race quantity refinement evidence invalid: {exc}') from exc
    offers=root/'lesson-offer-refinement'/path.name
    if offers.exists():
        from .lesson_offer_refinement import apply as apply_offers
        offer_extra=json.loads(offers.read_text(encoding='utf-8'))
        try:
            row=apply_offers(
                row,
                original,
                offer_extra,
                root/raw['evidence'],
                source_frame_path=root/frame['evidence'],
            )
            from .lesson_offer_adapter import merge_lesson_offer_cost_refinement
            facts=copy.deepcopy(row.get('facts',{}))
            preview=facts.get('lesson_offer_preview')
            if isinstance(preview,dict) and preview.get('offers'):
                facts['lesson_offer_preview']=merge_lesson_offer_cost_refinement(
                    preview,
                    offer_extra.get('cards'),
                    provenance=offer_extra,
                    raw=original,
                    gameplay_path=root/original['evidence'],
                    source_frame_path=root/frame['evidence'],
                    source_sha256=source_sha256,
                )
                row=dict(row,facts=facts)
        except (ValueError,KeyError,TypeError,OSError) as exc:
            raise PipelineError(f'Lesson offer refinement evidence invalid: {exc}') from exc
    row.update(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']);return row


def _cached_reading_task(task):
    frame,context=task
    return _cached_reading_row(frame,**context)


def cached_readings(report,root,allow_partial=False,*,workers=1):
    """Reinterpret immutable OCR observations without rerunning models.

    ``workers`` above one reinterprets the frames in a process pool.  Every
    row still comes from the same per-frame function, collected in frame
    order, so the result is identical to the sequential pass; the first
    failing frame in frame order raises exactly as it does sequentially.
    """
    root=Path(root)
    context=dict(root=root,source_sha256=report.get('source',{}).get('sha256'),allow_partial=allow_partial)
    frames=report['frames']
    if workers is None or int(workers)<=1 or len(frames)<2:
        rows=(_cached_reading_row(frame,**context) for frame in frames)
        return [row for row in rows if row is not None]
    readings=[]
    executor=ProcessPoolExecutor(max_workers=min(int(workers),len(frames)))
    try:
        for row in executor.map(_cached_reading_task,[(frame,context) for frame in frames],chunksize=8):
            if row is not None:readings.append(row)
    except BaseException:
        # Surface the first failure in frame order without draining the
        # remaining frames through the pool first.
        executor.shutdown(wait=False,cancel_futures=True)
        raise
    executor.shutdown(wait=True)
    return readings


def _prepare_fresh_hint_cards(readings, root, source_sha256, *, model_dir):
    """Prepare source-bound hint-card evidence during a fresh analysis.

    Hint-card identity recovery may run OCR.  It belongs immediately after
    the automatic-refinement reload of
    the base readings, while every row still comes from the base capture.
    Inspection and recovery namespaces are added later and must never be
    fed to the candidate generator.

    An existing cache is always replayed first, including when the base has
    fewer than two rows, so a stale or foreign artifact remains a fatal
    source-evidence error.  A missing cache with fewer than two rows cannot
    produce the recovery contract's required corroboration and is left
    absent.  Empty recovery results are also left absent so a later improved
    preparation pass is not blocked by the immutable-cache rule.
    """
    from .hint_card_cache import (
        _source_bound_rows,
        load as load_hint_cards,
        save as save_hint_cards,
    )

    evidence_root = Path(root)
    cache_path = evidence_root / "hint-card-recovery.json"
    if cache_path.exists():
        try:
            return load_hint_cards(
                readings,
                evidence_root,
                source_sha256,
                cache_path=cache_path,
            )
        except ValueError as exc:
            raise PipelineError(f"Hint-card cache evidence invalid: {exc}") from exc

    if not isinstance(readings, list) or len(readings) < 2:
        return []

    # ``cached_readings`` deliberately keeps the source-frame identity
    # envelope without duplicating the manifest-derived path.  Identity
    # recovery validates that path before it can inspect the source frame, so
    # derive it through the cache validator's exact capture-manifest bridge.
    # This private view is never merged back into the report and cannot select
    # rows from inspection/recovery namespaces.
    bound_readings = _source_bound_rows(
        readings,
        evidence_root,
        source_sha256=source_sha256,
    )
    if bound_readings is None:
        raise PipelineError("Fresh hint-card source evidence is invalid.")

    from .hint_card_identity import recover

    try:
        candidates = recover(
            bound_readings,
            evidence_root,
            source_sha256=source_sha256,
            model_dir=model_dir,
        )
        if not isinstance(candidates, list) or not candidates:
            return []
        try:
            save_hint_cards(
                cache_path,
                candidates,
                bound_readings,
                evidence_root,
                source_sha256=source_sha256,
            )
        except FileExistsError:
            # Another fresh worker may have completed the same immutable
            # preparation between the existence check and exclusive create.
            pass
        return load_hint_cards(
            bound_readings,
            evidence_root,
            source_sha256,
            cache_path=cache_path,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise PipelineError(f"Fresh hint-card preparation failed: {exc}") from exc


def main():
    """Run the producer; on a fatal error persist a partial report before re-raising."""
    global _STAGE_T0
    _STAGE_T0=time.monotonic()
    _CURRENT.update(report=None,output=None)
    try:
        return _main_body()
    except Exception as exc:
        _write_partial_report(exc)
        raise


def _main_body():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--fps',type=float,default=4);parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--dense-workers',type=int,default=None,help='worker processes for the dense re-read OCR passes (default: workers - 1, at least 1)')
    parser.add_argument('--model-dir',type=Path,default=Path('.local/models/rapidocr'))
    parser.add_argument('--reparse-only',action='store_true',help='Verify cached source observations and apply the current parser without OCR.')
    parser.add_argument('--no-viewer',action='store_true',help='Do not write the standalone viewer page (index.html).')
    parser.add_argument('--max-auto-refinement-frames',type=int,default=512,
                        help='Maximum weak performance-panel frames to reread automatically.')
    parser.add_argument('--max-status-refinement-frames',type=int,default=512,
                        help='Maximum weak status-badge frames to reread automatically.')
    parser.add_argument('--learned-reader',type=Path,default=None,
                        help='Exported learned result-card reader (ONNX). Its reads are stored on training result readings '
                             'and used by the accounting only where they equal an unexplained difference.')
    args=parser.parse_args()
    if not 1<=args.workers<=8:parser.error('Use 1 to 8 workers.')
    learned_reader=None
    if args.learned_reader is not None:
        from .learned_reader import LearnedReader
        if not args.learned_reader.is_file():raise PipelineError(f'Learned reader model not found: {args.learned_reader}')
        learned_reader=LearnedReader(args.learned_reader)
    # Passed on only when given, so a run without the flag assembles exactly as before.
    reader_kwargs=dict(learned_reader=learned_reader) if learned_reader is not None else {}
    report=capture(args.source,args.output,args.fps)
    _CURRENT.update(report=report,output=args.output)
    _progress('stage_done',name='capture')
    readings=cached_readings(report,args.output) if args.reparse_only else analyze_frames(report,args.output,args.workers,args.model_dir,pool=ocr_pool_for_device())
    _progress('stage_done',name='base_readings',readings=len(readings))
    if not args.reparse_only:readings=None  # reloaded below after refinement; free the first pass now
    from .automatic_refinement import run as run_automatic_refinement
    automatic_refinement=_guarded(report,'automatic_refinement',lambda: run_automatic_refinement(
        report,args.output,allow_ocr=not args.reparse_only,model_dir=args.model_dir,
        max_panel_frames=args.max_auto_refinement_frames,
        max_status_frames=args.max_status_refinement_frames,
    ),fallback=None)
    if automatic_refinement is not None:report['automatic_refinement']=automatic_refinement
    _progress('stage_done',name='automatic_refinement')
    # Fixed sidecars refine base observations. Load them before adding any
    # inspection or recovered rows: a later base-cache reload would discard
    # those source-backed additions, even while their recovery metadata says
    # they succeeded. Replay already loaded these sidecars and cannot add OCR.
    if not args.reparse_only:
        race_quantity=_guarded(report,'race_quantity_refinement',lambda: _generate_race_quantity_refinement(
            args.output,
            model_dir=args.model_dir,
        ),fallback=None)
        if race_quantity is not None:report['race_quantity_refinement'] = race_quantity
        _progress('stage_done',name='race_quantity_refinement')
        currency=_guarded(report,'currency_refinement',lambda: _generate_currency_refinement(
            args.output,
            model_dir=args.model_dir,
        ),fallback=None)
        if currency is not None:report['currency_refinement']=currency
        _progress('stage_done',name='currency_refinement')
        readings=cached_readings(report,args.output,workers=args.workers)
        _progress('stage_done',name='reload_readings',readings=len(readings),workers=args.workers)
        _guarded(report,'hint_card_preparation',lambda: _prepare_fresh_hint_cards(
            readings,
            args.output,
            report['source']['sha256'],
            model_dir=args.model_dir,
        ),fallback=None)
        _progress('stage_done',name='hint_card_preparation')
    from .receipt_recovery import recover as recover_numeric_receipts
    from .transactions import outcome_events
    # Dense re-read windows are OCR'd in a pool before their sequential
    # recording; one worker fewer than the base OCR stage leaves room for the
    # main process, which now holds every reading.
    dense_workers=args.dense_workers if args.dense_workers else max(1,args.workers-1)
    if dense_workers<1:raise PipelineError('dense workers must be at least 1')
    _progress('stage_done',name='inspections_merged',dense_workers=dense_workers)
    readings, recovery = _guarded(report,'numeric_receipt_recovery',lambda: recover_numeric_receipts(args.source,args.output,report['source'],readings,
        outcome_events(readings),allow_ocr=not args.reparse_only,model_dir=args.model_dir,dense_workers=dense_workers),fallback=(readings,None))
    if recovery is not None:report['numeric_receipt_recovery'] = recovery
    _progress('stage_done',name='numeric_receipt_recovery')
    from .training_gain_recovery import recover as recover_training_gains
    from .transactions import training_events
    readings, training_recovery = _guarded(report,'training_gain_recovery',lambda: recover_training_gains(args.source,args.output,report['source'],readings,
        training_events(readings),allow_ocr=not args.reparse_only,model_dir=args.model_dir,dense_workers=dense_workers),fallback=(readings,None))
    if training_recovery is not None:report['training_gain_recovery'] = training_recovery
    _progress('stage_done',name='training_gain_recovery')
    choice_observations,reward_observations=[],[]
    from .hint_card_cache import load as load_hint_cards
    hint_observations=_guarded(report,'hint_card_cache',lambda: load_hint_cards(readings,args.output,report['source']['sha256']),fallback=None)
    if automatic_refinement is not None:save_json(args.output/'automatic-refinement.json',automatic_refinement)
    _progress('stage_done',name='inspection_loads')
    # Generic occluded receipts are resolved only after every fixed sidecar
    # has been reparsed.  Calling this earlier would let the final fresh-cache
    # load replace promoted rows with the base observation; calling it here
    # keeps both fresh and reparse-only runs on the same source-bound path.
    from .occluded_receipt_recovery import recover as recover_occluded_receipts
    readings, occluded_recovery = _guarded(report,'occluded_receipt_recovery',lambda: recover_occluded_receipts(
            args.source, args.output, report['source'], readings,
            outcome_events(readings), allow_ocr=not args.reparse_only,
            model_dir=args.model_dir, dense_workers=dense_workers,
        ),fallback=(readings,None))
    if occluded_recovery is not None:report['occluded_receipt_recovery'] = occluded_recovery
    _progress('stage_done',name='occluded_receipt_recovery')
    def _choices():
        source=_guarded(report,'event_choice_observations',lambda: build_event_choice_observations(readings,args.output,choice_observations),fallback=None)
        return source,(source['committed_choices'] if isinstance(source,dict) else ())
    choice_source,committed=_choices()
    report=assemble(report,readings,choice_observations,reward_observations,hint_observations,
                    source_root=args.output,**reader_kwargs,
                    committed_choices=committed,
                    event_choice_observations=choice_source)
    _CURRENT['report']=report
    _progress('stage_done',name='assemble')
    from .boundary_state_recovery import recover as recover_boundary_states
    readings,boundary_recovery=_guarded(report,'boundary_state_recovery',lambda: recover_boundary_states(args.source,args.output,report,
        report['gameplay_tracking']['readings'],allow_ocr=not args.reparse_only,model_dir=args.model_dir,dense_workers=dense_workers),fallback=(readings,None))
    if boundary_recovery is not None:report['boundary_state_recovery']=boundary_recovery
    _progress('stage_done',name='boundary_state_recovery')
    if boundary_recovery and boundary_recovery.get('promoted_source_timestamps'):
        choice_source,committed=_choices()
        report=assemble(report,readings,choice_observations,reward_observations,hint_observations,
                        source_root=args.output,**reader_kwargs,
                        committed_choices=committed,
                        event_choice_observations=choice_source)
        _CURRENT['report']=report
        _progress('stage_done',name='assemble_after_boundary')
    validate_output(report,require_gameplay=True,source_root=args.output)
    _progress('stage_done',name='validate_output')
    save_json(args.output/'report.json',report)
    _progress('stage_done',name='save_report')
    _write_timeline_and_viewer(report,args.output,viewer=not args.no_viewer)
    print(json.dumps(dict(stage='complete',report=str(args.output/'report.json'),fully_verified=False)),flush=True)


def _write_timeline_and_viewer(report,root,viewer=True):
    """Write the compact timeline document and, unless told not to, the HTML viewer; neither is fatal."""
    from .timeline_document import write as write_timeline
    root=Path(root)
    size=_guarded(report,'timeline_document',lambda: write_timeline(report,root/'timeline.json'),fallback=None)
    _progress('stage_done',name='timeline_document',bytes=size)
    if viewer:
        html=_guarded(report,'viewer',lambda: render(report),fallback=None)
        if html is not None:(root/'index.html').write_text(html,encoding='utf-8')
    _progress('stage_done',name='viewer')

if __name__=='__main__':main()
