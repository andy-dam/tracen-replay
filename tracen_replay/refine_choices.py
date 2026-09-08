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
    if extra.get('version')!=2:raise ValueError('Regenerate choice observations with refine_choices (version 2 required).')
    if row['screen']!='unknown':return row
    return dict(row,facts=dict(row.get('facts',{}),choice_observation=extra['observation']))


def refine(root):
    root=Path(root);directory=root/'choice-refinement';directory.mkdir(exist_ok=True);count=0
    for path in sorted((root/'neural').glob('*.json')):
        raw=json.loads(path.read_text(encoding='utf-8'))
        if parse(raw)['screen']!='unknown':continue
        proof=root/raw['evidence'];target=directory/path.name
        if target.exists():
            previous=json.loads(target.read_text(encoding='utf-8'))
            if previous.get('version')==2:
                apply(parse(raw),raw,previous,proof)
                continue
            if previous.get('version')!=1:raise ValueError('Unsupported choice observation version.')
            if previous['raw_sha256']!=fingerprint(raw) or previous['evidence_sha256']!=hashlib.sha256(proof.read_bytes()).hexdigest():
                raise ValueError('Choice observation provenance mismatch.')
            archive=root/'choice-refinement-v1';archive.mkdir(exist_ok=True)
            saved=archive/path.name
            if saved.exists() and saved.read_bytes()!=target.read_bytes():
                raise ValueError('Archived choice observation differs; refusing to overwrite.')
            if not saved.exists():saved.write_bytes(target.read_bytes())
        with Image.open(proof) as pane:
            if hashlib.sha256(pane.convert('RGB').tobytes()).hexdigest()!=raw['gameplay_sha256']:
                raise ValueError('Choice proof pixels differ from original OCR pixels.')
            observation=observe(pane,raw['lines'])
        extra=dict(version=2,raw_sha256=fingerprint(raw),evidence_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),
                   observation=observation,independent_observations=False)
        target.write_text(json.dumps(extra,indent=2),encoding='utf-8');count+=1
    print(json.dumps(dict(stage='choice_refinement',new_observations=count)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    refine(parser.parse_args().output)
