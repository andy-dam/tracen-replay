"""Read the centered gameplay energy recovery popup.

Some result frames expose the visual ``+N``/``Energy`` popup while the lower
``Energy recovered by N.`` receipt is being covered by an animated effect.
The lower receipt remains the preferred ordinary grammar input.  This module
is a separate, source-bound observation channel for the centered popup; it
does not inspect balances, calculate a residual, or repair an occluded
receipt.

Only existing OCR lines are consumed here.  A caller can pass either the
raw sidecar mapping or its ``lines`` list.  The result carries both visual
lines and any source identity metadata present on the raw sidecar so a later
producer can validate the source before merging the observation.
"""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Iterable, Mapping


SCHEMA = "tracen-replay/energy-popup-v1"
MIN_CONFIDENCE = 97.0

# OCR boxes use full gameplay coordinates.  Keep the amount and label bands
# separate from the lower receipt band (y >= 770) and the persistent Energy
# status row (y ~= 120).  The bounds are layout geometry, not recording or
# event identifiers.
POPUP_AMOUNT_REGION = (300.0, 500.0, 900.0, 680.0)
POPUP_LABEL_REGION = (300.0, 600.0, 900.0, 760.0)

_AMOUNT_RE = re.compile(r"^\+\s*(\d{1,3})$")
_ENERGY_SCREENS = frozenset({
    "training_preview",
    "lesson_selection",
    "lesson_confirmation",
    "skill_selection",
    "skill_confirmation",
    "skill_receipt",
    "career_summary",
    "career_completion_hub",
    "career_finish_confirmation",
})


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split()).strip()
    return value or None


def _box(line: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(line, Mapping):
        return None
    value = line.get("box")
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = tuple(float(item) for item in value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(item) for item in box):
        return None
    left, top, right, bottom = box
    if not (0.0 <= left < right <= 960.0 and 0.0 <= top < bottom <= 1080.0):
        return None
    return box


def _confidence(line: Any) -> float | None:
    if not isinstance(line, Mapping):
        return None
    try:
        value = float(line.get("confidence", 0))
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _inside(line: Any, region: tuple[float, float, float, float]) -> bool:
    """Require the complete OCR box to fit inside one popup role band."""

    box = _box(line)
    confidence = _confidence(line)
    text = _text(line.get("text")) if isinstance(line, Mapping) else None
    if box is None or confidence is None or confidence < MIN_CONFIDENCE or text is None:
        return False
    left, top, right, bottom = box
    x1, y1, x2, y2 = region
    return x1 <= left and right <= x2 and y1 <= top and bottom <= y2


def _unique_lines(lines: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Remove exact duplicate OCR rows without collapsing distinct geometry."""

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[float, ...]]] = set()
    for line in lines:
        if not isinstance(line, Mapping):
            continue
        text = _text(line.get("text"))
        box = _box(line)
        if text is None or box is None:
            continue
        key = (text.casefold(), tuple(round(value, 3) for value in box))
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(line))
    return result


def _aligned(amount: Mapping[str, Any], label: Mapping[str, Any]) -> bool:
    """Check that the two rows belong to one centered popup card."""

    amount_box = _box(amount)
    label_box = _box(label)
    if amount_box is None or label_box is None:
        return False
    amount_left, amount_top, amount_right, amount_bottom = amount_box
    label_left, label_top, label_right, label_bottom = label_box
    amount_center = (amount_left + amount_right) / 2.0
    label_center = (label_left + label_right) / 2.0
    horizontal_overlap = min(amount_right, label_right) - max(amount_left, label_left)
    vertical_gap = label_top - amount_bottom
    # The source popup can have a small OCR-box overlap while it is fading
    # (frame 444), or a visible gap (frame 443).  It remains one popup only
    # when the label is below the amount, horizontally aligned, and close.
    return (
        label_top >= amount_top
        and label_top <= amount_bottom + 55.0
        and label_bottom > amount_bottom
        and abs(amount_center - label_center) <= 180.0
        and horizontal_overlap >= 20.0
        and vertical_gap >= -12.0
    )


def _source_metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Copy available source identifiers without treating them as OCR facts."""

    metadata: dict[str, Any] = {}
    if type(raw.get("source_timestamp_ms")) is int and raw["source_timestamp_ms"] >= 0:
        metadata["source_timestamp_ms"] = raw["source_timestamp_ms"]
    evidence = _text(raw.get("evidence"))
    if evidence is not None:
        metadata["evidence"] = evidence
    for key in (
        "source_sha256",
        "gameplay_sha256",
        "source_frame_sha256",
        "proof_sha256",
        "engine_fingerprint",
    ):
        value = _text(raw.get(key))
        if value is not None:
            metadata[key] = value
    return metadata


def read_energy_popup(
    lines: Iterable[Mapping[str, Any]] | Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return one source-backed energy recovery popup, or ``None``.

    The amount and the exact ``Energy`` label must be unique, high-confidence
    lines in their centered popup roles.  A persistent status-row label, a
    lower receipt, an unrelated ``+N`` line, a preview screen, and competing
    candidates cannot authorize this observation.
    """

    raw = lines if isinstance(lines, Mapping) else None
    if raw is not None:
        screen = _text(raw.get("screen"))
        if screen is not None and screen.casefold() in _ENERGY_SCREENS:
            return None
        lines = raw.get("lines", [])
    if not isinstance(lines, Iterable) or isinstance(lines, (str, bytes)):
        return None
    normalized = _unique_lines(line for line in lines if isinstance(line, Mapping))
    amount_candidates: list[tuple[int, dict[str, Any]]] = []
    for line in normalized:
        if not _inside(line, POPUP_AMOUNT_REGION):
            continue
        text = _text(line.get("text"))
        match = _AMOUNT_RE.fullmatch(text or "")
        if match is None:
            continue
        amount = int(match.group(1))
        if amount <= 0:
            continue
        amount_candidates.append((amount, line))
    labels = [
        line for line in normalized
        if _inside(line, POPUP_LABEL_REGION)
        and (_text(line.get("text")) or "").casefold() == "energy"
    ]
    if len(amount_candidates) != 1 or len(labels) != 1:
        return None
    amount, amount_line = amount_candidates[0]
    label_line = labels[0]
    if not _aligned(amount_line, label_line):
        return None

    source_proof: dict[str, Any] = {
        "basis": "same_frame_centered_energy_popup_geometry",
        "amount": deepcopy(amount_line),
        "label": deepcopy(label_line),
    }
    if raw is not None:
        metadata = _source_metadata(raw)
        if metadata:
            source_proof["source_identity"] = metadata
    result: dict[str, Any] = {
        "schema_version": SCHEMA,
        "kind": "energy_change",
        "amount": amount,
        "raw_text": f"{_text(amount_line.get('text'))} {_text(label_line.get('text'))}",
        "normalized_text": f"+{amount} Energy",
        "confidence": round(min(_confidence(amount_line), _confidence(label_line)), 4),
        "observation_basis": "visible_energy_recovery_popup",
        "source_proof": source_proof,
        "inferred_numeric_effects": False,
    }
    if raw is not None:
        metadata = _source_metadata(raw)
        for key in ("source_timestamp_ms", "evidence"):
            if key in metadata:
                result[key] = metadata[key]
    return result


def merge_energy_popup_effects(
    effects: Iterable[Mapping[str, Any]],
    popup: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Merge one popup into same-frame effects without duplicating a receipt.

    A complete lower receipt and its centered popup are two proofs of one
    energy change.  When their amounts agree, retain the receipt effect and
    attach the popup proof.  A popup-only frame becomes an effect.  Conflicting
    amounts are retained as separate observations for later reconciliation;
    this helper never chooses one by balance or by expected value.
    """

    result = [deepcopy(dict(effect)) for effect in effects
              if isinstance(effect, Mapping)]
    if not isinstance(popup, Mapping) or popup.get("kind") != "energy_change":
        return result
    amount = popup.get("amount")
    matches = [effect for effect in result
               if effect.get("kind") == "energy_change"
               and type(effect.get("amount")) is int
               and effect.get("amount") == amount]
    proof = popup.get("source_proof")
    if matches:
        for effect in matches:
            if not isinstance(proof, Mapping):
                continue
            existing = effect.get("energy_popup_proof")
            if existing is None:
                effect["energy_popup_proof"] = deepcopy(dict(proof))
            elif existing != proof:
                variants = effect.setdefault("energy_popup_proof_variants", [])
                if isinstance(variants, list) and proof not in variants:
                    variants.append(deepcopy(dict(proof)))
        return result
    result.append(deepcopy(dict(popup)))
    return result


# Keep a descriptive alias for callers that name the semantic channel rather
# than the visual layout.
read_energy_recovery_popup = read_energy_popup


__all__ = [
    "SCHEMA",
    "MIN_CONFIDENCE",
    "POPUP_AMOUNT_REGION",
    "POPUP_LABEL_REGION",
    "read_energy_popup",
    "read_energy_recovery_popup",
    "merge_energy_popup_effects",
]
