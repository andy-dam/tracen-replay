"""Recheck obscured training digits with two contrast views of source pixels."""
import hashlib
import json
import re


def fingerprint(raw):
    return hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()


def apply_contrast_refinement(raw, refinement, original=None):
    if refinement['raw_sha256'] != fingerprint(original or raw):
        raise ValueError('Contrast refinement source mismatch.')
    regions=dict(raw['regions'])
    for name,views in refinement['regions'].items():
        # These views are correlated; agreement does not count as multiple
        # frames. Temporal and before/result checks still happen downstream.
        if len(views)!=2 or any(v['confidence']<97 for v in views):continue
        texts={v['text'] for v in views}
        if len(texts)!=1:continue
        text=texts.pop()
        ratio=re.fullmatch(r'(\d{1,4})/(\d{4})',text)
        valid=bool(ratio and int(ratio[1])<=int(ratio[2]))
        if name=='result.skill_points':valid=bool(re.fullmatch(r'\d{1,4}',text))
        if valid:regions[name]=dict(views[0],confidence=min(v['confidence'] for v in views),contrast_consensus=True)
    return dict(raw,regions=regions)

