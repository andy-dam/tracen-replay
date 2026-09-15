"""Validate frozen baseline artifacts and report remaining evaluation work.

This checks artifact integrity and section boundaries, not visual correctness.
Review completion remains a separate, explicit judgment.
"""
import argparse
import hashlib
import json
from pathlib import Path

SECTIONS = [('source-a', 0, 180000), ('middle-a', 180000, 300000), ('tail-a', 300000, 480000),
            ('source-b', 480000, 960000), ('source-c', 960000, 1320000),
            ('tail-c', 1320000, 1440000), ('source-d', 1440000, 1866333)]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(root, repo):
    load = lambda p: json.loads(p.read_text(encoding='utf-8'))
    freeze = load(root/'freeze.json')
    errors = []
    if digest(root/'frozen-report.json') != freeze['report_sha256']:
        errors.append('Frozen report changed')
    for path, expected in freeze['analyzer_code_sha256'].items():
        if not (repo/path).is_file() or digest(repo/path) != expected:
            errors.append(f'Frozen analyzer changed or missing: {path}')
    sections = []
    for name, start, end in SECTIONS:
        folder = root/name
        labels_path = folder/'labels.json'
        labels = load(labels_path) if labels_path.is_file() else None
        seal_path = folder/'seal.json'
        seal = load(seal_path) if seal_path.is_file() else None
        issues = []
        if not labels:
            issues.append('Source labels missing')
        elif [labels['start_ms'], labels['end_ms']] != [start,end]:
            issues.append('Source scope differs from assigned partition')
        if seal:
            if digest(labels_path) != seal['labels_sha256']:
                issues.append('Sealed labels changed')
            if not seal['integrity_passed'] or seal['uncovered_intervals']:
                issues.append('Sealed integrity/declared coverage failed')
        else:
            issues.append('Source reference not sealed')
        if not (folder/'scorecard.md').is_file():
            issues.append('Section scorecard missing')
        grade_hashes = {}
        for filename in ['draft-grade.json','adjudicated-grade.json','adjudications.json','turn-state-grade.json','matched-balance-grade.json',
                         'states-actions-grade.json','effect-grade.json','purchase-grade.json','balance-audit.json']:
            p = folder/filename
            if not p.exists():continue
            data=load(p)
            grade_hashes[filename]=digest(p)
            expected=data.get('labels_sha256',data.get('sealed_labels_sha256',data.get('source_labels_sha256')))
            if expected and (not seal or expected != seal['labels_sha256']):
                issues.append(f'{filename}: source hash mismatch')
            report_hash=data.get('report_sha256',data.get('frozen_report_sha256'))
            if report_hash and report_hash != freeze['report_sha256']:
                issues.append(f'{filename}: report hash mismatch')
        sections.append({'section':name,'scope_ms':[start,end],
                         'source_label_count':len(labels['labels']) if labels else 0,
                         'source_labels_sha256':digest(labels_path) if labels else None,
                         'sealed':bool(seal),'grade_artifact_sha256':grade_hashes,'pending_artifact_checks':issues})
    return {'source_sha256':freeze['source_sha256'],'report_sha256':freeze['report_sha256'],
            'analyzer_files_checked':len(freeze['analyzer_code_sha256']),
            'freeze_errors':errors,'sections':sections,
            'semantic_review_complete':False,
            'limitation':'Hash and declared coverage checks cannot certify screenshot labels, occurrence matching, scoring completeness or full-run accuracy. A section scorecard may still require source adjudication.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('review_dir',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=inspect(args.review_dir.resolve(),Path(__file__).resolve().parents[1])
    payload=json.dumps(result,indent=2,ensure_ascii=False)+'\n'
    if args.output:args.output.write_text(payload,encoding='utf-8')
    else:print(json.dumps(result,ensure_ascii=True))
    if result['freeze_errors']:raise SystemExit(1)


if __name__=='__main__':main()
