"""Check final baseline document links, evidence hashes and purchase joins.

Mechanical checks supplement the human completion audit; they do not certify
source interpretation, visual review, or exhaustive recall.
"""
import hashlib
import json
from pathlib import Path
import re
from collections import Counter
from urllib.parse import unquote

from baseline_status import SECTIONS, inspect


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    repo=Path(__file__).resolve().parents[2]
    root=repo/'.local/full-run-baseline-v1'
    load=lambda p:json.loads(p.read_text(encoding='utf-8'))
    state=inspect(root,repo)
    assert not state['freeze_errors']
    assert all(not x['pending_artifact_checks'] for x in state['sections'])
    frames={}
    for folder,_,_ in SECTIONS:
        for frame in load(root/folder/'manifest.json')['frames']:
            key=Path(frame['evidence']).resolve()
            if key in frames: assert frames[key]['sha256']==frame['sha256']
            frames[key]=frame
    docs=[repo/'docs'/name for name in ('full-run-baseline.md','full-run-baseline-scorecard.md',
                                      'full-run-baseline-gaps.md','full-run-baseline-audit.md')]
    docs += [root/name/'scorecard.md' for name,_,_ in SECTIONS]
    docs += [root/'boundary-review.md',root/'source-d/sparse-interval-review.md']
    links=[]; images={}; document_hashes={}
    for document in docs:
        body=document.read_text(encoding='utf-8')
        document_hashes[str(document.relative_to(repo))]=digest(document)
        for match in re.finditer(r'\[[^\]]+\]\(([^)]+)\)',body):
            target=unquote(match.group(1)).strip('<>')
            if target.startswith(('http://','https://','#')):continue
            target=target.split('#',1)[0]
            path=Path(target)
            if not path.is_absolute():path=document.parent/path
            path=path.resolve()
            assert path.is_file(), f'Broken link in {document}: {target}'
            links.append({'document':str(document.relative_to(repo)),'target':str(path.relative_to(repo))})
            if path.suffix.lower()=='.png':
                assert path in frames, f'Image not in original source manifest: {path}'
                assert digest(path)==frames[path]['sha256'], f'Image changed: {path}'
                images[str(path.relative_to(repo))]={'sha256':frames[path]['sha256'],'timestamp_ms':frames[path]['timestamp_ms']}
    purchases=[]
    for section,filename in [('tail-a','adjudicated-grade.json'),('source-b','purchase-grade.json'),('source-c','draft-grade.json'),('source-d','draft-grade.json')]:
        data=load(root/section/filename)
        rows=data['rows'] if section=='source-b' else [x for x in data['results'] if x['category']=='purchase']
        for row in rows:
            purchases.append({'section':section,'label_id':row['label_id'],'prediction_id':row.get('prediction_id',row.get('matched_prediction'))})
    counts=Counter(x['prediction_id'] for x in purchases)
    assert len(purchases)==len(counts)==79 and None not in counts
    assert set(counts)=={f'lesson-{i:04}' for i in range(1,78)}|{'skills-001','skills-002'}
    occurrence=load(root/'occurrence-audit.json')
    assert not occurrence['pending_sections'] and not occurrence['cross_section_reuse']
    within=Counter((x['section'],x['prediction_id']) for x in occurrence['rows'])
    assert all(n==1 for n in within.values()), 'Prediction atom reused inside a section'
    score=load(root/'combined-scorecard.json')
    assert not score['pending_sections']
    for relative,expected in score['inputs_sha256'].items():
        assert digest(root/relative)==expected, f'Score input stale: {relative}'
    for name,_,_ in SECTIONS:
        evidence=load(root/name/'integrity-audit.json')
        assert evidence['integrity_passed'] and not evidence['uncovered_intervals']
        assert evidence['labels_sha256']==digest(root/name/'labels.json')
    out={'status':'mechanical_deliverables_checks_passed','documents_sha256':document_hashes,
         'local_links_checked':len(links),'linked_source_images':images,'purchase_occurrences':purchases,
         'unique_purchase_count':len(counts),'within_section_atom_reuse':[],
         'source_label_count':sum(x['source_label_count'] for x in state['sections']),
         'freeze':{'analyzer_files':state['analyzer_files_checked'],'report_sha256':state['report_sha256']},
         'limitations':['Semantic completion is assessed in the local evaluation records, not by this audit.',
                        'Link existence and hash integrity do not prove screenshot interpretations.',
                        'Purchase joins count completed batches once; component and acquisition effects are not additional purchases.']}
    (root/'deliverables-audit.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':out['status'],'links':len(links),'images':len(images),'unique_purchases':len(counts),'source_labels':out['source_label_count']}))


if __name__=='__main__':main()
