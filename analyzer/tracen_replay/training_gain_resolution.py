"""Resolve source-bound training gain observations.

The native training-result parser intentionally keeps a strict canonical crop
quality gate.  This module contains a separate, bounded recovery path for a
source-visible signed gain which falls just below that gate.  Recovery is
allowed only when the same physical result phase supplies independent
corroboration: repeated tight gain crops on distinct frames, or agreement of
the tight crop with a broader crop on the same frame.  Result totals and
before/after balances never participate.

The cross-frame clipping resolver also retains a signed overlay crop as
diagnostic evidence when the tight crop has lost a trailing digit.  It requires
an independently labelled broad crop and repeated tight prefix frames before
returning a value.
"""

from __future__ import annotations

import math
import posixpath
import re
from collections import defaultdict
from typing import Any, Iterable, Mapping

from .crop_provenance import (
    crop_family,
    source_geometry_overlaps,
    source_refinement_candidate_is_bound,
    source_signed_candidates,
)
from .ocr_confidence import confidence_percent


# These are recovery policy bounds, not replacements for the canonical crop
# rule.  A direct crop must still be fairly readable, and the broader view is
# used only as corroboration when it is geometrically tied to the tight view.
CANDIDATE_RECOVERY_MIN_CONFIDENCE = 90.0
CANDIDATE_RECOVERY_WIDE_MIN_CONFIDENCE = 80.0
CANDIDATE_RECOVERY_MIN_FRAMES = 2
CANDIDATE_RECOVERY_MAX_SPAN_MS = 250
CANDIDATE_RECOVERY_MAX_GAP_MS = 150

_SIGNED_EXACT = re.compile(r"^\s*\+\s*(?P<digits>\d{1,3})\s*$")
_SIGNED_PREFIX = re.compile(r"^\s*\+\s*(?P<digits>\d{1,3})(?P<suffix>[^\d\s]*)\s*$")
_RECOVERY_SUFFIX = frozenset(":.,'\u2019)]}")
_SOURCE_FAMILIES = frozenset(("gain", "wide_gain", "expanded_gain", "localized_gain"))

# A clipped badge needs a separate source view because the canonical ``gain``
# crop can repeatedly retain only its first digit.  The thresholds below are
# deliberately independent from the normal canonical resolver: the direct
# crop remains readable, an overlapping signed result overlay is high quality,
# and the wider crop is allowed a bounded lower floor because it is used only
# as cross-frame corroboration.  No state or expected value is part of this
# recovery channel.
SOURCE_CLIPPING_MIN_TIGHT_CONFIDENCE = 90.0
SOURCE_CLIPPING_MIN_OVERLAY_CONFIDENCE = 90.0
SOURCE_CLIPPING_MIN_BROAD_CONFIDENCE = 75.0
SOURCE_CLIPPING_MIN_TIGHT_FRAMES = 2
SOURCE_CLIPPING_MIN_FULL_FRAMES = 2
SOURCE_CLIPPING_MAX_SPAN_MS = 250
SOURCE_CLIPPING_MAX_GAP_MS = 150

# An expanded inner crop is a source-bound reread of the canonical badge
# geometry.  It is a different proof channel from the native tight/broad
# clipping resolver: the reread has already been checked against the exact
# gameplay pixels, so a single committed result row can establish its amount
# even when the native crop is clipped in every available frame.  Keep the
# confidence floor at the normal expanded-crop floor plus a margin; this
# branch must never become a low-confidence amount selector.
SOURCE_REFINEMENT_INNER_MIN_CONFIDENCE = 90.0
SOURCE_REFINEMENT_SCHEMA = "tracen-replay/training-gain-source-refinement-v1"
SOURCE_REFINEMENT_VALIDATION = SOURCE_REFINEMENT_SCHEMA + "/pixel-binding-v1"
SOURCE_REFINEMENT_BASIS = "source_pixel_refined_training_gain_phase"


def candidate_recovery_policy() -> dict[str, Any]:
    """Return the immutable bounds used by candidate-only recovery."""

    return {
        "minimum_tight_confidence": CANDIDATE_RECOVERY_MIN_CONFIDENCE,
        "minimum_broad_confidence": CANDIDATE_RECOVERY_WIDE_MIN_CONFIDENCE,
        "minimum_repeated_frames": CANDIDATE_RECOVERY_MIN_FRAMES,
        "maximum_span_ms": CANDIDATE_RECOVERY_MAX_SPAN_MS,
        "maximum_gap_ms": CANDIDATE_RECOVERY_MAX_GAP_MS,
        "result_family_is_diagnostic_only": True,
        "uses_balance_arithmetic": False,
        "uses_expected_amount": False,
    }


def source_clipping_policy() -> dict[str, Any]:
    """Return the fixed bounds for cross-frame clipped-badge recovery.

    This policy is separate from :func:`candidate_recovery_policy` because
    existing candidate recovery accepts only the canonical ``gain`` and broad
    families.  The clipping path may inspect a signed ``result`` overlay, but
    it never promotes that family on its own: a second broad source crop and
    repeated tight prefix observations are required.
    """

    return {
        "minimum_tight_confidence": SOURCE_CLIPPING_MIN_TIGHT_CONFIDENCE,
        "minimum_overlay_confidence": SOURCE_CLIPPING_MIN_OVERLAY_CONFIDENCE,
        "minimum_broad_confidence": SOURCE_CLIPPING_MIN_BROAD_CONFIDENCE,
        "minimum_tight_frames": SOURCE_CLIPPING_MIN_TIGHT_FRAMES,
        "minimum_full_frames": SOURCE_CLIPPING_MIN_FULL_FRAMES,
        "maximum_span_ms": SOURCE_CLIPPING_MAX_SPAN_MS,
        "maximum_gap_ms": SOURCE_CLIPPING_MAX_GAP_MS,
        "requires_signed_prefix": True,
        "requires_overlay_and_broad_agreement": True,
        "source_refinement_inner_enabled": True,
        "source_refinement_inner_min_confidence": SOURCE_REFINEMENT_INNER_MIN_CONFIDENCE,
        "source_refinement_inner_requires_pixel_binding": True,
        "result_family_is_diagnostic_only": True,
        "uses_balance_arithmetic": False,
        "uses_expected_amount": False,
    }


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(
        isinstance(part, bool) or not isinstance(part, (int, float))
        for part in value
    ):
        return None
    try:
        left, top, right, bottom = (float(part) for part in value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(part) for part in (left, top, right, bottom)):
        return None
    if not (148.0 <= left < right <= 958.0 and 0.0 <= top < bottom <= 1080.0):
        return None
    return left, top, right, bottom


def _contains(outer: tuple[float, float, float, float], inner: tuple[float, float, float, float]) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _strictly_contains(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
) -> bool:
    """Return whether the source crop is strictly broader than its peer."""

    return _contains(outer, inner) and outer != inner


def _timestamp(row: Mapping[str, Any]) -> int | None:
    value = row.get("source_timestamp_ms")
    # Source timestamps are part of physical-frame identity.  Coercing a
    # string or float here can merge a malformed sidecar into a trusted row
    # (and can manufacture a phase boundary after truncation), so the parser
    # accepts only the persisted integer form.
    return value if type(value) is int and value >= 0 else None


def _evidence(row: Mapping[str, Any]) -> str | None:
    value = row.get("evidence")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _evidence_identity(value: Any) -> str | None:
    """Normalize one source path for physical-frame identity checks."""

    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("\\", "/")
    normalized = re.sub(r"/+", "/", normalized)
    normalized = posixpath.normpath(normalized)
    if normalized in {"", "."}:
        return None
    return normalized.casefold()


def _valid_phase_key(value: Any) -> bool:
    """Accept only stable scalar phase owners used by the report schema."""

    if type(value) is int:
        return True
    return isinstance(value, str) and bool(value.strip())


def _finite_confidence_bound(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        bound = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(bound) or not 0.0 <= bound <= 100.0:
        return None
    return bound


def _integer_bound(value: Any, *, minimum: int = 0) -> int | None:
    if type(value) is not int or value < minimum:
        return None
    return value


def _validated_source_clipping_overrides(
    *,
    minimum_tight_confidence: Any,
    minimum_overlay_confidence: Any,
    minimum_broad_confidence: Any,
    minimum_tight_frames: Any,
    minimum_full_frames: Any,
    maximum_span_ms: Any,
    maximum_gap_ms: Any,
) -> dict[str, Any] | None:
    confidence_values = {
        "minimum_tight_confidence": _finite_confidence_bound(minimum_tight_confidence),
        "minimum_overlay_confidence": _finite_confidence_bound(minimum_overlay_confidence),
        "minimum_broad_confidence": _finite_confidence_bound(minimum_broad_confidence),
    }
    if any(value is None for value in confidence_values.values()):
        return None
    integer_values = {
        "minimum_tight_frames": _integer_bound(minimum_tight_frames, minimum=1),
        "minimum_full_frames": _integer_bound(minimum_full_frames, minimum=1),
        "maximum_span_ms": _integer_bound(maximum_span_ms),
        "maximum_gap_ms": _integer_bound(maximum_gap_ms),
    }
    if any(value is None for value in integer_values.values()):
        return None
    return {**confidence_values, **integer_values}


def _validated_candidate_recovery_overrides(
    *,
    minimum_tight_confidence: Any,
    minimum_broad_confidence: Any,
    minimum_repeated_frames: Any,
    maximum_span_ms: Any,
    maximum_gap_ms: Any,
) -> dict[str, Any] | None:
    """Validate candidate-only policy values before source selection."""

    confidence_values = {
        "minimum_tight_confidence": _finite_confidence_bound(minimum_tight_confidence),
        "minimum_broad_confidence": _finite_confidence_bound(minimum_broad_confidence),
    }
    if any(value is None for value in confidence_values.values()):
        return None
    integer_values = {
        "minimum_repeated_frames": _integer_bound(minimum_repeated_frames, minimum=1),
        "maximum_span_ms": _integer_bound(maximum_span_ms),
        "maximum_gap_ms": _integer_bound(maximum_gap_ms),
    }
    if any(value is None for value in integer_values.values()):
        return None
    if integer_values["maximum_gap_ms"] > integer_values["maximum_span_ms"]:
        return None
    return {**confidence_values, **integer_values}


def _phase_signature(row: Mapping[str, Any]) -> tuple[Any, ...] | None:
    """Return the phase identity available on one parsed result row.

    The caller supplies the event/window identity.  This row-level signature
    adds the visible screen, selected training option when present, and the
    preview flag.  A changed option or preview flag cannot be collapsed into a
    repeated badge observation.  The explicit markers mirror the source
    inspection phase contract, including preview declarations stored outside
    ``stats``.
    """

    if row.get("screen") != "training_result":
        return None
    stats = row.get("stats")
    if not isinstance(stats, Mapping):
        stats = {}
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        facts = {}
    explicit = row.get("phase")
    if explicit is None:
        explicit = row.get("training_phase")
    if explicit is None:
        for key in ("phase", "training_phase", "source_phase"):
            if facts.get(key) is not None:
                explicit = facts[key]
                break
    if (
        stats.get("training_preview") is True
        or facts.get("preview") is True
        or facts.get("preview_overlay_proven") is True
        or facts.get("preview_modifier_proven") is True
        or facts.get("training_preview") is True
        or (
            isinstance(facts.get("training_outcome"), str)
            and facts.get("training_outcome").strip().casefold() in {"failure", "failed"}
        )
        or bool(facts.get("failure_banner"))
    ):
        return None
    if isinstance(explicit, str):
        marker = explicit.strip().casefold().replace("-", "_").replace(" ", "_")
        if marker in {"preview", "training_preview", "projected", "projection"}:
            return None
    return (
        row.get("screen"),
        row.get("training_option"),
        False,
    )


def _exact_amount(text: Any) -> int | None:
    match = _SIGNED_EXACT.fullmatch(str(text or ""))
    return int(match.group("digits")) if match else None


def _broad_amount(text: Any) -> int | None:
    """Parse a broad corroborating crop with harmless terminal OCR noise.

    This is deliberately narrower than the native parser's expanded-crop
    handling.  Only a trailing punctuation glyph is tolerated, and the broad
    view can never be accepted without an agreeing tight signed crop.
    """

    match = _SIGNED_PREFIX.fullmatch(str(text or ""))
    if not match:
        return None
    suffix = match.group("suffix")
    if len(suffix) > 1 or (suffix and any(char not in _RECOVERY_SUFFIX for char in suffix)):
        return None
    return int(match.group("digits"))


def _region_matches_field(region: Any, family: Any, field: str) -> bool:
    """Bind native and bounded-refinement regions to one field.

    Source refinement keeps ``inner`` and ``outer`` geometry as separate
    observations so a conflicting crop cannot be hidden by a last-write
    merge. Only those two declared expanded-gain roles may sit between the
    family and field; arbitrary dotted names remain outside the source proof.
    """

    if not isinstance(region, str) or not isinstance(family, str):
        return False
    if region == f"{family}.{field}":
        return True
    if family != "expanded_gain":
        return False
    return region in {
        f"expanded_gain.inner.{field}",
        f"expanded_gain.outer.{field}",
    }


def _strict_prefix(short: int, long: int) -> bool:
    """Return whether ``short`` is a strict visible prefix of ``long``."""

    short_text = str(short)
    long_text = str(long)
    return len(long_text) > len(short_text) and long_text.startswith(short_text)


def _candidate_observations(row: Mapping[str, Any], field: str) -> list[dict[str, Any]]:
    """Extract source crop facts without promoting them.

    Only typed crop provenance is considered.  Raw OCR lines, result totals,
    state values, and candidate lists without geometry are intentionally
    excluded from this recovery boundary.
    """

    timestamp = _timestamp(row)
    evidence = _evidence(row)
    phase = _phase_signature(row)
    if timestamp is None or evidence is None or phase is None:
        return []
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        return []
    provenance = facts.get("training_gain_crop_provenance")
    if not isinstance(provenance, Mapping):
        return []
    resolution = provenance.get(field)
    if not isinstance(resolution, Mapping):
        return []
    candidates = resolution.get("candidates")
    if not isinstance(candidates, list):
        return []
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        family = candidate.get("crop_family")
        if family not in _SOURCE_FAMILIES or family == "result":
            continue
        region = candidate.get("region")
        if (
            not isinstance(region, str)
            or crop_family(region) != family
            or not _region_matches_field(region, family, field)
            or not source_refinement_candidate_is_bound(candidate)
        ):
            continue
        if candidate.get("input_eligible") is not True:
            continue
        role = candidate.get("source_role")
        if family in {"gain", "localized_gain"} and role != "amount_crop_candidate":
            continue
        if family in {"wide_gain", "expanded_gain"} and role not in {
            "amount_crop_candidate", "unparsed_crop_candidate",
        }:
            continue
        box = _box(candidate.get("box"))
        if box is None:
            continue
        text = str(candidate.get("raw_text", "")).strip()
        amount: int | None
        normalization: str
        if family in {"gain", "localized_gain"}:
            amount = _exact_amount(text)
            normalization = "signed_amount"
        else:
            amount = _broad_amount(text)
            normalization = "signed_amount_prefix" if amount is not None else "unparsed"
        if amount is None or not 0 <= amount <= 999:
            continue
        confidence = confidence_percent(candidate.get("confidence"))
        if confidence is None:
            continue
        result.append({
            "amount": amount,
            "field": field,
            "region": region,
            "crop_family": family,
            "raw_text": text,
            "confidence": confidence,
            "box": list(box),
            "source_role": candidate.get("source_role"),
            "source_timestamp_ms": timestamp,
            "evidence": evidence,
            "phase_signature": list(phase),
            "normalization": normalization,
        })
        for key in (
            "source_pixel_verified", "pixel_rgb_sha256", "gameplay_sha256",
            "source_frame_sha256", "source_frame_evidence", "source_frame_id",
            "evidence_sha256", "raw_sha256", "localization_schema",
            "source_observation_basis", "component_box", "scan_box",
            "source_pixel_basis", "source_refinement_schema",
            "source_refinement_role", "source_refinement_geometry",
            "source_refinement_verified", "source_crop_sha256",
        ):
            if key in candidate:
                result[-1][key] = candidate[key]
        # Native crops are emitted by the base reader before an external
        # localized/refined sidecar is attached, so they often lack the
        # decoded-frame identity that the sidecar carries.  A candidate is a
        # region of the row's already source-bound gameplay frame; inherit
        # only the row-level frame binding when the candidate did not declare
        # one.  Do not overwrite a candidate declaration: a disagreement must
        # remain visible and fail the physical-frame match rather than being
        # normalized into agreement here.
        for key in ("source_frame_sha256", "source_frame_evidence", "source_frame_id"):
            if key not in result[-1] and key in row:
                result[-1][key] = row[key]
    return result


def _invalid_source_row(row: Mapping[str, Any]) -> bool:
    """Reject an explicitly malformed source identity at the resolver edge."""

    if row.get("screen") != "training_result":
        return False
    if "source_timestamp_ms" in row and _timestamp(row) is None:
        return True
    if "evidence" in row and _evidence(row) is None:
        return True
    return False


def _invalid_candidate_confidence(row: Mapping[str, Any], field: str) -> bool:
    """Detect malformed confidence on a field-scoped signed candidate.

    A low confidence candidate is ordinary OCR evidence and remains
    diagnostic. A present but non-finite/out-of-range confidence is malformed
    metadata and must not disappear before a phase proof is checked.
    """

    facts = row.get("facts")
    provenance = facts.get("training_gain_crop_provenance") if isinstance(facts, Mapping) else None
    resolution = provenance.get(field) if isinstance(provenance, Mapping) else None
    candidates = resolution.get("candidates") if isinstance(resolution, Mapping) else None
    if not isinstance(candidates, list):
        return False
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or "confidence" not in candidate:
            continue
        region = candidate.get("region")
        family = crop_family(region) if isinstance(region, str) else None
        if family not in _SOURCE_FAMILIES:
            continue
        if not _region_matches_field(region, family, field):
            continue
        if type(candidate.get("amount")) is not int:
            continue
        if confidence_percent(candidate.get("confidence")) is None:
            return True
    return False


def _row_shape(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the signed source fields visible in a result phase."""

    facts = row.get("facts")
    provenance = facts.get("training_gain_crop_provenance") if isinstance(facts, Mapping) else None
    if not isinstance(provenance, Mapping):
        return ()
    fields: list[str] = []
    for name, resolution in provenance.items():
        if not isinstance(name, str) or not isinstance(resolution, Mapping):
            continue
        if any(
            item.get("crop_family") == "gain"
            and item.get("amount") is not None
            and item.get("raw_text", "").strip().startswith("+")
            for item in resolution.get("candidates", [])
            if isinstance(item, Mapping)
        ):
            fields.append(name)
    return tuple(sorted(set(fields)))


def _observation_key(observation: Mapping[str, Any]) -> tuple[str, str, str, int | None]:
    # Keep conflicting OCR values from the same physical crop visible.  A
    # repeated identical value is still one observation, but silently
    # dropping a second amount would let the first parser result win.
    return (
        _physical_frame_identity(observation),
        str(observation.get("region")),
        str(observation.get("crop_family")),
        observation.get("amount"),
    )


def _observation_instance_key(
    observation: Mapping[str, Any],
) -> tuple[int, str, str, str, int | None, str]:
    """Keep repeated aliases visible until physical-frame counts are checked."""

    return (
        int(observation["source_timestamp_ms"]),
        _evidence_identity(observation.get("evidence")) or "",
        str(observation.get("region")),
        str(observation.get("crop_family")),
        observation.get("amount"),
        _source_frame_sha256(observation) or "",
    )


def _proof(observation: Mapping[str, Any]) -> dict[str, Any]:
    proof = {
        key: observation.get(key)
        for key in (
            "amount", "field", "region", "crop_family", "raw_text", "confidence",
            "box", "source_role", "source_timestamp_ms", "evidence", "normalization",
            "phase_signature",
        )
    }
    for key in (
        "source_pixel_verified", "pixel_rgb_sha256", "gameplay_sha256",
        "source_frame_sha256", "source_frame_evidence", "source_frame_id",
        "evidence_sha256", "raw_sha256", "localization_schema",
        "source_observation_basis", "component_box", "scan_box",
        "source_pixel_basis", "source_refinement_schema",
        "source_refinement_role", "source_refinement_geometry",
        "source_refinement_verified", "source_crop_sha256",
    ):
        if key in observation:
            proof[key] = observation[key]
    return proof


def _clipping_observations(
    row: Mapping[str, Any], field: str,
) -> list[dict[str, Any]]:
    """Extract signed crop evidence, including diagnostic overlay crops."""

    timestamp = _timestamp(row)
    evidence = _evidence(row)
    phase = _phase_signature(row)
    if timestamp is None or evidence is None or phase is None:
        return []
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        return []
    provenance = facts.get("training_gain_crop_provenance")
    if not isinstance(provenance, Mapping):
        return []
    resolution = provenance.get(field)
    if not isinstance(resolution, Mapping):
        return []
    candidates = resolution.get("candidates")
    if not isinstance(candidates, list):
        return []

    result: list[dict[str, Any]] = []
    for candidate in source_signed_candidates(candidates, include_result=True):
        region = candidate.get("region")
        family = candidate.get("crop_family")
        if (
            not isinstance(region, str)
            or not isinstance(family, str)
            or crop_family(region) != family
            or not _region_matches_field(region, family, field)
            or not source_refinement_candidate_is_bound(candidate)
        ):
            continue
        if family == "result" and (
            candidate.get("source_role") != "result_crop_diagnostic_excluded"
            or candidate.get("input_eligible") is not False
        ):
            continue
        confidence = confidence_percent(candidate.get("confidence"))
        if confidence is None:
            continue
        amount = candidate.get("amount")
        if type(amount) is not int or not 0 <= amount <= 999:
            continue
        item = dict(candidate)
        item.update(
            field=field,
            confidence=confidence,
            source_timestamp_ms=timestamp,
            evidence=evidence,
            phase_signature=list(phase),
        )
        # The crop candidate and the row describe the same decoded gameplay
        # frame.  Native candidates may predate the source-frame binding that
        # is attached to the row by the inspection loader, while localized or
        # refined candidates already carry it.  Inherit only an absent row
        # binding so aliases compare as one physical frame; preserve any
        # candidate disagreement for the resolver to reject.
        for key in ("source_frame_sha256", "source_frame_evidence", "source_frame_id"):
            if key not in item and key in row:
                item[key] = row[key]
        result.append(item)
    return result


_SOURCE_FRAME_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _source_frame_sha256(observation: Mapping[str, Any]) -> str | None:
    """Return the validated decoded-frame identity carried by one crop."""

    value = observation.get("source_frame_sha256")
    if not isinstance(value, str) or _SOURCE_FRAME_SHA256.fullmatch(value) is None:
        return None
    return value.casefold()


def _physical_frame_identity(observation: Mapping[str, Any]) -> str:
    """Identify one source frame without treating timestamp aliases as votes."""

    frame_sha256 = _source_frame_sha256(observation)
    if frame_sha256 is not None:
        return f"frame-sha256:{frame_sha256}"
    evidence = _evidence_identity(observation.get("evidence")) or ""
    if evidence:
        # Existing native rows use the normalized evidence path as their
        # physical identity.  Keep that fallback so a path alias at another
        # timestamp cannot become a second corroborating frame when the
        # decoded-frame hash is unavailable.
        return f"evidence:{evidence}"
    timestamp = int(observation["source_timestamp_ms"])
    return f"timestamp:{timestamp}"


def _clipping_identity(observation: Mapping[str, Any]) -> str:
    """Return one physical source-frame key for clipping evidence."""

    return _physical_frame_identity(observation)


def _clipping_timestamp(observation: Mapping[str, Any]) -> int:
    return int(observation["source_timestamp_ms"])


def _clipping_proof(observation: Mapping[str, Any]) -> dict[str, Any]:
    proof = _proof(observation)
    proof["source_observation_role"] = observation.get("source_observation_role")
    return proof


def _source_pixel_badge(item: Mapping[str, Any], minimum_confidence: float) -> bool:
    """Validate the typed proof required for a localized badge candidate."""

    if item.get("crop_family") != "localized_gain":
        return False
    if item.get("source_observation_role") != "amount_crop_candidate":
        return False
    if item.get("input_eligible") is not True or item.get("canonical_eligible") is not True:
        return False
    if item.get("source_pixel_verified") is not True:
        return False
    if item.get("source_observation_basis") != "source_pixel_localized_training_badge":
        return False
    pixel_sha = item.get("pixel_rgb_sha256")
    if not isinstance(pixel_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", pixel_sha):
        return False
    confidence = confidence_percent(item.get("confidence"))
    if confidence is None:
        return False
    bound = confidence_percent(minimum_confidence)
    return bound is not None and confidence >= bound


def _confidence_at_least(item: Mapping[str, Any], minimum: Any) -> bool:
    """Check persisted OCR confidence without coercing malformed values."""

    confidence = confidence_percent(item.get("confidence"))
    bound = confidence_percent(minimum)
    return confidence is not None and bound is not None and confidence >= bound


def _bounded_source_frames(
    items: Iterable[Mapping[str, Any]],
    *,
    minimum_frames: int,
    maximum_span_ms: int,
    maximum_gap_ms: int,
) -> bool:
    """Require distinct source frames in one bounded temporal run."""

    identities = {_clipping_identity(item) for item in items}
    if len(identities) < minimum_frames:
        return False
    times = sorted({_clipping_timestamp(item) for item in items})
    return bool(
        len(times) >= minimum_frames
        and times[-1] - times[0] <= maximum_span_ms
        and all(
            later - earlier <= maximum_gap_ms
            for earlier, later in zip(times, times[1:])
        )
    )


def _resolve_localized_badge(
    field: str,
    phase_key: Any,
    deduped: list[dict[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve localized source pixels with bounded phase corroboration.

    A localized component is an independent view of the badge, but it can
    itself contain a clipped prefix.  The resolver therefore considers the
    complete tight ``gain`` amount as a candidate only when it has repeated
    source-frame support, or when a same-frame localized/tight pair is exact.
    Every localized reading must bind to the selected amount through source
    geometry.  Competing complete values, result overlays, and unrelated
    crops remain in the diagnostic observations.
    """

    localized = [
        item for item in deduped
        if _source_pixel_badge(item, policy["minimum_tight_confidence"])
    ]
    if not localized:
        return None
    direct = [
        item for item in deduped
        if item.get("crop_family") == "gain"
        and item.get("source_observation_role") == "amount_crop_candidate"
        and _confidence_at_least(item, policy["minimum_tight_confidence"])
    ]
    if not direct:
        return _source_clipping_unresolved(
            field, phase_key, "localized_badge_without_tight_agreement", deduped, policy,
        )

    direct_by_amount: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in direct:
        if type(item.get("amount")) is int:
            direct_by_amount[int(item["amount"])].append(item)

    def related(localized_item: Mapping[str, Any], direct_item: Mapping[str, Any]) -> bool:
        return (
            _clipping_identity(localized_item) == _clipping_identity(direct_item)
            and source_geometry_overlaps(localized_item, direct_item, minimum_fraction=0.40)
        )

    possible: list[dict[str, Any]] = []
    for amount, full_items in direct_by_amount.items():
        repeated = _bounded_source_frames(
            full_items,
            minimum_frames=int(policy["minimum_full_frames"]),
            maximum_span_ms=int(policy["maximum_span_ms"]),
            maximum_gap_ms=int(policy["maximum_gap_ms"]),
        )
        pairs: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        for local in localized:
            local_amount = local.get("amount")
            if type(local_amount) is not int:
                continue
            for tight in full_items:
                if not related(local, tight):
                    continue
                if local_amount == amount:
                    pairs.append((local, tight, "exact_same_frame"))
                elif _strict_prefix(int(local_amount), amount) and _strictly_contains(
                    tuple(tight["box"]), tuple(local["box"]),
                ):
                    pairs.append((local, tight, "localized_prefix"))

        # A localized frame may have only the broad crop (the native tight
        # crop can be clipped or absent).  Bind it to a complete broad view
        # on the same physical frame, while repeated tight frames elsewhere
        # provide the complete amount's independent support.
        broad_full = [
            item for item in deduped
            if item.get("crop_family") in {"wide_gain", "expanded_gain"}
            and int(item.get("amount", -1)) == amount
            and _confidence_at_least(item, policy["minimum_broad_confidence"])
        ]
        for local in localized:
            local_amount = local.get("amount")
            if type(local_amount) is not int:
                continue
            if local_amount != amount and not _strict_prefix(int(local_amount), amount):
                continue
            if any(pair[0] is local for pair in pairs):
                continue
            for broad_item in broad_full:
                if (
                    _clipping_identity(local) == _clipping_identity(broad_item)
                    and source_geometry_overlaps(local, broad_item, minimum_fraction=0.40)
                    and (
                        local_amount == amount
                        or _strictly_contains(tuple(broad_item["box"]), tuple(local["box"]))
                    )
                ):
                    pairs.append((local, broad_item, "localized_broad_bound"))
                    break

        if not pairs:
            continue
        # Strict prefix recovery requires repeated complete tight support. An
        # exact local/tight pair can stand alone because the two source crops
        # are independent views of one frame.
        if any(kind == "localized_prefix" for _local, _tight, kind in pairs) and not repeated:
            continue
        if not repeated and not any(kind == "exact_same_frame" for _local, _tight, kind in pairs):
            continue
        possible.append(dict(amount=amount, full_items=full_items, pairs=pairs, repeated=repeated))

    if len(possible) != 1:
        localized_values = {
            int(item["amount"])
            for item in localized
            if type(item.get("amount")) is int
        }
        direct_values = set(direct_by_amount)
        reason = "conflicting_localized_badge_reads" if len(localized_values) > 1 else (
            "localized_badge_tight_conflict"
            if any(value not in localized_values for value in direct_values)
            else "localized_badge_without_same_frame_tight_proof"
        )
        return _source_clipping_unresolved(field, phase_key, reason, deduped, policy)

    selected = possible[0]
    amount = selected["amount"]
    full_items = selected["full_items"]
    selected_pairs = selected["pairs"]
    selected_direct_ids = {_clipping_identity(item) for item in full_items}
    # Every high-confidence direct amount must either be the selected full
    # amount or a strict prefix of it.  This admits the clipped tight view
    # seen during the same animation while rejecting an unrelated complete
    # amount without ranking by magnitude or confidence.
    for item in direct:
        item_amount = int(item["amount"])
        if item_amount != amount and not _strict_prefix(item_amount, amount):
            return _source_clipping_unresolved(
                field, phase_key, "localized_badge_tight_conflict", deduped, policy,
            )

    # Localized candidates are source proof, so an unbound candidate is an
    # unresolved source disagreement rather than a reason to pick a convenient
    # direct crop.  Prefix candidates must remain in the selected phase.
    for local in localized:
        local_amount = int(local["amount"])
        if local_amount != amount and not _strict_prefix(local_amount, amount):
            return _source_clipping_unresolved(
                field, phase_key, "conflicting_localized_badge_reads", deduped, policy,
            )
        if not any(pair[0] is local for pair in selected_pairs):
            return _source_clipping_unresolved(
                field, phase_key, "localized_badge_without_same_frame_tight_proof", deduped, policy,
            )

    # A complete broad/expanded disagreement is admissible only when it is
    # physically tied to one selected localized/direct frame.  Noncanonical
    # low-quality alternatives stay audit evidence even when disjoint. Result
    # crops are diagnostics by contract and never affect the decision.
    differing_broad: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in deduped:
        family = item.get("crop_family")
        if family == "result" or family in {"gain", "localized_gain"}:
            continue
        if family not in {"wide_gain", "expanded_gain"}:
            continue
        if not _confidence_at_least(item, policy["minimum_broad_confidence"]):
            continue
        item_amount = int(item.get("amount", -1))
        if item_amount == amount:
            continue
        # A wider crop may retain the same clipped leading digits as the
        # native tight crop. It is a diagnostic prefix, not a competing
        # complete amount, and can remain disjoint from the localized crop.
        if _strict_prefix(item_amount, amount):
            continue
        if item.get("canonical_eligible") is False:
            continue
        differing_broad[item_amount].append(item)

    for item_amount, items in differing_broad.items():
        # At least one frame must bind the alternate broad reading to the
        # selected source proof. Once that anchor exists, repeated copies of
        # the same broad geometry remain diagnostic views of that badge even
        # when their timestamps differ slightly.
        if not any(
            _clipping_identity(item) == _clipping_identity(peer)
            and source_geometry_overlaps(item, peer, minimum_fraction=0.40)
            for local, tight, _kind in selected_pairs
            for peer in (local, tight)
            for item in items
        ):
            return _source_clipping_unresolved(
                field, phase_key, "localized_badge_geometry_conflict", deduped, policy,
            )
        if not all(
            any(
                source_geometry_overlaps(item, peer, minimum_fraction=0.40)
                for local, tight, _kind in selected_pairs
                for peer in (local, tight)
            )
            for item in items
        ):
            return _source_clipping_unresolved(
                field, phase_key, "localized_badge_geometry_conflict", deduped, policy,
            )

    accepted: list[dict[str, Any]] = []
    for item in full_items:
        accepted.append(item)
    for local, peer, _kind in selected_pairs:
        accepted.extend((local, peer))
    unique_accepted: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in accepted:
        key = _observation_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique_accepted.append(item)
    return {
        "status": "accepted",
        "accepted_amount": amount,
        "field": field,
        "phase_key": phase_key,
        "basis": "source_pixel_localized_training_badge_with_tight_agreement",
        "resolution_mode": (
            "localized_prefix_with_repeated_full_support"
            if any(kind == "localized_prefix" for _local, _peer, kind in selected_pairs)
            else "exact_same_frame"
        ),
        "reason": None,
        "observed_amounts": sorted({
            int(item["amount"])
            for item in deduped
            if type(item.get("amount")) is int
        }),
        "observations": [_clipping_proof(item) for item in deduped],
        "accepted_observations": [_clipping_proof(item) for item in unique_accepted],
        "short_observations": [
            _clipping_proof(item)
            for item in direct
            if int(item["amount"]) != amount
        ],
        "full_observations": [_clipping_proof(item) for item in full_items],
        "policy": {
            **dict(policy),
            "source_pixel_localization_enabled": True,
            "requires_canonical_tight_agreement": True,
            "requires_same_frame_geometry": True,
            "requires_repeated_full_support_for_prefix": True,
        },
    }


def _source_refined_complete_candidate(
    item: Mapping[str, Any],
    field: str,
    role: str,
    minimum_confidence: Any,
    *,
    source_roles: frozenset[str],
    require_canonical: bool,
) -> bool:
    """Validate one complete source-pixel-bound expanded amount.

    ``source_role`` is derived from the parsed text and input boundary.  A
    source sidecar may therefore carry a complete signed outer reading whose
    role was recorded as ``unparsed_crop_candidate`` because the ordinary
    canonical gate rejected that role.  Such a reading remains noncanonical,
    but it is still a real source disagreement and must not be silently
    ranked below an accepted inner reading.
    """

    if not isinstance(item, Mapping) or not isinstance(field, str) or not field:
        return False
    if role not in {"inner", "outer"}:
        return False
    if (
        item.get("crop_family") != "expanded_gain"
        or item.get("region") != f"expanded_gain.{role}.{field}"
        or item.get("source_role") not in source_roles
        or item.get("source_observation_role") != item.get("source_role")
        or item.get("input_eligible") is not True
        or (require_canonical and item.get("canonical_eligible") is not True)
    ):
        return False
    if (
        item.get("source_refinement_schema") != SOURCE_REFINEMENT_SCHEMA
        or item.get("source_refinement_role") != role
        or item.get("source_refinement_geometry")
        != (
            "canonical_gain_relative_inner"
            if role == "inner"
            else "wide_gain_relative_outer"
        )
        or item.get("source_refinement_verified") is not True
        or item.get("source_refinement_pixel_bound") is not True
        or item.get("source_pixel_verified") is not True
        or item.get("source_pixel_basis") != "relative_training_gain_source_crop"
        or item.get("source_observation_basis") != "source_pixel_refined_training_gain"
        or item.get("source_refinement_validation") != SOURCE_REFINEMENT_VALIDATION
    ):
        return False
    for key in ("source_crop_sha256", "gameplay_sha256"):
        value = item.get(key)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
            return False
    if _source_frame_sha256(item) is None:
        return False
    if not source_refinement_candidate_is_bound(item):
        return False
    amount = item.get("amount")
    if type(amount) is not int or not 0 <= amount <= 999:
        return False
    if item.get("normalization") != "signed_amount":
        return False
    match = _SIGNED_EXACT.fullmatch(str(item.get("raw_text", "")))
    if match is None or int(match.group("digits")) != amount:
        return False
    return _confidence_at_least(item, minimum_confidence)


def _source_refined_inner_candidate(
    item: Mapping[str, Any],
    field: str,
    minimum_confidence: Any,
) -> bool:
    """Validate one source-pixel-bound expanded inner amount."""

    return _source_refined_complete_candidate(
        item,
        field,
        "inner",
        minimum_confidence,
        source_roles=frozenset({"amount_crop_candidate"}),
        require_canonical=True,
    )


def _source_refined_outer_candidate(
    item: Mapping[str, Any],
    field: str,
    minimum_confidence: Any,
) -> bool:
    """Recognize a complete outer source crop, including noncanonical roles."""

    return _source_refined_complete_candidate(
        item,
        field,
        "outer",
        minimum_confidence,
        source_roles=frozenset({"amount_crop_candidate", "unparsed_crop_candidate"}),
        require_canonical=False,
    )


def _resolve_source_refined_inner(
    field: str,
    phase_key: Any,
    extracted: Iterable[Mapping[str, Any]],
    deduped: list[dict[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve a validated expanded-inner source reread for one phase.

    This is intentionally separate from the native clipping resolver.  A
    source-bound inner reread proves the complete signed badge directly, so
    it does not need a second crop family or a result-counter overlay.  The
    phase is still owned by the ordinary ``training_result`` row and selected
    option supplied to this resolver.  Competing canonical source crops are
    retained as a conflict instead of being ranked by confidence or amount.
    """

    minimum = policy.get(
        "source_refinement_inner_min_confidence",
        SOURCE_REFINEMENT_INNER_MIN_CONFIDENCE,
    )
    source_items = [
        dict(item)
        for item in extracted
        if _source_refined_inner_candidate(item, field, minimum)
    ]
    if not source_items:
        return None

    # One source path at two timestamps, or two copies of one physical source
    # row, cannot turn a single reread into temporal corroboration.  Keep the
    # source identity check here before the general dedupe step so an exact
    # duplicate is not silently collapsed.
    identities = [_clipping_identity(item) for item in source_items]
    if len(set(identities)) != len(identities):
        return _source_clipping_unresolved(
            field,
            phase_key,
            "duplicate_source_refinement_identity",
            deduped,
            policy,
        )

    values = {int(item["amount"]) for item in source_items}
    if len(values) != 1:
        return _source_clipping_unresolved(
            field,
            phase_key,
            "conflicting_source_refinement_inner_amounts",
            deduped,
            policy,
        )
    amount = next(iter(values))

    # A complete, source-bound outer crop remains a real disagreement even
    # when its recorded role is ``unparsed_crop_candidate`` and therefore its
    # canonical flag is false.  Do not let the accepted inner crop win by
    # ranking around that contradiction.  Low-confidence or clipped outer
    # diagnostics are intentionally left out of this check.
    outer_conflicts = [
        item
        for item in deduped
        if _source_refined_outer_candidate(item, field, minimum)
        and int(item["amount"]) != amount
    ]
    if outer_conflicts:
        return _source_clipping_unresolved(
            field,
            phase_key,
            "source_refinement_outer_crop_conflict",
            deduped,
            policy,
        )

    # A distinct canonical crop in the same committed phase is a real source
    # disagreement.  Result-family readings remain diagnostics by contract;
    # they cannot veto or establish the source-refined amount.
    for item in deduped:
        if item.get("crop_family") == "result":
            continue
        if item.get("canonical_eligible") is not True:
            continue
        item_amount = item.get("amount")
        if type(item_amount) is int and item_amount != amount:
            return _source_clipping_unresolved(
                field,
                phase_key,
                "source_refinement_canonical_crop_conflict",
                deduped,
                policy,
            )

    # ``deduped`` carries the exact source rows which the evaluator will
    # rebuild.  Select accepted proof by physical identity rather than amount
    # or confidence, retaining all other crops as diagnostics.
    accepted: list[dict[str, Any]] = []
    accepted_identities = set(identities)
    for item in deduped:
        if (
            item.get("crop_family") == "expanded_gain"
            and item.get("region") == f"expanded_gain.inner.{field}"
            and type(item.get("amount")) is int
            and item.get("amount") == amount
            and _clipping_identity(item) in accepted_identities
            and _source_refined_inner_candidate(item, field, minimum)
        ):
            accepted.append(item)
    if len(accepted) != len(source_items):
        return _source_clipping_unresolved(
            field,
            phase_key,
            "source_refinement_identity_not_represented",
            deduped,
            policy,
        )

    return {
        "status": "accepted",
        "accepted_amount": amount,
        "field": field,
        "phase_key": phase_key,
        "basis": SOURCE_REFINEMENT_BASIS,
        "resolution_mode": (
            "source_refinement_inner_single_frame"
            if len(accepted) == 1
            else "source_refinement_inner_multiple_frames"
        ),
        "reason": None,
        "observed_amounts": sorted({
            int(item["amount"])
            for item in deduped
            if type(item.get("amount")) is int
        }),
        "observations": [_clipping_proof(item) for item in deduped],
        "accepted_observations": [_clipping_proof(item) for item in accepted],
        "short_observations": [],
        "full_observations": [_clipping_proof(item) for item in accepted],
        "policy": {
            **dict(policy),
            "source_refinement_inner_resolution": True,
            "source_refinement_phase_proof": (
                "committed_training_result_row_with_source_pixel_bound_inner_crop"
            ),
            "source_refinement_uses_independent_frame_count": False,
        },
    }


def _resolve_tight_consensus(
    field: str,
    phase_key: Any,
    deduped: list[dict[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve repeated tight frames before one later broad OCR alternative.

    This handles a complete tight badge that remains stable while a final wide
    crop carries one competing reading.  It is deliberately disabled when a
    qualifying result overlay exists, because a result crop cannot establish
    which amount was awarded.  The alternative must be a single later,
    same-length broad view with nested geometry; repeated or interleaved
    alternatives remain unresolved.
    """

    direct = [
        item for item in deduped
        if item.get("crop_family") == "gain"
        and item.get("source_observation_role") == "amount_crop_candidate"
        and _confidence_at_least(item, policy["minimum_tight_confidence"])
    ]
    if not direct:
        return None
    overlays = [
        item for item in deduped
        if item.get("crop_family") == "result"
        and item.get("source_observation_role") == "signed_overlay_diagnostic"
        and _confidence_at_least(item, policy["minimum_overlay_confidence"])
    ]
    if overlays:
        return None
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in direct:
        if type(item.get("amount")) is int:
            groups[int(item["amount"])].append(item)
    possible: list[tuple[int, list[dict[str, Any]], list[dict[str, Any]]]] = []
    for amount, items in groups.items():
        if not _bounded_source_frames(
            items,
            minimum_frames=int(policy["minimum_full_frames"]),
            maximum_span_ms=int(policy["maximum_span_ms"]),
            maximum_gap_ms=int(policy["maximum_gap_ms"]),
        ):
            continue
        alternatives = [
            item for item in deduped
            if item.get("crop_family") in {"wide_gain", "expanded_gain"}
            and _confidence_at_least(item, policy["minimum_broad_confidence"])
            and int(item.get("amount", -1)) != amount
        ]
        if not alternatives:
            continue
        alt_values = {int(item["amount"]) for item in alternatives}
        if len(alt_values) != 1 or len(alternatives) != 1:
            continue
        alt = alternatives[0]
        direct_times = [_clipping_timestamp(item) for item in items]
        alt_identity = _clipping_identity(alt)
        if _clipping_timestamp(alt) <= max(direct_times):
            continue
        if len(str(abs(int(alt["amount"])))) != len(str(abs(amount))):
            continue
        if not any(
            source_geometry_overlaps(alt, item, minimum_fraction=0.60)
            and _contains(tuple(alt["box"]), tuple(item["box"]))
            for item in items
        ):
            continue
        possible.append((amount, items, alternatives))
    if len(possible) != 1:
        return None
    amount, items, alternatives = possible[0]
    return {
        "status": "accepted",
        "accepted_amount": amount,
        "field": field,
        "phase_key": phase_key,
        "basis": "repeated_tight_gain_before_single_later_broad_alternative",
        "reason": None,
        "observed_amounts": sorted({
            int(item["amount"])
            for item in deduped
            if type(item.get("amount")) is int
        }),
        "observations": [_clipping_proof(item) for item in deduped],
        "accepted_observations": [_clipping_proof(item) for item in items],
        "short_observations": [],
        "full_observations": [_clipping_proof(item) for item in items],
        "policy": {
            **dict(policy),
            "tight_consensus_before_later_broad": True,
            "later_broad_diagnostic_observations": [
                _clipping_proof(item) for item in alternatives
            ],
        },
    }


def _source_clipping_unresolved(
    field: str,
    phase_key: Any,
    reason: str,
    observations: Iterable[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    observations = list(observations)
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in observations:
        key = (
            item.get("source_timestamp_ms"),
            _evidence_identity(item.get("evidence")),
            item.get("region"),
            item.get("amount"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(_clipping_proof(item))
    return {
        "status": "unresolved",
        "accepted_amount": None,
        "field": field,
        "phase_key": phase_key,
        "basis": "source_clipping_evidence_unresolved",
        "reason": reason,
        "observed_amounts": sorted({
            item.get("amount")
            for item in observations
            if type(item.get("amount")) is int
        }),
        "observations": unique,
        "accepted_observations": [],
        "short_observations": [],
        "full_observations": [],
        "policy": dict(policy),
    }


def resolve_source_clipped_gain(
    rows: Iterable[Mapping[str, Any]],
    field: str,
    *,
    phase_key: Any = None,
    minimum_tight_confidence: float = SOURCE_CLIPPING_MIN_TIGHT_CONFIDENCE,
    minimum_overlay_confidence: float = SOURCE_CLIPPING_MIN_OVERLAY_CONFIDENCE,
    minimum_broad_confidence: float = SOURCE_CLIPPING_MIN_BROAD_CONFIDENCE,
    minimum_tight_frames: int = SOURCE_CLIPPING_MIN_TIGHT_FRAMES,
    minimum_full_frames: int = SOURCE_CLIPPING_MIN_FULL_FRAMES,
    maximum_span_ms: int = SOURCE_CLIPPING_MAX_SPAN_MS,
    maximum_gap_ms: int = SOURCE_CLIPPING_MAX_GAP_MS,
) -> dict[str, Any]:
    """Resolve a clipped gain from independent source crop views.

    This recovery path addresses an animation where the tight ``gain`` crop
    repeatedly reads a first-digit prefix (for example ``+6``) while the
    visible badge is ``+65``.  It requires all of the following source facts:

    * two distinct physical frames with the same readable signed tight value;
    * a strict decimal-prefix relation between that value and one complete
      signed value;
    * the complete value on at least two distinct frames, with one high-quality
      signed diagnostic overlay and one independently labelled broad crop; and
    * same-frame box overlap tying each complete view to the tight crop.

    A result-family observation is retained as diagnostic evidence only.  The
    function never reads balances, result totals, expected labels, or numeric
    state.  Rows must already be scoped to one committed training phase by the
    caller; the explicit ``phase_key`` prevents an unowned window from being
    promoted accidentally.
    """

    policy = source_clipping_policy()
    if not isinstance(field, str) or not field:
        return _source_clipping_unresolved(
            field, phase_key, "invalid_training_field", [], policy,
        )
    if phase_key is None:
        return _source_clipping_unresolved(
            field, phase_key, "missing_source_phase_owner", [], policy,
        )
    if not _valid_phase_key(phase_key):
        return _source_clipping_unresolved(
            field, phase_key, "invalid_source_phase_owner", [], policy,
        )
    overrides = _validated_source_clipping_overrides(
        minimum_tight_confidence=minimum_tight_confidence,
        minimum_overlay_confidence=minimum_overlay_confidence,
        minimum_broad_confidence=minimum_broad_confidence,
        minimum_tight_frames=minimum_tight_frames,
        minimum_full_frames=minimum_full_frames,
        maximum_span_ms=maximum_span_ms,
        maximum_gap_ms=maximum_gap_ms,
    )
    if overrides is None:
        return _source_clipping_unresolved(
            field, phase_key, "invalid_source_clipping_policy", [], policy,
        )
    policy.update(overrides)

    rows = list(rows)
    if any(not isinstance(row, Mapping) for row in rows):
        return _source_clipping_unresolved(
            field, phase_key, "invalid_source_identity", [], policy,
        )
    for row in rows:
        if _invalid_source_row(row):
            return _source_clipping_unresolved(
                field, phase_key, "invalid_source_identity", [], policy,
            )
        if _invalid_candidate_confidence(row, field):
            return _source_clipping_unresolved(
                field, phase_key, "invalid_source_confidence", [], policy,
            )
    extracted: list[dict[str, Any]] = []
    valid_rows = 0
    options: set[str] = set()
    evidence_times: dict[str, set[int]] = defaultdict(set)
    frame_hash_times: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        if _phase_signature(row) is None:
            continue
        valid_rows += 1
        option = row.get("training_option")
        if isinstance(option, str) and option.strip():
            options.add(option.strip())
        timestamp = _timestamp(row)
        evidence = _evidence(row)
        if timestamp is not None and evidence is not None:
            identity = _evidence_identity(evidence)
            if identity is not None:
                evidence_times[identity].add(timestamp)
        extracted.extend(_clipping_observations(row, field))

    for item in extracted:
        frame_sha256 = _source_frame_sha256(item)
        if frame_sha256 is not None:
            frame_hash_times[frame_sha256].add(_clipping_timestamp(item))

    if len(options) > 1:
        return _source_clipping_unresolved(
            field, phase_key, "conflicting_training_options", extracted, policy,
        )
    if any(len(times) > 1 for times in evidence_times.values()):
        return _source_clipping_unresolved(
            field, phase_key, "duplicate_source_frame_identity", extracted, policy,
        )
    if any(len(times) > 1 for times in frame_hash_times.values()):
        return _source_clipping_unresolved(
            field, phase_key, "duplicate_source_frame_identity", extracted, policy,
        )
    if not extracted:
        reason = "no_source_signed_gain_candidate"
        if valid_rows == 0:
            reason = "no_committed_result_phase_rows"
        return _source_clipping_unresolved(field, phase_key, reason, extracted, policy)

    # A candidate list can contain repeated copies of one crop.  Keep every
    # family/value disagreement in the provenance, but only count one view per
    # physical frame for the proof below.
    deduped: list[dict[str, Any]] = []
    seen_candidates: set[tuple[Any, ...]] = set()
    for item in extracted:
        key = _observation_instance_key(item)
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        deduped.append(item)

    # The source-pixel localizer is a supplemental view.  Give it the first
    # chance to explain a broad contaminated reading, but only after the
    # same-frame tight crop and committed phase have been established.  When
    # its proof is present but contradictory, return unresolved immediately;
    # ordinary clipping/prefix logic must not silently choose around it.
    localized_resolution = _resolve_localized_badge(
        field, phase_key, deduped, policy,
    )
    if localized_resolution is not None:
        return localized_resolution

    # A validated expanded-inner reread is a separate source proof channel.
    # It may establish a complete amount when the native tight crop is
    # clipped throughout the result interval; the native clipping rules below
    # continue to require their own repeated tight/broad/result evidence.
    refined_inner_resolution = _resolve_source_refined_inner(
        field, phase_key, extracted, deduped, policy,
    )
    if refined_inner_resolution is not None:
        return refined_inner_resolution

    # A complete tight badge can remain stable while one later broad crop is
    # contaminated by an animation layer. This path is source-only and is
    # deliberately skipped when a result overlay is present.
    tight_consensus = _resolve_tight_consensus(
        field, phase_key, deduped, policy,
    )
    if tight_consensus is not None:
        return tight_consensus

    direct = [
        item for item in deduped
        if item.get("crop_family") == "gain"
        and item.get("source_observation_role") == "amount_crop_candidate"
        and _confidence_at_least(item, policy["minimum_tight_confidence"])
    ]
    broad = [
        item for item in deduped
        if item.get("crop_family") in {"wide_gain", "expanded_gain"}
        and _confidence_at_least(item, policy["minimum_broad_confidence"])
    ]
    overlays = [
        item for item in deduped
        if item.get("crop_family") == "result"
        and item.get("source_observation_role") == "signed_overlay_diagnostic"
        and _confidence_at_least(item, policy["minimum_overlay_confidence"])
    ]
    full_views = [*broad, *overlays]

    def bounded_frames(
        items: Iterable[Mapping[str, Any]],
        *,
        minimum_frames: int,
    ) -> bool:
        identities = {_clipping_identity(item) for item in items}
        times = sorted({_clipping_timestamp(item) for item in items})
        return bool(
            len(identities) >= minimum_frames
            and len(times) >= minimum_frames
            and times[-1] - times[0] <= policy["maximum_span_ms"]
            and all(
                later - earlier <= policy["maximum_gap_ms"]
                for earlier, later in zip(times, times[1:])
            )
        )

    direct_by_amount: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for item in direct:
        direct_by_amount[int(item["amount"])].append(item)

    possible: list[dict[str, Any]] = []
    for short_amount, short_items in direct_by_amount.items():
        if not bounded_frames(
            short_items,
            minimum_frames=policy["minimum_tight_frames"],
        ):
            continue
        # At least two tight frames must support the proposed prefix.  A
        # candidate from another amount remains visible and is handled as a
        # conflict below; no numeric magnitude ranking is used.
        associated: list[dict[str, Any]] = []
        conflicts = False
        for candidate in full_views:
            same_frame = [
                tight for tight in short_items
                if _clipping_identity(tight) == _clipping_identity(candidate)
                and source_geometry_overlaps(candidate, tight)
            ]
            amount = int(candidate["amount"])
            if same_frame and _strict_prefix(short_amount, amount):
                if candidate.get("crop_family") in {"wide_gain", "expanded_gain"}:
                    broader = any(
                        _strictly_contains(
                            tuple(candidate["box"]), tuple(tight["box"]),
                        )
                        for tight in same_frame
                    )
                    if broader:
                        associated.append(candidate)
                    else:
                        conflicts = True
                else:
                    associated.append(candidate)
            elif amount != short_amount:
                # A readable alternate on the same scoped field is a real
                # contradiction unless it is the one strict extension being
                # evaluated.  This rejects +72 versus +71 beside a +7 badge.
                if _strict_prefix(short_amount, amount) or same_frame:
                    conflicts = True
                elif candidate.get("crop_family") in {"result", "wide_gain", "expanded_gain"}:
                    conflicts = True

        full_values = {int(item["amount"]) for item in associated}
        full_frames = {_clipping_identity(item) for item in associated}
        full_families = {item.get("crop_family") for item in associated}
        if (
            conflicts
            or len(full_values) != 1
            or len(full_frames) < policy["minimum_full_frames"]
            or "result" not in full_families
            or not full_families.intersection({"wide_gain", "expanded_gain"})
            or not bounded_frames(
                associated,
                minimum_frames=policy["minimum_full_frames"],
            )
        ):
            continue
        full_amount = next(iter(full_values))
        # All qualifying source candidates in this phase must agree with the
        # same pair.  Equal short reads are expected prefix views; a second
        # complete amount or a non-prefix signed amount makes the phase
        # ambiguous and is never silently discarded.
        phase_conflict = False
        for candidate in full_views:
            amount = int(candidate["amount"])
            if amount == short_amount:
                continue
            if amount != full_amount:
                phase_conflict = True
                break
            identity = _clipping_identity(candidate)
            if identity not in full_frames:
                # A matching amount with no overlapping tight source crop is
                # not evidence for this field's badge.
                phase_conflict = True
                break
        if phase_conflict:
            continue
        possible.append(
            dict(
                short_amount=short_amount,
                full_amount=full_amount,
                short_observations=sorted(
                    short_items,
                    key=lambda item: (item["source_timestamp_ms"], item["evidence"]),
                ),
                full_observations=sorted(
                    associated,
                    key=lambda item: (item["source_timestamp_ms"], item["evidence"], item["region"]),
                ),
            )
        )

    if len(possible) != 1:
        reason = "insufficient_source_clipping_corroboration"
        if possible:
            reason = "ambiguous_multiple_source_clipping_pairs"
        return _source_clipping_unresolved(field, phase_key, reason, deduped, policy)

    selected = possible[0]
    accepted = selected["full_observations"]
    return {
        "status": "accepted",
        "accepted_amount": selected["full_amount"],
        "field": field,
        "phase_key": phase_key,
        "basis": "cross_frame_source_clipped_gain_overlay_and_broad_agreement",
        "reason": None,
        "observed_amounts": sorted({
            int(item["amount"]) for item in deduped
            if type(item.get("amount")) is int
        }),
        "observations": [_clipping_proof(item) for item in deduped],
        "accepted_observations": [_clipping_proof(item) for item in accepted],
        "short_observations": [
            _clipping_proof(item) for item in selected["short_observations"]
        ],
        "full_observations": [_clipping_proof(item) for item in accepted],
        "policy": policy,
    }


# Short alias for callers that do not need to distinguish this from the other
# source-only gain resolvers.  Keep the descriptive name above in provenance.
resolve_clipped_gain = resolve_source_clipped_gain


def _unresolved(field: str, phase_key: Any, reason: str, observations: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    unique = []
    seen = set()
    for item in observations:
        key = _observation_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(_proof(item))
    return {
        "status": "unresolved",
        "accepted_amount": None,
        "field": field,
        "phase_key": phase_key,
        "basis": "candidate_only_source_evidence_unresolved",
        "reason": reason,
        "observations": unique,
        "accepted_observations": [],
        "policy": candidate_recovery_policy(),
    }


def resolve_candidate_only_gain(
    rows: Iterable[Mapping[str, Any]],
    field: str,
    *,
    phase_key: Any = None,
    minimum_tight_confidence: float = CANDIDATE_RECOVERY_MIN_CONFIDENCE,
    minimum_broad_confidence: float = CANDIDATE_RECOVERY_WIDE_MIN_CONFIDENCE,
    minimum_repeated_frames: int = CANDIDATE_RECOVERY_MIN_FRAMES,
    maximum_span_ms: int = CANDIDATE_RECOVERY_MAX_SPAN_MS,
    maximum_gap_ms: int = CANDIDATE_RECOVERY_MAX_GAP_MS,
) -> dict[str, Any]:
    """Resolve one candidate-only gain from one committed result phase.

    ``rows`` must already be scoped to one event/window by the caller.  The
    resolver still checks result-screen identity and row-level phase fields.
    It accepts either:

    * at least two distinct result frames with the same tight ``gain`` amount,
      same phase signature, and bounded temporal span; or
    * one tight signed amount and one broader signed-prefix view on the same
      physical frame, with nested geometry and matching values.

    The accepted proof contains only source crop facts.  No balance, expected
    delta, result total, or state-derived field is consulted.
    """

    if not isinstance(field, str) or not field:
        result = _unresolved(field, phase_key, "invalid_training_field", [])
        result["policy"] = candidate_recovery_policy()
        return result
    if phase_key is None:
        result = _unresolved(field, phase_key, "missing_source_phase_owner", [])
        result["policy"] = candidate_recovery_policy()
        return result

    rows = list(rows)
    policy = candidate_recovery_policy()
    if any(not isinstance(row, Mapping) for row in rows):
        result = _unresolved(field, phase_key, "invalid_source_identity", [])
        result["policy"] = policy
        return result
    overrides = _validated_candidate_recovery_overrides(
        minimum_tight_confidence=minimum_tight_confidence,
        minimum_broad_confidence=minimum_broad_confidence,
        minimum_repeated_frames=minimum_repeated_frames,
        maximum_span_ms=maximum_span_ms,
        maximum_gap_ms=maximum_gap_ms,
    )
    if overrides is None:
        result = _unresolved(field, phase_key, "invalid_candidate_recovery_policy", [])
        result["policy"] = policy
        return result
    policy.update(overrides)
    for row in rows:
        if isinstance(row, Mapping) and _invalid_source_row(row):
            result = _unresolved(field, phase_key, "invalid_source_identity", [])
            result["policy"] = policy
            return result
        if isinstance(row, Mapping) and _invalid_candidate_confidence(row, field):
            result = _unresolved(field, phase_key, "invalid_source_confidence", [])
            result["policy"] = policy
            return result
    extracted: list[dict[str, Any]] = []
    valid_rows = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        phase = _phase_signature(row)
        if phase is None:
            continue
        valid_rows += 1
        extracted.extend(_candidate_observations(row, field))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str, str, int | None, str]] = set()
    for item in extracted:
        key = _observation_instance_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    # A normalized evidence path identifies one physical source frame.  Keep
    # copied rows in the diagnostic set so the resolver can report its usual
    # insufficient-corroboration result, but count physical identities (below)
    # rather than timestamps as independent votes.  Distinct crop families
    # at one timestamp remain eligible for the nested same-frame branch.
    for item in deduped:
        if _evidence_identity(item.get("evidence")) is None:
            result = _unresolved(field, phase_key, "invalid_source_identity", deduped)
            result["policy"] = policy
            return result
    if not deduped:
        reason = "no_source_signed_gain_candidate"
        if valid_rows == 0:
            reason = "no_committed_result_phase_rows"
        result = _unresolved(field, phase_key, reason, deduped)
        result["policy"] = policy
        return result

    direct = [
        item for item in deduped
        if item["crop_family"] == "gain"
        and _confidence_at_least(item, minimum_tight_confidence)
    ]
    accepted: list[dict[str, Any]] = []

    # Prefer repeated tight source frames.  A field/amount with a different
    # phase signature is a separate occurrence, even if its number matches.
    groups: dict[tuple[int, tuple[Any, ...]], list[dict[str, Any]]] = defaultdict(list)
    for item in direct:
        groups[(int(item["amount"]), tuple(item["phase_signature"]))].append(item)
    repeated_candidates: list[tuple[int, tuple[Any, ...], list[dict[str, Any]]]] = []
    for (amount, phase), group in groups.items():
        # Evidence identity is the physical-frame boundary.  A timestamp can
        # be sampled more than once for the same image, so timestamp changes
        # alone must not create a second vote.
        physical = {_physical_frame_identity(item) for item in group}
        times = sorted({int(item["source_timestamp_ms"]) for item in group})
        group_frames = {
            _physical_frame_identity(item)
            for item in group
        }
        broad_conflict = any(
            _confidence_at_least(item, minimum_broad_confidence)
            and tuple(item["phase_signature"]) == phase
            and (
                _physical_frame_identity(item) in group_frames
                and int(item["amount"]) != amount
            )
            for item in deduped
            if item["crop_family"] in {"wide_gain", "expanded_gain"}
        )
        shapes = {
            _row_shape(row)
            for row in rows
            if isinstance(row, Mapping)
            and _timestamp(row) in {item["source_timestamp_ms"] for item in group}
            and _evidence_identity(_evidence(row)) in {
                _evidence_identity(item.get("evidence")) for item in group
            }
        }
        if (
            len(physical) == len(group)
            and len(physical) >= int(minimum_repeated_frames)
            and len(times) >= int(minimum_repeated_frames)
            and times[-1] - times[0] <= int(maximum_span_ms)
            and all(later - earlier <= int(maximum_gap_ms) for earlier, later in zip(times, times[1:]))
            and len(shapes) <= 1
            and not broad_conflict
        ):
            repeated_candidates.append((amount, phase, group))
    if len(repeated_candidates) == 1:
        amount, phase, group = repeated_candidates[0]
        eligible_source = [
            item for item in deduped
            if (
                item["crop_family"] == "gain"
                and _confidence_at_least(item, minimum_tight_confidence)
            ) or (
                item["crop_family"] in {"wide_gain", "expanded_gain"}
                and _confidence_at_least(item, minimum_broad_confidence)
            )
        ]
        conflicting = any(
            tuple(item["phase_signature"]) != phase
            or int(item["amount"]) != amount
            for item in eligible_source
        )
        if not conflicting:
            accepted = sorted(group, key=lambda item: (item["source_timestamp_ms"], item["evidence"]))
            return {
                "status": "accepted",
                "accepted_amount": amount,
                "field": field,
                "phase_key": phase_key,
                "basis": "repeated_source_gain_badge_same_phase",
                "reason": None,
                "observations": [_proof(item) for item in deduped],
                "accepted_observations": [_proof(item) for item in accepted],
                "policy": policy,
            }

    # A same-frame broader view is a second source geometry, not an alternate
    # OCR vote.  It may carry harmless terminal punctuation, but it must nest
    # the tight crop and agree on the amount.  Result crops are never eligible.
    by_frame: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in deduped:
        by_frame[_physical_frame_identity(item)].append(item)
    dual_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for frame_items in by_frame.values():
        tights = [
            item for item in frame_items
            if item["crop_family"] == "gain"
            and _confidence_at_least(item, minimum_tight_confidence)
        ]
        broads = [
            item for item in frame_items
            if item["crop_family"] in {"wide_gain", "expanded_gain"}
            and _confidence_at_least(item, minimum_broad_confidence)
        ]
        for tight in tights:
            for broad in broads:
                if (
                    int(tight["amount"]) == int(broad["amount"])
                    and tuple(tight["phase_signature"]) == tuple(broad["phase_signature"])
                    and _contains(tuple(broad["box"]), tuple(tight["box"]))
                ):
                    dual_matches.append((tight, broad))
    if len(dual_matches) == 1:
        tight, broad = dual_matches[0]
        selected_amount = int(tight["amount"])
        selected_phase = tuple(tight["phase_signature"])
        eligible_source = [
            item for item in deduped
            if (
                item["crop_family"] == "gain"
                and _confidence_at_least(item, minimum_tight_confidence)
            ) or (
                item["crop_family"] in {"wide_gain", "expanded_gain"}
                and _confidence_at_least(item, minimum_broad_confidence)
            )
        ]
        # Do not let a valid-looking pair win after another eligible crop or
        # phase has already made the source ambiguous.  In particular, this
        # guard prevents the dual path from bypassing a rejected repeated
        # group that contained a confident conflicting view.
        if (
            any(tuple(item["phase_signature"]) != selected_phase for item in eligible_source)
            or any(int(item["amount"]) != selected_amount for item in eligible_source)
        ):
            result = _unresolved(field, phase_key, "candidate_conflict_or_phase_change", deduped)
            result["policy"] = policy
            return result
        return {
            "status": "accepted",
            "accepted_amount": selected_amount,
            "field": field,
            "phase_key": phase_key,
            "basis": "same_frame_nested_source_gain_crop_agreement",
            "reason": None,
            "observations": [_proof(item) for item in deduped],
            "accepted_observations": [_proof(tight), _proof(broad)],
            "policy": policy,
        }

    reason = "candidate_conflict_or_phase_change"
    if direct and not repeated_candidates and not dual_matches:
        reason = "insufficient_same_phase_corroboration"
    elif len(repeated_candidates) > 1 or len(dual_matches) > 1:
        reason = "ambiguous_multiple_source_corroborations"
    result = _unresolved(field, phase_key, reason, deduped)
    result["policy"] = policy
    return result


def resolve_occluded_prefix(observations, *, minimum_complete_frames=3, maximum_span_ms=500):
    """Accept a repeated complete badge whose trailing digits were occluded.

    Particles and sparkles over a result badge can hide its trailing digit on
    many dense frames, so the short reading may outnumber the complete one.
    A displayed badge never loses a correct trailing digit and gains it back;
    the complete value is therefore accepted when exactly two values were
    read, the short one is a strict decimal prefix of the complete one, and
    the complete value was observed on at least ``minimum_complete_frames``
    distinct physical frames with no frame reading both values.  No balance,
    preview or expected award is consulted.
    """
    if type(minimum_complete_frames) is not int or minimum_complete_frames < 2:
        return None
    if type(maximum_span_ms) is not int or maximum_span_ms < 0:
        return None
    validated = []
    seen_identity = set()
    seen_evidence = set()
    for pair in observations:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            return None
        row, value = pair
        if not isinstance(row, Mapping) or type(value) is not int or value < 0:
            return None
        timestamp = _timestamp(row)
        evidence = _evidence(row)
        evidence_identity = _evidence_identity(evidence)
        if timestamp is None or evidence is None or evidence_identity is None:
            return None
        identity = (timestamp, evidence_identity)
        if identity in seen_identity or evidence_identity in seen_evidence:
            return None
        seen_identity.add(identity)
        seen_evidence.add(evidence_identity)
        validated.append((row, value, timestamp, evidence))
    if not validated:
        return None
    by_time = {}
    for row, value, timestamp, evidence in validated:
        by_time.setdefault(timestamp, set()).add(value)
    if any(len(values) != 1 for values in by_time.values()):
        return None
    values = {next(iter(v)) for v in by_time.values()}
    if len(values) != 2:
        return None
    complete = max(values, key=lambda n: len(str(n)))
    short = next(v for v in values if v != complete)
    if not str(complete).startswith(str(short)) or len(str(complete)) <= len(str(short)):
        return None
    full_times = {t for t, vs in by_time.items() if complete in vs}
    if len(full_times) < minimum_complete_frames or max(by_time) - min(by_time) > maximum_span_ms:
        return None

    def proofs(value):
        return [dict(source_timestamp_ms=timestamp, evidence=evidence, value=value)
                for row, observed_value, timestamp, evidence in validated
                if observed_value == value]
    return dict(accepted_amount=complete, observed_amounts=sorted(values),
                basis='repeated_complete_badge_with_occluded_prefix_observations',
                complete_frames=len(full_times), prefix_frames=len(by_time) - len(full_times),
                complete_observations=proofs(complete), prefix_observations=proofs(short))


def resolve_prefix(observations, *, minimum_span_ms=30, maximum_span_ms=250):
    """Require two complete frames and only brief, less-supported prefixes.

    No before/after balance, expected award or chosen numeric delta is input.
    A singleton complete badge, a sustained alternative or a non-prefix
    disagreement remains unresolved. Duplicate OCR views cannot supply votes.
    """
    if (
        type(minimum_span_ms) is not int
        or minimum_span_ms < 0
        or type(maximum_span_ms) is not int
        or maximum_span_ms < minimum_span_ms
    ):
        return None

    # Prefix resolution is still source-bound.  In particular, do not coerce
    # a malformed timestamp or treat two rows pointing at the same physical
    # frame as independent corroboration.  Normalize path aliases only for
    # identity checks; preserve the original row values in the returned proof
    # so the diagnostic remains auditable.
    validated = []
    seen_identity = set()
    seen_evidence = set()
    for pair in observations:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            return None
        row, value = pair
        if not isinstance(row, Mapping) or type(value) is not int or value < 0:
            return None
        timestamp = _timestamp(row)
        evidence = _evidence(row)
        evidence_identity = _evidence_identity(evidence)
        if timestamp is None or evidence is None or evidence_identity is None:
            return None
        identity = (timestamp, evidence_identity)
        # A physical source frame cannot become independent corroboration by
        # being copied under another timestamp.  Keep the timestamp in the
        # row proof for auditability, but reject the duplicate identity here.
        if identity in seen_identity or evidence_identity in seen_evidence:
            return None
        seen_identity.add(identity)
        seen_evidence.add(evidence_identity)
        validated.append((row, value, timestamp, evidence))

    if not validated:
        return None

    by_time={}
    for row,value,timestamp,evidence in validated:
        by_time.setdefault(timestamp,set()).add(value)
    if any(len(values)!=1 for values in by_time.values()):return None
    values={next(iter(v)) for v in by_time.values()}
    if len(values)!=2:return None
    complete=max(values,key=lambda n:len(str(n)))
    short=next(v for v in values if v!=complete)
    if not str(complete).startswith(str(short)) or len(str(complete))<=len(str(short)):return None
    full_times={t for t,vs in by_time.items() if complete in vs}
    short_times={t for t,vs in by_time.items() if short in vs}
    if (len(full_times)<2 or len(short_times)>2 or len(full_times)<len(short_times)
        or max(full_times)-min(full_times)<minimum_span_ms
        or max(by_time)-min(by_time)>maximum_span_ms):return None
    shapes={value:{frozenset(row.get('facts',{}).get('observed_training_gain_fields',
                   [k for k,n in row.get('facts',{}).get('training_gains',{}).items() if type(n) is int]))
                   for row,n,_,_ in validated if n==value} for value in values}
    # Changing companion badges may indicate a separate component phase.
    # Mixed shapes cannot become clipping evidence just because the more
    # specific component-phase recognizer could not resolve them.
    if (len(shapes[complete])>1 or len(shapes[short])>1 or any(short_shape<full_shape
            for short_shape in shapes[short] for full_shape in shapes[complete])):
        return None
    def proofs(value):
        return [dict(source_timestamp_ms=timestamp,evidence=evidence,value=value)
                for row,observed_value,timestamp,evidence in validated
                if observed_value==value]
    return dict(accepted_amount=complete,observed_amounts=sorted(values),
                basis='repeated_complete_badge_with_brief_prefix_observations',
                complete_observations=proofs(complete),prefix_observations=proofs(short))
