"""Convert preserved baseline source-QA records into explicit source overlays.

This is migration code for two historical adjudication layouts. It changes no
sealed source and reads no predictions. Every cited screenshot hash is checked.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def convert(labels, amendments):
    rows = {r['id']:copy.deepcopy(r) for r in labels['labels']}
    output = []
    for amendment in amendments:
        identity = amendment['label_id']
        parent, separator, child = identity.partition('/outcome/')
        row = rows[parent]
        original = amendment['original']
        replacement = amendment['replacement']
        proofs = amendment.get('evidence',amendment.get('result_evidence',
            replacement.get('evidence',[]) if isinstance(replacement,dict) else []))
        if not proofs:
            raise ValueError('Historical amendment has no screenshot evidence')
        for proof in proofs:
            if digest(proof['path']) != proof['sha256']:
                raise ValueError('Historical source-QA screenshot changed')

        def change(field, after):
            output.append(dict(label_id=parent,field=field,before=copy.deepcopy(row[field]),after=copy.deepcopy(after),
                reason=amendment['reason'],evidence=copy.deepcopy(proofs),historical_label_id=identity,
                historical_field=amendment['field']))
            row[field] = copy.deepcopy(after)

        if separator:
            if amendment['field'] != 'expected.amount':
                raise ValueError('Unknown grouped amendment shape')
            expected = copy.deepcopy(row['expected'])
            target = expected['outcomes'][int(child)]
            if target['amount'] != original:
                raise ValueError('Historical amount disagrees with sealed source')
            target['amount'] = replacement
            change('expected',expected)
            continue
        if 'first_seen_ms' in original:
            for field in ('first_seen_ms','last_seen_ms','evidence'):
                if row[field] != original[field]:
                    raise ValueError('Historical timing/evidence amendment disagrees with sealed source')
                after = [p['path'] for p in replacement['evidence']] if field == 'evidence' else replacement[field]
                change(field,after)
        expected = copy.deepcopy(row['expected'])
        if 'amount' in original:
            if expected['amount'] != original['amount']:
                raise ValueError('Historical amount amendment disagrees with sealed source')
            expected['amount'] = replacement['amount']
            change('expected',expected)
        if 'name' in original:
            # This historical layout records the exact nested paths it changed.
            if amendment['field'] != 'expected.request.components[2].name and expected.components[2].name':
                raise ValueError('Unknown nested source-name amendment')
            for components in (expected['request']['components'],expected['components']):
                if components[2]['name'] != original['name']:
                    raise ValueError('Historical component name disagrees with sealed source')
                components[2]['name'] = replacement['name']
            change('expected',expected)
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels',type=Path,required=True)
    parser.add_argument('--adjudications',type=Path,required=True)
    parser.add_argument('--layout',choices=('source-b','source-c'),required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    labels,document=read(args.labels),read(args.adjudications)
    if args.layout=='source-b':
        amendments=document['source_amendments']
    else:
        qa=document['semantic_source_qa']
        if qa['labels_sha256']!=digest(args.labels):raise ValueError('Historical label hash mismatch')
        amendments=qa['amendments']
    converted=convert(labels,amendments)
    result=dict(source_sha256=labels['source_sha256'],historical_amendments=amendments,
                amendments=converted,inputs_sha256={str(p):digest(p) for p in (args.labels,args.adjudications)})
    with args.output.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,ensure_ascii=False)
    print(json.dumps(dict(historical_amendments=len(amendments),field_overlays=len(converted))))


if __name__=='__main__': main()
