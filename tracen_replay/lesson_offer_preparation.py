"""Prepare source-bound lesson-cost sidecars in an isolated namespace.

The normal fresh worker already knows how to reread one lesson-selection
frame.  This module supplies the bounded discovery layer needed before a
cached replay: it scans capture-listed neural rows, asks the source lesson
adapter which cards still have unresolved cost slots, and runs the existing
crop refiner only for those rows.  It never reads an accepted report, labels,
balances, residuals, or expected values.

Preparation always writes below a caller-supplied output directory.  The
input cache roots are read-only.  Each selected candidate brings along its
raw JSON, gameplay crop, captured source frame, and capture manifest so the
result can be copied into a disposable replay root and validated by the
ordinary ``cached_readings`` path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "tracen-replay/lesson-offer-preparation-v1"
DEFAULT_MODEL_DIR = Path(".local/models/rapidocr")
_SHA256_RE = r"^[0-9a-fA-F]{64}$"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# A capture commonly contains many adjacent OCR rows for one unchanged lesson
# menu.  Preparation reads one source-bound representative for an occurrence;
# the remaining rows stay in the inventory as member proofs.  The defaults are
# deliberately bounded so an accidental cache-wide preparation cannot turn into
# an unreviewed OCR run.  Callers can raise them explicitly after reviewing the
# inventory.
DEFAULT_OCCURRENCE_GAP_MS = 750
DEFAULT_MAX_OCCURRENCES = 128
DEFAULT_MAX_OCR_CROPS = 12_000
DEFAULT_MAX_OCR_BATCHES = 384


class LessonOfferPreparationError(ValueError):
    """A deterministic source-preparation failure."""


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise LessonOfferPreparationError(f"Could not hash source file: {path}") from exc
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LessonOfferPreparationError(f"Could not read source JSON: {path}") from exc


def _relative(value: Any, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise LessonOfferPreparationError(f"{field} must be a relative path.")
    if not value:
        return ""
    if "\x00" in value:
        raise LessonOfferPreparationError(f"{field} contains an invalid path character.")
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
        raise LessonOfferPreparationError(f"{field} must stay below its source root.")
    return posix.as_posix()


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise LessonOfferPreparationError(f"Could not inspect source path: {path}") from exc
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _safe_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if not candidate.is_dir():
        raise LessonOfferPreparationError(f"Lesson source root is missing: {candidate}")
    cursor = candidate
    while True:
        if _is_reparse(cursor):
            raise LessonOfferPreparationError(f"Lesson source root contains a reparse point: {cursor}")
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    return candidate


def _safe_file(root: Path, value: Any, field: str) -> tuple[Path, str]:
    relative = _relative(value, field)
    path = root / Path(relative)
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise LessonOfferPreparationError(f"{field} leaves the source root.") from exc
    cursor = root
    for component in PurePosixPath(relative).parts:
        cursor /= component
        if cursor.exists() and _is_reparse(cursor):
            raise LessonOfferPreparationError(f"{field} contains a reparse point: {cursor}")
    if not path.is_file():
        raise LessonOfferPreparationError(f"{field} is missing: {relative}")
    return path, relative


def _safe_frame_id(value: Any) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise LessonOfferPreparationError("Capture frame id must be a basename.")
    normalized = _relative(value, "capture frame id")
    if "/" in normalized:
        raise LessonOfferPreparationError("Capture frame id must be a basename.")
    return normalized


def _safe_output(output: str | Path, inputs: Iterable[Path]) -> Path:
    candidate = Path(output).expanduser().resolve()
    for source in inputs:
        source = source.resolve()
        try:
            candidate.relative_to(source)
        except ValueError:
            pass
        else:
            raise LessonOfferPreparationError(
                f"Output directory must not be inside an input cache: {candidate}"
            )
        try:
            source.relative_to(candidate)
        except ValueError:
            pass
        else:
            raise LessonOfferPreparationError(
                f"Output directory must not contain an input cache: {candidate}"
            )
    return candidate


def _capture(base_root: Path) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    capture_path, _ = _safe_file(base_root, "capture.json", "capture manifest")
    capture = _load_json(capture_path)
    if not isinstance(capture, dict):
        raise LessonOfferPreparationError("Capture manifest must be an object.")
    source = capture.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("sha256"), str):
        raise LessonOfferPreparationError("Capture manifest has no source hash.")
    source_sha256 = source["sha256"].lower()
    import re

    if re.fullmatch(_SHA256_RE, source_sha256) is None:
        raise LessonOfferPreparationError("Capture source hash is not SHA-256.")
    frames = capture.get("frames")
    if not isinstance(frames, list):
        raise LessonOfferPreparationError("Capture manifest has no frame list.")
    return capture, source_sha256, frames


def _frame_inputs(
    base_root: Path,
    frame: Mapping[str, Any],
    *,
    verify_source_frame: bool = True,
) -> dict[str, Any]:
    frame_id = _safe_frame_id(frame.get("id"))
    timestamp = frame.get("source_timestamp_ms")
    if type(timestamp) is not int or timestamp < 0:
        raise LessonOfferPreparationError(f"Frame {frame_id} has an invalid timestamp.")
    source_frame_path, source_frame = _safe_file(
        base_root, frame.get("evidence"), f"frame {frame_id} evidence"
    )
    raw_path, raw_relative = _safe_file(
        base_root, f"neural/{frame_id}.json", f"frame {frame_id} neural observation"
    )
    raw = _load_json(raw_path)
    if not isinstance(raw, dict):
        raise LessonOfferPreparationError(f"Frame {frame_id} neural observation is not an object.")
    if raw.get("source_timestamp_ms") != timestamp:
        raise LessonOfferPreparationError(f"Frame {frame_id} neural timestamp differs from capture.")
    gameplay_path, gameplay = _safe_file(
        base_root, raw.get("evidence"), f"frame {frame_id} gameplay evidence"
    )
    source_frame_sha256 = None
    if verify_source_frame:
        source_frame_sha256 = _digest(source_frame_path)
        declared_source_frame = raw.get("source_frame_sha256")
        if declared_source_frame != source_frame_sha256:
            raise LessonOfferPreparationError(
                f"Frame {frame_id} source-frame hash differs from capture evidence."
            )
    return {
        "id": frame_id,
        "source_timestamp_ms": timestamp,
        "evidence": source_frame,
        "raw_path": raw_path,
        "raw_relative": raw_relative,
        "raw": raw,
        "gameplay_path": gameplay_path,
        "gameplay": gameplay,
        "source_frame_path": source_frame_path,
        "source_frame": source_frame,
        "source_frame_sha256": source_frame_sha256,
    }


def _offer_summary(result: Mapping[str, Any], missing: Mapping[int, Iterable[str]]) -> list[dict[str, Any]]:
    missing_by_card = {int(card): sorted(set(fields)) for card, fields in missing.items()}
    offers: list[dict[str, Any]] = []
    for offer in result.get("offers", []):
        if not isinstance(offer, Mapping):
            continue
        card_index = offer.get("card_index")
        fields = missing_by_card.get(card_index, [])
        effects = []
        for effect in offer.get("effects", []):
            if not isinstance(effect, Mapping):
                continue
            # Effects are source observations used only to keep two visually
            # different cards from being grouped as one occurrence.  Their
            # amounts are never used as lesson prices or as a selection score.
            effects.append(
                {
                    key: effect.get(key)
                    for key in (
                        "kind",
                        "field",
                        "category",
                        "amount",
                        "level",
                        "name",
                        "name_visible",
                        "raw_label",
                        "source_offer",
                        "source_semantics",
                    )
                    if key in effect
                }
            )
        offers.append(
            {
                "offer_id": offer.get("offer_id"),
                "card_index": card_index,
                "name": offer.get("name"),
                "status": offer.get("status"),
                "missing_fields": fields,
                "effects": effects,
            }
        )
    return offers


def _positive_int_or_none(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise LessonOfferPreparationError(f"{field} must be a non-negative integer or null.")
    return value


def _source_segment(candidate: Mapping[str, Any]) -> str:
    evidence = candidate.get("source_frame_evidence")
    if not isinstance(evidence, str) or not evidence:
        return ""
    return PurePosixPath(evidence.replace("\\", "/")).parent.as_posix()


def _effect_signature(value: Any) -> tuple[tuple[str, Any], ...]:
    if not isinstance(value, Mapping):
        return ()
    keys = (
        "kind",
        "field",
        "category",
        "amount",
        "level",
        "name",
        "name_visible",
        "raw_label",
        "source_offer",
        "source_semantics",
    )
    return tuple((key, value.get(key)) for key in keys if key in value)


def _panel_signature(candidate: Mapping[str, Any]) -> tuple[tuple[Any, ...], ...]:
    """Return the stable card identity used for occurrence grouping.

    ``offer_id`` is intentionally excluded.  It is derived from line geometry
    and can change between adjacent OCR rows while the displayed card remains
    the same.  Title/card position and source-observed effects preserve actual
    card transitions; unresolved cost fields are deliberately excluded because
    they often fluctuate while a menu animates into place.
    """

    offers = candidate.get("offers")
    if not isinstance(offers, Sequence) or isinstance(offers, (str, bytes)):
        return ()
    signature: list[tuple[Any, ...]] = []
    for fallback_index, offer in enumerate(offers):
        if not isinstance(offer, Mapping):
            signature.append((fallback_index, None, ()))
            continue
        card_index = offer.get("card_index", fallback_index)
        if type(card_index) is not int:
            card_index = fallback_index
        name = offer.get("name")
        if isinstance(name, str):
            name = " ".join(name.split()).casefold()
        else:
            name = None
        effects = offer.get("effects", ())
        if not isinstance(effects, Sequence) or isinstance(effects, (str, bytes)):
            effects = ()
        signature.append(
            (
                card_index,
                name,
                tuple(_effect_signature(effect) for effect in effects),
            )
        )
    return tuple(signature)


def _capture_index(candidate: Mapping[str, Any]) -> int | None:
    value = candidate.get("capture_index")
    return value if type(value) is int and value >= 0 else None


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    capture_index = _capture_index(candidate)
    return (
        0 if capture_index is not None else 1,
        capture_index if capture_index is not None else 0,
        candidate.get("source_timestamp_ms", 0),
        str(candidate.get("frame_id", "")),
    )


def _can_join_occurrence(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    max_gap_ms: int,
) -> bool:
    if _source_segment(previous) != _source_segment(current):
        return False
    if _panel_signature(previous) != _panel_signature(current):
        return False
    previous_time = previous.get("source_timestamp_ms")
    current_time = current.get("source_timestamp_ms")
    if type(previous_time) is not int or type(current_time) is not int:
        return False
    gap = current_time - previous_time
    if gap < 0 or gap > max_gap_ms:
        return False
    previous_index = _capture_index(previous)
    current_index = _capture_index(current)
    if previous_index is not None and current_index is not None:
        # A non-candidate frame can be a transition or an occlusion.  Do not
        # bridge it merely because the title happened to reappear nearby.
        return current_index == previous_index + 1
    return True


def _member_proof(candidate: Mapping[str, Any], *, reason: str | None = None,
                  representative_frame_id: str | None = None) -> dict[str, Any]:
    proof = {
        key: candidate.get(key)
        for key in (
            "frame_id",
            "capture_index",
            "source_timestamp_ms",
            "source_frame_evidence",
            "source_frame_sha256",
            "gameplay_evidence",
            "raw_evidence",
            "sidecar",
            "status",
            "missing_fields",
        )
        if key in candidate
    }
    if reason is not None:
        proof["reason"] = reason
    if representative_frame_id is not None:
        proof["representative_frame_id"] = representative_frame_id
    return proof


def group_lesson_offer_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    max_gap_ms: int = DEFAULT_OCCURRENCE_GAP_MS,
) -> list[dict[str, Any]]:
    """Group adjacent source rows that show one stable lesson menu.

    Grouping is based on capture adjacency, source segment, card order/title,
    and source-observed effects.  It never uses a report amount, a balance, or
    an expected label.  Every member retains its own path, timestamp and
    source-frame hash so a later consumer can audit why a representative was
    chosen.
    """

    if type(max_gap_ms) is not int or max_gap_ms < 0:
        raise LessonOfferPreparationError("max_gap_ms must be a non-negative integer.")
    ordered = sorted(
        [candidate for candidate in candidates if isinstance(candidate, Mapping)],
        key=_candidate_sort_key,
    )
    groups: list[list[Mapping[str, Any]]] = []
    for candidate in ordered:
        if not groups or not _can_join_occurrence(groups[-1][-1], candidate, max_gap_ms=max_gap_ms):
            groups.append([candidate])
        else:
            groups[-1].append(candidate)
    occurrences: list[dict[str, Any]] = []
    for members in groups:
        first = members[0]
        last = members[-1]
        panel_signature = _panel_signature(first)
        signature_json = [
            {
                "card_index": card_index,
                "name": name,
                "effects": [dict(items) for items in effects],
            }
            for card_index, name, effects in panel_signature
        ]
        occurrence_id = "lesson-occurrence:" + hashlib.sha256(
            json.dumps(
                {
                    "segment": _source_segment(first),
                    "first_frame_id": first.get("frame_id"),
                    "panel_signature": signature_json,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        occurrences.append(
            {
                "occurrence_id": occurrence_id,
                "source_segment": _source_segment(first),
                "panel_signature": signature_json,
                "start_frame_id": first.get("frame_id"),
                "end_frame_id": last.get("frame_id"),
                "start_timestamp_ms": first.get("source_timestamp_ms"),
                "end_timestamp_ms": last.get("source_timestamp_ms"),
                "member_frame_ids": [member.get("frame_id") for member in members],
                "member_timestamps_ms": [member.get("source_timestamp_ms") for member in members],
                "members": [_member_proof(member) for member in members],
            }
        )
    return occurrences


def _representative_score(candidate: Mapping[str, Any]) -> tuple[int, int, int, str]:
    missing = candidate.get("missing_fields")
    missing_count = len(missing) if isinstance(missing, Sequence) and not isinstance(missing, (str, bytes)) else 999999
    offers = candidate.get("offers")
    offer_count = len(offers) if isinstance(offers, Sequence) and not isinstance(offers, (str, bytes)) else 0
    capture_index = _capture_index(candidate)
    return (
        missing_count,
        -offer_count,
        capture_index if capture_index is not None else 0,
        str(candidate.get("frame_id", "")),
    )


def select_lesson_offer_occurrences(
    candidates: Iterable[Mapping[str, Any]],
    *,
    max_occurrences: int | None = DEFAULT_MAX_OCCURRENCES,
    max_ocr_crops: int | None = DEFAULT_MAX_OCR_CROPS,
    max_ocr_batches: int | None = DEFAULT_MAX_OCR_BATCHES,
    max_gap_ms: int = DEFAULT_OCCURRENCE_GAP_MS,
) -> dict[str, Any]:
    """Select one representative per stable occurrence under explicit budgets.

    Existing source-valid sidecars are reusable and do not consume OCR budget.
    New representatives are selected in source order, with the least number of
    unresolved slots preferred within each occurrence.  Deferred members and
    budget-excluded occurrences remain explicit in the returned audit data.
    """

    max_occurrences = _positive_int_or_none(max_occurrences, "max_occurrences")
    max_ocr_crops = _positive_int_or_none(max_ocr_crops, "max_ocr_crops")
    max_ocr_batches = _positive_int_or_none(max_ocr_batches, "max_ocr_batches")
    candidate_rows = [candidate for candidate in candidates if isinstance(candidate, Mapping)]
    occurrences = group_lesson_offer_candidates(candidate_rows, max_gap_ms=max_gap_ms)
    by_id = {candidate.get("frame_id"): candidate for candidate in candidate_rows}
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    used_occurrences = 0
    used_crops = 0
    used_batches = 0
    selected_ids: set[str] = set()

    for occurrence in occurrences:
        members = [by_id[frame_id] for frame_id in occurrence["member_frame_ids"] if frame_id in by_id]
        reusable = [member for member in members if member.get("status") == "existing_valid"]
        pool = reusable or members
        if not pool:
            continue
        representative = min(pool, key=_representative_score)
        requires_ocr = not reusable
        crop_cost = int(representative.get("estimated_ocr_crops") or 0) if requires_ocr else 0
        batch_cost = int(representative.get("estimated_ocr_batches") or 0) if requires_ocr else 0
        reason = "existing_sidecar_reuse" if reusable else "occurrence_representative"
        budget_reason = None
        if requires_ocr:
            if max_occurrences is not None and used_occurrences >= max_occurrences:
                budget_reason = "occurrence_budget_exhausted"
            elif max_ocr_crops is not None and used_crops + crop_cost > max_ocr_crops:
                budget_reason = "ocr_crop_budget_exhausted"
            elif max_ocr_batches is not None and used_batches + batch_cost > max_ocr_batches:
                budget_reason = "ocr_batch_budget_exhausted"
        if budget_reason is not None:
            occurrence["selected"] = False
            occurrence["representative_frame_id"] = representative.get("frame_id")
            occurrence["selection_reason"] = budget_reason
            for member in members:
                frame_id = member.get("frame_id")
                deferred.append(_member_proof(member, reason=budget_reason,
                                               representative_frame_id=representative.get("frame_id")))
                if isinstance(member, dict):
                    member["selection"] = "deferred"
                    member["deferred_reason"] = budget_reason
                    member["occurrence_id"] = occurrence["occurrence_id"]
            continue
        occurrence["selected"] = True
        occurrence["representative_frame_id"] = representative.get("frame_id")
        occurrence["selection_reason"] = reason
        selected_ids.add(representative.get("frame_id"))
        if requires_ocr:
            used_occurrences += 1
            used_crops += crop_cost
            used_batches += batch_cost
        for member in members:
            frame_id = member.get("frame_id")
            if isinstance(member, dict):
                member["occurrence_id"] = occurrence["occurrence_id"]
                member["representative_frame_id"] = representative.get("frame_id")
                if frame_id == representative.get("frame_id"):
                    member["selection"] = "selected"
                    member["selection_reason"] = reason
                else:
                    member["selection"] = "deduplicated"
                    member["deferred_reason"] = (
                        "occurrence_deduplicated_existing_sidecar"
                        if reusable
                        else "occurrence_deduplicated"
                    )
                    deferred.append(
                        _member_proof(
                            member,
                            reason=member["deferred_reason"],
                            representative_frame_id=representative.get("frame_id"),
                        )
                    )
        selected.append(dict(representative))
    selected.sort(key=_candidate_sort_key)
    occurrence_by_frame = {}
    for occurrence in occurrences:
        for frame_id in occurrence["member_frame_ids"]:
            occurrence_by_frame[frame_id] = occurrence["occurrence_id"]
    return {
        "occurrences": occurrences,
        "selected": selected,
        "selected_ids": [candidate.get("frame_id") for candidate in selected],
        "deferred": deferred,
        "budget": {
            "max_occurrences": max_occurrences,
            "max_ocr_crops": max_ocr_crops,
            "max_ocr_batches": max_ocr_batches,
            "max_gap_ms": max_gap_ms,
            "selected_occurrences": used_occurrences,
            "used_ocr_crops": used_crops,
            "used_ocr_batches": used_batches,
            "deferred_occurrences": sum(not occurrence.get("selected") for occurrence in occurrences),
        },
        "occurrence_by_frame": occurrence_by_frame,
        "selected_frame_ids": selected_ids,
    }


def _existing_sidecar_status(
    sidecar_path: Path,
    *,
    raw: Mapping[str, Any],
    gameplay_path: Path,
    source_frame_path: Path,
) -> tuple[str, str | None]:
    if not sidecar_path.is_file():
        return "candidate", None
    try:
        extra = _load_json(sidecar_path)
        from PIL import Image
        from .lesson_offer_refinement import observe

        with Image.open(gameplay_path) as image:
            observe(
                image.convert("RGB"),
                dict(raw),
                extra,
                source_frame_path=source_frame_path,
            )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        return "existing_invalid", str(exc)
    return "existing_valid", None


def discover(
    source_root: str | Path,
    *,
    name: str | None = None,
    base_folder: str = "",
    expected_source_sha256: str | None = None,
    max_occurrences: int | None = DEFAULT_MAX_OCCURRENCES,
    max_ocr_crops: int | None = DEFAULT_MAX_OCR_CROPS,
    max_ocr_batches: int | None = DEFAULT_MAX_OCR_BATCHES,
    max_gap_ms: int = DEFAULT_OCCURRENCE_GAP_MS,
) -> dict[str, Any]:
    """Inventory unresolved lesson cards from one capture-listed source root.

    Discovery performs no reread.  Non-lesson frames are rejected from the
    candidate set before gameplay pixels are opened.  A frame with an
    existing valid sidecar is retained in the inventory but is never selected
    for a replacement write.
    """

    max_occurrences = _positive_int_or_none(max_occurrences, "max_occurrences")
    max_ocr_crops = _positive_int_or_none(max_ocr_crops, "max_ocr_crops")
    max_ocr_batches = _positive_int_or_none(max_ocr_batches, "max_ocr_batches")
    if type(max_gap_ms) is not int or max_gap_ms < 0:
        raise LessonOfferPreparationError("max_gap_ms must be a non-negative integer.")
    source_root = _safe_root(source_root)
    base_folder = _relative(base_folder, "base_folder", allow_empty=True)
    base_root = source_root / Path(base_folder)
    if not base_root.is_dir():
        raise LessonOfferPreparationError(f"Lesson base folder is missing: {base_root}")
    capture, source_sha256, frames = _capture(base_root)
    if expected_source_sha256 is not None:
        expected_source_sha256 = expected_source_sha256.lower()
        if source_sha256 != expected_source_sha256:
            raise LessonOfferPreparationError("Capture source hash differs from requested source.")
    from .lesson_offer_adapter import LessonOfferSourceError, adapt_lesson_offer_frame, missing_cost_fields

    candidates: list[dict[str, Any]] = []
    non_candidates = 0
    malformed: list[dict[str, Any]] = []
    existing_valid = 0
    existing_invalid = 0
    for index, frame in enumerate(frames):
        if not isinstance(frame, Mapping):
            malformed.append({"index": index, "reason": "frame_not_object"})
            continue
        try:
            # Validate the row and its relative evidence paths first.  The
            # source capture bytes are only opened and hashed after the
            # immutable neural lines identify an actual lesson menu.  This
            # keeps dry discovery bounded on large recordings and prevents
            # unrelated story/lobby frames from becoming image work.
            inputs = _frame_inputs(base_root, frame, verify_source_frame=False)
        except LessonOfferPreparationError as exc:
            malformed.append(
                {
                    "index": index,
                    "frame_id": frame.get("id"),
                    "reason": "source_input_invalid",
                    "detail": str(exc),
                }
            )
            continue
        raw = inputs["raw"]
        try:
            adapted = adapt_lesson_offer_frame(
                raw,
                gameplay_path=inputs["gameplay_path"],
                source_sha256=source_sha256,
            )
        except (LessonOfferSourceError, OSError, TypeError, ValueError) as exc:
            # A malformed source lesson row is reportable, but it must never
            # become an OCR request merely because a title resembles a card.
            if raw.get("header") != "Lessons":
                non_candidates += 1
                continue
            malformed.append(
                {
                    "index": index,
                    "frame_id": inputs["id"],
                    "source_timestamp_ms": inputs["source_timestamp_ms"],
                    "reason": "lesson_source_invalid",
                    "detail": str(exc),
                }
            )
            continue
        missing = missing_cost_fields(adapted)
        if not adapted.get("offers") or not missing:
            non_candidates += 1
            continue
        # Candidate rows must carry a verified source-frame identity.  Do
        # this only for lesson rows selected for inspection, while the first
        # pass above remains free of source-frame byte reads.
        try:
            verified = _frame_inputs(base_root, frame, verify_source_frame=True)
        except LessonOfferPreparationError as exc:
            malformed.append(
                {
                    "index": index,
                    "frame_id": inputs["id"],
                    "source_timestamp_ms": inputs["source_timestamp_ms"],
                    "reason": "source_frame_invalid",
                    "detail": str(exc),
                }
            )
            continue
        inputs["source_frame_sha256"] = verified["source_frame_sha256"]
        # Rebuild the proof with the verified source frame attached.  The
        # second adapter pass is intentionally limited to this candidate.
        try:
            adapted = adapt_lesson_offer_frame(
                raw,
                gameplay_path=inputs["gameplay_path"],
                source_frame_path=inputs["source_frame_path"],
                source_sha256=source_sha256,
            )
        except (LessonOfferSourceError, OSError, TypeError, ValueError) as exc:
            malformed.append(
                {
                    "index": index,
                    "frame_id": inputs["id"],
                    "source_timestamp_ms": inputs["source_timestamp_ms"],
                    "reason": "lesson_source_invalid",
                    "detail": str(exc),
                }
            )
            continue
        missing = missing_cost_fields(adapted)
        if not adapted.get("offers") or not missing:
            non_candidates += 1
            continue
        sidecar = base_root / "lesson-offer-refinement" / f"{inputs['id']}.json"
        status, detail = _existing_sidecar_status(
            sidecar,
            raw=raw,
            gameplay_path=inputs["gameplay_path"],
            source_frame_path=inputs["source_frame_path"],
        )
        if status == "existing_valid":
            existing_valid += 1
        elif status == "existing_invalid":
            existing_invalid += 1
        candidates.append(
            {
                "frame_id": inputs["id"],
                "capture_index": index,
                "source_timestamp_ms": inputs["source_timestamp_ms"],
                "source_frame_evidence": inputs["source_frame"],
                "gameplay_evidence": inputs["gameplay"],
                "raw_evidence": inputs["raw_relative"],
                "source_frame_sha256": inputs["source_frame_sha256"],
                "sidecar": f"lesson-offer-refinement/{inputs['id']}.json",
                "status": status,
                "detail": detail,
                "offers": _offer_summary(adapted, missing),
                "missing_fields": sorted({field for fields in missing.values() for field in fields}),
                "card_count": len(adapted.get("offers", [])),
                # ``build`` sends one crop batch and at most two same-source
                # preprocessing batches when any slot remains unresolved.
                "estimated_ocr_batches": 0 if status != "candidate" else 3,
                "estimated_ocr_crops": (
                    0
                    if status != "candidate"
                    else len(adapted.get("offers", [])) * 5 * 3
                ),
            }
        )
    candidates.sort(key=_candidate_sort_key)
    selection = select_lesson_offer_occurrences(
        candidates,
        max_occurrences=max_occurrences,
        max_ocr_crops=max_ocr_crops,
        max_ocr_batches=max_ocr_batches,
        max_gap_ms=max_gap_ms,
    )
    selected_candidates = selection["selected"]
    return {
        "schema_version": SCHEMA,
        "name": name or source_root.name,
        "source_root": source_root.as_posix(),
        "base_folder": base_folder,
        "source_sha256": source_sha256,
        "capture_sha256": _digest(base_root / "capture.json"),
        "capture_frame_count": len(frames),
        "non_candidates": non_candidates,
        "malformed": malformed,
        "existing_valid": existing_valid,
        "existing_invalid": existing_invalid,
        "candidates": candidates,
        "occurrences": selection["occurrences"],
        "deferred_candidates": selection["deferred"],
        "selected_candidates": selection["selected_ids"],
        "selected_ocr_candidates": [
            item["frame_id"] for item in selected_candidates if item.get("status") == "candidate"
        ],
        "selected_reusable_candidates": [
            item["frame_id"] for item in selected_candidates if item.get("status") == "existing_valid"
        ],
        "estimated_ocr_batches": selection["budget"]["used_ocr_batches"],
        "estimated_ocr_crops": selection["budget"]["used_ocr_crops"],
        "all_candidate_count": len(candidates),
        "occurrence_count": len(selection["occurrences"]),
        "deferred_count": len(selection["deferred"]),
        "budget": selection["budget"],
        "policy": {
            "source": "capture-listed neural rows and their own gameplay/source-frame proofs",
            "selection": "lesson_selection with at least one unresolved adapter cost slot",
            "amount_source": "same-frame crop OCR only; no balances, reports, labels, or expected values",
            "views": "one raw crop batch plus up to two same-source preprocessing batches",
            "grouping": "adjacent same-segment cards/title/effect identity; unresolved slots do not split an occurrence",
            "representative": "fewest source-unresolved cost slots, then greatest observed card count, then source order",
            "budget": "existing valid sidecars are reusable; new OCR representatives consume occurrence/crop/batch budgets",
        },
        # The full capture manifest is deliberately omitted from the
        # inventory.  Its immutable identity is represented by
        # ``capture_sha256`` and ``capture_frame_count``; retaining all frame
        # records here would duplicate a large source object in every report.
    }


def _copy_equal(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if _is_reparse(target) or not target.is_file() or _digest(source) != _digest(target):
            raise LessonOfferPreparationError(f"Output path collides with different content: {target}")
        return
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        raise LessonOfferPreparationError(f"Could not copy source evidence to {target}") from exc
    if _digest(source) != _digest(target):
        raise LessonOfferPreparationError(f"Copied source evidence changed: {target}")


def _write_json_exclusive(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise LessonOfferPreparationError(f"Refusing to overwrite output sidecar: {path}")
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except OSError as exc:
        raise LessonOfferPreparationError(f"Could not write output JSON: {path}") from exc
    return _digest(path)


def _runtime(reader: Any, model_dir: str | Path) -> dict[str, Any]:
    return {
        "python": sys.version,
        "model_dir": str(Path(model_dir).as_posix()),
        "engine_fingerprint": getattr(reader, "fingerprint", None),
        "models": getattr(reader, "models", None),
    }


def _validate_sidecar_cache(
    sidecar_path: Path,
    *,
    raw: Mapping[str, Any],
    frame: Mapping[str, Any],
    target_base_root: Path,
    source_sha256: str,
) -> dict[str, Any]:
    """Validate one generated sidecar through both source adapters and cache loading."""

    extra = _load_json(sidecar_path)
    gameplay_path = target_base_root / Path(str(raw["evidence"]))
    source_frame_path = target_base_root / Path(str(frame["evidence"]))
    from PIL import Image
    from .lesson_offer_adapter import (
        adapt_lesson_offer_frame,
        merge_lesson_offer_cost_refinement,
    )
    from .lesson_offer_refinement import observe

    with Image.open(gameplay_path) as image:
        observe(
            image.convert("RGB"),
            dict(raw),
            extra,
            source_frame_path=source_frame_path,
        )
    base = adapt_lesson_offer_frame(
        raw,
        gameplay_path=gameplay_path,
        source_frame_path=source_frame_path,
        source_sha256=source_sha256,
    )
    merged = merge_lesson_offer_cost_refinement(
        base,
        extra.get("cards") if isinstance(extra, Mapping) else None,
        provenance=extra if isinstance(extra, Mapping) else None,
        raw=raw,
        gameplay_path=gameplay_path,
        source_frame_path=source_frame_path,
        source_sha256=source_sha256,
    )
    # ``cached_readings`` is a no-OCR path.  The source adapter checks above
    # ensure the sidecar itself is also compatible with the current grouped
    # offer parser before this cache-level check.
    from .full_recording import cached_readings

    cached = cached_readings(
        {
            "source": {"sha256": source_sha256},
            "frames": [
                {
                    "id": frame["id"],
                    "source_timestamp_ms": frame["source_timestamp_ms"],
                    "evidence": frame["evidence"],
                }
            ],
        },
        target_base_root,
    )
    cached_preview = cached[0].get("facts", {}).get("lesson_offer_preview", {})
    return {
        "extra": extra,
        "merged": merged,
        "cached_preview": cached_preview,
    }


def prepare(
    source_root: str | Path,
    output_root: str | Path,
    *,
    name: str | None = None,
    base_folder: str = "",
    expected_source_sha256: str | None = None,
    model_dir: str | Path = DEFAULT_MODEL_DIR,
    reader: Any = None,
    write: bool = False,
    _inventory: Mapping[str, Any] | None = None,
    max_occurrences: int | None = DEFAULT_MAX_OCCURRENCES,
    max_ocr_crops: int | None = DEFAULT_MAX_OCR_CROPS,
    max_ocr_batches: int | None = DEFAULT_MAX_OCR_BATCHES,
    max_gap_ms: int = DEFAULT_OCCURRENCE_GAP_MS,
) -> dict[str, Any]:
    """Inventory and optionally materialize one recording's sidecars.

    ``write=False`` is a dry run and never instantiates OCR.  With ``write``
    enabled, output receives only the selected source closure and sidecars;
    the input cache and any existing manifests remain untouched.
    """

    source_root = _safe_root(source_root)
    base_folder = _relative(base_folder, "base_folder", allow_empty=True)
    output_root = _safe_output(output_root, [source_root])
    inventory = (
        dict(_inventory)
        if _inventory is not None
        else discover(
            source_root,
            name=name,
            base_folder=base_folder,
            expected_source_sha256=expected_source_sha256,
            max_occurrences=max_occurrences,
            max_ocr_crops=max_ocr_crops,
            max_ocr_batches=max_ocr_batches,
            max_gap_ms=max_gap_ms,
        )
    )
    result = {
        "schema_version": SCHEMA,
        "name": inventory["name"],
        "source_root": inventory["source_root"],
        "base_folder": inventory["base_folder"],
        "source_sha256": inventory["source_sha256"],
        "output_root": output_root.as_posix(),
        "write": bool(write),
        "inventory": inventory,
        "runtime": None,
        "generated": [],
        "unresolved": [],
    }
    if not write:
        return result
    selected_ids = set(inventory.get("selected_candidates", []))
    selected = [
        item
        for item in inventory.get("candidates", [])
        if item.get("frame_id") in selected_ids
        and item.get("status") in {"candidate", "existing_valid"}
    ]
    if not selected:
        return result
    if reader is None:
        from .vision import NeuralReader

        if any(item.get("status") == "candidate" for item in selected):
            reader = NeuralReader(model_dir)
    result["runtime"] = _runtime(reader, model_dir)
    base_root = _safe_root(source_root / Path(base_folder))
    target_recording_root = output_root / inventory["name"]
    target_base_root = target_recording_root / Path(base_folder)
    capture_path = base_root / "capture.json"
    _copy_equal(capture_path, target_base_root / "capture.json")
    from .lesson_offer_adapter import LessonOfferSourceError
    from .lesson_offer_adapter import refine_lesson_offer_costs

    for item in selected:
        try:
            frame = _frame_inputs(
                base_root,
                {
                    "id": item["frame_id"],
                    "source_timestamp_ms": item["source_timestamp_ms"],
                    "evidence": item["source_frame_evidence"],
                },
            )
            raw = frame["raw"]
            _copy_equal(frame["raw_path"], target_base_root / frame["raw_relative"])
            _copy_equal(frame["gameplay_path"], target_base_root / frame["gameplay"])
            _copy_equal(frame["source_frame_path"], target_base_root / frame["source_frame"])
            target_gameplay = target_base_root / frame["gameplay"]
            target_source_frame = target_base_root / frame["source_frame"]
            sidecar_target = target_base_root / "lesson-offer-refinement" / f"{frame['id']}.json"
            if item.get("status") == "existing_valid":
                source_sidecar = base_root / Path(str(item["sidecar"]))
                _copy_equal(source_sidecar, sidecar_target)
            if sidecar_target.exists():
                # A stopped preparation can be resumed safely.  Existing
                # output is never overwritten; it must pass the exact same
                # source and current-adapter checks before it is counted.
                validated = _validate_sidecar_cache(
                    sidecar_target,
                    raw=raw,
                    frame={
                        "id": frame["id"],
                        "source_timestamp_ms": frame["source_timestamp_ms"],
                        "evidence": frame["source_frame"],
                    },
                    target_base_root=target_base_root,
                    source_sha256=inventory["source_sha256"],
                )
                cached_preview = validated["cached_preview"]
                result["generated"].append(
                    {
                        "frame_id": frame["id"],
                        "source_timestamp_ms": frame["source_timestamp_ms"],
                        "sidecar": sidecar_target.as_posix(),
                        "sidecar_sha256": _digest(sidecar_target),
                        "cached_offer_count": (
                            len(cached_preview.get("offers", []))
                            if isinstance(cached_preview, Mapping)
                            else 0
                        ),
                        "cached_complete_offer_count": (
                            sum(
                                offer.get("status") == "complete"
                                for offer in cached_preview.get("offers", [])
                                if isinstance(offer, Mapping)
                            )
                            if isinstance(cached_preview, Mapping)
                            else 0
                        ),
                        "refined_offer_count": len(validated["merged"].get("offers", [])),
                        "reused": True,
                    }
                )
                continue
            refined, extra = refine_lesson_offer_costs(
                raw,
                gameplay_path=target_gameplay,
                source_frame_path=target_source_frame,
                source_sha256=inventory["source_sha256"],
                reader=reader,
            )
            if extra is None:
                result["unresolved"].append(
                    dict(item, reason="normal_refiner_returned_no_sidecar")
                )
                continue
            sidecar_sha256 = _write_json_exclusive(sidecar_target, extra)
            validated = _validate_sidecar_cache(
                sidecar_target,
                raw=raw,
                frame={
                    "id": frame["id"],
                    "source_timestamp_ms": frame["source_timestamp_ms"],
                    "evidence": frame["source_frame"],
                },
                target_base_root=target_base_root,
                source_sha256=inventory["source_sha256"],
            )
            cached_preview = validated["cached_preview"]
            result["generated"].append(
                {
                    "frame_id": frame["id"],
                    "source_timestamp_ms": frame["source_timestamp_ms"],
                    "sidecar": (target_recording_root / Path(base_folder) / "lesson-offer-refinement" / f"{frame['id']}.json").as_posix(),
                    "sidecar_sha256": sidecar_sha256,
                    "cached_offer_count": len(cached_preview.get("offers", [])) if isinstance(cached_preview, dict) else 0,
                    "cached_complete_offer_count": sum(
                        offer.get("status") == "complete"
                        for offer in cached_preview.get("offers", [])
                        if isinstance(offer, Mapping)
                    ) if isinstance(cached_preview, dict) else 0,
                    "refined_offer_count": len(refined.get("offers", [])),
                    "reused": False,
                }
            )
        except (LessonOfferSourceError, LessonOfferPreparationError, OSError, TypeError, ValueError, KeyError) as exc:
            result["unresolved"].append(
                dict(item, reason="normal_sidecar_generation_failed", detail=str(exc))
            )
    return result


def prepare_all(
    recordings: Mapping[str, Mapping[str, Any]],
    output_root: str | Path,
    *,
    model_dir: str | Path = DEFAULT_MODEL_DIR,
    write: bool = False,
    max_occurrences: int | None = DEFAULT_MAX_OCCURRENCES,
    max_ocr_crops: int | None = DEFAULT_MAX_OCR_CROPS,
    max_ocr_batches: int | None = DEFAULT_MAX_OCR_BATCHES,
    max_gap_ms: int = DEFAULT_OCCURRENCE_GAP_MS,
) -> dict[str, Any]:
    """Run the same discovery policy over several named recording roots."""

    output_root = Path(output_root).expanduser().resolve()
    source_roots = [_safe_root(spec["source_root"]) for spec in recordings.values()]
    output_root = _safe_output(output_root, source_roots)
    started = time.monotonic()
    results = []
    reader = None
    if write:
        pending = []
        for name, spec in recordings.items():
            pending.append(
                (name, spec, discover(
                    spec["source_root"],
                    name=name,
                    base_folder=spec.get("base_folder", ""),
                    expected_source_sha256=spec.get("source_sha256"),
                    max_occurrences=max_occurrences,
                    max_ocr_crops=max_ocr_crops,
                    max_ocr_batches=max_ocr_batches,
                    max_gap_ms=max_gap_ms,
                ))
            )
        if any(item[2].get("selected_ocr_candidates") for item in pending):
            from .vision import NeuralReader

            reader = NeuralReader(model_dir)
        for name, spec, inventory in pending:
            # Reuse the already computed inventory without making a second
            # cache scan.  ``prepare`` remains the single-recording API; this
            # loop intentionally supplies the shared reader and writes into
            # the same isolated namespace.
            result = prepare(
                spec["source_root"],
                output_root,
                name=name,
                base_folder=spec.get("base_folder", ""),
                expected_source_sha256=spec.get("source_sha256"),
                model_dir=model_dir,
                reader=reader,
                write=True,
                _inventory=inventory,
                max_occurrences=max_occurrences,
                max_ocr_crops=max_ocr_crops,
                max_ocr_batches=max_ocr_batches,
                max_gap_ms=max_gap_ms,
            )
            results.append(result)
    else:
        for name, spec in recordings.items():
            results.append(
                prepare(
                    spec["source_root"],
                    output_root,
                    name=name,
                    base_folder=spec.get("base_folder", ""),
                    expected_source_sha256=spec.get("source_sha256"),
                    model_dir=model_dir,
                    write=False,
                    max_occurrences=max_occurrences,
                    max_ocr_crops=max_ocr_crops,
                    max_ocr_batches=max_ocr_batches,
                    max_gap_ms=max_gap_ms,
                )
            )
    summary = {
        "schema_version": SCHEMA,
        "output_root": output_root.as_posix(),
        "write": bool(write),
        "recordings": results,
        "candidate_count": sum(
            len(item["inventory"]["selected_candidates"]) for item in results
        ),
        "all_candidate_count": sum(
            item["inventory"].get("all_candidate_count", 0) for item in results
        ),
        "occurrence_count": sum(
            item["inventory"].get("occurrence_count", 0) for item in results
        ),
        "deferred_count": sum(
            item["inventory"].get("deferred_count", 0) for item in results
        ),
        "estimated_ocr_batches": sum(
            item["inventory"]["estimated_ocr_batches"] for item in results
        ),
        "estimated_ocr_crops": sum(
            item["inventory"]["estimated_ocr_crops"] for item in results
        ),
        "generated_count": sum(len(item["generated"]) for item in results),
        "unresolved_count": sum(len(item["unresolved"]) for item in results),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "policy": "source-bound candidate lesson screens only; no accepted report or expected values",
    }
    return summary


def _parse_recording(value: str) -> tuple[str, dict[str, Any]]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("recording must be NAME=PATH")
    name, raw_path = value.split("=", 1)
    if not name or not raw_path:
        raise argparse.ArgumentTypeError("recording must be NAME=PATH")
    base_folder = ""
    if ":" in raw_path:
        # Optional ``NAME=PATH:BASE_FOLDER`` syntax is intentionally simple;
        # Windows drive letters are preserved by splitting only the final
        # colon when the suffix names a directory.
        possible_path, possible_base = raw_path.rsplit(":", 1)
        if possible_base and Path(possible_path).is_dir():
            raw_path, base_folder = possible_path, possible_base
    return name, {"source_root": Path(raw_path), "base_folder": base_folder}


def default_recordings(repository_root: str | Path | None = None) -> dict[str, dict[str, Any]]:
    root = Path(repository_root or Path(__file__).resolve().parents[1])
    return {
        "v1": {"source_root": root / ".local/full-recording/v1", "base_folder": ""},
        "independent-01": {
            "source_root": root / ".local/full-recording/independent-01",
            "base_folder": "",
        },
        "independent-02": {
            "source_root": root / ".local/full-recording/independent-02",
            "base_folder": "initial-baseline",
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new isolated output root; it must be outside every input cache",
    )
    parser.add_argument(
        "--recording",
        action="append",
        type=_parse_recording,
        metavar="NAME=PATH",
        help="input cache (repeatable); defaults to v1, independent-01, independent-02",
    )
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument(
        "--write",
        action="store_true",
        help="materialize source closure and sidecars; omit for read-only inventory",
    )
    parser.add_argument(
        "--inventory-json",
        type=Path,
        help="optional path for the machine-readable inventory summary",
    )
    parser.add_argument(
        "--max-occurrences",
        type=int,
        default=DEFAULT_MAX_OCCURRENCES,
        help=f"maximum new OCR representatives per recording (default: {DEFAULT_MAX_OCCURRENCES})",
    )
    parser.add_argument(
        "--max-ocr-crops",
        type=int,
        default=DEFAULT_MAX_OCR_CROPS,
        help=f"maximum estimated OCR crops per recording (default: {DEFAULT_MAX_OCR_CROPS})",
    )
    parser.add_argument(
        "--max-ocr-batches",
        type=int,
        default=DEFAULT_MAX_OCR_BATCHES,
        help=f"maximum estimated OCR batches per recording (default: {DEFAULT_MAX_OCR_BATCHES})",
    )
    parser.add_argument(
        "--occurrence-gap-ms",
        type=int,
        default=DEFAULT_OCCURRENCE_GAP_MS,
        help=f"maximum source timestamp gap for fallback occurrence grouping (default: {DEFAULT_OCCURRENCE_GAP_MS})",
    )
    args = parser.parse_args(argv)
    if args.recording:
        recordings = dict(args.recording)
        # The CLI form binds source hashes from each capture manifest; it does
        # not accept labels or expected amounts as configuration.
    else:
        recordings = default_recordings()
    summary = prepare_all(
        recordings,
        args.output,
        model_dir=args.model_dir,
        write=args.write,
        max_occurrences=args.max_occurrences,
        max_ocr_crops=args.max_ocr_crops,
        max_ocr_batches=args.max_ocr_batches,
        max_gap_ms=args.occurrence_gap_ms,
    )
    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.inventory_json:
        args.inventory_json.parent.mkdir(parents=True, exist_ok=True)
        args.inventory_json.write_text(payload + "\n", encoding="utf-8")
    print(payload, flush=True)


__all__ = [
    "SCHEMA",
    "DEFAULT_MODEL_DIR",
    "DEFAULT_OCCURRENCE_GAP_MS",
    "DEFAULT_MAX_OCCURRENCES",
    "DEFAULT_MAX_OCR_CROPS",
    "DEFAULT_MAX_OCR_BATCHES",
    "LessonOfferPreparationError",
    "default_recordings",
    "discover",
    "group_lesson_offer_candidates",
    "select_lesson_offer_occurrences",
    "prepare",
    "prepare_all",
]


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke tests
    main()
