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
from .pipeline import probe,decode_frames,PipelineError
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
    if (video['width'],video['height'])!=(1920,1080):raise PipelineError('Full-recording analysis requires the supported English 1080p layout.')
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
        part=root/f'part-{index:03d}';part.mkdir(exist_ok=True);dest=part/'frames';manifest=part/'frames.json'
        if manifest.exists():rows=json.loads(manifest.read_text(encoding='utf-8'))
        else:
            dest.mkdir(exist_ok=True)
            if list(dest.iterdir()):raise PipelineError(f'Incomplete frame directory: {dest}. Preserve it and choose a new output directory.')
            rows=decode_frames(source,dest,start,length,fps,origin)
            for row in rows:
                row['id']=f'part-{index:03d}-'+row['id'];row['evidence']=f'part-{index:03d}/'+row['evidence'];row['clip_timestamp_ms']=row['source_timestamp_ms']
            save_json(manifest,rows)
        frames.extend(rows)
        print(json.dumps(dict(stage='capture',part=index,through_seconds=start+length,frames=len(frames))),flush=True)
    report=dict(schema_version=FULL_RECORDING_SCHEMA,source=dict(name=source.name,sha256=digest,size_bytes=source.stat().st_size,duration_ms=round(duration*1000),timeline_origin_seconds=origin,width=1920,height=1080,codec=video['codec_name']),frames=frames,
                clip=dict(source_start_ms=0,duration_ms=round(duration*1000)),sampling=dict(requested_fps=fps,frame_count=len(frames),method='minimum_interval_on_decoded_pts',guarantees_all_events=False),observations=[],limitations=['Entire source sampled; sampling alone does not establish verification.'])
    validate_output(report)
    save_json(root/'capture.json',report)
    return report


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
    raw=reader.read(pane)
    relative=f'gameplay/{frame["id"]}.png';pane.save(root/relative)
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
    return _analyze_frame(_PROCESS_READER,frame,Path(root),source_sha256)


def ocr_pool_for_device(device=None):
    """Executor kind for the OCR stage: DirectML readers need their own processes."""
    return 'process' if (OCR_DEVICE if device is None else device) in ('dml','cuda') else 'thread'


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
    def process(frame):return _analyze_frame(local.reader,frame,root,source_sha256)
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


def assemble(report,readings,choice_observations=(),race_reward_observations=(),hint_card_observations=None,
             *,committed_choices=(),event_choice_observations=None,source_root=None):
    from .inventory import summarize as inventory_summary
    from .vision import enrich_performance_panels
    from .preview_observations import build_preview_observations
    from .status_badges import build_observations as build_status_observations
    from .source_state_observations import build_observations as build_state_observations
    from .skill_menu_observations import build_observations as build_skill_menu_observations
    readings=enrich_performance_panels(readings)
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


_EARLY_RAW_SIDECAR_NAMES = frozenset({
    'weak_state_recovery',
    'numeric_cap_refinement',
    'preview_recovery',
    'status_badge_refinement',
    'training_badge_localization',
})


def _safe_replay_path(root, value, field):
    """Resolve a manifest path below ``root`` without following an escape."""

    if not isinstance(value, str) or not value:
        raise PipelineError(f'Replay sidecar {field} path is invalid.')
    root = Path(root).resolve()
    try:
        candidate = (root / Path(value)).resolve()
        relative = candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PipelineError(f'Replay sidecar {field} leaves the evidence root.') from exc
    cursor = root
    for component in relative.parts:
        cursor = cursor / component
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise PipelineError(f'Replay sidecar {field} is unreadable.') from exc
        if cursor.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise PipelineError(f'Replay sidecar {field} contains a reparse point.')
    if not candidate.is_file():
        raise PipelineError(f'Replay sidecar {field} is missing: {value}')
    return candidate


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


def _source_frame_for_sidecar(sidecar, *, base_root, sidecar_root, frame):
    """Find the cited decoded source frame in the disposable namespace.

    Reviewed sidecars were produced in recording-specific roots, so their
    provenance path can include ``independent-01/`` or
    ``independent-02/initial-baseline/``.  The path is metadata only; the
    actual file is located by trying bounded suffixes below the validated
    worker root and is accepted only when its declared SHA-256 matches.
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
            candidates.extend((Path(base_root) / suffix, Path(sidecar_root) / suffix))
    # A capture frame is a valid source-frame candidate for sidecars generated
    # by a normal fresh run.  Its hash still has to agree with the sidecar.
    if isinstance(frame, dict) and isinstance(frame.get('evidence'), str):
        candidates.append(Path(base_root) / Path(frame['evidence']))
    seen = set()
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
            candidate.relative_to(Path(sidecar_root).resolve())
        except (OSError, RuntimeError, ValueError):
            continue
        if candidate in seen or not candidate.is_file():
            continue
        if _contains_reparse(sidecar_root, candidate):
            continue
        seen.add(candidate)
        if isinstance(expected_hash, str) and _hash_file(candidate) == expected_hash:
            return candidate
    raise PipelineError(
        f'Replay sidecar source-frame proof is missing or does not match '
        f'{sidecar.get("source_frame_id", frame.get("id"))}.'
    )


def _index_early_manifest_sidecars(source_sidecars):
    """Index normalized manifest sidecars by timestamp before frame replay."""

    indexed = {}
    for supplement in source_sidecars or ():
        if not isinstance(supplement, dict) or supplement.get('kind') != 'raw_sidecars':
            continue
        name = supplement.get('name')
        if name not in _EARLY_RAW_SIDECAR_NAMES:
            continue
        for entry in supplement.get('entries', ()):
            if not isinstance(entry, dict):
                continue
            timestamp = entry.get('source_timestamp_ms')
            if type(timestamp) is not int:
                continue
            indexed.setdefault(timestamp, []).append((name, supplement, entry))
    return indexed


def _early_sidecars_for_frame(source_sidecars, *, sidecar_root, base_root, frame, raw_path):
    """Return registered source sidecars for one base frame in stable order."""

    entries = []
    seen = set()
    if isinstance(source_sidecars, dict):
        manifest_entries = source_sidecars.get(frame['source_timestamp_ms'], ())
    else:
        manifest_entries = []
        for supplement in source_sidecars or ():
            if not isinstance(supplement, dict) or supplement.get('kind') != 'raw_sidecars':
                continue
            name = supplement.get('name')
            if name not in _EARLY_RAW_SIDECAR_NAMES:
                continue
            manifest_entries.extend(
                (name, supplement, entry)
                for entry in supplement.get('entries', ())
                if isinstance(entry, dict)
                and entry.get('source_timestamp_ms') == frame['source_timestamp_ms']
            )
    for item in manifest_entries:
            if len(item) == 3:
                name, supplement, entry = item
            else:  # pragma: no cover - defensive for malformed direct callers
                continue
            entry_path = entry.get('path')
            # ``load_manifest`` returns root-relative sidecar paths.  Accept a
            # raw manifest entry as well for focused callers by joining its
            # declared group folder exactly once.
            if (isinstance(entry_path, str) and len(Path(entry_path).parts) == 1
                    and isinstance(supplement.get('folder'), str)
                    and supplement.get('folder')):
                entry_path = str(Path(supplement['folder']) / entry_path)
            sidecar_path = _safe_replay_path(sidecar_root, entry_path, f'{name}.path')
            if sidecar_path in seen:
                continue
            raw_entry_path = _safe_replay_path(sidecar_root, entry.get('raw_path'), f'{name}.raw_path')
            evidence_path = _safe_replay_path(sidecar_root, entry.get('evidence_path'), f'{name}.evidence_path')
            entries.append((name, sidecar_path, raw_entry_path, evidence_path))
            seen.add(sidecar_path)

    # Fresh runs publish these two bounded sidecar kinds directly below the
    # worker root.  The fallback is deliberately closed to the two registered
    # names and never treats arbitrary JSON as a parser input.
    for name, folder in (('weak_state_recovery', 'weak-state-recovery'),
                         ('numeric_cap_refinement', 'numeric-cap-refinement'),
                         ('preview_recovery', 'preview-recovery'),
                         ('training_badge_localization', 'training-badge-localization')):
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

    order = {'weak_state_recovery': 0, 'numeric_cap_refinement': 1,
             'preview_recovery': 2, 'status_badge_refinement': 3,
             'training_badge_localization': 4}
    return sorted(entries, key=lambda item: (order[item[0]], item[1].as_posix()))


def _apply_early_sidecars(raw, *, sidecars, original, frame, base_root, sidecar_root):
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
        source_frame_path = _source_frame_for_sidecar(
            sidecar, base_root=base_root, sidecar_root=sidecar_root, frame=frame)
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
            elif name == 'preview_recovery':
                from .preview_recovery import load as load_preview
                raw = load_preview(raw, sidecar_path, **kwargs)
            elif name == 'training_badge_localization':
                from .training_badge_localization import load as load_training_badges
                raw = load_training_badges(raw, sidecar_path, **kwargs)
            elif name == 'status_badge_refinement':
                from .status_badge_refinement import load as load_status_badge
                raw = load_status_badge(raw, sidecar_path,
                                        evidence_path=evidence_path,
                                        original=original,
                                        source_frame_id=frame['id'])
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


def _cached_reading_row(frame,*,root,status_badge_root,sidecar_root,source_sidecar_index,source_sha256,allow_partial):
    """Reinterpret one frame's immutable OCR observation (see :func:`cached_readings`)."""
    root=Path(root);status_badge_root=Path(status_badge_root);sidecar_root=Path(sidecar_root)
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
        sidecars=_early_sidecars_for_frame(
            source_sidecar_index,
            sidecar_root=sidecar_root,
            base_root=root,
            frame=frame,
            raw_path=path,
        ),
        original=original,
        frame=frame,
        base_root=root,
        sidecar_root=sidecar_root,
    )
    refinement=root/'outcome-refinement'/path.name
    if refinement.exists():
        from .refine_outcomes import apply_refinements
        review=json.loads(refinement.read_text(encoding='utf-8'))
        if review['evidence_sha256']!=hashlib.sha256((root/raw['evidence']).read_bytes()).hexdigest():raise PipelineError('Refinement image changed.')
        raw=apply_refinements(raw,review)
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
    skill_points=root/'skill-points-refinement'/path.name
    if skill_points.exists():
        from .refine_contrast import fingerprint
        extra=json.loads(skill_points.read_text(encoding='utf-8'))
        if extra['raw_sha256']!=fingerprint(original) or extra['evidence_sha256']!=hashlib.sha256((root/raw['evidence']).read_bytes()).hexdigest():
            raise PipelineError('Skill-point refinement evidence changed.')
        raw=dict(raw,skill_point_refinement=extra['views'])
    variants=root/'skill-variants'/path.name
    if variants.exists():
        from .refine_contrast import fingerprint
        extra=json.loads(variants.read_text(encoding='utf-8'))
        if extra['raw_sha256']!=fingerprint(original) or extra['evidence_sha256']!=hashlib.sha256((root/raw['evidence']).read_bytes()).hexdigest():raise PipelineError('Skill variant evidence changed.')
        raw=dict(raw,skill_variants=extra['observations'])
    symbols=root/'song-symbols'/path.name
    if symbols.exists():
        from .song_symbols import apply as apply_symbols
        raw=apply_symbols(raw,json.loads(symbols.read_text(encoding='utf-8')),root/raw['evidence'],original)
    symbol_refinement=root/'song-symbol-refinement'/path.name
    if symbol_refinement.exists():
        from .song_symbol_refinement import apply as apply_symbol_refinement
        try:
            raw=apply_symbol_refinement(raw,json.loads(symbol_refinement.read_text(encoding='utf-8')),
                                        root/raw['evidence'],original)
        except ValueError as exc:
            raise PipelineError(f'Song-symbol refinement invalid: {exc}') from exc
    star_refinement=root/'song-star-refinement'/path.name
    if star_refinement.exists():
        from .song_star_refinement import apply as apply_star_refinement
        try:
            from PIL import Image
            with Image.open(root/frame['evidence']) as source_image:
                source_pixels=source_image.convert('RGB').crop((148,0,958,1080))
            if hashlib.sha256(source_pixels.tobytes()).hexdigest()!=original.get('gameplay_sha256'):
                raise ValueError('Gameplay pixels differ from decoded source crop.')
            raw=apply_star_refinement(raw,json.loads(star_refinement.read_text(encoding='utf-8')),
                                      root/raw['evidence'],original)
        except ValueError as exc:
            raise PipelineError(f'Song-star refinement invalid: {exc}') from exc
    concert_panel=root/'concert-panel-refinement'/path.name
    if concert_panel.exists():
        from .concert_panel_refinement import apply as apply_concert_panel
        raw=apply_concert_panel(raw,json.loads(concert_panel.read_text(encoding='utf-8')),
            root/raw['evidence'],observation_root=root,original=original)
    receipt=root/'base-receipt-refinement'/path.name
    if receipt.exists():
        from .base_receipt_refinement import load as load_base_receipt
        try:
            raw=load_base_receipt(raw,receipt,original=original,
                evidence_path=root/original['evidence'],source_frame_path=root/frame['evidence'])
        except ValueError as exc:
            raise PipelineError(f'Base receipt refinement evidence invalid: {exc}') from exc
    race_identity=root/'race-identity-refinement'/path.name
    if race_identity.exists():
        from .race_identity_refinement import apply as apply_race_identity
        try:
            raw=apply_race_identity(raw,json.loads(race_identity.read_text(encoding='utf-8')),root,original=original)
        except (ValueError,KeyError,TypeError,OSError) as exc:
            raise PipelineError(f'Race identity refinement evidence invalid: {exc}') from exc
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
    if not status_badge.exists() and status_badge_root.resolve()!=root.resolve():
        status_badge=status_badge_root/'status-badge-refinement'/path.name
    # A manifest-scoped status sidecar may already have been applied by
    # the early source-bound stage above.  Do not apply the same evidence
    # again through the conventional folder.
    if status_badge.exists() and 'status_badge_refinement' not in raw:
        from .status_badge_refinement import load as load_status_badge
        try:
            raw=load_status_badge(raw,status_badge,original=original,
                evidence_path=root/original['evidence'],source_frame_id=frame['id'])
        except (ValueError,KeyError,TypeError,OSError) as exc:
            raise PipelineError(f'Status badge refinement evidence invalid: {exc}') from exc
    row=parse_receipt_pixels(raw,root,frame,original,source_sha256=source_sha256)
    if 'base_receipt_refinement' in raw:
        row['base_receipt_refinement']=raw['base_receipt_refinement']
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
    inventory=root/'inventory-refinement'/path.name
    if inventory.exists():
        from .refine_inventory import apply as apply_inventory
        row=apply_inventory(row,original,json.loads(inventory.read_text(encoding='utf-8')),root/raw['evidence'])
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
    choice=root/'choice-refinement'/path.name
    if choice.exists():
        from .refine_choices import apply as apply_choice
        row=apply_choice(row,original,json.loads(choice.read_text(encoding='utf-8')),root/raw['evidence'])
    choice_cards=root/'choice-card-refinement'/path.name
    if choice_cards.exists():
        from .choice_card_refinement import apply as apply_choice_cards
        row=apply_choice_cards(row,original,json.loads(choice_cards.read_text(encoding='utf-8')),root/raw['evidence'])
    row.update(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']);return row


def _cached_reading_task(task):
    frame,context=task
    return _cached_reading_row(frame,**context)


def cached_readings(report,root,allow_partial=False,*,status_badge_root=None,
                    source_sidecars=None,sidecar_root=None,workers=1):
    """Reinterpret immutable OCR observations without rerunning models.

    ``workers`` above one reinterprets the frames in a process pool.  Every
    row still comes from the same per-frame function, collected in frame
    order, so the result is identical to the sequential pass; the first
    failing frame in frame order raises exactly as it does sequentially.
    """
    root=Path(root);status_badge_root=Path(status_badge_root or root)
    sidecar_root=Path(sidecar_root or root)
    context=dict(root=root,status_badge_root=status_badge_root,sidecar_root=sidecar_root,
                 source_sidecar_index=_index_early_manifest_sidecars(source_sidecars),
                 source_sha256=report.get('source',{}).get('sha256'),allow_partial=allow_partial)
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


def _read_json(path):
    path=Path(path)
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc:
        raise PipelineError(f'Replay input JSON is not readable: {path}') from exc
    if not isinstance(value,dict):
        raise PipelineError(f'Replay input JSON must be an object: {path}')
    return value


def _rebase_paths(value,origin,root):
    """Rewrite actual proof paths from one cache namespace to root-relative paths."""
    origin=Path(origin);root=Path(root)
    if isinstance(value,dict):
        return {key:_rebase_paths(item,origin,root) for key,item in value.items()}
    if isinstance(value,list):
        return [_rebase_paths(item,origin,root) for item in value]
    if isinstance(value,str) and value.lower().endswith(('.png','.jpg','.json')):
        normalized=value.replace('\\','/')
        parts=list(Path(normalized).parts)
        candidates=[origin/Path(normalized)]
        # Reviewed sidecars may retain a recording prefix in provenance paths
        # while the disposable root contains only its base namespace. Resolve
        # bounded suffixes below that root; hashes and arbitrary strings never
        # enter this branch.
        candidates.extend(origin/Path(*parts[index:]) for index in range(1,len(parts)))
        for candidate in candidates:
            try:
                relative=candidate.resolve().relative_to(root.resolve())
            except (OSError,RuntimeError,ValueError):
                continue
            if candidate.is_file():
                return relative.as_posix()
    return value


def _manifest_group_payload(root,group):
    """Return selected readings rewritten for the group's declared own root."""
    root=Path(root)
    folder=root/Path(group.get('folder',''))
    # replay_inputs normalizes manifest to a cache-root-relative path, even
    # when the declared folder is nested. Do not prepend folder a second time.
    manifest_path=root/Path(group['manifest'])
    from .replay_inputs import ReplayInputError, load_bound_inspection_manifest
    try:
        payload = load_bound_inspection_manifest(root, group['manifest'], group.get('manifest_sha256'))
    except ReplayInputError as exc:
        raise PipelineError(f'Replay inspection is not bound: {exc}') from exc
    readings=payload.get('readings')
    if not isinstance(readings,list):
        raise PipelineError(f'Replay input readings are not an array: {manifest_path}')
    indices=group.get('row_indices')
    if not isinstance(indices,list) or not indices:
        raise PipelineError(f'Replay input group has no selected rows: {manifest_path}')
    evidence_root=root if group.get('row_evidence_root')=='root' else folder
    own_root=root/Path(group.get('own_root',''))
    selected=[]
    for index in indices:
        try:
            row=readings[index]
        except (IndexError,TypeError) as exc:
            raise PipelineError(f'Replay input row index is outside its manifest: {manifest_path}') from exc
        if not isinstance(row,dict) or not isinstance(row.get('evidence'),str):
            raise PipelineError(f'Replay input row evidence is invalid: {manifest_path}')
        evidence=evidence_root/Path(row['evidence'])
        try:
            relative=evidence.relative_to(own_root)
        except ValueError as exc:
            raise PipelineError(f'Replay input row evidence is outside its own root: {manifest_path}') from exc
        selected.append(dict(row,evidence=relative.as_posix()))
    return dict(payload,readings=selected),own_root


def _manifest_group_rows(root,group):
    from .inspect_training import reparse_inspection
    payload,own_root=_manifest_group_payload(root,group)
    return reparse_inspection(payload,own_root),own_root


def _fixed_supplement_folder(kind,name,folder,base_folder):
    """Identify sidecars already applied by ``cached_readings``."""
    expected_prefix=Path(base_folder).as_posix() if base_folder else ''
    if kind=='raw_sidecars':
        fixed={
            'outcome-refinement':'outcome-refinement',
            'skill_points':'skill-points-refinement',
            'skill_variants':'skill-variants',
            'song_symbol_refinement':'song-symbol-refinement',
            'song_star_refinement':'song-star-refinement',
            'concert_panel':'concert-panel-refinement',
            'base_receipt':'base-receipt-refinement',
            'race_identity':'race-identity-refinement',
            'performance_panel':'performance-panel-refinement',
            'status_badge_refinement':'status-badge-refinement',
            'inventory':'inventory-refinement',
            'lesson_offer':'lesson-offer-refinement',
            'choice':'choice-refinement',
            'choice_cards':'choice-card-refinement',
        }
        expected_name=fixed.get(name)
        if name=='status_badge_refinement':
            expected={expected_name,Path(expected_prefix,expected_name).as_posix()} if expected_prefix else {expected_name}
            return folder in expected
        expected=Path(expected_prefix,expected_name).as_posix() if expected_prefix and expected_name else expected_name
        return expected_name is not None and folder==expected
    fixed={
        'song_symbols':'song-symbols',
        'currency_regions':None,
        'race_identity':'race-identity-refinement',
    }
    if kind=='currency_regions':
        expected={Path(expected_prefix,item).as_posix() if expected_prefix else item
                  for item in ('currency-refinement','currency-padding-refinement')}
        return folder in expected
    expected=fixed.get(kind)
    if expected is None:
        return False
    expected=Path(expected_prefix,expected).as_posix() if expected_prefix else expected
    return folder==expected


def _apply_manifest_supplements(normalized,root,base_root,capture,rows,*,early_applied=False):
    """Apply semantic supplements whose source files are outside fixed cache names."""
    frame_by_time={frame['source_timestamp_ms']:frame for frame in capture['frames']}
    by_time={row['source_timestamp_ms']:row for row in rows}
    applied=[]
    base_folder=normalized['base'].get('folder','')
    for supplement in normalized.get('supplements',[]):
        kind=supplement['kind'];name=supplement.get('name')
        folder=supplement.get('folder','')
        if _fixed_supplement_folder(kind,name,folder,base_folder):
            continue
        if kind=='raw_sidecars':
            # Registered source sidecars are loaded before fixed sidecars by
            # ``cached_readings`` so every loader sees the untouched original
            # neural record.  This also covers a status-badge sidecar staged
            # under its own source namespace.  Keep the dispatch closed: an
            # unregistered raw group must never become an arbitrary parser.
            if early_applied and name in _EARLY_RAW_SIDECAR_NAMES:
                continue
            if name != 'status_badge_refinement':
                raise PipelineError(f'No shared loader is registered for raw sidecar group {name!r}.')
            for entry in supplement.get('entries',[]):
                sidecar_path=root/Path(entry['path'])
                raw_path=root/Path(entry['raw_path'])
                evidence_path=root/Path(entry['evidence_path'])
                raw=_read_json(raw_path)
                timestamp=entry['source_timestamp_ms']
                frame=frame_by_time.get(timestamp)
                row=by_time.get(timestamp)
                if frame is None or row is None:
                    raise PipelineError(f'Replay supplement has no base frame at {timestamp}: {sidecar_path}')
                if raw_path.parent.resolve()!= (Path(base_root)/'neural').resolve():
                    raise PipelineError(f'Replay supplement raw input is outside the base neural namespace: {raw_path}')
                source_evidence=base_root/Path(raw.get('evidence',''))
                if source_evidence.resolve()!=evidence_path.resolve():
                    raise PipelineError(f'Replay supplement evidence path disagrees with raw input: {sidecar_path}')
                from .status_badge_refinement import apply as apply_status_badge
                try:
                    refined=apply_status_badge(
                        raw,_read_json(sidecar_path),evidence_path=source_evidence,
                        original=raw,source_frame_id=frame['id'])
                    parsed=parse_receipt_pixels(
                        refined,base_root,frame,raw,
                        source_sha256=normalized['source_sha256'])
                except (ValueError,KeyError,TypeError,OSError) as exc:
                    raise PipelineError(f'Status badge supplement evidence invalid: {sidecar_path}') from exc
                if parsed['screen']!=row.get('screen') or parsed['stats']!=row.get('stats'):
                    raise PipelineError(f'Status badge supplement changed unrelated state at {timestamp}: {sidecar_path}')
                replacement=dict(row, facts=parsed.get('facts', row.get('facts', {})),
                                 ocr=parsed.get('ocr', row.get('ocr', {})))
                refinement_metadata=parsed.get('status_badge_refinement')
                if refinement_metadata is not None:
                    replacement['status_badge_refinement']=copy.deepcopy(refinement_metadata)
                by_time[timestamp]=replacement
                applied.append(dict(kind=kind,name=name,path=entry['path'],
                                    sidecar_sha256=entry['sidecar_sha256'],
                                    source_timestamp_ms=timestamp))
            continue
        if kind not in {'song_symbols','currency_regions','race_identity'}:
            raise PipelineError(f'Unsupported replay supplement kind: {kind!r}')
        for entry in supplement.get('entries',[]):
            sidecar_path=root/Path(entry['path'])
            raw_path=root/Path(entry['raw_path'])
            evidence_path=root/Path(entry['evidence_path'])
            raw=_read_json(raw_path)
            timestamp=entry['source_timestamp_ms']
            frame=frame_by_time.get(timestamp)
            row=by_time.get(timestamp)
            if frame is None or row is None:
                raise PipelineError(f'Replay supplement has no base frame at {timestamp}: {sidecar_path}')
            if raw_path.parent.resolve()!= (Path(base_root)/'neural').resolve():
                raise PipelineError(f'Replay supplement raw input is outside the base neural namespace: {raw_path}')
            source_evidence=base_root/Path(raw.get('evidence',''))
            if source_evidence.resolve()!=evidence_path.resolve():
                raise PipelineError(f'Replay supplement evidence path disagrees with raw input: {sidecar_path}')
            sidecar=_read_json(sidecar_path)
            if kind=='song_symbols':
                from .song_symbols import apply as apply_symbols
                refined=apply_symbols(raw,sidecar,source_evidence,original=raw)
                parsed=parse_receipt_pixels(refined,base_root,frame,raw,source_sha256=normalized['source_sha256'])
                if parsed['screen']!=row.get('screen') or parsed['stats']!=row.get('stats'):
                    raise PipelineError(f'Song supplement changed unrelated state at {timestamp}: {sidecar_path}')
                by_time[timestamp]=dict(row,effects=parsed['effects'])
            elif kind=='currency_regions':
                from .refine_contrast import fingerprint
                regions=sidecar.get('regions')
                if not isinstance(regions,dict) or sidecar.get('raw_sha256')!=fingerprint(raw):
                    raise PipelineError(f'Currency supplement provenance is invalid: {sidecar_path}')
                refined=dict(raw,regions=dict(raw.get('regions',{}),**regions))
                parsed=parse(refined)
                if parsed['screen']!=row.get('screen'):
                    raise PipelineError(f'Currency supplement changed screen identity at {timestamp}: {sidecar_path}')
                target={'lesson_selection':'performance_points',
                        'lesson_confirmation':'projected_performance_points'}.get(row.get('screen'))
                if target is None:
                    raise PipelineError(f'Currency supplement has no supported target screen at {timestamp}: {sidecar_path}')
                facts=copy.deepcopy(row.get('facts',{}));facts[target]=copy.deepcopy(parsed.get('facts',{}).get(target))
                facts['currency_refinement_evidence']=dict(path=entry['path'],sha256=entry['sidecar_sha256'],
                    raw_sha256=entry['raw_sha256'],evidence_sha256=entry['evidence_sha256'],independent_frame_count=1)
                by_time[timestamp]=dict(row,facts=facts)
            else:
                from .race_identity_refinement import apply as apply_race_identity
                try:
                    refined=apply_race_identity(raw,sidecar,base_root,original=raw)
                except (ValueError,KeyError,TypeError,OSError) as exc:
                    raise PipelineError(f'Race identity supplement evidence invalid: {sidecar_path}') from exc
                parsed=parse(refined)
                if parsed['screen']!=row.get('screen'):
                    raise PipelineError(f'Race identity supplement changed screen identity at {timestamp}: {sidecar_path}')
                field=sidecar.get('field')
                facts=copy.deepcopy(row.get('facts',{}))
                if field in parsed.get('facts',{}):
                    facts[field]=copy.deepcopy(parsed['facts'][field])
                facts['race_identity_refinement']=copy.deepcopy(parsed.get('facts',{}).get('race_identity_refinement',sidecar))
                by_time[timestamp]=dict(row,facts=facts)
            applied.append(dict(kind=kind,path=entry['path'],sidecar_sha256=entry['sidecar_sha256'],
                                source_timestamp_ms=timestamp))
    return [by_time[timestamp] for timestamp in sorted(by_time)],applied


def _validate_training_replay_windows(value, label):
    """Validate the small window contract used by training-gain replay.

    The replay manifest is source-bound before this helper runs, but a
    mutable ``last-plan.json`` is still untrusted metadata.  Only the fields
    needed to scope promotion are copied out; in particular, no amount or
    state value can enter a replay window.
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise PipelineError(f'Replay training recovery {label} must be an array.')
    from .reconcile import FIELDS
    from .gameplay import CURRENCIES
    allowed_fields = set(FIELDS)
    allowed_performance_fields = set(CURRENCIES)
    allowed_keys = {
        'start_ms', 'end_ms', 'owner_id', 'fields', 'reason', 'fps',
        # The committed-result projection window carries its selected option
        # and the independent performance-sidebar fields it may read.  These
        # are scope metadata only; amounts remain in the validated source rows.
        'performance_fields', 'training_option', 'source_result_projection',
    }
    result = []
    seen = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise PipelineError(f'Replay training recovery {label}[{index}] must be an object.')
        unknown = set(raw) - allowed_keys
        if unknown:
            names = ', '.join(sorted(map(str, unknown)))
            raise PipelineError(f'Replay training recovery {label}[{index}] has unsupported fields: {names}.')
        start, end = raw.get('start_ms'), raw.get('end_ms')
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise PipelineError(f'Replay training recovery {label}[{index}] has an invalid time window.')
        owner = raw.get('owner_id')
        if not isinstance(owner, str) or not owner.strip():
            raise PipelineError(f'Replay training recovery {label}[{index}] has no source phase owner.')
        fields = raw.get('fields')
        if (not isinstance(fields, list) or not fields
                or any(not isinstance(field, str) or field not in allowed_fields for field in fields)
                or len(set(fields)) != len(fields)):
            raise PipelineError(f'Replay training recovery {label}[{index}] has invalid fields.')
        normalized = {
            'start_ms': start,
            'end_ms': end,
            'owner_id': owner.strip(),
            'fields': sorted(fields),
        }
        if 'reason' in raw:
            if not isinstance(raw['reason'], str) or not raw['reason'].strip():
                raise PipelineError(f'Replay training recovery {label}[{index}] has an invalid reason.')
            normalized['reason'] = raw['reason']
        if 'fps' in raw:
            if type(raw['fps']) is not int or raw['fps'] <= 0:
                raise PipelineError(f'Replay training recovery {label}[{index}] has an invalid fps.')
            normalized['fps'] = raw['fps']
        if 'performance_fields' in raw:
            performance_fields = raw['performance_fields']
            if (not isinstance(performance_fields, list)
                    or any(type(field) is not str
                           or field not in allowed_performance_fields
                           for field in performance_fields)
                    or len(set(performance_fields)) != len(performance_fields)):
                raise PipelineError(
                    f'Replay training recovery {label}[{index}] has invalid performance fields.')
            normalized['performance_fields'] = sorted(performance_fields)
        if 'training_option' in raw:
            training_option = raw['training_option']
            if (not isinstance(training_option, str)
                    or not training_option.strip()
                    or training_option.strip() not in allowed_fields):
                raise PipelineError(
                    f'Replay training recovery {label}[{index}] has an invalid training option.')
            normalized['training_option'] = training_option.strip()
        if 'source_result_projection' in raw:
            projection = raw['source_result_projection']
            if type(projection) is not bool:
                raise PipelineError(
                    f'Replay training recovery {label}[{index}] has an invalid result projection flag.')
            normalized['source_result_projection'] = projection
            if projection and (
                    'performance_fields' not in normalized
                    or 'training_option' not in normalized):
                raise PipelineError(
                    f'Replay training recovery {label}[{index}] lacks result projection scope metadata.')
        identity = (
            normalized['start_ms'], normalized['end_ms'], normalized['owner_id'],
            tuple(normalized['fields']),
            tuple(normalized.get('performance_fields', [])),
            normalized.get('training_option'),
            normalized.get('source_result_projection'),
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(normalized)
    return result


def _replay_training_manifest_ranges(group, directory):
    """Read reviewed training ranges without trusting any numeric payload."""

    windows = group.get('windows')
    if windows is None:
        manifest_name = group.get('manifest')
        manifest_path = directory / Path(manifest_name) if isinstance(manifest_name, str) else None
        if manifest_path is None or not manifest_path.is_file():
            return []
        manifest_payload = _read_json(manifest_path)
        windows = manifest_payload.get('windows') if isinstance(manifest_payload, dict) else None
    if windows is None:
        return []
    if not isinstance(windows, list):
        raise PipelineError('Replay training recovery manifest windows must be an array.')
    result = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            raise PipelineError(f'Replay training recovery manifest window {index} is invalid.')
        start, end = window.get('start_ms'), window.get('end_ms')
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            raise PipelineError(f'Replay training recovery manifest window {index} has invalid bounds.')
        normalized = {'start_ms': start, 'end_ms': end}
        if 'fps' in window:
            if type(window['fps']) is not int or window['fps'] <= 0:
                raise PipelineError(f'Replay training recovery manifest window {index} has invalid fps.')
            normalized['fps'] = window['fps']
        result.append(normalized)
    return result


def _replay_training_source_rows(rows, fresh):
    """Combine parsed timeline and recovery rows for source-only planning."""

    result = []
    seen = set()
    for row in [*rows, *fresh]:
        if not isinstance(row, dict) or row.get('screen') != 'training_result':
            continue
        timestamp = row.get('source_timestamp_ms')
        if type(timestamp) is not int:
            continue
        evidence = row.get('evidence')
        identity = (timestamp, evidence if isinstance(evidence, str) else None)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return sorted(result, key=lambda row: (row['source_timestamp_ms'], str(row.get('evidence', ''))))


def _replay_training_events(source_rows):
    """Build one source-owned event view for all replay training checks."""

    from .transactions import training_events

    try:
        return training_events(source_rows)
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelineError('Replay training recovery source rows are malformed.') from exc


def _replay_training_candidate_windows(rows, fresh, events=None):
    """Recompute candidate-only scopes from current source crop provenance."""

    from .training_gain_recovery import _candidate_only_fields, plan

    source_rows = _replay_training_source_rows(rows, fresh)
    if not source_rows:
        return []
    if events is None:
        events = _replay_training_events(source_rows)
    try:
        windows = plan(source_rows, events)
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelineError('Replay training recovery source rows are malformed.') from exc
    windows = _validate_training_replay_windows(windows, 'candidate plan')

    # ``plan`` historically requires a non-zero event span.  A single
    # committed result frame can nevertheless carry valid nested tight/wide
    # source crop agreement, which ``promote_with_metadata`` accepts.  Give
    # that source frame a one-millisecond inclusive scope derived from the
    # observed event instead of dropping it or widening to an arbitrary
    # neighbouring event.
    planned_fields = {
        (window['owner_id'], field)
        for window in windows
        for field in window['fields']
    }
    fallback = []
    for event in events:
        owner = event.get('id')
        first, last = event.get('first_seen_ms'), event.get('last_seen_ms')
        if not isinstance(owner, str) or type(first) is not int or type(last) is not int:
            continue
        event_rows = [row for row in source_rows
                      if row.get('screen') == 'training_result'
                      and type(row.get('source_timestamp_ms')) is int
                      and first <= row['source_timestamp_ms'] <= last]
        candidate_fields = set()
        for row in event_rows:
            candidate_fields.update(_candidate_only_fields(row))
        for field in sorted(candidate_fields):
            if (owner, field) in planned_fields:
                continue
            times = [row['source_timestamp_ms'] for row in event_rows
                     if field in _candidate_only_fields(row)]
            if not times:
                continue
            start = max(first, min(times) - 100)
            end = min(last, max(times) + 100)
            if end <= start:
                # Keep the one physical source timestamp in scope while
                # satisfying the half-open interval contract used by the
                # promotion helper.
                start = max(0, min(times) - 1)
                end = min(times) + 1
            if end <= start:
                continue
            fallback.append(dict(
                start_ms=start,
                end_ms=end,
                owner_id=owner,
                fields=[field],
                reason='candidate_only_source_gain_evidence',
            ))
    return _merge_replay_training_windows(
        windows,
        _validate_training_replay_windows(fallback, 'single-frame candidate plan'),
    )


def _replay_training_conflict_windows(rows, ranges, events=None):
    """Bind immutable reviewed ranges to one current training phase."""

    if not ranges:
        return []
    from .reconcile import FIELDS

    source_rows = _replay_training_source_rows(rows, [])
    if events is None:
        events = _replay_training_events(source_rows)
    allowed_fields = set(FIELDS)
    result = []
    for window in ranges:
        from .training_gain_recovery import _prefix_resolved_fields
        owners = [event for event in events
                  if event.get('first_seen_ms', window['end_ms'] + 1) <= window['end_ms']
                  and event.get('last_seen_ms', window['start_ms'] - 1) >= window['start_ms']
                  and ((isinstance(event.get('conflicting_readings'), dict)
                        and event.get('conflicting_readings'))
                       or _prefix_resolved_fields(event))]
        if len(owners) != 1:
            continue
        owner = owners[0]
        conflicting = owner.get('conflicting_readings') if isinstance(owner.get('conflicting_readings'), dict) else {}
        # Prefix-resolved fields stay in scope for the same reason as in
        # ``training_gain_recovery.plan``: their acceptance rests on the dense
        # frames this window projects.
        fields = sorted(field for field in set(conflicting) | _prefix_resolved_fields(owner) if field in allowed_fields)
        if not fields:
            continue
        item = dict(window, owner_id=owner.get('id'), fields=fields,
                    reason='replayed_manifest_conflicting_readings')
        result.extend(_validate_training_replay_windows([item], 'manifest-derived plan'))
    return result


def _replay_training_candidate_rows(rows, fresh, windows, events=None):
    """Return source candidate rows in one owned phase for each window.

    Dense inspections can include a transition frame immediately before the
    action header is readable.  If the committed event has a visible option,
    an option-less candidate is not allowed to veto the later option-owned
    phase; explicit evidence for a different option remains in the resolver's
    input and therefore keeps the field unresolved.  The returned field map is
    also used to remove only those discarded candidate facts from the mutable
    promotion copy.  Original source rows remain untouched.
    """

    from .training_gain_recovery import _candidate_only_fields

    source = [row for row in [*rows, *fresh] if isinstance(row, dict)]
    event_by_id = {
        event.get('id'): event for event in (events or [])
        if isinstance(event, dict) and isinstance(event.get('id'), str)
    }
    selected = {}
    for window in windows:
        scoped = []
        for row in source:
            fields = _candidate_only_fields(row).intersection(window['fields'])
            timestamp = row.get('source_timestamp_ms')
            evidence = row.get('evidence')
            if (not fields or type(timestamp) is not int
                    or not isinstance(evidence, str) or not evidence.strip()
                    or not (window['start_ms'] <= timestamp <= window['end_ms'])):
                continue
            scoped.append((row, fields))
        if not scoped:
            continue

        owner = event_by_id.get(window.get('owner_id'))
        owner_option = owner.get('training_option') if owner else None
        owner_option = owner_option.strip() if isinstance(owner_option, str) else None
        # Candidate-only recovery is allowed to fill a committed result when
        # the source rows identify that committed option.  A frame may miss
        # its heading, so an option-less row can join matching rows from the
        # same owner interval.  It is never sufficient by itself, and an
        # explicit competing option remains in scope so the resolver rejects
        # the phase instead of silently discarding that source fact.
        if not owner_option:
            continue
        matching = [
            item for item in scoped
            if isinstance(item[0].get('training_option'), str)
            and item[0]['training_option'].strip() == owner_option
        ]
        if not matching:
            continue
        optionless = [
            item for item in scoped
            if item[0].get('training_option') is None
            or (isinstance(item[0].get('training_option'), str)
                and not item[0]['training_option'].strip())
        ]
        explicit_other = [
            item for item in scoped
            if isinstance(item[0].get('training_option'), str)
            and item[0]['training_option'].strip()
            and item[0]['training_option'].strip() != owner_option
        ]
        if not explicit_other:
            scoped = matching + optionless

        for row, fields in scoped:
            identity = (row['source_timestamp_ms'], row['evidence'].strip())
            selected.setdefault(identity, set()).update(fields)

    result = []
    seen = set()
    for row in source:
        timestamp = row.get('source_timestamp_ms')
        evidence = row.get('evidence')
        if type(timestamp) is not int or not isinstance(evidence, str) or not evidence.strip():
            continue
        identity = (timestamp, evidence.strip())
        if identity not in selected or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result, selected


def _replay_training_filter_promotion_rows(fresh, windows, selected):
    """Strip rejected candidate fields from a temporary promotion view."""

    from .training_gain_recovery import _candidate_only_fields

    result = []
    for row in fresh:
        if not isinstance(row, dict):
            continue
        timestamp = row.get('source_timestamp_ms')
        evidence = row.get('evidence')
        identity = ((timestamp, evidence.strip())
                    if type(timestamp) is int and isinstance(evidence, str)
                    else None)
        candidate_fields = _candidate_only_fields(row)
        if identity is not None and candidate_fields:
            allowed = selected.get(identity, set())
            rejected = candidate_fields - allowed
            if rejected:
                row = copy.deepcopy(row)
                facts = row.get('facts')
                provenance = facts.get('training_gain_crop_provenance') if isinstance(facts, dict) else None
                if isinstance(provenance, dict):
                    for field in rejected:
                        provenance.pop(field, None)
                    if not provenance:
                        facts.pop('training_gain_crop_provenance', None)
        result.append(row)
    return result


def _merge_replay_training_windows(*window_lists):
    result = []
    seen = set()
    for windows in window_lists:
        for window in windows:
            identity = (
                window['start_ms'], window['end_ms'], window['owner_id'],
                tuple(window['fields']),
            )
            if identity in seen:
                continue
            seen.add(identity)
            result.append(copy.deepcopy(window))
    return result


def _coalesce_replay_training_promotion_windows(windows):
    """Collapse overlapping scopes owned by the same committed event.

    A replay manifest can describe the same event twice: once as a reviewed
    conflict range and once as a newly discovered candidate range.  The
    promotion helper treats overlapping windows as ambiguous owners and
    consequently drops every accepted row in their intersection.  Windows
    with the same source owner are one bounded phase, so their union is safe;
    windows owned by different events remain separate and still block a
    promotion at an ambiguous overlap.

    This helper is used only for the internal promotion call.  The original
    requested/processed windows remain in replay metadata for auditability.
    """

    result = []
    for raw in windows:
        pending = copy.deepcopy(raw)
        if not isinstance(pending, dict):
            continue
        owner = pending.get('owner_id')
        start = pending.get('start_ms')
        end = pending.get('end_ms')
        fields = pending.get('fields')
        if (not isinstance(owner, str) or type(start) is not int
                or type(end) is not int or not isinstance(fields, list)):
            continue
        fields = sorted(set(fields))
        pending['fields'] = fields
        index = 0
        while index < len(result):
            existing = result[index]
            if (existing.get('owner_id') != owner
                    or pending['start_ms'] > existing['end_ms']
                    or existing['start_ms'] > pending['end_ms']):
                index += 1
                continue
            # A single source phase may have a generic committed-result
            # projection window plus a narrower conflict/candidate window.
            # They can share one bounded promotion scope, but only when their
            # explicit option/flag metadata agrees.  Preserve the projection
            # scope while coalescing; dropping it silently turns result
            # performance awards into ordinary stat-only recovery rows.
            pending_option = pending.get('training_option')
            existing_option = existing.get('training_option')
            if (isinstance(pending_option, str) and pending_option.strip()
                    and isinstance(existing_option, str) and existing_option.strip()
                    and pending_option.strip() != existing_option.strip()):
                index += 1
                continue
            pending_projection = pending.get('source_result_projection')
            existing_projection = existing.get('source_result_projection')
            if (type(pending_projection) is bool and type(existing_projection) is bool
                    and pending_projection != existing_projection):
                index += 1
                continue
            pending['start_ms'] = min(pending['start_ms'], existing['start_ms'])
            pending['end_ms'] = max(pending['end_ms'], existing['end_ms'])
            pending['fields'] = sorted(set(pending['fields']) | set(existing.get('fields', [])))
            if not (isinstance(pending_option, str) and pending_option.strip()) \
                    and isinstance(existing_option, str) and existing_option.strip():
                pending['training_option'] = existing_option.strip()
            performance_fields = sorted(set(
                pending.get('performance_fields', [])
                if isinstance(pending.get('performance_fields'), list) else []
            ) | set(
                existing.get('performance_fields', [])
                if isinstance(existing.get('performance_fields'), list) else []
            ))
            if performance_fields:
                pending['performance_fields'] = performance_fields
            if pending_projection is True or existing_projection is True:
                pending['source_result_projection'] = True
            elif pending_projection is False or existing_projection is False:
                pending['source_result_projection'] = False
            # Preserve the first reason/fps only as descriptive metadata. The
            # promotion helper consumes the unioned bounds, owner, fields,
            # and the preserved result-projection scope.
            for key in ('reason', 'fps'):
                if key not in pending and key in existing:
                    pending[key] = copy.deepcopy(existing[key])
            result.pop(index)
            index = 0
        result.append(pending)
    return sorted(result, key=lambda item: (
        item['start_ms'], item['end_ms'], item['owner_id'], tuple(item['fields']),
    ))


def _preserve_replay_training_phase_provenance(original, fresh, promoted):
    """Carry source phase shape onto newly promoted dense result rows.

    ``training_gain_recovery.promote`` intentionally creates a minimal row
    containing only the requested accepted gains.  That is appropriate for a
    standalone candidate promotion, but it erases the source row's visible
    component/full shape.  The checkpoint-aware transaction resolver needs
    that shape to distinguish a repeated complete badge from a later
    component badge.  Restore only the source-owned shape and explicit option
    on rows introduced from a fresh source frame; no balance, expected value,
    or unrelated fact is copied.
    """

    original_identities = {
        (row.get('source_timestamp_ms'), row.get('evidence'))
        for row in original
        if isinstance(row, dict)
    }
    fresh_by_identity = {
        (row.get('source_timestamp_ms'), row.get('evidence')): row
        for row in fresh
        if isinstance(row, dict)
        and type(row.get('source_timestamp_ms')) is int
        and isinstance(row.get('evidence'), str)
    }
    result = []
    for row in promoted:
        if not isinstance(row, dict):
            result.append(row)
            continue
        identity = (row.get('source_timestamp_ms'), row.get('evidence'))
        source = fresh_by_identity.get(identity)
        if source is None or identity in original_identities:
            result.append(row)
            continue
        source_facts = source.get('facts')
        source_shape = (
            source_facts.get('observed_training_gain_fields')
            if isinstance(source_facts, dict) else None
        )
        if not isinstance(source_shape, (list, tuple)) or any(
                not isinstance(field, str) or not field.strip()
                for field in source_shape):
            source_shape = None
        source_option = source.get('training_option')
        updated = copy.deepcopy(row)
        facts = updated.get('facts')
        if not isinstance(facts, dict):
            facts = {}
        if source_shape is not None:
            facts['observed_training_gain_fields'] = list(source_shape)
        if (isinstance(source_option, str) and source_option.strip()
                and not isinstance(updated.get('training_option'), str)):
            updated['training_option'] = source_option
        updated['facts'] = facts
        result.append(updated)
    return result


def _merge_replay_training_rows(fresh, candidate_rows):
    result = []
    seen = set()
    for row in [*fresh, *candidate_rows]:
        if not isinstance(row, dict) or type(row.get('source_timestamp_ms')) is not int:
            continue
        evidence = row.get('evidence')
        identity = (row['source_timestamp_ms'], evidence if isinstance(evidence, str) else None)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def _replay_training_metadata(metadata, windows, recoveries, group, source_sha256,
                              *, candidate_attempted=False):
    """Attach only source resolver output to replay metadata."""

    from .training_gain_resolution import candidate_recovery_policy

    metadata = copy.deepcopy(metadata)
    if not metadata:
        metadata = {
            'method': 'replayed_bounded_training_gain_recovery',
            'requested_windows': copy.deepcopy(windows),
            'processed_windows': copy.deepcopy(windows),
            'manifest_sha256': group.get('manifest_sha256'),
            'source_sha256': source_sha256,
        }
    else:
        requested = _validate_training_replay_windows(
            metadata.get('requested_windows'), 'requested_windows')
        processed = _validate_training_replay_windows(
            metadata.get('processed_windows'), 'processed_windows')
        active = processed or requested
        active = _merge_replay_training_windows(active, windows)
        if windows:
            metadata['requested_windows'] = _merge_replay_training_windows(requested or active, windows)
            metadata['processed_windows'] = _merge_replay_training_windows(processed or active, windows)
    if recoveries or candidate_attempted:
        prior = metadata.get('candidate_only_recoveries', [])
        if not isinstance(prior, list):
            raise PipelineError('Replay training recovery candidate_only_recoveries must be an array.')
        combined = [*prior]
        seen = {
            (item.get('owner_id'), item.get('field'), item.get('source_timestamp_ms'), item.get('evidence'))
            for item in prior if isinstance(item, dict)
        }
        for item in recoveries:
            identity = (item.get('owner_id'), item.get('field'),
                        item.get('source_timestamp_ms'), item.get('evidence'))
            if identity not in seen:
                combined.append(copy.deepcopy(item))
                seen.add(identity)
        metadata['candidate_only_recoveries'] = combined
        metadata['candidate_only_recovery_policy'] = candidate_recovery_policy()
        metadata['candidate_only_recovery_count'] = sum(
            1 for item in combined
            if isinstance(item, dict) and item.get('status') == 'accepted'
        )
    return metadata


def _replay_recovery_rows(rows,fresh,group,root,source_sha256=None):
    """Promote recovery observations with the same bounded scope as the producer.

    Training candidate crops may arrive through an inspection group rather
    than the historical ``training-gain-recovery`` group.  Recompute only the
    source-owned candidate plan from the parsed rows, then feed those exact
    rows through ``promote_with_metadata`` so the resolver rechecks phase,
    crop geometry, amount text, and physical evidence identity.
    """

    kind=group.get('kind')
    directory=Path(root)/Path(group.get('folder',''))
    metadata={}
    path=directory/'last-plan.json'
    if kind == 'occluded_receipt':
        from .replay_inputs import ReplayInputError, load_bound_recovery_plan
        try:
            metadata = load_bound_recovery_plan(
                root, group.get('plan'), group.get('plan_sha256'),
                source_sha256=source_sha256,
                manifest_sha256=group.get('manifest_sha256'),
            )
        except ReplayInputError as exc:
            raise PipelineError(f'Replay occluded-receipt plan is not bound: {exc}') from exc
    elif path.is_file():
        metadata=_read_json(path)
    if metadata:
        if not isinstance(metadata, dict):
            raise PipelineError(f'Replay recovery metadata must be an object: {path}')
        declared_source=metadata.get('source_sha256')
        if declared_source is not None and declared_source != source_sha256:
            raise PipelineError(f'Replay recovery metadata belongs to another source: {path}')
        declared_manifest=metadata.get('manifest_sha256')
        if declared_manifest is not None and declared_manifest != group.get('manifest_sha256'):
            raise PipelineError(f'Replay recovery manifest metadata is stale: {path}')
        if kind == 'training_gain' and metadata and (
                declared_source != source_sha256
                or declared_manifest != group.get('manifest_sha256')):
            # Candidate proofs are source facts.  A training plan without
            # both bindings cannot be reused; the caller can still recover
            # from the current, validated rows when no mutable plan exists.
            if declared_source != source_sha256:
                raise PipelineError(f'Replay recovery metadata belongs to another source: {path}')
            raise PipelineError(f'Replay recovery manifest metadata is stale: {path}')
        if kind == 'occluded_receipt' and (
                declared_source != source_sha256
                or declared_manifest != group.get('manifest_sha256')):
            # Generic receipt promotion needs its exact source-bound trigger
            # geometry and ownership metadata.  A manifest alone only proves
            # that pixels exist; it does not prove which base receipt they
            # were intended to refine.
            if declared_source != source_sha256:
                raise PipelineError(f'Replay recovery metadata belongs to another source: {path}')
            raise PipelineError(f'Replay recovery manifest metadata is stale: {path}')

    if kind == 'occluded_receipt':
        # Generic occluded-receipt recovery is deliberately replayed through
        # the same source-bound scope selector as its producer.  A cached
        # manifest without a source/manifest-bound last plan has no safe
        # ownership scope, so it must not promote every readable receipt in
        # the inspection window.
        from .inspect_receipts import merge as merge_receipts
        from .occluded_receipt_recovery import (
            _validate_cache_provenance,
            _validate_plan_metadata,
            _window_key,
        )
        manifest_name = group.get('manifest')
        if not isinstance(manifest_name, str) or not manifest_name:
            raise PipelineError('Replay occluded-receipt recovery has no inspection manifest.')
        manifest_path = Path(root) / Path(manifest_name)
        from .replay_inputs import load_bound_inspection_manifest
        try:
            inspection = load_bound_inspection_manifest(root, manifest_name, group.get('manifest_sha256'))
        except ReplayInputError as exc:
            raise PipelineError(f'Replay occluded-receipt inspection is not bound: {exc}') from exc
        try:
            _validate_cache_provenance(inspection, directory, source_sha256)
        except ValueError as exc:
            raise PipelineError(f'Replay occluded-receipt inspection evidence is invalid: {manifest_path}') from exc
        try:
            requested, processed, pending = _validate_plan_metadata(metadata, source_sha256)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc
        # A hash-bound plan proves which metadata was supplied, not that its
        # claimed sampling window was actually inspected. Use the same exact
        # window identity check as the fresh producer before promoting rows.
        inspected_keys = {
            (window['start_ms'], window['end_ms'], window['fps'])
            for window in inspection['windows']
        }
        for window in processed:
            if _window_key(window, metadata['requested_fps']) not in inspected_keys:
                raise PipelineError('Replay occluded-receipt processed window was not inspected.')
        windows = processed
        if not metadata:
            raise PipelineError('Replay occluded-receipt recovery has no source-bound plan.')
        if not windows:
            return rows, metadata
        from .occluded_receipt_recovery import scoped_observations
        promoted = scoped_observations(rows, fresh, windows)
        return merge_receipts(rows, promoted), metadata

    if kind=='numeric_receipt':
        from .inspect_receipts import merge as merge_receipts
        from .receipt_recovery import scoped_observations
        windows=metadata.get('requested_windows') or metadata.get('processed_windows')
        promoted=scoped_observations(rows,fresh,windows) if windows else fresh
        return merge_receipts(rows,promoted),metadata

    if kind=='training_gain':
        # Validate mutable plans before deciding whether to reuse them.  The
        # actual amounts remain inside source crop observations and are never
        # copied from this metadata.
        requested = _validate_training_replay_windows(
            metadata.get('requested_windows'), 'requested_windows')
        processed = _validate_training_replay_windows(
            metadata.get('processed_windows'), 'processed_windows')
        manifest_ranges = _replay_training_manifest_ranges(group, directory)
        source_rows = _replay_training_source_rows(rows, fresh)
        events = _replay_training_events(source_rows) if source_rows else []
        conflict_windows = _replay_training_conflict_windows(rows, manifest_ranges, events)
        candidate_windows = _replay_training_candidate_windows(rows, fresh, events)
        existing_windows = processed or requested
        windows = _merge_replay_training_windows(
            existing_windows, conflict_windows, candidate_windows)
        # Reviewed conflict ranges and recomputed candidate ranges can overlap
        # for the same event.  Keep the exact ranges in metadata, but give the
        # promotion helper one coalesced scope per owner so accepted dense
        # frames are retained for the later checkpoint-aware phase resolver.
        promotion_windows = _coalesce_replay_training_promotion_windows(windows)

        candidate_rows, selected_candidates = _replay_training_candidate_rows(
            rows, fresh, promotion_windows, events,
        )
        # Filter the complete fresh set even when no candidate row survived
        # ownership checks.  Otherwise the fallback ``promote`` call would
        # receive option-less candidates directly and its source resolver
        # (which intentionally does not decide event ownership) could still
        # promote them.
        promotion_fresh = _replay_training_filter_promotion_rows(
            fresh, promotion_windows, selected_candidates,
        )
        if candidate_rows:
            from .training_gain_recovery import promote_with_metadata
            promotion_fresh = _merge_replay_training_rows(
                promotion_fresh, candidate_rows,
            )
            promoted, recoveries = promote_with_metadata(
                rows, promotion_fresh, promotion_windows,
            )
            promoted = _preserve_replay_training_phase_provenance(
                rows, fresh, promoted,
            )
            metadata = _replay_training_metadata(
                metadata, windows, recoveries, group, source_sha256,
                candidate_attempted=True)
            return promoted, metadata

        from .training_gain_recovery import promote
        promoted=promote(rows,promotion_fresh,promotion_windows)
        promoted = _preserve_replay_training_phase_provenance(
            rows, fresh, promoted,
        )
        if windows and not metadata:
            metadata = _replay_training_metadata(
                metadata, windows, [], group, source_sha256)
        return promoted,metadata

    if kind=='boundary_state':
        from .boundary_state_recovery import promote
        windows=metadata.get('processed_windows') or metadata.get('requested_windows')
        return promote(rows,fresh,windows or []),metadata
    raise PipelineError(f'Unsupported replay recovery kind: {kind!r}')


def _occluded_receipt_plan_metadata(report, readings):
    """Describe pending generic receipt windows without performing OCR.

    Manifest replay is intentionally read-only.  When a cache has not yet
    registered the generic recovery namespace, retain the source-bound plan in
    the new report instead of presenting a clean replay that silently skipped
    the unresolved receipt lines.  The separate preparation pass can execute
    these windows on a disposable clone and register its immutable proofs.
    """

    from .occluded_receipt_recovery import SCHEMA, plan
    from .transactions import outcome_events

    source = report.get('source', {}) if isinstance(report, dict) else {}
    duration = source.get('duration_ms') if isinstance(source, dict) else None
    if type(duration) is not int or duration <= 0:
        requested = []
    else:
        try:
            requested = plan(readings, outcome_events(readings), duration)
        except (KeyError, TypeError, ValueError) as exc:
            raise PipelineError('Could not prepare the generic receipt recovery plan.') from exc
    return dict(
        schema=SCHEMA,
        method='bounded_source_bound_occluded_receipt_recovery',
        source_sha256=source.get('sha256') if isinstance(source, dict) else None,
        requested_windows=requested,
        processed_windows=[],
        pending_windows=copy.deepcopy(requested),
        new_frames=0,
        manifest_sha256=None,
        unpromoted_observations=[],
        complete_event_history=False,
        preparation_required=bool(requested),
    )


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


def load_replay_input_bundle(manifest_path,root,*,expected_source_sha256=None,fps=4,allow_partial=False):
    """Load a validated source-bound manifest through the common producer parsers.

    ``root`` is the disposable cache/output root. The function only reads it;
    callers decide where to write the resulting report. Every parsed proof is
    rebased to ``root`` so the worker evidence root remains the actual run
    directory, including nested own-root inspection namespaces.
    """
    from .replay_inputs import ReplayInputError,load_manifest
    root=Path(root).resolve()
    try:
        normalized=load_manifest(manifest_path,root,expected_source_sha256=expected_source_sha256)
    except ReplayInputError as exc:
        raise PipelineError(f'Replay input manifest invalid ({exc.code}): {exc}') from exc
    source_sha256=normalized['source_sha256']
    base_spec=normalized['base'];base_root=root/Path(base_spec['folder'])
    # ``replay_inputs`` normalizes the capture and neural paths relative to the
    # manifest root.  The declared base folder is retained separately for
    # cached frame/evidence lookup; prepending it again breaks manifests whose
    # base lives below a nested folder (for example independent-02).
    capture_path=root/Path(base_spec['capture'])
    capture=_load_cached_capture(capture_path,source_sha256,float(fps))
    rows=cached_readings(
        capture,
        base_root,
        allow_partial=allow_partial,
        status_badge_root=root,
        source_sidecars=normalized.get('supplements', []),
        sidecar_root=root,
    )
    rows,supplement_audit=_apply_manifest_supplements(
        normalized,root,base_root,capture,rows,early_applied=True)
    rows=_rebase_paths(rows,base_root,root)
    report=_rebase_paths(copy.deepcopy(capture),base_root,root)
    inspections=[]
    for group in normalized.get('inspections',[]):
        fresh,own_root=_manifest_group_rows(root,group)
        fresh=_rebase_paths(fresh,own_root,root)
        from .inspect_training import merge as merge_training
        from .inspect_receipts import merge as merge_receipts
        merger={'training':merge_training,'receipt':merge_receipts}[group['merge']]
        rows=merger(rows,fresh)
        inspections.append(dict(id=group['id'],kind='inspection',rows=len(fresh),own_root=group['own_root'],
                                manifest=group['manifest'],manifest_sha256=group['manifest_sha256']))
    recoveries=[];recovery_metadata={}
    for group in normalized.get('recovery',[]):
        fresh,own_root=_manifest_group_rows(root,group)
        fresh=_rebase_paths(fresh,own_root,root)
        rows,metadata=_replay_recovery_rows(rows,fresh,group,root,source_sha256)
        kind=group['kind'];recovery_metadata[kind]=metadata
        recoveries.append(dict(kind=kind,rows=len(fresh),own_root=group['own_root'],manifest=group['manifest'],
                               manifest_sha256=group['manifest_sha256']))
    report=copy.deepcopy(report)
    recovery_report_fields = {
        'numeric_receipt': 'numeric_receipt_recovery',
        'training_gain': 'training_gain_recovery',
        'boundary_state': 'boundary_state_recovery',
        'occluded_receipt': 'occluded_receipt_recovery',
    }
    for kind,metadata in recovery_metadata.items():
        report[recovery_report_fields[kind]]=metadata
    if 'occluded_receipt' not in recovery_metadata:
        report['occluded_receipt_recovery'] = _occluded_receipt_plan_metadata(report, rows)
    return dict(manifest=normalized,report=report,readings=rows,base_root=base_root,
                inspections=inspections,recoveries=recoveries,supplements=supplement_audit)


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
    parser.add_argument('--max-auto-refinement-frames',type=int,default=512,
                        help='Maximum weak performance-panel frames to reread automatically.')
    parser.add_argument('--max-status-refinement-frames',type=int,default=512,
                        help='Maximum weak status-badge frames to reread automatically.')
    parser.add_argument('--replay-input-manifest',type=Path,
                        help='Use a validated source-bound replay-input manifest instead of fixed cache names.')
    parser.add_argument('--replay-input-root',type=Path,
                        help='Disposable cache root for --replay-input-manifest (defaults to --output).')
    args=parser.parse_args()
    if not 1<=args.workers<=8:parser.error('Use 1 to 8 workers.')
    if args.replay_input_manifest:
        if not args.reparse_only:
            raise PipelineError('--replay-input-manifest requires --reparse-only; it never starts OCR.')
        output_root=args.output.resolve()
        input_root=(args.replay_input_root or args.output).resolve()
        if input_root!=output_root:
            raise PipelineError('--replay-input-root must equal --output so emitted evidence stays in the worker root.')
        source_path=args.source.resolve()
        try:
            with source_path.open('rb') as stream:
                source_sha256=hashlib.file_digest(stream,'sha256').hexdigest()
        except OSError as exc:
            raise PipelineError(f'Could not read replay source: {source_path}') from exc
        bundle=load_replay_input_bundle(args.replay_input_manifest,input_root,
                                        expected_source_sha256=source_sha256,fps=args.fps)
        report=bundle['report'];readings=bundle['readings']
        _CURRENT.update(report=report,output=output_root)
        _progress('replay_loaded',readings=len(readings))
        source_sha256=report['source']['sha256']
        from .inspect_choices import load as load_choices
        choice_metadata,choice_observations=load_choices(input_root,source_sha256)
        if choice_metadata:report['choice_inspection']=choice_metadata
        from .race_reward_inspection import load as load_race_rewards
        reward_metadata,reward_observations=load_race_rewards(input_root,source_sha256)
        if reward_metadata:report['race_reward_inspection']=reward_metadata
        from .hint_card_cache import load as load_hint_cards
        try:
            hint_observations=load_hint_cards(readings,input_root,source_sha256)
        except ValueError as exc:
            raise PipelineError(f'Hint-card cache evidence invalid: {exc}') from exc
        from .automatic_refinement import run as run_automatic_refinement
        # The manifest report is rebased to the disposable root, but its
        # immutable neural/capture namespace remains the declared base folder.
        # Discover against that namespace so nested layouts (for example
        # ``initial-baseline/neural``) are neither missed nor double-prefixed.
        automatic_refinement=run_automatic_refinement(
            report,bundle['base_root'],allow_ocr=False,model_dir=args.model_dir,
            max_panel_frames=args.max_auto_refinement_frames,
            max_status_frames=args.max_status_refinement_frames,
        )
        report['automatic_refinement']=automatic_refinement
        save_json(output_root/'automatic-refinement.json',automatic_refinement)
        choice_source=build_event_choice_observations(readings,input_root,choice_observations)
        report=assemble(report,readings,choice_observations,reward_observations,hint_observations,
                        source_root=input_root,
                        committed_choices=choice_source['committed_choices'],
                        event_choice_observations=choice_source)
        _CURRENT['report']=report
        _progress('stage_done',name='assemble')
        validate_output(report,require_gameplay=True,source_root=input_root)
        _progress('stage_done',name='validate_output')
        save_json(output_root/'report.json',report)
        _progress('stage_done',name='save_report')
        _write_timeline_and_viewer(report,output_root)
        print(json.dumps(dict(stage='complete',report=str(output_root/'report.json'),fully_verified=False)),flush=True)
        return
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
        readings=cached_readings(report,args.output,workers=args.workers)
        _progress('stage_done',name='reload_readings',readings=len(readings),workers=args.workers)
        _guarded(report,'hint_card_preparation',lambda: _prepare_fresh_hint_cards(
            readings,
            args.output,
            report['source']['sha256'],
            model_dir=args.model_dir,
        ),fallback=None)
        _progress('stage_done',name='hint_card_preparation')
    supplemental=args.output/'training-inspection.json'
    if supplemental.exists():
        from .inspect_training import merge,reparse_inspection
        inspection=json.loads(supplemental.read_text(encoding='utf-8'))
        if inspection['source_sha256']!=report['source']['sha256']:raise PipelineError('Training inspection belongs to another source.')
        readings=merge(readings,reparse_inspection(inspection,args.output))
        report['training_inspection']=dict(windows=inspection['windows'],requested_fps=30,method='training_result_layout_and_gain_inspection')
    native=args.output/'native-inspection.json'
    if native.exists():
        from .inspect_training import merge,reparse_inspection
        inspection=json.loads(native.read_text(encoding='utf-8'))
        if inspection['source_sha256']!=report['source']['sha256']:raise PipelineError('Native inspection belongs to another source.')
        readings=merge(readings,reparse_inspection(inspection,args.output))
        report['native_inspection']=dict(windows=inspection['windows'],requested_fps=60,method='unresolved_training_animation')
    receipts=args.output/'receipt-inspection.json'
    if receipts.exists():
        from .inspect_training import reparse_inspection
        from .inspect_receipts import merge as merge_receipts
        inspection=json.loads(receipts.read_text(encoding='utf-8'))
        if inspection['source_sha256']!=report['source']['sha256']:raise PipelineError('Receipt inspection belongs to another source.')
        readings=merge_receipts(readings,reparse_inspection(inspection,args.output))
        report['receipt_inspection']=dict(windows=inspection['windows'],method='bounded_receipt_review')
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
    from .inspect_choices import load as load_choices
    choice_metadata,choice_observations=_guarded(report,'choice_inspection',lambda: load_choices(args.output,report['source']['sha256']),fallback=(None,[]))
    if choice_metadata:report['choice_inspection']=choice_metadata
    from .race_reward_inspection import load as load_race_rewards
    reward_metadata,reward_observations=_guarded(report,'race_reward_inspection',lambda: load_race_rewards(args.output,report['source']['sha256']),fallback=(None,[]))
    if reward_metadata:report['race_reward_inspection']=reward_metadata
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
                    source_root=args.output,
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
                        source_root=args.output,
                        committed_choices=committed,
                        event_choice_observations=choice_source)
        _CURRENT['report']=report
        _progress('stage_done',name='assemble_after_boundary')
    validate_output(report,require_gameplay=True,source_root=args.output)
    _progress('stage_done',name='validate_output')
    evidence_audit=args.output/'evidence-audit.json'
    if evidence_audit.exists():
        snapshot=json.loads(evidence_audit.read_text(encoding='utf-8'))
        manifest_hash=hashlib.sha256((args.output/'capture.json').read_bytes()).hexdigest()
        manifests={p.relative_to(args.output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (
            supplemental,native,receipts,args.output/'choice-inspection.json',args.output/'race-reward-inspection.json',
            args.output/'numeric-receipt-recovery/receipt-inspection.json',
            args.output/'training-gain-recovery/receipt-inspection.json',
            args.output/'occluded-receipt-recovery/receipt-inspection.json',
            args.output/'boundary-state-recovery/receipt-inspection.json') if p.exists()}
        if snapshot.get('source_sha256')==report['source']['sha256'] and snapshot.get('capture_manifest_sha256')==manifest_hash and snapshot.get('inspection_manifest_sha256')==manifests:
            report['evidence_integrity_snapshot']=snapshot
    save_json(args.output/'report.json',report)
    _progress('stage_done',name='save_report')
    _write_timeline_and_viewer(report,args.output)
    print(json.dumps(dict(stage='complete',report=str(args.output/'report.json'),fully_verified=False)),flush=True)


def _write_timeline_and_viewer(report,root):
    """Write the compact timeline document and the HTML viewer; neither is fatal."""
    from .timeline_document import write as write_timeline
    root=Path(root)
    size=_guarded(report,'timeline_document',lambda: write_timeline(report,root/'timeline.json'),fallback=None)
    _progress('stage_done',name='timeline_document',bytes=size)
    html=_guarded(report,'viewer',lambda: render(report),fallback=None)
    if html is not None:(root/'index.html').write_text(html,encoding='utf-8')
    _progress('stage_done',name='viewer')

if __name__=='__main__':main()
