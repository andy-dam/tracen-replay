"""Persist and replay source-bound hint-card recovery observations.

``hint_card_identity.recover`` is an explicit preparation step: it performs
the OCR needed to establish a complete-line prefix or a wrapped receipt's
amount outside the cursor.  This module stores that result together with the
source-row and pixel provenance needed to validate it later.  ``load`` never
constructs a ``NeuralReader`` or reruns OCR; it rechecks the saved proof
against the current parsed rows and source cache, and returns no candidates
when any binding is stale.

The cache is an immutable preparation artifact.  ``save`` refuses to replace
an existing path.  A caller may use the default ``root/hint-card-recovery.json``
or pass an explicit artifact path with ``cache_path=`` to ``load``.  A missing
cache is an ordinary empty result; a present cache that is malformed, foreign,
or stale raises ``ValueError`` so a producer cannot silently publish a report
after losing previously prepared evidence.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


CACHE_SCHEMA = "tracen-replay/hint-card-recovery-cache-v1"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SINGLE_LINE_HINT_KIND = "single_line_hint_receipt"
_SINGLE_LINE_HINT_BASIS = (
    "standalone_hint_card_with_single_line_receipt_and_per_row_amount_ocr"
)
_MIN_PREFIX_CONFIDENCE = 90.0
_MAX_ROW_GAP_MS = 250


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _canonical_hash(value: Any) -> str | None:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _row_key(row: Mapping[str, Any]) -> tuple[int, str] | None:
    timestamp = row.get("source_timestamp_ms")
    evidence = row.get("evidence")
    if type(timestamp) is not int or timestamp < 0:
        return None
    if not isinstance(evidence, str) or not evidence or Path(evidence).is_absolute():
        return None
    path = Path(evidence)
    if ".." in path.parts:
        return None
    return timestamp, evidence.replace("\\", "/")


def _validated_rows(readings: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]] | None:
    if not isinstance(readings, Sequence) or isinstance(readings, (str, bytes)):
        return None
    result: list[Mapping[str, Any]] = []
    keys: set[tuple[int, str]] = set()
    for row in readings:
        if not isinstance(row, Mapping):
            return None
        key = _row_key(row)
        if key is None or key in keys:
            return None
        keys.add(key)
        result.append(row)
    return result


def _span_rows(
    readings: Sequence[Mapping[str, Any]],
    start_ms: int,
    end_ms: int,
) -> list[Mapping[str, Any]] | None:
    if type(start_ms) is not int or type(end_ms) is not int or start_ms < 0 or end_ms < start_ms:
        return None
    rows = [
        row
        for row in readings
        if start_ms <= row["source_timestamp_ms"] <= end_ms
    ]
    return sorted(rows, key=lambda row: (row["source_timestamp_ms"], row["evidence"]))


def _span_hashes(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]] | None:
    result = []
    for row in rows:
        key = _row_key(row)
        row_hash = _canonical_hash(row)
        if key is None or row_hash is None:
            return None
        result.append(
            {
                "timestamp_ms": key[0],
                "evidence": key[1],
                "row_sha256": row_hash,
            }
        )
    return result


def _hash_list_equal(left: Any, right: Any) -> bool:
    return isinstance(left, list) and left == right


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if not all(_finite_number(item) for item in value):
        return None
    left, top, right, bottom = (float(item) for item in value)
    if not (148.0 <= left < right <= 958.0 and 0.0 <= top < bottom <= 1080.0):
        return None
    return left, top, right, bottom


def _boxes_equal(left: Any, right: Any) -> bool:
    left_box = _box(left)
    right_box = _box(right)
    return left_box is not None and right_box is not None and left_box == right_box


def _optional_boxes_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return _boxes_equal(left, right)


def _source_manifest_hash(root: Path) -> str | None:
    return _digest(root / "capture.json")


def _candidate_observation_matches(
    candidate_observation: Mapping[str, Any],
    item: Mapping[str, Any],
) -> bool:
    card = candidate_observation.get("card")
    receipt = candidate_observation.get("receipt")
    if not isinstance(card, Mapping) or not isinstance(receipt, Mapping):
        return False
    expected_card = item.get("card")
    expected_receipt = item.get("receipt")
    if not isinstance(expected_card, Mapping) or not isinstance(expected_receipt, Mapping):
        return False
    expected_header = expected_card.get("header")
    saved_header = card.get("header")
    if not isinstance(expected_header, Mapping) or not isinstance(saved_header, Mapping):
        return False
    if (
        card.get("text") != expected_card.get("text")
        or receipt.get("text") != expected_receipt.get("text")
        or receipt.get("raw_name") != expected_receipt.get("name")
        or receipt.get("amount") != expected_receipt.get("amount")
        or card.get("confidence") != expected_card.get("confidence")
        or receipt.get("confidence") != expected_receipt.get("confidence")
        or card.get("suffix") not in (None, "single_circle", "double_circle")
        or not _boxes_equal(card.get("box"), expected_card.get("box"))
        or not _boxes_equal(receipt.get("box"), expected_receipt.get("box"))
        or saved_header.get("text") != expected_header.get("text")
        or saved_header.get("confidence") != expected_header.get("confidence")
        or not _boxes_equal(saved_header.get("box"), expected_header.get("box"))
    ):
        return False
    return _boxes_equal(candidate_observation.get("overlay_box"), item.get("overlay"))


def _prefix_proof_matches(
    candidate_observation: Mapping[str, Any],
    item: Mapping[str, Any],
    amount: int,
) -> bool:
    proof = candidate_observation.get("prefix_amount_proof")
    if not isinstance(proof, Mapping):
        return False
    confidence = proof.get("confidence")
    recognized = proof.get("recognized_text")
    raw_name = proof.get("raw_name")
    proof_amount = proof.get("amount")
    crop_box = proof.get("crop_box")
    overlay = item.get("overlay")
    receipt = item.get("receipt")
    if (
        type(proof_amount) is not int
        or proof_amount != amount
        or not _finite_number(confidence)
        or not 0.0 <= float(confidence) <= 100.0
        or float(confidence) < _MIN_PREFIX_CONFIDENCE
        or not isinstance(recognized, str)
        or not isinstance(raw_name, str)
        or not raw_name.strip()
        or proof.get("basis") != "independent_prefix_crop_ocr_to_verified_overlay_boundary"
        or not isinstance(receipt, Mapping)
        or not isinstance(overlay, (list, tuple))
    ):
        return False
    try:
        from .hint_card_identity import _parse_hint_text

        parsed = _parse_hint_text(recognized)
    except (ImportError, TypeError, ValueError):
        return False
    if parsed is None or parsed[0] != amount or parsed[1] != raw_name:
        return False
    receipt_box = _box(receipt.get("box"))
    overlay_box = _box(overlay)
    saved_crop = _box(crop_box)
    if receipt_box is None or overlay_box is None or saved_crop is None:
        return False
    expected_crop = (receipt_box[0], receipt_box[1], overlay_box[0], receipt_box[3])
    return saved_crop == expected_crop and saved_crop[2] > saved_crop[0] + 32.0


def _single_line_prefix_proof_matches(
    candidate_observation: Mapping[str, Any],
    item: Mapping[str, Any],
    amount: int,
    model_fingerprint: str,
    *,
    image: Any = None,
) -> bool:
    """Validate single-line prefix metadata and, when supplied, its pixels."""
    proof = candidate_observation.get("prefix_amount_proof")
    if (
        not _prefix_proof_matches(candidate_observation, item, amount)
        or not isinstance(proof, Mapping)
        or not isinstance(model_fingerprint, str)
        or not _SHA256_RE.fullmatch(model_fingerprint)
        or proof.get("model_fingerprint") != model_fingerprint
        or not isinstance(proof.get("pixel_rgb_sha256"), str)
        or not _SHA256_RE.fullmatch(proof["pixel_rgb_sha256"])
    ):
        return False
    try:
        from .hint_card_identity import (
            _single_line_prefix_name_compatible,
        )
    except (ImportError, TypeError, ValueError):
        return False
    expected_card = item.get("card")
    expected_receipt = item.get("receipt")
    if (
        not isinstance(expected_card, Mapping)
        or not isinstance(expected_receipt, Mapping)
        or not _single_line_prefix_name_compatible(
            expected_card.get("text"),
            expected_receipt.get("name"),
            proof.get("raw_name"),
        )
    ):
        return False
    if image is None:
        return True
    crop_box = _box(proof.get("crop_box"))
    if crop_box is None:
        return False
    try:
        left, top, right, bottom = (int(round(value)) for value in crop_box)
        crop = image.crop((left - 148, top, right - 148, bottom)).convert("RGB")
        pixel_sha256 = hashlib.sha256(crop.tobytes()).hexdigest()
    except (AttributeError, TypeError, ValueError):
        return False
    return pixel_sha256 == proof["pixel_rgb_sha256"]


def _cache_observation_provenance(
    candidate_observation: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> dict[str, Any] | None:
    raw = binding.get("raw")
    if not isinstance(raw, Mapping):
        return None
    engine = raw.get("engine_fingerprint")
    models = raw.get("model_sha256")
    if (
        not isinstance(engine, str)
        or not _SHA256_RE.fullmatch(engine)
        or not isinstance(models, Mapping)
        or not models
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or not _SHA256_RE.fullmatch(value)
            for key, value in models.items()
        )
    ):
        return None
    return {
        "timestamp_ms": candidate_observation.get("timestamp_ms"),
        "evidence": candidate_observation.get("evidence"),
        "evidence_sha256": binding.get("evidence_sha256") or candidate_observation.get("evidence_sha256"),
        "raw_sha256": binding.get("raw_sha256"),
        "gameplay_sha256": binding.get("gameplay_sha256"),
        "source_frame_sha256": binding.get("source_frame_sha256"),
        "source_frame_path": binding.get("source_frame_path"),
        "capture_frame_id": binding.get("capture_frame_id"),
        "raw_engine_fingerprint": engine,
        "raw_model_sha256": deepcopy(dict(models)),
    }


def _observation_binding_matches(
    observation: Mapping[str, Any],
    binding: Mapping[str, Any],
) -> bool:
    """Ensure every persisted source hash still matches the current cache."""
    fields = (
        "evidence_sha256",
        "raw_sha256",
        "gameplay_sha256",
        "source_frame_sha256",
        "source_frame_path",
        "capture_frame_id",
    )
    return all(observation.get(field) == binding.get(field) for field in fields)


def _wrapped_line_matches(
    saved: Any,
    expected: Any,
) -> bool:
    if not isinstance(saved, Mapping) or not isinstance(expected, Mapping):
        return False
    return (
        saved.get("text") == expected.get("text")
        and saved.get("confidence") == expected.get("confidence")
        and _boxes_equal(saved.get("box"), expected.get("box"))
        and saved.get("source") == expected.get("source")
        and saved.get("overlay_occluded") is expected.get("overlay_occluded")
        and saved.get("overlay_boxes", []) == expected.get("overlay_boxes", [])
        and saved.get("recipient_name_occluded", False)
        is expected.get("recipient_name_occluded", False)
    )


def _wrapped_candidate_observation_matches(
    observation: Mapping[str, Any],
    item: Mapping[str, Any],
) -> bool:
    """Compare one persisted wrapped observation with current parsed rows."""
    card = observation.get("card")
    receipt = observation.get("receipt")
    parts = observation.get("receipt_parts")
    expected_card = item.get("card")
    expected_receipt = item.get("receipt")
    if (
        not isinstance(card, Mapping)
        or not isinstance(receipt, Mapping)
        or not isinstance(parts, Mapping)
        or not isinstance(expected_card, Mapping)
        or not isinstance(expected_receipt, Mapping)
    ):
        return False
    expected_header = expected_card.get("header")
    saved_header = card.get("header")
    if (
        not isinstance(expected_header, Mapping)
        or not isinstance(saved_header, Mapping)
        or card.get("text") != expected_card.get("text")
        or card.get("confidence") != expected_card.get("confidence")
        or not _boxes_equal(card.get("box"), expected_card.get("box"))
        or not _wrapped_line_matches(saved_header, expected_header)
        or receipt.get("text") != expected_receipt.get("text")
        or receipt.get("raw_name") != expected_receipt.get("name")
        or receipt.get("amount") != expected_receipt.get("amount")
        or receipt.get("confidence") != expected_receipt.get("confidence")
        or receipt.get("source") != expected_receipt.get("source")
        or not _boxes_equal(receipt.get("box"), expected_receipt.get("box"))
        or not _wrapped_line_matches(
            parts.get("prefix"), expected_receipt.get("prefix")
        )
        or not _wrapped_line_matches(
            parts.get("continuation"), expected_receipt.get("continuation")
        )
        or not _optional_boxes_equal(observation.get("overlay_box"), item.get("overlay"))
    ):
        return False
    suffix = card.get("suffix")
    suffix_state = card.get("suffix_state")
    if suffix_state not in ("present", "undetermined"):
        return False
    if suffix_state == "present":
        if suffix not in ("single_circle", "double_circle"):
            return False
    elif suffix is not None:
        return False
    return True


def _validate_wrapped_candidate(
    candidate: Mapping[str, Any],
    readings: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    source_sha256: str,
    capture_manifest_sha256: str,
    stored_span_hashes: Any = None,
    stored_observation_provenance: Any = None,
) -> dict[str, Any] | None:
    """Validate a wrapped candidate without invoking OCR."""
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("observation_kind") != "wrapped_hint_receipt"
        or candidate.get("kind") != "skill_hint_change"
        or candidate.get("source_sha256") != source_sha256
        or not isinstance(candidate.get("name"), str)
        or not candidate["name"].strip()
        or type(candidate.get("amount")) is not int
        or not 0 < candidate["amount"] <= 99
    ):
        return None
    observations = candidate.get("observations")
    source_times = candidate.get("source_timestamps_ms")
    identity = candidate.get("identity_proof")
    provenance = candidate.get("provenance")
    raw_names = candidate.get("raw_receipt_name_candidates")
    if (
        not isinstance(observations, list)
        or not isinstance(source_times, list)
        or not isinstance(identity, Mapping)
        or not isinstance(provenance, Mapping)
        or not isinstance(raw_names, list)
        or len(observations) < 2
        or len(observations) != len(source_times)
        or any(type(value) is not int or value < 0 for value in source_times)
        or len(set(source_times)) != len(source_times)
        or source_times != sorted(source_times)
        or any(not isinstance(value, str) or not value.strip() for value in raw_names)
        or raw_names != sorted(set(raw_names))
    ):
        return None
    rank_state = identity.get("rank_state")
    identity_suffix = identity.get("suffix")
    context_observed = identity.get("context_observed")
    same_context = identity.get("same_context")
    if (
        identity.get("basis")
        != "standalone_hint_card_with_wrapped_receipt_and_per_row_amount_ocr"
        or identity.get("card_text") != candidate["name"]
        or rank_state not in ("present", "undetermined")
        or (rank_state == "present" and identity_suffix not in ("single_circle", "double_circle"))
        or (rank_state == "undetermined" and identity_suffix is not None)
        or identity.get("distinct_timestamp_count") != len(source_times)
        or identity.get("geometry_stable") is not True
        or type(context_observed) is not bool
        or (context_observed and (not isinstance(same_context, str) or not same_context))
        or (not context_observed and same_context is not None)
        or provenance.get("source_evidence_type") != "decoded_gameplay_png"
        or provenance.get("independent_observations") is not False
        or provenance.get("multi_crop_not_counted_as_timestamp") is not True
        or provenance.get("capture_manifest_sha256") != capture_manifest_sha256
        or not isinstance(provenance.get("prefix_ocr_model_fingerprint"), str)
        or _SHA256_RE.fullmatch(provenance["prefix_ocr_model_fingerprint"]) is None
        or provenance.get("prefix_crop_count") != len(observations)
    ):
        return None

    try:
        from .hint_card_identity import (
            _load_image,
            _load_source_manifest,
            _raw_supports_wrapped_item,
            _source_overlay_matches,
            _wrapped_amount_proof_matches,
            _wrapped_identity_fragment_proof_matches,
            _wrapped_run_is_consistent,
            _wrapped_static_row,
        )
        from .inventory_suffix import detect as detect_suffix
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None

    source_manifest = _load_source_manifest(root, source_sha256=source_sha256)
    if source_manifest is None:
        return None
    valid_rows = _validated_rows(readings)
    if valid_rows is None:
        return None
    start_ms, end_ms = source_times[0], source_times[-1]
    span = _span_rows(valid_rows, start_ms, end_ms)
    if span is None or len(span) < 2:
        return None
    span_static = []
    for row in span:
        item = _wrapped_static_row(row)
        if item is None:
            return None
        span_static.append(item)
    if not _wrapped_run_is_consistent(span_static):
        return None
    span_times = [item["timestamp"] for item in span_static]
    if span_times != source_times:
        return None
    if any(item["card"]["text"] != candidate["name"] for item in span_static):
        return None
    if any(item["receipt"]["amount"] != candidate["amount"] for item in span_static):
        return None
    if any(item["context"] != same_context for item in span_static):
        return None
    current_span_hashes = _span_hashes(span)
    if current_span_hashes is None:
        return None
    if stored_span_hashes is not None and not _hash_list_equal(
        stored_span_hashes,
        current_span_hashes,
    ):
        return None

    image_cache: dict[str, dict[str, Any]] = {}
    observation_provenance: list[dict[str, Any]] = []
    observed_names: list[str] = []
    observed_bindings: list[Mapping[str, Any]] = []
    for observation, item in zip(observations, span_static):
        if not isinstance(observation, Mapping):
            return None
        if (
            observation.get("timestamp_ms") != item["timestamp"]
            or observation.get("evidence") != item["evidence"]
            or not _wrapped_candidate_observation_matches(observation, item)
        ):
            return None
        loaded = _load_image(
            root,
            item["row"],
            image_cache,
            source_sha256=source_sha256,
            source_manifest=source_manifest,
        )
        if loaded is None:
            return None
        if (
            not _raw_supports_wrapped_item(item, loaded["binding"]["raw"])
            or not _observation_binding_matches(observation, loaded["binding"])
            or not _wrapped_amount_proof_matches(
                observation.get("prefix_amount_proof"),
                item,
                loaded["image"],
            )
            or (
                item["overlay"] is not None
                and not _wrapped_identity_fragment_proof_matches(
                    observation.get("identity_fragment_proof"),
                    item,
                    loaded["image"],
                )
            )
            or (
                item["overlay"] is None
                and observation.get("identity_fragment_proof") is not None
            )
            or observation["prefix_amount_proof"].get("model_fingerprint")
            != provenance["prefix_ocr_model_fingerprint"]
        ):
            return None
        if item["overlay"] is not None and not _source_overlay_matches(
            loaded["image"], item["overlay"]
        ):
            return None
        try:
            suffix = detect_suffix(loaded["image"], item["card"]["box"])
        except (AttributeError, ImportError, IndexError, RuntimeError, TypeError, ValueError):
            return None
        if suffix not in (None, "single_circle", "double_circle"):
            return None
        saved_suffix = observation["card"].get("suffix")
        saved_state = observation["card"].get("suffix_state")
        if saved_state == "present" and suffix != saved_suffix:
            return None
        if (
            rank_state == "present"
            and (saved_state != "present" or suffix != identity_suffix)
        ):
            return None
        # An undetermined rank remains undetermined even if a detector happens
        # to find a marker during replay; the cache must not promote it.
        observed_names.append(item["receipt"]["name"])
        observed_bindings.append(loaded["binding"])
        proof = _cache_observation_provenance(observation, loaded["binding"])
        if proof is None:
            return None
        observation_provenance.append(proof)

    if sorted(set(observed_names)) != raw_names:
        return None
    for field in ("evidence_sha256", "gameplay_sha256", "source_frame_sha256"):
        values = [binding.get(field) for binding in observed_bindings]
        if len(set(values)) != len(values):
            return None
    if stored_observation_provenance is not None and not _hash_list_equal(
        stored_observation_provenance,
        observation_provenance,
    ):
        return None
    return {
        "span_hashes": current_span_hashes,
        "observation_provenance": observation_provenance,
    }


def _validate_single_line_candidate(
    candidate: Mapping[str, Any],
    readings: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    source_sha256: str,
    capture_manifest_sha256: str,
    stored_span_hashes: Any = None,
    stored_observation_provenance: Any = None,
) -> dict[str, Any] | None:
    """Validate a repeated single-line receipt without running OCR.

    The candidate records an unknown suffix state.  A replay detector may
    notice a marker later, but it cannot promote this candidate to a ranked
    effect; persisted observations themselves must retain ``suffix=None``.
    """
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("observation_kind") != _SINGLE_LINE_HINT_KIND
        or candidate.get("kind") != "skill_hint_change"
        or candidate.get("source_sha256") != source_sha256
        or not isinstance(candidate.get("name"), str)
        or not candidate["name"].strip()
        or re.search(r"[○◯◎]\s*$", candidate["name"])
        or type(candidate.get("amount")) is not int
        or not 0 < candidate["amount"] <= 99
    ):
        return None
    observations = candidate.get("observations")
    source_times = candidate.get("source_timestamps_ms")
    identity = candidate.get("identity_proof")
    provenance = candidate.get("provenance")
    raw_names = candidate.get("raw_receipt_name_candidates")
    if (
        not isinstance(observations, list)
        or not isinstance(source_times, list)
        or not isinstance(identity, Mapping)
        or not isinstance(provenance, Mapping)
        or not isinstance(raw_names, list)
        or len(observations) < 2
        or len(observations) != len(source_times)
        or any(type(value) is not int or value < 0 for value in source_times)
        or len(set(source_times)) != len(source_times)
        or source_times != sorted(source_times)
        or any(not isinstance(value, str) or not value.strip() for value in raw_names)
        or raw_names != sorted(set(raw_names))
    ):
        return None
    if (
        identity.get("basis") != _SINGLE_LINE_HINT_BASIS
        or identity.get("card_text") != candidate["name"]
        or identity.get("suffix") is not None
        or identity.get("rank_state") != "undetermined"
        or identity.get("distinct_timestamp_count") != len(source_times)
        or identity.get("geometry_stable") is not True
        or not isinstance(identity.get("same_context"), str)
        or not identity.get("same_context")
        or provenance.get("source_evidence_type") != "decoded_gameplay_png"
        or provenance.get("independent_observations") is not False
        or provenance.get("multi_crop_not_counted_as_timestamp") is not True
        or provenance.get("capture_manifest_sha256") != capture_manifest_sha256
        or not isinstance(provenance.get("prefix_ocr_model_fingerprint"), str)
        or not _SHA256_RE.fullmatch(provenance["prefix_ocr_model_fingerprint"])
        or provenance.get("prefix_crop_count") != len(observations)
    ):
        return None

    try:
        from .hint_card_identity import (
            _load_image,
            _load_source_manifest,
            _raw_supports_item,
            _run_is_consistent,
            _single_line_receipt_name_compatible,
            _source_overlay_matches,
            _static_row,
        )
        from .inventory_suffix import detect as detect_suffix
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None

    source_manifest = _load_source_manifest(root, source_sha256=source_sha256)
    if source_manifest is None:
        return None
    valid_rows = _validated_rows(readings)
    if valid_rows is None:
        return None
    start_ms, end_ms = source_times[0], source_times[-1]
    span = _span_rows(valid_rows, start_ms, end_ms)
    if span is None or len(span) < 2:
        return None
    span_static = []
    for row in span:
        item = _static_row(row)
        if item is None:
            return None
        span_static.append(item)
    if not _run_is_consistent(span_static):
        return None
    span_times = [item["timestamp"] for item in span_static]
    if (
        span_times != source_times
        or any(right - left > _MAX_ROW_GAP_MS for left, right in zip(span_times, span_times[1:]))
        or any(item["context"] != identity["same_context"] for item in span_static)
        or any(item["card"]["text"] != candidate["name"] for item in span_static)
        or any(item["receipt"]["amount"] != candidate["amount"] for item in span_static)
    ):
        return None
    current_span_hashes = _span_hashes(span)
    if current_span_hashes is None:
        return None
    if stored_span_hashes is not None and not _hash_list_equal(
        stored_span_hashes, current_span_hashes
    ):
        return None

    image_cache: dict[str, dict[str, Any]] = {}
    observation_provenance: list[dict[str, Any]] = []
    observed_names: list[str] = []
    observed_bindings: list[Mapping[str, Any]] = []
    for observation, item in zip(observations, span_static):
        if not isinstance(observation, Mapping):
            return None
        saved_card = observation.get("card")
        if not isinstance(saved_card, Mapping):
            return None
        if (
            observation.get("timestamp_ms") != item["timestamp"]
            or observation.get("evidence") != item["evidence"]
            or not _candidate_observation_matches(observation, item)
            or saved_card.get("suffix") is not None
            or saved_card.get("suffix_state") != "undetermined"
            or not _single_line_receipt_name_compatible(
                item["card"]["text"], item["receipt"]["name"]
            )
            or not _single_line_prefix_proof_matches(
                observation,
                item,
                candidate["amount"],
                provenance["prefix_ocr_model_fingerprint"],
            )
        ):
            return None
        loaded = _load_image(
            root,
            item["row"],
            image_cache,
            source_sha256=source_sha256,
            source_manifest=source_manifest,
        )
        if (
            loaded is None
            or not _raw_supports_item(item, loaded["binding"]["raw"])
            or not _observation_binding_matches(observation, loaded["binding"])
            or not _source_overlay_matches(loaded["image"], item["overlay"])
            or not _single_line_prefix_proof_matches(
                observation,
                item,
                candidate["amount"],
                provenance["prefix_ocr_model_fingerprint"],
                image=loaded["image"],
            )
        ):
            return None
        try:
            suffix = detect_suffix(loaded["image"], item["card"]["box"])
        except (AttributeError, ImportError, IndexError, RuntimeError, TypeError, ValueError):
            return None
        # Preparation selected this mode only when the source detector saw no
        # suffix.  A changed replay interpretation is a stale proof, even if
        # the card text itself remains visible; require fresh preparation
        # rather than promoting a newly detected rank into an unknown-rank
        # candidate.
        if suffix is not None:
            return None
        observed_names.append(item["receipt"]["name"])
        observed_bindings.append(loaded["binding"])
        proof = _cache_observation_provenance(observation, loaded["binding"])
        if proof is None:
            return None
        observation_provenance.append(proof)

    if sorted(set(observed_names)) != raw_names:
        return None
    for field in ("evidence_sha256", "gameplay_sha256", "source_frame_sha256"):
        values = [binding.get(field) for binding in observed_bindings]
        if len(set(values)) != len(values):
            return None
    if stored_observation_provenance is not None and not _hash_list_equal(
        stored_observation_provenance, observation_provenance
    ):
        return None
    return {
        "span_hashes": current_span_hashes,
        "observation_provenance": observation_provenance,
    }


def _validate_candidate(
    candidate: Mapping[str, Any],
    readings: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    source_sha256: str,
    capture_manifest_sha256: str,
    stored_span_hashes: Any = None,
    stored_observation_provenance: Any = None,
) -> dict[str, Any] | None:
    """Validate a saved candidate without constructing an OCR reader."""
    if (
        isinstance(candidate, Mapping)
        and candidate.get("observation_kind") == _SINGLE_LINE_HINT_KIND
    ):
        return _validate_single_line_candidate(
            candidate,
            readings,
            root,
            source_sha256=source_sha256,
            capture_manifest_sha256=capture_manifest_sha256,
            stored_span_hashes=stored_span_hashes,
            stored_observation_provenance=stored_observation_provenance,
        )
    if (
        isinstance(candidate, Mapping)
        and candidate.get("observation_kind") == "wrapped_hint_receipt"
    ):
        return _validate_wrapped_candidate(
            candidate,
            readings,
            root,
            source_sha256=source_sha256,
            capture_manifest_sha256=capture_manifest_sha256,
            stored_span_hashes=stored_span_hashes,
            stored_observation_provenance=stored_observation_provenance,
        )
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("kind") != "skill_hint_change"
        or candidate.get("source_sha256") != source_sha256
        or not isinstance(candidate.get("name"), str)
        or not candidate["name"].strip()
        or type(candidate.get("amount")) is not int
        or not 0 < candidate["amount"] <= 99
    ):
        return None
    observations = candidate.get("observations")
    source_times = candidate.get("source_timestamps_ms")
    identity = candidate.get("identity_proof")
    provenance = candidate.get("provenance")
    if (
        not isinstance(observations, list)
        or not isinstance(source_times, list)
        or not isinstance(identity, Mapping)
        or not isinstance(provenance, Mapping)
        or len(observations) < 2
        or any(type(value) is not int or value < 0 for value in source_times)
        or len(set(source_times)) != len(source_times)
        or source_times != sorted(source_times)
        or len(observations) != len(source_times)
        or candidate.get("raw_receipt_name_candidates") is None
    ):
        return None
    if (
        identity.get("card_text") != candidate["name"]
        or not isinstance(identity.get("same_context"), str)
        or not identity.get("same_context")
        or identity.get("suffix") not in ("single_circle", "double_circle")
        or identity.get("distinct_timestamp_count") != len(source_times)
        or identity.get("geometry_stable") is not True
        or provenance.get("independent_observations") is not False
        or provenance.get("multi_crop_not_counted_as_timestamp") is not True
        or provenance.get("capture_manifest_sha256") != capture_manifest_sha256
        or not isinstance(provenance.get("prefix_ocr_model_fingerprint"), str)
        or not _SHA256_RE.fullmatch(provenance["prefix_ocr_model_fingerprint"])
        or provenance.get("prefix_crop_count") != len(observations)
        or not isinstance(candidate.get("raw_receipt_name_candidates"), list)
        or any(not isinstance(value, str) or not value for value in candidate["raw_receipt_name_candidates"])
    ):
        return None

    try:
        from .hint_card_identity import (
            _load_image,
            _load_source_manifest,
            _raw_supports_item,
            _run_is_consistent,
            _source_overlay_matches,
            _static_row,
        )
        from .inventory_suffix import detect as detect_suffix
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None

    source_manifest = _load_source_manifest(root, source_sha256=source_sha256)
    if source_manifest is None:
        return None
    valid_rows = _validated_rows(readings)
    if valid_rows is None:
        return None
    by_key = {_row_key(row): row for row in valid_rows}
    if any(key is None for key in by_key):
        return None
    start_ms, end_ms = source_times[0], source_times[-1]
    span = _span_rows(valid_rows, start_ms, end_ms)
    if span is None or len(span) < 2:
        return None
    span_static = []
    for row in span:
        item = _static_row(row)
        if item is None:
            return None
        span_static.append(item)
    if not _run_is_consistent(span_static):
        return None
    span_times = [item["timestamp"] for item in span_static]
    if span_times != source_times:
        return None
    if any(
        right - left > _MAX_ROW_GAP_MS
        for left, right in zip(span_times, span_times[1:])
    ):
        return None
    if any(item["context"] != identity["same_context"] for item in span_static):
        return None
    if any(item["card"]["text"] != candidate["name"] for item in span_static):
        return None
    if any(item["receipt"]["amount"] != candidate["amount"] for item in span_static):
        return None

    current_span_hashes = _span_hashes(span)
    if current_span_hashes is None:
        return None
    if stored_span_hashes is not None and not _hash_list_equal(stored_span_hashes, current_span_hashes):
        return None

    image_cache: dict[str, dict[str, Any]] = {}
    observation_provenance: list[dict[str, Any]] = []
    observed_names: list[str] = []
    observed_suffixes: list[str] = []
    observed_prefixes = []
    for observation, item in zip(observations, span_static):
        if not isinstance(observation, Mapping):
            return None
        timestamp = observation.get("timestamp_ms")
        evidence = observation.get("evidence")
        if timestamp != item["timestamp"] or evidence != item["evidence"]:
            return None
        if not _candidate_observation_matches(observation, item):
            return None
        if not _prefix_proof_matches(observation, item, candidate["amount"]):
            return None
        loaded = _load_image(
            root,
            item["row"],
            image_cache,
            source_sha256=source_sha256,
            source_manifest=source_manifest,
        )
        if (
            loaded is None
            or not _raw_supports_item(item, loaded["binding"]["raw"])
            or not _observation_binding_matches(observation, loaded["binding"])
        ):
            return None
        if not _source_overlay_matches(loaded["image"], item["overlay"]):
            return None
        try:
            suffix = detect_suffix(loaded["image"], item["card"]["box"])
        except (AttributeError, ImportError, IndexError, RuntimeError, TypeError, ValueError):
            return None
        if suffix != observation["card"].get("suffix") or suffix != identity["suffix"]:
            return None
        observed_names.append(item["receipt"]["name"])
        observed_suffixes.append(suffix)
        observed_prefixes.append(observation["prefix_amount_proof"])
        proof = _cache_observation_provenance(observation, loaded["binding"])
        if proof is None:
            return None
        observation_provenance.append(proof)

    if len(set(observed_suffixes)) != 1 or len(observed_suffixes) < 2:
        return None
    if sorted(set(observed_names)) != candidate["raw_receipt_name_candidates"]:
        return None
    if stored_observation_provenance is not None and not _hash_list_equal(
        stored_observation_provenance,
        observation_provenance,
    ):
        return None
    return {
        "span_hashes": current_span_hashes,
        "observation_provenance": observation_provenance,
    }


def _candidate_cache_entry(
    candidate: Mapping[str, Any],
    validation: Mapping[str, Any],
    *,
    source_sha256: str,
    capture_manifest_sha256: str,
) -> dict[str, Any]:
    entry = deepcopy(dict(candidate))
    entry["cache_provenance"] = {
        "schema": CACHE_SCHEMA,
        "source_sha256": source_sha256,
        "capture_manifest_sha256": capture_manifest_sha256,
        "span_rows": deepcopy(validation["span_hashes"]),
        "observations": deepcopy(validation["observation_provenance"]),
    }
    return entry


def _payload(
    candidates: Sequence[Mapping[str, Any]],
    readings: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    source_sha256: str,
) -> dict[str, Any] | None:
    if not isinstance(source_sha256, str) or not _SHA256_RE.fullmatch(source_sha256):
        return None
    valid_rows = _validated_rows(readings)
    if valid_rows is None:
        return None
    capture_manifest_sha256 = _source_manifest_hash(root)
    if capture_manifest_sha256 is None:
        return None
    entries = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            return None
        validation = _validate_candidate(
            candidate,
            valid_rows,
            root,
            source_sha256=source_sha256,
            capture_manifest_sha256=capture_manifest_sha256,
        )
        if validation is None:
            return None
        entries.append(
            _candidate_cache_entry(
                candidate,
                validation,
                source_sha256=source_sha256,
                capture_manifest_sha256=capture_manifest_sha256,
            )
        )
    spans = [
        set(entry["source_timestamps_ms"])
        for entry in entries
    ]
    if any(left & right for index, left in enumerate(spans) for right in spans[index + 1 :]):
        return None
    return {
        "schema": CACHE_SCHEMA,
        "source_sha256": source_sha256,
        "capture_manifest_sha256": capture_manifest_sha256,
        "candidate_count": len(entries),
        "candidates": entries,
    }


def save(
    path: str | Path,
    candidates: Sequence[Mapping[str, Any]],
    readings: Sequence[Mapping[str, Any]],
    root: str | Path,
    *,
    source_sha256: str,
) -> dict[str, Any]:
    """Validate and write a new immutable cache artifact.

    The target must not already exist.  A ``ValueError`` means the candidate
    or its source proof did not satisfy the cache contract.
    """
    output = Path(path)
    evidence_root = Path(root)
    if output.exists():
        raise FileExistsError(f"Hint-card cache already exists: {output}")
    payload = _payload(
        candidates,
        readings,
        evidence_root,
        source_sha256=source_sha256,
    )
    if payload is None:
        raise ValueError("Hint-card cache input failed source validation.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # The initial existence check gives a useful error in the common case;
    # exclusive creation also closes the check/write race and preserves the
    # immutable-artifact contract when two preparation jobs share a path.
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return payload


def load(
    readings: Sequence[Mapping[str, Any]],
    root: str | Path,
    source_sha256: str,
    *,
    cache_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load and source-revalidate a persisted cache without OCR.

    A missing cache returns an empty list.  A present malformed, foreign,
    stale, or partially invalid cache raises ``ValueError`` so callers cannot
    silently publish a report after losing prepared evidence.
    ``cache_path`` selects an explicit artifact; otherwise the conventional
    ``root/hint-card-recovery.json`` is used.
    """
    evidence_root = Path(root)
    path = Path(cache_path) if cache_path is not None else evidence_root / "hint-card-recovery.json"
    if not path.exists():
        return []
    if not path.is_file():
        raise ValueError(f"Hint-card cache path is not a regular file: {path}")
    if not isinstance(source_sha256, str) or not _SHA256_RE.fullmatch(source_sha256):
        raise ValueError("Hint-card cache source SHA-256 is invalid.")
    valid_rows = _validated_rows(readings)
    if valid_rows is None:
        raise ValueError("Current gameplay readings are invalid for hint-card cache replay.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"Hint-card cache is not valid JSON: {path}") from None
    if not isinstance(payload, Mapping):
        raise ValueError("Hint-card cache root must be a JSON object.")
    capture_manifest_sha256 = _source_manifest_hash(evidence_root)
    if (
        payload.get("schema") != CACHE_SCHEMA
        or payload.get("source_sha256") != source_sha256
        or not isinstance(payload.get("capture_manifest_sha256"), str)
        or payload.get("capture_manifest_sha256") != capture_manifest_sha256
        or type(payload.get("candidate_count")) is not int
        or not isinstance(payload.get("candidates"), list)
        or payload["candidate_count"] != len(payload["candidates"])
    ):
        raise ValueError("Hint-card cache header is stale, foreign, or malformed.")
    entries = payload["candidates"]
    candidates: list[dict[str, Any]] = []
    spans = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("Hint-card cache contains a malformed candidate.")
        cache_provenance = entry.get("cache_provenance")
        if not isinstance(cache_provenance, Mapping):
            raise ValueError("Hint-card cache candidate is missing provenance.")
        if (
            cache_provenance.get("schema") != CACHE_SCHEMA
            or cache_provenance.get("source_sha256") != source_sha256
            or cache_provenance.get("capture_manifest_sha256") != capture_manifest_sha256
            or not isinstance(cache_provenance.get("span_rows"), list)
            or not isinstance(cache_provenance.get("observations"), list)
        ):
            raise ValueError("Hint-card cache candidate provenance is stale or malformed.")
        validation = _validate_candidate(
            entry,
            valid_rows,
            evidence_root,
            source_sha256=source_sha256,
            capture_manifest_sha256=capture_manifest_sha256,
            stored_span_hashes=cache_provenance["span_rows"],
            stored_observation_provenance=cache_provenance["observations"],
        )
        if validation is None:
            raise ValueError("Hint-card cache candidate failed source validation.")
        span = set(entry["source_timestamps_ms"])
        if any(span & prior for prior in spans):
            raise ValueError("Hint-card cache candidates have overlapping source spans.")
        spans.append(span)
        candidate = deepcopy(dict(entry))
        candidate.pop("cache_provenance", None)
        candidates.append(candidate)
    return candidates


def prepare(
    report: Mapping[str, Any],
    root: str | Path,
    output: str | Path,
    *,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> dict[str, Any]:
    """Run explicit source preparation and persist a new cache artifact."""
    if not isinstance(report, Mapping):
        raise ValueError("Report must be a JSON object.")
    source = report.get("source")
    tracking = report.get("gameplay_tracking")
    readings = tracking.get("readings") if isinstance(tracking, Mapping) else None
    source_sha256 = source.get("sha256") if isinstance(source, Mapping) else None
    if not isinstance(readings, list) or not isinstance(source_sha256, str):
        raise ValueError("Report lacks source SHA-256 or gameplay readings.")
    if start_ms is not None or end_ms is not None:
        if type(start_ms) is not int or type(end_ms) is not int or start_ms < 0 or end_ms < start_ms:
            raise ValueError("Preparation bounds must be nonnegative integer milliseconds.")
        selected = [
            row
            for row in readings
            if isinstance(row, Mapping)
            and start_ms <= row.get("source_timestamp_ms", -1) <= end_ms
        ]
    else:
        selected = readings
    from .hint_card_identity import recover

    candidates = recover(selected, root, source_sha256=source_sha256)
    return save(
        output,
        candidates,
        selected,
        root,
        source_sha256=source_sha256,
    )


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Existing full-recording report JSON")
    parser.add_argument("--output", required=True, type=Path, help="New cache path; must not exist")
    parser.add_argument("--evidence-root", type=Path, help="Report/cache evidence root (defaults to report parent)")
    parser.add_argument("--start-ms", type=int)
    parser.add_argument("--end-ms", type=int)
    args = parser.parse_args(argv)
    root = args.evidence_root or args.report.parent
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        payload = prepare(
            report,
            root,
            args.output,
            start_ms=args.start_ms,
            end_ms=args.end_ms,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "schema": CACHE_SCHEMA,
                "output": str(args.output),
                "candidate_count": payload["candidate_count"],
                "source_sha256": payload["source_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["CACHE_SCHEMA", "load", "main", "prepare", "save"]
