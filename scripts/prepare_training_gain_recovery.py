"""Prepare source-bound training observations without modifying the starting report."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tracen_replay.training_gain_recovery import recover


parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('report',type=Path);parser.add_argument('--config',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True);parser.add_argument('--reparse-only',action='store_true')
args=parser.parse_args(); raw=args.report.read_bytes();report=json.loads(raw)
config=json.loads(args.config.read_text(encoding='utf-8')); data=report['gameplay_tracking']
rows,metadata=recover(config['source_video'],config['cache_root'],report['source'],data['readings'],data['events'],allow_ocr=not args.reparse_only)
if args.report.read_bytes()!=raw:raise ValueError('Starting report changed')
args.output.parent.mkdir(parents=True,exist_ok=True)
with args.output.open('x',encoding='utf-8') as stream:
    json.dump(dict(input_report_sha256=hashlib.sha256(raw).hexdigest(),source_sha256=report['source']['sha256'],readings=rows,metadata=metadata),stream,ensure_ascii=False)
print(json.dumps({k:v for k,v in metadata.items() if k not in ('requested_windows','processed_windows','pending_windows')}))
