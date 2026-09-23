"""Bounded source-pixel OCR refinement for animated training gain badges.

The ordinary training-result crops are deliberately small.  During the card
animation a sparkle, a large success overlay, or a neighbouring transition
can cover a glyph in that crop even though the same badge is visible a few
pixels farther out.  This module requests two fixed, *relative* alternatives
around the existing gain geometry.  It retains every OCR result and only
marks a result as an eligible ``expanded_gain`` candidate when the recognizer
returned one complete signed amount above the family floor.

The helper is source-only.  It accepts a gameplay pane and an injected OCR
callback; it never receives a claimed amount, a balance, a timestamp filter,
or a report label.  Conflicting source crops remain in ``regions`` so the
normal crop resolver can abstain instead of selecting a convenient variant.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from .layout import ORIGIN_X, clamp, inside_pane, pane_size
from .training_badge_localization import FIELDS, gain_box, wide_gain_box
from .ocr_confidence import confidence_percent


VERSION = 1
SCHEMA = "tracen-replay/training-gain-source-refinement-v1"
MIN_CONFIDENCE = 80.0
_SIGNED_EXACT = re.compile(r"^\s*\+\s*(\d{1,3})\s*$")


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Training gain refinement coordinate is not numeric.")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError('Training gain refinement coordinate is not finite.') from exc
    if not math.isfinite(value):
        raise ValueError("Training gain refinement coordinate is not finite.")
    return value


def _integer_box(value: Sequence[float], *, name: str) -> list[int]:
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise ValueError(f"Training gain refinement {name} box is invalid.")
    coordinates = [_number(item) for item in value]
    if any(float(item) != int(item) for item in coordinates):
        raise ValueError(f"Training gain refinement {name} box is not integral.")
    left, top, right, bottom = (int(item) for item in coordinates)
    if not inside_pane((left, top, right, bottom)):
        raise ValueError(f"Training gain refinement {name} box leaves gameplay bounds.")
    return [left, top, right, bottom]


def _inner_box(field: str) -> list[int]:
    """Return a small extension of the canonical badge box.

    The extension follows the fixed card geometry and does not depend on the
    amount or on which recording supplied the frame.
    """

    left, top, right, bottom = gain_box(field)
    return _integer_box(clamp((left, top - 12, right + 6, bottom)), name="inner")


def _outer_box(field: str) -> list[int]:
    """Return a bounded outer view of the same card's broad badge band.

    The offsets are proportions of the established broad card box.  They keep
    the outer edge that recovered a clipped terminal glyph while preserving a
    stable field anchor for every stat card.
    """

    left, top, right, bottom = wide_gain_box(field)
    width = right - left
    height = bottom - top
    return _integer_box(
        clamp((
            left,
            top + max(1, int(round(height * 0.04))),
            right + max(1, int(round(width * (13 / 212)))),
            bottom + max(1, int(round(height * (5 / 97)))),
        )),
        name="outer",
    )


def refinement_requests(
    fields: Iterable[str] = FIELDS,
) -> list[tuple[str, list[int], dict[str, Any]]]:
    """Return deterministic relative crop requests for valid fields."""

    selected = []
    for field in fields:
        if field not in FIELDS or field in selected:
            continue
        selected.append(field)
    requests: list[tuple[str, list[int], dict[str, Any]]] = []
    for field in selected:
        for role, box in (("inner", _inner_box(field)), ("outer", _outer_box(field))):
            requests.append(
                (
                    f"expanded_gain.{role}.{field}",
                    box,
                    {
                        "field": field,
                        "source_refinement_role": role,
                        "source_refinement_geometry": (
                            "canonical_gain_relative_inner"
                            if role == "inner"
                            else "wide_gain_relative_outer"
                        ),
                    },
                )
            )
    return requests


def _rgb_array(pane: Any):
    try:
        import numpy as np

        if hasattr(pane, "convert"):
            array = np.asarray(pane.convert("RGB"))
        else:
            array = np.asarray(pane)
    except (ImportError, TypeError, ValueError) as exc:
        raise ValueError("Training gain refinement pane is not readable RGB data.") from exc
    if getattr(array, "ndim", None) != 3 or array.shape[2] != 3:
        raise ValueError("Training gain refinement pane is not an RGB image.")
    if (array.shape[1], array.shape[0]) != pane_size():
        raise ValueError("Training gain refinement expects the gameplay pane.")
    return array


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Training gain refinement OCR confidence is invalid.")
    value = confidence_percent(value)
    if value is None:
        raise ValueError("Training gain refinement OCR confidence is invalid.")
    # RapidOCR returns a fraction; repository observations use percentages.
    return value * 100.0 if 0.0 <= value <= 1.0 else value


def _recognition_pair(value: Any) -> tuple[str, float]:
    if isinstance(value, Mapping):
        text = value.get("text", value.get("raw_text", ""))
        confidence = value.get("confidence", value.get("score"))
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        text, confidence = value
    else:
        raise ValueError("Training gain refinement recognizer returned an invalid result.")
    return _text(text), _confidence(confidence)


def _metadata(value: Mapping[str, Any] | None, gameplay_sha256: str) -> dict[str, Any]:
    result: dict[str, Any] = {"gameplay_sha256": gameplay_sha256}
    if not isinstance(value, Mapping):
        return result
    for key in (
        "source_timestamp_ms",
        "evidence",
        "source_sha256",
        "source_frame_sha256",
        "source_frame_evidence",
        "source_frame_id",
        "evidence_sha256",
        "raw_sha256",
        "engine_fingerprint",
    ):
        item = value.get(key)
        if key == "source_timestamp_ms":
            if type(item) is int and item >= 0:
                result[key] = item
        elif isinstance(item, str) and item.strip():
            result[key] = item.strip()
    return result


def _source_observation(
    request: tuple[str, list[int], dict[str, Any]],
    crop: Any,
    recognized: Any,
    metadata: Mapping[str, Any],
) -> tuple[str, dict[str, Any], str | None]:
    region, box, request_metadata = request
    try:
        text, confidence = _recognition_pair(recognized)
    except ValueError:
        text, confidence = "", 0.0
        rejection = "invalid_recognizer_result"
    else:
        rejection = None
    match = _SIGNED_EXACT.fullmatch(text)
    if match is None:
        rejection = rejection or "unsigned_or_clipped_badge_text"
    elif confidence < MIN_CONFIDENCE:
        rejection = "source_refinement_confidence_below_floor"
    observation: dict[str, Any] = {
        "text": text,
        "raw_text": text,
        "confidence": round(confidence, 4),
        "box": list(box),
        "role": "amount_crop_candidate",
        "source_role": "amount_crop_candidate",
        "input_eligible": True,
        "source_refinement_schema": SCHEMA,
        "source_refinement_role": request_metadata["source_refinement_role"],
        "source_refinement_geometry": request_metadata["source_refinement_geometry"],
        "source_refinement_verified": True,
        "source_refinement_pixel_bound": True,
        "source_crop_sha256": hashlib.sha256(crop.tobytes()).hexdigest(),
        "source_pixel_verified": True,
        "source_pixel_basis": "relative_training_gain_source_crop",
        "source_observation_basis": "source_pixel_refined_training_gain",
    }
    observation.update(metadata)
    if match is not None:
        observation["amount"] = int(match.group(1))
        observation["normalization"] = "signed_amount"
        observation["suffix"] = ""
    else:
        observation["amount"] = None
        observation["normalization"] = None
        observation["suffix"] = None
    return region, observation, rejection


def refine_training_gain_regions(
    pane: Any,
    *,
    recognize: Callable[[Sequence[Any]], Iterable[Any]],
    fields: Iterable[str] = FIELDS,
    header: Any = "Training",
    result_grid: Any = True,
    preview: Any = False,
    source_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read relative source crops while preserving every alternative.

    The returned ``regions`` can be merged into a raw reader record.  A
    conflicting pair is deliberately returned with ``status='unresolved'``;
    callers may retain its diagnostics, but the normal resolver will not turn
    either value into a canonical gain.
    """

    result: dict[str, Any] = {
        "schema_version": SCHEMA,
        "version": VERSION,
        "status": "gated",
        "regions": {},
        "observations": [],
        "rejections": {},
        "policy": {
            "minimum_confidence": MIN_CONFIDENCE,
            "signed_text_only": True,
            "uses_expected_amount": False,
            "uses_balance_arithmetic": False,
        },
    }
    normalized_header = _text(header).casefold()
    if normalized_header != "training":
        result["reason"] = "training_header_not_proven"
        return result
    if result_grid is not True:
        result["reason"] = "training_result_grid_not_proven"
        return result
    if preview is True:
        result["reason"] = "preview_screen"
        return result
    requests = refinement_requests(fields)
    if not requests:
        result["status"] = "empty"
        return result
    try:
        rgb = _rgb_array(pane)
        gameplay_sha256 = hashlib.sha256(rgb.tobytes()).hexdigest()
        metadata = _metadata(source_metadata, gameplay_sha256)
        crops = [
            rgb[box[1] : box[3], box[0] - ORIGIN_X : box[2] - ORIGIN_X].copy()
            for _region, box, _request_metadata in requests
        ]
        recognized = list(recognize(crops))
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        result["reason"] = type(exc).__name__
        return result
    result["metadata"] = metadata
    # Keep the pane fingerprint at the sidecar root as well as in the
    # per-observation metadata.  Cached readers can therefore validate the
    # source without depending on a particular metadata nesting convention.
    result["gameplay_sha256"] = gameplay_sha256
    result["requests"] = [
        {"region": region, "box": list(box), **request_metadata}
        for region, box, request_metadata in requests
    ]
    if len(recognized) != len(requests):
        result["reason"] = "result_count_mismatch"
        return result
    by_field: dict[str, set[int]] = {}
    eligible_count = 0
    for request, crop, item in zip(requests, crops, recognized):
        region, observation, rejection = _source_observation(request, crop, item, metadata)
        result["observations"].append(dict(region=region, **observation))
        result["regions"][region] = observation
        if rejection:
            result["rejections"][region] = rejection
        else:
            eligible_count += 1
            field = request[2]["field"]
            by_field.setdefault(field, set()).add(int(observation["amount"]))
    conflicts = {field: sorted(values) for field, values in by_field.items() if len(values) > 1}
    if conflicts:
        result["conflicts"] = conflicts
        result["status"] = "unresolved"
        result["reason"] = "conflicting_source_refinement_crops"
    elif eligible_count:
        result["status"] = "refined"
    else:
        result["status"] = "unresolved"
        result["reason"] = "no_eligible_source_refinement_amount"
    return result


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_REFINEMENT_VALIDATION = SCHEMA + "/pixel-binding-v1"


def _hash_crop(rgb: Any, box: Sequence[int]) -> str:
    """Hash the exact RGB pixels addressed by a full-pane crop box."""

    left, top, right, bottom = box
    crop = rgb[
        top:bottom,
        left - ORIGIN_X : right - ORIGIN_X,
    ]
    expected_shape = (bottom - top, right - left, 3)
    if getattr(crop, "shape", None) != expected_shape:
        raise ValueError("Training gain refinement crop leaves gameplay pixels.")
    return hashlib.sha256(crop.tobytes()).hexdigest()


def _refinement_request_map() -> dict[str, tuple[list[int], dict[str, Any]]]:
    """Return the production-owned geometry for every refinement role."""

    return {
        region: (list(box), dict(metadata))
        for region, box, metadata in refinement_requests(FIELDS)
    }


def _validate_refinement_observation(
    region: str,
    observation: Mapping[str, Any],
    *,
    expected_box: Sequence[int],
    expected_metadata: Mapping[str, Any],
    rgb: Any,
    gameplay_sha256: str,
) -> dict[str, Any]:
    """Validate one refinement observation against the supplied source pane.

    The persisted hash is useful only when it is recomputed from the same
    pane.  In particular, a well-formed hash on a copied or self-rehashed
    candidate is not source proof.  Keep this check independent of OCR
    confidence or amount selection so it cannot become a hidden amount vote.
    """

    if not isinstance(observation, Mapping):
        raise ValueError("Training gain refinement observation is invalid.")
    if observation.get("source_refinement_schema") != SCHEMA:
        raise ValueError("Training gain refinement schema is invalid.")
    if observation.get("source_refinement_verified") is not True:
        raise ValueError("Training gain refinement verification marker is missing.")
    if observation.get("source_pixel_verified") is not True:
        raise ValueError("Training gain refinement lacks source-pixel proof.")
    if observation.get("source_pixel_basis") != "relative_training_gain_source_crop":
        raise ValueError("Training gain refinement pixel basis is invalid.")
    if observation.get("source_observation_basis") != "source_pixel_refined_training_gain":
        raise ValueError("Training gain refinement observation basis is invalid.")
    role = expected_metadata["source_refinement_role"]
    geometry = expected_metadata["source_refinement_geometry"]
    if observation.get("source_refinement_role") != role:
        raise ValueError("Training gain refinement role disagrees with its region.")
    if observation.get("source_refinement_geometry") != geometry:
        raise ValueError("Training gain refinement geometry disagrees with its region.")

    box = _integer_box(observation.get("box"), name="observation")
    if box != list(expected_box):
        raise ValueError("Training gain refinement box is not the declared role box.")
    declared_crop_sha = observation.get("source_crop_sha256")
    if not isinstance(declared_crop_sha, str) or _SHA256.fullmatch(declared_crop_sha) is None:
        raise ValueError("Training gain refinement crop hash is invalid.")
    actual_crop_sha = _hash_crop(rgb, box)
    if declared_crop_sha.casefold() != actual_crop_sha:
        raise ValueError("Training gain refinement crop pixels disagree with source.")

    declared_gameplay_sha = observation.get("gameplay_sha256")
    if not isinstance(declared_gameplay_sha, str) or _SHA256.fullmatch(declared_gameplay_sha) is None:
        raise ValueError("Training gain refinement gameplay hash is invalid.")
    if declared_gameplay_sha.casefold() != gameplay_sha256:
        raise ValueError("Training gain refinement gameplay pixels disagree with source.")

    # Persisted observations already contain percentages. A stored 1 means
    # 1 percent, not the recognizer's fractional representation of 100.
    confidence = confidence_percent(observation.get("confidence"))
    if confidence is None:
        raise ValueError("Training gain refinement confidence is outside 0..100.")
    text = _text(observation.get("raw_text", observation.get("text", "")))
    declared_text = _text(observation.get("text", ""))
    if declared_text != text:
        raise ValueError("Training gain refinement text fields disagree.")
    match = _SIGNED_EXACT.fullmatch(text)
    amount = observation.get("amount")
    if match is not None:
        parsed_amount = int(match.group(1))
        if type(amount) is not int or amount != parsed_amount or not 0 <= amount <= 999:
            raise ValueError("Training gain refinement amount disagrees with OCR text.")
        if observation.get("normalization") != "signed_amount":
            raise ValueError("Training gain refinement amount normalization is invalid.")
        if observation.get("suffix") not in ("", None):
            raise ValueError("Training gain refinement signed amount has a suffix.")
    else:
        # Rejected OCR remains a diagnostic record, but it must not smuggle a
        # parsed amount into the accepted source channel.
        if amount is not None or observation.get("normalization") not in (None, ""):
            raise ValueError("Training gain refinement rejected text carries an amount.")

    result = dict(observation)
    result.update(
        {
            "region": region,
            "box": list(box),
            "confidence": round(confidence, 4),
            "source_crop_sha256": actual_crop_sha,
            "gameplay_sha256": gameplay_sha256,
            "source_refinement_pixel_bound": True,
            "source_refinement_validation": _REFINEMENT_VALIDATION,
        }
    )
    return result


def validate_training_gain_refinement(
    raw: Mapping[str, Any],
    pane: Any,
) -> dict[str, Any]:
    """Validate a cached source-refinement record against gameplay pixels.

    ``raw`` is the original neural observation containing
    ``training_gain_source_refinement``.  The return value is a new mapping;
    callers may merge its ``regions`` only when ``status`` is ``validated``.
    A rejected record contributes no accepted regions.  Non-signed or
    low-confidence records are retained as diagnostics when their source
    geometry and hashes are valid, while any proof mismatch rejects the
    complete refinement record.
    """

    result: dict[str, Any] = {
        "status": "absent",
        "source_status": None,
        "regions": {},
        "rejections": {},
        "validated_regions": [],
    }
    if not isinstance(raw, Mapping):
        result.update(status="rejected", reason="raw_observation_not_mapping")
        return result
    refinement = raw.get("training_gain_source_refinement")
    if refinement is None and raw.get("schema_version") == SCHEMA:
        # This makes the validator useful for a sidecar loaded independently
        # of its parent raw record as well as for the normal cached path.
        refinement = raw
    if refinement is None:
        return result
    if not isinstance(refinement, Mapping):
        result.update(status="rejected", reason="refinement_not_mapping")
        return result
    result["source_status"] = refinement.get("status")
    if refinement.get("schema_version") != SCHEMA or refinement.get("version") != VERSION:
        result.update(status="rejected", reason="refinement_schema_mismatch")
        return result
    metadata = refinement.get("metadata")
    if refinement.get("preview") is True or (
        isinstance(metadata, Mapping) and metadata.get("preview") is True
    ):
        result.update(status="rejected", reason="refinement_preview_screen")
        return result
    if refinement.get("status") not in {"refined", "unresolved"}:
        # ``gated`` and ``empty`` records are useful diagnostics from the
        # producer, but they contain no accepted source refinement.  They must
        # not become an external companion that looks like a usable source
        # record merely because a caller supplied a pane.
        result.update(status="rejected", reason="refinement_status_not_eligible")
        return result
    regions = refinement.get("regions")
    if not isinstance(regions, Mapping):
        result.update(status="rejected", reason="refinement_regions_missing")
        return result

    try:
        rgb = _rgb_array(pane)
        gameplay_sha256 = hashlib.sha256(rgb.tobytes()).hexdigest()
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        result.update(status="rejected", reason=type(exc).__name__)
        return result

    for owner, label in ((raw, "raw"), (refinement, "refinement")):
        declared = owner.get("gameplay_sha256")
        if declared is None and label == "refinement":
            metadata = owner.get("metadata")
            if isinstance(metadata, Mapping):
                declared = metadata.get("gameplay_sha256")
        if declared is None:
            if label == "refinement":
                result.update(status="rejected", reason="refinement_gameplay_hash_missing")
                return result
            continue
        if not isinstance(declared, str) or _SHA256.fullmatch(declared) is None:
            result.update(status="rejected", reason=f"{label}_gameplay_hash_invalid")
            return result
        if declared.casefold() != gameplay_sha256:
            result.update(status="rejected", reason=f"{label}_gameplay_pixels_changed")
            return result

    expected_requests = _refinement_request_map()
    requests = refinement.get("requests")
    if not isinstance(requests, list):
        result.update(status="rejected", reason="refinement_requests_missing")
        return result
    requested_regions: set[str] = set()
    try:
        for request in requests:
            if not isinstance(request, Mapping):
                raise ValueError("refinement request is invalid")
            region = request.get("region")
            if not isinstance(region, str) or region in requested_regions:
                raise ValueError("refinement request region is invalid")
            expected = expected_requests.get(region)
            if expected is None:
                raise ValueError("refinement request region is not production geometry")
            box = _integer_box(request.get("box"), name="request")
            if box != expected[0]:
                raise ValueError("refinement request box disagrees with production geometry")
            if request.get("field") != expected[1]["field"]:
                raise ValueError("refinement request field disagrees with its region")
            if request.get("source_refinement_role") != expected[1]["source_refinement_role"]:
                raise ValueError("refinement request role disagrees with its region")
            if request.get("source_refinement_geometry") != expected[1]["source_refinement_geometry"]:
                raise ValueError("refinement request geometry disagrees with its region")
            requested_regions.add(region)
        if set(regions) != requested_regions:
            raise ValueError("refinement regions do not match source requests")
        checked_regions: dict[str, Any] = {}
        for region in sorted(requested_regions):
            expected_box, expected_metadata = expected_requests[region]
            checked_regions[region] = _validate_refinement_observation(
                region,
                regions[region],
                expected_box=expected_box,
                expected_metadata=expected_metadata,
                rgb=rgb,
                gameplay_sha256=gameplay_sha256,
            )
    except (TypeError, ValueError, KeyError) as exc:
        result.update(status="rejected", reason=str(exc) or type(exc).__name__)
        return result

    # External inspection envelopes carry the decoded source-frame identity
    # at the envelope/metadata level.  Older producer runs did not duplicate
    # that identity on every region, so carry only the already-declared,
    # source-bound fields onto the checked observations before attaching them
    # to the ordinary parser.  This keeps physical-frame deduplication intact
    # without deriving identity from a timestamp or an evidence filename.
    source_bindings: dict[str, Any] = {}
    for key in (
        "source_frame_sha256",
        "source_frame_evidence",
        "source_frame_id",
        "source_timestamp_ms",
        "evidence",
        "source_sha256",
        "evidence_sha256",
        "raw_sha256",
    ):
        value = refinement.get(key)
        if value is None and isinstance(metadata, Mapping):
            value = metadata.get(key)
        if key == "source_frame_sha256":
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                continue
            value = value.casefold()
        elif key == "source_timestamp_ms":
            if type(value) is not int or value < 0:
                continue
        elif value is not None and (not isinstance(value, str) or not value.strip()):
            continue
        if value is not None:
            source_bindings[key] = value
    for region, checked in checked_regions.items():
        for key, value in source_bindings.items():
            existing = checked.get(key)
            if existing is not None and existing != value:
                result.update(status="rejected", reason="source_binding_disagrees")
                return result
            checked[key] = value

    # The ordinary parser may also carry a merged copy in raw["regions"].
    # If it disagrees with the validated nested sidecar, do not let insertion
    # order decide which proof reaches the resolver.
    raw_regions = raw.get("regions")
    if isinstance(raw_regions, Mapping):
        for region, checked in checked_regions.items():
            merged = raw_regions.get(region)
            if merged is None:
                continue
            if not isinstance(merged, Mapping):
                result.update(status="rejected", reason="merged_refinement_region_invalid")
                return result
            for key in ("box", "raw_text", "text", "amount", "source_crop_sha256", "gameplay_sha256"):
                if merged.get(key) != checked.get(key):
                    result.update(status="rejected", reason="merged_refinement_region_disagrees")
                    return result

    result.update(
        status="validated",
        gameplay_sha256=gameplay_sha256,
        regions=checked_regions,
        validated_regions=sorted(checked_regions),
    )
    return result


__all__ = [
    "SCHEMA",
    "VERSION",
    "MIN_CONFIDENCE",
    "refinement_requests",
    "refine_training_gain_regions",
    "validate_training_gain_refinement",
    "attach_training_gain_refinement",
]
