"""Store gameplay choice observations separately from immutable OCR."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image
from .choice_evidence import observe
from .refine_contrast import fingerprint
from .vision import parse


def apply(row,raw,extra,proof):
    if extra['raw_sha256']!=fingerprint(raw) or extra['evidence_sha256']!=hashlib.sha256(proof.read_bytes()).hexdigest():
        raise ValueError('Choice observation provenance mismatch.')
    if extra.get('version')!=1:raise ValueError('Unsupported choice observation version.')
    if row['screen']!='unknown':return row
    return dict(row,facts=dict(row.get('facts',{}),choice_observation=extra['observation']))


def refine(root):
    root=Path(root);directory=root/'choice-refinement';directory.mkdir(exist_ok=True);count=0
    for path in sorted((root/'neural').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf-8'))
        if parse(raw)['screen']!='unknown':continue
        proof=root/raw['evidence'];target=directory/path.name
        if target.exists():
            apply(parse(raw),raw,json.loads(target.read_text(encoding='utf-8')),proof)
            continue
        with Image.open(proof) as pane:
            if hashlib.sha256(pane.convert('RGB').tobytes()).hexdigest()!=raw['gameplay_sha256']:
                raise ValueError('Choice proof pixels differ from original OCR pixels.')
            observation=observe(pane,raw['lines'])
        extra=dict(version=1,raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),
                   observation=observation,independent_observations=False)
        target.write_text(json.dumps(extra,indent=2),encoding='utf-8');count+=1
    print(json.dumps(dict(stage='choice_refinement',new_observations=count)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    refine(parser.parse_args().output)
