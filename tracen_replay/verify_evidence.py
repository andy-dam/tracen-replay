"""Verify the source, decoded samples, gameplay crops and refinement provenance."""
import argparse
import hashlib
import json
from pathlib import Path
from fractions import Fraction
from PIL import Image
from .full_recording import save_json
from .refine_contrast import fingerprint


def inside(root,relative):
    path=(root/relative).resolve()
    if not path.is_relative_to(root.resolve()):raise ValueError('Evidence path leaves the run directory.')
    return path


def verify(root,source):
    root=Path(root).resolve();capture=json.loads((root/'capture.json').read_text(encoding='utf-8'))
    with Path(source).open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    errors=[];records=[]
    if digest!=capture['source']['sha256']:errors.append(dict(reason='source_hash_mismatch'))
    for frame in capture['frames']:
        records.append((root/'neural'/(frame['id']+'.json'),inside(root,frame['evidence']),frame))
    manifests={};inspection_hashes={}
    for name in ('training-inspection.json','native-inspection.json','receipt-inspection.json','choice-inspection.json'):
        path=root/name
        if not path.exists():continue
        inspection_hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        inspection=json.loads(path.read_text(encoding='utf-8'))
        if inspection['source_sha256']!=digest:errors.append(dict(reason='inspection_source_mismatch',path=name))
        for row in inspection['readings']:
            evidence=inside(root,row['evidence']);raw_path=evidence.with_suffix('.v2.json')
            directory=evidence.parent
            if directory not in manifests:manifests[directory]={f['id']:f for f in json.loads((directory/'frames.json').read_text(encoding='utf-8'))}
            frame=manifests[directory][evidence.stem]
            records.append((raw_path,inside(root,directory.relative_to(root)/frame['evidence']),frame))
    checked=0;refinements=0
    for raw_path,frame_path,frame in records:
        try:
            time=frame['source_timestamp_ms']
            pts_time=round((float(frame['source_pts']*Fraction(frame['time_base']))-capture['source'].get('timeline_origin_seconds',0))*1000)
            if abs(pts_time-time)>1:raise ValueError('Source PTS differs from sample timestamp.')
            raw=json.loads(raw_path.read_text(encoding='utf-8'));evidence=inside(root,raw['evidence'])
            if raw['source_timestamp_ms']!=time:raise ValueError('OCR timestamp differs from capture manifest.')
            if hashlib.sha256(frame_path.read_bytes()).hexdigest()!=raw['source_frame_sha256']:raise ValueError('Decoded frame hash changed.')
            with Image.open(frame_path) as frame:
                pane=frame.convert('RGB').crop((148,0,958,1080));pixels=hashlib.sha256(pane.tobytes()).hexdigest()
            with Image.open(evidence) as image:
                if image.size!=(810,1080) or hashlib.sha256(image.convert('RGB').tobytes()).hexdigest()!=pixels:raise ValueError('Gameplay proof differs from source crop.')
            if pixels!=raw['gameplay_sha256']:raise ValueError('OCR pixels differ from source gameplay crop.')
            proof_hash=hashlib.sha256(evidence.read_bytes()).hexdigest();raw_hash=fingerprint(raw)
            extras=[raw_path.with_suffix('.'+suffix+'.json') for suffix in ('totals','contrast','performance','awards','receipt')]
            extras.append(evidence.with_suffix('.overlay.json'))
            extras.append(evidence.with_suffix('.choice.json'))
            if raw_path.parent.name=='neural':extras += [root/folder/raw_path.name for folder in ('outcome-refinement','currency-refinement','skill-variants','skill-points-refinement','currency-padding-refinement','choice-refinement','choice-card-refinement','song-symbols','song-symbol-refinement','inventory-refinement','concert-panel-refinement')]
            for extra_path in extras:
                if not extra_path.exists():continue
                extra=json.loads(extra_path.read_text(encoding='utf-8'))
                if not isinstance(extra,dict):raise ValueError('Refinement must be a JSON object: '+str(extra_path.relative_to(root)))
                if extra['raw_sha256']!=raw_hash or extra['evidence_sha256']!=proof_hash:raise ValueError('Refinement provenance mismatch: '+str(extra_path.relative_to(root)))
                if extra_path.parent.name=='concert-panel-refinement':
                    from .concert_panel_refinement import apply as apply_concert_panel
                    apply_concert_panel(raw,extra,evidence,observation_root=root)
                if extra_path.parent.name=='choice-card-refinement':
                    from .choice_card_refinement import apply as apply_choice_cards
                    apply_choice_cards({'screen':'unknown'},raw,extra,evidence)
                if extra_path.parent.name=='song-symbol-refinement':
                    from .song_symbol_refinement import apply as apply_song_symbol
                    apply_song_symbol(raw,extra,evidence)
                refinements+=1
            checked+=1
        except (ValueError,KeyError,OSError) as error:errors.append(dict(path=str(raw_path.relative_to(root)),reason=str(error)))
        if (checked+len(errors))%1000==0:print(json.dumps(dict(stage='verify_evidence',checked=checked,total=len(records),errors=len(errors))),flush=True)
    race_reward_frames=0
    race_manifest=root/'race-reward-inspection.json'
    if race_manifest.exists():
        inspection_hashes[race_manifest.name]=hashlib.sha256(race_manifest.read_bytes()).hexdigest()
        try:
            from .race_reward_inspection import load as load_race_rewards
            metadata,_=load_race_rewards(root,digest)
            race_reward_frames=metadata['verified_frames']
        except (ValueError,KeyError,OSError) as error:
            errors.append(dict(path=race_manifest.name,reason=str(error)))
    result=dict(source_sha256=digest,source_duration_ms=capture['source']['duration_ms'],base_frames=len(capture['frames']),
                expected_observations=len(records),verified_observations=checked,verified_refinements=refinements,
                capture_manifest_sha256=hashlib.sha256((root/'capture.json').read_bytes()).hexdigest(),
                inspection_manifest_sha256=inspection_hashes,
                evidence_integrity_verified=not errors and checked==len(records),errors=errors,
                verified_race_reward_inspection_frames=race_reward_frames,
                scope='Source file, sampled PTS manifests, decoded frame hashes, exact gameplay crop pixels, original OCR pixel hashes and refinement source/proof hashes. This is not semantic effect-recall verification.')
    save_json(root/'evidence-audit.json',result);print(json.dumps(result),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();result=verify(args.output,args.source)
    raise SystemExit(0 if result['evidence_integrity_verified'] else 1)
