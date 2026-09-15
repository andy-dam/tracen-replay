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
import os
import shutil
import sys
import threading
import time
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
    parser.add_argument("--dense-workers", type=int, default=None,
                        help="Worker processes for the dense re-read OCR passes, 1–8 (default: workers - 1, at least 1)")
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"),
                        help="Local OCR model directory")
    parser.add_argument("--reparse-only", action="store_true",
                        help="Use cached observations without running OCR")
    parser.add_argument("--replay-input-manifest", type=Path,
                        help="Source-bound replay input manifest for common cached parsing")
    parser.add_argument("--replay-input-root", type=Path,
                        help="Disposable cache root used by the replay input manifest")
    parser.add_argument("--prune-frames", action="store_true",
                        help="After the report is validated, delete the frame images under --output "
                             "(the report and timeline keep only source timestamps)")
    parser.add_argument("--prune-working-data", action="store_true",
                        help="After the report is validated, delete every directory under --output (frames, "
                             "OCR caches, crops, recovery inputs; about 1 GB per run); the report, the "
                             "timeline, the viewer and the top-level metadata files stay")
    parser.add_argument("--owner-pid", type=int, default=None,
                        help="Process id of the service that owns this job; the job ends, with its "
                             "worker processes, as soon as that process is gone")
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
    dense = getattr(args, "dense_workers", None)
    if dense is not None and not MIN_WORKERS <= dense <= MAX_WORKERS:
        raise JobInputError("invalid_dense_workers", "Dense workers must be between 1 and 8.")
    owner = getattr(args, "owner_pid", None)
    if owner is not None and owner <= 0:
        raise JobInputError("invalid_owner_pid", "Owner pid must be a positive integer.")
    manifest = getattr(args, "replay_input_manifest", None)
    replay_root = getattr(args, "replay_input_root", None)
    if manifest is None and replay_root is not None:
        raise JobInputError(
            "invalid_arguments",
            "--replay-input-root requires --replay-input-manifest.",
        )
    if manifest is not None:
        manifest = manifest.expanduser().resolve()
        if not args.reparse_only:
            raise JobInputError(
                "invalid_arguments",
                "--replay-input-manifest requires --reparse-only; it never starts OCR.",
            )
        if not manifest.is_file():
            raise JobInputError("replay_manifest_not_found", "Replay input manifest must be an existing file.")
        args.replay_input_manifest = manifest
        input_root = (replay_root or output).expanduser().resolve()
        if input_root != output:
            raise JobInputError(
                "replay_root_mismatch",
                "--replay-input-root must equal --output so emitted evidence stays in the worker root.",
            )
        args.replay_input_root = input_root
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
    if _is_proof_metadata_key(lowered):
        return False
    return (
        lowered == "evidence"
        or lowered == "supporting_frames"
        or lowered in {"evidence_pair", "evidence_pairs"}
        or lowered.endswith("_evidence")
        or lowered in {"source_frame_path", "evidence_path"}
    )


def _is_proof_metadata_key(key: object) -> bool:
    """Return whether a key names a hash or verification value.

    Evidence maps contain both path strings and proof metadata.  The metadata
    must never become a filesystem path merely because it is nested below a
    path map (for example ``evidence_sha256`` or ``capture_sha256``).
    """
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return (
        lowered in {"hash", "hashes", "sha256", "verified"}
        or lowered.endswith("_hash")
        or lowered.endswith("_sha256")
        or lowered.endswith("_verified")
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
    if not resolved.is_file():
        raise JobInputError(
            "evidence_missing",
            f"{field} must identify an existing evidence file.",
        )


def _evidence_paths(value, field: str, *, in_evidence: bool = False,
                    in_path_map: bool = False, in_metadata: bool = False):
    """Yield path fields while keeping receipt names and reasons as metadata."""
    if isinstance(value, str):
        if in_evidence and not in_metadata:
            yield field, value
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_field = f"{field}.{key}"
            metadata_key = in_metadata or _is_proof_metadata_key(key)
            key_is_path = not metadata_key and isinstance(key, str) and key.lower() in {
                "path", "file", "filename", "source_frame_path", "evidence_path",
            }
            path_map = not metadata_key and key in {
                'evidence', 'field_evidence', 'performance_evidence', 'concert_bonus_evidence'
            }
            nested_map = (not metadata_key and in_path_map and
                          isinstance(child, (Mapping, list)))
            child_in_evidence = (not metadata_key and
                                 (_is_evidence_key(key) or
                                  (in_evidence and key_is_path) or nested_map))
            yield from _evidence_paths(
                child,
                child_field,
                in_evidence=child_in_evidence,
                in_path_map=path_map or nested_map,
                in_metadata=metadata_key,
            )
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _evidence_paths(
                child,
                f"{field}[{index}]",
                in_evidence=in_evidence,
                in_path_map=in_path_map,
                in_metadata=in_metadata,
            )


def _validate_evidence_paths(report: Mapping[str, object], root: Path) -> None:
    root = root.resolve()
    checked = set()
    for field, path in _evidence_paths(report, 'report'):
        if path in checked:
            continue
        _check_evidence_path(path, field, root)
        checked.add(path)


def _validate_declared_evidence_root(report: Mapping[str, object], root: Path) -> None:
    """Validate an optional report root declaration without redirecting paths."""
    context = report.get("evaluation_context")
    if context is None:
        return
    if not isinstance(context, Mapping):
        raise JobInputError("invalid_evidence_root", "report.evaluation_context must be an object.")
    if "evidence_root" not in context:
        return
    declared = context.get("evidence_root")
    if not isinstance(declared, str) or not declared:
        raise JobInputError(
            "invalid_evidence_root",
            "report.evaluation_context.evidence_root must be a path string.",
        )
    try:
        declared_root = Path(declared).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise JobInputError(
            "invalid_evidence_root",
            "report.evaluation_context.evidence_root is not a resolvable path.",
        ) from exc
    if declared_root != root.resolve():
        raise JobInputError(
            "evidence_root_mismatch",
            "report.evaluation_context.evidence_root must resolve to the worker evidence root.",
        )


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
    dense = getattr(args, "dense_workers", None)
    if dense is not None:
        values.extend(["--dense-workers", str(dense)])
    if args.reparse_only:
        values.append("--reparse-only")
    manifest = getattr(args, "replay_input_manifest", None)
    if manifest is not None:
        values.extend(["--replay-input-manifest", str(manifest)])
        input_root = getattr(args, "replay_input_root", None)
        if input_root is not None:
            values.extend(["--replay-input-root", str(input_root)])
    return values


def _end_with_owner() -> None:
    """End this job and every process it started; nobody is left to read the result."""
    import psutil

    try:
        for child in psutil.Process().children(recursive=True):
            try:
                child.kill()
            except psutil.Error:
                pass
    finally:
        os._exit(3)


def watch_owner(pid: int, on_gone=_end_with_owner, interval: float = 2.0) -> threading.Thread:
    """Call ``on_gone`` once the process ``pid`` is no longer running.

    The service that started this worker is the only reader of its result;
    when it has died (a crash, a forced stop) the analysis would otherwise
    keep its OCR processes, the GPU and gigabytes of memory busy for an
    hour. psutil compares the process's start time, so a reused pid does not
    keep the job alive. Portable: the same check runs on every platform.
    """
    import psutil

    try:
        owner = psutil.Process(pid)
    except psutil.Error as exc:
        raise JobInputError("invalid_owner_pid", f"Owner process {pid} is not running: {exc}") from exc

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                if not owner.is_running() or owner.status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.Error:
                break
        on_gone()

    thread = threading.Thread(target=loop, name="owner-watch", daemon=True)
    thread.start()
    return thread


def _run_producer(source: Path, output: Path, model_dir: Path, args: argparse.Namespace) -> None:
    """Run the existing producer while keeping its progress off stdout."""
    previous_argv = sys.argv
    sys.argv = _producer_argv(source, output, model_dir, args)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            full_recording.main()
    finally:
        sys.argv = previous_argv


def worker_version() -> dict[str, str]:
    """Identify this worker: the package version and the digest of its source."""
    from .code_identity import package_digest
    try:
        from importlib.metadata import version
        package = version("tracen-replay")
    except Exception:  # not installed as a distribution
        package = "unknown"
    return {"package": package, "code_digest": package_digest()}


def _failure(code: str, message: str) -> dict[str, object]:
    return {
        "schema_version": JOB_SCHEMA,
        "status": "failed",
        "error": {"code": code, "message": message},
        "worker_version": worker_version(),
    }


def _emit(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), flush=True)


def _validate_verification_coverage(
    report: Mapping[str, object], verification: Mapping[str, object]
) -> None:
    """Reject a complete-source claim contradicted by coverage metadata.

    The producer's recording audit is the authority for coverage.  A false
    ``full_source_processed`` value remains valid when the audit is
    conservative, but a true value cannot coexist with known missing or
    unprocessed base observations.  Optional fields remain optional for older
    reports; when supplied, their shape is checked before using them.
    """
    source = report.get("source")
    source_duration = source.get("duration_ms") if isinstance(source, Mapping) else None
    if type(source_duration) is not int or source_duration < 1:
        raise JobInputError(
            "invalid_report_status",
            "report.source.duration_ms must be a positive integer before coverage is checked.",
        )

    full_source_processed = verification.get("full_source_processed")
    fully_verified = verification.get("fully_verified")
    go_ready = verification.get("go_ready")
    if fully_verified and not full_source_processed:
        raise JobInputError(
            "invalid_fully_verified",
            "verification.fully_verified cannot be true when full_source_processed is false.",
        )
    if go_ready and not fully_verified:
        raise JobInputError(
            "invalid_go_ready",
            "verification.go_ready cannot be true when fully_verified is false.",
        )

    errors = verification.get("source_coverage_errors")
    if errors is not None and (
        not isinstance(errors, list)
        or any(not isinstance(item, str) or not item.strip() for item in errors)
    ):
        raise JobInputError(
            "invalid_report_status",
            "verification.source_coverage_errors must be an array of non-empty text.",
        )

    missing = verification.get("missing_base_timestamps_ms")
    if missing is not None and (
        not isinstance(missing, list)
        or any(type(item) is not int or item < 0 or item > source_duration for item in missing)
    ):
        raise JobInputError(
            "invalid_report_status",
            "verification.missing_base_timestamps_ms must contain timestamps within the source.",
        )
    if missing is not None and missing != sorted(set(missing)):
        raise JobInputError(
            "invalid_report_status",
            "verification.missing_base_timestamps_ms must be sorted and unique.",
        )

    expected = verification.get("base_frames_expected")
    processed = verification.get("base_frames_processed")
    if (expected is None) != (processed is None):
        raise JobInputError(
            "invalid_report_status",
            "verification.base_frames_expected and base_frames_processed must be supplied together.",
        )
    if expected is not None:
        if type(expected) is not int or expected < 0:
            raise JobInputError(
                "invalid_report_status",
                "verification.base_frames_expected must be a non-negative integer.",
            )
        if type(processed) is not int or processed < 0 or processed > expected:
            raise JobInputError(
                "invalid_report_status",
                "verification.base_frames_processed must be between zero and base_frames_expected.",
            )

    bins = verification.get("sampled_coverage_by_minute")
    incomplete_bin = False
    if bins is not None:
        if not isinstance(bins, list) or not bins:
            raise JobInputError(
                "invalid_report_status",
                "verification.sampled_coverage_by_minute must be a non-empty array.",
            )
        cursor = 0
        total_expected = 0
        total_processed = 0
        required_bin_fields = (
            "start_ms", "end_ms", "base_frames_expected", "base_frames_processed",
            "classified_observations", "unknown_observations", "reviewed",
        )
        for index, item in enumerate(bins):
            if not isinstance(item, Mapping):
                raise JobInputError(
                    "invalid_report_status",
                    f"verification.sampled_coverage_by_minute[{index}] must be an object.",
                )
            missing_fields = [field for field in required_bin_fields if field not in item]
            if missing_fields:
                raise JobInputError(
                    "invalid_report_status",
                    f"verification.sampled_coverage_by_minute[{index}] is missing {missing_fields[0]}.",
                )
            start = item["start_ms"]
            end = item["end_ms"]
            expected_bin = item["base_frames_expected"]
            processed_bin = item["base_frames_processed"]
            classified = item["classified_observations"]
            unknown = item["unknown_observations"]
            if (type(start) is not int or type(end) is not int
                    or start < 0 or end <= start or end > source_duration
                    or start != cursor):
                raise JobInputError(
                    "invalid_report_status",
                    f"verification.sampled_coverage_by_minute[{index}] has a non-contiguous source interval.",
                )
            if end != min(start + 60000, source_duration):
                raise JobInputError(
                    "invalid_report_status",
                    f"verification.sampled_coverage_by_minute[{index}] must use one-minute source intervals.",
                )
            for field, value in (
                ("base_frames_expected", expected_bin),
                ("base_frames_processed", processed_bin),
                ("classified_observations", classified),
                ("unknown_observations", unknown),
            ):
                if type(value) is not int or value < 0:
                    raise JobInputError(
                        "invalid_report_status",
                        f"verification.sampled_coverage_by_minute[{index}].{field} must be a non-negative integer.",
                    )
            if processed_bin > expected_bin:
                raise JobInputError(
                    "invalid_report_status",
                    f"verification.sampled_coverage_by_minute[{index}].base_frames_processed exceeds expected.",
                )
            if type(item["reviewed"]) is not bool:
                raise JobInputError(
                    "invalid_report_status",
                    f"verification.sampled_coverage_by_minute[{index}].reviewed must be a boolean.",
                )
            cursor = end
            total_expected += expected_bin
            total_processed += processed_bin
            incomplete_bin |= processed_bin != expected_bin
        if cursor != source_duration:
            raise JobInputError(
                "invalid_report_status",
                "verification.sampled_coverage_by_minute must cover the complete source interval.",
            )
        if full_source_processed is True and incomplete_bin:
            raise JobInputError(
                "invalid_full_source_processed",
                "verification.full_source_processed cannot be true with an incomplete coverage bin.",
            )
        if expected is not None and total_expected != expected:
            raise JobInputError(
                "invalid_report_status",
                "verification.sampled_coverage_by_minute expected counts do not match base_frames_expected.",
            )
        if processed is not None and total_processed != processed:
            raise JobInputError(
                "invalid_report_status",
                "verification.sampled_coverage_by_minute processed counts do not match base_frames_processed.",
            )

    if full_source_processed is not True:
        return
    if errors:
        raise JobInputError(
            "invalid_full_source_processed",
            "verification.full_source_processed cannot be true with source coverage errors.",
        )
    if missing:
        raise JobInputError(
            "invalid_full_source_processed",
            "verification.full_source_processed cannot be true with missing base timestamps.",
        )
    if expected is not None and processed != expected:
        raise JobInputError(
            "invalid_full_source_processed",
            "verification.full_source_processed cannot be true when base frames are unprocessed.",
        )


def _read_and_validate_report(report_path: Path, evidence_root: Path) -> tuple[dict, bytes]:
    try:
        payload = report_path.read_bytes()
        report = json.loads(payload.decode("utf-8"))
    except OSError as exc:
        raise JobInputError("report_unreadable", f"Could not read producer report: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JobInputError("invalid_report", f"Producer report is not valid UTF-8 JSON: {exc}") from exc
    try:
        # The report contract must validate persisted source-bound preview
        # recoveries against the same immutable evidence root used below for
        # path and coverage checks.  Omitting it would leave the normal job
        # caller on the mutable serialized-row fallback.
        validate_report(
            report, require_gameplay=True, source_root=evidence_root
        )
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
    _validate_verification_coverage(report, verification)
    _validate_declared_evidence_root(report, evidence_root)
    _validate_evidence_paths(report, evidence_root)
    return report, payload


_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def prune_frames(output: Path) -> dict[str, int]:
    """Delete frame images under the run root; keep every JSON, HTML and text file.

    The report and the timeline document locate every fact by its source
    timestamp, so the sampled and re-read frames are working data.  Directories
    left empty are removed.  Runs only after the report was validated.
    """
    root = output.resolve()
    files = 0
    size = 0
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES:
            size += path.stat().st_size
            path.unlink()
            files += 1
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return {"files": files, "bytes": size}


def prune_working_data(output: Path) -> dict[str, int]:
    """Delete every directory under the run root; keep the top-level files.

    The report, the timeline document and the viewer page are top-level files
    and locate every fact by its source timestamp; the directories hold the
    sampled frames, the per-frame OCR caches, crops and recovery inputs, the
    working data of one run.  Runs only after the report was validated; a
    pruned run cannot be re-parsed.
    """
    root = output.resolve()
    files = 0
    size = 0
    directories = 0
    for path in sorted(root.iterdir()):
        if not path.is_dir() or path.is_symlink():
            continue
        for item in path.rglob("*"):
            if item.is_file():
                files += 1
                size += item.stat().st_size
        shutil.rmtree(path)
        directories += 1
    return {"directories": directories, "files": files, "bytes": size}


def _success(report_path: Path, output: Path, report: Mapping[str, object], payload: bytes,
             pruned: Mapping[str, int] | None = None,
             pruned_working_data: Mapping[str, int] | None = None) -> dict[str, object]:
    verification = report["verification"]
    assert isinstance(verification, Mapping)
    source = report["source"]
    assert isinstance(source, Mapping)
    failures = report.get("stage_failures")
    failures = [dict(stage=item.get("stage"), error=item.get("error")) for item in failures
                if isinstance(item, Mapping)] if isinstance(failures, list) else []
    record: dict[str, object] = {
        "schema_version": JOB_SCHEMA,
        "status": "completed_with_stage_failures" if failures else "succeeded",
        "worker_version": worker_version(),
        "report_schema_version": FULL_RECORDING_SCHEMA,
        "report_path": str(report_path),
        "report_sha256": hashlib.sha256(payload).hexdigest(),
        "source_sha256": source["sha256"],
        "evidence_root": str(output),
        "full_source_processed": verification["full_source_processed"],
        "fully_verified": verification["fully_verified"],
        "go_ready": verification["go_ready"],
    }
    if failures:
        record["stage_failures"] = failures
    timeline = report_path.with_name("timeline.json")
    if timeline.is_file():
        record["timeline_path"] = str(timeline)
    if pruned is not None:
        record["pruned_frames"] = dict(pruned)
    if pruned_working_data is not None:
        record["pruned_working_data"] = dict(pruned_working_data)
    return record


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
        if args.owner_pid:
            watch_owner(args.owner_pid)
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
    pruned = None
    if getattr(args, "prune_frames", False):
        # Evidence paths were checked above against the real files; from here
        # on the run is identified by its report, timeline and timestamps.
        pruned = prune_frames(output)
    pruned_working_data = None
    if getattr(args, "prune_working_data", False):
        pruned_working_data = prune_working_data(output)
    _emit(_success(report_path, output, report, payload, pruned, pruned_working_data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
