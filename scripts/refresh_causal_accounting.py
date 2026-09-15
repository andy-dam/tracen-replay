"""Refresh only the derived accounting view of an existing source-bound report."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracen_replay.causal_accounting import build
from tracen_replay.report_contract import validate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    original=args.report.read_bytes()
    report=json.loads(original)
    report['causal_accounting']=build(report)
    validate(report,require_gameplay=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False)
    audit=dict(input_sha256=hashlib.sha256(original).hexdigest(),output_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(),
        accounting_code_sha256=hashlib.sha256((Path(__file__).resolve().parents[1]/'tracen_replay/causal_accounting.py').read_bytes()).hexdigest(),
        source_sha256=report['source']['sha256'],scope='Accounting view only; existing recognition and source-linked observations preserved.')
    with args.output.with_suffix('.accounting-audit.json').open('x',encoding='utf-8') as stream:json.dump(audit,stream,indent=2)
    if args.report.read_bytes()!=original:raise ValueError('Input report changed')
    print(json.dumps(report['causal_accounting']['summary']))


if __name__=='__main__':main()
