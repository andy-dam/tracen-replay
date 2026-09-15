"""Record runtime/model bytes for a reproducible analyzer evaluation.

This reads installed dependencies and models without constructing OCR sessions.
It is an environment snapshot, not a semantic-validation or completion gate.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import sys


PACKAGES = ('rapidocr', 'onnxruntime', 'onnxruntime-directml', 'onnxruntime-gpu', 'numpy', 'Pillow', 'psutil',
            'opencv-python', 'opencv-python-headless', 'pyclipper', 'Shapely',
            'PyYAML', 'omegaconf')


def binding(path):
    path = Path(path).resolve(strict=True)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return dict(path=str(path), bytes=path.stat().st_size, sha256=digest.hexdigest())


def capture(model_dir):
    packages = {}
    for name in PACKAGES:
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            packages[name] = {'installed': False}
            continue
        files = []
        for entry in distribution.files or []:
            path = Path(distribution.locate_file(entry))
            if path.suffix.lower() in ('.pyc', '.pyo') or not path.is_file():
                continue
            files.append(dict(relative_path=str(entry), **binding(path)))
        packages[name] = dict(installed=True, version=distribution.version, files=files)
    executables = {}
    for name in ('ffmpeg', 'ffprobe'):
        path = shutil.which(name)
        executables[name] = binding(path) if path else {'available': False}
    import onnxruntime
    from tracen_replay.vision import PARAMS
    return dict(
        schema_version='tracen-replay/analyzer-runtime-snapshot-v1',
        captured_at=datetime.now(timezone.utc).isoformat(),
        scope='Installed analyzer dependency files, models, interpreter and video tools; no OCR executed.',
        python=dict(version=sys.version, executable=binding(sys.executable)),
        platform=platform.platform(), packages=packages, executables=executables,
        models=[binding(path) for path in sorted(Path(model_dir).glob('*.onnx'))],
        onnxruntime_available_providers=onnxruntime.get_available_providers(),
        provider_scope='Availability only; actual session providers must be recorded by the execution.',
        neural_reader_parameters=PARAMS,
        limitations=['Operating-system files and GPU drivers are not byte-frozen.',
                     'Implementation snapshot and invocation settings are separate required bindings.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=Path('.local/models/rapidocr'))
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite a prior runtime snapshot')
    if not args.model_dir.is_dir():
        raise ValueError('Model directory does not exist')
    result = capture(args.model_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(dict(output=str(args.output), sha256=binding(args.output)['sha256'],
                          packages=sum(p['installed'] for p in result['packages'].values()),
                          models=len(result['models']))))


if __name__ == '__main__':
    main()
