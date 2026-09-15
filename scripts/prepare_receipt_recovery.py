"""Preserve bounded numeric-receipt recovery observations for a report replay."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracen_replay.receipt_recovery import recover


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reparse-only', action='store_true')
    args = parser.parse_args()
    original = args.report.read_bytes()
    report = json.loads(original)
    config = json.loads(args.config.read_text(encoding='utf-8'))
    args.output.mkdir(parents=True, exist_ok=False)
    data = report['gameplay_tracking']
    rows, metadata = recover(config['source_video'], Path(config['cache_root']), report['source'],
                             data['readings'], data['events'], allow_ocr=not args.reparse_only)
    result = dict(source_sha256=report['source']['sha256'],
                  input_report_sha256=hashlib.sha256(original).hexdigest(),
                  metadata=metadata, readings=rows)
    with (args.output/'observations.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False)
    if args.report.read_bytes() != original:
        raise ValueError('Input report changed during recovery')
    print(json.dumps(dict(source_sha256=result['source_sha256'], observations=len(rows),
                          new_frames=metadata['new_frames'], pending_windows=len(metadata['pending_windows']))))


if __name__ == '__main__':
    main()
