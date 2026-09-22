"""Inspect every detected training result at 30 FPS using only gameplay pixels."""
import hashlib
import json
from pathlib import Path
from .pipeline import PipelineError


def reparse_inspection(inspection,root):
    result=[];root=Path(root);manifests={}
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
        # Every dense refinement hashes the immutable raw record.
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
        from .full_recording import parse_receipt_pixels
        source_frame=dict(frame,evidence=(evidence.parent/frame['evidence']).relative_to(root).as_posix())
        parsed = parse_receipt_pixels(raw,root,source_frame,original)
        result.append(dict(parsed,source_timestamp_ms=raw['source_timestamp_ms'],evidence=raw['evidence']))
    return result
