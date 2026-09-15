"""Preserve analyzer/evaluator bytes and detect worktree drift around a run.

This is a code snapshot only. Runtime, source inputs, invocation settings and
semantic acceptance remain separate required evidence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import stat
from pathlib import Path
import shutil


def selected_files(root):
    root = Path(root).resolve()
    files = []
    for folder in ('tracen_replay', 'tools', 'lab'):
        for path in sorted((root / folder).rglob('*')):
            if '__pycache__' in path.parts or path.suffix in ('.pyc', '.pyo'):
                continue
            if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise ValueError(f'Reparse path in implementation: {path}')
            if path.is_file():
                if not path.resolve().is_relative_to(root):
                    raise ValueError(f'Implementation path escapes repository: {path}')
                files.append(path)
    project = root / 'pyproject.toml'
    if project.is_file():
        files.append(project)
    return files


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def capture(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    files = selected_files(root)
    if not files or not (root / 'tracen_replay/analysis_job.py').is_file():
        raise ValueError('Missing analyzer implementation')
    if output.exists():
        raise ValueError('Refusing to overwrite implementation snapshot')
    if any(output.is_relative_to(root / name) for name in ('tools', 'lab', 'tracen_replay')):
        raise ValueError('Snapshot must be outside implementation directories')
    output.mkdir(parents=True)
    hashes = {}
    for source in files:
        relative = source.relative_to(root).as_posix()
        target = output / 'files' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        expected = digest(source)
        shutil.copyfile(source, target)
        if digest(target) != expected or digest(source) != expected:
            raise ValueError(f'Implementation changed during copy: {relative}')
        hashes[relative] = expected
    manifest = dict(schema_version='tracen-replay/implementation-snapshot-v1',
                    captured_at=datetime.now(timezone.utc).isoformat(),
                    repository_root=str(root), files=hashes,
                    scope='Analyzer package, tools, lab scripts, package resources and project metadata; no acceptance claim.')
    # A second complete enumeration detects additions/removals during copying.
    current = {p.relative_to(root).as_posix(): digest(p) for p in selected_files(root)}
    if current != hashes:
        raise ValueError('Implementation changed during snapshot')
    (output / 'code-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def verify(root, snapshot, expected_manifest_sha256):
    root, snapshot = Path(root).resolve(), Path(snapshot).resolve()
    path = snapshot / 'code-manifest.json'
    if digest(path) != expected_manifest_sha256:
        raise ValueError('Implementation manifest changed')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    current = {p.relative_to(root).as_posix(): digest(p) for p in selected_files(root)}
    if current != manifest['files']:
        raise ValueError('Current implementation differs from snapshot')
    for relative, expected in manifest['files'].items():
        copy = (snapshot / 'files' / relative).resolve()
        if not copy.is_relative_to(snapshot / 'files') or digest(copy) != expected:
            raise ValueError(f'Preserved implementation changed: {relative}')
    return len(current)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify-manifest-sha256')
    args = parser.parse_args()
    if args.verify_manifest_sha256:
        count = verify(args.root, args.output, args.verify_manifest_sha256)
    else:
        count = len(capture(args.root, args.output)['files'])
    print(json.dumps({'files': count, 'manifest_sha256': digest(args.output / 'code-manifest.json')}))


if __name__ == '__main__':
    main()
