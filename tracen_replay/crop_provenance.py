"""Resolve numeric OCR crops while retaining their source geometry.

The training result has several intentionally overlapping OCR views.  A
result crop is useful as a diagnostic, but it is not the source of a training
award: it contains the post-action counter and can therefore disagree with
the signed badge.  This module keeps the observations from every view and
selects a canonical amount only from an eligible signed amount crop.

The resolver is deliberately independent of recordings, timestamps and
balances.  Those facts belong to the transaction and phase layers.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Iterable, Mapping
from .ocr_confidence import confidence_percent


# The values express the crop's physical role, rather than an OCR confidence
# ordering.  A wider crop can preserve a clipped leading/trailing digit, but
# a result crop is always diagnostic-only even when its OCR score is perfect.
CROP_FAMILY_RULES = {
    "gain": {
        "canonical": True,
        "minimum_confidence": 98.0,
        "allow_signed_prefix": False,
        "rank": 30,
    },
    "wide_gain": {
        "canonical": True,
        "minimum_confidence": 90.0,
        "allow_signed_prefix": False,
        "rank": 40,
    },
    "expanded_gain": {
        "canonical": True,
        "minimum_confidence": 80.0,
        "allow_signed_prefix": True,
        "rank": 50,
    },
    "scaled_gain": {
        "canonical": True,
        "minimum_confidence": 98.0,
        "allow_signed_prefix": False,
        "rank": 35,
    },
    # A localized badge is a supplemental source-pixel view.  It is kept
    # below the native broad-crop rank so the ordinary resolver still prefers
    # its established geometry when the readings agree.  The training gain
    # phase resolver gives this family a stricter source-pixel agreement gate
    # before it can resolve a disagreement.
    "localized_gain": {
        "canonical": True,
        "minimum_confidence": 97.0,
        "allow_signed_prefix": False,
        "rank": 25,
    },
    "result": {
        "canonical": False,
        "minimum_confidence": 0.0,
        "allow_signed_prefix": False,
        "rank": 0,
    },
}

_SIGNED_PREFIX = re.compile(r"^\s*\+\s*(?P<digits>\d{1,3})(?P<suffix>[^\d\s]*)\s*$")
_SIGNED_EXACT = re.compile(r"^\s*\+\s*(?P<digits>\d{1,3})\s*$")
_GLOBAL_GAMEPLAY_PANE = (148.0, 0.0, 958.0, 1080.0)
_SOURCE_REFINEMENT_SCHEMA = "tracen-replay/training-gain-source-refinement-v1"
_SOURCE_REFINEMENT_ROLES = frozenset(("inner", "outer"))
_SOURCE_REFINEMENT_GEOMETRY = {
    "inner": "canonical_gain_relative_inner",
    "outer": "wide_gain_relative_outer",
}
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def crop_family(name: str) -> str | None:
    """Return the normalized family for a region key.

    Region keys in the native reader are ``family.field``.  Award refinement
    crops use ``scaled_gain_<index>.field``; all of those share one family.
    Unknown keys are ignored instead of being treated as amount evidence.
    """

    if not isinstance(name, str):
        return None
    if name.startswith("scaled_gain_") and "." in name:
        return "scaled_gain"
    for family in CROP_FAMILY_RULES:
        if name.startswith(family + "."):
            return family
    return None


def _box(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(
        isinstance(part, bool) or not isinstance(part, (int, float))
        for part in value
    ):
        return None
    try:
        result = [float(part) for part in value]
    except (TypeError, ValueError, OverflowError):
        return None
    left, top, right, bottom = result
    if not all(math.isfinite(part) for part in result):
        return None
    pane_left, pane_top, pane_right, pane_bottom = _GLOBAL_GAMEPLAY_PANE
    if not (
        pane_left <= left < right <= pane_right
        and pane_top <= top < bottom <= pane_bottom
    ):
        return None
    return result


def _text(observation: Mapping[str, Any]) -> str:
    return str(observation.get("text", "")).strip()


def _amount(observation: Mapping[str, Any], family: str) -> tuple[int, str, str] | None:
    """Parse a signed amount and return amount, normalization and suffix."""

    text = _text(observation)
    exact = _SIGNED_EXACT.fullmatch(text)
    if exact:
        return int(exact.group("digits")), "signed_amount", ""
    rule = CROP_FAMILY_RULES[family]
    if not rule["allow_signed_prefix"]:
        return None
    match = _SIGNED_PREFIX.fullmatch(text)
    if not match:
        return None
    suffix = match.group("suffix")
    # Expanded crops can include a nearby glyph after a clipped amount.  A
    # plus sign, slash, or another digit would indicate a merged expression,
    # which this source crop cannot establish as one award.
    if any(char in suffix for char in "+/"):
        return None
    return int(match.group("digits")), "signed_amount_prefix", suffix


def _default_role(family: str, parsed: bool) -> str:
    if family == "result":
        return "result_crop_diagnostic_excluded"
    if parsed:
        return "amount_crop_candidate"
    return "unparsed_crop_candidate"


def source_refinement_candidate_is_bound(
    candidate: Mapping[str, Any],
) -> bool:
    """Validate the typed proof for a dotted relative gain crop.

    Native ``expanded_gain.<field>`` records predate the source-refinement
    sidecar and remain compatible.  Only the new ``inner``/``outer`` names
    need this additional boundary.  Returning ``True`` for every other
    region keeps this helper a narrow supplement rather than a new global
    crop policy.
    """

    if not isinstance(candidate, Mapping):
        return False
    name = candidate.get("region")
    if not isinstance(name, str) or not name.startswith("expanded_gain."):
        return True
    parts = name.split(".")
    if len(parts) != 3:
        return True
    role = parts[1]
    if role not in _SOURCE_REFINEMENT_ROLES:
        return False
    if candidate.get("source_refinement_schema") != _SOURCE_REFINEMENT_SCHEMA:
        return False
    if candidate.get("source_refinement_role") != role:
        return False
    if candidate.get("source_refinement_geometry") != _SOURCE_REFINEMENT_GEOMETRY[role]:
        return False
    if candidate.get("source_refinement_verified") is not True:
        return False
    if candidate.get("source_pixel_verified") is not True:
        return False
    if candidate.get("source_pixel_basis") != "relative_training_gain_source_crop":
        return False
    if candidate.get("source_observation_basis") != "source_pixel_refined_training_gain":
        return False
    if candidate.get("source_refinement_pixel_bound") is not True:
        return False
    from .training_gain_source_refinement import refinement_requests
    expected = {region: box for region, box, _ in refinement_requests()}
    if _box(candidate.get('box')) != expected.get(name):
        return False
    crop_sha = candidate.get("source_crop_sha256")
    return isinstance(crop_sha, str) and _SHA256.fullmatch(crop_sha) is not None


def _canonical_eligible(
    name: str,
    observation: Mapping[str, Any],
    family: str,
    parsed: tuple[int, str, str] | None,
) -> bool:
    """Apply crop role and family quality rules to one observation."""

    if parsed is None or not CROP_FAMILY_RULES[family]["canonical"]:
        return False
    if _box(observation.get('box')) is None:
        return False
    # Fixture and refinement records may carry an explicit role.  It is
    # authoritative for the input boundary: an excluded/unmarked candidate
    # must remain a diagnostic even if its text happens to start with '+'.
    role = observation.get("role")
    if role is not None and role != "amount_crop_candidate":
        return False
    if observation.get("input_eligible") is False:
        return False
    # The source-refinement reader uses dotted region names to distinguish
    # its two relative crops.  Treat those names as an evidence boundary:
    # the crop must carry the typed schema, role, geometry, pixel hash, and
    # source-pixel basis generated by the reader.  A copied name or a
    # self-authored amount must remain a diagnostic candidate.
    if family == "expanded_gain" and name.count(".") == 2:
        if not source_refinement_candidate_is_bound(
            dict(observation, region=name),
        ):
            return False
    confidence = confidence_percent(observation.get('confidence'))
    return confidence is not None and confidence >= CROP_FAMILY_RULES[family]["minimum_confidence"]


def _candidate(name: str, observation: Mapping[str, Any]) -> dict[str, Any] | None:
    family = crop_family(name)
    if family is None:
        return None
    parsed = _amount(observation, family)
    role = observation.get("role")
    if not isinstance(role, str) or not role:
        role = _default_role(family, parsed is not None)
    confidence: float | int = confidence_percent(observation.get('confidence'))
    if confidence is None:
        confidence = 0
    if isinstance(confidence, float) and confidence.is_integer():
        confidence = int(confidence)
    record: dict[str, Any] = {
        "region": name,
        "crop_family": family,
        "raw_text": _text(observation),
        "confidence": confidence,
        "box": _box(observation.get("box")),
        "source_role": role,
        "input_eligible": bool(observation.get("input_eligible", family != "result")),
        "canonical_eligible": _canonical_eligible(name, observation, family, parsed),
    }
    # Source-localized observations carry their binding in the candidate
    # record so the phase resolver can validate the actual pixel proof.  Keep
    # this allow-list narrow: arbitrary metadata must not become an evidence
    # channel merely because it was attached to a raw OCR region.
    for key in (
        "source_pixel_verified", "pixel_rgb_sha256", "gameplay_sha256",
        "source_frame_sha256", "source_frame_evidence", "source_frame_id",
        "source_timestamp_ms", "evidence", "evidence_sha256", "raw_sha256",
        "localization_schema", "source_observation_basis", "component_box",
        "scan_box", "source_pixel_basis", "normalization",
        "source_refinement_schema", "source_refinement_role",
        "source_refinement_geometry", "source_refinement_verified",
        "source_refinement_pixel_bound", "source_refinement_validation",
        "source_crop_sha256",
    ):
        if key in observation:
            record[key] = deepcopy(observation[key])
    if parsed is not None:
        record.update(
            amount=parsed[0],
            normalization=parsed[1],
            suffix=parsed[2],
        )
    else:
        record.update(amount=None, normalization=None, suffix=None)
    return record


def collect_gain_candidates(
    regions: Mapping[str, Mapping[str, Any]], field: str,
) -> list[dict[str, Any]]:
    """Collect all crop observations for ``field`` in deterministic order.

    The returned list includes malformed and diagnostic-only observations so
    callers can expose the full OCR disagreement.  Only records with an
    integer amount and ``canonical_eligible`` may become the canonical gain.
    """

    if not isinstance(regions, Mapping):
        return []
    found: list[dict[str, Any]] = []
    for name in sorted(regions):
        if not isinstance(name, str) or not name.endswith("." + str(field)):
            continue
        observation = regions[name]
        if not isinstance(observation, Mapping):
            continue
        candidate = _candidate(name, observation)
        if candidate is not None:
            found.append(candidate)
    # Stable physical family order avoids the former insertion/last-crop
    # overwrite behavior while retaining deterministic ties within a family.
    found.sort(key=lambda item: (
        -CROP_FAMILY_RULES[item["crop_family"]]["rank"],
        item["region"],
    ))
    return found


def numeric_amounts(candidates: Iterable[Mapping[str, Any]]) -> list[int]:
    """Return sorted numeric alternatives, including diagnostic crops."""

    return sorted({
        int(candidate["amount"])
        for candidate in candidates
        if type(candidate.get("amount")) is int
    })


def source_amounts(
    candidates: Iterable[Mapping[str, Any]],
    resolution: Mapping[str, Any] | None = None,
) -> list[int]:
    """Return transaction-facing alternatives from source amount crops.

    Low-confidence and malformed raw readings stay in the provenance record,
    while this compatibility list contains only signed amount-crop values at
    the existing 90 confidence floor.  The selected canonical value is kept
    even when a broad crop used a noisy but readable signed prefix.  Result
    crops never enter this list.
    """

    values = set()
    for candidate in candidates:
        if not candidate.get("canonical_eligible"):
            continue
        if candidate.get("crop_family") == "result":
            continue
        if type(candidate.get("amount")) is not int:
            continue
        try:
            confidence = float(candidate.get("confidence", 0))
        except (TypeError, ValueError):
            continue
        if confidence >= 90:
            values.add(int(candidate["amount"]))
    if isinstance(resolution, Mapping) and type(resolution.get("canonical_amount")) is int:
        values.add(int(resolution["canonical_amount"]))
    return sorted(values)


def source_signed_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    include_result: bool = False,
) -> list[dict[str, Any]]:
    """Return source OCR candidates carrying a strict signed amount.

    ``source_amounts`` is intentionally limited to canonical amount crops for
    transaction projection.  A clipped badge can also be readable through an
    overlapping diagnostic crop, however, and the clipping resolver needs to
    retain that fact without turning the diagnostic into a canonical amount.
    This helper exposes the typed evidence channel while preserving each
    candidate's family, role, confidence, and geometry.

    Result-family candidates are included only when ``include_result`` is
    explicitly requested.  They remain diagnostic in the returned record and
    must be corroborated by a separate source crop before any caller can use
    them for a bounded recovery.
    """

    found: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        family = candidate.get("crop_family")
        if family not in CROP_FAMILY_RULES:
            continue
        if family == "result" and not include_result:
            continue
        region = candidate.get("region")
        if not isinstance(region, str) or crop_family(region) != family:
            continue
        if not source_refinement_candidate_is_bound(candidate):
            continue
        if type(candidate.get("amount")) is not int:
            continue
        normalization = candidate.get("normalization")
        if normalization not in {"signed_amount", "signed_amount_prefix"}:
            continue
        if family != "expanded_gain" and normalization != "signed_amount":
            continue
        raw_text = str(candidate.get("raw_text", ""))
        if normalization == "signed_amount":
            parsed = _SIGNED_EXACT.fullmatch(raw_text)
            if parsed is None or int(parsed.group("digits")) != int(candidate["amount"]):
                continue
        else:
            parsed = _SIGNED_PREFIX.fullmatch(raw_text)
            if parsed is None or int(parsed.group("digits")) != int(candidate["amount"]):
                continue
            suffix = parsed.group("suffix")
            if any(char in suffix for char in "+/"):
                continue
        if family == "result":
            if (
                candidate.get("source_role") != "result_crop_diagnostic_excluded"
                or candidate.get("input_eligible") is not False
            ):
                continue
        elif candidate.get("input_eligible") is not True:
            continue
        if _box(candidate.get("box")) is None:
            continue
        raw_role = candidate.get("source_role")
        if family == "result":
            role = "signed_overlay_diagnostic"
        elif raw_role in {"amount_crop_candidate", "unparsed_crop_candidate"}:
            role = raw_role
        else:
            continue
        item = deepcopy(dict(candidate))
        item["source_observation_role"] = role
        found.append(item)
    found.sort(
        key=lambda item: (
            -CROP_FAMILY_RULES[item["crop_family"]]["rank"],
            str(item.get("region", "")),
            str(item.get("raw_text", "")),
        )
    )
    return found


def _contains(outer: list[float] | None, inner: list[float] | None) -> bool:
    if outer is None or inner is None:
        return False
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
    )


def _overlaps_inner(outer: list[float] | None, inner: list[float] | None) -> bool:
    """Allow small crop jitter while requiring the same source geometry."""

    if outer is None or inner is None:
        return False
    intersection = max(0.0, min(outer[2], inner[2]) - max(outer[0], inner[0])) * max(
        0.0, min(outer[3], inner[3]) - max(outer[1], inner[1])
    )
    inner_area = _area(inner)
    center_x = (inner[0] + inner[2]) / 2
    center_y = (inner[1] + inner[3]) / 2
    return inner_area > 0 and intersection / inner_area >= 0.75 and (
        outer[0] <= center_x <= outer[2] and outer[1] <= center_y <= outer[3]
    )


def source_geometry_overlaps(
    first: Mapping[str, Any] | Iterable[float],
    second: Mapping[str, Any] | Iterable[float],
    *,
    minimum_fraction: float = 0.60,
) -> bool:
    """Return whether two OCR boxes can describe one visible badge.

    The normal ``wide_gain`` crop contains the tight crop.  A result overlay
    can shift a few pixels while remaining the same badge, so strict
    containment is too narrow for this source channel.  The comparison uses
    intersection over the smaller box and rejects invalid thresholds or
    disjoint geometry.  It is an association predicate only; it never chooses
    an amount.
    """

    def box(value: Mapping[str, Any] | Iterable[float]) -> list[float] | None:
        if isinstance(value, Mapping):
            value = value.get("box")
        return _box(value)

    try:
        fraction = float(minimum_fraction)
    except (TypeError, ValueError):
        return False
    if not 0.0 < fraction <= 1.0:
        return False
    first_box = box(first)
    second_box = box(second)
    if first_box is None or second_box is None:
        return False
    intersection = max(
        0.0,
        min(first_box[2], second_box[2]) - max(first_box[0], second_box[0]),
    ) * max(
        0.0,
        min(first_box[3], second_box[3]) - max(first_box[1], second_box[1]),
    )
    smaller = min(_area(first_box), _area(second_box))
    return smaller > 0.0 and intersection / smaller >= fraction


def _area(box: list[float] | None) -> float:
    if box is None:
        return 0.0
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _broader(candidate: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    """Whether candidate has a source geometry broader than other."""

    candidate_rank = CROP_FAMILY_RULES[candidate["crop_family"]]["rank"]
    other_rank = CROP_FAMILY_RULES[other["crop_family"]]["rank"]
    if candidate_rank <= other_rank:
        return False
    return _contains(candidate.get("box"), other.get("box")) or _overlaps_inner(
        candidate.get("box"), other.get("box")
    )


def _strong(candidate: Mapping[str, Any]) -> bool:
    if not candidate.get("canonical_eligible"):
        return False
    family = candidate["crop_family"]
    confidence = confidence_percent(candidate.get('confidence'))
    return confidence is not None and confidence >= CROP_FAMILY_RULES[family]["minimum_confidence"]


def _strict_prefix(short: int, long: int) -> bool:
    short_text, long_text = str(short), str(long)
    return len(long_text) > len(short_text) and long_text.startswith(short_text)


def _source_pixel_localized(candidate: Mapping[str, Any]) -> bool:
    """Return whether a candidate carries the complete localized proof.

    The family name is necessary but insufficient.  A caller can construct a
    raw region with that name without having decoded the source pixels.  Keep
    the proof predicate exact and independent of amount magnitude or report
    balances so a copied diagnostic cannot become a canonical gain.
    """

    if candidate.get("crop_family") != "localized_gain":
        return False
    if candidate.get("source_pixel_verified") is not True:
        return False
    if candidate.get("source_observation_basis") != "source_pixel_localized_training_badge":
        return False
    pixel_sha = candidate.get("pixel_rgb_sha256")
    if not isinstance(pixel_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", pixel_sha):
        return False
    return _strong(candidate)


def _resolve_source_pixel_localized(
    records: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    result: dict[str, Any],
) -> bool:
    """Resolve a localized badge only with same-view tight agreement.

    A localized crop is an independent pixel view, not a free-standing OCR
    amount.  The native tight ``gain`` crop must read the same signed amount,
    and any competing canonical crop must either be that tight view or a
    broader crop overlapping the localized component.  This lets a clean
    ``+2`` localized view disambiguate a contaminated ``+200`` broad view
    while preserving unrelated/non-overlapping conflicts as unresolved.
    """

    localized = [candidate for candidate in eligible if _source_pixel_localized(candidate)]
    if not localized:
        return False
    values = {int(candidate["amount"]) for candidate in localized}
    if len(values) != 1:
        result["conflict_state"] = "unresolved_conflicting_localized_badges"
        return True
    amount = next(iter(values))
    tight = [candidate for candidate in eligible if candidate.get("crop_family") == "gain"]
    matching_tight = [candidate for candidate in tight if int(candidate.get("amount")) == amount]
    if any(int(candidate.get("amount")) != amount for candidate in tight):
        result["conflict_state"] = "unresolved_localized_tight_conflict"
        return True
    if not matching_tight:
        result["conflict_state"] = "unresolved_localized_without_tight_agreement"
        return True

    # Only a broader source crop physically containing/overlapping the
    # localized badge may disagree.  A second tight-like or disjoint crop is
    # a real source conflict and must not be resolved by insertion order.
    for candidate in eligible:
        if candidate in localized or candidate in matching_tight:
            continue
        if candidate.get("crop_family") == "result":
            continue
        if int(candidate.get("amount")) == amount:
            continue
        if candidate.get("crop_family") not in {"wide_gain", "expanded_gain"}:
            result["conflict_state"] = "unresolved_localized_source_conflict"
            return True
        if not any(
            source_geometry_overlaps(candidate, item, minimum_fraction=0.40)
            for item in localized
        ):
            result["conflict_state"] = "unresolved_localized_geometry_conflict"
            return True

    # The tight and localized crops belong to one result record in the normal
    # reader.  Geometry binds them even when legacy raw rows do not carry a
    # timestamp on each region.  Cross-frame distinctness remains the phase
    # resolver's responsibility.
    if not any(
        source_geometry_overlaps(local, tight_candidate, minimum_fraction=0.40)
        for local in localized for tight_candidate in matching_tight
    ):
        result["conflict_state"] = "unresolved_localized_geometry_disagreement"
        return True
    chosen = max(localized, key=lambda item: float(item.get("confidence", 0)))
    result.update(
        canonical_amount=amount,
        canonical_basis="source_crop_pixel_localized_training_badge_agreement",
        conflict_state="resolved_source_pixel_localized",
        canonical_candidate=chosen,
    )
    return True


def resolve_gain_candidates(candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Select a source crop amount while retaining unresolved conflicts.

    A broader crop may resolve a clipped one-digit prefix when its amount is a
    strict prefix extension and its box is physically broader.  A low-quality
    wider reading is retained as evidence but cannot displace a complete
    tight badge.  Non-prefix disagreements are left unresolved.  No balance,
    result total, timestamp, or expected value participates in this choice.
    """

    records = [deepcopy(dict(candidate)) for candidate in candidates]
    amounts = numeric_amounts(records)
    eligible = [candidate for candidate in records if _strong(candidate)]
    result: dict[str, Any] = {
        "canonical_amount": None,
        "canonical_basis": "unresolved_source_crop_evidence",
        "conflict_state": "unresolved_no_eligible_source_amount",
        "candidate_amounts": amounts,
        "candidates": records,
    }
    if not eligible:
        return result
    # A malformed competing amount crop cannot certify the other value by
    # disappearing from the comparison. Preserve the invalid source layout
    # as uncertainty, including when a valid tight crop is a clipped prefix.
    malformed = [candidate for candidate in records
                 if candidate.get('source_role') == 'amount_crop_candidate'
                 and CROP_FAMILY_RULES.get(candidate.get('crop_family'), {}).get('canonical')
                 and type(candidate.get('amount')) is int
                 and _box(candidate.get('box')) is None]
    if malformed:
        result['conflict_state'] = 'unresolved_invalid_crop_geometry'
        return result

    # ``expanded_gain.inner`` and ``expanded_gain.outer`` are two source
    # views requested for the same physical badge.  If both views return
    # different complete signed amounts, their disagreement is a source
    # conflict.  Do this before the generic broader-prefix rule: that rule
    # is useful for a native tight crop plus one corroborating broad crop, but
    # it must not silently choose one of two competing refinement views.
    refined = [
        candidate
        for candidate in eligible
        if candidate.get("source_refinement_schema") == _SOURCE_REFINEMENT_SCHEMA
        and candidate.get("source_refinement_role") in _SOURCE_REFINEMENT_ROLES
    ]
    refined_values = sorted({int(candidate["amount"]) for candidate in refined})
    if len(refined_values) > 1:
        result.update(
            conflict_state="unresolved_conflicting_source_refinement",
            source_refinement_conflicts=refined_values,
        )
        return result

    # Evaluate the supplemental source-pixel view before the generic prefix
    # resolver.  Otherwise a contaminated wide crop such as ``+200`` could
    # win the ordinary strict-prefix branch over a localized ``+2`` badge.
    if _resolve_source_pixel_localized(records, eligible, result):
        return result

    by_amount: dict[int, list[dict[str, Any]]] = {}
    for candidate in eligible:
        by_amount.setdefault(int(candidate["amount"]), []).append(candidate)

    scaled = [candidate for candidate in eligible
              if candidate["crop_family"] == "scaled_gain"]
    scaled_values = {int(candidate["amount"]) for candidate in scaled}
    scaled_complete = [value for value in scaled_values if len(scaled_values) > 1
                       and all(_strict_prefix(other, value) for other in scaled_values
                               if other != value)]
    if len(scaled_complete) == 1:
        amount = scaled_complete[0]
        chosen = max(
            (candidate for candidate in scaled if int(candidate["amount"]) == amount),
            key=lambda item: float(item.get("confidence", 0)),
        )
        result.update(
            canonical_amount=amount,
            canonical_basis="same_family_scaled_crop_prefix_consensus",
            conflict_state="resolved_same_family_prefix",
            canonical_candidate=chosen,
        )
        return result

    # Prefer a single amount supported by the best family/geometry.  Keep all
    # alternatives in ``candidates`` even when they are not canonical.
    if len(by_amount) == 1:
        amount, support = next(iter(by_amount.items()))
        chosen = max(
            support,
            key=lambda item: (
                CROP_FAMILY_RULES[item["crop_family"]]["rank"],
                float(item.get("confidence", 0)),
            ),
        )
        result.update(
            canonical_amount=amount,
            canonical_basis="source_crop_family_geometry",
            conflict_state="resolved_same_amount_across_source_crops",
            canonical_candidate=chosen,
        )
        return result

    # A wider, complete amount can explain a shorter tight prefix.  Require
    # both source geometry and the family quality floor; this is what keeps a
    # weak '+71' diagnostic from replacing a high-confidence '+7' badge while
    # allowing a readable '+12' wide crop to replace a clipped '+1'.
    promotions: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for long_amount, long_support in by_amount.items():
        for short_amount, short_support in by_amount.items():
            if not _strict_prefix(short_amount, long_amount):
                continue
            for long_candidate in long_support:
                for short_candidate in short_support:
                    if _broader(long_candidate, short_candidate):
                        promotions.append((long_candidate, short_candidate))
    if promotions:
        long_amounts = {int(long["amount"]) for long, _short in promotions}
        if len(long_amounts) == 1:
            long_candidate = max(
                (long for long, _short in promotions),
                key=lambda item: (
                    CROP_FAMILY_RULES[item["crop_family"]]["rank"],
                    float(item.get("confidence", 0)),
                ),
            )
            result.update(
                canonical_amount=int(long_candidate["amount"]),
                canonical_basis="source_crop_family_geometry_prefix_resolution",
                conflict_state="resolved_broader_prefix",
                canonical_candidate=long_candidate,
            )
            return result

    result["conflict_state"] = "unresolved_nonprefix_or_equal_geometry_conflict"
    return result


def resolve_gain_regions(
    regions: Mapping[str, Mapping[str, Any]], field: str,
) -> dict[str, Any]:
    """Collect and resolve one field in one OCR reading."""

    return resolve_gain_candidates(collect_gain_candidates(regions, field))
