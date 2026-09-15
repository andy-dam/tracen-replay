"""Freeze reviewed references and verify their bytes before evaluation.

Integrity checks do not establish that a human/source review was correct.
Root review of the labels and targeted-case coverage remains a separate gate.
Analyzer code hashes record the pre-tuning implementation; they deliberately
are not required to remain unchanged when evaluating the later implementation.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


RUNS = ('v1', 'independent-01', 'independent-02')
SCHEMA = 'final-reliability-reference-freeze-v1'
# This command seals the already selected local benchmark, not a new selection
# supplied after inspecting recognition results. Tests inject their own anchors.
SELECTION_SHA256 = 'dd6d20d6a24aa3a5692bab456bdd6197a5d40b5b5794b0ba264d93933e762233'
BASELINE_SHA256 = 'e31932b845cb0cc4b0edc9ae6992e434130f030ee559881d3dc4f630e87741fa'


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def evidence_path(root, relative):
    root = Path(root).resolve()
    require(isinstance(relative, str) and bool(relative), 'Missing evidence path')
    path = Path(relative)
    require(not path.is_absolute() and '..' not in path.parts, 'Evidence must be relative within its root')
    resolved = (root / path).resolve()
    require(resolved.is_relative_to(root), 'Evidence escapes its root')
    require(resolved.is_file(), f'Evidence is missing: {relative}')
    return resolved


def dependency_hashes(bundle_paths):
    """Bind targeted reviews to their images, OCR sidecars and capture metadata."""
    hashes = {}
    for path in bundle_paths:
        path = Path(path).resolve()
        bundle = read(path)
        require(bundle.get('schema_version') == 'tracen-replay/final-source-review-bundle-v1',
                f'Unsupported source dependency bundle: {path}')
        files = bundle.get('immutable_files_sha256')
        require(isinstance(files, dict) and bool(files), f'Empty source dependency bundle: {path}')
        for file, expected_hash in files.items():
            source = Path(file)
            require(source.is_absolute() and source.is_file(), f'Missing bundled source: {file}')
            source = source.resolve()
            require(digest(source) == expected_hash, f'Bundled source changed: {source}')
            require(str(source) not in hashes or hashes[str(source)] == expected_hash,
                    f'Conflicting bundled source: {source}')
            hashes[str(source)] = expected_hash
        for entry in bundle.get('review_documents', []) + bundle.get('supporting_documents', []):
            require(hashes.get(str(Path(entry['path']).resolve())) == entry['sha256'],
                    f'Unbound source review document: {entry["path"]}')
        hashes[str(path)] = digest(path)
    return hashes


def ledger_turn_at(turns, time_ms):
    """The ledger turn holding ``time_ms``: start <= t < end, the last turn end-inclusive.

    A boundary time belongs to the turn that starts there, which is where the
    ledger records the opening state.
    """
    for index, turn in enumerate(turns):
        start, end = turn['start_ms'], turn['end_ms']
        if start <= time_ms < end or (index == len(turns) - 1 and start <= time_ms <= end):
            return turn['id']
    return None


def load_owner_ledgers(report_paths):
    """Turn windows of the reports whose ledger defines ownership, keyed by run."""
    ledgers = {}
    for run, path in report_paths.items():
        require(run in RUNS, f'Unknown owner ledger run: {run}')
        turns = read(path)['turn_ledger']['turns']
        require(all(type(t.get('start_ms')) is int and type(t.get('end_ms')) is int and isinstance(t.get('id'), str) for t in turns),
                f'Owner ledger has turns without integer windows: {path}')
        ledgers[run] = turns
    return ledgers


def validate_references(selection_path, reference_paths, owner_ledgers=None):
    """Validate the exact selection and return every reference/evidence hash.

    With ``owner_ledgers`` (run -> ledger turns) the turn ids of the selection,
    the cases and the observations must be the ledger turn holding their time:
    ownership is then derived from the video's time windows, so a reference
    whose baseline numbering merged two real turns can be re-issued with the
    ids re-mapped without any fact, time or proof changing.  Without it every
    observation must carry its case's turn id, as the first frozen set did.
    """
    from tools.evaluate_final_reliability import validate_source_reference, _coverage_allows
    selection = read(selection_path)
    selected = selection['cases']
    require(len(selected) == 24, 'Expected the frozen 24 transition selection')
    require(Counter(c['run'] for c in selected) == Counter(dict.fromkeys(RUNS, 8)),
            'Expected eight transitions per recording')
    expected = {c['id']: c for c in selected}
    require(len(expected) == 24, 'Duplicate selected case ID')
    hashes = {str(Path(selection_path).resolve()): digest(selection_path)}
    seen, runs = set(), set()
    for path in reference_paths:
        document = read(path)
        require(document.get('schema_version') == 'final-reliability-source-reference-v1',
                f'Unsupported reference schema: {path}')
        require(document.get('auxiliary_log_used') is False,
                f'Reference must explicitly exclude auxiliary side-log evidence: {path}')
        normalized = validate_source_reference(document, expected)
        for case in normalized['cases']:
            coverage = case['review_coverage']
            require(coverage['explicit_fields'] and bool(coverage['fields'])
                    and coverage['explicit_time'] and bool(coverage['intervals_ms']),
                    f"Missing explicit gradeable field/time coverage: {case['case_id']}")
            for observation in case['observations']:
                require(observation.get('status') != 'observed' or _coverage_allows(observation, coverage),
                        f"Observed label outside declared review coverage: {observation['id']}")
        run = document.get('run')
        require(run in RUNS and run not in runs, f'Duplicate or unknown reference run: {run}')
        runs.add(run)
        require(len(document['cases']) == 8, f'Expected eight cases in {run}')
        image_hashes = document.get('image_sha256', {})
        hashes[str(Path(path).resolve())] = digest(path)
        for case in document['cases']:
            identity = case['case_id']
            require(identity in expected and identity not in seen, f'Unexpected or duplicate case: {identity}')
            seen.add(identity)
            wanted = expected[identity]
            require(run == wanted['run'], f'Wrong recording for {identity}')
            require(document['source_sha256'] == wanted['source_sha256'], f'Wrong source hash for {identity}')
            require(case.get('turn_id') == wanted['turn_id'], f'Wrong turn for {identity}')
            scope = [wanted['start_ms'], wanted['end_ms']]
            require(case.get('scope_ms') == scope, f'Changed selected interval for {identity}')
            ledger = (owner_ledgers or {}).get(run)
            if owner_ledgers is not None:
                require(ledger is not None, f'No owner ledger for {run}')
                require(case.get('turn_id') == ledger_turn_at(ledger, scope[0]),
                        f'Case turn is not the ledger turn at its start: {identity}')
            require(case.get('reference_complete') is True, f'Incomplete review: {identity}')
            require(bool(case.get('review_coverage')), f'Missing review coverage: {identity}')
            observations = case.get('observations')
            require(isinstance(observations, list), f'Missing observations: {identity}')
            observation_ids = set()
            for observation in observations:
                oid = observation['id']
                require(oid not in observation_ids, f'Duplicate observation ID in {identity}: {oid}')
                observation_ids.add(oid)
                start, end = observation['start_ms'], observation['end_ms']
                require(type(start) is int and type(end) is int and scope[0] <= start <= end <= scope[1]
                        and start < scope[1], f'Observation outside selected interval: {oid}')
                status = observation.get('status')
                require(status in ('observed', 'unobservable', 'ambiguous'), f'Missing visibility status: {oid}')
                owner = wanted['turn_id'] if ledger is None else ledger_turn_at(ledger, start)
                require(observation.get('expected_turn_id') in (None, owner),
                        f'Unexpected labeled owner for {oid}')
                proof = observation.get('evidence', [])
                require(isinstance(proof, list), f'Evidence must be a list: {oid}')
                require(status != 'observed' or bool(proof), f'Observed label lacks source proof: {oid}')
                for relative in proof:
                    source = evidence_path(wanted['evidence_root'], relative)
                    actual = digest(source)
                    require(image_hashes.get(relative) == actual, f'Changed or unhashed evidence: {relative}')
                    hashes[str(source)] = actual
        # Bind supporting review frames too, not just accepted-label images.
        root = next(c['evidence_root'] for c in selected if c['run'] == run)
        for relative, expected_hash in image_hashes.items():
            source = evidence_path(root, relative)
            require(digest(source) == expected_hash, f'Changed supporting frame: {relative}')
            hashes[str(source)] = expected_hash
    require(seen == set(expected) and runs == set(RUNS), 'Missing selected reference cases')
    return hashes


def freeze(selection_path, reference_paths, targeted_paths, baseline_path, output, code_root, *,
           expected_selection_sha256=SELECTION_SHA256, expected_baseline_sha256=BASELINE_SHA256,
           dependency_bundle_paths=(), owner_ledger_paths=None):
    output = Path(output)
    require(not output.exists(), 'A reference freeze cannot be overwritten')
    require(digest(selection_path) == expected_selection_sha256, 'Original selected benchmark changed')
    require(digest(baseline_path) == expected_baseline_sha256, 'Original baseline manifest changed')
    owner_ledgers = load_owner_ledgers(owner_ledger_paths) if owner_ledger_paths else None
    hashes = validate_references(selection_path, reference_paths, owner_ledgers)
    if owner_ledger_paths:
        for path in owner_ledger_paths.values():
            hashes[str(Path(path).resolve())] = digest(path)
    require(bool(targeted_paths), 'Targeted source reviews must be included')
    baseline = read(baseline_path)
    code_root = Path(code_root).resolve()
    checklist = code_root / baseline['checklist']
    original = next((value for key, value in baseline['worktree_sha256'].items()
                     if (code_root / key).resolve() == checklist.resolve()), None)
    require(original is not None and digest(checklist) == original, 'Original checklist changed')
    for path in [baseline_path, checklist, *targeted_paths]:
        hashes[str(Path(path).resolve())] = digest(path)
    for path, value in dependency_hashes(dependency_bundle_paths).items():
        require(path not in hashes or hashes[path] == value, f'Source changed during freeze: {path}')
        hashes[path] = value
    document = dict(schema_version=SCHEMA,
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        selection_path=str(Path(selection_path).resolve()),
        selection_sha256=expected_selection_sha256,
        baseline_path=str(Path(baseline_path).resolve()),
        baseline_sha256=expected_baseline_sha256,
        reference_paths=[str(Path(p).resolve()) for p in reference_paths],
        targeted_source_reviews=[str(Path(p).resolve()) for p in targeted_paths],
        dependency_bundle_paths=[str(Path(p).resolve()) for p in dependency_bundle_paths],
        immutable_files_sha256=hashes,
        analyzer_code_at_freeze={str(p.relative_to(code_root)): digest(p)
                                 for p in sorted((code_root / 'analyzer' / 'tracen_replay').rglob('*.py'))},
        independent_full_recording_validation=False,
        limitation='Hashes establish integrity; source-review correctness and targeted coverage require separate review.')
    if owner_ledger_paths:
        document['ownership'] = 'ledger_time_window'
        document['owner_ledger_paths'] = {run: str(Path(path).resolve()) for run, path in owner_ledger_paths.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also protects against concurrent writers.
    with output.open('x', encoding='utf-8') as stream:
        json.dump(document, stream, indent=2)
        stream.write('\n')
    return document


def verify_manifest(path, *, expected_selection_sha256=SELECTION_SHA256,
                    expected_baseline_sha256=BASELINE_SHA256,
                    expected_manifest_sha256=None):
    if expected_manifest_sha256 is not None:
        require(digest(path) == expected_manifest_sha256, 'Recorded freeze checkpoint changed')
    manifest = read(path)
    require(manifest.get('schema_version') == SCHEMA, 'Unsupported freeze schema')
    require(manifest.get('selection_sha256') == expected_selection_sha256
            and digest(manifest['selection_path']) == expected_selection_sha256,
            'Original selected benchmark changed')
    require(manifest.get('baseline_sha256') == expected_baseline_sha256
            and digest(manifest['baseline_path']) == expected_baseline_sha256,
            'Original baseline manifest changed')
    require(bool(manifest.get('immutable_files_sha256')), 'Empty freeze manifest')
    for file, expected_hash in manifest['immutable_files_sha256'].items():
        require(digest(file) == expected_hash, f'Frozen evidence changed: {file}')
    owner_paths = manifest.get('owner_ledger_paths')
    require(bool(owner_paths) == (manifest.get('ownership') == 'ledger_time_window'), 'Inconsistent ownership declaration')
    owner_ledgers = load_owner_ledgers(owner_paths) if owner_paths else None
    actual = validate_references(manifest['selection_path'], manifest['reference_paths'], owner_ledgers)
    require(all(manifest['immutable_files_sha256'].get(p) == value for p, value in actual.items()),
            'Reference or evidence omitted from freeze')
    require(bool(manifest.get('targeted_source_reviews')), 'Missing targeted reviews')
    require(all(p in manifest['immutable_files_sha256'] for p in manifest['targeted_source_reviews']),
            'Unbound targeted review')
    dependencies = dependency_hashes(manifest.get('dependency_bundle_paths', []))
    require(all(manifest['immutable_files_sha256'].get(p) == value for p, value in dependencies.items()),
            'Source dependency omitted from freeze')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', required=True)
    parser.add_argument('--references', nargs=3, required=True)
    parser.add_argument('--targeted-reviews', nargs='+', required=True)
    parser.add_argument('--baseline', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--code-root', default='.')
    parser.add_argument('--dependency-bundles', nargs='+', default=[])
    parser.add_argument('--selection-sha256', default=SELECTION_SHA256,
                        help='sha256 of the selection being sealed; the original benchmark by default, a re-issued '
                             'selection (turn ids re-mapped by time) must be named explicitly')
    parser.add_argument('--owner-ledger-dir',
                        help='directory holding <run>-report.json for each run; turn ownership is then the ledger '
                             'turn at each time instead of one id per case')
    args = parser.parse_args()
    ledgers = None
    if args.owner_ledger_dir:
        ledgers = {run: str(Path(args.owner_ledger_dir) / f'{run}-report.json') for run in RUNS}
    freeze(args.selection, args.references, args.targeted_reviews, args.baseline, args.output, args.code_root,
           expected_selection_sha256=args.selection_sha256, dependency_bundle_paths=args.dependency_bundles,
           owner_ledger_paths=ledgers)
    print(f'Frozen references: {args.output}')


if __name__ == '__main__':
    main()
