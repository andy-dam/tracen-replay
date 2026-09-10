"""Machine-readable subprocess boundary for full-recording analysis.

The full-recording producer is intentionally verbose and currently exposes its
arguments through ``sys.argv``.  This module provides the stable boundary a
worker can call while keeping producer progress on stderr and reserving stdout
for one terminal JSON object.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import full_recording
from .report_contract import (
    FULL_RECORDING_SCHEMA,
    ReportContractError,
    validate as validate_report,
)


JOB_SCHEMA = "tracen-replay/analysis-job-v1"
MIN_FPS = 1.0
MAX_FPS = 8.0
MIN_WORKERS = 1
MAX_WORKERS = 8
_STATUS_FIELDS = ("full_source_processed", "fully_verified", "go_ready")


class JobInputError(ValueError):
    """An input that can be reported without starting the producer."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _ArgumentParser(argparse.ArgumentParser):
    """Raise a typed error so malformed CLI input gets the JSON envelope."""

    def error(self, message: str) -> None:
        raise JobInputError("invalid_arguments", message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Local full-recording video file")
    parser.add_argument("--output", required=True, type=Path,
                        help="Run directory for the producer's capture and report")
    parser.add_argument("--fps", type=float, default=4.0,
                        help="Base sampling rate, 1–8 FPS (default: 4)")
    parser.add_argument("--workers", type=int, default=4,
                        help="OCR worker count, 1–8 (default: 4)")
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"),
                        help="Local OCR model directory")
    parser.add_argument("--reparse-only", action="store_true",
                        help="Use cached observations without running OCR")
    return parser


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    model_dir = args.model_dir.expanduser().resolve()
    if not source.is_file():
        raise JobInputError("source_not_found", "Source must be an existing local video file.")
    if output.exists() and not output.is_dir():
        raise JobInputError("output_not_directory", "Output path must be a directory.")
    if not math.isfinite(args.fps) or not MIN_FPS <= args.fps <= MAX_FPS:
        raise JobInputError("invalid_fps", "FPS must be between 1 and 8.")
    if not MIN_WORKERS <= args.workers <= MAX_WORKERS:
        raise JobInputError("invalid_workers", "Workers must be between 1 and 8.")
    return source, output, model_dir


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _report_state(path: Path):
    """Return enough file identity to detect a producer no-op."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        getattr(stat, "st_ctime_ns", None),
    )


def _is_evidence_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    if lowered.endswith("_sha256") or lowered.endswith("_verified"):
        return False
    return (
        lowered == "evidence"
        or lowered == "supporting_frames"
        or lowered.endswith("_evidence")
        or lowered.startswith("evidence_")
        or lowered in {"source_frame_path", "evidence_path"}
    )


def _check_evidence_path(value: str, field: str, root: Path) -> None:
    if not isinstance(value, str) or not value:
        raise JobInputError("evidence_outside_root", f"{field} must be a relative evidence path.")
    try:
        windows = PureWindowsPath(value)
        posix = PurePosixPath(value)
        if windows.is_absolute() or windows.root or windows.drive or posix.is_absolute():
            raise ValueError("absolute path")
        resolved = (root / Path(value)).resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise JobInputError(
            "evidence_outside_root",
            f"{field} must resolve inside the evidence root.",
        ) from exc


def _walk_evidence(value, field: str, *, root: Path, in_evidence: bool = False) -> None:
    """Check path-shaped evidence values without treating boxes as paths."""
    if isinstance(value, str):
        if in_evidence:
            _check_evidence_path(value, field, root)
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_field = f"{field}.{key}"
            key_is_path = isinstance(key, str) and key.lower() in {
                "path", "file", "filename", "source_frame_path", "evidence_path",
            }
            child_in_evidence = _is_evidence_key(key) or (in_evidence and
                (key_is_path or isinstance(child, (Mapping, list))))
            _walk_evidence(
                child,
                child_field,
                root=root,
                in_evidence=child_in_evidence,
            )
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _walk_evidence(child, f"{field}[{index}]", root=root, in_evidence=in_evidence)


def _validate_evidence_paths(report: Mapping[str, object], root: Path) -> None:
    _walk_evidence(report, "report", root=root.resolve())


def _producer_argv(source: Path, output: Path, model_dir: Path, args: argparse.Namespace) -> list[str]:
    values = [
        "tracen-replay-full-recording",
        str(source),
        "--output",
        str(output),
        "--fps",
        str(args.fps),
        "--workers",
        str(args.workers),
        "--model-dir",
        str(model_dir),
    ]
    if args.reparse_only:
        values.append("--reparse-only")
    return values


def _run_producer(source: Path, output: Path, model_dir: Path, args: argparse.Namespace) -> None:
    """Run the existing producer while keeping its progress off stdout."""
    previous_argv = sys.argv
    sys.argv = _producer_argv(source, output, model_dir, args)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            full_recording.main()
    finally:
        sys.argv = previous_argv


def _failure(code: str, message: str) -> dict[str, object]:
    return {
        "schema_version": JOB_SCHEMA,
        "status": "failed",
        "error": {"code": code, "message": message},
    }


def _emit(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), flush=True)


def _read_and_validate_report(report_path: Path, evidence_root: Path) -> tuple[dict, bytes]:
    try:
        payload = report_path.read_bytes()
        report = json.loads(payload.decode("utf-8"))
    except OSError as exc:
        raise JobInputError("report_unreadable", f"Could not read producer report: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JobInputError("invalid_report", f"Producer report is not valid UTF-8 JSON: {exc}") from exc
    try:
        validate_report(report, require_gameplay=True)
    except ReportContractError as exc:
        raise JobInputError("invalid_report", str(exc)) from exc
    if "turn_ledger" not in report:
        raise JobInputError("missing_turn_ledger", "Producer report must include the versioned turn ledger.")
    verification = report.get("verification")
    if not isinstance(verification, Mapping):
        raise JobInputError("invalid_report_status", "Producer report is missing verification status.")
    for field in _STATUS_FIELDS:
        if type(verification.get(field)) is not bool:
            raise JobInputError(f"invalid_{field}", f"verification.{field} must be a boolean.")
    if verification.get("source_sha256") != report["source"]["sha256"]:
        raise JobInputError("invalid_report_status", "verification.source_sha256 does not match report.source.sha256.")
    _validate_evidence_paths(report, evidence_root)
    return report, payload


def _success(report_path: Path, output: Path, report: Mapping[str, object], payload: bytes) -> dict[str, object]:
    verification = report["verification"]
    assert isinstance(verification, Mapping)
    source = report["source"]
    assert isinstance(source, Mapping)
    return {
        "schema_version": JOB_SCHEMA,
        "status": "succeeded",
        "report_schema_version": FULL_RECORDING_SCHEMA,
        "report_path": str(report_path),
        "report_sha256": hashlib.sha256(payload).hexdigest(),
        "source_sha256": source["sha256"],
        "evidence_root": str(output),
        "full_source_processed": verification["full_source_processed"],
        "fully_verified": verification["fully_verified"],
        "go_ready": verification["go_ready"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run one analysis job and emit one terminal JSON envelope."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
        source, output, model_dir = _paths(args)
    except JobInputError as exc:
        _emit(_failure(exc.code, str(exc)))
        return 2
    except (OSError, ValueError, RuntimeError) as exc:
        _emit(_failure("invalid_paths", str(exc)))
        return 2

    try:
        requested_source_sha256 = _sha256_file(source)
    except OSError as exc:
        _emit(_failure("source_unreadable", f"Could not read source file: {exc}"))
        return 2

    report_path = output / "report.json"
    previous_report_state = _report_state(report_path)
    try:
        _run_producer(source, output, model_dir, args)
    except SystemExit as exc:
        _emit(_failure("producer_failed", f"Producer exited with status {exc.code!r}."))
        return 1
    except Exception as exc:  # The worker boundary must turn producer failures into JSON.
        _emit(_failure("producer_failed", f"{type(exc).__name__}: {exc}"))
        return 1

    try:
        completed_source_sha256 = _sha256_file(source)
    except OSError as exc:
        _emit(_failure("source_unreadable", f"Could not read source file after production: {exc}"))
        return 1
    if completed_source_sha256 != requested_source_sha256:
        _emit(_failure("source_mismatch", "The source file changed while the producer was running."))
        return 1
    if not report_path.is_file():
        _emit(_failure("report_missing", "Producer completed without writing report.json."))
        return 1
    if previous_report_state is not None and _report_state(report_path) == previous_report_state:
        _emit(_failure("report_stale", "Producer did not update the existing report.json."))
        return 1
    try:
        report, payload = _read_and_validate_report(report_path, output)
    except JobInputError as exc:
        _emit(_failure(exc.code, str(exc)))
        return 1
    if report["source"]["sha256"] != completed_source_sha256:
        _emit(_failure("source_mismatch", "Report source.sha256 does not match the requested source file."))
        return 1
    _emit(_success(report_path, output, report, payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
