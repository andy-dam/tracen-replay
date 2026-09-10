"""Resumable full-recording capture and local neural OCR. Processing is not verification."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
import threading
import time
from .pipeline import probe,decode_frames,PipelineError
from .vision import NeuralReader,parse
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


def validate_output(report, *, require_gameplay=False):
    """Convert contract failures into the pipeline's public error type."""
    try:
        return validate_report(report, require_gameplay=require_gameplay)
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


def parse_receipt_pixels(raw,root,frame,original=None,*,source_sha256=None):
    """Apply source-bound receipt checks before interpreting an OCR observation."""
    from .receipt_occlusion import annotate_path
    root=Path(root)
    evidence_path=root/raw['evidence']
    checked=annotate_path(raw,evidence_path,original,source_sha256=source_sha256)
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
    from .receipt_symbols import annotate, has_eligible_receipt
    if has_eligible_receipt(checked['lines']):
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
            checked=annotate(checked,pane,proof)
    return parse(checked)


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


def analyze_frames(report,root,workers=4,model_dir='.local/models/rapidocr'):
    root=Path(root);cache=root/'neural';cache.mkdir(exist_ok=True);evidence=root/'gameplay';evidence.mkdir(exist_ok=True)
    local=threading.local()
    def initialize():local.reader=NeuralReader(model_dir)
    def process(frame):
        reader=local.reader
        image_path=root/frame['evidence'];digest=hashlib.sha256(image_path.read_bytes()).hexdigest();path=cache/(frame['id']+'.json')
        if path.exists():
            saved=json.loads(path.read_text(encoding='utf-8'))
            if saved.get('engine_fingerprint')==reader.fingerprint and saved.get('source_frame_sha256')==digest:
                return frame['id'],saved,True
        with reader.Image.open(image_path) as image:pane=image.convert('RGB').crop((148,0,958,1080))
        raw=reader.read(pane)
        relative=f'gameplay/{frame["id"]}.png';pane.save(root/relative)
        raw.update(source_timestamp_ms=frame['source_timestamp_ms'],evidence=relative,source_frame_sha256=digest)
        save_json(path,raw)
        return frame['id'],raw,False
    started=time.monotonic();completed=0;cached=0;raws={}
    with ThreadPoolExecutor(max_workers=workers,initializer=initialize) as executor:
        futures=[executor.submit(process,frame) for frame in report['frames']]
        for future in as_completed(futures):
            key,raw,hit=future.result();raws[key]=raw;completed+=1;cached+=hit
            if completed%100==0 or completed==len(futures):
                progress=dict(stage='ocr',processed=completed,total=len(futures),cached=cached,elapsed_seconds=round(time.monotonic()-started))
                save_json(root/'progress.json',progress);print(json.dumps(progress),flush=True)
    readings=[]
    for frame in report['frames']:
        raw=raws[frame['id']];row=parse_receipt_pixels(raw,root,frame,source_sha256=report.get('source',{}).get('sha256'));row.update(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']);readings.append(row)
    return readings


def assemble(report,readings,choice_observations=(),race_reward_observations=(),hint_card_observations=None):
    from .inventory import summarize as inventory_summary
    if hint_card_observations is None:
        hint_card_observations=report.get('gameplay_tracking',{}).get('hint_card_observations',[])
    if race_reward_observations:
        from .race_reward_inspection import refine_base_rows
        readings=refine_base_rows(readings,race_reward_observations)
    stat_rows=[dict(r['stats'],source_timestamp_ms=r['source_timestamp_ms'],evidence=r['evidence']) for r in readings]
    spans=screen_summary(readings)
    report['gameplay_tracking']=dict(method='neural_gameplay_v1',auxiliary_log_used=False,input_region=[148,0,958,1080],readings=readings,
        **reconstruct(readings,choice_observations,race_reward_observations,
                      hint_card_observations=hint_card_observations,source_sha256=report.get('source',{}).get('sha256')),
        screens=spans,training_previews=preview_segments(stat_rows),skill_receipts=[s for s in spans if s['screen']=='skill_receipt'])
    if hint_card_observations:
        report['gameplay_tracking']['hint_card_observations']=copy.deepcopy(hint_card_observations)
    if race_reward_observations:
        report['gameplay_tracking']['race_reward_observations']=race_reward_observations
    report['recognition']=dict(enabled=True,model='RapidOCR 3.9.2 / PP-OCRv6 detection + English PP-OCRv5 recognition')
    report['gameplay_tracking']['owned_skill_inventory']=inventory_summary(readings)
    report['verification']=audit(report)
    return report


def cached_readings(report,root,allow_partial=False):
    """Reinterpret immutable OCR observations without rerunning models."""
    root=Path(root);readings=[]
    for frame in report['frames']:
        path=root/'neural'/(frame['id']+'.json')
        if not path.exists():
            if allow_partial:continue
            raise PipelineError(f'Missing OCR observation: {frame["id"]}')
        raw=json.loads(path.read_text(encoding='utf-8'))
        if raw.get('source_timestamp_ms')!=frame['source_timestamp_ms']:
            raise PipelineError(f'OCR timestamp mismatch: {frame["id"]}')
        if raw.get('source_frame_sha256')!=hashlib.sha256((root/frame['evidence']).read_bytes()).hexdigest():
            raise PipelineError(f'OCR source evidence mismatch: {frame["id"]}')
        if not raw.get('model_sha256') or not raw.get('engine_fingerprint') or not (root/raw['evidence']).is_file():
            raise PipelineError(f'Incomplete OCR provenance: {frame["id"]}')
        original=raw
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
        row=parse_receipt_pixels(raw,root,frame,original,source_sha256=report.get('source',{}).get('sha256'))
        if 'base_receipt_refinement' in raw:
            row['base_receipt_refinement']=raw['base_receipt_refinement']
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
            row=apply_offers(row,original,json.loads(offers.read_text(encoding='utf-8')),root/raw['evidence'])
        choice=root/'choice-refinement'/path.name
        if choice.exists():
            from .refine_choices import apply as apply_choice
            row=apply_choice(row,original,json.loads(choice.read_text(encoding='utf-8')),root/raw['evidence'])
        choice_cards=root/'choice-card-refinement'/path.name
        if choice_cards.exists():
            from .choice_card_refinement import apply as apply_choice_cards
            row=apply_choice_cards(row,original,json.loads(choice_cards.read_text(encoding='utf-8')),root/raw['evidence'])
        row.update(source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']);readings.append(row)
    return readings


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--fps',type=float,default=4);parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--model-dir',type=Path,default=Path('.local/models/rapidocr'))
    parser.add_argument('--reparse-only',action='store_true',help='Verify cached source observations and apply the current parser without OCR.')
    args=parser.parse_args()
    if not 1<=args.workers<=8:parser.error('Use 1 to 8 workers.')
    report=capture(args.source,args.output,args.fps)
    readings=cached_readings(report,args.output) if args.reparse_only else analyze_frames(report,args.output,args.workers,args.model_dir)
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
    from .inspect_choices import load as load_choices
    choice_metadata,choice_observations=load_choices(args.output,report['source']['sha256'])
    if choice_metadata:report['choice_inspection']=choice_metadata
    from .race_reward_inspection import load as load_race_rewards
    reward_metadata,reward_observations=load_race_rewards(args.output,report['source']['sha256'])
    if reward_metadata:report['race_reward_inspection']=reward_metadata
    from .hint_card_cache import load as load_hint_cards
    try:
        hint_observations=load_hint_cards(readings,args.output,report['source']['sha256'])
    except ValueError as exc:
        raise PipelineError(f'Hint-card cache evidence invalid: {exc}') from exc
    report=assemble(report,readings,choice_observations,reward_observations,hint_observations)
    validate_output(report,require_gameplay=True)
    evidence_audit=args.output/'evidence-audit.json'
    if evidence_audit.exists():
        snapshot=json.loads(evidence_audit.read_text(encoding='utf-8'))
        manifest_hash=hashlib.sha256((args.output/'capture.json').read_bytes()).hexdigest()
        manifests={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (supplemental,native,receipts,args.output/'choice-inspection.json',args.output/'race-reward-inspection.json') if p.exists()}
        if snapshot.get('source_sha256')==report['source']['sha256'] and snapshot.get('capture_manifest_sha256')==manifest_hash and snapshot.get('inspection_manifest_sha256')==manifests:
            report['evidence_integrity_snapshot']=snapshot
    save_json(args.output/'report.json',report)
    (args.output/'index.html').write_text(render(report),encoding='utf-8')
    print(json.dumps(dict(stage='complete',report=str(args.output/'report.json'),fully_verified=False)),flush=True)

if __name__=='__main__':main()
