"""Prepare source-bound badge sidecars for a dense training inspection.

The ordinary replay cache stores one OCR record per source frame.  Dense
training inspections have their own ``training-inspection.json`` namespace,
so a sidecar generated for a base ``neural`` row cannot be reused there.  This
module provides the bounded, source-driven preparation pass for that namespace.

The pass discovers candidates from the immutable training-result header/grid
and the decoded gameplay pixels.  It never uses an amount, a balance, a
timestamp allow-list, or a report label to select a crop.  The output contains
only source-bound sidecars and a fragment that can be registered by copying
the sidecar directory and adding its ``entries`` list to the inspection JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Mapping

from .pipeline import PipelineError
from .training_badge_localization import (
    build_sidecar,
    discover_training_badge_crops,
    file_fingerprint,
)
from .vision import NeuralReader


SCHEMA = "tracen-replay/training-inspection-localized-sidecars-v1"
VERSION = 1
DEFAULT_MAX_CANDIDATES = 256
DEFAULT_MAX_ROWS = None

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _json(path: Path, field: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"{field} is not readable JSON: {path}") from exc


def _relative_path(root: Path, path: Path, field: str) -> str:
    """Return a checked root-relative POSIX path for a registration record."""

    root = root.resolve()
    try:
        relative = path.resolve().relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise PipelineError(f"{field} leaves the inspection root: {path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise PipelineError(f"{field} is not a safe relative path: {path}")
    return PurePosixPath(*relative.parts).as_posix()


def _safe_declared_relative(value: Any, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise PipelineError(f"{field} is not a relative path.")
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
        raise PipelineError(f"{field} leaves the inspection root.")
    return posix


def _resolve_declared(root: Path, value: Any, field: str) -> Path:
    relative = _safe_declared_relative(value, field)
    path = root.joinpath(*relative.parts)
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise PipelineError(f"{field} leaves the inspection root.") from exc
    cursor = root.resolve()
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PipelineError(f"{field} contains a reparse point.")
    return path


def _raw_path(root: Path, evidence: Path) -> Path:
    path = evidence.with_suffix(".v2.json")
    if not path.is_file():
        path = evidence.with_suffix(".json")
    if not path.is_file():
        raise PipelineError(f"Training inspection raw observation is missing: {evidence}")
    return path


def _frame_for_raw(root: Path, evidence: Path, raw: Mapping[str, Any]) -> tuple[dict[str, Any], Path, str]:
    """Resolve and verify the source frame referenced by one dense raw row."""

    frames_path = evidence.parent / "frames.json"
    frames = _json(frames_path, "Training inspection frame manifest")
    if not isinstance(frames, list):
        raise PipelineError(f"Training inspection frame manifest is not an array: {frames_path}")
    matches = [
        item
        for item in frames
        if isinstance(item, Mapping) and item.get("id") == evidence.stem
    ]
    if len(matches) != 1:
        raise PipelineError(f"Training inspection source frame is not unique: {evidence}")
    frame = dict(matches[0])
    timestamp = raw.get("source_timestamp_ms")
    if frame.get("source_timestamp_ms") != timestamp:
        raise PipelineError(f"Training inspection source frame timestamp mismatch: {evidence}")
    frame_evidence = frame.get("evidence")
    if not isinstance(frame_evidence, str) or not frame_evidence.strip():
        raise PipelineError(f"Training inspection source frame evidence is missing: {evidence}")
    source_frame = _resolve_declared(evidence.parent, frame_evidence, "source frame evidence")
    expected_hash = raw.get("source_frame_sha256")
    if not isinstance(expected_hash, str) or _SHA256_RE.fullmatch(expected_hash) is None:
        raise PipelineError(f"Training inspection source frame hash is missing: {evidence}")
    if file_fingerprint(source_frame) != expected_hash:
        raise PipelineError(f"Training inspection source frame changed: {source_frame}")
    root_relative = _relative_path(root, source_frame, "source frame evidence")
    return frame, source_frame, root_relative


def _candidate_key(raw: Mapping[str, Any], discovery: Mapping[str, Any]) -> tuple[Any, ...]:
    """Deduplicate repeated references to one physical source frame only."""

    geometry = tuple(
        sorted(
            (
                item.get("field"),
                tuple(item.get("box", ())),
                tuple(item.get("component_box", ())),
            )
            for item in discovery.get("observations", ())
            if isinstance(item, Mapping)
        )
    )
    return (
        raw.get("source_frame_sha256"),
        raw.get("gameplay_sha256"),
        geometry,
    )


def discover_candidates(
    inspection_root: str | Path,
    *,
    inspection: Mapping[str, Any] | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_rows: int | None = DEFAULT_MAX_ROWS,
) -> dict[str, Any]:
    """Discover bounded source-pixel candidates without running OCR.

    The returned ``candidates`` contain paths and immutable raw metadata, not
    numeric values from the inspection facts.  ``max_rows`` bounds image
    decoding for a preparation run; ``None`` means scan all declared rows.
    ``max_candidates`` bounds the subsequent OCR requests.
    """

    root = Path(inspection_root).resolve()
    if not root.is_dir():
        raise PipelineError(f"Training inspection root is missing: {root}")
    payload = dict(inspection) if inspection is not None else _json(
        root / "training-inspection.json", "Training inspection manifest"
    )
    if not isinstance(payload.get("readings"), list):
        raise PipelineError("Training inspection readings must be an array.")
    source_sha = payload.get("source_sha256")
    if not isinstance(source_sha, str) or _SHA256_RE.fullmatch(source_sha) is None:
        raise PipelineError("Training inspection source identity is missing.")
    if type(max_candidates) is not int or not 1 <= max_candidates <= 10000:
        raise ValueError("max_candidates must be between 1 and 10000.")
    if max_rows is not None and (type(max_rows) is not int or not 1 <= max_rows <= 100000):
        raise ValueError("max_rows must be None or between 1 and 100000.")

    stats: dict[str, Any] = {
        "rows_declared": len(payload["readings"]),
        "rows_scanned": 0,
        "training_result_rows": 0,
        "image_candidates": 0,
        "unique_image_candidates": 0,
        "deduplicated_rows": 0,
        "pending_candidates": 0,
        "skipped_rows": 0,
        "rejections": {},
    }
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def reject(reason: str) -> None:
        stats["rejections"][reason] = stats["rejections"].get(reason, 0) + 1

    for index, row in enumerate(payload["readings"]):
        if max_rows is not None and stats["rows_scanned"] >= max_rows:
            break
        stats["rows_scanned"] += 1
        if not isinstance(row, Mapping):
            stats["skipped_rows"] += 1
            reject("row_not_object")
            continue
        if row.get("screen") != "training_result":
            stats["skipped_rows"] += 1
            reject("screen_not_training_result")
            continue
        stats["training_result_rows"] += 1
        evidence_value = row.get("evidence")
        try:
            evidence = _resolve_declared(root, evidence_value, "reading evidence")
            raw_path = _raw_path(root, evidence)
            raw = _json(raw_path, "Training inspection raw observation")
            if not isinstance(raw, Mapping):
                raise PipelineError("Training inspection raw observation is not an object.")
            if (
                raw.get("source_sha256") != source_sha
                or raw.get("source_timestamp_ms") != row.get("source_timestamp_ms")
                or raw.get("evidence") != evidence_value
            ):
                raise PipelineError("Training inspection raw source identity disagrees with its row.")
            header = raw.get("header")
            if not isinstance(header, str) or not header.strip().casefold().startswith("training"):
                reject("training_header_not_proven")
                continue
            if raw.get("result_grid") is not True:
                reject("training_result_grid_not_proven")
                continue
            from PIL import Image

            with Image.open(evidence) as image:
                pane = image.convert("RGB")
            discovery = discover_training_badge_crops(
                pane,
                header=header,
                result_grid=True,
                screen="training_result",
            )
            if discovery.get("status") != "candidate":
                reject(";".join(sorted(set(map(str, discovery.get("rejections", {}).values())))) or "no_badge_component")
                continue
            stats["image_candidates"] += 1
            key = _candidate_key(raw, discovery)
            if key in seen:
                stats["deduplicated_rows"] += 1
                continue
            seen.add(key)
            frame, source_frame, source_frame_evidence = _frame_for_raw(root, evidence, raw)
            stats["unique_image_candidates"] += 1
            if len(candidates) >= max_candidates:
                stats["pending_candidates"] += 1
                stats["candidate_limit_reached"] = True
                continue
            candidates.append(
                {
                    "row_index": index,
                    "source_timestamp_ms": raw.get("source_timestamp_ms"),
                    "evidence": str(evidence_value).replace("\\", "/"),
                    "raw_path": _relative_path(root, raw_path, "raw observation"),
                    "source_frame": source_frame_evidence,
                    "source_frame_id": frame.get("id"),
                    "source_frame_sha256": raw.get("source_frame_sha256"),
                    "discovery": discovery,
                    "raw": dict(raw),
                    "evidence_path": evidence,
                    "raw_path_abs": raw_path,
                    "source_frame_path": source_frame,
                }
            )
        except (PipelineError, OSError, ValueError, TypeError) as exc:
            reject(type(exc).__name__ + ":" + str(exc).split(":", 1)[0])
            continue
    stats["candidate_count"] = len(candidates)
    stats["scan_truncated"] = max_rows is not None and stats["rows_scanned"] < stats["rows_declared"]
    stats.setdefault("candidate_limit_reached", False)
    return {
        "schema_version": SCHEMA,
        "version": VERSION,
        "source_sha256": source_sha,
        "inspection_manifest_sha256": file_fingerprint(root / "training-inspection.json"),
        "policy": {
            "max_candidates": max_candidates,
            "max_rows": max_rows,
            "candidate_basis": "training_result_header_grid_and_source_pixel_component",
            "deduplication_basis": "source_frame_sha256_gameplay_sha256_component_geometry",
        },
        "stats": stats,
        "candidates": candidates,
    }


def _sidecar_name(candidate: Mapping[str, Any]) -> str:
    identity = f"{candidate['source_timestamp_ms']}\n{candidate['evidence']}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:24] + ".json"


def prepare_sidecars(
    inspection_root: str | Path,
    output_root: str | Path,
    *,
    model_dir: str | Path = ".local/models/rapidocr",
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_rows: int | None = DEFAULT_MAX_ROWS,
    dry_run: bool = False,
    reader: Any = None,
    reader_factory: Callable[[str | Path], Any] = NeuralReader,
) -> dict[str, Any]:
    """Generate a new, isolated dense-inspection sidecar bundle.

    ``output_root`` is required to be outside ``inspection_root`` and is
    never merged into the source cache.  A caller registers the result only
    after review by copying the generated sidecar files into the target
    inspection namespace and adding the returned ``entries`` list to that
    namespace's ``training-inspection.json``.
    """

    source_root = Path(inspection_root).resolve()
    output = Path(output_root).resolve()
    if output == source_root:
        raise PipelineError("Training inspection preparation cannot write into its source root.")
    try:
        output.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise PipelineError("Training inspection preparation output must be outside its source root.")
    if not source_root.is_dir():
        raise PipelineError(f"Training inspection root is missing: {source_root}")
    if output.exists() and any(output.iterdir()):
        raise PipelineError(f"Training inspection preparation output is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    inventory = discover_candidates(
        source_root,
        max_candidates=max_candidates,
        max_rows=max_rows,
    )
    entries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    sidecar_dir = output / "training-badge-localization"
    if not dry_run:
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        if reader is None:
            reader = reader_factory(model_dir)

    for candidate in inventory["candidates"]:
        if dry_run:
            continue
        try:
            raw = candidate["raw"]
            from PIL import Image

            with Image.open(candidate["evidence_path"]) as image:
                pane = image.convert("RGB")
            localization = reader._localize_training_badges(
                pane,
                header=raw.get("header"),
                result_grid=raw.get("result_grid"),
                screen="training_result",
            )
            if localization.get("status") != "localized":
                errors.append(
                    {
                        "row_index": candidate["row_index"],
                        "evidence": candidate["evidence"],
                        "reason": "source_localizer_unresolved",
                        "rejections": localization.get("rejections", {}),
                    }
                )
                continue
            localization = dict(localization)
            localization["metadata"] = dict(localization.get("metadata", {}))
            localization["metadata"].update(
                {
                    "source_timestamp_ms": raw.get("source_timestamp_ms"),
                    "evidence": raw.get("evidence"),
                    "source_sha256": raw.get("source_sha256"),
                    "source_frame_sha256": raw.get("source_frame_sha256"),
                    "source_frame_evidence": candidate["source_frame"],
                    "source_frame_id": candidate["source_frame_id"],
                }
            )
            sidecar = build_sidecar(
                raw,
                localization,
                evidence_path=candidate["evidence_path"],
                source_frame_path=candidate["source_frame_path"],
                source_frame_id=candidate["source_frame_id"],
                source_frame_evidence=candidate["source_frame"],
                source_sha256=inventory["source_sha256"],
            )
            path = sidecar_dir / _sidecar_name(candidate)
            path.write_text(
                json.dumps(sidecar, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            entries.append(
                {
                    "path": _relative_path(output, path, "sidecar"),
                    "sidecar_sha256": file_fingerprint(path),
                    "source_timestamp_ms": candidate["source_timestamp_ms"],
                    "evidence": candidate["evidence"],
                    "raw_path": candidate["raw_path"],
                    "source_frame": candidate["source_frame"],
                    "source_frame_id": candidate["source_frame_id"],
                }
            )
        except (PipelineError, OSError, ValueError, TypeError, AttributeError) as exc:
            errors.append(
                {
                    "row_index": candidate["row_index"],
                    "evidence": candidate["evidence"],
                    "reason": "sidecar_generation_failed",
                    "detail": str(exc),
                }
            )

    summary = dict(
        inventory["stats"],
        localized_sidecars=len(entries),
        generation_errors=len(errors),
        dry_run=dry_run,
    )
    fragment = {
        "schema_version": SCHEMA,
        "version": VERSION,
        "source_sha256": inventory["source_sha256"],
        "inspection_manifest_sha256": inventory["inspection_manifest_sha256"],
        "policy": inventory["policy"],
        "summary": summary,
        "entries": entries,
        # This key is deliberately identical to the registration key accepted
        # by inspect_training.reparse_inspection.  It makes the fragment
        # directly mergeable without creating a second unvalidated namespace.
        "training_badge_localization_sidecars": entries,
        "errors": errors,
    }
    (output / "manifest.fragment.json").write_text(
        json.dumps(fragment, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return fragment


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inspection_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"))
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    fragment = prepare_sidecars(
        args.inspection_root,
        args.output,
        model_dir=args.model_dir,
        max_candidates=args.max_candidates,
        max_rows=args.max_rows,
        dry_run=args.dry_run,
    )
    print(json.dumps(fragment["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SCHEMA", "VERSION", "discover_candidates", "prepare_sidecars", "main"]
