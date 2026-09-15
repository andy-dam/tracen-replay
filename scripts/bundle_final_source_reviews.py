"""Build an integrity manifest for the bounded final source reviews.

This command is deliberately small in scope.  It walks the paths used by the
named review JSON documents, resolves them against the repository or the
recording evidence root, hashes every local file once, and records the source
metadata used to bind timestamped images.  It does not inspect pixels and it
does not change replay or recognition code.

Source videos are outside the repository and can be very large.  They are
listed in the manifest by default, but are streamed and included in
``immutable_files_sha256`` only when ``hash_source_video=True`` (the
``--hash-source-video`` command-line option).
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "tracen-replay/final-source-review-bundle-v1"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm"})

# These names are references in the review formats used by the final
# reliability investigation.  A plain ``path`` is also accepted because all
# cited image and report paths use that shape.  Values are never globbed.
FILE_REFERENCE_KEYS = frozenset({
    "path",
    "relative_path",
    "absolute_path",
    "evidence",
    "evidence_path",
    "evidence_paths",
    "gameplay_crops",
    "gameplay_crop",
    "gameplay_crop_path",
    "source_frame",
    "source_frame_path",
    "source_frames",
    "raw_sidecar",
    "raw_sidecars",
    "source_video",
    "source_video_path",
    "source_identity_manifest",
    "frame_manifest",
    "capture_manifest",
    "replay_config",
    "replay_configs",
    "locator_report",
    "locator_reports",
    "report",
    "reports",
    "inventory",
    "inventory_files",
    "checklist",
    "prior_boundary_report",
    "prior_source_findings",
    "canonical_inventory",
    "frozen_source_selection",
    "boundary_inventory",
    "proof_paths",
    "supporting_frames",
    "absolute_evidence",
    "absolute_paths",
    "relative_paths",
    "before_evidence",
    "after_evidence",
    "source_evidence",
    "source_review_artifact",
    "sampled_source_artifact",
    "full_frame_evidence",
    "contact_sheet",
    "contact_sheet_page",
    "contact_sheet_pages",
    "source_path",
    "source_observations",
    "artifact_path",
    "script",
    "source_record",
    "panel_gameplay_crop",
    "preceding_gameplay_crop",
    "v1_gameplay_crop",
    "independent_01_gameplay_crop",
    "initial_transition_frame_without_control",
    "provenance_source_frame",
})

DIRECTORY_REFERENCE_KEYS = frozenset({
    "evidence_root",
    "cache_root",
    "evidence_caches_reviewed",
    "capture_cache",
    "input_directory",
})

# Mapping from a declared hash to the sibling path field it describes.  The
# generic ``sha256`` form is common in preserved-input lists and source
# evidence rows.  ``source_sha256`` is a source-video identity hash and is
# handled through the run's source-video map rather than as an image hash.
HASH_PATH_KEYS: dict[str, tuple[str, ...]] = {
    "sha256": (
        "path", "relative_path", "absolute_path", "evidence", "source_frame",
        "gameplay_crop", "source_review_artifact", "sampled_source_artifact",
        "report", "inventory", "checklist", "frame_manifest",
        "capture_manifest", "source_identity_manifest", "replay_config",
        "source_video", "source_evidence", "artifact_path", "script",
        "source_record", "panel_gameplay_crop", "preceding_gameplay_crop",
        "v1_gameplay_crop", "independent_01_gameplay_crop",
        "initial_transition_frame_without_control", "provenance_source_frame",
    ),
    "evidence_sha256": ("evidence", "evidence_path", "source_evidence", "path"),
    "source_frame_sha256": ("source_frame", "source_frame_path"),
    "raw_sidecar_sha256": ("raw_sidecar", "raw_sidecars"),
    "gameplay_crop_sha256": ("gameplay_crop", "gameplay_crop_path"),
    "gameplay_sha256": ("gameplay_crop", "gameplay_crop_path", "path"),
    "capture_manifest_sha256": ("capture_manifest",),
    "frame_manifest_sha256": ("frame_manifest",),
    "source_identity_manifest_sha256": ("source_identity_manifest",),
    "replay_config_sha256": ("replay_config",),
    "report_sha256": ("report",),
    "inventory_sha256": ("inventory",),
    "boundary_inventory_sha256": ("boundary_inventory",),
    "source_observations_sha256": ("sampled_source_artifact", "source_observations"),
    "checklist_sha256": ("checklist",),
    "script_sha256": ("script",),
}

# These hashes identify the external recording associated with a review.  They
# are not hashes of the neighboring report/artifact path: the run context must
# bind them to a declared source-video path before they are accepted.
SOURCE_IDENTITY_HASH_KEYS = (
    "source_sha256",
    "source_video_sha256",
    "artifact_source_sha256",
)


def _hash_path_keys(key: str) -> tuple[str, ...]:
    """Return path siblings for a file hash key used by a review format."""
    explicit = HASH_PATH_KEYS.get(key)
    if explicit is not None:
        return explicit
    # The independent-02 terminal projection prefixes this artifact hash with
    # its run name.  Keep the accepted shape narrow so an unrelated hash-only
    # field cannot be treated as a file reference by accident.
    if key.endswith("_source_observations_sha256"):
        return ("sampled_source_artifact", "source_observations")
    return ()


def _has_path_value(value: Mapping[str, Any], keys: Iterable[str]) -> bool:
    """Whether *value* has at least one concrete path sibling in *keys*."""
    for key in keys:
        raw = value.get(key)
        candidates = raw if isinstance(raw, list) else (raw,)
        if any(isinstance(item, str) and _looks_like_path(item, key)
               for item in candidates):
            return True
    return False


def _has_single_image_reference(value: Mapping[str, Any]) -> bool:
    """Whether a nested evidence object cites exactly one image path.

    Some preserved review rows put the observation timestamp on the enclosing
    case and keep the image path in a one-object ``source_evidence`` field.
    That timestamp can safely be inherited for the one image.  A list of
    images remains an aggregate and must use explicit per-item timestamps.
    """
    count = 0
    for key, raw in value.items():
        if key not in FILE_REFERENCE_KEYS:
            continue
        values = raw if isinstance(raw, list) else [raw]
        for item in values:
            if (isinstance(item, str) and _looks_like_path(item, key) and
                    Path(item.replace("\\", "/")).suffix.lower() in IMAGE_SUFFIXES):
                count += 1
    return count == 1

TIMESTAMP_KEYS = (
    "source_timestamp_ms",
    "timestamp_ms",
    "capture_timestamp_ms",
    "source_time_ms",
    "time_ms",
)

RUN_MAPPING_KEYS = frozenset({
    "recordings", "source_integrity", "runs", "inputs", "preserved_inputs",
})


class BundleError(ValueError):
    """Raised when a review reference cannot be bound to immutable bytes."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BundleError(message)


def digest(path: str | Path) -> str:
    """Return a streaming SHA-256 digest for *path*."""
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


sha256 = digest


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"Cannot read JSON {path}: {exc}") from exc


def _pointer(parent: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{parent}[{key}]"
    escaped = str(key).replace("~", "~0").replace("/", "~1")
    return f"{parent}.{escaped}" if parent != "$" else f"$.{escaped}"


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _timestamp(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return value


def _row_timestamp(value: Mapping[str, Any]) -> int | float | None:
    for key in TIMESTAMP_KEYS:
        if key in value:
            return _timestamp(value[key])
    return None


def _aligned_timestamps(value: Mapping[str, Any], key: str,
                        count: int) -> list[int | float] | None:
    """Return per-item timestamps when a list reference supplies them."""
    candidates = (
        f"{key}_timestamps_ms",
        f"{key}_source_timestamp_ms",
        "evidence_source_timestamp_ms",
        "source_timestamps_ms",
        "timestamps_ms",
    )
    for candidate_key in candidates:
        raw = value.get(candidate_key)
        if not isinstance(raw, list) or len(raw) != count:
            continue
        timestamps = [_timestamp(item) for item in raw]
        if all(item is not None for item in timestamps):
            return [item for item in timestamps if item is not None]
    return None


_TIMESTAMP_UNSET = object()


def _hash_value(key: str, value: Any, pointer: str) -> str | None:
    if not key.endswith("sha256"):
        return None
    require(isinstance(value, str) and SHA256_RE.fullmatch(value.strip()) is not None,
            f"Invalid SHA-256 at {pointer}: expected 64 hexadecimal characters")
    return value.strip().lower()


def _normal_text(value: Any, pointer: str) -> str:
    require(isinstance(value, str) and value.strip(), f"Missing path at {pointer}")
    text = value.strip()
    require("\x00" not in text, f"NUL in path at {pointer}")
    normalized = text.replace("\\", "/")
    parts = normalized.split("/")
    require(".." not in parts, f"Parent traversal is not allowed at {pointer}: {value}")
    require(not any(char in normalized for char in ("*", "?", "{", "}", "<", ">")),
            f"Path template is not allowed at {pointer}: {value}")
    require(not normalized.lower().startswith(("http://", "https://")),
            f"URL is not a local source reference at {pointer}: {value}")
    return text


def _looks_like_path(value: Any, key: str) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip().replace("\\", "/")
    if text.lower().startswith(("http://", "https://")):
        return False
    if any(char in text for char in ("*", "?", "{", "}", "<", ">")):
        return False
    # An ``evidence`` field is occasionally used for a prose note.  Require a
    # recognizable file shape for that generic name; explicit path keys do not
    # need this heuristic.
    if key in {"evidence", "source_evidence", "source_observations", "source_record"}:
        if key == "source_record" and " and " in text:
            return False
        suffix = Path(text).suffix.lower()
        return (suffix in IMAGE_SUFFIXES or suffix in VIDEO_SUFFIXES or
                suffix in {".json", ".md", ".txt", ".csv", ".html"} or
                "/" in text or text.startswith("."))
    return True


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


@dataclass(frozen=True)
class Context:
    run: str | None = None
    roots: tuple[Path, ...] = ()
    source_videos: tuple[Path, ...] = ()

    def with_values(self, *, run: str | None = None,
                    roots: Iterable[Path] = (),
                    source_videos: Iterable[Path] = ()) -> "Context":
        return Context(
            run=run or self.run,
            roots=tuple(_dedupe_paths((*self.roots, *roots))),
            source_videos=tuple(_dedupe_paths((*self.source_videos, *source_videos))),
        )


@dataclass(frozen=True)
class MetadataMapping:
    image: Path
    timestamp_ms: int | float
    metadata_path: Path
    frame_id: str | None = None


class MetadataIndex:
    """Small lazy index of nearby capture/frame manifests."""

    def __init__(self, builder: "BundleBuilder") -> None:
        self.builder = builder
        self.by_path: dict[Path, list[MetadataMapping]] = defaultdict(list)
        self.by_id: dict[str, list[MetadataMapping]] = defaultdict(list)
        self.loaded: set[Path] = set()

    def add(self, mapping: MetadataMapping) -> None:
        image = mapping.image.resolve()
        if mapping not in self.by_path[image]:
            self.by_path[image].append(mapping)
        if mapping.frame_id:
            key = str(mapping.frame_id)
            if mapping not in self.by_id[key]:
                self.by_id[key].append(mapping)

    def load(self, path: Path, context: Context) -> None:
        path = path.resolve()
        if path in self.loaded:
            return
        self.loaded.add(path)
        if not path.is_file() or path.suffix.lower() != ".json":
            return
        try:
            payload = _read_json(path)
        except BundleError:
            # A cited manifest must be readable.  An adjacent non-manifest
            # sidecar may be opaque; it is still hashed as immutable evidence.
            if path.name in {"capture.json", "frames.json", "manifest.json"}:
                raise
            return
        for row in self._rows(payload):
            evidence = row.get("evidence")
            timestamp = _row_timestamp(row)
            if evidence is None or timestamp is None:
                continue
            paths = self._resolve_metadata_evidence(evidence, path, context)
            frame_id = row.get("id")
            frame_id = str(frame_id) if frame_id is not None else None
            for image in paths:
                self.add(MetadataMapping(image, timestamp, path, frame_id))

    @staticmethod
    def _rows(value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            if "evidence" in value and any(key in value for key in TIMESTAMP_KEYS):
                yield value
            for child in value.values():
                yield from MetadataIndex._rows(child)
        elif isinstance(value, list):
            for child in value:
                yield from MetadataIndex._rows(child)

    def _resolve_metadata_evidence(self, value: Any, metadata: Path,
                                   context: Context) -> list[Path]:
        if not isinstance(value, str) or not value.strip():
            return []
        text = value.strip().replace("\\", "/")
        if ".." in text.split("/"):
            return []
        raw = Path(text)
        candidates: list[Path] = []
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.append(metadata.parent / raw)
            candidates.extend(root / raw for root in context.roots)
            candidates.extend(self.builder._roots_for_run(context.run))
            candidates.append(self.builder.repo_root / raw)
        return [path.resolve() for path in _dedupe_paths(candidates) if path.is_file()]

    def for_image(self, image: Path, context: Context) -> list[MetadataMapping]:
        return list(self.by_path.get(image.resolve(), ()))

    def for_id(self, frame_id: str, context: Context) -> list[MetadataMapping]:
        mappings = self.by_id.get(frame_id, ())
        roots = tuple((*context.roots, *self.builder._roots_for_run(context.run)))
        if not roots:
            return list(mappings)
        return [mapping for mapping in mappings
                if any(_inside(mapping.metadata_path, root) or
                       _inside(mapping.image, root) for root in roots)]


@dataclass
class FileRecord:
    path: Path
    kinds: set[str] = field(default_factory=set)
    pointers: set[str] = field(default_factory=set)


class BundleBuilder:
    def __init__(self, repo_root: str | Path, *, hash_source_video: bool = False) -> None:
        self.repo_root = Path(repo_root).resolve()
        require(self.repo_root.is_dir(), f"Repository root is missing: {self.repo_root}")
        self.hash_source_video = hash_source_video
        self.run_roots: dict[str, list[Path]] = defaultdict(list)
        self.global_roots: list[Path] = []
        self.run_videos: dict[str, list[Path]] = defaultdict(list)
        self.files: dict[Path, FileRecord] = {}
        self.file_hashes: dict[Path, str] = {}
        self.declared_hash_paths: dict[str, set[Path]] = defaultdict(set)
        self.deferred_hashes: list[tuple[str, str, str]] = []
        self.directories: dict[Path, set[str]] = {}
        self.bindings: dict[tuple[Path, Path, str, int | float | None], dict[str, Any]] = {}
        self.source_videos: dict[Path, dict[str, Any]] = {}
        self.metadata = MetadataIndex(self)
        self.review_documents: list[dict[str, Any]] = []
        self.supporting_documents: list[dict[str, Any]] = []
        self._active_review: Path | None = None

    def _add_root(self, run: str | None, root: Path) -> None:
        root = root.resolve()
        require(_inside(root, self.repo_root), f"Evidence root escapes repository: {root}")
        require(root.is_dir(), f"Evidence root is missing: {root}")
        if root not in self.global_roots:
            self.global_roots.append(root)
        if run:
            if root not in self.run_roots[run]:
                self.run_roots[run].append(root)

    def _roots_for_run(self, run: str | None) -> list[Path]:
        if not run:
            return []
        roots = list(self.run_roots.get(run, ()))
        # The independent-02 control has a longer descriptive key in one
        # review.  Preserve its exact root and also use the recording prefix.
        if run.startswith("independent-02"):
            roots.extend(self.run_roots.get("independent-02", ()))
        return _dedupe_paths(roots)

    def _try_local(self, raw: str, context: Context) -> Path | None:
        value = raw.replace("\\", "/")
        path = Path(value)
        candidates: list[Path]
        if path.is_absolute():
            candidates = [path]
        else:
            candidates = [*(root / path for root in context.roots),
                          *(root / path for root in self._roots_for_run(context.run)),
                          *(root / path for root in self.global_roots),
                          self.repo_root / path]
        for candidate in _dedupe_paths(candidates):
            if candidate.exists():
                return candidate
        return None

    def _resolve_directory(self, raw: str, context: Context, pointer: str) -> Path:
        text = _normal_text(raw, pointer)
        candidate = self._try_local(text, context)
        require(candidate is not None and candidate.is_dir(),
                f"Directory reference is missing at {pointer}: {raw}")
        require(_inside(candidate, self.repo_root),
                f"Directory reference escapes repository at {pointer}: {candidate}")
        return candidate

    def _resolve_source_video(self, raw: str, context: Context, pointer: str) -> Path:
        text = _normal_text(raw, pointer)
        path = Path(text)
        candidates: list[Path] = []
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend(root / path for root in context.roots)
            candidates.extend(root / path for root in self._roots_for_run(context.run))
            candidates.append(self.repo_root / path)
            # A config may preserve only the filename while a run context has
            # already supplied the external source path.
            candidates.extend(video for video in context.source_videos
                             if video.name == path.name)
            candidates.extend(video for video in self.run_videos.get(context.run or "", ())
                             if video.name == path.name)
        for candidate in _dedupe_paths(candidates):
            if candidate.is_file():
                return candidate
        raise BundleError(f"Source video is missing at {pointer}: {raw}")

    def _resolve_file(self, raw: str, key: str, context: Context,
                      pointer: str) -> Path:
        text = _normal_text(raw, pointer)
        if key in {"source_video", "source_video_path"} or Path(text).suffix.lower() in VIDEO_SUFFIXES:
            return self._resolve_source_video(text, context, pointer)
        value = Path(text.replace("\\", "/"))
        candidates: list[Path]
        if value.is_absolute():
            candidates = [value]
        else:
            candidates = [*(root / value for root in context.roots),
                          *(root / value for root in self._roots_for_run(context.run)),
                          self.repo_root / value]
            if self._active_review is not None:
                candidates.append(self._active_review.parent / value)
        existing = [path for path in _dedupe_paths(candidates) if path.is_file()]
        require(existing, f"File reference is missing at {pointer}: {raw}")
        # Context roots are intentionally preferred.  If two roots expose the
        # same relative path, silently choosing one would make the proof
        # ambiguous, so fail unless an explicit root chose it.
        if not value.is_absolute():
            preferred = [path for path in existing
                         if any(_inside(path, root) for root in context.roots)]
            if len(preferred) == 1:
                return preferred[0]
            if len(preferred) > 1:
                raise BundleError(f"Ambiguous file reference at {pointer}: {raw}")
        if len(existing) > 1:
            # A repository-relative path has one canonical interpretation.
            repo_matches = [path for path in existing if _inside(path, self.repo_root)]
            if len(repo_matches) == 1:
                return repo_matches[0]
            raise BundleError(f"Ambiguous file reference at {pointer}: {raw}")
        resolved = existing[0]
        require(_inside(resolved, self.repo_root),
                f"Local reference escapes repository at {pointer}: {resolved}")
        return resolved

    def _context_from_dict(self, context: Context, value: Mapping[str, Any],
                           pointer: str) -> Context:
        run_value = value.get("run", value.get("recording"))
        run = run_value.strip() if isinstance(run_value, str) and run_value.strip() else context.run
        roots = list(context.roots)
        for key in ("evidence_root", "cache_root"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                root = self._resolve_directory(raw, context, _pointer(pointer, key))
                roots.append(root)
                self._add_root(run, root)
        videos = list(context.source_videos)
        raw_video = value.get("source_video")
        if isinstance(raw_video, str) and raw_video.strip():
            try:
                video = self._resolve_source_video(raw_video, context, _pointer(pointer, "source_video"))
            except BundleError:
                video = None
            if video is not None:
                videos.append(video)
                if run and video not in self.run_videos[run]:
                    self.run_videos[run].append(video)
        return context.with_values(run=run, roots=roots, source_videos=videos)

    def _child_context(self, context: Context, key: str, child_key: str | int) -> Context:
        if isinstance(child_key, str):
            if child_key in self.run_roots or child_key.startswith(("v1", "independent-")):
                return context.with_values(run=child_key)
            if key in RUN_MAPPING_KEYS and child_key in {"v1", "independent-01", "independent-02"}:
                return context.with_values(run=child_key)
        return context

    def collect_contexts(self, value: Any, context: Context = Context(),
                         pointer: str = "$") -> None:
        if isinstance(value, dict):
            local = self._context_from_dict(context, value, pointer)
            # Some preserved-input records call the video field ``path``
            # (for example source-video-integrity.json and before-report
            # integrity rows).  Register those paths during the context pass
            # so a sibling semantic source hash can bind before the main walk.
            for key in ("path", "source_video_path"):
                raw_video = value.get(key)
                if (isinstance(raw_video, str) and
                        Path(raw_video.replace("\\", "/")).suffix.lower() in VIDEO_SUFFIXES):
                    try:
                        video = self._resolve_source_video(raw_video, local,
                                                           _pointer(pointer, key))
                    except BundleError:
                        video = None
                    if video is not None:
                        if local.run and video not in self.run_videos[local.run]:
                            self.run_videos[local.run].append(video)
                        local = local.with_values(source_videos=(video,))
            for key, child in value.items():
                child_context = self._child_context(local, key, key)
                self.collect_contexts(child, child_context, _pointer(pointer, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self.collect_contexts(child, context, _pointer(pointer, index))

    def _hash_sibling_values(self, value: Mapping[str, Any], ref_key: str,
                             pointer: str) -> list[tuple[str, str]]:
        expected: list[tuple[str, str]] = []
        for key, raw in value.items():
            if key not in HASH_PATH_KEYS and key not in SOURCE_IDENTITY_HASH_KEYS \
                    and not key.endswith("_source_observations_sha256"):
                continue
            if key in SOURCE_IDENTITY_HASH_KEYS:
                # These may be scalar hashes or a run->hash map; the latter
                # is validated and bound by _source_video_context_refs.
                continue
            declared = _hash_value(key, raw, _pointer(pointer, key))
            if declared is None:
                continue
            path_keys = _hash_path_keys(key)
            if ref_key in path_keys:
                expected.append((key, declared))
        return expected

    def _kind_for(self, key: str, path: Path) -> str:
        if key in {"source_video", "source_video_path"} or path.suffix.lower() in VIDEO_SUFFIXES:
            return "source_video"
        if key in {"source_frame", "source_frame_path"}:
            return "source_frame"
        if key in {"gameplay_crop", "gameplay_crop_path", "gameplay_crops"}:
            return "gameplay_crop"
        if key == "full_frame_evidence":
            return "excluded_auxiliary_provenance"
        if key in {"raw_sidecar", "raw_sidecars"}:
            return "raw_sidecar"
        if key in {"capture_manifest", "frame_manifest", "source_identity_manifest"}:
            return "capture_manifest"
        if key in {"replay_config", "replay_configs"}:
            return "replay_config"
        if key in {"report", "reports", "inventory", "inventory_files", "checklist",
                   "prior_boundary_report", "prior_source_findings", "canonical_inventory",
                   "frozen_source_selection", "boundary_inventory"}:
            return "supporting_document"
        return "evidence" if path.suffix.lower() in IMAGE_SUFFIXES else "supporting_file"

    def _record_file(self, path: Path, kind: str, pointer: str,
                     *, include_external: bool = False,
                     expected: Iterable[str] = ()) -> str | None:
        path = path.resolve()
        if include_external:
            require(path.is_file(), f"Source file is missing: {path}")
        else:
            require(_inside(path, self.repo_root), f"Local file escapes repository: {path}")
            require(path.is_file(), f"Local file is missing: {path}")
        record = self.files.setdefault(path, FileRecord(path))
        record.kinds.add(kind)
        record.pointers.add(pointer)
        expected_values = list(expected)
        if path not in self.file_hashes:
            # External source videos are intentionally skipped unless the
            # caller requests the one-pass hash operation.
            if include_external and not self.hash_source_video:
                return None
            self.file_hashes[path] = digest(path)
        actual = self.file_hashes[path]
        for declared in expected_values:
            require(actual == declared,
                    f"Hash mismatch at {pointer}: {path} has {actual}, expected {declared}")
            self.declared_hash_paths[declared].add(path)
        return actual

    def _source_video_hashes(self, value: Mapping[str, Any], pointer: str) -> list[str]:
        result: list[str] = []
        for key in SOURCE_IDENTITY_HASH_KEYS:
            if key in value:
                raw = value[key]
                if isinstance(raw, dict):
                    for run, item in raw.items():
                        declared = _hash_value(key, item,
                                               _pointer(_pointer(pointer, key), str(run)))
                        if declared:
                            result.append(declared)
                else:
                    declared = _hash_value(key, raw, _pointer(pointer, key))
                    if declared:
                        result.append(declared)
        return result

    def _source_video_hash_pairs(self, value: Mapping[str, Any], pointer: str
                                 ) -> list[tuple[str | None, str]]:
        pairs: list[tuple[str | None, str]] = []
        for key in SOURCE_IDENTITY_HASH_KEYS:
            if key not in value:
                continue
            raw = value[key]
            if isinstance(raw, dict):
                for run, item in raw.items():
                    declared = _hash_value(key, item,
                                           _pointer(_pointer(pointer, key), str(run)))
                    if declared:
                        pairs.append((str(run), declared))
            else:
                declared = _hash_value(key, raw, _pointer(pointer, key))
                if declared:
                    pairs.append((None, declared))
        return pairs

    def _register_source_video(self, path: Path, context: Context,
                               pointer: str, declared: Iterable[str]) -> None:
        path = path.resolve()
        values = list(dict.fromkeys(declared))
        info = self.source_videos.setdefault(path, {
            "path": str(path),
            "runs": [],
            "declared_sha256": [],
            "verification": "not_requested",
        })
        combined_hashes = set(info["declared_sha256"]) | set(values)
        require(len(combined_hashes) <= 1,
                f"Conflicting source-video hashes for {path} at {pointer}")
        if context.run and context.run not in info["runs"]:
            info["runs"].append(context.run)
        for expected in values:
            if expected not in info["declared_sha256"]:
                info["declared_sha256"].append(expected)
        if self.hash_source_video:
            actual = self._record_file(path, "source_video", pointer, include_external=True,
                                       expected=values)
            info["sha256"] = actual
            info["verification"] = "streamed_and_verified"
        else:
            info["verification"] = "not_requested"

    def _source_video_context_refs(self, value: Mapping[str, Any], context: Context,
                                   pointer: str) -> None:
        pairs = self._source_video_hash_pairs(value, pointer)
        if not pairs:
            return
        for run_hint, declared in pairs:
            target_run = run_hint or context.run
            paths = (self._run_videos(target_run) if run_hint else list(context.source_videos))
            if not paths and target_run:
                paths = self._run_videos(target_run)
            require(paths, f"Source-video hash has no bound source path at {pointer}")
            target_context = context.with_values(run=target_run)
            for path in paths:
                self._register_source_video(path, target_context, pointer, [declared])

    def _run_videos(self, run: str | None) -> list[Path]:
        if not run:
            return []
        paths = list(self.run_videos.get(run, ()))
        if run.startswith("independent-02"):
            paths.extend(self.run_videos.get("independent-02", ()))
        return _dedupe_paths(paths)

    def _implicit_sidecars(self, image: Path, context: Context, pointer: str) -> None:
        # The plain OCR sidecar and versioned source sidecars are useful to
        # reproduce a review.  Totals/performance derivatives are not needed
        # for source identity and would turn this into an unbounded directory
        # copy.
        for suffix in (".json", ".v2.json", ".v3.json"):
            sidecar = image.with_suffix(suffix)
            if sidecar.is_file() and _inside(sidecar, self.repo_root):
                self._record_file(sidecar, "raw_sidecar", f"{pointer} [implicit sidecar]")
                self._load_sidecar(sidecar, image, context)

    def _load_sidecar(self, sidecar: Path, image: Path, context: Context) -> None:
        if sidecar in self.metadata.loaded:
            return
        self.metadata.loaded.add(sidecar)
        try:
            payload = _read_json(sidecar)
        except BundleError:
            return
        if not isinstance(payload, dict):
            return
        timestamp = _row_timestamp(payload)
        if timestamp is None:
            return
        evidence = payload.get("evidence")
        matched = False
        if isinstance(evidence, str):
            for candidate in self.metadata._resolve_metadata_evidence(evidence, sidecar, context):
                if candidate == image.resolve():
                    matched = True
        # A sidecar with the exact image stem is explicit metadata even when
        # an old sidecar omitted its ``evidence`` field.
        if not matched and sidecar.stem.split(".")[0] == image.stem:
            matched = True
        if matched:
            self.metadata.add(MetadataMapping(image.resolve(), timestamp, sidecar, None))

    def _nearby_manifests(self, image: Path, context: Context) -> list[Path]:
        paths: list[Path] = []
        current = image.parent.resolve()
        stop_roots = [self.repo_root, *context.roots, *self._roots_for_run(context.run)]
        # Walk at most eight levels, enough for run/part/frames and native
        # review directories.  The filesystem root and repository root bound
        # the search even when a caller supplies a deeply nested image.
        for _ in range(9):
            for name in ("capture.json", "frames.json", "manifest.json"):
                candidate = current / name
                if candidate.is_file() and _inside(candidate, self.repo_root):
                    paths.append(candidate)
            if current == self.repo_root or current.parent == current:
                break
            current = current.parent
        # If an explicit evidence root is below the repository, do not climb
        # from it into a sibling recording after the relevant run manifest.
        return _dedupe_paths(paths)

    def _frame_id_candidates(self, image: Path) -> list[str]:
        stem = image.stem
        candidates = [stem]
        # Raw frame evidence uses ``.../part-002/frames/000322.jpg`` while
        # gameplay crops use ``part-002-frame-000322.png``.
        parent = image.parent.name
        if parent == "frames" and image.parent.parent.name:
            candidates.append(f"{image.parent.parent.name}-{stem}")
            candidates.append(f"frame-{stem}")
        if stem.startswith("part-") and "-frame-" in stem:
            candidates.append(stem.replace("part-", "", 1))
        if stem.startswith("frame-"):
            candidates.append(stem[len("frame-"):])
        return list(dict.fromkeys(candidates))

    def _ensure_metadata(self, image: Path, context: Context, pointer: str) -> None:
        self._implicit_sidecars(image, context, pointer)
        for manifest in self._nearby_manifests(image, context):
            self._record_file(manifest, "capture_manifest", f"{pointer} [nearest manifest]")
            self.metadata.load(manifest, context)

    def _timestamp_binding(self, image: Path, timestamp: int | float,
                           context: Context, pointer: str,
                           alternatives: Sequence[int | float] = ()) -> MetadataMapping:
        self._ensure_metadata(image, context, pointer)
        mappings = self.metadata.for_image(image, context)
        if not mappings:
            for frame_id in self._frame_id_candidates(image):
                mappings.extend(self.metadata.for_id(frame_id, context))
        # Keep only mappings whose metadata and image are in the relevant run
        # when a context root is available.
        mappings = list(dict.fromkeys(mappings))
        require(mappings,
                f"Timestamp is not bound to capture/sidecar metadata at {pointer}: {image}")
        exact = [mapping for mapping in mappings if mapping.timestamp_ms == timestamp]
        if not exact:
            alternate_timestamps = {
                mapping.timestamp_ms for mapping in mappings
                if mapping.timestamp_ms in alternatives
            }
            if len(alternate_timestamps) == 1:
                wanted = next(iter(alternate_timestamps))
                return next(mapping for mapping in mappings
                            if mapping.timestamp_ms == wanted)
            observed = sorted({mapping.timestamp_ms for mapping in mappings})
            raise BundleError(
                f"Timestamp mismatch at {pointer}: {image} declares {timestamp}, "
                f"metadata records {observed}")
        return exact[0]

    def _source_frame_candidate(self, image: Path, timestamp: int | float,
                                expected: str, context: Context, pointer: str) -> Path:
        self._ensure_metadata(image, context, pointer)
        candidates: list[Path] = []
        for mapping in self.metadata.for_image(image, context):
            if mapping.timestamp_ms == timestamp and mapping.image.suffix.lower() in {".jpg", ".jpeg"}:
                candidates.append(mapping.image)
        for frame_id in self._frame_id_candidates(image):
            for mapping in self.metadata.for_id(frame_id, context):
                if mapping.timestamp_ms == timestamp and mapping.image.suffix.lower() in {".jpg", ".jpeg"}:
                    candidates.append(mapping.image)
        candidates = _dedupe_paths(candidates)
        require(candidates,
                f"Source-frame hash has no raw frame mapping at {pointer}: {image}")
        matches = [candidate for candidate in candidates if digest(candidate) == expected]
        require(matches,
                f"Source-frame hash does not match a raw frame at {pointer}: {image}")
        return matches[0]

    def _record_binding(self, path: Path, kind: str, pointer: str,
                        timestamp: int | float | None, declared: Iterable[str],
                        actual: str | None, *, metadata: MetadataMapping | None = None,
                        admissible: bool = True) -> dict[str, Any]:
        key = (self._active_review or Path("<unknown>"), path.resolve(), kind, timestamp)
        row = self.bindings.setdefault(key, {
            "review": str(self._active_review) if self._active_review else None,
            "path": str(path.resolve()),
            "kind": kind,
            "json_pointers": [],
            "timestamp_ms": timestamp,
            "declared_sha256": [],
            "sha256": actual,
            "admissible_gameplay_evidence": admissible,
        })
        if pointer not in row["json_pointers"]:
            row["json_pointers"].append(pointer)
        for value in declared:
            if value not in row["declared_sha256"]:
                row["declared_sha256"].append(value)
        if metadata is not None:
            row["timestamp_verification"] = {
                "status": "bound",
                "metadata_path": str(metadata.metadata_path.resolve()),
                "metadata_timestamp_ms": metadata.timestamp_ms,
                "frame_id": metadata.frame_id,
            }
        return row

    def _process_reference(self, raw: str, key: str, value: Mapping[str, Any],
                           context: Context, pointer: str,
                           timestamp_override: int | float | None | object = _TIMESTAMP_UNSET,
                           timestamp_candidates: Sequence[int | float] = (),
                           inherited_timestamp: int | float | None = None,
                           inherited_timestamp_pointer: str | None = None,
                           ) -> None:
        path = self._resolve_file(raw, key, context, pointer)
        kind = self._kind_for(key, path)
        is_source_video = kind == "source_video"
        declared_pairs = self._hash_sibling_values(value, key, pointer)
        declared = [hash_value for _, hash_value in declared_pairs]
        # A generic ``sha256`` next to a source_video string is a direct video
        # hash; source_video_sha256/source_sha256 are handled below.
        if is_source_video:
            generic = [hash_value for hash_key, hash_value in declared_pairs
                       if hash_key == "sha256"]
            self._register_source_video(path, context, pointer,
                                        [*generic, *self._source_video_hashes(value, pointer)])
            return
        actual = self._record_file(path, kind, pointer, expected=declared)
        image = path.suffix.lower() in IMAGE_SUFFIXES
        if image:
            timestamp = (_row_timestamp(value) if timestamp_override is _TIMESTAMP_UNSET
                          else timestamp_override)
            if timestamp is None and timestamp_override is _TIMESTAMP_UNSET:
                timestamp = inherited_timestamp
        else:
            timestamp = None
        metadata = None
        declared_timestamp = timestamp
        if image:
            self._ensure_metadata(path, context, pointer)
            if timestamp is not None:
                metadata = self._timestamp_binding(path, timestamp, context, pointer,
                                                   alternatives=timestamp_candidates)
                timestamp = metadata.timestamp_ms
        binding = self._record_binding(path, kind, pointer, timestamp, declared, actual,
                                       metadata=metadata,
                                       admissible=(kind != "excluded_auxiliary_provenance"))
        if (inherited_timestamp is not None and timestamp == inherited_timestamp and
                inherited_timestamp_pointer is not None and metadata is not None):
            binding["timestamp_verification"]["inherited_from_record"] = inherited_timestamp_pointer
        if (declared_timestamp is not None and metadata is not None and
                declared_timestamp != metadata.timestamp_ms):
            binding["timestamp_verification"].update({
                "status": "bound_by_timestamp_set",
                "declared_timestamp_ms": declared_timestamp,
                "declared_timestamp_candidates_ms": list(timestamp_candidates),
            })

        # source_frame_sha256 usually sits beside a gameplay crop path.  It is
        # a digest of the raw native frame, not of the crop, so bind it through
        # the timestamped frame metadata and include that raw frame separately.
        source_frame_hash = _hash_value("source_frame_sha256", value.get("source_frame_sha256"),
                                       _pointer(pointer, "source_frame_sha256")) \
            if "source_frame_sha256" in value else None
        if source_frame_hash and (image or key in {"source_frame", "source_frame_path", "gameplay_crop", "gameplay_crop_path", "evidence", "evidence_path"}):
            direct = key in {"source_frame", "source_frame_path"} or path.suffix.lower() in {".jpg", ".jpeg"}
            if direct:
                require(actual == source_frame_hash,
                        f"Source-frame hash mismatch at {pointer}: {path}")
            else:
                require(timestamp is not None,
                        f"Source-frame hash is unbound without a source timestamp at {pointer}")
                raw_frame = self._source_frame_candidate(path, timestamp, source_frame_hash,
                                                         context, pointer)
                raw_actual = self._record_file(raw_frame, "raw_source_frame", pointer,
                                               expected=[source_frame_hash])
                self._record_binding(raw_frame, "raw_source_frame", pointer, timestamp,
                                     [source_frame_hash], raw_actual, metadata=metadata)

    def _process_image_hash_map(self, value: Mapping[str, Any], context: Context,
                                 pointer: str) -> None:
        for raw_path, raw_hash in value.items():
            declared = _hash_value("sha256", raw_hash, _pointer(pointer, str(raw_path)))
            if declared is None:
                continue
            require(isinstance(raw_path, str) and _looks_like_path(raw_path, "path"),
                    f"Invalid image hash path at {pointer}: {raw_path}")
            path_pointer = _pointer(pointer, str(raw_path))
            path = self._resolve_file(raw_path, "path", context, path_pointer)
            actual = self._record_file(path, "evidence", path_pointer, expected=[declared])
            self._record_binding(path, "evidence", path_pointer, None, [declared], actual)

    def _resolve_deferred_hashes(self) -> None:
        """Bind hash-only proofs to one explicit path/hash pair in the inputs."""
        for key, declared, pointer in self.deferred_hashes:
            candidates = sorted(self.declared_hash_paths.get(declared, ()),
                                key=lambda path: str(path).lower())
            require(len(candidates) == 1,
                    f"Hash-only reference is not uniquely bound at {pointer}: "
                    f"{declared} matches {len(candidates)} files")
            path = candidates[0]
            actual = self.file_hashes.get(path)
            require(actual == declared,
                    f"Hash-only reference is stale at {pointer}: {path}")
            self._record_binding(path, "supporting_file", pointer, None,
                                 [declared], actual)

    def walk(self, value: Any, context: Context = Context(), pointer: str = "$",
             inherited_timestamp: int | float | None = None,
             inherited_timestamp_pointer: str | None = None) -> None:
        if isinstance(value, dict):
            local = self._context_from_dict(context, value, pointer)
            self._source_video_context_refs(value, local, pointer)
            for key, child in value.items():
                if not isinstance(key, str) or not key.endswith("sha256"):
                    continue
                # image_sha256 is a path->hash map handled as a dedicated
                # bounded reference shape below.  Source identity hashes are
                # bound through the run's source-video context above.
                if key == "image_sha256" or key in SOURCE_IDENTITY_HASH_KEYS:
                    continue
                if key == "source_frame_sha256" and _has_path_value(
                        value, ("path", "evidence", "source_evidence",
                                "gameplay_crop", "gameplay_crop_path",
                                "source_frame", "source_frame_path")):
                    # A crop's source_frame_sha256 is resolved by the
                    # timestamped raw-frame binding in _process_reference.
                    continue
                path_keys = _hash_path_keys(key)
                if key.endswith("_source_observations_sha256") and path_keys \
                        and not _has_path_value(value, path_keys):
                    declared = _hash_value(key, child, _pointer(pointer, key))
                    require(declared is not None,
                            f"Invalid hash-only source reference at {_pointer(pointer, key)}")
                    self.deferred_hashes.append((key, declared,
                                                  _pointer(pointer, key)))
                    continue
                require(path_keys and _has_path_value(value, path_keys),
                        f"Hash has no bound path reference at {_pointer(pointer, key)}")
            for key, child in value.items():
                child_pointer = _pointer(pointer, key)
                if key in DIRECTORY_REFERENCE_KEYS:
                    values = child if isinstance(child, list) else [child]
                    for index, raw in enumerate(values):
                        if isinstance(raw, str) and raw.strip():
                            directory = self._resolve_directory(raw, local,
                                                                child_pointer if not isinstance(child, list)
                                                                else _pointer(child_pointer, index))
                            self.directories.setdefault(directory, set()).add(key)
                    continue
                if key == "image_sha256" and isinstance(child, dict):
                    self._process_image_hash_map(child, local, child_pointer)
                    continue
                if key in FILE_REFERENCE_KEYS:
                    values = child if isinstance(child, list) else [child]
                    aligned = (_aligned_timestamps(value, key, len(values))
                               if isinstance(child, list) else None)
                    # A scalar row timestamp identifies one observation, not
                    # every image in an aggregate evidence list.  Apply it to
                    # a list item only when the review supplies aligned
                    # per-item timestamps; otherwise leave each item's image
                    # timestamp unassigned and retain the path/hash evidence.
                    aggregate_without_alignment = (
                        isinstance(child, list) and aligned is None and
                        _row_timestamp(value) is not None and
                        all(isinstance(item, str) for item in values)
                    )
                    for index, raw in enumerate(values):
                        item_pointer = child_pointer if not isinstance(child, list) else _pointer(child_pointer, index)
                        if isinstance(raw, str) and _looks_like_path(raw, key):
                            if aligned is not None:
                                timestamp_override = aligned[index]
                            elif aggregate_without_alignment:
                                timestamp_override = None
                            else:
                                timestamp_override = _TIMESTAMP_UNSET
                            self._process_reference(raw, key, value, local, item_pointer,
                                                    timestamp_override=timestamp_override,
                                                    timestamp_candidates=(aligned or ()),
                                                    inherited_timestamp=inherited_timestamp,
                                                    inherited_timestamp_pointer=inherited_timestamp_pointer)
                child_context = self._child_context(local, key, key)
                child_inherited_timestamp = None
                child_inherited_pointer = None
                row_timestamp = _row_timestamp(value)
                if (key == "source_evidence" and isinstance(child, dict) and
                        row_timestamp is not None and
                        _has_single_image_reference(child) and
                        _row_timestamp(child) is None):
                    child_inherited_timestamp = row_timestamp
                    child_inherited_pointer = pointer
                self.walk(child, child_context, child_pointer,
                          inherited_timestamp=child_inherited_timestamp,
                          inherited_timestamp_pointer=child_inherited_pointer)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                self.walk(child, context, _pointer(pointer, index))

    def _load_target(self, path: Path, *, supporting: bool) -> Any:
        path = path.resolve()
        require(_inside(path, self.repo_root), f"Review path escapes repository: {path}")
        require(path.is_file(), f"Review file is missing: {path}")
        payload = _read_json(path)
        require(isinstance(payload, dict), f"Review JSON must be an object: {path}")
        actual = self._record_file(path, "supporting_document" if supporting else "review_document",
                                   str(path))
        entry = {"path": str(path), "sha256": actual}
        if isinstance(payload.get("schema_version"), str):
            entry["schema_version"] = payload["schema_version"]
        elif not supporting:
            # Every target review in this gate is versioned.  Refusing an
            # unversioned target prevents a generic report from being frozen
            # accidentally as source evidence.
            raise BundleError(f"Review document lacks schema_version: {path}")
        if supporting:
            self.supporting_documents.append(entry)
        else:
            self.review_documents.append(entry)
        return payload

    def _initial_pass(self, documents: Sequence[tuple[Path, bool]]) -> list[tuple[Path, bool, Any]]:
        loaded: list[tuple[Path, bool, Any]] = []
        for path, supporting in documents:
            payload = self._load_target(path, supporting=supporting)
            loaded.append((path.resolve(), supporting, payload))
        for path, _supporting, payload in loaded:
            self.collect_contexts(payload, Context(), "$" )
        return loaded

    def build(self, review_paths: Sequence[str | Path], output: str | Path,
              *, supporting_paths: Sequence[str | Path] = (),
              config_paths: Sequence[str | Path] = ()) -> dict[str, Any]:
        review_paths = [Path(path) for path in ([review_paths] if isinstance(review_paths, (str, Path)) else review_paths)]
        supporting = [Path(path) for path in (*supporting_paths, *config_paths)]
        all_paths: list[tuple[Path, bool]] = []
        seen: set[Path] = set()
        for path in [*review_paths, *supporting]:
            resolved = path.resolve()
            require(resolved not in seen, f"Duplicate bundle input: {resolved}")
            seen.add(resolved)
            all_paths.append((resolved, resolved in {p.resolve() for p in supporting}))
        loaded = self._initial_pass(all_paths)
        # Review files are already hashed/recorded; references in every
        # document are now resolved with all run roots and source videos known.
        for path, is_supporting, payload in loaded:
            self._active_review = path if not is_supporting else None
            self.walk(payload)
        self._active_review = None
        self._resolve_deferred_hashes()
        output_path = Path(output).resolve()
        require(not output_path.exists(), f"Bundle output already exists: {output_path}")
        require(output_path.parent.is_dir() or not output_path.parent.exists(),
                f"Bundle output parent is invalid: {output_path.parent}")

        immutable = {str(path): self.file_hashes[path]
                     for path in sorted(self.file_hashes, key=lambda p: str(p).lower())
                     if path in self.files}
        # Local files are always hashed; source videos appear in this map only
        # when their optional one-pass verification was requested.
        for path, info in self.source_videos.items():
            if path in self.file_hashes and path not in immutable:
                immutable[str(path)] = self.file_hashes[path]
            if not info["declared_sha256"] and path in self.file_hashes:
                info["declared_sha256"] = [self.file_hashes[path]]
        document = {
            "schema_version": SCHEMA,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "review_documents": sorted(self.review_documents, key=lambda row: row["path"]),
            "supporting_documents": sorted(self.supporting_documents, key=lambda row: row["path"]),
            "immutable_files_sha256": immutable,
            "directories": [
                {"path": str(path), "roles": sorted(roles)}
                for path, roles in sorted(self.directories.items(), key=lambda item: str(item[0]).lower())
            ],
            "evidence_bindings": sorted(self.bindings.values(),
                                        key=lambda row: (row["review"] or "", row["path"],
                                                         row["kind"], str(row["timestamp_ms"]))),
            "source_videos": sorted(self.source_videos.values(), key=lambda row: row["path"]),
            "options": {"hash_source_video": self.hash_source_video},
            "limitation": "File integrity and source-timestamp binding only; pixel correctness remains a manual review gate.",
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output_path.open("x", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2)
                stream.write("\n")
        except FileExistsError as exc:
            raise BundleError(f"Bundle output already exists: {output_path}") from exc
        return document


def build_bundle(review_paths: Sequence[str | Path] | str | Path,
                 output: str | Path, *, repo_root: str | Path = ROOT,
                 supporting_paths: Sequence[str | Path] = (),
                 config_paths: Sequence[str | Path] = (),
                 hash_source_video: bool = False) -> dict[str, Any]:
    """Build and write a source-review dependency manifest.

    ``supporting_paths`` is for preserved proof/checkpoint documents such as
    ``source-video-integrity.json``.  ``config_paths`` is an alias kept for
    callers that want to name replay configs separately; both are traversed
    and included in the immutable file map.
    """
    builder = BundleBuilder(repo_root, hash_source_video=hash_source_video)
    return builder.build(review_paths, output, supporting_paths=supporting_paths,
                         config_paths=config_paths)


def verify_bundle(path: str | Path) -> dict[str, Any]:
    """Verify every file hash and structural binding in a bundle manifest."""
    manifest_path = Path(path).resolve()
    require(manifest_path.is_file(), f"Bundle manifest is missing: {manifest_path}")
    manifest = _read_json(manifest_path)
    require(isinstance(manifest, dict) and manifest.get("schema_version") == SCHEMA,
            f"Unsupported bundle schema: {manifest_path}")
    immutable = manifest.get("immutable_files_sha256")
    require(isinstance(immutable, dict) and immutable, "Empty immutable_files_sha256")
    for raw_path, expected in immutable.items():
        require(isinstance(raw_path, str) and isinstance(expected, str) and SHA256_RE.fullmatch(expected),
                f"Invalid immutable file entry: {raw_path}")
        file_path = Path(raw_path).resolve()
        require(file_path.is_file(), f"Frozen file is missing: {file_path}")
        actual = digest(file_path)
        require(actual == expected.lower(), f"Frozen file changed: {file_path}")
    for section in ("review_documents", "supporting_documents"):
        for row in manifest.get(section, []):
            require(isinstance(row, dict), f"Invalid {section} row")
            file_path = str(Path(row["path"]).resolve())
            require(immutable.get(file_path) == row.get("sha256"),
                    f"{section} is not bound by immutable_files_sha256: {file_path}")
    source_videos = manifest.get("source_videos")
    require(isinstance(source_videos, list), "Invalid source_videos section")
    for row in source_videos:
        require(isinstance(row, dict), "Invalid source video row")
        raw_path = row.get("path")
        require(isinstance(raw_path, str) and raw_path.strip(),
                "Source video row has no path")
        video_path = str(Path(raw_path).resolve())
        require(Path(video_path).is_file(), f"Source video is missing: {video_path}")
        declared = row.get("declared_sha256", [])
        require(isinstance(declared, list) and all(
            isinstance(value, str) and SHA256_RE.fullmatch(value) for value in declared
        ), f"Invalid source video hashes: {video_path}")
        verification = row.get("verification")
        require(verification in {"not_requested", "streamed_and_verified"},
                f"Invalid source video verification: {video_path}")
        if verification == "streamed_and_verified":
            actual = immutable.get(video_path)
            require(isinstance(actual, str),
                    f"Verified source video is not immutable: {video_path}")
            require(row.get("sha256") == actual,
                    f"Verified source video hash is stale: {video_path}")
            require(actual in [value.lower() for value in declared],
                    f"Verified source video hash is not declared: {video_path}")
        else:
            require(row.get("sha256") is None,
                    f"Unrequested source video has a computed hash: {video_path}")
    for row in manifest.get("evidence_bindings", []):
        require(isinstance(row, dict), "Invalid evidence binding")
        file_path = str(Path(row["path"]).resolve())
        require(file_path in immutable, f"Evidence binding is not immutable: {file_path}")
        require(row.get("sha256") == immutable[file_path],
                f"Evidence binding hash is stale: {file_path}")
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_paths", nargs="+", metavar="REVIEW_JSON",
                        help="targeted review JSON documents")
    parser.add_argument("--supporting", action="append", default=[], metavar="JSON",
                        help="preserved supporting proof/checkpoint JSON (repeatable)")
    parser.add_argument("--config", action="append", default=[], metavar="JSON",
                        help="replay config JSON (repeatable; included as supporting input)")
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--output", required=True)
    parser.add_argument("--hash-source-video", action="store_true",
                        help="stream each unique external source video once and verify its declared hash")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        build_bundle(args.review_paths, args.output, repo_root=args.repo_root,
                     supporting_paths=args.supporting, config_paths=args.config,
                     hash_source_video=args.hash_source_video)
    except BundleError as exc:
        _parser().error(str(exc))
    print(f"Source-review bundle: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
