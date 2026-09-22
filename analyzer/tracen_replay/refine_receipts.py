"""Re-read receipt text with padded crops; never supply expected text to OCR."""
import hashlib
import json
from .gameplay import effects_from_lines


def fingerprint(raw):
    return hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()


def consensus(views):
    # These are correlated views of ONE frame, not three independent observations.
    texts={v['text'].strip() for v in views}
    if len(views)!=3:return None
    if len(texts)==1 and receipt(next(iter(texts))) and min(v['confidence'] for v in views)>=95 and max(v['confidence'] for v in views)>=97:
        return dict(text=next(iter(texts)),confidence=min(v['confidence'] for v in views))
    valid=[v for v in views if v['confidence']>=95 and receipt(v['text'])]
    strong=[v for v in valid if v['confidence']>=97]
    if len(strong)>=2 and len({receipt(v['text']) for v in valid})==1:
        return dict(text=strong[0]['text'],confidence=min(v['confidence'] for v in strong))
    return None


def receipt(text):
    effects=effects_from_lines([dict(text=text,confidence=100)])
    if not effects:return None
    return json.dumps([{k:v for k,v in e.items() if k not in ('raw_text','confidence')} for e in effects],sort_keys=True)


def apply(raw,extra):
    if extra['raw_sha256']!=fingerprint(raw):raise ValueError('Receipt refinement source mismatch.')
    result=dict(raw,lines=[dict(line) for line in raw['lines']])
    for item in extra['lines']:
        accepted=consensus(item['views'])
        if accepted:
            original=result['lines'][item['index']]
            result['lines'][item['index']]=dict(original,**accepted,original_text=original['text'],
                original_confidence=original['confidence'],receipt_crop_views=item['views'])
        else:
            original=result['lines'][item['index']]
            alternatives={receipt(v['text']) for v in item['views'] if v['confidence']>=95 and receipt(v['text'])}
            if alternatives and alternatives!={receipt(original['text'])}:
                # An unresolved crop disagreement invalidates the original confident
                # reading; it does not authorize choosing one of the alternatives.
                result['lines'][item['index']]=dict(original,confidence=0,original_confidence=original['confidence'],
                    receipt_crop_views=item['views'],receipt_crop_conflict=True)
    return result

