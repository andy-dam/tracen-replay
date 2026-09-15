"""Grade source labels and a report with the shared occurrence evaluator."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracen_replay.evaluation_adapters import report_document, source_document
from tracen_replay.observation_evaluate import evaluate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path)
    parser.add_argument('--amendments', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    doc = read(args.reference)
    amendments = read(args.amendments)['amendments'] if args.amendments else []
    ref = source_document(doc, evidence_root=args.evidence_root.resolve() if args.evidence_root else None, amendments=amendments)
    pred = report_document(read(args.report))
    score = evaluate(ref, pred)
    categories = defaultdict(Counter)
    for row in score['results']:
        categories[row['category']][row['status']] += 1
    score['by_category'] = {k:dict(v) for k,v in categories.items()}
    score['field_status_counts'] = dict(Counter(f['status'] for r in score['results'] for f in r['fields']))
    score['source_adapter_audit'] = dict(original_label_count=len(ref['original_labels']),
        normalized_observation_count=len(ref['observations']),amendments=ref['amendments'],
        unscored_fields=[dict(source_id=r['id'],fields=r['unscored_expected_fields'],note=r.get('adapter_note'))
                        for r in ref['observations'] if r['unscored_expected_fields'] or r.get('adapter_note')])
    inputs = [args.reference,args.report] + ([args.amendments] if args.amendments else [])
    score['input_sha256'] = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    package=Path(__file__).resolve().parents[1]/'tracen_replay'
    score['evaluator_code_sha256']={name:hashlib.sha256((package/name).read_bytes()).hexdigest()
        for name in ('evaluation_adapters.py','evaluation_labels.py','observation_evaluate.py')}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(score,stream,indent=2,ensure_ascii=False)
    print(json.dumps(dict(observations=len(score['results']),by_category=score['by_category'],
                          fields=score['field_status_counts'],output=str(args.output))))


if __name__ == '__main__': main()
