"""Refresh discovered base OCR rows in a disposable replay clone.

The discovery reports identify source-bound rows that the frozen reader should
reread. This helper validates those reports and the clone before any model
call, then (only with --execute) runs the frozen NeuralReader.read on
those rows. It never reads an accepted report, derives a value from a balance,
or writes to a canonical/prepared source root.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import sys
import uuid
from typing import Any, Iterable


SCHEMA = "tracen-replay/discovered-base-reading-refresh-v1"
SNAPSHOT_SCHEMA = "tracen-replay/implementation-snapshot-v1"
PANE_BOUNDS = (148, 0, 958, 1080)
IMAGE_SIZE = (1920, 1080)
PANE_SIZE = (810, 1080)
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
HEX64 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
FRAME_ID = re.compile(r"^[A-Za-z0-9_.-]+$")

# These are outputs or source metadata, rather than registered replay
# sidecars. In particular, report.json is intentionally excluded so an
# accepted report can never become an input to this refresh.
NON_INPUT_JSON_NAMES = frozenset(
    {
        "capture.json",
        "report.json",
        "preparation-report.json",
        "clone-verification.json",
        "identity.json",
        "progress.json",
    }
)
NON_INPUT_JSON_PARTS = frozenset({"reports", "grading", "logs", "source-manifests"})
SKIP_INPUT_DIRS = frozenset({"neural", "gameplay", "frames", "__pycache__"})

# A discovered base refresh changes the serialized neural row.  Companions that
# identify that row by a raw file/object fingerprint must be regenerated in the
# new clone.  The image identities below are different: the source frame and
# gameplay pane are immutable, independently verified evidence and remain valid
# when the OCR JSON is reread.
RAW_DEPENDENCY_KEYS = frozenset(
    {
        "raw_sha",
        "raw_sha256",
        "raw_hash",
        "raw_fingerprint",
        "source_raw_sha",
        "source_raw_sha256",
        "source_raw_hash",
        "source_raw_fingerprint",
        "source_row_fingerprint",
        "row_fingerprint",
        "reading_fingerprint",
        "reading_raw_sha",
        "reading_raw_sha256",
        "neural_sha",
        "neural_sha256",
        "neural_hash",
        "raw_path",
        "source_raw_path",
        "reading_path",
        "base_raw_path",
        "base_neural_path",
        "neural_path",
    }
)
GENERIC_HASH_KEYS = frozenset({"hash", "sha", "sha256", "digest", "fingerprint"})
IMAGE_HASH_KEYS = frozenset(
    {
        "source_frame_sha",
        "source_frame_sha256",
        "source_frame_hash",
        "source_image_sha",
        "source_image_sha256",
        "source_image_hash",
        "gameplay_sha",
        "gameplay_sha256",
        "gameplay_hash",
        "gameplay_image_sha",
        "gameplay_image_sha256",
        "evidence_sha",
        "evidence_sha256",
        "evidence_hash",
        "frame_sha",
        "frame_sha256",
        "frame_hash",
        "screenshot_sha",
        "screenshot_sha256",
        "screenshot_hash",
    }
)
IMAGE_PATH_KEYS = frozenset(
    {
        "source_frame",
        "source_frame_path",
        "source_frame_evidence",
        "source_image",
        "source_image_path",
        "frame",
        "frame_path",
        "gameplay",
        "gameplay_path",
        "gameplay_crop_path",
        "evidence",
        "evidence_path",
        "screenshot",
        "screenshot_path",
    }
)
IMAGE_BINDING_CONTEXT_KEYS = frozenset(
    {
        "source_timestamp",
        "source_timestamp_ms",
        "timestamp",
        "timestamp_ms",
        "evidence",
        "evidence_path",
        "source_frame",
        "source_frame_path",
        "source_frame_evidence",
        "source_frame_sha",
        "source_frame_sha256",
        "gameplay",
        "gameplay_path",
        "gameplay_crop_path",
        "gameplay_sha",
        "gameplay_sha256",
    }
)

CHOICE_DEPENDENCY_REASONS = frozenset(
    {
        "neural_path",
        "raw_fingerprint_default_ascii",
        "raw_fingerprint_default_utf8",
        "raw_fingerprint_compact_utf8",
    }
)
CHOICE_REFINEMENT_LEAF = "choice-refinement"
PROGRESS_SCHEMA = "tracen-replay/discovered-base-reading-refresh-progress-v1"


class RefreshError(ValueError):
    """A fail-closed refresh error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


def _fail(code: str, message: str, *, details: Any = None) -> None:
    raise RefreshError(code, message, details=details)


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        _fail("path_unreadable", f"Could not inspect {path}: {exc}")
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0) & REPARSE_POINT
    )


def _path_inside(root: Path, path: Path, field: str) -> None:
    root = Path(root).resolve()
    path = Path(path)
    try:
        relative = path.absolute().relative_to(root.absolute())
    except (OSError, RuntimeError, ValueError):
        _fail("path_outside_root", f"{field} leaves {root}: {path}")
    cursor = root
    for component in relative.parts:
        cursor /= component
        if cursor.exists() and _is_reparse(cursor):
            _fail("reparse_point", f"{field} contains a reparse point: {cursor}")
    try:
        path.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError):
        _fail("path_outside_root", f"{field} resolves outside {root}: {path}")


def _is_within(path: Path, root: Path) -> bool:
    """Return whether a resolved path is contained by a resolved root."""

    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _assert_file(path: Path, field: str, *, root: Path | None = None) -> Path:
    path = Path(path)
    if root is not None:
        _path_inside(root, path, field)
    if not path.is_file():
        _fail("missing_file", f"{field} is missing: {path}")
    if _is_reparse(path):
        _fail("reparse_point", f"{field} is a reparse point: {path}")
    return path


def _assert_dir(path: Path, field: str, *, root: Path | None = None) -> Path:
    path = Path(path)
    if root is not None:
        _path_inside(root, path, field)
    if not path.is_dir():
        _fail("missing_directory", f"{field} is missing: {path}")
    if _is_reparse(path):
        _fail("reparse_point", f"{field} is a reparse point: {path}")
    return path


def _safe_relative(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value:
        _fail("invalid_path", f"{field} must be a relative path")
    if not value and allow_empty:
        return ""
    if not value:
        _fail("invalid_path", f"{field} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or windows.root
        or ".." in posix.parts
    ):
        _fail("path_outside_root", f"{field} must stay below its root")
    return posix.as_posix()


def _digest(path: Path) -> str:
    path = Path(path)
    try:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as exc:
        _fail("read_failed", f"Could not hash {path}: {exc}")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path, field: str, *, root: Path | None = None) -> dict[str, Any]:
    _assert_file(path, field, root=root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("invalid_json", f"{field} is not readable JSON: {path}", details=str(exc))
    if not isinstance(value, dict):
        _fail("invalid_json", f"{field} must be a JSON object: {path}")
    return value


def _validate_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        _fail("invalid_hash", f"{field} must be a SHA-256 hex digest")
    return value.lower()


def _validate_strict_json_object(value: Any, field: str) -> dict[str, Any]:
    """Validate a reader payload before any replacement is written.

    Python's default JSON encoder accepts NaN and infinities even though they
    are outside the JSON data model.  Fingerprints and downstream readers must
    see one deterministic, standards-compliant object, so fail before touching
    the target row.
    """

    if not isinstance(value, dict):
        _fail("reader_invalid", f"{field} must be a JSON object")
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        _fail("reader_invalid", f"{field} is not strict JSON: {exc}")
    return value


def _hash_pane(path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(path) as image:
            rgb = image.convert("RGB")
            if rgb.size != PANE_SIZE:
                _fail(
                    "invalid_gameplay_proof",
                    f"Gameplay proof must be {PANE_SIZE}, got {rgb.size}: {path}",
                )
            return _digest_bytes(rgb.tobytes())
    except RefreshError:
        raise
    except (OSError, ValueError) as exc:
        _fail("invalid_gameplay_proof", f"Could not read gameplay proof {path}: {exc}")


def _source_frame_hash(path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(path) as image:
            if image.size != IMAGE_SIZE:
                _fail(
                    "invalid_source_frame",
                    f"Source frame must be {IMAGE_SIZE}, got {image.size}: {path}",
                )
    except RefreshError:
        raise
    except (OSError, ValueError) as exc:
        _fail("invalid_source_frame", f"Could not read source frame {path}: {exc}")
    return _digest(path)


def _source_pane(source_frame: Path):
    try:
        from PIL import Image

        with Image.open(source_frame) as image:
            if image.size != IMAGE_SIZE:
                _fail(
                    "invalid_source_frame",
                    f"Source frame must be {IMAGE_SIZE}, got {image.size}: {source_frame}",
                )
            return image.convert("RGB").crop(PANE_BOUNDS)
    except RefreshError:
        raise
    except (OSError, ValueError) as exc:
        _fail("invalid_source_frame", f"Could not decode source frame {source_frame}: {exc}")


def _snapshot_file_hash(manifest: dict[str, Any], relative: str) -> str:
    files = manifest.get("files")
    if not isinstance(files, dict):
        _fail("invalid_snapshot", "Frozen snapshot has no files map")
    value = files.get(relative)
    if not isinstance(value, str):
        _fail("missing_snapshot_module", f"Frozen snapshot lacks {relative}")
    return _validate_hash(value, f"snapshot.files[{relative!r}]")


def load_snapshot(snapshot: Path, expected_manifest_sha256: str) -> dict[str, Any]:
    """Validate a complete implementation snapshot without importing it."""

    snapshot = Path(snapshot).resolve()
    _assert_dir(snapshot, "implementation snapshot")
    manifest_path = snapshot / "code-manifest.json"
    _assert_file(manifest_path, "implementation snapshot manifest", root=snapshot)
    expected = _validate_hash(expected_manifest_sha256, "snapshot manifest hash")
    actual = _digest(manifest_path)
    if actual != expected:
        _fail(
            "snapshot_hash_mismatch",
            f"Frozen snapshot manifest changed: expected {expected}, got {actual}",
        )
    manifest = _read_json(manifest_path, "implementation snapshot manifest", root=snapshot)
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA:
        _fail("invalid_snapshot", "Frozen snapshot schema is unsupported")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        _fail("invalid_snapshot", "Frozen snapshot files map is empty")
    for relative, expected_hash in files.items():
        relative = _safe_relative(relative, "snapshot file path")
        path = snapshot / "files" / Path(relative)
        _assert_file(path, f"snapshot file {relative}", root=snapshot / "files")
        if _digest(path) != _validate_hash(expected_hash, f"snapshot hash {relative}"):
            _fail("snapshot_file_mismatch", f"Frozen snapshot file changed: {relative}")
    # The reader and both source-layout helpers are required even when a
    # discovery file contains only one candidate class.
    for relative in (
        "tracen_replay/vision.py",
        "tracen_replay/training_result_layout.py",
        "tracen_replay/current_state_layout.py",
    ):
        _snapshot_file_hash(manifest, relative)
    return {
        "path": str(snapshot),
        "manifest_path": str(manifest_path),
        "manifest_sha256": actual,
        "manifest": manifest,
    }


def _validate_discovery_helpers(report: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, str]:
    """Require every discovery helper hash to identify the frozen modules."""

    manifest = snapshot["manifest"]
    expected = {
        "training_result_layout.py": _snapshot_file_hash(
            manifest, "tracen_replay/training_result_layout.py"
        ),
        "current_state_layout.py": _snapshot_file_hash(
            manifest, "tracen_replay/current_state_layout.py"
        ),
    }
    if report.get("helper_unchanged") is False or report.get("helpers_unchanged") is False:
        _fail("discovery_changed", "Discovery report says its helper changed during the scan")
    if "helper_sha256" in report:
        value = _validate_hash(report.get("helper_sha256"), "discovery helper hash")
        if value != expected["training_result_layout.py"]:
            _fail("discovery_helper_mismatch", "Layout discovery used a different frozen helper")
        return {"training_result_layout.py": value}
    hashes = report.get("helper_hashes")
    if not isinstance(hashes, dict):
        _fail("invalid_discovery", "Discovery report has no helper hash record")
    found: dict[str, str] = {}
    for key, value in hashes.items():
        if not isinstance(key, str):
            continue
        normalized = key.replace("\\", "/").casefold()
        for name, expected_hash in expected.items():
            if normalized == f"tracen_replay/{name}" or normalized.endswith(f"/tracen_replay/{name}"):
                actual = _validate_hash(value, f"discovery helper hash {key}")
                if actual != expected_hash:
                    _fail("discovery_helper_mismatch", f"Discovery used a different {name}")
                found[name] = actual
    if "current_state_layout.py" not in found or "training_result_layout.py" not in found:
        _fail("invalid_discovery", "Current-state discovery omitted a required helper hash")
    return found


@dataclass
class CaptureContext:
    root: Path
    base_root: Path
    neural: Path
    capture_path: Path
    source_sha256: str
    frames: dict[str, dict[str, Any]]


@dataclass
class Candidate:
    frame_id: str
    timestamp_ms: int
    old_raw_path: Path
    old_raw_sha256: str
    old_raw: dict[str, Any]
    old_source_frame_path: Path
    old_source_frame_relative: str
    old_source_frame_sha256: str
    old_gameplay_path: Path
    old_gameplay_sha256: str
    kinds: set[str] = field(default_factory=set)
    discovery_paths: set[str] = field(default_factory=set)
    discovery_roots: set[str] = field(default_factory=set)
    target_frame: dict[str, Any] | None = None
    target_source_frame_path: Path | None = None
    target_gameplay_path: Path | None = None
    target_raw_path: Path | None = None


def _manifest_base(root: Path, manifest: dict[str, Any], field: str) -> tuple[Path, Path, Path]:
    base = manifest.get("base")
    if not isinstance(base, dict):
        _fail("invalid_manifest", f"{field} has no base object")
    folder = _safe_relative(base.get("folder", ""), f"{field}.base.folder", allow_empty=True)
    capture_rel = _safe_relative(base.get("capture"), f"{field}.base.capture")
    neural_rel = _safe_relative(base.get("neural"), f"{field}.base.neural")
    base_root = root / Path(folder)
    capture = root / Path(capture_rel)
    neural = base_root / Path(neural_rel)
    _assert_dir(base_root, f"{field} base root", root=root)
    _assert_file(capture, f"{field} capture", root=root)
    _assert_dir(neural, f"{field} neural directory", root=base_root)
    return base_root, capture, neural


def _capture_context(
    root: Path,
    manifest: dict[str, Any],
    *,
    field: str,
    expected_source_sha256: str | None = None,
) -> CaptureContext:
    base_root, capture_path, neural = _manifest_base(root, manifest, field)
    capture = _read_json(capture_path, f"{field} capture", root=root)
    source = capture.get("source")
    if not isinstance(source, dict):
        _fail("invalid_capture", f"{field} capture has no source object")
    source_sha = _validate_hash(source.get("sha256"), f"{field} capture source hash")
    declared_source = manifest.get('source_sha256')
    if declared_source is not None and _validate_hash(declared_source, f'{field} manifest source hash') != source_sha:
        _fail('source_mismatch', f'{field} manifest source hash differs from capture')
    declared_capture = manifest.get('base', {}).get('capture_sha256')
    if declared_capture is not None and _validate_hash(declared_capture, f'{field} capture manifest hash') != _digest(capture_path):
        _fail('capture_hash_mismatch', f'{field} capture bytes differ from manifest')
    if expected_source_sha256 is not None and source_sha != expected_source_sha256:
        _fail("source_mismatch", f"{field} capture source hash differs")
    frames_value = capture.get("frames")
    if not isinstance(frames_value, list):
        _fail("invalid_capture", f"{field} capture has no frames list")
    frames: dict[str, dict[str, Any]] = {}
    for index, frame in enumerate(frames_value):
        if not isinstance(frame, dict):
            _fail("invalid_capture", f"{field} frame {index} is not an object")
        frame_id = frame.get("id")
        if not isinstance(frame_id, str) or not FRAME_ID.fullmatch(frame_id):
            _fail("invalid_capture", f"{field} frame {index} has an invalid id")
        if frame_id in frames:
            _fail("invalid_capture", f"{field} contains duplicate frame {frame_id}")
        timestamp = frame.get("source_timestamp_ms")
        if type(timestamp) is not int or timestamp < 0:
            _fail("invalid_capture", f"{field} frame {frame_id} has an invalid timestamp")
        evidence = _safe_relative(frame.get("evidence"), f"{field} frame {frame_id} evidence")
        source_path = base_root / Path(evidence)
        _assert_file(source_path, f"{field} frame {frame_id} source image", root=base_root)
        frames[frame_id] = dict(frame, evidence=evidence)
    declared_count = manifest.get("base", {}).get("frame_count")
    if type(declared_count) is int and declared_count != len(frames):
        _fail("invalid_manifest", f"{field} frame count does not match capture")
    return CaptureContext(
        root=Path(root).resolve(),
        base_root=base_root.resolve(),
        neural=neural.resolve(),
        capture_path=capture_path.resolve(),
        source_sha256=source_sha,
        frames=frames,
    )


def _load_target(root: Path) -> CaptureContext:
    root = Path(root).resolve()
    _assert_dir(root, "refresh target root")
    manifest_path = root / "replay-input-manifest.json"
    manifest = _read_json(manifest_path, "refresh target replay manifest", root=root)
    return _capture_context(root, manifest, field="target")


def _load_old_context(record: dict[str, Any]) -> CaptureContext:
    root_value = record.get("root")
    neural_value = record.get("neural")
    base_value = record.get("base")
    if not all(isinstance(value, str) for value in (root_value, neural_value)):
        _fail("invalid_discovery", "Discovery recording lacks absolute root/neural namespaces")
    if base_value is not None and not isinstance(base_value, str):
        _fail("invalid_discovery", "Discovery recording has a non-string base namespace")
    if any(not Path(value).is_absolute() for value in (root_value, neural_value)):
        _fail("invalid_discovery", "Discovery root/neural namespaces must be absolute")
    if base_value is not None and not Path(base_value).is_absolute():
        _fail("invalid_discovery", "Discovery base namespace must be absolute")
    root = Path(root_value).resolve()
    neural = Path(neural_value).resolve()
    _assert_dir(root, "discovery source root")
    manifest_path = root / "replay-input-manifest.json"
    if not manifest_path.is_file():
        _fail("missing_source_manifest", f"Discovery source manifest is missing: {manifest_path}")
    manifest = _read_json(manifest_path, "discovery source replay manifest", root=root)
    capture = _capture_context(root, manifest, field="discovery source")
    # The layout discovery report records root/base/neural.  The current-state
    # discovery report predates the base field and records only root/neural;
    # derive the omitted base from its source manifest rather than weakening
    # the namespace check.  Both forms still have to agree with the manifest.
    if base_value is not None:
        base_root = Path(base_value).resolve()
        _assert_dir(base_root, "discovery source base", root=root)
        try:
            base_root.relative_to(root)
        except ValueError:
            _fail("invalid_discovery", "Discovery base namespace leaves source root")
        if capture.base_root != base_root:
            _fail("invalid_discovery", "Discovery base namespace disagrees with source manifest")
    _assert_dir(neural, "discovery source neural", root=capture.base_root)
    try:
        neural.relative_to(capture.base_root)
    except ValueError:
        _fail("invalid_discovery", "Discovery neural namespace leaves source base")
    if capture.neural != neural:
        _fail("invalid_discovery", "Discovery namespaces disagree with source manifest")
    return capture


def _read_old_candidate(
    record: dict[str, Any],
    candidate_data: dict[str, Any],
    discovery_path: Path,
    old_context: CaptureContext,
) -> Candidate:
    raw_value = candidate_data.get("raw")
    if not isinstance(raw_value, str) or not Path(raw_value).is_absolute():
        _fail("invalid_discovery", "Candidate raw path must be absolute")
    raw_path = Path(raw_value).resolve()
    declared_neural = old_context.neural
    try:
        raw_path.relative_to(declared_neural)
    except ValueError:
        _fail("candidate_outside_source", f"Candidate raw leaves discovery neural: {raw_path}")
    if raw_path.parent != declared_neural.resolve():
        _fail("candidate_outside_source", f"Candidate raw is not in the declared neural cache: {raw_path}")
    _assert_file(raw_path, "discovery candidate raw")
    frame_id = raw_path.stem
    if not FRAME_ID.fullmatch(frame_id):
        _fail("invalid_discovery", f"Candidate raw filename is not a frame id: {raw_path.name}")
    old_raw_sha = _validate_hash(candidate_data.get("raw_sha256"), "candidate raw hash")
    if _digest(raw_path) != old_raw_sha:
        _fail("old_raw_hash_mismatch", f"Candidate raw changed: {raw_path}")
    old_raw = _read_json(raw_path, "discovery candidate raw")
    timestamp = candidate_data.get("source_timestamp_ms")
    if type(timestamp) is not int or timestamp < 0:
        _fail("invalid_discovery", f"Candidate {frame_id} has an invalid timestamp")
    if old_raw.get("source_timestamp_ms") != timestamp:
        _fail("candidate_binding_mismatch", f"Candidate {frame_id} timestamp differs from its raw row")
    raw_evidence = _safe_relative(old_raw.get("evidence"), f"candidate {frame_id} gameplay evidence")
    if candidate_data.get("evidence") is not None and candidate_data.get("evidence") != raw_evidence:
        _fail("candidate_binding_mismatch", f"Candidate {frame_id} evidence differs from its raw row")
    gameplay_path = old_context.base_root / Path(raw_evidence)
    _assert_file(gameplay_path, f"candidate {frame_id} gameplay proof", root=old_context.base_root)
    gameplay_sha = _validate_hash(old_raw.get("gameplay_sha256"), f"candidate {frame_id} gameplay hash")
    if _hash_pane(gameplay_path) != gameplay_sha:
        _fail("gameplay_proof_mismatch", f"Candidate {frame_id} gameplay pixels changed")
    declared_gameplay_sha = candidate_data.get("gameplay_sha256")
    if declared_gameplay_sha is not None:
        if _validate_hash(declared_gameplay_sha, f"candidate {frame_id} gameplay hash") != gameplay_sha:
            _fail("candidate_binding_mismatch", f"Candidate {frame_id} gameplay hash differs from its raw row")
    source_frame_sha = _validate_hash(
        old_raw.get("source_frame_sha256"), f"candidate {frame_id} source-frame hash"
    )
    frame = old_context.frames.get(frame_id)
    if frame is None:
        _fail("candidate_binding_mismatch", f"Candidate {frame_id} is absent from source capture")
    if frame["source_timestamp_ms"] != timestamp:
        _fail("candidate_binding_mismatch", f"Candidate {frame_id} timestamp differs from source capture")
    source_frame_path = old_context.base_root / Path(frame["evidence"])
    _assert_file(source_frame_path, f"candidate {frame_id} source frame", root=old_context.base_root)
    if _source_frame_hash(source_frame_path) != source_frame_sha:
        _fail("source_frame_hash_mismatch", f"Candidate {frame_id} source frame changed")
    source_pane_sha = _digest_bytes(_source_pane(source_frame_path).tobytes())
    if source_pane_sha != gameplay_sha:
        _fail(
            "source_gameplay_mismatch",
            f"Candidate {frame_id} gameplay proof is not the pane from its source frame",
        )
    declared_source_frame_sha = candidate_data.get("source_frame_sha256")
    if declared_source_frame_sha is not None:
        if _validate_hash(declared_source_frame_sha, f"candidate {frame_id} source-frame hash") != source_frame_sha:
            _fail("candidate_binding_mismatch", f"Candidate {frame_id} source-frame hash differs from its raw row")
    candidate_source_sha = candidate_data.get("source_sha256")
    if candidate_source_sha is not None:
        if _validate_hash(candidate_source_sha, f"candidate {frame_id} source hash") != old_context.source_sha256:
            _fail("candidate_binding_mismatch", f"Candidate {frame_id} belongs to another recording")
    return Candidate(
        frame_id=frame_id,
        timestamp_ms=timestamp,
        old_raw_path=raw_path,
        old_raw_sha256=old_raw_sha,
        old_raw=old_raw,
        old_source_frame_path=source_frame_path,
        old_source_frame_relative=source_frame_path.relative_to(old_context.base_root).as_posix(),
        old_source_frame_sha256=source_frame_sha,
        old_gameplay_path=gameplay_path,
        old_gameplay_sha256=gameplay_sha,
        discovery_paths={str(discovery_path.resolve())},
        discovery_roots={str(old_context.root)},
    )


def _merge_candidate(
    candidates: dict[str, Candidate],
    candidate: Candidate,
    *,
    kind: str,
) -> None:
    candidate.kinds.add(kind)
    existing = candidates.get(candidate.frame_id)
    if existing is None:
        candidates[candidate.frame_id] = candidate
        return
    comparable = (
        existing.old_raw_sha256,
        existing.timestamp_ms,
        existing.old_source_frame_sha256,
        existing.old_gameplay_sha256,
        str(existing.old_raw_path),
    )
    incoming = (
        candidate.old_raw_sha256,
        candidate.timestamp_ms,
        candidate.old_source_frame_sha256,
        candidate.old_gameplay_sha256,
        str(candidate.old_raw_path),
    )
    if comparable != incoming:
        _fail(
            "conflicting_discovery",
            f"Discovery files disagree about candidate {candidate.frame_id}",
            details={"existing": comparable, "incoming": incoming},
        )
    existing.kinds.update(candidate.kinds)
    existing.discovery_paths.update(candidate.discovery_paths)
    existing.discovery_roots.update(candidate.discovery_roots)


def _load_candidates(
    discovery_paths: Iterable[Path],
    *,
    recording: str,
    snapshot: dict[str, Any],
    target: CaptureContext,
) -> tuple[list[Candidate], list[dict[str, Any]], list[Path]]:
    candidates: dict[str, Candidate] = {}
    discovery_meta: list[dict[str, Any]] = []
    source_roots: list[Path] = []
    paths = [Path(path).resolve() for path in discovery_paths]
    if not paths:
        _fail("missing_discovery", "At least one discovery report is required")
    for path in paths:
        report = _read_json(path, "discovery report")
        helper_hashes = _validate_discovery_helpers(report, snapshot)
        recordings = report.get("recordings")
        if not isinstance(recordings, list):
            _fail("invalid_discovery", f"Discovery report has no recordings list: {path}")
        matches = [
            item
            for item in recordings
            if isinstance(item, dict) and item.get("recording") == recording
        ]
        if len(matches) != 1:
            _fail(
                "discovery_recording_missing",
                f"Discovery report does not contain exactly one {recording} record: {path}",
            )
        record = matches[0]
        old_context = _load_old_context(record)
        if target.root == old_context.root or target.root.is_relative_to(old_context.root) or old_context.root.is_relative_to(target.root):
            _fail(
                "original_root",
                "Refresh target overlaps the discovery source root",
                details={"target": str(target.root), "source": str(old_context.root)},
            )
        source_roots.append(old_context.root)
        raw_candidates = record.get("candidates")
        if not isinstance(raw_candidates, list):
            _fail("invalid_discovery", f"Discovery record has no candidates list: {path}")
        declared_count = record.get(
            "new_result_candidates",
            record.get("new_current_state_candidates"),
        )
        if type(declared_count) is int and declared_count != len(raw_candidates):
            _fail("invalid_discovery", f"Discovery count does not match candidates: {path}")
        kind = "layout" if "new_result_candidates" in record else "current_state"
        for item in raw_candidates:
            if not isinstance(item, dict):
                _fail("invalid_discovery", f"Discovery candidate is not an object: {path}")
            candidate = _read_old_candidate(record, item, path, old_context)
            _merge_candidate(candidates, candidate, kind=kind)
        discovery_meta.append(
            {
                "path": str(path),
                "sha256": _digest(path),
                "recording": recording,
                "candidate_count": len(raw_candidates),
                "helper_hashes": helper_hashes,
                "source_root": str(old_context.root),
                "source_base": str(old_context.base_root),
            }
        )
    if not candidates:
        _fail("no_candidates", f"Discovery reports contain no candidates for {recording}")
    for candidate in candidates.values():
        target_frame = target.frames.get(candidate.frame_id)
        if target_frame is None:
            _fail("target_frame_missing", f"Target clone lacks candidate frame {candidate.frame_id}")
        if target_frame["source_timestamp_ms"] != candidate.timestamp_ms:
            _fail("target_binding_mismatch", f"Target timestamp differs for {candidate.frame_id}")
        target_source = target.base_root / Path(target_frame["evidence"])
        _assert_file(target_source, f"target source frame {candidate.frame_id}", root=target.base_root)
        if _source_frame_hash(target_source) != candidate.old_source_frame_sha256:
            _fail("target_source_mismatch", f"Target source pixels differ for {candidate.frame_id}")
        target_raw = target.neural / f"{candidate.frame_id}.json"
        _assert_file(target_raw, f"target raw row {candidate.frame_id}", root=target.base_root)
        if os.path.samefile(target_raw, candidate.old_raw_path):
            _fail("shared_raw_inode", f"Target raw row still aliases the source row: {target_raw}")
        if _digest(target_raw) != candidate.old_raw_sha256:
            _fail("target_old_raw_mismatch", f"Target clone raw differs before refresh: {target_raw}")
        target_raw_value = _read_json(
            target_raw, f"target raw row {candidate.frame_id}", root=target.base_root
        )
        if (
            target_raw_value.get("source_timestamp_ms") != candidate.timestamp_ms
            or target_raw_value.get("evidence") != candidate.old_raw.get("evidence")
        ):
            _fail("target_binding_mismatch", f"Target raw metadata differs for {candidate.frame_id}")
        target_gameplay = target.base_root / Path(
            _safe_relative(candidate.old_raw.get("evidence"), f"target {candidate.frame_id} gameplay evidence")
        )
        _assert_file(target_gameplay, f"target gameplay proof {candidate.frame_id}", root=target.base_root)
        if _hash_pane(target_gameplay) != candidate.old_gameplay_sha256:
            _fail("target_gameplay_mismatch", f"Target gameplay pixels differ for {candidate.frame_id}")
        if _digest_bytes(_source_pane(target_source).tobytes()) != candidate.old_gameplay_sha256:
            _fail(
                "target_source_gameplay_mismatch",
                f"Target gameplay proof is not the pane from its source frame for {candidate.frame_id}",
            )
        candidate.target_frame = target_frame
        candidate.target_source_frame_path = target_source
        candidate.target_gameplay_path = target_gameplay
        candidate.target_raw_path = target_raw
    return (
        sorted(candidates.values(), key=lambda item: (item.timestamp_ms, item.frame_id)),
        discovery_meta,
        source_roots,
    )


def _is_accepted_output(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if path.name.casefold() in NON_INPUT_JSON_NAMES:
        return True
    if path.name.casefold().endswith("-grade.json") or "grade" in path.name.casefold():
        return True
    return any(part.casefold() in NON_INPUT_JSON_PARTS for part in relative.parts)


def _iter_sidecar_json(root: Path) -> Iterable[Path]:
    root = root.resolve()
    for path in root.rglob("*.json"):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part.casefold() in SKIP_INPUT_DIRS for part in relative.parts):
            continue
        if _is_accepted_output(path, root):
            continue
        if path.is_file() and not _is_reparse(path):
            yield path


def _raw_identity_fingerprints(raw: dict[str, Any]) -> dict[str, str]:
    """Return the JSON identities used by the repository's sidecar loaders.

    Refinement modules historically used the default JSON encoding, while the
    newer lesson and hint caches use compact UTF-8 JSON.  These are identities
    of the immutable raw object, not replacements for its file hash.  Keeping
    the encodings explicit lets the dependency scan find a stale companion
    without importing mutable worktree code.
    """

    encodings = {
        "raw_fingerprint_default_ascii": dict(sort_keys=True),
        "raw_fingerprint_default_utf8": dict(sort_keys=True, ensure_ascii=False),
        "raw_fingerprint_compact_utf8": dict(
            sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
    }
    result: dict[str, str] = {}
    for name, options in encodings.items():
        try:
            encoded = json.dumps(raw, **options, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as exc:
            _fail("invalid_raw", f"Could not fingerprint source raw row: {exc}")
        result[name] = _digest_bytes(encoded)
    return result


def _normalized_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _normalized_path(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\\", "/").casefold().strip()


def _iter_json_scalars(
    value: Any,
    *,
    path: tuple[str, ...] = (),
    ancestors: tuple[dict[str, Any], ...] = (),
) -> Iterable[tuple[tuple[str, ...], Any, tuple[dict[str, Any], ...]]]:
    """Yield scalar values together with their containing JSON records."""

    if isinstance(value, dict):
        next_ancestors = ancestors + (value,)
        for key, child in value.items():
            if isinstance(key, str):
                yield from _iter_json_scalars(
                    child,
                    path=path + (key,),
                    ancestors=next_ancestors,
                )
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_json_scalars(
                child,
                path=path + (str(index),),
                ancestors=ancestors,
            )
        return
    yield path, value, ancestors


def _has_image_binding_context(ancestors: tuple[dict[str, Any], ...]) -> bool:
    """Require a hash/path match to belong to an actual source record.

    A bare ``source_frame_sha256`` string is not enough to make a companion
    image-bound.  Real inspection/recovery readings carry a timestamp or an
    evidence/path field in the same record (or its enclosing record).  This
    keeps malformed or fabricated metadata fail-closed while allowing those
    readings to survive a neural-row refresh.
    """

    for record in reversed(ancestors):
        keys = {_normalized_key(key) for key in record}
        if len(keys & IMAGE_BINDING_CONTEXT_KEYS) >= 2:
            return True
    return False


def _raw_dependency_reason(
    key: str,
    value: str,
    candidate: Candidate,
    *,
    identity_reasons: dict[str, str],
) -> str | None:
    """Classify an exact match that is tied to the serialized raw row."""

    if key in IMAGE_HASH_KEYS:
        return None
    if key in IMAGE_PATH_KEYS:
        return None
    if value == candidate.old_raw_sha256:
        if key in RAW_DEPENDENCY_KEYS:
            return key
        if key in GENERIC_HASH_KEYS or key.endswith("_hash") or key.endswith("_sha256"):
            return "old_raw_file_sha256"
        # An exact old-file digest under an unrecognised field is still a
        # dependency.  Rejecting it is safer than silently retaining stale
        # parsed data.
        return "old_raw_file_sha256_unlabelled"
    reason = identity_reasons.get(value)
    if reason is None:
        return None
    if key in RAW_DEPENDENCY_KEYS or "fingerprint" in key or key in GENERIC_HASH_KEYS:
        return reason
    # Semantic fingerprints are identities of the old raw object even when a
    # legacy companion used a less specific field name.
    return f"{reason}_unlabelled"


def _image_match_reason(key: str, value: str, candidate: Candidate) -> str | None:
    if key in {"source_frame_sha", "source_frame_sha256", "source_frame_hash", "source_image_sha", "source_image_sha256", "source_image_hash", "frame_sha", "frame_sha256", "frame_hash"} and value == candidate.old_source_frame_sha256:
        return "source_frame_image_identity"
    if key in {"gameplay_sha", "gameplay_sha256", "gameplay_hash", "gameplay_image_sha", "gameplay_image_sha256"} and value == candidate.old_gameplay_sha256:
        return "gameplay_image_identity"
    return None


def _image_path_match_reason(key: str, value: str, candidate: Candidate) -> str | None:
    if key not in IMAGE_PATH_KEYS:
        return None
    normalized = _normalized_path(value)
    if not normalized or not normalized.casefold().endswith((".png", ".jpg", ".jpeg", ".webp")):
        return None
    expected = {
        _normalized_path(candidate.old_raw.get("evidence")),
        _normalized_path(candidate.old_gameplay_path),
        _normalized_path(candidate.old_source_frame_relative),
        _normalized_path(candidate.old_source_frame_path),
    }
    expected.discard("")
    if normalized in expected:
        return "image_evidence_path"
    return None


def _sidecar_dependencies(
    root: Path,
    candidates: Iterable[Candidate],
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Classify old-row dependencies without treating image identity as stale.

    The returned conflicts are only serialized-row dependencies.  Image-only
    matches are structurally checked and returned as a bounded audit summary;
    they are intentionally not refusal conditions because their pixels do not
    change when the neural JSON is reread.
    """

    candidate_list = list(candidates)
    candidate_map = {candidate.frame_id: candidate for candidate in candidate_list}
    value_identities: dict[str, list[tuple[str, str]]] = {}
    path_identities: dict[str, list[tuple[str, str]]] = {}
    path_tokens: set[str] = set()

    def add_value(value: Any, candidate: Candidate, reason: str) -> None:
        if isinstance(value, str) and value:
            value_identities.setdefault(value, []).append((candidate.frame_id, reason))

    def add_path(value: Any, candidate: Candidate, kind: str) -> None:
        normalized = _normalized_path(value)
        if normalized:
            path_identities.setdefault(normalized, []).append((candidate.frame_id, kind))
            if isinstance(value, str):
                # JSON escapes backslashes, while sidecars also occur with
                # POSIX separators.  Keep only exact path spellings; there is
                # deliberately no basename fallback.
                path_tokens.update(
                    {
                        value,
                        value.replace("\\", "/"),
                        value.replace("/", "\\"),
                        json.dumps(value, ensure_ascii=False)[1:-1],
                    }
                )

    for candidate in candidate_list:
        add_value(candidate.old_raw_sha256, candidate, "old_raw_file_sha256")
        for name, value in _raw_identity_fingerprints(candidate.old_raw).items():
            add_value(value, candidate, name)
        for value in (
            f"neural/{candidate.frame_id}.json",
            f"neural\\{candidate.frame_id}.json",
            str(candidate.old_raw_path),
            candidate.old_raw_path.as_posix(),
        ):
            add_path(value, candidate, "neural_path")
        for value in (
            candidate.old_raw.get("evidence"),
            f"gameplay/{candidate.frame_id}.png",
            f"gameplay\\{candidate.frame_id}.png",
            str(candidate.old_gameplay_path),
            candidate.old_gameplay_path.as_posix(),
            candidate.old_source_frame_relative,
            str(candidate.old_source_frame_path),
            candidate.old_source_frame_path.as_posix(),
        ):
            add_path(value, candidate, "image_evidence_path")
        add_value(candidate.old_source_frame_sha256, candidate, "source_frame_sha256")
        add_value(candidate.old_gameplay_sha256, candidate, "gameplay_sha256")

    conflicts: list[dict[str, Any]] = []
    image_bound_pairs: set[tuple[str, str]] = set()
    image_examples: list[dict[str, Any]] = []

    def record_image_binding(frame_id: str, path: Path, reason: str, field: str) -> None:
        identity = (frame_id, str(path))
        if identity in image_bound_pairs:
            return
        image_bound_pairs.add(identity)
        if len(image_examples) < 32:
            image_examples.append(
                {
                    "frame_id": frame_id,
                    "path": str(path),
                    "reason": reason,
                    "field": field,
                }
            )

    for path in _iter_sidecar_json(root):
        try:
            payload = path.read_bytes()
        except OSError as exc:
            _fail("sidecar_read_failed", f"Could not inspect sidecar {path}: {exc}")
        if not any(needle.encode("utf-8") in payload for needle in value_identities) and not any(
            needle.encode("utf-8") in payload for needle in path_tokens
        ):
            continue
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail(
                "sidecar_read_failed",
                f"Could not parse matching sidecar {path}: {exc}",
            )
        seen: set[tuple[str, str, str]] = set()
        for scalar_path, scalar, ancestors in _iter_json_scalars(decoded):
            if not isinstance(scalar, str):
                continue
            key = _normalized_key(scalar_path[-1] if scalar_path else "")
            for frame_id, identity_reason in value_identities.get(scalar, ()):
                candidate = candidate_map[frame_id]
                image_reason = _image_match_reason(key, scalar, candidate)
                if image_reason is not None:
                    if not _has_image_binding_context(ancestors):
                        marker = (frame_id, "unverified_image_binding", ".".join(scalar_path))
                        if marker not in seen:
                            conflicts.append(
                                {
                                    "frame_id": frame_id,
                                    "path": str(path),
                                    "reason": "unverified_image_binding",
                                    "field": ".".join(scalar_path),
                                    "old_raw_sha256": candidate.old_raw_sha256,
                                }
                            )
                            seen.add(marker)
                    else:
                        record_image_binding(
                            frame_id, path, image_reason, ".".join(scalar_path)
                        )
                    continue
                reason = _raw_dependency_reason(
                    key,
                    scalar,
                    candidate,
                    identity_reasons={scalar: identity_reason},
                )
                if reason is not None:
                    marker = (frame_id, reason, ".".join(scalar_path))
                    if marker not in seen:
                        conflicts.append(
                            {
                                "frame_id": frame_id,
                                "path": str(path),
                                "reason": reason,
                                "field": ".".join(scalar_path),
                                "old_raw_sha256": candidate.old_raw_sha256,
                            }
                        )
                        seen.add(marker)
            normalized_scalar = _normalized_path(scalar)
            for frame_id, path_reason in path_identities.get(normalized_scalar, ()):
                candidate = candidate_map[frame_id]
                if path_reason == "neural_path":
                    marker = (frame_id, path_reason, ".".join(scalar_path))
                    if marker not in seen:
                        conflicts.append(
                            {
                                "frame_id": frame_id,
                                "path": str(path),
                                "reason": path_reason,
                                "field": ".".join(scalar_path),
                                "old_raw_sha256": candidate.old_raw_sha256,
                            }
                        )
                        seen.add(marker)
                elif path_reason == "image_evidence_path":
                    if _image_path_match_reason(key, scalar, candidate) is None or key in RAW_DEPENDENCY_KEYS:
                        marker = (frame_id, "unverified_image_binding", ".".join(scalar_path))
                        if marker not in seen:
                            conflicts.append(
                                {
                                    "frame_id": frame_id,
                                    "path": str(path),
                                    "reason": "unverified_image_binding",
                                    "field": ".".join(scalar_path),
                                    "old_raw_sha256": candidate.old_raw_sha256,
                                }
                            )
                            seen.add(marker)
                        continue
                    if not _has_image_binding_context(ancestors):
                        marker = (frame_id, "unverified_image_binding", ".".join(scalar_path))
                        if marker not in seen:
                            conflicts.append(
                                {
                                    "frame_id": frame_id,
                                    "path": str(path),
                                    "reason": "unverified_image_binding",
                                    "field": ".".join(scalar_path),
                                    "old_raw_sha256": candidate.old_raw_sha256,
                                }
                            )
                            seen.add(marker)
                    else:
                        record_image_binding(
                            frame_id,
                            path,
                            "image_evidence_path",
                            ".".join(scalar_path),
                        )
    return conflicts, len(image_bound_pairs), image_examples


def _sidecar_conflicts(root: Path, candidates: Iterable[Candidate]) -> list[dict[str, Any]]:
    """Return only companions invalidated by replacing the raw neural row."""

    return _sidecar_dependencies(root, candidates)[0]


def _reader_raw(reader: Any, candidate: Candidate) -> tuple[dict[str, Any], str]:
    if candidate.target_source_frame_path is None:
        _fail("internal_error", f"Candidate is not bound to target: {candidate.frame_id}")
    pane = _source_pane(candidate.target_source_frame_path)
    actual_gameplay_sha = _digest_bytes(pane.tobytes())
    try:
        value = reader.read(pane)
    except Exception as exc:  # noqa: BLE001 - convert reader failures to audit errors
        _fail("reader_failed", f"Frozen reader failed for {candidate.frame_id}: {exc}")
    if not isinstance(value, dict):
        _fail("reader_invalid", f"Frozen reader returned a non-object for {candidate.frame_id}")
    engine = value.get("engine_fingerprint")
    if not isinstance(engine, str) or not engine:
        _fail("reader_invalid", f"Frozen reader omitted engine fingerprint for {candidate.frame_id}")
    model = value.get("model_sha256")
    if not isinstance(model, (dict, str)) or not model:
        _fail("reader_invalid", f"Frozen reader omitted model provenance for {candidate.frame_id}")
    reported_gameplay_sha = value.get("gameplay_sha256")
    if not isinstance(reported_gameplay_sha, str) or reported_gameplay_sha.lower() != actual_gameplay_sha:
        _fail("reader_gameplay_mismatch", f"Frozen reader gameplay hash differs for {candidate.frame_id}")
    enriched = dict(
        value,
        source_timestamp_ms=candidate.timestamp_ms,
        evidence=candidate.old_raw["evidence"],
        source_frame_sha256=candidate.old_source_frame_sha256,
        gameplay_sha256=actual_gameplay_sha,
    )
    _validate_strict_json_object(enriched, f"Frozen reader payload for {candidate.frame_id}")
    # Compute every identity before the first selected row is replaced.  This
    # keeps a later invalid payload from leaving an earlier replacement behind.
    _raw_identity_fingerprints(enriched)
    return enriched, actual_gameplay_sha


def _write_replacement(path: Path, payload: dict[str, Any], root: Path) -> str:
    _validate_strict_json_object(payload, "refresh raw payload")
    _assert_file(path, "refresh raw destination", root=root)
    if _is_reparse(path):
        _fail("reparse_point", f"Refresh raw destination is a reparse point: {path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.refresh.tmp")
    _path_inside(root, temporary, "refresh temporary raw")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    except (OSError, TypeError, ValueError) as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        _fail("write_failed", f"Could not persist refreshed raw row {path}: {exc}")
    return _digest(path)


def _json_bytes(value: dict[str, Any], field: str) -> bytes:
    """Serialize a strict JSON object exactly as the replacement writer does."""

    _validate_strict_json_object(value, field)
    try:
        return (
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        _fail("invalid_json", f"{field} is not serializable: {exc}")


def _write_json_replacement(path: Path, payload: dict[str, Any], root: Path, field: str) -> str:
    """Atomically replace an existing JSON object below a refresh root."""

    encoded = _json_bytes(payload, field)
    _assert_file(path, field, root=root)
    if _is_reparse(path):
        _fail("reparse_point", f"{field} is a reparse point: {path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.refresh.tmp")
    _path_inside(root, temporary, f"{field} temporary")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        _fail("write_failed", f"Could not persist {field}: {path}: {exc}")
    return _digest(path)


def _progress_journal_path(root: Path, audit_name: str) -> Path:
    """Return the durable journal path associated with one refresh audit."""

    root = Path(root).resolve()
    relative = Path(_safe_relative(audit_name, "audit name"))
    journal_relative = relative.with_suffix(".progress.json")
    path = root / "reports" / "refresh-journals" / journal_relative
    _path_inside(root, path, "refresh progress journal")
    return path


def _create_progress_journal(
    path: Path,
    payload: dict[str, Any],
    root: Path,
) -> str:
    """Create the initial journal before any clone data is mutated."""

    encoded = _json_bytes(payload, "refresh progress journal")
    _path_inside(root, path, "refresh progress journal")
    if path.exists() or path.is_symlink():
        _fail("output_exists", f"Refresh progress journal already exists: {path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        _fail("write_failed", f"Could not create refresh progress journal: {path}: {exc}")
    return _digest(path)


def _progress_failure(exc: BaseException) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "code": getattr(exc, "code", None),
        "message": str(exc),
    }


_CHOICE_OBSERVER: Any | None = None
_CHOICE_OBSERVER_SHA256: str | None = None


def _choice_observer(snapshot: dict[str, Any]) -> tuple[Any, str]:
    """Load the frozen choice observer without importing the mutable package.

    The dependency refresh only re-runs ``choice_evidence.observe`` over the
    already captured gameplay pane and refreshed OCR lines.  Loading this
    small module by path avoids importing ``tracen_replay.vision`` before the
    frozen reader has completed its provenance checks.
    """

    global _CHOICE_OBSERVER, _CHOICE_OBSERVER_SHA256
    relative = "tracen_replay/choice_evidence.py"
    expected_sha256 = _snapshot_file_hash(snapshot["manifest"], relative)
    module_path = Path(snapshot["path"]) / "files" / Path(relative)
    _assert_file(module_path, "frozen choice observer", root=Path(snapshot["path"]) / "files")
    module_sha256 = _digest(module_path)
    if module_sha256 != expected_sha256:
        _fail("snapshot_file_mismatch", "Frozen choice observer changed during refresh")
    if _CHOICE_OBSERVER is not None and _CHOICE_OBSERVER_SHA256 == module_sha256:
        return _CHOICE_OBSERVER, module_sha256
    try:
        spec = importlib.util.spec_from_file_location(
            "_tracen_replay_refresh_choice_evidence_v1", module_path
        )
        if spec is None or spec.loader is None:
            raise ImportError("choice observer module has no import loader")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        observer = getattr(module, "observe")
    except Exception as exc:  # noqa: BLE001 - convert loader failures to audit errors
        _fail("choice_refresh_unavailable", f"Could not load choice observer: {exc}")
    if not callable(observer):
        _fail("choice_refresh_unavailable", "Choice observer has no callable observe function")
    _CHOICE_OBSERVER = observer
    _CHOICE_OBSERVER_SHA256 = module_sha256
    return observer, module_sha256


def _choice_raw_fingerprint(raw: dict[str, Any]) -> str:
    """Match ``refine_contrast.fingerprint`` used by choice sidecars."""

    _validate_strict_json_object(raw, "choice refresh raw row")
    try:
        encoded = json.dumps(raw, sort_keys=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        _fail("invalid_raw", f"Could not fingerprint choice refresh row: {exc}")
    return _digest_bytes(encoded)


def _manifest_value(manifest: dict[str, Any], field: str) -> Any:
    """Resolve a dotted ``supplements.N.entries.M.field`` path safely."""

    value: Any = manifest
    for component in field.split("."):
        if isinstance(value, dict):
            if component not in value:
                return None
            value = value[component]
        elif isinstance(value, list) and component.isdigit():
            index = int(component)
            if index >= len(value):
                return None
            value = value[index]
        else:
            return None
    return value


def _choice_folder(value: Any, field: str) -> str | None:
    if not isinstance(value, str):
        return None
    relative = _safe_relative(value, field)
    if PurePosixPath(relative).name.casefold() != CHOICE_REFINEMENT_LEAF:
        return None
    return relative


def _choice_dependency_plan(
    root: Path,
    candidates: Iterable[Candidate],
    conflicts: Iterable[dict[str, Any]],
    *,
    audit_name: str,
) -> dict[str, Any]:
    """Validate a bounded choice-refinement dependency closure.

    This is the only dependency family that the discovered-base refresh can
    regenerate in place.  Every accepted conflict must point either to the
    registered ``choice-refinement`` entry in the replay manifest or to its
    exact sidecar.  Other raw-bound companions remain fail-closed.
    """

    root = Path(root).resolve()
    manifest_path = root / "replay-input-manifest.json"
    manifest = _read_json(manifest_path, "target replay manifest", root=root)
    supplements = manifest.get("supplements")
    if not isinstance(supplements, list):
        _fail("invalid_manifest", "Target replay manifest has no supplements list")
    selected = {candidate.frame_id: candidate for candidate in candidates}
    manifest_entries: dict[tuple[int, int], dict[str, Any]] = {}
    frame_entries: dict[str, dict[str, Any]] = {}
    sidecar_paths: dict[str, Path] = {}
    for supplement_index, supplement in enumerate(supplements):
        if not isinstance(supplement, dict):
            _fail("invalid_manifest", "Target replay manifest supplement is not an object")
        folder = _choice_folder(
            supplement.get("folder"),
            f"target replay manifest supplements.{supplement_index}.folder",
        )
        if folder is None:
            continue
        name = supplement.get("name")
        if name not in (None, "choice"):
            _fail(
                "choice_dependency_conflict",
                "Choice-refinement folder has an unsupported manifest name",
                details={"supplement_index": supplement_index, "name": name},
            )
        entries = supplement.get("entries")
        if not isinstance(entries, list):
            _fail("invalid_manifest", "Choice-refinement supplement has no entries list")
        for entry_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                _fail("invalid_manifest", "Choice-refinement manifest entry is not an object")
            entry_path = _safe_relative(
                entry.get("path"),
                f"target replay manifest supplements.{supplement_index}.entries.{entry_index}.path",
            )
            entry_name = PurePosixPath(entry_path).name
            if entry_name != entry_path or not entry_name.endswith(".json"):
                _fail(
                    "choice_dependency_conflict",
                    "Choice-refinement entries must name direct JSON sidecars",
                    details={"path": entry_path},
                )
            frame_id = entry_name[:-5]
            if frame_id not in selected:
                continue
            sidecar_path = root / Path(folder) / Path(entry_path)
            _assert_file(sidecar_path, "choice-refinement sidecar", root=root)
            key = (supplement_index, entry_index)
            if frame_id in frame_entries:
                _fail(
                    "choice_dependency_conflict",
                    f"Choice-refinement frame is registered more than once: {frame_id}",
                    details={"frame_id": frame_id},
                )
            raw_path_value = entry.get("raw_path")
            raw_path_relative = _safe_relative(
                raw_path_value,
                f"target replay manifest supplements.{supplement_index}.entries.{entry_index}.raw_path",
            )
            raw_path = root / Path(raw_path_relative)
            candidate = selected[frame_id]
            if candidate.target_raw_path is None or raw_path.resolve() != candidate.target_raw_path.resolve():
                _fail(
                    "choice_dependency_conflict",
                    f"Choice-refinement raw path does not bind {frame_id} to the selected base row",
                    details={"frame_id": frame_id, "raw_path": str(raw_path)},
                )
            entry_timestamp = entry.get("source_timestamp_ms")
            if type(entry_timestamp) is not int or entry_timestamp != candidate.timestamp_ms:
                _fail(
                    "choice_dependency_conflict",
                    f"Choice-refinement timestamp does not bind {frame_id} to the selected base row",
                    details={"frame_id": frame_id, "source_timestamp_ms": entry_timestamp},
                )
            entry_evidence_relative = _safe_relative(
                entry.get("evidence_path"),
                f"target replay manifest supplements.{supplement_index}.entries.{entry_index}.evidence_path",
            )
            entry_evidence_path = root / Path(entry_evidence_relative)
            if candidate.target_gameplay_path is None or entry_evidence_path.resolve() != candidate.target_gameplay_path.resolve():
                _fail(
                    "choice_dependency_conflict",
                    f"Choice-refinement evidence path does not bind {frame_id} to its gameplay proof",
                    details={"frame_id": frame_id, "evidence_path": str(entry_evidence_path)},
                )
            sidecar_value = _read_json(sidecar_path, "choice-refinement sidecar", root=root)
            if sidecar_value.get("version") != 2 or not isinstance(sidecar_value.get("observation"), dict):
                _fail(
                    "choice_dependency_conflict",
                    f"Choice-refinement sidecar is not a supported v2 observation: {sidecar_path}",
                )
            expected_evidence_sha = _digest(candidate.target_gameplay_path)
            manifest_sidecar_sha = _validate_hash(
                entry.get("sidecar_sha256"),
                f"target replay manifest supplements.{supplement_index}.entries.{entry_index}.sidecar_sha256",
            )
            if manifest_sidecar_sha != _digest(sidecar_path):
                _fail(
                    "choice_dependency_conflict",
                    f"Choice-refinement manifest sidecar hash is stale: {sidecar_path}",
                    details={"frame_id": frame_id},
                )
            manifest_raw_sha = _validate_hash(
                entry.get("raw_sha256"),
                f"target replay manifest supplements.{supplement_index}.entries.{entry_index}.raw_sha256",
            )
            if manifest_raw_sha != sidecar_value.get("raw_sha256"):
                _fail(
                    "choice_dependency_conflict",
                    f"Choice-refinement manifest raw identity disagrees with its sidecar: {sidecar_path}",
                    details={"frame_id": frame_id},
                )
            manifest_evidence_sha = _validate_hash(
                entry.get("evidence_sha256"),
                f"target replay manifest supplements.{supplement_index}.entries.{entry_index}.evidence_sha256",
            )
            if manifest_evidence_sha != expected_evidence_sha or manifest_evidence_sha != sidecar_value.get("evidence_sha256"):
                _fail(
                    "choice_dependency_conflict",
                    f"Choice-refinement manifest evidence hash is stale: {sidecar_path}",
                    details={"frame_id": frame_id},
                )
            old_identity = _raw_identity_fingerprints(candidate.old_raw)
            if sidecar_value.get("raw_sha256") != old_identity["raw_fingerprint_default_ascii"]:
                _fail(
                    "choice_dependency_conflict",
                    f"Choice-refinement sidecar does not match the selected old raw row: {sidecar_path}",
                    details={"frame_id": frame_id},
                )
            evidence_sha = _digest(candidate.target_gameplay_path) if candidate.target_gameplay_path else None
            if sidecar_value.get("evidence_sha256") != evidence_sha:
                _fail(
                    "choice_dependency_conflict",
                    f"Choice-refinement sidecar evidence does not match the selected gameplay proof: {sidecar_path}",
                    details={"frame_id": frame_id},
                )
            record = {
                "frame_id": frame_id,
                "candidate": candidate,
                "supplement_index": supplement_index,
                "entry_index": entry_index,
                "folder": folder,
                "entry_path": entry_path,
                "sidecar_path": sidecar_path,
                "sidecar_before_bytes": sidecar_path.read_bytes(),
                "sidecar_before_sha256": _digest(sidecar_path),
                "sidecar_before": sidecar_value,
                "manifest_entry": entry,
                "manifest_raw_path": raw_path_relative,
            }
            manifest_entries[key] = record
            frame_entries[frame_id] = record
            sidecar_paths[frame_id] = sidecar_path

    conflict_list = [dict(item) for item in conflicts]
    conflict_frames: set[str] = set()
    allowed_conflicts: list[dict[str, Any]] = []
    manifest_path_resolved = manifest_path.resolve()
    for conflict in conflict_list:
        frame_id = conflict.get("frame_id")
        reason = conflict.get("reason")
        path_value = conflict.get("path")
        field = conflict.get("field")
        if frame_id not in selected or reason not in CHOICE_DEPENDENCY_REASONS:
            _fail(
                "sidecar_conflict",
                "Refresh-choice mode can only regenerate registered choice-refinement dependencies",
                details=conflict_list,
            )
        if not isinstance(path_value, str) or not isinstance(field, str):
            _fail("sidecar_conflict", "Choice dependency conflict lacks a path or field", details=conflict_list)
        path = Path(path_value).resolve()
        if path == manifest_path_resolved:
            match = re.fullmatch(r"supplements\.(\d+)\.entries\.(\d+)\.(raw_path|raw_sha256)", field)
            if match is None:
                _fail("sidecar_conflict", "Manifest conflict is outside a choice raw binding", details=conflict_list)
            key = (int(match.group(1)), int(match.group(2)))
            record = manifest_entries.get(key)
            if record is None or record["frame_id"] != frame_id:
                _fail("sidecar_conflict", "Manifest conflict does not bind a registered choice sidecar", details=conflict_list)
        else:
            try:
                path.relative_to(root)
            except ValueError:
                _fail("sidecar_conflict", "Choice dependency path leaves the target root", details=conflict_list)
            record = next((item for item in frame_entries.values() if item["sidecar_path"].resolve() == path), None)
            if record is None or record["frame_id"] != frame_id:
                _fail("sidecar_conflict", "Conflict is outside a registered choice-refinement sidecar", details=conflict_list)
            if field != "raw_sha256":
                _fail("sidecar_conflict", "Choice sidecar conflict is outside its raw identity", details=conflict_list)
        conflict_frames.add(frame_id)
        allowed_conflicts.append(conflict)

    archive_root = root / "reports" / "refresh-archives" / f"{Path(audit_name).stem}-choice-dependencies"
    _path_inside(root, archive_root, "choice dependency archive")
    if archive_root.exists():
        _fail("output_exists", f"Choice dependency archive already exists: {archive_root}")
    if not conflict_frames:
        return {
            "manifest_path": manifest_path,
            "manifest_before": manifest,
            "manifest_before_sha256": _digest(manifest_path),
            "manifest_before_bytes": manifest_path.read_bytes(),
            "entries": {},
            "conflicts": allowed_conflicts,
            "archive_root": archive_root,
            "observer_sha256": None,
        }
    missing = sorted(frame_id for frame_id in conflict_frames if frame_id not in frame_entries)
    if missing:
        _fail(
            "sidecar_conflict",
            "Choice dependency conflicts are not all registered in the replay manifest",
            details={"missing_frames": missing, "conflicts": conflict_list},
        )
    return {
        "manifest_path": manifest_path,
        "manifest_before": manifest,
        "manifest_before_sha256": _digest(manifest_path),
        "manifest_before_bytes": manifest_path.read_bytes(),
        "entries": {frame_id: frame_entries[frame_id] for frame_id in sorted(conflict_frames)},
        "conflicts": allowed_conflicts,
        "archive_root": archive_root,
        "observer_sha256": None,
    }


def _choice_sidecar_payload(
    candidate: Candidate,
    raw: dict[str, Any],
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Recompute one choice sidecar from refreshed OCR and immutable pixels."""

    if candidate.target_gameplay_path is None:
        _fail("internal_error", f"Choice candidate has no gameplay proof: {candidate.frame_id}")
    gameplay_path = candidate.target_gameplay_path
    actual_gameplay_sha = _hash_pane(gameplay_path)
    if raw.get("gameplay_sha256") != actual_gameplay_sha:
        _fail("choice_refresh_source_mismatch", f"Choice gameplay proof changed: {candidate.frame_id}")
    lines = raw.get("lines")
    if not isinstance(lines, list):
        _fail("choice_refresh_invalid_raw", f"Choice refresh raw row has no OCR lines: {candidate.frame_id}")
    try:
        from PIL import Image

        with Image.open(gameplay_path) as image:
            pane = image.convert("RGB")
        observer, observer_sha256 = _choice_observer(snapshot)
        observation = observer(pane, lines)
    except RefreshError:
        raise
    except Exception as exc:  # noqa: BLE001 - convert observer failures to audit errors
        _fail("choice_refresh_failed", f"Choice observation failed for {candidate.frame_id}: {exc}")
    if not isinstance(observation, dict):
        _fail("choice_refresh_invalid_observation", f"Choice observer returned a non-object for {candidate.frame_id}")
    payload = {
        "version": 2,
        "raw_sha256": _choice_raw_fingerprint(raw),
        "evidence_sha256": _digest(gameplay_path),
        "observation": observation,
        "independent_observations": False,
    }
    _validate_strict_json_object(payload, f"Choice refresh sidecar {candidate.frame_id}")
    return payload, observer_sha256


def _updated_choice_manifest(
    dependency_plan: dict[str, Any],
    refreshed: dict[str, tuple[Candidate, dict[str, Any], str]],
    payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return a manifest copy whose choice entries bind the new sidecars."""

    manifest = copy.deepcopy(dependency_plan["manifest_before"])
    supplements = manifest.get("supplements")
    if not isinstance(supplements, list):
        _fail("invalid_manifest", "Target replay manifest has no supplements list")
    for frame_id, record in dependency_plan["entries"].items():
        payload = payloads.get(frame_id)
        refreshed_row = refreshed.get(frame_id)
        if payload is None or refreshed_row is None:
            _fail("internal_error", f"Choice dependency refresh is missing {frame_id}")
        candidate, raw, _gameplay_sha = refreshed_row
        entry = supplements[record["supplement_index"]]["entries"][record["entry_index"]]
        entry["sidecar_sha256"] = _digest_bytes(_json_bytes(payload, f"choice sidecar {frame_id}"))
        entry["raw_sha256"] = payload["raw_sha256"]
        entry["evidence_sha256"] = payload["evidence_sha256"]
        entry["source_timestamp_ms"] = raw["source_timestamp_ms"]
        expected_raw_path = Path(record["manifest_raw_path"])
        if candidate.target_raw_path is None or (Path(dependency_plan["manifest_path"].parent) / expected_raw_path).resolve() != candidate.target_raw_path.resolve():
            _fail("choice_dependency_conflict", f"Choice manifest raw binding changed for {frame_id}")
    _validate_strict_json_object(manifest, "updated replay manifest")
    return manifest


def _archive_choice_dependencies(dependency_plan: dict[str, Any]) -> list[dict[str, str]]:
    """Archive old choice sidecars and the old manifest before replacement."""

    archive_root = dependency_plan["archive_root"]
    if archive_root.exists():
        _fail("output_exists", f"Choice dependency archive already exists: {archive_root}")
    _path_inside(
        Path(dependency_plan["manifest_path"]).parent,
        archive_root,
        "choice dependency archive",
    )
    try:
        archive_root.mkdir(parents=True, exist_ok=False)
        sidecar_archive_root = archive_root / CHOICE_REFINEMENT_LEAF
        sidecar_archive_root.mkdir()
        manifest_archive = archive_root / "replay-input-manifest.json"
        manifest_archive.write_bytes(dependency_plan["manifest_before_bytes"])
        records = [
            (manifest_archive, dependency_plan["manifest_path"]),
        ]
        for frame_id, record in sorted(dependency_plan["entries"].items()):
            destination = sidecar_archive_root / record["sidecar_path"].name
            destination.write_bytes(record["sidecar_before_bytes"])
            records.append((destination, record["sidecar_path"]))
    except OSError as exc:
        _fail("write_failed", f"Could not archive choice dependencies: {exc}")
    return [
        {"archive_path": str(destination), "original_path": str(original)}
        for destination, original in records
    ]


def _plan(
    *,
    snapshot: dict[str, Any],
    target: CaptureContext,
    recording: str,
    candidates: list[Candidate],
    selected: list[Candidate],
    discovery_meta: list[dict[str, Any]],
    source_roots: list[Path],
    source_video: Path | None,
    max_frames: int | None,
    image_bound_sidecar_count: int,
    image_bound_sidecar_examples: list[dict[str, Any]],
) -> dict[str, Any]:
    video_hash = None
    if source_video is not None:
        _assert_file(source_video, "source video")
        video_hash = _digest(source_video)
        if video_hash != target.source_sha256:
            _fail("source_mismatch", "Source video hash differs from target capture")
    rows = []
    for candidate in selected:
        rows.append(
            {
                "frame_id": candidate.frame_id,
                "source_timestamp_ms": candidate.timestamp_ms,
                "candidate_kinds": sorted(candidate.kinds),
                "discovery_reports": sorted(candidate.discovery_paths),
                "source_root": sorted(candidate.discovery_roots),
                "old_raw_path": str(candidate.old_raw_path),
                "old_raw_sha256": candidate.old_raw_sha256,
                "old_raw_identity_fingerprints": _raw_identity_fingerprints(candidate.old_raw),
                "old_source_frame_sha256": candidate.old_source_frame_sha256,
                "old_gameplay_sha256": candidate.old_gameplay_sha256,
                "target_raw_path": str(candidate.target_raw_path),
                "target_source_frame_path": str(candidate.target_source_frame_path),
                "target_gameplay_path": str(candidate.target_gameplay_path),
                "status": "ready",
            }
        )
    return {
        "schema_version": SCHEMA,
        "recording": recording,
        "target_root": str(target.root),
        "target_base_root": str(target.base_root),
        "source_sha256": target.source_sha256,
        "source_video": str(source_video) if source_video is not None else None,
        "source_video_sha256": video_hash,
        "source_video_hash_verified": source_video is not None,
        "snapshot": {
            "path": snapshot["path"],
            "manifest_sha256": snapshot["manifest_sha256"],
            "required_modules": {
                name: _snapshot_file_hash(snapshot["manifest"], f"tracen_replay/{name}")
                for name in ("vision.py", "training_result_layout.py", "current_state_layout.py")
            },
        },
        "discovery": discovery_meta,
        "discovery_source_roots": sorted({str(path) for path in source_roots}),
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "deferred_count": len(candidates) - len(selected),
        "max_frames": max_frames,
        "ocr_executed": False,
        "analysis_job_executed": False,
        "accepted_report_values_loaded": False,
        "sidecar_conflicts": [],
        "image_bound_sidecar_count": image_bound_sidecar_count,
        "image_bound_sidecar_examples": image_bound_sidecar_examples,
        "rows": rows,
    }


def refresh(
    snapshot: Path,
    snapshot_manifest_sha256: str,
    target_root: Path,
    recording: str,
    discovery_paths: Iterable[Path],
    *,
    source_video: Path | None = None,
    max_frames: int | None = None,
    reader: Any | None = None,
    execute: bool = False,
    model_dir: Path | None = None,
    audit_name: str = "discovered-base-reading-refresh-v1.json",
    refresh_choice_dependencies: bool = False,
) -> dict[str, Any]:
    """Preflight or execute a source-bound discovered base refresh.

    execute=False performs every binding and sidecar check but makes no
    filesystem changes and never instantiates a model. execute=True is
    the only mode that calls NeuralReader.read and replaces selected JSON
    rows in the new clone.  ``refresh_choice_dependencies`` additionally
    permits only registered ``choice-refinement`` conflicts and recomputes
    those selected companions from the refreshed OCR lines and unchanged
    gameplay evidence.
    """

    snapshot_info = load_snapshot(snapshot, snapshot_manifest_sha256)
    target = _load_target(Path(target_root))
    candidates, discovery_meta, source_roots = _load_candidates(
        discovery_paths,
        recording=recording,
        snapshot=snapshot_info,
        target=target,
    )
    if max_frames is not None:
        if type(max_frames) is not int or max_frames <= 0:
            _fail("invalid_limit", "max_frames must be a positive integer")
        selected = candidates[:max_frames]
    else:
        selected = candidates
    if not selected:
        _fail("no_selected_candidates", "No discovered candidates were selected")
    audit_path = target.root / audit_name
    _safe_relative(audit_name, "audit name")
    if audit_path.exists() or audit_path.is_symlink():
        _fail("output_exists", f"Refresh audit already exists: {audit_path}")
    progress_path = _progress_journal_path(target.root, audit_name)
    if progress_path.exists() or progress_path.is_symlink():
        _fail("output_exists", f"Refresh progress journal already exists: {progress_path}")
    conflicts, image_bound_count, image_bound_examples = _sidecar_dependencies(
        target.root, selected
    )
    choice_dependencies = None
    if refresh_choice_dependencies:
        choice_dependencies = _choice_dependency_plan(
            target.root,
            selected,
            conflicts,
            audit_name=audit_name,
        )
    if conflicts and choice_dependencies is None:
        _fail(
            "sidecar_conflict",
            "Selected base rows have registered companions bound to their old raw rows; refresh those companions in the new clone first.",
            details=conflicts,
        )
    plan = _plan(
        snapshot=snapshot_info,
        target=target,
        recording=recording,
        candidates=candidates,
        selected=selected,
        discovery_meta=discovery_meta,
        source_roots=source_roots,
        source_video=Path(source_video).resolve() if source_video is not None else None,
        max_frames=max_frames,
        image_bound_sidecar_count=image_bound_count,
        image_bound_sidecar_examples=image_bound_examples,
    )
    if choice_dependencies is None:
        plan["choice_dependency_refresh"] = {
            "requested": False,
            "mode": None,
            "frames": [],
            "sidecars": [],
        }
    else:
        plan["choice_dependency_refresh"] = {
            "requested": True,
            "mode": "choice-refinement-v1",
            "frames": sorted(choice_dependencies["entries"]),
            "sidecars": [
                {
                    "frame_id": frame_id,
                    "path": str(record["sidecar_path"]),
                    "before_sha256": record["sidecar_before_sha256"],
                }
                for frame_id, record in sorted(choice_dependencies["entries"].items())
            ],
            "manifest_path": str(choice_dependencies["manifest_path"]),
            "manifest_before_sha256": choice_dependencies["manifest_before_sha256"],
            "archive_root": str(choice_dependencies["archive_root"]),
            "conflict_count": len(choice_dependencies["conflicts"]),
        }
    plan["progress_journal_path"] = str(progress_path)
    plan["progress_journal_sha256"] = None
    if not execute:
        return plan
    if reader is None:
        files_root = Path(snapshot_info["path"]) / "files"
        if str(files_root) not in sys.path:
            sys.path.insert(0, str(files_root))
        for loaded_name in list(sys.modules):
            if loaded_name == "tracen_replay" or loaded_name.startswith("tracen_replay."):
                _fail(
                    "frozen_import_conflict",
                    "tracen_replay was already imported before frozen reader load",
                )
        try:
            from tracen_replay.vision import NeuralReader
        except ImportError as exc:
            _fail("frozen_reader_unavailable", f"Could not import frozen NeuralReader: {exc}")
        reader = NeuralReader(model_dir=model_dir) if model_dir is not None else NeuralReader()
        reader_module = sys.modules.get("tracen_replay.vision")
        if (
            reader_module is None
            or not _is_within(Path(reader_module.__file__).resolve(), files_root.resolve())
        ):
            _fail("mutable_reader_import", "NeuralReader did not load from the frozen snapshot")
    else:
        # Test/integration callers may inject a deterministic reader, but a
        # reader class exposed by tracen_replay still has to come from the
        # requested frozen snapshot.  This prevents the library API from
        # becoming an accidental mutable-import bypass.
        module_name = getattr(type(reader), "__module__", "")
        if isinstance(module_name, str) and module_name == "tracen_replay.vision":
            files_root = Path(snapshot_info["path"]) / "files"
            reader_module = sys.modules.get(module_name)
            if (
                reader_module is None
                or not getattr(reader_module, "__file__", None)
                or not _is_within(Path(reader_module.__file__).resolve(), files_root.resolve())
            ):
                _fail("mutable_reader_import", "Injected reader did not load from the frozen snapshot")
    for candidate in selected:
        if candidate.target_raw_path is None or _digest(candidate.target_raw_path) != candidate.old_raw_sha256:
            _fail("target_changed", f"Target raw changed before refresh: {candidate.frame_id}")
        if candidate.target_source_frame_path is None or _source_frame_hash(candidate.target_source_frame_path) != candidate.old_source_frame_sha256:
            _fail("target_changed", f"Target source frame changed before refresh: {candidate.frame_id}")
    refreshed: list[tuple[Candidate, dict[str, Any], str]] = []
    for candidate in selected:
        raw, gameplay_sha = _reader_raw(reader, candidate)
        refreshed.append((candidate, raw, gameplay_sha))
    refreshed_by_frame = {
        candidate.frame_id: (candidate, raw, gameplay_sha)
        for candidate, raw, gameplay_sha in refreshed
    }
    choice_payloads: dict[str, dict[str, Any]] = {}
    choice_observer_sha256 = None
    updated_manifest = None
    archive_records: list[dict[str, str]] = []
    if choice_dependencies is not None and choice_dependencies["entries"]:
        for frame_id in choice_dependencies["entries"]:
            refreshed_row = refreshed_by_frame.get(frame_id)
            if refreshed_row is None:
                _fail("internal_error", f"Choice dependency frame was not refreshed: {frame_id}")
            payload, observer_sha256 = _choice_sidecar_payload(
                refreshed_row[0], refreshed_row[1], snapshot_info
            )
            choice_payloads[frame_id] = payload
            if choice_observer_sha256 is None:
                choice_observer_sha256 = observer_sha256
            elif choice_observer_sha256 != observer_sha256:
                _fail("choice_refresh_unavailable", "Choice observer changed during refresh")
        updated_manifest = _updated_choice_manifest(
            choice_dependencies,
            refreshed_by_frame,
            choice_payloads,
        )
        if _digest(choice_dependencies["manifest_path"]) != choice_dependencies["manifest_before_sha256"]:
            _fail("target_changed", "Target replay manifest changed before choice refresh")
        for frame_id, record in choice_dependencies["entries"].items():
            if _digest(record["sidecar_path"]) != record["sidecar_before_sha256"]:
                _fail("target_changed", f"Choice sidecar changed before refresh: {frame_id}")
    for candidate, _raw, _gameplay_sha in refreshed:
        if candidate.target_raw_path is None or _digest(candidate.target_raw_path) != candidate.old_raw_sha256:
            _fail("target_changed", f"Target raw changed before persistence: {candidate.frame_id}")
    journal = {
        "schema_version": PROGRESS_SCHEMA,
        "status": "started",
        "recording": recording,
        "target_root": str(target.root),
        "audit_path": str(audit_path),
        "snapshot_manifest_sha256": snapshot_info["manifest_sha256"],
        "selected_frames": [
            {
                "frame_id": candidate.frame_id,
                "path": str(candidate.target_raw_path),
                "source_path": str(candidate.old_raw_path),
                "old_sha256": candidate.old_raw_sha256,
                "status": "pending",
            }
            for candidate in selected
        ],
        "choice_sidecars": [
            {
                "frame_id": frame_id,
                "path": str(record["sidecar_path"]),
                "old_sha256": record["sidecar_before_sha256"],
                "status": "pending",
            }
            for frame_id, record in sorted((choice_dependencies or {}).get("entries", {}).items())
        ],
        "manifest": (
            {
                "path": str(choice_dependencies["manifest_path"]),
                "before_sha256": choice_dependencies["manifest_before_sha256"],
                "status": "pending",
            }
            if choice_dependencies is not None and choice_dependencies["entries"]
            else None
        ),
        "archive": {"status": "pending", "records": []},
        "writes_completed": [],
        "failure": None,
    }
    journal_sha256 = _create_progress_journal(progress_path, journal, target.root)

    def persist_journal() -> None:
        nonlocal journal_sha256
        journal_sha256 = _write_json_replacement(
            progress_path,
            journal,
            target.root,
            "refresh progress journal",
        )

    try:
        if choice_dependencies is not None and choice_dependencies["entries"]:
            journal["archive"]["status"] = "writing"
            persist_journal()
            archive_records = _archive_choice_dependencies(choice_dependencies)
            journal["archive"] = {"status": "completed", "records": archive_records}
            persist_journal()
        else:
            journal["archive"] = {"status": "not_applicable", "records": []}
            persist_journal()

        for candidate, raw, gameplay_sha in refreshed:
            journal_row = next(
                item for item in journal["selected_frames"] if item["frame_id"] == candidate.frame_id
            )
            journal_row["status"] = "writing"
            persist_journal()
            new_sha = _write_replacement(candidate.target_raw_path, raw, target.root)
            journal_row.update({"status": "written", "new_sha256": new_sha})
            journal["writes_completed"].append(
                {
                    "kind": "neural_raw",
                    "frame_id": candidate.frame_id,
                    "path": str(candidate.target_raw_path),
                    "sha256": new_sha,
                }
            )
            persist_journal()
            row = next(item for item in plan["rows"] if item["frame_id"] == candidate.frame_id)
            row.update(
                {
                    "status": "refreshed",
                    "new_raw_sha256": new_sha,
                    "new_raw_identity_fingerprints": _raw_identity_fingerprints(raw),
                    "new_engine_fingerprint": raw.get("engine_fingerprint"),
                    "new_model_sha256": raw.get("model_sha256"),
                    "new_gameplay_sha256": gameplay_sha,
                }
            )

        if choice_dependencies is not None and choice_dependencies["entries"]:
            for frame_id, payload in choice_payloads.items():
                record = choice_dependencies["entries"][frame_id]
                journal_sidecar = next(
                    item for item in journal["choice_sidecars"] if item["frame_id"] == frame_id
                )
                journal_sidecar["status"] = "writing"
                persist_journal()
                new_sha = _write_json_replacement(
                    record["sidecar_path"],
                    payload,
                    target.root,
                    f"choice-refinement sidecar {frame_id}",
                )
                journal_sidecar.update({"status": "written", "new_sha256": new_sha})
                journal["writes_completed"].append(
                    {
                        "kind": "choice_refinement_sidecar",
                        "frame_id": frame_id,
                        "path": str(record["sidecar_path"]),
                        "sha256": new_sha,
                    }
                )
                persist_journal()
                row = next(item for item in plan["rows"] if item["frame_id"] == frame_id)
                row["choice_dependency"] = {
                    "status": "refreshed",
                    "path": str(record["sidecar_path"]),
                    "new_sidecar_sha256": new_sha,
                    "new_raw_sha256": payload["raw_sha256"],
                    "observer_sha256": choice_observer_sha256,
                }
            if updated_manifest is None:
                _fail("internal_error", "Choice manifest update was not prepared")
            journal["manifest"]["status"] = "writing"
            persist_journal()
            manifest_sha256 = _write_json_replacement(
                choice_dependencies["manifest_path"],
                updated_manifest,
                target.root,
                "updated replay manifest",
            )
            journal["manifest"].update({"status": "written", "after_sha256": manifest_sha256})
            journal["writes_completed"].append(
                {
                    "kind": "replay_input_manifest",
                    "path": str(choice_dependencies["manifest_path"]),
                    "sha256": manifest_sha256,
                }
            )
            persist_journal()
            plan["choice_dependency_refresh"].update(
                {
                    "refreshed_count": len(choice_payloads),
                    "observer_sha256": choice_observer_sha256,
                    "manifest_after_sha256": manifest_sha256,
                    "archive_records": archive_records,
                }
            )
        elif choice_dependencies is not None:
            plan["choice_dependency_refresh"].update(
                {
                    "refreshed_count": 0,
                    "observer_sha256": None,
                    "manifest_after_sha256": choice_dependencies["manifest_before_sha256"],
                    "archive_records": [],
                }
            )

        plan["ocr_executed"] = True
        plan["reader_fingerprint"] = getattr(reader, "fingerprint", None)
        plan["reader_models"] = getattr(reader, "models", None)
        plan["refreshed_count"] = len(refreshed)
        plan["audit_path"] = str(audit_path)
        plan["audit_sha256"] = None
        journal["status"] = "completed"
        persist_journal()
        plan["progress_journal_sha256"] = journal_sha256
        try:
            with audit_path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(plan, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        except (OSError, TypeError, ValueError, OverflowError) as exc:
            _fail("write_failed", f"Could not persist refresh audit: {audit_path}: {exc}")
        plan["audit_sha256"] = _digest(audit_path)
        return plan
    except Exception as exc:  # noqa: BLE001 - persist a bounded failure record
        journal["status"] = "failed"
        journal["failure"] = _progress_failure(exc)
        for collection_name in ("selected_frames", "choice_sidecars"):
            for item in journal[collection_name]:
                if item.get("status") == "writing":
                    item["status"] = "failed"
        try:
            persist_journal()
        except Exception:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument(
        "--snapshot-manifest-sha256",
        required=True,
        help="Expected SHA-256 of the frozen snapshot code-manifest.json",
    )
    parser.add_argument("--root", type=Path, required=True, help="Fresh cloned worker root")
    parser.add_argument("--recording", required=True)
    parser.add_argument("--discovery", type=Path, action="append", required=True)
    parser.add_argument("--source", type=Path, help="Optional source video for full source-hash verification")
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"))
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--audit-name", default="discovered-base-reading-refresh-v1.json")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run the frozen reader and replace selected clone JSON rows",
    )
    parser.add_argument(
        "--refresh-choice-dependencies",
        action="store_true",
        help="Regenerate only registered choice-refinement companions for selected rows",
    )
    args = parser.parse_args(argv)
    try:
        expected_hash = args.snapshot_manifest_sha256
        result = refresh(
            args.snapshot,
            expected_hash,
            args.root,
            args.recording,
            args.discovery,
            source_video=args.source,
            max_frames=args.max_frames,
            execute=args.execute,
            model_dir=args.model_dir,
            audit_name=args.audit_name,
            refresh_choice_dependencies=args.refresh_choice_dependencies,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except RefreshError as exc:
        error = {"schema_version": SCHEMA, "error": exc.code, "message": str(exc)}
        if exc.details is not None:
            error["details"] = exc.details
        print(json.dumps(error, ensure_ascii=False, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())





