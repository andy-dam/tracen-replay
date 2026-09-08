"""Evaluate source-bound gameplay observations against reviewed point examples."""
import argparse
import json
from pathlib import Path


def evaluate(reference, reports):
    if not reference.get('observations'):
        raise ValueError('At least one reviewed example is required.')
    readings={}
    for report in reports:
        if report['source']['sha256'] != reference['source_sha256']:
            raise ValueError('Reference and report recordings differ.')
        data=report.get('gameplay_tracking',{})
        if data.get('auxiliary_log_used') is not False:
            raise ValueError('Only gameplay-only reports are eligible.')
        for row in data['readings']:
            timestamp=row['source_timestamp_ms']
            if timestamp in readings:
                raise ValueError('Overlapping report samples are ambiguous.')
            readings[timestamp]=row
    results=[]
    for example in reference['observations']:
        row=readings.get(example['source_timestamp_ms'])
        failures=[]
        if row is None:
            failures.append('missing_sample')
        else:
            if row['screen']!=example['screen']:
                failures.append(f'screen: {row["screen"]}')
            for key,value in example.get('facts',{}).items():
                if row['facts'].get(key)!=value:
                    failures.append(f'fact: {key}')
            if 'training_option' in example and row.get('training_option')!=example['training_option']:
                failures.append('training_option')
            for expected in example.get('effects',[]):
                if not any(all(effect.get(k)==v for k,v in expected.items()) for effect in row['effects']):
                    failures.append(f'effect: {expected}')
            if example.get('no_awards') and row['effects']:
                failures.append('false_award')
        results.append(dict(source_timestamp_ms=example['source_timestamp_ms'],passed=not failures,failures=failures))
    return dict(passed=all(r['passed'] for r in results),matched=sum(r['passed'] for r in results),
                total=len(results),examples=results,independently_reviewed=reference.get('independently_reviewed',False),
                scope='Development point checks; not event recall or independent-recording generalization.')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('reference',type=Path)
    parser.add_argument('reports',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=evaluate(json.loads(args.reference.read_text(encoding='utf-8')),[json.loads(p.read_text(encoding='utf-8')) for p in args.reports])
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
    return 0 if result['passed'] else 1

if __name__=='__main__':
    raise SystemExit(main())
