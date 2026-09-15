"""Prepare small, isolated cached-replay roots for the final worker runs.

The three preserved recording caches are large because they contain decoded
video frames.  A final manifest replay only reads the source-bound paths named
by the manifest and a small set of conventional sidecar caches.  This script
materializes that closure in a disposable root, hardlinking only image files
whose role is an immutable replay input and copying JSON/HTML and generated
proof images independently.  It refuses source or destination reparse
points, unequal path collisions, and an existing preparation directory.

Preparation performs source/hash/manifest validation and a read-only race
window load.  It never starts ``analysis_job`` or OCR.  The generated report
contains the exact deferred commands for those runs.

Gameplay receipt proof overlays are included as adjacent source-bound
sidecars when they exist.  They are consumed by the receipt occlusion loader,
so leaving them out of a disposable worker root can turn a readable gameplay
receipt into an occluded line before the normal parser or repeated-text
recovery sees it.

Selected training/native/receipt inspection crops use the same adjacent
receipt overlay contract.  Their overlays are added only from selected
inspection rows, never by scanning an auxiliary panel or the cache tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tracen_replay.replay_inputs import SCHEMA, ReplayInputError, load_manifest


PREPARATION_SCHEMA = "tracen-replay/final-worker-input-preparation-v1"
CONCERT_PROOF_REPAIR_SCHEMA = "tracen-replay/final-worker-concert-proof-repair-v1"
INSPECTION_RECEIPT_OVERLAY_REPAIR_SCHEMA = (
    "tracen-replay/final-worker-inspection-receipt-overlay-repair-v1"
)
G2_FREEZE_SHA256 = "f2ce7c137f7dcd54ae45179d9bc5b3f965835e0a95e17738474909d6b29c6f68"
G2_FREEZE_PATH = REPOSITORY_ROOT / ".local/final-reliability-v1/reference-freeze-v2.json"
DEFAULT_OUTPUT = REPOSITORY_ROOT / ".local/final-reliability-v1/worker-runs/post-recognition-g8-v1-prepared"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


RECORDINGS: dict[str, dict[str, Any]] = {
    "v1": {
        "source_video": Path(r"C:\Users\andy-\Videos\2026-09-08 01-59-24.mp4"),
        "source_sha256": "a75008eb095a2d37f9d0b49d09f1a54be8285bd9ef69e243037440a580193174",
        "cache_root": REPOSITORY_ROOT / ".local/full-recording/v1",
        "base_folder": "",
        "manifest": REPOSITORY_ROOT / ".local/final-reliability-v1/replay-inputs/v1-replay-input-manifest.json",
    },
    "independent-01": {
        "source_video": Path(r"C:\Users\andy-\Videos\2026-09-08 12-21-21.mp4"),
        "source_sha256": "24e000837fa4bba40d4e9e7c1c7d6121300a51d166424ea7bf5cfea24f521c18",
        "cache_root": REPOSITORY_ROOT / ".local/full-recording/independent-01",
        "base_folder": "",
        "manifest": REPOSITORY_ROOT / ".local/final-reliability-v1/replay-inputs/independent-01-replay-input-manifest.json",
    },
    "independent-02": {
        "source_video": Path(r"C:\Users\andy-\Videos\2026-09-09 20-52-07.mp4"),
        "source_sha256": "a10bd8261176e2aa79eb991ab0eb6b4b23dc4c8a6ffe13edcb799d43e8982313",
        "cache_root": REPOSITORY_ROOT / ".local/full-recording/independent-02",
        "base_folder": "initial-baseline",
        "manifest": REPOSITORY_ROOT / ".local/final-reliability-v1/replay-inputs/independent-02-replay-input-manifest.json",
    },
}


STATUS_SIDECAR = {
    "source": REPOSITORY_ROOT
    / ".local/final-reliability-v1/status-badge-source/independent-02-t029-opening-mood/status-badge-refinement/part-005-frame-000077.json",
    "target": "status-badge-source/independent-02-t029-opening-mood/status-badge-refinement/part-005-frame-000077.json",
    "folder": "status-badge-source/independent-02-t029-opening-mood/status-badge-refinement",
    "raw_path": "initial-baseline/neural/part-005-frame-000077.json",
    "evidence_path": "initial-baseline/gameplay/part-005-frame-000077.png",
    "source_timestamp_ms": 619000,
}

RACE_VERDICT = REPOSITORY_ROOT / ".local/final-reliability-v1/race-quantity-external/independent-02-t029-v3-verdict.json"
RACE_STAGING = REPOSITORY_ROOT / ".local/final-reliability-v1/race-quantity-external/independent-02-t029-v3"
RACE_TARGET_DIRECTORY = "race-reward-inspection/642000-646000-60"

# Source-reviewed auxiliary sidecars are staged as independent writable JSON
# copies.  Their values are never read from an accepted report: each entry is
# bound to the worker-local raw observation, gameplay proof, and decoded source
# frame by the common loader.  Keep this registry explicit so a new artifact
# cannot silently become an input merely by appearing in the cache tree.
AUXILIARY_SIDECARS: dict[str, dict[str, list[tuple[Path, str]]]] = {
    "v1": {
        "weak_state_recovery": [
            (REPOSITORY_ROOT / ".local/final-reliability-v1/weak-state-recovery/v1-t018-dance-merged.json", "part-002-frame-000050.json"),
            (REPOSITORY_ROOT / ".local/final-reliability-v1/weak-state-recovery/v1-t028-performance-anchors.json", "part-004-frame-000039.json"),
            (REPOSITORY_ROOT / ".local/final-reliability-v1/weak-state-recovery/v1-t068-performance-geometry.json", "part-011-frame-000328.json"),
        ],
    },
    "independent-01": {
        "weak_state_recovery": [
            (REPOSITORY_ROOT / ".local/final-reliability-v1/weak-state-recovery/independent-01-t063-before-performance.json", "part-011-frame-000093.json"),
            (REPOSITORY_ROOT / ".local/final-reliability-v1/weak-state-recovery/independent-01-t063-training-success.json", "part-011-frame-000114.json"),
        ],
        "numeric_cap_refinement": [
            (REPOSITORY_ROOT / ".local/final-reliability-v1/numeric-cap-refinements-v1/independent-01/part-006-frame-000261.json", "part-006-frame-000261.json"),
            (REPOSITORY_ROOT / ".local/final-reliability-v1/numeric-cap-refinements-v1/independent-01/part-011-frame-000093.json", "part-011-frame-000093.json"),
        ],
    },
    "independent-02": {
        "weak_state_recovery": [
            (REPOSITORY_ROOT / ".local/final-reliability-v1/weak-state-recovery/independent-02-t019-speed.json", "part-003-frame-000164.json"),
        ],
    },
}

# ``cached_readings`` opens these sidecar paths whenever a JSON artifact is
# present.  We therefore copy their complete directories (including proof
# images) independently, while the source/evidence images named by the base
# manifest are hardlinked below.
FIXED_SIDECAR_DIRECTORIES = (
    "outcome-refinement",
    "currency-refinement",
    "currency-padding-refinement",
    "skill-points-refinement",
    "skill-variants",
    "song-symbols",
    "song-symbol-refinement",
    "song-star-refinement",
    "concert-panel-refinement",
    "base-receipt-refinement",
    "race-identity-refinement",
    "performance-panel-refinement",
    "status-badge-refinement",
    "inventory-refinement",
    "lesson-offer-refinement",
    "choice-refinement",
    "choice-card-refinement",
)

# This directory contains aggregate source-bound artifacts.  A normal cached
# reading can load a per-frame file from it and validate all observation proof
# images, so its complete directory is retained when present.
FULL_CACHE_DIRECTORIES = ("race-quantity-refinement",)
OPTIONAL_CACHE_FILES = (
    "hint-card-recovery.json",
    "choice-inspection.json",
    "race-reward-inspection.json",
)
OPTIONAL_CACHE_DIRECTORIES = ("choice-inspection", "race-reward-inspection")

# ``inspect_training.reparse_inspection`` derives these sibling files from a
# selected inspection raw record.  Keep the family set shared by fresh
# closure preparation and bounded repair so a new refinement is included by
# its loader contract rather than by a filename-specific allowlist.
INSPECTION_REFINEMENT_SUFFIXES = ("totals", "contrast", "performance", "awards", "receipt")
INSPECTION_SOURCE_REFINEMENT_KEY = "training_gain_source_refinement_sidecars"
INSPECTION_LOCALIZED_SIDECAR_KEY = "training_badge_localization_sidecars"
INSPECTION_REFINEMENT_REPAIR_SCHEMA = (
    "tracen-replay/final-worker-input-inspection-refinement-repair-v1"
)


class PreparationError(ValueError):
    """A deterministic preparation failure with a machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise PreparationError(code, message)


def _safe_relative(value: str | Path, field: str) -> str:
    text = value.as_posix() if isinstance(value, Path) else value
    if not isinstance(text, str) or not text or "\x00" in text:
        _fail("invalid_path", f"{field} must be a non-empty relative path.")
    normalized = text.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(text)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or windows.root
        or ".." in posix.parts
    ):
        _fail("path_outside_root", f"{field} must stay below its root.")
    return posix.as_posix()


def _join(*parts: str) -> str:
    return "/".join(part.strip("/") for part in parts if part)


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        _fail("path_unreadable", f"Could not inspect {path}: {exc}")
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


def _assert_safe_path(root: Path, path: Path, field: str) -> None:
    root = root.resolve()
    try:
        # Inspect the lexical components before resolving so an in-root
        # symlink/junction cannot disappear into its resolved destination.
        relative = path.absolute().relative_to(root.absolute())
    except (OSError, RuntimeError, ValueError) as exc:
        _fail("path_outside_root", f"{field} leaves {root}: {path}")
    cursor = root
    for component in relative.parts:
        cursor /= component
        if cursor.exists() and _is_reparse(cursor):
            _fail("reparse_point", f"{field} contains a reparse point: {cursor}")
    try:
        path.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail("path_outside_root", f"{field} resolves outside {root}: {path}")


def _iter_files(root: Path) -> Iterable[Path]:
    """Walk a tree without following links, rejecting every reparse point."""

    root = root.resolve()
    if not root.is_dir():
        _fail("missing_directory", f"Directory is missing: {root}")
    if _is_reparse(root):
        _fail("reparse_point", f"Directory is a reparse point: {root}")
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
        except OSError as exc:
            _fail("directory_unreadable", f"Could not enumerate {directory}: {exc}")
        for entry in entries:
            path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                _fail("path_unreadable", f"Could not inspect {path}: {exc}")
            if entry.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT):
                _fail("reparse_point", f"Tree contains a reparse point: {path}")
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif entry.is_file(follow_symlinks=False):
                yield path
            else:
                _fail("unsupported_entry", f"Tree contains a non-file entry: {path}")


class DigestCache:
    def __init__(self) -> None:
        self.values: dict[Path, str] = {}

    def digest(self, path: Path) -> str:
        path = path.resolve()
        if path not in self.values:
            digest = hashlib.sha256()
            try:
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                _fail("file_unreadable", f"Could not hash {path}: {exc}")
            self.values[path] = digest.hexdigest()
        return self.values[path]


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("invalid_json", f"Could not read JSON {path}: {exc}")


def _copy_writable(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target)
        os.chmod(target, target.stat().st_mode | stat.S_IWRITE)
    except OSError as exc:
        _fail("copy_failed", f"Could not copy {source} to {target}: {exc}")


def _ensure_destination_parent(root: Path, relative: str) -> Path:
    root = root.resolve()
    parts = PurePosixPath(relative).parts
    cursor = root
    for component in parts[:-1]:
        cursor /= component
        if cursor.exists():
            if _is_reparse(cursor):
                _fail("reparse_point", f"Destination parent is a reparse point: {cursor}")
            if not cursor.is_dir():
                _fail("path_collision", f"Destination parent is a file: {cursor}")
        else:
            try:
                cursor.mkdir()
            except OSError as exc:
                _fail("mkdir_failed", f"Could not create {cursor}: {exc}")
    return root / Path(relative)


def _empty_stats() -> dict[str, int]:
    return {
        "files": 0,
        "bytes": 0,
        "copied_files": 0,
        "copied_bytes": 0,
        "hardlinked_files": 0,
        "hardlinked_bytes": 0,
        "equal_collisions": 0,
    }


def _copy_one(
    source: Path,
    target_root: Path,
    target_relative: str,
    *,
    source_root: Path,
    hardlink: bool,
    digests: DigestCache,
    stats: dict[str, int],
) -> None:
    """Copy one safe file, with independent JSON/HTML output by default."""

    target_relative = _safe_relative(target_relative, "destination path")
    _assert_safe_path(source_root, source, "source path")
    if not source.is_file():
        _fail("missing_file", f"Source file is missing: {source}")
    target = _ensure_destination_parent(target_root, target_relative)
    if target.exists():
        if _is_reparse(target):
            _fail("reparse_point", f"Destination file is a reparse point: {target}")
        if not target.is_file():
            _fail("path_collision", f"Destination file collides with a directory: {target}")
        incoming = digests.digest(source)
        existing = digests.digest(target)
        if incoming != existing:
            _fail("content_collision", f"Unequal source content collides at {target_relative}")
        stats["files"] += 1
        stats["bytes"] += source.stat().st_size
        stats["equal_collisions"] += 1
        return

    source_hash = digests.digest(source)
    source_size = source.stat().st_size
    try:
        if hardlink:
            # A hardlink failure is an error.  Falling back to a copy would
            # silently change the audited layout and can consume many GB.
            os.link(source, target)
            stats["hardlinked_files"] += 1
            stats["hardlinked_bytes"] += source_size
        else:
            _copy_writable(source, target)
            if digests.digest(target) != source_hash:
                _fail("copy_hash_mismatch", f"Copied content differs at {target_relative}")
            stats["copied_files"] += 1
            stats["copied_bytes"] += source_size
    except OSError as exc:
        _fail("materialize_failed", f"Could not materialize {source} at {target}: {exc}")
    stats["files"] += 1
    stats["bytes"] += source_size


def _copy_selected(
    source_root: Path,
    target_root: Path,
    relative_paths: Iterable[str],
    immutable_paths: set[str],
    *,
    digests: DigestCache,
    stats: dict[str, int],
) -> None:
    source_root = source_root.resolve()
    for relative in sorted(set(relative_paths)):
        relative = _safe_relative(relative, "selected source path")
        source = source_root / Path(relative)
        _copy_one(
            source,
            target_root,
            relative,
            source_root=source_root,
            hardlink=relative in immutable_paths and Path(relative).suffix.casefold() in IMAGE_SUFFIXES,
            digests=digests,
            stats=stats,
        )


def _copy_directory(
    source_root: Path,
    source_directory: Path,
    target_root: Path,
    target_prefix: str,
    *,
    immutable_predicate: Callable[[str], bool] | None,
    digests: DigestCache,
    stats: dict[str, int],
) -> int:
    """Copy all files below one directory, preserving its relative layout."""

    source_root = source_root.resolve()
    source_directory = source_directory.resolve()
    try:
        source_relative = source_directory.relative_to(source_root).as_posix()
    except ValueError:
        _fail("path_outside_root", f"Directory is outside its source root: {source_directory}")
    count = 0
    for source in sorted(_iter_files(source_directory), key=lambda path: path.as_posix().casefold()):
        inside = source.relative_to(source_directory).as_posix()
        destination_relative = _join(target_prefix, inside)
        source_relative_file = _join(source_relative, inside)
        _copy_one(
            source,
            target_root,
            destination_relative,
            source_root=source_root,
            hardlink=bool(immutable_predicate and immutable_predicate(inside)),
            digests=digests,
            stats=stats,
        )
        count += 1
    return count


def _add_path(selected: set[str], immutable: set[str], path: str, *, image_input: bool = False) -> None:
    path = _safe_relative(path, "manifest path")
    selected.add(path)
    if image_input and Path(path).suffix.casefold() in IMAGE_SUFFIXES:
        immutable.add(path)


def _receipt_overlay_path(path: str) -> str | None:
    """Return the adjacent receipt proof sidecar for a gameplay image.

    Receipt overlays are source-bound annotations, not a general cache-tree
    glob.  Restricting this to an image whose immediate parent is the
    gameplay crop directory keeps auxiliary profile/trainee panels out of the
    worker closure while allowing every manifest or proof gameplay image to
    bring along its validated ``.overlay.json`` when present.
    """

    normalized = PurePosixPath(path)
    if (
        len(normalized.parts) < 2
        or normalized.parts[-2].casefold() != "gameplay"
        or normalized.suffix.casefold() not in IMAGE_SUFFIXES
    ):
        return None
    return normalized.with_suffix(".overlay.json").as_posix()


def _inspection_receipt_overlay_path(path: str) -> str | None:
    """Return the receipt overlay derived from a selected inspection crop."""

    normalized = PurePosixPath(path)
    if normalized.suffix.casefold() not in IMAGE_SUFFIXES:
        return None
    return normalized.with_suffix(".overlay.json").as_posix()


def _inspection_refinement_path(raw_path: str, suffix: str) -> str | None:
    """Return one loader-derived refinement sibling for an inspection raw path."""

    normalized = PurePosixPath(raw_path)
    if normalized.suffix.casefold() != ".json" or suffix not in INSPECTION_REFINEMENT_SUFFIXES:
        return None
    return normalized.with_suffix(f".{suffix}.json").as_posix()


def _inspection_refinement_companions_for_row(
    row: dict[str, Any], source_root: Path
) -> list[str]:
    """Return existing loader-derived companion paths for one row."""

    raw_path = row.get("raw")
    if not isinstance(raw_path, str):
        return []
    raw_path = _safe_relative(raw_path, "inspection raw path")
    result: list[str] = []
    for suffix in INSPECTION_REFINEMENT_SUFFIXES:
        companion = _inspection_refinement_path(raw_path, suffix)
        if companion is not None and (Path(source_root) / Path(companion)).is_file():
            result.append(companion)
    return result


def _inspection_refinement_companions(
    normalized: dict[str, Any], source_root: Path
) -> dict[str, dict[str, Any]]:
    """Index existing source-bound inspection refinements by their path.

    The inspection loader derives each companion from the selected raw path,
    so this walks only normalized inspection/recovery rows.  It never globs
    the cache tree and therefore cannot import an unrelated artifact merely
    because it happens to use one of the companion suffixes.
    """

    source_root = Path(source_root).resolve()
    result: dict[str, dict[str, Any]] = {}
    for group in [*normalized.get("inspections", []), *normalized.get("recovery", [])]:
        for row in group.get("rows", []):
            raw_path = row.get("raw")
            if not isinstance(raw_path, str):
                continue
            raw_path = _safe_relative(raw_path, "inspection raw path")
            for companion in _inspection_refinement_companions_for_row(
                row, source_root
            ):
                suffix = next(
                    item
                    for item in INSPECTION_REFINEMENT_SUFFIXES
                    if companion == _inspection_refinement_path(raw_path, item)
                )
                result.setdefault(
                    companion,
                    {
                        "path": companion,
                        "suffix": suffix,
                        "raw": raw_path,
                        "row": row,
                    },
                )
    return result


def _inspection_source_refinement_companions(
    normalized: dict[str, Any], source_root: Path
) -> dict[str, dict[str, Any]]:
    """Index explicitly registered expanded training-gain envelopes.

    These envelopes are not adjacent to the inspection raw JSON, so deriving
    a sibling filename would miss them.  They are accepted only when the
    selected normalized group contains the exact timestamp/evidence identity
    named by the inspection manifest.  The source-bound inspection loader
    performs the full raw/frame/pixel validation later; this closure step
    merely materializes the declared envelope file and never reads its
    amount fields.
    """

    source_root = Path(source_root).resolve()
    allowed_keys = frozenset(
        {
            "path",
            "sidecar_sha256",
            "source_timestamp_ms",
            "evidence",
            "raw_path",
            "source_frame",
            "source_frame_id",
        }
    )
    result: dict[str, dict[str, Any]] = {}
    for group in [*normalized.get("inspections", []), *normalized.get("recovery", [])]:
        manifest_relative = group.get("manifest")
        if not isinstance(manifest_relative, str):
            continue
        manifest_path = source_root / Path(manifest_relative)
        if not manifest_path.is_file():
            fallback = source_root / Path(_join(group.get("folder", ""), manifest_relative))
            if fallback.is_file():
                manifest_path = fallback
        if not manifest_path.is_file():
            continue
        payload = _load_json(manifest_path)
        entries = payload.get(INSPECTION_SOURCE_REFINEMENT_KEY)
        if entries is None:
            continue
        if not isinstance(entries, list):
            _fail(
                "inspection_source_refinement_invalid",
                f"{INSPECTION_SOURCE_REFINEMENT_KEY} must be an array: {manifest_path}",
            )
        selected: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for row in group.get("rows", []):
            if not isinstance(row, dict):
                continue
            timestamp = row.get("source_timestamp_ms")
            evidence = row.get("evidence")
            if (
                type(timestamp) is int
                and timestamp >= 0
                and isinstance(evidence, str)
                and evidence.strip()
            ):
                selected.setdefault((timestamp, evidence), []).append(row)
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                _fail(
                    "inspection_source_refinement_invalid",
                    f"Source refinement entry {index} is not an object: {manifest_path}",
                )
            unknown = set(entry) - allowed_keys
            if unknown:
                _fail(
                    "inspection_source_refinement_invalid",
                    f"Source refinement entry {index} has unsupported keys: {manifest_path}",
                )
            path = entry.get("path")
            timestamp = entry.get("source_timestamp_ms")
            evidence = entry.get("evidence")
            if (
                not isinstance(path, str)
                or not isinstance(timestamp, int)
                or isinstance(timestamp, bool)
                or timestamp < 0
                or not isinstance(evidence, str)
                or not evidence.strip()
            ):
                _fail(
                    "inspection_source_refinement_invalid",
                    f"Source refinement entry {index} has incomplete identity: {manifest_path}",
                )
            relative = _safe_relative(path, "inspection source refinement path")
            source = source_root / Path(relative)
            _assert_safe_path(source_root, source, "inspection source refinement path")
            if not source.is_file():
                _fail("missing_file", f"Inspection source refinement is missing: {source}")
            matching_rows = selected.get((timestamp, evidence), [])
            if len(matching_rows) != 1:
                _fail(
                    "inspection_source_refinement_unselected",
                    f"Source refinement entry {index} is not bound to one unique selected inspection row: {manifest_path}",
                )
            row = matching_rows[0]

            # Keep the explicit proof identities in the closure.  The loader
            # derives these relationships again from the source row, so a
            # malformed registration must fail before a worker clone is made.
            raw_value = entry.get("raw_path", row.get("raw"))
            frame_value = entry.get("source_frame", row.get("source_frame"))
            row_raw = row.get("raw")
            row_frame = row.get("source_frame")
            if (
                not isinstance(raw_value, str)
                or not raw_value
                or not isinstance(frame_value, str)
                or not frame_value
                or not isinstance(row_raw, str)
                or not isinstance(row_frame, str)
            ):
                _fail(
                    "inspection_source_refinement_invalid",
                    f"Source refinement entry {index} has incomplete proof paths: {manifest_path}",
                )
            relative_raw = _safe_relative(raw_value, "inspection source refinement raw path")
            relative_evidence = _safe_relative(
                evidence, "inspection source refinement evidence path"
            )
            relative_frame = _safe_relative(
                frame_value, "inspection source refinement source frame path"
            )
            normalized_row_raw = _safe_relative(row_raw, "inspection source refinement row raw path")
            normalized_row_frame = _safe_relative(
                row_frame, "inspection source refinement row source frame path"
            )
            if relative_raw != normalized_row_raw or relative_frame != normalized_row_frame:
                _fail(
                    "inspection_source_refinement_path_mismatch",
                    f"Source refinement entry {index} proof paths disagree with its selected row: {manifest_path}",
                )
            for proof_relative, field in (
                (relative, "inspection source refinement path"),
                (relative_raw, "inspection source refinement raw path"),
                (relative_evidence, "inspection source refinement evidence path"),
                (relative_frame, "inspection source refinement source frame path"),
            ):
                proof_path = source_root / Path(proof_relative)
                _assert_safe_path(source_root, proof_path, field)
                if not proof_path.is_file():
                    _fail("missing_file", f"Inspection source refinement proof is missing: {proof_path}")
            frame_manifest = (source_root / Path(relative_evidence)).parent / "frames.json"
            frame_manifest_relative = frame_manifest.relative_to(source_root).as_posix()
            _assert_safe_path(
                source_root, frame_manifest, "inspection source refinement frame manifest"
            )
            if not frame_manifest.is_file():
                _fail(
                    "missing_file",
                    f"Inspection source refinement frame manifest is missing: {frame_manifest}",
                )
            existing = result.get(relative)
            descriptor = {
                "path": relative,
                "raw": relative_raw,
                "evidence": relative_evidence,
                "source_frame": relative_frame,
                "frames": frame_manifest_relative,
                "source_timestamp_ms": timestamp,
                "row": row,
                "manifest": manifest_relative,
            }
            if existing is not None and (
                existing.get("source_timestamp_ms") != timestamp
                or existing.get("evidence") != evidence
            ):
                _fail(
                    "inspection_source_refinement_conflict",
                    f"Source refinement path is registered for conflicting rows: {relative}",
                )
            result[relative] = descriptor
    return result


def _inspection_localized_sidecar_companions(
    normalized: dict[str, Any], source_root: Path
) -> dict[str, dict[str, Any]]:
    """Index explicitly registered localized training badge sidecars.

    Localized badge files live in their own cache namespace and are therefore
    invisible to the row-derived inspection companion rules.  Their manifest
    registration is the source-bound bridge: every entry must name one
    selected timestamp/evidence identity, and every referenced sidecar, raw
    record, gameplay crop, source frame, and frame manifest is retained in the
    closure.  Amounts or badge values are deliberately never inspected here;
    ``inspect_training._localized_sidecar_entries`` performs the complete
    pixel/raw/frame validation when the prepared root is consumed.
    """

    source_root = Path(source_root).resolve()
    result: dict[str, dict[str, Any]] = {}
    registered_identities: dict[str, tuple[int, str]] = {}
    allowed_keys = frozenset(
        {
            "path",
            "sidecar_sha256",
            "source_timestamp_ms",
            "evidence",
            "raw_path",
            "source_frame",
            "source_frame_id",
        }
    )
    for group in [*normalized.get("inspections", []), *normalized.get("recovery", [])]:
        manifest_relative = group.get("manifest")
        if not isinstance(manifest_relative, str):
            continue
        manifest_path = source_root / Path(manifest_relative)
        if not manifest_path.is_file():
            fallback = source_root / Path(_join(group.get("folder", ""), manifest_relative))
            if fallback.is_file():
                manifest_path = fallback
        if not manifest_path.is_file():
            continue
        payload = _load_json(manifest_path)
        entries = payload.get(INSPECTION_LOCALIZED_SIDECAR_KEY)
        if entries is None:
            continue
        if not isinstance(entries, list):
            _fail(
                "inspection_localized_sidecar_invalid",
                f"{INSPECTION_LOCALIZED_SIDECAR_KEY} must be an array: {manifest_path}",
            )

        selected_by_identity: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for row in group.get("rows", []):
            if not isinstance(row, dict):
                continue
            timestamp = row.get("source_timestamp_ms")
            evidence = row.get("evidence")
            if type(timestamp) is int and timestamp >= 0 and isinstance(evidence, str):
                selected_by_identity.setdefault((timestamp, evidence), []).append(row)

        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                _fail(
                    "inspection_localized_sidecar_invalid",
                    f"Localized sidecar entry {index} is not an object: {manifest_path}",
                )
            unknown = set(entry) - allowed_keys
            if unknown:
                _fail(
                    "inspection_localized_sidecar_invalid",
                    f"Localized sidecar entry {index} has unsupported keys: {manifest_path}",
                )
            path_value = entry.get("path")
            timestamp = entry.get("source_timestamp_ms")
            evidence_value = entry.get("evidence")
            if (
                not isinstance(path_value, str)
                or not isinstance(timestamp, int)
                or isinstance(timestamp, bool)
                or timestamp < 0
                or not isinstance(evidence_value, str)
                or not evidence_value.strip()
            ):
                _fail(
                    "inspection_localized_sidecar_invalid",
                    f"Localized sidecar entry {index} has incomplete identity: {manifest_path}",
                )
            identity = (timestamp, evidence_value)
            matching_rows = selected_by_identity.get(identity, [])
            if len(matching_rows) != 1:
                _fail(
                    "inspection_localized_sidecar_unselected",
                    f"Localized sidecar entry {index} is not bound to one selected inspection row: {manifest_path}",
                )
            row = matching_rows[0]
            relative = _safe_relative(path_value, "inspection localized sidecar path")
            source = source_root / Path(relative)
            _assert_safe_path(source_root, source, "inspection localized sidecar path")
            if not source.is_file():
                _fail("missing_file", f"Inspection localized sidecar is missing: {source}")
            declared_hash = entry.get("sidecar_sha256")
            if (
                not isinstance(declared_hash, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", declared_hash)
                or hashlib.sha256(source.read_bytes()).hexdigest() != declared_hash.casefold()
            ):
                _fail(
                    "inspection_localized_sidecar_hash_mismatch",
                    f"Inspection localized sidecar hash does not match its registration: {source}",
                )
            previous_identity = registered_identities.get(relative)
            if previous_identity is not None:
                if previous_identity != identity:
                    _fail(
                        "inspection_localized_sidecar_conflict",
                        f"Inspection localized sidecar is registered for conflicting rows: {relative}",
                    )
                _fail(
                    "inspection_localized_sidecar_duplicate",
                    f"Inspection localized sidecar is registered more than once: {relative}",
                )

            # Keep all explicitly named proof paths.  The normalized row also
            # carries these paths, but using the registration fields here
            # catches a closure that would otherwise omit a sidecar's proof.
            raw_value = entry.get("raw_path", row.get("raw"))
            frame_value = entry.get("source_frame", row.get("source_frame"))
            if not isinstance(raw_value, str) or not raw_value:
                _fail(
                    "inspection_localized_sidecar_invalid",
                    f"Localized sidecar entry {index} has no raw path: {manifest_path}",
                )
            if not isinstance(frame_value, str) or not frame_value:
                _fail(
                    "inspection_localized_sidecar_invalid",
                    f"Localized sidecar entry {index} has no source frame path: {manifest_path}",
                )
            raw_relative = _safe_relative(raw_value, "inspection localized raw path")
            frame_relative = _safe_relative(frame_value, "inspection localized source frame path")
            evidence_relative = _safe_relative(evidence_value, "inspection localized evidence path")
            for proof_relative, image_input in (
                (relative, False),
                (raw_relative, False),
                (evidence_relative, True),
                (frame_relative, True),
            ):
                proof_path = source_root / Path(proof_relative)
                _assert_safe_path(source_root, proof_path, "inspection localized proof path")
                if not proof_path.is_file():
                    _fail("missing_file", f"Inspection localized proof is missing: {proof_path}")
                # Store the path on the descriptor for callers that need to
                # audit the closure; _collect_closure adds it below.

            frame_path = source_root / Path(frame_relative)
            frame_manifest = frame_path.parent.parent / "frames.json"
            frame_manifest_relative = frame_manifest.relative_to(source_root).as_posix()
            if not frame_manifest.is_file():
                _fail("missing_file", f"Inspection localized frame manifest is missing: {frame_manifest}")

            registered_identities[relative] = identity
            result[relative] = {
                "path": relative,
                "raw": raw_relative,
                "evidence": evidence_relative,
                "source_frame": frame_relative,
                "frames": frame_manifest_relative,
                "source_timestamp_ms": timestamp,
                "row": row,
                "manifest": manifest_relative,
            }
    return result


def _inspection_receipt_overlay_descriptors(
    normalized: dict[str, Any], source_root: Path
) -> dict[str, dict[str, Any]]:
    """Index overlays derived from selected inspection evidence paths.

    ``inspect_training.reparse_inspection`` eventually calls
    ``receipt_occlusion.annotate_path`` with each row's evidence crop.  The
    overlay path is derived from that crop, so only rows already selected by
    the replay manifest may introduce an overlay.  This keeps the closure
    source-bound and avoids importing arbitrary cache or auxiliary-panel
    annotations.
    """

    source_root = Path(source_root).resolve()
    result: dict[str, dict[str, Any]] = {}
    for group in [*normalized.get("inspections", []), *normalized.get("recovery", [])]:
        for row in group.get("rows", []):
            evidence = row.get("evidence")
            raw = row.get("raw")
            if not isinstance(evidence, str) or not isinstance(raw, str):
                continue
            evidence = _safe_relative(evidence, "inspection evidence path")
            raw = _safe_relative(raw, "inspection raw path")
            overlay = _inspection_receipt_overlay_path(evidence)
            if overlay is None or not (source_root / Path(overlay)).is_file():
                continue
            result.setdefault(
                overlay,
                {
                    "path": overlay,
                    "raw": raw,
                    "evidence": evidence,
                    "row": row,
                },
            )
    return result


def _canonical_json_fingerprint(value: Any) -> str:
    """Match the refinement modules' canonical raw JSON fingerprint."""

    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


_CONCERT_PROOF_FIELDS = (
    "evidence",
    "source_frame_evidence",
    "source_manifest_evidence",
    "panel_raw_evidence",
)


def _concert_panel_proof_paths(source_root: Path, base_folder: str = "") -> list[tuple[str, bool]]:
    """Return source files named by conventional Concert Info sidecars.

    The fixed sidecar directory itself is copied by the conventional-cache
    closure, but Concert Info observations also point at source gameplay
    crops, decoded frames, a sibling PTS manifest, and the original panel OCR
    JSON.  Those nested proofs are the actual inputs consumed by
    ``concert_panel_refinement.apply`` and therefore belong in the worker
    closure too.  Paths are interpreted below the recording's declared base
    namespace, exactly as the cached-reading loader interprets them.

    Only the direct sidecar files are inspected.  Archived artifacts are not
    loaded by ``cached_readings`` and cannot silently add proof inputs.
    """

    source_root = Path(source_root).resolve()
    base_root = source_root / Path(base_folder)
    directory = base_root / "concert-panel-refinement"
    if not directory.is_dir():
        return []
    if _is_reparse(directory):
        _fail("reparse_point", f"Concert panel sidecar directory is a reparse point: {directory}")
    paths: dict[str, bool] = {}
    for sidecar in sorted(directory.glob("*.json"), key=lambda path: path.name.casefold()):
        if _is_reparse(sidecar):
            _fail("reparse_point", f"Concert panel sidecar is a reparse point: {sidecar}")
        payload = _load_json(sidecar)
        if not isinstance(payload, dict):
            _fail("invalid_sidecar", f"Concert panel sidecar is not an object: {sidecar}")
        observations = payload.get("observations", [])
        if not isinstance(observations, list):
            _fail("invalid_sidecar", f"Concert panel observations are not a list: {sidecar}")
        for index, observation in enumerate(observations):
            if not isinstance(observation, dict):
                _fail(
                    "invalid_sidecar",
                    f"Concert panel observation {index} is not an object: {sidecar}",
                )
            for field in _CONCERT_PROOF_FIELDS:
                value = observation.get(field)
                if value is None:
                    continue
                if not isinstance(value, str) or not value:
                    _fail(
                        "invalid_sidecar",
                        f"Concert panel {field} path is invalid: {sidecar}",
                    )
                relative = _join(base_folder, value)
                relative = _safe_relative(relative, f"Concert panel {field} path")
                # The source frame is an immutable decoded input.  Gameplay
                # crops are generated proof artifacts and must remain
                # independent writable copies in the worker root.
                paths[relative] = (
                    field == "source_frame_evidence"
                    and Path(relative).suffix.casefold() in IMAGE_SUFFIXES
                )
    return sorted(paths.items(), key=lambda item: item[0].casefold())


def _collect_closure(normalized: dict[str, Any], source_root: Path) -> tuple[set[str], set[str], dict[str, Any]]:
    """Collect exactly the manifest and conventional cache paths the loader reads."""

    source_root = Path(source_root).resolve()
    selected: set[str] = set()
    immutable: set[str] = set()
    inspection_companions = _inspection_refinement_companions(normalized, source_root)
    inspection_source_refinements = _inspection_source_refinement_companions(
        normalized, source_root
    )
    inspection_localized_sidecars = _inspection_localized_sidecar_companions(
        normalized, source_root
    )
    inspection_receipt_overlays = _inspection_receipt_overlay_descriptors(
        normalized, source_root
    )
    companions_by_raw: dict[str, list[str]] = {}
    for companion in inspection_companions.values():
        companions_by_raw.setdefault(companion["raw"], []).append(companion["path"])
    for companion in inspection_source_refinements.values():
        _add_path(selected, immutable, companion["path"])
        _add_path(selected, immutable, companion["raw"])
        _add_path(selected, immutable, companion["evidence"], image_input=True)
        _add_path(selected, immutable, companion["source_frame"], image_input=True)
        _add_path(selected, immutable, companion["frames"])
    for companion in inspection_localized_sidecars.values():
        _add_path(selected, immutable, companion["path"])
        _add_path(selected, immutable, companion["raw"])
        _add_path(selected, immutable, companion["evidence"], image_input=True)
        _add_path(selected, immutable, companion["source_frame"], image_input=True)
        _add_path(selected, immutable, companion["frames"])
    base = normalized["base"]
    _add_path(selected, immutable, base["capture"])
    for frame in base["frames"]:
        _add_path(selected, immutable, frame["evidence"], image_input=True)
        _add_path(selected, immutable, frame["raw"])
        _add_path(selected, immutable, frame["raw_evidence"], image_input=True)

    for group in [*normalized.get("inspections", []), *normalized.get("recovery", [])]:
        manifest_relative = group["manifest"]
        # Normalized recovery groups retain the root-relative manifest path
        # (for example ``numeric-receipt-recovery/receipt-inspection.json``)
        # even though ``folder`` is also retained for dispatch.  Do not add
        # that folder a second time.
        if not (source_root / Path(manifest_relative)).is_file():
            manifest_relative = _join(group.get("folder", ""), manifest_relative)
        _add_path(selected, immutable, manifest_relative)
        for row in group.get("rows", []):
            for key in ("evidence", "raw_evidence", "source_frame"):
                _add_path(selected, immutable, row[key], image_input=True)
            _add_path(selected, immutable, row["raw"])
            for companion in companions_by_raw.get(row.get("raw"), []):
                _add_path(selected, immutable, companion)
            # ``_frame_manifest`` reads the sibling frames.json for each
            # inspection source frame.  Keep that file even though it is not
            # returned in normalized rows.
            source_frame = PurePosixPath(row["source_frame"])
            # The source frame lives below ``<window>/frames`` while the
            # inspection frame manifest lives beside that directory at
            # ``<window>/frames.json`` (the path used by replay_inputs).
            _add_path(selected, immutable, (source_frame.parent.parent / "frames.json").as_posix())

    # Generic occluded receipt replay validates its mutable plan against both
    # the recording and the selected inspection manifest before promoting any
    # effects.  Keep that plan beside the registered recovery namespace so a
    # prepared clone can replay the exact source-bound windows without OCR.
    for group in normalized.get("recovery", []):
        if group.get("kind") != "occluded_receipt":
            continue
        plan_path = _join(group.get("folder", ""), "last-plan.json")
        _add_path(selected, immutable, plan_path)

    for supplement in normalized.get("supplements", []):
        for entry in supplement.get("entries", []):
            _add_path(selected, immutable, entry["path"])
            _add_path(selected, immutable, entry["raw_path"])
            _add_path(selected, immutable, entry["evidence_path"], image_input=True)

    # A nested base (independent-02) still needs the top-level capture for the
    # independently registered race windows.  It is a source manifest file,
    # not a report value.
    top_capture = source_root / "capture.json"
    if top_capture.is_file():
        _add_path(selected, immutable, "capture.json")

    base_folder = base.get("folder", "")
    conventional: dict[str, Any] = {"fixed_sidecars": [], "full_directories": [], "optional_files": [], "optional_directories": [], "concert_panel_proof_paths": [], "receipt_overlay_sidecars": [], "inspection_receipt_overlay_sidecars": [], "inspection_refinement_companions": [], "inspection_source_refinement_sidecars": [], "inspection_localized_sidecars": []}
    conventional["inspection_receipt_overlay_sidecars"] = sorted(
        inspection_receipt_overlays
    )
    conventional["inspection_refinement_companions"] = sorted(inspection_companions)
    conventional["inspection_source_refinement_sidecars"] = sorted(
        inspection_source_refinements
    )
    conventional["inspection_localized_sidecars"] = sorted(
        inspection_localized_sidecars
    )
    for folder in FIXED_SIDECAR_DIRECTORIES:
        relative_folder = _join(base_folder, folder)
        directory = source_root / Path(relative_folder)
        if not directory.is_dir():
            continue
        conventional["fixed_sidecars"].append(relative_folder)
        for path in _iter_files(directory):
            _add_path(selected, immutable, path.relative_to(source_root).as_posix())

    # The Concert Info sidecar is conventional, but each accepted observation
    # carries four additional proof paths.  Materialize them before copying
    # so the worker's actual apply path can be validated during preparation.
    for relative, image_input in _concert_panel_proof_paths(source_root, base_folder):
        _add_path(selected, immutable, relative, image_input=image_input)
        conventional["concert_panel_proof_paths"].append(relative)
    conventional["concert_panel_proof_paths"] = sorted(
        set(conventional["concert_panel_proof_paths"])
    )

    for folder in FULL_CACHE_DIRECTORIES:
        relative_folder = _join(base_folder, folder)
        directory = source_root / Path(relative_folder)
        if not directory.is_dir():
            continue
        conventional["full_directories"].append(relative_folder)
        for path in _iter_files(directory):
            _add_path(selected, immutable, path.relative_to(source_root).as_posix())

    for relative_file in OPTIONAL_CACHE_FILES:
        path = source_root / Path(relative_file)
        if path.is_file():
            _add_path(selected, immutable, relative_file)
            conventional["optional_files"].append(relative_file)

    # Existing bounded race windows are registered by a small manifest at the
    # cache root, but their directories are deliberately named per capture
    # (for example ``race-reward-native-probe-...``) rather than placed below
    # ``race-reward-inspection``.  Preserve those namespaces too.  Only the
    # decoded source frames are eligible for hardlinks; neural JSON, capture
    # metadata, quantity refinements, and gameplay proof images remain
    # independent writable copies.
    race_manifest = source_root / "race-reward-inspection.json"
    if race_manifest.is_file():
        race_payload = _load_json(race_manifest)
        windows = race_payload.get("windows") if isinstance(race_payload, dict) else None
        if not isinstance(windows, list):
            _fail("invalid_race_manifest", f"Race inspection manifest has no windows list: {race_manifest}")
        for index, window in enumerate(windows):
            if not isinstance(window, dict):
                _fail("invalid_race_manifest", f"Race inspection window {index} is not an object.")
            directory_relative = _safe_relative(window.get("directory", ""), "race inspection directory")
            directory = source_root / Path(directory_relative)
            if not directory.is_dir():
                _fail("missing_directory", f"Race inspection directory is missing: {directory}")
            conventional.setdefault("race_window_directories", []).append(directory_relative)
            for path in _iter_files(directory):
                relative = path.relative_to(source_root).as_posix()
                inside = path.relative_to(directory)
                immutable_input = bool(inside.parts and inside.parts[0].casefold() == "frames")
                _add_path(selected, immutable, relative, image_input=immutable_input)

    for relative_folder in OPTIONAL_CACHE_DIRECTORIES:
        directory = source_root / Path(relative_folder)
        if not directory.is_dir():
            continue
        conventional["optional_directories"].append(relative_folder)
        for path in _iter_files(directory):
            _add_path(selected, immutable, path.relative_to(source_root).as_posix())

    # ``receipt_occlusion.annotate_path`` reads a sibling overlay beside the
    # gameplay crop.  Keep this closure source-bound and gameplay-only: the
    # overlay is copied as writable proof metadata, while the image itself
    # remains the immutable replay input.  Do not scan for arbitrary overlays
    # or include panels from the auxiliary Career Profile/Trainee log.
    for relative in tuple(sorted(selected)):
        overlay = _receipt_overlay_path(relative)
        if overlay is None or not (source_root / Path(overlay)).is_file():
            continue
        _add_path(selected, immutable, overlay)
        conventional["receipt_overlay_sidecars"].append(overlay)
    conventional["receipt_overlay_sidecars"] = sorted(
        set(conventional["receipt_overlay_sidecars"])
    )
    for relative in sorted(inspection_receipt_overlays):
        _add_path(selected, immutable, relative)

    return selected, immutable, conventional


def _verify_nested_namespace(source_root: Path, base_folder: str, selected: set[str]) -> dict[str, int]:
    """Prove independent-02's nested base is represented without flattening it."""

    if not base_folder:
        return {"checked": 0, "same_resolved_path": 0, "same_content": 0}
    nested = source_root / Path(base_folder)
    if not nested.is_dir():
        _fail("missing_directory", f"Nested source namespace is missing: {nested}")
    checked = same_resolved = same_content = 0
    digests = DigestCache()
    prefix = base_folder.strip("/") + "/"
    for relative in sorted(selected):
        if not relative.startswith(prefix):
            continue
        nested_relative = relative[len(prefix) :]
        source_path = source_root / Path(relative)
        alias_path = nested / Path(nested_relative)
        _assert_safe_path(source_root, source_path, "nested source namespace")
        _assert_safe_path(nested, alias_path, "nested source alias")
        if not alias_path.is_file():
            _fail("missing_file", f"Nested source alias is missing: {alias_path}")
        checked += 1
        if source_path.resolve() == alias_path.resolve():
            same_resolved += 1
        elif digests.digest(source_path) != digests.digest(alias_path):
            _fail("namespace_mismatch", f"Nested source alias differs for {relative}")
        else:
            same_content += 1
    # The root capture is an explicit legacy alias used by race window load.
    root_capture = source_root / "capture.json"
    nested_capture = nested / "capture.json"
    if not root_capture.is_file() or not nested_capture.is_file():
        _fail("missing_file", "Independent-02 requires both top-level and nested capture.json.")
    if digests.digest(root_capture) != digests.digest(nested_capture):
        _fail("namespace_mismatch", "Independent-02 top-level and nested capture.json differ.")
    return {"checked": checked, "same_resolved_path": same_resolved, "same_content": same_content}


def _patch_status_manifest(manifest: dict[str, Any], source: Path, target_root: Path) -> dict[str, Any]:
    if any(
        entry.get("kind") == "raw_sidecars" and entry.get("name") == "status_badge_refinement"
        for entry in manifest.get("supplements", [])
        if isinstance(entry, dict)
    ):
        _fail("duplicate_supplement", "Status badge refinement is already registered in the manifest.")
    sidecar = _load_json(source)
    if not isinstance(sidecar, dict):
        _fail("invalid_json", "Status badge sidecar must be a JSON object.")
    sidecar_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    raw_sha256 = sidecar.get("raw_sha256")
    evidence_sha256 = sidecar.get("evidence_sha256")
    if not isinstance(raw_sha256, str) or not isinstance(evidence_sha256, str):
        _fail("invalid_sidecar", "Status badge sidecar lacks raw/evidence hashes.")
    updated = json.loads(json.dumps(manifest))
    updated.setdefault("supplements", []).append(
        {
            "kind": "raw_sidecars",
            "name": "status_badge_refinement",
            "folder": STATUS_SIDECAR["folder"],
            "entries": [
                {
                    "path": Path(STATUS_SIDECAR["target"]).name,
                    "sidecar_sha256": sidecar_sha256,
                    "raw_path": STATUS_SIDECAR["raw_path"],
                    "raw_sha256": raw_sha256,
                    "evidence_path": STATUS_SIDECAR["evidence_path"],
                    "evidence_sha256": evidence_sha256,
                    "source_timestamp_ms": STATUS_SIDECAR["source_timestamp_ms"],
                }
            ],
        }
    )
    return updated


def _write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError:
        _fail("output_exists", f"Refusing to overwrite {path}")
    except OSError as exc:
        _fail("write_failed", f"Could not write {path}: {exc}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _overwrite_json(path: Path, payload: Any) -> str:
    """Rewrite disposable metadata through a temporary sibling only."""

    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        temporary.replace(path)
    except FileExistsError:
        _fail("output_exists", f"Refusing to reuse temporary metadata path: {temporary}")
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        _fail("write_failed", f"Could not update {path}: {exc}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_frame_candidate(target_root: Path, sidecar: dict[str, Any], base_folder: str) -> Path:
    declared = sidecar.get("source_frame_evidence")
    expected = sidecar.get("source_frame_sha256")
    if not isinstance(declared, str) or not isinstance(expected, str):
        _fail("invalid_sidecar", "Auxiliary sidecar lacks source-frame provenance.")
    parts = list(PurePosixPath(declared.replace("\\", "/")).parts)
    candidates: list[Path] = []
    base_root = target_root / Path(base_folder)
    for index in range(len(parts)):
        suffix = Path(*parts[index:])
        candidates.extend((base_root / suffix, target_root / suffix))
    digest = DigestCache()
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            candidate.relative_to(target_root.resolve())
        except ValueError:
            continue
        if candidate.is_file() and digest.digest(candidate) == expected:
            return candidate
    _fail("missing_sidecar_source_frame", f"Auxiliary source-frame proof is missing: {declared}")


def _auxiliary_entry(
    source: Path,
    target_root: Path,
    base_folder: str,
    *,
    folder_name: str,
    target_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Copy one source-reviewed sidecar and return its manifest entry/audit."""

    if not source.is_file():
        _fail("missing_file", f"Auxiliary sidecar is missing: {source}")
    payload = _load_json(source)
    if not isinstance(payload, dict):
        _fail("invalid_sidecar", f"Auxiliary sidecar is not an object: {source}")
    timestamp = payload.get("source_timestamp_ms")
    if type(timestamp) is not int or timestamp < 0:
        _fail("invalid_sidecar", f"Auxiliary sidecar timestamp is invalid: {source}")
    source_frame = _source_frame_candidate(target_root, payload, base_folder)
    base_root = target_root / Path(base_folder)
    frame_id = payload.get("source_frame_id")
    if not isinstance(frame_id, str) or not SAFE_NAME.fullmatch(frame_id):
        _fail("invalid_sidecar", f"Auxiliary sidecar frame identity is invalid: {source}")
    raw_path = base_root / "neural" / f"{frame_id}.json"
    evidence_rel = payload.get("evidence")
    if not isinstance(evidence_rel, str):
        _fail("invalid_sidecar", f"Auxiliary sidecar evidence path is invalid: {source}")
    evidence_path = (base_root / Path(evidence_rel)).resolve()
    try:
        evidence_path.relative_to(base_root.resolve())
    except ValueError:
        _fail("path_outside_root", f"Auxiliary sidecar evidence leaves the worker base: {source}")
    if not raw_path.is_file() or not evidence_path.is_file():
        _fail("missing_sidecar_source", f"Auxiliary sidecar source files are missing: {source}")
    raw = _load_json(raw_path)
    if not isinstance(raw, dict) or raw.get("source_timestamp_ms") != timestamp:
        _fail("timestamp_mismatch", f"Auxiliary sidecar timestamp does not match raw input: {source}")
    raw_digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    if payload.get("raw_sha256") != raw_digest:
        _fail("hash_mismatch", f"Auxiliary sidecar raw hash does not match worker input: {source}")
    evidence_digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    if payload.get("evidence_sha256") != evidence_digest:
        _fail("hash_mismatch", f"Auxiliary sidecar gameplay hash does not match worker input: {source}")
    target_relative = _join(base_folder, folder_name, target_name)
    stats = _empty_stats()
    _copy_one(
        source,
        target_root,
        target_relative,
        source_root=source.parent,
        hardlink=False,
        digests=DigestCache(),
        stats=stats,
    )
    entry = {
        "path": target_name,
        "sidecar_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "raw_path": _join(base_folder, "neural", f"{frame_id}.json"),
        "raw_sha256": raw_digest,
        "evidence_path": _join(base_folder, evidence_rel),
        "evidence_sha256": evidence_digest,
        "source_timestamp_ms": timestamp,
    }
    audit = {
        "source": str(source),
        "target": target_relative,
        "source_sha256": entry["sidecar_sha256"],
        "raw_path": entry["raw_path"],
        "evidence_path": entry["evidence_path"],
        "source_frame_path": str(source_frame.relative_to(target_root).as_posix()),
        "materialization": stats,
    }
    return entry, audit


def verified_source_freeze() -> dict[str, Any]:
    """Bind preparation to the actual frozen references before copying inputs."""
    from scripts.freeze_final_reliability import verify_manifest

    try:
        manifest = verify_manifest(
            G2_FREEZE_PATH, expected_manifest_sha256=G2_FREEZE_SHA256
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _fail("source_freeze_invalid", f"Source reference freeze failed validation: {exc}")
    return {
        "g2_freeze_path": str(G2_FREEZE_PATH),
        "g2_freeze_sha256": G2_FREEZE_SHA256,
        "g2_freeze_schema": manifest["schema_version"],
        "g2_freeze_verified_files": len(manifest["immutable_files_sha256"]),
    }


def augment(output: Path, names: list[str]) -> dict[str, Any]:
    """Stage reviewed auxiliary sidecars into existing disposable roots."""

    output = output.resolve()
    if not output.is_dir():
        _fail("missing_directory", f"Prepared worker root is missing: {output}")
    source_freeze = verified_source_freeze()
    report: dict[str, Any] = {
        "schema_version": "tracen-replay/final-worker-input-augmentation-v1",
        **source_freeze,
        "base_preparation_root": str(output),
        "ocr_executed": False,
        "analysis_job_executed": False,
        "accepted_report_values_loaded": False,
        "recordings": [],
    }
    for name in names:
        if name not in RECORDINGS or name not in AUXILIARY_SIDECARS:
            _fail("unknown_recording", f"No auxiliary sidecar registry for {name}.")
        worker_root = output / name
        manifest_path = worker_root / "replay-input-manifest.json"
        if not worker_root.is_dir() or not manifest_path.is_file():
            _fail("missing_manifest", f"Prepared worker manifest is missing: {manifest_path}")
        previous_path = worker_root / "replay-input-manifest.previous-v1.json"
        if previous_path.exists():
            _fail("output_exists", f"Refusing to replace preserved manifest: {previous_path}")
        _copy_writable(manifest_path, previous_path)
        original_hash = hashlib.sha256(previous_path.read_bytes()).hexdigest()
        manifest = _load_json(manifest_path)
        if not isinstance(manifest, dict):
            _fail("invalid_manifest", f"Worker manifest is not an object: {manifest_path}")
        groups = []
        audits = []
        base_folder = RECORDINGS[name]["base_folder"]
        for group_name, records in AUXILIARY_SIDECARS[name].items():
            entries = []
            for source, target_name in records:
                entry, audit = _auxiliary_entry(
                    source,
                    worker_root,
                    base_folder,
                    folder_name=group_name.replace("_", "-"),
                    target_name=target_name,
                )
                entries.append(entry)
                audits.append(dict(group=group_name, **audit))
            groups.append({
                "kind": "raw_sidecars",
                "name": group_name,
                "folder": _join(base_folder, group_name.replace("_", "-")),
                "entries": entries,
            })
        existing_names = {
            item.get("name") for item in manifest.get("supplements", [])
            if isinstance(item, dict) and item.get("kind") == "raw_sidecars"
        }
        duplicate = [group["name"] for group in groups if group["name"] in existing_names]
        if duplicate:
            _fail("duplicate_supplement", f"Auxiliary sidecar groups already registered: {duplicate}")
        manifest.setdefault("supplements", []).extend(groups)
        new_hash = _overwrite_json(manifest_path, manifest)
        try:
            normalized = load_manifest(
                manifest_path,
                worker_root,
                expected_source_sha256=RECORDINGS[name]["source_sha256"],
            )
        except ReplayInputError as exc:
            _fail("augmented_manifest_invalid", f"Augmented manifest failed validation ({exc.code}): {exc}")
        report["recordings"].append({
            "recording": name,
            "worker_root": str(worker_root),
            "manifest": str(manifest_path),
            "previous_manifest": str(previous_path),
            "previous_manifest_sha256": original_hash,
            "manifest_sha256": new_hash,
            "registered_groups": [group["name"] for group in groups],
            "entries": audits,
            "validated_supplement_entries": sum(len(group.get("entries", [])) for group in normalized.get("supplements", [])),
        })
    report_path = output / "auxiliary-sidecar-augmentation-v1.json"
    report["report_sha256"] = _write_json(report_path, report)
    markdown_path = output / "auxiliary-sidecar-augmentation-v1.md"
    lines = [
        "# Auxiliary source-sidecar augmentation",
        "",
        "This updates disposable worker manifests only. Canonical caches, source videos, and the prior worker manifests are preserved.",
        "",
    ]
    for item in report["recordings"]:
        lines.extend([
            f"## `{item['recording']}`",
            "",
            f"- Current manifest: `{item['manifest']}` (SHA-256 `{item['manifest_sha256']}`)",
            f"- Preserved previous manifest: `{item['previous_manifest']}` (SHA-256 `{item['previous_manifest_sha256']}`)",
            f"- Registered groups: {', '.join(item['registered_groups'])}",
            f"- Entries validated by replay_inputs: {item['validated_supplement_entries']}",
            "",
        ])
    markdown_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    report["markdown_report"] = str(markdown_path)
    return report


def _verify_race_source(expected_source: str) -> dict[str, Any]:
    verdict = _load_json(RACE_VERDICT)
    if not isinstance(verdict, dict) or verdict.get("schema_version") != "tracen-replay/final-race-quantity-external-verdict-v1":
        _fail("invalid_race_verdict", "External race quantity verdict schema is invalid.")
    case = verdict.get("case")
    refinement = verdict.get("refinement")
    policy = verdict.get("policy")
    if not isinstance(case, dict) or case.get("run") != "independent-02" or case.get("run_variant") != "initial-baseline":
        _fail("race_source_mismatch", "External race verdict is not for independent-02 initial-baseline.")
    if not isinstance(refinement, dict) or refinement.get("source_recording_sha256") != expected_source:
        _fail("race_source_mismatch", "External race verdict source hash differs.")
    if not isinstance(policy, dict) or policy.get("fixed_quantity_windows") != "fixed_badge_quantity_windows_v2":
        _fail("race_policy_mismatch", "External race verdict does not use fixed quantity windows.")
    capture = RACE_STAGING / "capture.json"
    if not capture.is_file():
        _fail("missing_file", f"External race staging capture is missing: {capture}")
    capture_sha256 = hashlib.sha256(capture.read_bytes()).hexdigest()
    if refinement.get("source_capture_manifest_sha256") != capture_sha256:
        _fail("race_hash_mismatch", "External race staging capture hash differs from verdict.")
    artifact_value = refinement.get("artifact")
    artifact_path = Path(artifact_value) if isinstance(artifact_value, str) else None
    if artifact_path is not None and not artifact_path.is_absolute():
        artifact_path = REPOSITORY_ROOT / artifact_path
    if artifact_path is None or not artifact_path.is_file():
        _fail("missing_file", "External race artifact is missing.")
    artifact_sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    if refinement.get("artifact_sha256") != artifact_sha256:
        _fail("race_hash_mismatch", "External race artifact hash differs from verdict.")
    return {
        "verdict": str(RACE_VERDICT),
        "verdict_sha256": hashlib.sha256(RACE_VERDICT.read_bytes()).hexdigest(),
        "staging": str(RACE_STAGING),
        "staging_capture_sha256": capture_sha256,
        "artifact": str(artifact_path),
        "artifact_sha256": artifact_sha256,
        "fixed_quantity_windows": True,
        "turn_id": case.get("turn_id"),
        "race_frame_id": case.get("race_frame_id"),
    }


def _register_external_race(target_root: Path, expected_source: str) -> dict[str, Any]:
    race_info = _verify_race_source(expected_source)
    race_destination = target_root / Path(RACE_TARGET_DIRECTORY)
    stats = _empty_stats()
    digests = DigestCache()
    _copy_directory(
        RACE_STAGING,
        RACE_STAGING,
        target_root,
        RACE_TARGET_DIRECTORY,
        # The external window is immutable during reparse-only replay, but
        # its generated gameplay/proof images stay independent.  Hardlink
        # only the six original source frames.
        immutable_predicate=lambda rel: bool(
            re.fullmatch(r"part-[^/]+/frames/[^/]+", rel)
            and Path(rel).suffix.casefold() in IMAGE_SUFFIXES
        ),
        digests=digests,
        stats=stats,
    )
    if target_root.joinpath("race-reward-inspection.json").exists():
        _fail("duplicate_race_manifest", "Worker root already contains a race reward manifest.")
    try:
        from tracen_replay.race_reward_inspection import load as load_race_rewards
        from tracen_replay.race_reward_inspection import register as register_race_window

        register_race_window(target_root, RACE_TARGET_DIRECTORY)
        metadata, rows = load_race_rewards(target_root, expected_source)
    except (OSError, TypeError, KeyError, ValueError, ZeroDivisionError) as exc:
        _fail("race_validation_failed", f"External race window failed read-only validation: {exc}")
    race_manifest = target_root / "race-reward-inspection.json"
    if metadata is None or len(rows) != 6:
        _fail("race_validation_failed", "External race window did not validate all six source frames.")
    race_info.update(
        {
            "target_directory": RACE_TARGET_DIRECTORY,
            "target_manifest": "race-reward-inspection.json",
            "target_manifest_sha256": hashlib.sha256(race_manifest.read_bytes()).hexdigest(),
            "verified_frames": len(rows),
            "materialization": stats,
        }
    )
    return race_info


def _copy_canonical_manifest(canonical: Path, target: Path, digests: DigestCache) -> str:
    if not canonical.is_file():
        _fail("missing_manifest", f"Replay input manifest is missing: {canonical}")
    _copy_writable(canonical, target)
    actual = digests.digest(target)
    expected = digests.digest(canonical)
    if actual != expected:
        _fail("copy_hash_mismatch", f"Manifest copy differs: {target}")
    return actual


def _validate_concert_panel_sidecars(worker_root: Path, base_folder: str = "") -> dict[str, Any]:
    """Run the real Concert Info sidecar apply path before worker execution.

    ``load_manifest`` validates registered manifest paths, but conventional
    sidecars are discovered by ``cached_readings`` after that step.  Their
    nested observation proofs therefore need an explicit preflight.  This
    invokes the same ``concert_panel_refinement.apply`` used by the loader,
    against the worker-local raw record and evidence root, so missing proof
    paths, hash drift, panel identity changes, and PTS linkage failures stop
    preparation rather than surfacing during a long worker run.
    """

    worker_root = Path(worker_root).resolve()
    base_root = (worker_root / Path(base_folder)).resolve()
    directory = base_root / "concert-panel-refinement"
    if not directory.is_dir():
        return {"checked": 0, "sidecars": []}
    if _is_reparse(directory):
        _fail("reparse_point", f"Concert panel sidecar directory is a reparse point: {directory}")

    from tracen_replay.concert_panel_refinement import apply as apply_concert_panel

    checked: list[str] = []
    for sidecar_path in sorted(directory.glob("*.json"), key=lambda path: path.name.casefold()):
        if _is_reparse(sidecar_path):
            _fail("reparse_point", f"Concert panel sidecar is a reparse point: {sidecar_path}")
        raw_path = base_root / "neural" / sidecar_path.name
        if not raw_path.is_file():
            _fail(
                "missing_concert_panel_source",
                f"Concert panel sidecar has no matching worker raw record: {sidecar_path}",
            )
        raw = _load_json(raw_path)
        if not isinstance(raw, dict):
            _fail("invalid_sidecar_source", f"Concert panel raw record is not an object: {raw_path}")
        evidence_value = raw.get("evidence")
        if not isinstance(evidence_value, str) or not evidence_value:
            _fail("invalid_sidecar_source", f"Concert panel raw evidence path is invalid: {raw_path}")
        evidence_relative = _safe_relative(evidence_value, "Concert panel raw evidence path")
        evidence_path = base_root / Path(evidence_relative)
        _assert_safe_path(base_root, evidence_path, "Concert panel raw evidence path")
        if not evidence_path.is_file():
            _fail("missing_concert_panel_source", f"Concert panel gameplay evidence is missing: {evidence_path}")
        refinement = _load_json(sidecar_path)
        try:
            apply_concert_panel(
                raw,
                refinement,
                evidence_path,
                observation_root=base_root,
                original=raw,
            )
        except (OSError, TypeError, KeyError, ValueError) as exc:
            _fail(
                "concert_panel_validation_failed",
                f"Concert panel sidecar failed source-bound apply: {sidecar_path}: {exc}",
            )
        checked.append(sidecar_path.relative_to(worker_root).as_posix())
    return {"checked": len(checked), "sidecars": checked}


def repair_concert_panel_proof_closure(output: Path, names: list[str]) -> dict[str, Any]:
    """Complete missing Concert Info proof files in an existing worker root.

    This is a bounded repair for disposable preparation roots.  It reads the
    canonical sidecars and manifests, copies only their declared proof files,
    and then runs the same source-bound apply preflight used by ``prepare``.
    Existing worker manifests and preserved prior manifests are never edited.
    """

    output = output.resolve()
    if not output.is_dir():
        _fail("missing_directory", f"Prepared worker root is missing: {output}")
    source_freeze = verified_source_freeze()
    report: dict[str, Any] = {
        "schema_version": CONCERT_PROOF_REPAIR_SCHEMA,
        **source_freeze,
        "preparation_root": str(output),
        "ocr_executed": False,
        "analysis_job_executed": False,
        "manifests_modified": False,
        "recordings": [],
    }

    for name in names:
        if name not in RECORDINGS:
            _fail("unknown_recording", f"Unknown recording: {name}")
        details = RECORDINGS[name]
        worker_root = output / name
        worker_manifest = worker_root / "replay-input-manifest.json"
        previous_manifest = worker_root / "replay-input-manifest.previous-v1.json"
        if not worker_root.is_dir() or not worker_manifest.is_file():
            _fail("missing_manifest", f"Prepared worker manifest is missing: {worker_manifest}")
        if previous_manifest.exists() and not previous_manifest.is_file():
            _fail("path_collision", f"Preserved worker manifest is not a file: {previous_manifest}")

        cache_root = Path(details["cache_root"]).resolve()
        canonical_manifest = Path(details["manifest"]).resolve()
        expected_source = details["source_sha256"]
        try:
            canonical_normalized = load_manifest(
                canonical_manifest,
                cache_root,
                expected_source_sha256=expected_source,
            )
            load_manifest(
                worker_manifest,
                worker_root,
                expected_source_sha256=expected_source,
            )
        except ReplayInputError as exc:
            _fail("repair_manifest_invalid", f"Worker or canonical manifest failed validation ({exc.code}): {exc}")

        base_folder = canonical_normalized["base"]["folder"]
        proof_paths = _concert_panel_proof_paths(cache_root, base_folder)
        stats = _empty_stats()
        digests = DigestCache()
        staged: list[str] = []
        already_present: list[str] = []
        proof_files: list[dict[str, Any]] = []
        manifest_sha_before = digests.digest(worker_manifest)
        for relative, image_input in proof_paths:
            target = worker_root / Path(relative)
            existed = target.exists()
            _copy_one(
                cache_root / Path(relative),
                worker_root,
                relative,
                source_root=cache_root,
                hardlink=image_input and Path(relative).suffix.casefold() in IMAGE_SUFFIXES,
                digests=digests,
                stats=stats,
            )
            (already_present if existed else staged).append(relative)
            source = cache_root / Path(relative)
            proof_files.append(
                {
                    "path": relative,
                    "sha256": digests.digest(source),
                    "bytes": source.stat().st_size,
                    "immutable_image_input": bool(image_input),
                }
            )

        validation = _validate_concert_panel_sidecars(worker_root, base_folder)
        manifest_sha_after = digests.digest(worker_manifest)
        if manifest_sha_after != manifest_sha_before:
            _fail("manifest_modified", f"Worker manifest changed during proof repair: {worker_manifest}")
        report["recordings"].append(
            {
                "recording": name,
                "worker_root": str(worker_root),
                "worker_manifest": str(worker_manifest),
                "worker_manifest_sha256": manifest_sha_after,
                "canonical_manifest": str(canonical_manifest),
                "canonical_manifest_sha256": digests.digest(canonical_manifest),
                "base_folder": base_folder,
                "proof_files": proof_files,
                "proof_file_count": len(proof_files),
                "staged_paths": staged,
                "already_present_paths": already_present,
                "materialization": stats,
                "concert_panel_validation": validation,
            }
        )

    report_path = output / "concert-panel-proof-repair-v1.json"
    report["report_sha256"] = _write_json(report_path, report)
    markdown_path = output / "concert-panel-proof-repair-v1.md"
    if markdown_path.exists():
        _fail("output_exists", f"Refusing to overwrite {markdown_path}")
    lines = [
        "# Concert panel proof closure repair",
        "",
        "This bounded repair copied only source-declared Concert Info proof files into disposable worker roots.",
        "Worker manifests and preserved prior manifests were not modified; no OCR or analysis job was run.",
        "",
    ]
    for item in report["recordings"]:
        lines.extend(
            [
                f"## `{item['recording']}`",
                "",
                f"- Worker manifest SHA-256: `{item['worker_manifest_sha256']}`",
                f"- Proof files: {item['proof_file_count']}; newly staged: {len(item['staged_paths'])}; "
                f"already present: {len(item['already_present_paths'])}.",
                f"- Source-bound Concert Info sidecars validated: {item['concert_panel_validation']['checked']}.",
                "",
            ]
        )
    try:
        markdown_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    except OSError as exc:
        _fail("write_failed", f"Could not write {markdown_path}: {exc}")
    report["markdown_report"] = str(markdown_path)
    return report


def _validate_inspection_refinement_companion(
    root: Path,
    source_sha256: str,
    descriptor: dict[str, Any],
) -> dict[str, Any]:
    """Validate one inspection companion against its raw row and proof image."""

    root = Path(root).resolve()
    relative = _safe_relative(descriptor["path"], "inspection refinement path")
    raw_relative = _safe_relative(descriptor["raw"], "inspection refinement raw path")
    row = descriptor["row"]
    evidence_value = row.get("evidence")
    if not isinstance(evidence_value, str):
        _fail("inspection_refinement_invalid", f"Inspection row evidence is invalid: {relative}")
    evidence_relative = _safe_relative(evidence_value, "inspection refinement evidence path")
    companion_path = root / Path(relative)
    raw_path = root / Path(raw_relative)
    evidence_path = root / Path(evidence_relative)
    for path, field in (
        (companion_path, "inspection refinement path"),
        (raw_path, "inspection refinement raw path"),
        (evidence_path, "inspection refinement evidence path"),
    ):
        _assert_safe_path(root, path, field)
        if not path.is_file():
            _fail("missing_file", f"Inspection refinement source is missing: {path}")
    extra = _load_json(companion_path)
    raw = _load_json(raw_path)
    if not isinstance(extra, dict) or not isinstance(raw, dict):
        _fail("inspection_refinement_invalid", f"Inspection refinement is not an object: {companion_path}")
    if raw.get("source_sha256") != source_sha256:
        _fail("source_mismatch", f"Inspection raw source differs: {raw_path}")
    if raw.get("source_timestamp_ms") != row.get("source_timestamp_ms"):
        _fail("timestamp_mismatch", f"Inspection raw timestamp differs: {raw_path}")
    raw_evidence = raw.get("evidence")
    own_root_value = row.get("own_root", "")
    if not isinstance(own_root_value, str):
        _fail("path_mismatch", f"Inspection row own root is invalid: {raw_path}")
    own_root = _safe_relative(own_root_value, "inspection row own root") if own_root_value else ""
    if not isinstance(raw_evidence, str):
        _fail("path_mismatch", f"Inspection raw evidence differs: {raw_path}")
    raw_evidence_relative = _safe_relative(raw_evidence, "inspection raw evidence path")
    raw_evidence_path = root / Path(_join(own_root, raw_evidence_relative))
    _assert_safe_path(root, raw_evidence_path, "inspection raw evidence path")
    if raw_evidence_path.resolve() != evidence_path.resolve():
        _fail("path_mismatch", f"Inspection raw evidence differs: {raw_path}")
    proof_sha256 = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    if extra.get("evidence_sha256") != proof_sha256:
        _fail("hash_mismatch", f"Inspection refinement evidence hash differs: {companion_path}")
    raw_sha256 = _canonical_json_fingerprint(raw)
    if extra.get("raw_sha256") != raw_sha256:
        _fail("hash_mismatch", f"Inspection refinement raw hash differs: {companion_path}")
    if not isinstance(extra.get("model_sha256"), dict):
        _fail("inspection_refinement_invalid", f"Inspection refinement model provenance is missing: {companion_path}")
    return {
        "path": relative,
        "suffix": descriptor["suffix"],
        "raw": raw_relative,
        "evidence": evidence_relative,
        "source_timestamp_ms": row.get("source_timestamp_ms"),
        "sha256": hashlib.sha256(companion_path.read_bytes()).hexdigest(),
        "evidence_sha256": proof_sha256,
        "raw_sha256": raw_sha256,
    }


def _validate_inspection_refinement_with_loader(
    root: Path,
    source_sha256: str,
    descriptor: dict[str, Any],
) -> dict[str, Any]:
    """Run ``inspect_training.reparse_inspection`` for one companion sample."""

    from tracen_replay.inspect_training import reparse_inspection
    from tracen_replay.pipeline import PipelineError

    try:
        rows = reparse_inspection(
            {"source_sha256": source_sha256, "readings": [descriptor["row"]]},
            Path(root),
        )
    except (PipelineError, OSError, TypeError, KeyError, ValueError) as exc:
        _fail(
            "inspection_refinement_loader_failed",
            f"Inspection refinement loader rejected {descriptor['path']}: {exc}",
        )
    if len(rows) != 1:
        _fail(
            "inspection_refinement_loader_failed",
            f"Inspection refinement loader returned {len(rows)} rows for {descriptor['path']}.",
        )
    return {
        "path": descriptor["path"],
        "suffix": descriptor["suffix"],
        "source_timestamp_ms": descriptor["row"].get("source_timestamp_ms"),
        "status": "passed",
    }


def repair_inspection_refinement_companions(
    output: Path, names: list[str]
) -> dict[str, Any]:
    """Repair omitted inspection refinement companions in disposable roots.

    Canonical and worker manifests are validated before any copy.  Only
    loader-derived companion files for rows already selected by the canonical
    replay manifest are copied; no accepted report, manifest, or source cache
    is written.  Existing target files must be byte-identical, and one source
    and worker sample per refinement family is run through the real
    ``reparse_inspection`` path after materialization.
    """

    output = Path(output).resolve()
    if not output.is_dir():
        _fail("missing_directory", f"Prepared worker root is missing: {output}")
    source_freeze = verified_source_freeze()
    report: dict[str, Any] = {
        "schema_version": INSPECTION_REFINEMENT_REPAIR_SCHEMA,
        **source_freeze,
        "preparation_root": str(output),
        "ocr_executed": False,
        "analysis_job_executed": False,
        "accepted_report_values_loaded": False,
        "manifests_modified": False,
        "recordings": [],
    }

    for name in names:
        if name not in RECORDINGS:
            _fail("unknown_recording", f"Unknown recording: {name}")
        details = RECORDINGS[name]
        worker_root = output / name
        worker_manifest = worker_root / "replay-input-manifest.json"
        if not worker_root.is_dir() or not worker_manifest.is_file():
            _fail("missing_manifest", f"Prepared worker manifest is missing: {worker_manifest}")
        preserved_manifests = [
            path
            for path in worker_root.glob("replay-input-manifest.previous*.json")
            if path.is_file()
        ]
        manifest_paths = [worker_manifest, *preserved_manifests]
        for path in manifest_paths:
            if _is_reparse(path):
                _fail("reparse_point", f"Worker manifest is a reparse point: {path}")
        manifest_hashes_before = {
            str(path.relative_to(worker_root).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in manifest_paths
        }
        cache_root = Path(details["cache_root"]).resolve()
        canonical_manifest = Path(details["manifest"]).resolve()
        expected_source = details["source_sha256"]
        try:
            canonical_normalized = load_manifest(
                canonical_manifest,
                cache_root,
                expected_source_sha256=expected_source,
            )
            load_manifest(
                worker_manifest,
                worker_root,
                expected_source_sha256=expected_source,
            )
        except ReplayInputError as exc:
            _fail(
                "repair_manifest_invalid",
                f"Worker or canonical manifest failed validation ({exc.code}): {exc}",
            )
        companions = _inspection_refinement_companions(canonical_normalized, cache_root)
        # Validate every canonical companion before touching the worker root.
        source_validations = [
            _validate_inspection_refinement_companion(cache_root, expected_source, descriptor)
            for descriptor in sorted(companions.values(), key=lambda item: item["path"].casefold())
        ]
        digests = DigestCache()
        stats = _empty_stats()
        staged: list[str] = []
        already_present: list[str] = []
        for descriptor in sorted(companions.values(), key=lambda item: item["path"].casefold()):
            relative = descriptor["path"]
            source = cache_root / Path(relative)
            target = worker_root / Path(relative)
            _assert_safe_path(worker_root, target, "inspection refinement destination")
            if target.exists():
                if _is_reparse(target) or not target.is_file():
                    _fail("path_collision", f"Inspection refinement destination is not a file: {target}")
                if digests.digest(target) != digests.digest(source):
                    _fail("content_collision", f"Inspection refinement differs at {relative}")

        for descriptor in sorted(companions.values(), key=lambda item: item["path"].casefold()):
            relative = descriptor["path"]
            target = worker_root / Path(relative)
            existed = target.exists()
            _copy_one(
                cache_root / Path(relative),
                worker_root,
                relative,
                source_root=cache_root,
                hardlink=False,
                digests=digests,
                stats=stats,
            )
            (already_present if existed else staged).append(relative)

        # Validate the materialized companions again, then exercise one real
        # loader path per family on both source and worker roots.  This proves
        # the repair is usable without reparsing the entire recording here.
        worker_validations = [
            _validate_inspection_refinement_companion(worker_root, expected_source, descriptor)
            for descriptor in sorted(companions.values(), key=lambda item: item["path"].casefold())
        ]
        loader_samples: list[dict[str, Any]] = []
        for suffix in INSPECTION_REFINEMENT_SUFFIXES:
            family = [
                descriptor
                for descriptor in companions.values()
                if descriptor["suffix"] == suffix
            ]
            if not family:
                continue
            descriptor = sorted(family, key=lambda item: item["path"].casefold())[0]
            source_loader = _validate_inspection_refinement_with_loader(
                cache_root, expected_source, descriptor
            )
            worker_loader = _validate_inspection_refinement_with_loader(
                worker_root, expected_source, descriptor
            )
            loader_samples.append(
                {
                    "suffix": suffix,
                    "path": descriptor["path"],
                    "source": source_loader,
                    "worker": worker_loader,
                }
            )

        manifest_hashes_after = {
            str(path.relative_to(worker_root).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in manifest_paths
        }
        if manifest_hashes_after != manifest_hashes_before:
            _fail("manifest_modified", f"Worker manifest changed during inspection refinement repair: {worker_manifest}")
        report["recordings"].append(
            {
                "recording": name,
                "worker_root": str(worker_root),
                "worker_manifest": str(worker_manifest),
                "worker_manifest_sha256": manifest_hashes_after["replay-input-manifest.json"],
                "preserved_manifest_hashes": manifest_hashes_after,
                "canonical_manifest": str(canonical_manifest),
                "canonical_manifest_sha256": hashlib.sha256(canonical_manifest.read_bytes()).hexdigest(),
                "base_folder": canonical_normalized["base"].get("folder", ""),
                "companion_count": len(companions),
                "companion_counts_by_suffix": {
                    suffix: sum(1 for descriptor in companions.values() if descriptor["suffix"] == suffix)
                    for suffix in INSPECTION_REFINEMENT_SUFFIXES
                },
                "source_validated_count": len(source_validations),
                "worker_validated_count": len(worker_validations),
                "loader_samples": loader_samples,
                "staged_paths": staged,
                "already_present_paths": already_present,
                "materialization": stats,
            }
        )

    report_path = output / "inspection-refinement-companion-repair-v1.json"
    report["report_sha256"] = _write_json(report_path, report)
    markdown_path = output / "inspection-refinement-companion-repair-v1.md"
    if markdown_path.exists():
        _fail("output_exists", f"Refusing to overwrite {markdown_path}")
    lines = [
        "# Inspection refinement companion repair",
        "",
        "This bounded repair copied only loader-derived totals, contrast, performance, awards, and receipt companions for rows already selected by the replay manifests.",
        "Canonical caches, source videos, worker manifests, preserved manifests, accepted reports, OCR, and analysis jobs were not modified or run.",
        "",
    ]
    for item in report["recordings"]:
        lines.extend(
            [
                f"## `{item['recording']}`",
                "",
                f"- Worker manifest SHA-256: `{item['worker_manifest_sha256']}`",
                f"- Companions validated: {item['companion_count']} ({item['companion_counts_by_suffix']}).",
                f"- Newly staged: {len(item['staged_paths'])}; already present: {len(item['already_present_paths'])}.",
                f"- Real `reparse_inspection` loader samples passed: {len(item['loader_samples'])} families.",
                "",
            ]
        )
    try:
        markdown_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    except OSError as exc:
        _fail("write_failed", f"Could not write {markdown_path}: {exc}")
    report["markdown_report"] = str(markdown_path)
    return report


def _validate_inspection_receipt_overlay(
    root: Path,
    source_sha256: str,
    descriptor: dict[str, Any],
) -> dict[str, Any]:
    """Validate one selected inspection receipt overlay and its source proof."""

    root = Path(root).resolve()
    relative = _safe_relative(descriptor["path"], "inspection receipt overlay path")
    raw_relative = _safe_relative(descriptor["raw"], "inspection receipt raw path")
    evidence_relative = _safe_relative(
        descriptor["evidence"], "inspection receipt evidence path"
    )
    row = descriptor["row"]
    overlay_path = root / Path(relative)
    raw_path = root / Path(raw_relative)
    evidence_path = root / Path(evidence_relative)
    for path, field in (
        (overlay_path, "inspection receipt overlay path"),
        (raw_path, "inspection receipt raw path"),
        (evidence_path, "inspection receipt evidence path"),
    ):
        _assert_safe_path(root, path, field)
        if not path.is_file():
            _fail("missing_file", f"Inspection receipt overlay source is missing: {path}")
    try:
        from PIL import Image

        with Image.open(evidence_path) as pane:
            evidence_size = pane.size
    except OSError as exc:
        _fail("inspection_receipt_overlay_invalid", f"Inspection receipt gameplay crop is unreadable: {evidence_path}: {exc}")
    if evidence_size != (810, 1080):
        _fail(
            "inspection_receipt_overlay_invalid",
            f"Inspection receipt evidence is not an 810x1080 gameplay crop: {evidence_path}",
        )
    extra = _load_json(overlay_path)
    raw = _load_json(raw_path)
    if not isinstance(extra, dict) or not isinstance(raw, dict):
        _fail("inspection_receipt_overlay_invalid", f"Inspection receipt proof is not an object: {overlay_path}")
    if raw.get("source_sha256") != source_sha256:
        _fail("source_mismatch", f"Inspection receipt raw source differs: {raw_path}")
    if raw.get("source_timestamp_ms") != row.get("source_timestamp_ms"):
        _fail("timestamp_mismatch", f"Inspection receipt raw timestamp differs: {raw_path}")
    raw_evidence = raw.get("evidence")
    own_root_value = row.get("own_root", "")
    if not isinstance(own_root_value, str):
        _fail("path_mismatch", f"Inspection receipt row own root is invalid: {raw_path}")
    own_root = _safe_relative(own_root_value, "inspection receipt row own root") if own_root_value else ""
    if not isinstance(raw_evidence, str):
        _fail("path_mismatch", f"Inspection receipt raw evidence is invalid: {raw_path}")
    raw_evidence_relative = _safe_relative(raw_evidence, "inspection receipt raw evidence path")
    raw_evidence_path = root / Path(_join(own_root, raw_evidence_relative))
    _assert_safe_path(root, raw_evidence_path, "inspection receipt raw evidence path")
    if raw_evidence_path.resolve() != evidence_path.resolve():
        _fail("path_mismatch", f"Inspection receipt evidence differs: {raw_path}")
    evidence_sha256 = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    if extra.get("evidence_sha256") != evidence_sha256:
        _fail("hash_mismatch", f"Inspection receipt overlay evidence hash differs: {overlay_path}")
    raw_sha256 = _canonical_json_fingerprint(raw)
    if extra.get("raw_sha256") != raw_sha256:
        _fail("hash_mismatch", f"Inspection receipt overlay raw hash differs: {overlay_path}")
    if not isinstance(extra.get("lines"), list):
        _fail("inspection_receipt_overlay_invalid", f"Inspection receipt overlay lines are missing: {overlay_path}")
    if not isinstance(extra.get("model_sha256"), dict):
        _fail("inspection_receipt_overlay_invalid", f"Inspection receipt overlay model provenance is missing: {overlay_path}")
    return {
        "path": relative,
        "raw": raw_relative,
        "evidence": evidence_relative,
        "source_timestamp_ms": row.get("source_timestamp_ms"),
        "sha256": hashlib.sha256(overlay_path.read_bytes()).hexdigest(),
        "evidence_sha256": evidence_sha256,
        "raw_sha256": raw_sha256,
    }


def _validate_inspection_receipt_overlay_with_loader(
    root: Path,
    source_sha256: str,
    descriptor: dict[str, Any],
) -> dict[str, Any]:
    """Validate one inspection overlay through ``receipt_occlusion.annotate_path``."""

    from tracen_replay.receipt_occlusion import annotate_path, receipt_line

    root = Path(root).resolve()
    raw_path = root / Path(_safe_relative(descriptor["raw"], "inspection receipt raw path"))
    evidence_path = root / Path(
        _safe_relative(descriptor["evidence"], "inspection receipt evidence path")
    )
    raw = _load_json(raw_path)
    try:
        checked = annotate_path(
            raw,
            evidence_path,
            raw,
            source_sha256=source_sha256,
        )
    except (OSError, TypeError, KeyError, ValueError) as exc:
        _fail(
            "inspection_receipt_overlay_loader_failed",
            f"Inspection receipt overlay loader rejected {descriptor['path']}: {exc}",
        )
    if not isinstance(checked, dict):
        _fail(
            "inspection_receipt_overlay_loader_failed",
            f"Inspection receipt overlay loader returned an invalid result for {descriptor['path']}.",
        )
    return {
        "path": descriptor["path"],
        "status": "annotate_path_invoked",
        "provenance_bound": isinstance(checked.get("receipt_overlay_provenance"), dict),
        "receipt_line_candidates": sum(
            1
            for line in raw.get("lines", [])
            if isinstance(line, dict) and receipt_line(line)
        ),
        "occluded_receipt_lines": len(checked.get("occluded_receipt_lines", [])),
        "resolved_receipt_occlusions": len(checked.get("resolved_receipt_occlusions", [])),
    }


def repair_inspection_receipt_overlays(output: Path, names: list[str]) -> dict[str, Any]:
    """Repair selected inspection receipt overlays in disposable roots.

    The canonical and worker manifests are checked before any copy.  Only
    overlays derived from selected inspection evidence rows are materialized;
    source and worker proof is validated through the real
    ``receipt_occlusion.annotate_path`` loader.  No report or manifest values
    participate in this repair.
    """

    output = Path(output).resolve()
    if not output.is_dir():
        _fail("missing_directory", f"Prepared worker root is missing: {output}")
    source_freeze = verified_source_freeze()
    report: dict[str, Any] = {
        "schema_version": INSPECTION_RECEIPT_OVERLAY_REPAIR_SCHEMA,
        **source_freeze,
        "preparation_root": str(output),
        "ocr_executed": False,
        "analysis_job_executed": False,
        "accepted_report_values_loaded": False,
        "manifests_modified": False,
        "recordings": [],
    }

    for name in names:
        if name not in RECORDINGS:
            _fail("unknown_recording", f"Unknown recording: {name}")
        details = RECORDINGS[name]
        worker_root = output / name
        worker_manifest = worker_root / "replay-input-manifest.json"
        if not worker_root.is_dir() or not worker_manifest.is_file():
            _fail("missing_manifest", f"Prepared worker manifest is missing: {worker_manifest}")
        preserved_manifests = [
            path
            for path in worker_root.glob("replay-input-manifest.previous*.json")
            if path.is_file()
        ]
        manifest_paths = [worker_manifest, *preserved_manifests]
        for path in manifest_paths:
            if _is_reparse(path):
                _fail("reparse_point", f"Worker manifest is a reparse point: {path}")
        manifest_hashes_before = {
            path.relative_to(worker_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in manifest_paths
        }
        cache_root = Path(details["cache_root"]).resolve()
        canonical_manifest = Path(details["manifest"]).resolve()
        expected_source = details["source_sha256"]
        try:
            canonical_normalized = load_manifest(
                canonical_manifest,
                cache_root,
                expected_source_sha256=expected_source,
            )
            load_manifest(
                worker_manifest,
                worker_root,
                expected_source_sha256=expected_source,
            )
        except ReplayInputError as exc:
            _fail(
                "repair_manifest_invalid",
                f"Worker or canonical manifest failed validation ({exc.code}): {exc}",
            )

        print(
            json.dumps(
                {
                    "stage": "inspection-receipt-overlay-manifest-validated",
                    "recording": name,
                    "worker_root": str(worker_root),
                },
                sort_keys=True,
            ),
            flush=True,
        )

        overlays = _inspection_receipt_overlay_descriptors(
            canonical_normalized, cache_root
        )
        ordered = sorted(overlays.values(), key=lambda item: item["path"].casefold())
        # Validate every source proof before touching the worker root.  The
        # direct hash checks ensure annotate_path cannot silently return early
        # for a raw row without a receipt-shaped line.
        source_validations = [
            _validate_inspection_receipt_overlay(cache_root, expected_source, descriptor)
            for descriptor in ordered
        ]
        source_loader_validations = [
            _validate_inspection_receipt_overlay_with_loader(
                cache_root, expected_source, descriptor
            )
            for descriptor in ordered
        ]
        digests = DigestCache()
        stats = _empty_stats()
        staged: list[str] = []
        already_present: list[str] = []
        for descriptor in ordered:
            relative = descriptor["path"]
            source = cache_root / Path(relative)
            target = worker_root / Path(relative)
            _assert_safe_path(worker_root, target, "inspection receipt overlay destination")
            if target.exists() or target.is_symlink():
                if _is_reparse(target) or not target.is_file():
                    _fail("path_collision", f"Inspection receipt overlay destination is not a file: {target}")
                if digests.digest(target) != digests.digest(source):
                    _fail("content_collision", f"Inspection receipt overlay differs at {relative}")

        for descriptor in ordered:
            relative = descriptor["path"]
            target = worker_root / Path(relative)
            existed = target.exists()
            _copy_one(
                cache_root / Path(relative),
                worker_root,
                relative,
                source_root=cache_root,
                hardlink=False,
                digests=digests,
                stats=stats,
            )
            (already_present if existed else staged).append(relative)

        worker_validations = [
            _validate_inspection_receipt_overlay(worker_root, expected_source, descriptor)
            for descriptor in ordered
        ]
        worker_loader_validations = [
            _validate_inspection_receipt_overlay_with_loader(
                worker_root, expected_source, descriptor
            )
            for descriptor in ordered
        ]
        print(
            json.dumps(
                {
                    "stage": "inspection-receipt-overlays-validated",
                    "recording": name,
                    "overlay_count": len(ordered),
                    "staged_count": len(staged),
                    "source_annotate_path_invoked_count": len(source_loader_validations),
                    "worker_annotate_path_invoked_count": len(worker_loader_validations),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        manifest_hashes_after = {
            path.relative_to(worker_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in manifest_paths
        }
        if manifest_hashes_after != manifest_hashes_before:
            _fail(
                "manifest_modified",
                f"Worker manifest changed during inspection receipt overlay repair: {worker_manifest}",
            )
        report["recordings"].append(
            {
                "recording": name,
                "worker_root": str(worker_root),
                "worker_manifest": str(worker_manifest),
                "worker_manifest_sha256": manifest_hashes_after["replay-input-manifest.json"],
                "preserved_manifest_hashes": manifest_hashes_after,
                "canonical_manifest": str(canonical_manifest),
                "canonical_manifest_sha256": hashlib.sha256(canonical_manifest.read_bytes()).hexdigest(),
                "base_folder": canonical_normalized["base"].get("folder", ""),
                "overlay_count": len(ordered),
                "source_provenance_validated_count": len(source_validations),
                "source_annotate_path_invoked_count": len(source_loader_validations),
                "source_annotate_path_provenance_bound_count": sum(
                    bool(item["provenance_bound"]) for item in source_loader_validations
                ),
                "worker_provenance_validated_count": len(worker_validations),
                "worker_annotate_path_invoked_count": len(worker_loader_validations),
                "worker_annotate_path_provenance_bound_count": sum(
                    bool(item["provenance_bound"]) for item in worker_loader_validations
                ),
                "source_annotate_path_samples": source_loader_validations,
                "worker_annotate_path_samples": worker_loader_validations,
                "staged_paths": staged,
                "already_present_paths": already_present,
                "materialization": stats,
            }
        )

    report_path = output / "inspection-receipt-overlay-repair-v1.json"
    markdown_path = output / "inspection-receipt-overlay-repair-v1.md"
    if report_path.exists() or markdown_path.exists():
        _fail("output_exists", f"Refusing to overwrite inspection receipt overlay repair report in {output}")
    report["report_sha256"] = _write_json(report_path, report)
    lines = [
        "# Inspection receipt overlay repair",
        "",
        "This bounded repair copied only overlays derived from selected inspection evidence rows.",
        "Source and worker proofs passed the real `receipt_occlusion.annotate_path` loader; worker manifests, preserved manifests, canonical caches, accepted reports, OCR, and analysis jobs were not modified or run.",
        "",
    ]
    for item in report["recordings"]:
        lines.extend(
            [
                f"## `{item['recording']}`",
                "",
                f"- Worker manifest SHA-256: `{item['worker_manifest_sha256']}`",
                f"- Selected inspection overlays: {item['overlay_count']}; newly staged: {len(item['staged_paths'])}; already present: {len(item['already_present_paths'])}.",
                f"- Source and worker `annotate_path` invocations: {item['source_annotate_path_invoked_count']} / {item['worker_annotate_path_invoked_count']}; provenance-bound results: {item['source_annotate_path_provenance_bound_count']} / {item['worker_annotate_path_provenance_bound_count']}.",
                "",
            ]
        )
    try:
        markdown_path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    except OSError as exc:
        _fail("write_failed", f"Could not write {markdown_path}: {exc}")
    report["markdown_report"] = str(markdown_path)
    return report


def _worker_command(details: dict[str, Any], worker: Path) -> str:
    source = str(details["source_video"])
    return (
        f"& '.venv/Scripts/python.exe' -m tracen_replay.analysis_job "
        f"'{source}' --output '{worker.as_posix()}' --fps 4 --workers 4 "
        f"--model-dir '.local/models/rapidocr' --reparse-only "
        f"--replay-input-manifest '{(worker / 'replay-input-manifest.json').as_posix()}' "
        f"--replay-input-root '{worker.as_posix()}'"
    )


def _prepare_recording(name: str, details: dict[str, Any], output_root: Path, source_manifest_root: Path) -> dict[str, Any]:
    started = time.monotonic()
    source_video = Path(details["source_video"]).resolve()
    cache_root = Path(details["cache_root"]).resolve()
    canonical_manifest = Path(details["manifest"]).resolve()
    expected_source = details["source_sha256"]
    if not source_video.is_file():
        _fail("source_not_found", f"Source video is missing: {source_video}")
    if not cache_root.is_dir():
        _fail("cache_not_found", f"Source cache is missing: {cache_root}")
    video_sha256 = DigestCache().digest(source_video)
    if video_sha256 != expected_source:
        _fail("source_mismatch", f"Source video hash differs for {name}: {video_sha256}")
    canonical_normalized = load_manifest(canonical_manifest, cache_root, expected_source_sha256=expected_source)
    selected, immutable, conventional = _collect_closure(canonical_normalized, cache_root)
    inspection_receipt_overlays = _inspection_receipt_overlay_descriptors(
        canonical_normalized, cache_root
    )
    # Validate source-bound inspection overlays before creating the disposable
    # root.  The provenance checks remain authoritative even when
    # ``annotate_path`` returns early for a row without a receipt-shaped line.
    inspection_receipt_overlay_validations = [
        _validate_inspection_receipt_overlay(cache_root, expected_source, descriptor)
        for descriptor in sorted(
            inspection_receipt_overlays.values(), key=lambda item: item["path"].casefold()
        )
    ]
    inspection_receipt_overlay_loader_validations = [
        _validate_inspection_receipt_overlay_with_loader(
            cache_root, expected_source, descriptor
        )
        for descriptor in sorted(
            inspection_receipt_overlays.values(), key=lambda item: item["path"].casefold()
        )
    ]
    namespace = _verify_nested_namespace(cache_root, details["base_folder"], selected)

    worker_root = output_root / name
    if worker_root.exists():
        _fail("output_exists", f"Refusing to reuse worker root: {worker_root}")
    worker_root.mkdir(parents=True)
    stats = _empty_stats()
    digests = DigestCache()
    _copy_selected(cache_root, worker_root, selected, immutable, digests=digests, stats=stats)

    # Preserve the source manifest as an independent metadata copy for audit,
    # and put a worker-local copy beside the disposable input tree.
    canonical_copy = source_manifest_root / f"{name}-canonical.json"
    canonical_manifest_sha256 = _copy_canonical_manifest(canonical_manifest, canonical_copy, digests)
    local_manifest = _load_json(canonical_manifest)
    if not isinstance(local_manifest, dict):
        _fail("invalid_manifest", f"Replay input manifest is not an object: {canonical_manifest}")
    if name == "independent-02":
        local_manifest = _patch_status_manifest(local_manifest, STATUS_SIDECAR["source"], worker_root)
        sidecar_target = worker_root / Path(STATUS_SIDECAR["target"])
        sidecar_target_relative = sidecar_target.relative_to(worker_root).as_posix()
        _copy_one(
            STATUS_SIDECAR["source"],
            worker_root,
            sidecar_target_relative,
            source_root=STATUS_SIDECAR["source"].parent,
            hardlink=False,
            digests=digests,
            stats=stats,
        )
        # Keep a frame-id alias in the base namespace so the fresh
        # automatic-refinement audit and the manifest replay observe the same
        # valid status sidecar.  The manifest-scoped early loader suppresses a
        # second application when this conventional alias is present.
        status_alias_relative = _join(
            details["base_folder"],
            "status-badge-refinement",
            Path(STATUS_SIDECAR["target"]).name,
        )
        _copy_one(
            STATUS_SIDECAR["source"],
            worker_root,
            status_alias_relative,
            source_root=STATUS_SIDECAR["source"].parent,
            hardlink=False,
            digests=digests,
            stats=stats,
        )

    local_manifest_path = worker_root / "replay-input-manifest.json"
    local_manifest_sha256 = _write_json(local_manifest_path, local_manifest)
    race_info = None
    if name == "independent-02":
        race_info = _register_external_race(worker_root, expected_source)

    try:
        normalized_local = load_manifest(local_manifest_path, worker_root, expected_source_sha256=expected_source)
    except ReplayInputError as exc:
        _fail("local_manifest_invalid", f"Worker-local manifest failed validation ({exc.code}): {exc}")
    concert_validation = _validate_concert_panel_sidecars(
        worker_root, details["base_folder"]
    )
    race_metadata = None
    race_rows = 0
    if name != "independent-02":
        try:
            from tracen_replay.race_reward_inspection import load as load_race_rewards

            race_metadata, race_rows_list = load_race_rewards(worker_root, expected_source)
            race_rows = len(race_rows_list)
        except (OSError, TypeError, KeyError, ValueError, ZeroDivisionError) as exc:
            _fail("race_validation_failed", f"Existing race window failed validation for {name}: {exc}")
    elif race_info is not None:
        race_metadata = race_info
        race_rows = int(race_info["verified_frames"])

    supplement_names = [
        supplement.get("name") or supplement.get("kind")
        for supplement in normalized_local.get("supplements", [])
        if isinstance(supplement, dict)
    ]
    performance_entries = sum(
        len(supplement.get("entries", []))
        for supplement in normalized_local.get("supplements", [])
        if isinstance(supplement, dict) and supplement.get("name") == "performance_panel"
    )
    status_entries = sum(
        len(supplement.get("entries", []))
        for supplement in normalized_local.get("supplements", [])
        if isinstance(supplement, dict) and supplement.get("name") == "status_badge_refinement"
    )
    result = {
        "recording": name,
        "source_video": str(source_video),
        "source_sha256": expected_source,
        "cache_root": str(cache_root),
        "source_namespaces": [str(cache_root)]
        + ([str(cache_root / details["base_folder"])] if details["base_folder"] else []),
        "base_folder": details["base_folder"],
        "worker_root": str(worker_root),
        "worker_manifest": str(local_manifest_path),
        "worker_manifest_sha256": local_manifest_sha256,
        "canonical_manifest": str(canonical_manifest),
        "canonical_manifest_copy": str(canonical_copy),
        "canonical_manifest_sha256": canonical_manifest_sha256,
        "closure": {
            "selected_paths": len(selected),
            "immutable_image_paths": len(immutable),
            "conventional": conventional,
            "namespace_alias": namespace,
        },
        "materialization": stats,
        "validation": {
            "status": "passed",
            "base_frames": normalized_local["base"]["frame_count"],
            "inspection_rows": sum(group["row_count"] for group in normalized_local.get("inspections", [])),
            "recovery_rows": sum(group["row_count"] for group in normalized_local.get("recovery", [])),
            "supplement_entries": sum(len(group.get("entries", [])) for group in normalized_local.get("supplements", [])),
            "supplement_names": supplement_names,
            "performance_panel_entries": performance_entries,
            "status_badge_entries": status_entries,
            "concert_panel_entries": concert_validation["checked"],
            "concert_panel_sidecars": concert_validation["sidecars"],
            "race_rows": race_rows,
            "race_metadata": race_metadata,
            "inspection_receipt_overlay_count": len(inspection_receipt_overlays),
            "inspection_receipt_overlay_provenance_validated_count": len(
                inspection_receipt_overlay_validations
            ),
            "inspection_receipt_overlay_annotate_path_invoked_count": len(
                inspection_receipt_overlay_loader_validations
            ),
        },
        "deferred_command": _worker_command(details, worker_root),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    return result


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Final worker input preparation",
        "",
        f"Preparation schema: `{report['schema_version']}`.",
        f"G2 source freeze reference: `{report['g2_freeze_sha256']}`.",
        "",
        "This preparation is read-only with respect to the three canonical caches and source videos. "
        "It performed no `analysis_job` execution and no OCR. Each worker root contains only the "
        "manifest closure plus explicitly registered source-bound caches. Source-frame and gameplay "
        "images named by the manifest are hardlinked; JSON, HTML, sidecar, and generated proof files "
        "are independent writable copies. Gameplay receipt overlays and inspection refinement "
        "companions are included only when their loader-derived source paths exist. Selected "
        "inspection evidence overlays are included by row-derived path only. Reparse points "
        "and unequal collisions fail closed.",
        "",
        "## Prepared roots",
        "",
    ]
    for item in report["recordings"]:
        mat = item["materialization"]
        val = item["validation"]
        lines.extend(
            [
                f"### `{item['recording']}`",
                "",
                f"- Worker root: `{item['worker_root']}`",
                f"- Manifest: `{item['worker_manifest']}` (SHA-256 `{item['worker_manifest_sha256']}`)",
                f"- Base namespace: `{item['base_folder'] or '.'}`",
                f"- Closure: {item['closure']['selected_paths']} files selected; "
                f"{item['closure']['immutable_image_paths']} immutable image paths eligible for hardlinks.",
                f"- Materialized: {mat['copied_files']} independent copies ({mat['copied_bytes']} bytes), "
                f"{mat['hardlinked_files']} hardlinks ({mat['hardlinked_bytes']} bytes), "
                f"{mat['equal_collisions']} equal namespace collisions.",
                f"- Dry validation: `{val['status']}`; base frames {val['base_frames']}, "
                f"inspection rows {val['inspection_rows']}, recovery rows {val['recovery_rows']}, "
                f"supplement entries {val['supplement_entries']}.",
                f"- Performance panel entries: {val['performance_panel_entries']}; "
                f"status badge entries: {val['status_badge_entries']}; concert panel sidecars: "
                f"{val['concert_panel_entries']}; inspection refinement companions: "
                f"{len(item['closure']['conventional'].get('inspection_refinement_companions', []))}; "
                f"inspection receipt overlays: "
                f"{len(item['closure']['conventional'].get('inspection_receipt_overlay_sidecars', []))} "
                f"(source provenance + annotate_path validated: "
                f"{val.get('inspection_receipt_overlay_provenance_validated_count', 0)} + "
                f"{val.get('inspection_receipt_overlay_annotate_path_invoked_count', 0)}); "
                f"registered race rows: {val['race_rows']}.",
                "",
                "Deferred command (run only after the combined implementation freeze):",
                "",
                "```powershell",
                item["deferred_command"],
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Registered source-bound extras",
            "",
            "The independent-02 root contains the reviewed status badge sidecar under "
            "`status-badge-source/independent-02-t029-opening-mood/status-badge-refinement/`, "
            "a frame-id alias under the base `status-badge-refinement/` namespace, and "
            "a registered `race-reward-inspection/642000-646000-60` window. The race window was "
            "loaded through `race_reward_inspection.load` and verified all six staged source frames; "
            "its quantity artifact uses `fixed_badge_quantity_windows_v2`. The canonical status and "
            "race staging sources remain untouched.",
            "",
            "`automatic_refinement` remains a bounded panel/status candidate stage. Fresh bounded race "
            "candidates must enter through `race_reward_inspection.inspect(..., fixed_quantity_windows=True)` "
            "and its registered window loader. The three deferred commands are reparse-only, so they "
            "consume only the registered window above; they do not generate a new OCR window.",
            "",
            "## Remaining dependencies",
            "",
            "- Run the deferred commands sequentially only after the parent confirms the combined code/report freeze; the fourth independent recording remains untouched.",
            "- Rebuild these disposable roots if any canonical cache, registered sidecar, replay manifest, or implementation contract changes before execution. Never refresh by writing into a canonical cache.",
            "- The worker-local manifest has no accepted report values. Any `reference.accepted_report_sha256` field remains comparison metadata only.",
            "- `race-quantity-refinement` aggregate artifacts are preserved where present; new race windows must be registered through the race inspection API so the common loader can revalidate PTS, pixels, raw hashes, and quantity observations.",
            "",
        ]
    )
    return "\n".join(lines)


def prepare(output: Path, names: list[str]) -> dict[str, Any]:
    output = output.resolve()
    if output.exists():
        _fail("output_exists", f"Refusing to reuse preparation root: {output}")
    source_freeze = verified_source_freeze()
    output.mkdir(parents=True)
    source_manifest_root = output / "source-manifests"
    source_manifest_root.mkdir()
    report: dict[str, Any] = {
        "schema_version": PREPARATION_SCHEMA,
        "manifest_schema": SCHEMA,
        **source_freeze,
        "output_root": str(output),
        "recordings": [],
        "prepared_at_ms": round(time.time() * 1000),
        "ocr_executed": False,
        "analysis_job_executed": False,
        "accepted_report_values_loaded": False,
    }
    for name in names:
        item = _prepare_recording(name, RECORDINGS[name], output, source_manifest_root)
        report["recordings"].append(item)
        print(
            json.dumps(
                {
                    "stage": "worker-input-prepared",
                    "recording": name,
                    "worker_root": item["worker_root"],
                    "copied_files": item["materialization"]["copied_files"],
                    "hardlinked_files": item["materialization"]["hardlinked_files"],
                    "validation": item["validation"]["status"],
                },
                sort_keys=True,
            ),
            flush=True,
        )
    report_path = output / "preparation-report.json"
    report["report_sha256"] = _write_json(report_path, report)
    markdown_path = output / "preparation-report.md"
    try:
        markdown_path.write_text(_markdown(report), encoding="utf-8", newline="\n")
    except OSError as exc:
        _fail("write_failed", f"Could not write {markdown_path}: {exc}")
    report["markdown_report"] = str(markdown_path)
    # Update the JSON with the markdown path only through a fresh preparation
    # report is intentionally avoided: its hash is the immutable audit record.
    print(json.dumps({"stage": "complete", "report": str(report_path), "markdown": str(markdown_path)}, sort_keys=True), flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--recording", choices=sorted(RECORDINGS), action="append")
    parser.add_argument(
        "--augment-source-sidecars",
        action="store_true",
        help="Add registered weak-state and numeric sidecars to existing disposable roots.",
    )
    parser.add_argument(
        "--repair-concert-panel-proofs",
        action="store_true",
        help="Complete source-declared Concert Info proof files in existing disposable roots.",
    )
    parser.add_argument(
        "--repair-inspection-refinement-companions",
        action="store_true",
        help="Complete loader-derived training inspection refinement companions in existing disposable roots.",
    )
    parser.add_argument(
        "--repair-inspection-receipt-overlays",
        action="store_true",
        help="Complete receipt overlays for selected inspection evidence in existing disposable roots.",
    )
    args = parser.parse_args(argv)
    try:
        names = args.recording or list(RECORDINGS)
        existing_root_repairs = sum(
            bool(value)
            for value in (
                args.augment_source_sidecars,
                args.repair_concert_panel_proofs,
                args.repair_inspection_refinement_companions,
                args.repair_inspection_receipt_overlays,
            )
        )
        if existing_root_repairs > 1:
            _fail("invalid_options", "Choose one existing-root mutation operation.")
        if args.repair_concert_panel_proofs:
            report = repair_concert_panel_proof_closure(args.output, names)
            print(json.dumps({
                "stage": "concert-panel-proofs-repaired",
                "report": str(args.output.resolve() / "concert-panel-proof-repair-v1.json"),
                "recordings": [item["recording"] for item in report["recordings"]],
            }, sort_keys=True), flush=True)
        elif args.augment_source_sidecars:
            report = augment(args.output, names)
            print(json.dumps({
                "stage": "auxiliary-sidecars-augmented",
                "report": str(args.output.resolve() / "auxiliary-sidecar-augmentation-v1.json"),
                "recordings": [item["recording"] for item in report["recordings"]],
            }, sort_keys=True), flush=True)
        elif args.repair_inspection_refinement_companions:
            report = repair_inspection_refinement_companions(args.output, names)
            print(json.dumps({
                "stage": "inspection-refinement-companions-repaired",
                "report": str(args.output.resolve() / "inspection-refinement-companion-repair-v1.json"),
                "recordings": [item["recording"] for item in report["recordings"]],
            }, sort_keys=True), flush=True)
        elif args.repair_inspection_receipt_overlays:
            report = repair_inspection_receipt_overlays(args.output, names)
            print(json.dumps({
                "stage": "inspection-receipt-overlays-repaired",
                "report": str(args.output.resolve() / "inspection-receipt-overlay-repair-v1.json"),
                "recordings": [item["recording"] for item in report["recordings"]],
            }, sort_keys=True), flush=True)
        else:
            prepare(args.output, names)
    except (PreparationError, ReplayInputError) as exc:
        print(json.dumps({"stage": "failed", "code": getattr(exc, "code", "preparation_error"), "message": str(exc)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
