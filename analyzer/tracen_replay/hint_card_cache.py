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

# Row fingerprints are versioned independently from the cache schema.  The
# original cache omitted only the parser-owned ``facts`` projections listed
# below.  The current parser also projects a goal countdown into ``stats``;
# v2 keeps the immutable OCR lines in the fingerprint while removing only
# that derived projection and its proof decoration.
_ROW_HASH_VERSION_V1 = "source-row-v1"
_ROW_HASH_VERSION_V2 = "source-row-v2"
_ROW_HASH_VERSIONS = frozenset((_ROW_HASH_VERSION_V1, _ROW_HASH_VERSION_V2))
_ROW_HASH_VERSION_FIELD = "row_hash_version"
_COUNTDOWN_VALUE_KEY = "turns_remaining_to_goal"
_COUNTDOWN_PROVENANCE_KEY = "turns_remaining_provenance"
_MISSING = object()

# These fields are parser-owned observations that can be added or refined
# without changing the source row used to prove a hint receipt.  Keep them
# out of the persisted row fingerprint so an older prepared cache remains
# replayable after a parser adds a choice, preview, or status projection.
# The candidate checks below still validate the source-bound envelope, card,
# receipt, raw OCR, and source pixels directly before accepting a cache.
_PARSER_DERIVED_FACT_KEYS = frozenset(
    (
        "choice_observation",
        "preview_option",
        "preview_overlay_effects",
        "preview_overlay_evidence",
        "preview_overlay_proven",
        "preview_modifier_effects",
        "preview_modifier_proven",
        "rejected_counts",
        "status_badges",
    )
)

# ``cached_readings`` now exposes the source chain and reader identity on each
# parsed row.  Those fields are redundant with the immutable neural/source
# bindings and were absent from older cache fingerprints.  They are omitted
# from the semantic row hash only after the candidate span has been checked
# against the manifest, raw neural row, and decoded gameplay pixels below.
# ``source_frame_path`` is included because it is a manifest lookup result in
# the normalized validation view, not an independent observation.
_SOURCE_BOUND_ROW_KEYS = frozenset(
    (
        "engine_fingerprint",
        "model_sha256",
        "gameplay_sha256",
        "source_frame_id",
        "source_frame_sha256",
        "source_frame_path",
        "source_sha256",
    )
)

# A refreshed cache can carry the complete current row fingerprint while its
# identity proof is evaluated against the cursor-only receipt view.  The
# animated particle detector is a second source-backed obstruction channel;
# keeping it in the fingerprint means a later replay cannot silently accept a
# changed particle box or marker.
_SOURCE_ROW_VIEW_FIELD = "source_row_view"
_RECEIPT_OCCLUSION_CURSOR_VIEW = "receipt-occlusion-cursor-only-v1"

# A cursor-only refresh must retain a source-pixel witness for the card text.
# The witness is produced during explicit refresh (where OCR is allowed) and
# checked during replay without constructing an OCR reader.  It is kept in
# cache provenance rather than the returned candidate so the public candidate
# shape remains compatible with the original identity recovery API.
_CARD_IDENTITY_PROOFS_FIELD = "card_identity_proofs"
_CARD_IDENTITY_PROOF_BASIS = "source_bound_card_identity_crop_ocr"


def _normalize_neutral_receipt_occlusion_facts(
    facts: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove only neutral particle-detector decorations from a row hash.

    The receipt occlusion parser may add empty particle lists and a ``False``
    marker after an older hint cache was prepared.  Those decorations do not
    change the OCR line, cursor overlay, or source pixel proof.  Preserve any
    nonempty list, ``True`` marker, malformed value, or unrelated field so a
    real source change still invalidates the cache.
    """
    normalized = dict(facts)
    lines = facts.get("occluded_receipt_lines")
    if isinstance(lines, list):
        normalized_lines = []
        lines_changed = False
        for line in lines:
            if not isinstance(line, Mapping):
                normalized_lines.append(line)
                continue
            normalized_line = dict(line)
            animated_boxes = normalized_line.get("animated_overlay_boxes")
            if type(animated_boxes) is list and not animated_boxes:
                normalized_line.pop("animated_overlay_boxes", None)
                lines_changed = True
            if normalized_line.get("animated_overlay_occluded") is False:
                normalized_line.pop("animated_overlay_occluded", None)
                lines_changed = True
            normalized_lines.append(normalized_line)
        if lines_changed:
            normalized["occluded_receipt_lines"] = normalized_lines

    overlay = facts.get("receipt_overlay_evidence")
    if isinstance(overlay, Mapping):
        normalized_overlay = dict(overlay)
        animated_boxes = normalized_overlay.get("animated_overlay_boxes")
        if type(animated_boxes) is list and not animated_boxes:
            normalized_overlay.pop("animated_overlay_boxes", None)
            normalized["receipt_overlay_evidence"] = normalized_overlay
    return normalized


def _receipt_occlusion_cursor_view(
    readings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]] | None:
    """Make a private cursor-only view for refreshed identity validation.

    ``receipt_occlusion.annotate`` retains particle boxes alongside the
    cursor boxes.  Hint-card identity proofs use the cursor boundary, while
    particle boxes remain part of the real current rows and their hashes.  A
    refreshed cache therefore validates its old identity structure against
    this derived view and validates the full current row hash separately.
    Malformed particle metadata is never removed or normalized into a valid
    view; it rejects the refresh instead.
    """
    try:
        normalized = deepcopy(list(readings))
    except (TypeError, ValueError):
        return None
    for row in normalized:
        if not isinstance(row, dict):
            return None
        facts = row.get("facts")
        if not isinstance(facts, dict):
            continue
        lines = facts.get("occluded_receipt_lines")
        if isinstance(lines, list):
            normalized_lines = []
            for line in lines:
                if not isinstance(line, dict):
                    normalized_lines.append(line)
                    continue
                normalized_line = dict(line)
                boxes_present = "animated_overlay_boxes" in normalized_line
                marker_present = "animated_overlay_occluded" in normalized_line
                if not boxes_present and not marker_present:
                    normalized_lines.append(normalized_line)
                    continue
                animated_boxes = normalized_line.get("animated_overlay_boxes", [])
                if type(animated_boxes) is not list:
                    return None
                parsed_animated = []
                for box in animated_boxes:
                    parsed = _box(box)
                    if parsed is None:
                        return None
                    parsed_animated.append(parsed)
                marker = normalized_line.get("animated_overlay_occluded", _MISSING)
                if marker is not _MISSING:
                    if type(marker) is not bool or marker != bool(parsed_animated):
                        return None
                overlay_boxes = normalized_line.get("overlay_boxes")
                if overlay_boxes is not None:
                    if not isinstance(overlay_boxes, list):
                        return None
                    parsed_overlay_boxes = []
                    for box in overlay_boxes:
                        parsed = _box(box)
                        if parsed is None:
                            return None
                        parsed_overlay_boxes.append((box, parsed))
                    if parsed_animated:
                        normalized_line["overlay_boxes"] = [
                            box
                            for box, parsed in parsed_overlay_boxes
                            if parsed not in parsed_animated
                        ]
                elif parsed_animated:
                    # The annotation claims a particle obstruction but does
                    # not retain the combined overlay list needed to derive
                    # the cursor-only boundary.  Keep the source row stale.
                    return None
                normalized_line.pop("animated_overlay_boxes", None)
                normalized_line.pop("animated_overlay_occluded", None)
                normalized_lines.append(normalized_line)
            facts["occluded_receipt_lines"] = normalized_lines

        overlay = facts.get("receipt_overlay_evidence")
        if isinstance(overlay, dict) and "animated_overlay_boxes" in overlay:
            animated_boxes = overlay.get("animated_overlay_boxes")
            if type(animated_boxes) is not list:
                return None
            if any(_box(box) is None for box in animated_boxes):
                return None
            normalized_overlay = dict(overlay)
            normalized_overlay.pop("animated_overlay_boxes", None)
            facts["receipt_overlay_evidence"] = normalized_overlay
    return normalized


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


def _candidate_observation_rows(
    candidate: Mapping[str, Any],
    readings: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]] | None:
    """Select exactly the source rows witnessed by one cache candidate.

    A replay bundle can contain the base capture alongside rows from dense
    inspection or recovery namespaces.  Those rows are legitimate members
    of the bundle, but their frame ids are not entries in the base capture
    manifest used by a hint-card cache.  Candidate provenance already names
    the exact ``(timestamp, evidence)`` pair for every row used to establish
    the card; source binding must therefore be scoped to those pairs after
    validating the complete bundle's row-key integrity.

    This is deliberately an exact lookup.  Falling back to a timestamp range
    would allow a same-time inspection row to replace the witnessed base
    frame, while choosing an arbitrary row would weaken the cache's source
    proof.  Missing, duplicate, or malformed observation keys fail closed.
    """
    if not isinstance(candidate, Mapping):
        return None
    observations = candidate.get("observations")
    source_times = candidate.get("source_timestamps_ms")
    if not isinstance(observations, list) or not isinstance(source_times, list):
        return None
    if len(observations) != len(source_times) or not observations:
        return None

    valid = _validated_rows(readings)
    if valid is None:
        return None
    by_key: dict[tuple[int, str], Mapping[str, Any]] = {}
    for row in valid:
        key = _row_key(row)
        if key is None or key in by_key:
            return None
        by_key[key] = row

    expected_keys: list[tuple[int, str]] = []
    for timestamp, observation in zip(source_times, observations):
        if type(timestamp) is not int or timestamp < 0:
            return None
        if not isinstance(observation, Mapping):
            return None
        if observation.get("timestamp_ms") != timestamp:
            return None
        evidence = observation.get("evidence")
        if not isinstance(evidence, str) or not evidence:
            return None
        key = _row_key(
            {
                "source_timestamp_ms": timestamp,
                "evidence": evidence,
            }
        )
        if key is None or key in expected_keys:
            return None
        expected_keys.append(key)

    selected = [by_key.get(key) for key in expected_keys]
    if any(row is None for row in selected):
        return None
    return [row for row in selected if row is not None]


def _safe_relative_manifest_path(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def _source_bound_rows(
    readings: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    source_sha256: str,
    source_manifest: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[Mapping[str, Any]] | None:
    """Return a private row view with a manifest-derived source-frame path.

    Newer parsed rows expose source/model identity at the row envelope while
    older cache hashes do not.  The path is derived only from the row's
    source evidence and the validated capture manifest; it is never guessed
    from a basename or an unrelated sidecar.  All explicit identity fields
    are retained so the source metadata validator can check them before a
    semantic row hash ignores the redundant envelope.
    """
    valid = _validated_rows(readings)
    if valid is None:
        return None

    frame_keys = (
        "source_frame_id",
        "source_frame_sha256",
        "source_frame_path",
    )
    needs_manifest = False
    for row in valid:
        row_source = row.get("source_sha256")
        if row_source is not None and row_source != source_sha256:
            return None
        engine = row.get("engine_fingerprint")
        if engine is not None and not isinstance(engine, str):
            return None
        models = row.get("model_sha256")
        if models is not None and not isinstance(models, Mapping):
            return None
        gameplay = row.get("gameplay_sha256")
        if gameplay is not None and (
            not isinstance(gameplay, str) or _SHA256_RE.fullmatch(gameplay) is None
        ):
            return None
        frame_id = row.get("source_frame_id")
        if frame_id is not None and not isinstance(frame_id, str):
            return None
        frame_sha = row.get("source_frame_sha256")
        if frame_sha is not None and (
            not isinstance(frame_sha, str) or _SHA256_RE.fullmatch(frame_sha) is None
        ):
            return None
        frame_path = row.get("source_frame_path")
        if frame_path is not None and not isinstance(frame_path, str):
            return None
        if any(row.get(key) is not None for key in frame_keys):
            needs_manifest = True

    if not needs_manifest:
        return valid
    if source_manifest is None:
        try:
            from .hint_card_identity import _load_source_manifest

            source_manifest = _load_source_manifest(
                root,
                source_sha256=source_sha256,
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError):
            return None
    if source_manifest is None:
        return None

    try:
        from .hint_card_identity import _frame_id
    except (ImportError, AttributeError, TypeError, ValueError):
        return None

    result: list[Mapping[str, Any]] = []
    for row in valid:
        if not any(row.get(key) is not None for key in frame_keys):
            result.append(row)
            continue
        evidence = row.get("evidence")
        frame_id = _frame_id(evidence) if isinstance(evidence, str) else None
        frame = source_manifest.get(frame_id) if frame_id is not None else None
        if not isinstance(frame, Mapping):
            return None
        if frame.get("source_timestamp_ms") != row.get("source_timestamp_ms"):
            return None
        expected_path = frame.get("evidence")
        if not _safe_relative_manifest_path(expected_path):
            return None
        explicit_id = row.get("source_frame_id")
        if explicit_id is not None and explicit_id != frame_id:
            return None
        explicit_path = row.get("source_frame_path")
        if explicit_path is not None and explicit_path != expected_path:
            return None
        if explicit_path is None and (
            row.get("source_frame_id") is not None
            or row.get("source_frame_sha256") is not None
        ):
            # Do not mutate the caller's parsed row.  The derived field is
            # only a private aid for the existing source-chain validator.
            normalized = dict(row)
            normalized["source_frame_path"] = expected_path
            result.append(normalized)
        else:
            result.append(row)
    return result


def _source_metadata_matches(
    row: Mapping[str, Any],
    loaded: Mapping[str, Any],
    *,
    source_sha256: str,
) -> bool:
    """Validate redundant row-envelope identity against loaded source data."""
    binding = loaded.get("binding")
    raw = binding.get("raw") if isinstance(binding, Mapping) else None
    if not isinstance(binding, Mapping) or not isinstance(raw, Mapping):
        return False

    checks = (
        ("source_sha256", source_sha256),
        ("source_frame_id", binding.get("capture_frame_id")),
        ("source_frame_sha256", binding.get("source_frame_sha256")),
        ("source_frame_path", binding.get("source_frame_path")),
        ("gameplay_sha256", binding.get("gameplay_sha256")),
        ("engine_fingerprint", raw.get("engine_fingerprint")),
        ("model_sha256", raw.get("model_sha256")),
    )
    for field, expected in checks:
        supplied = row.get(field)
        if supplied is not None and supplied != expected:
            return False
    return True


def _source_metadata_span_valid(
    rows: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    source_sha256: str,
    source_manifest: Mapping[str, Mapping[str, Any]] | None = None,
) -> bool:
    """Check every supplied row identity before hashing a candidate span."""
    valid = _validated_rows(rows)
    if valid is None:
        return False
    try:
        from .hint_card_identity import _load_image, _load_source_manifest
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return False
    if source_manifest is None:
        source_manifest = _load_source_manifest(root, source_sha256=source_sha256)
    if source_manifest is None:
        return False
    image_cache: dict[str, dict[str, Any]] = {}
    for row in valid:
        loaded = _load_image(
            root,
            row,
            image_cache,
            source_sha256=source_sha256,
            source_manifest=source_manifest,
        )
        if loaded is None or not _source_metadata_matches(
            row,
            loaded,
            source_sha256=source_sha256,
        ):
            return False
    return True


def _hash_row(
    row: Mapping[str, Any],
    *,
    version: str,
    legacy_countdown: str | None = None,
) -> dict[str, Any] | None:
    if version not in _ROW_HASH_VERSIONS:
        return None
    # Hash the source-backed row while allowing only the declared parser
    # projections to evolve.  The copy is deliberately local: replay must
    # return the current readings unchanged, including newer observations.
    hash_row = {
        name: value
        for name, value in row.items()
        if name not in _SOURCE_BOUND_ROW_KEYS
    }
    facts = row.get("facts")
    if isinstance(facts, Mapping):
        source_facts = {
            name: value
            for name, value in facts.items()
            if name not in _PARSER_DERIVED_FACT_KEYS
        }
        source_facts = _normalize_neutral_receipt_occlusion_facts(source_facts)
        if source_facts:
            hash_row["facts"] = source_facts
        else:
            hash_row.pop("facts", None)
    if version == _ROW_HASH_VERSION_V2:
        stats = row.get("stats")
        if isinstance(stats, Mapping):
            source_stats = {
                name: value
                for name, value in stats.items()
                if name not in (_COUNTDOWN_VALUE_KEY, _COUNTDOWN_PROVENANCE_KEY)
            }
            # Preserve an explicitly present empty stats object.  This keeps
            # the normalization narrow instead of conflating absent fields
            # with an unrelated parser shape.
            hash_row["stats"] = source_stats
    elif legacy_countdown in ("none", "absent"):
        stats = row.get("stats")
        if isinstance(stats, Mapping):
            source_stats = dict(stats)
            source_stats.pop(_COUNTDOWN_PROVENANCE_KEY, None)
            if legacy_countdown == "none":
                source_stats[_COUNTDOWN_VALUE_KEY] = None
            else:
                source_stats.pop(_COUNTDOWN_VALUE_KEY, None)
            hash_row["stats"] = source_stats
    return hash_row


def _span_hashes(
    rows: Sequence[Mapping[str, Any]],
    *,
    version: str = _ROW_HASH_VERSION_V1,
    legacy_countdown: str | None = None,
) -> list[dict[str, Any]] | None:
    result = []
    for row in rows:
        key = _row_key(row)
        hash_row = _hash_row(row, version=version, legacy_countdown=legacy_countdown)
        if hash_row is None:
            return None
        row_hash = _canonical_hash(hash_row)
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


def _countdown_projection_matches(
    row: Mapping[str, Any],
    *,
    require_projection: bool,
) -> bool:
    """Validate a parser countdown against the same row's immutable OCR.

    A missing or ``None`` projection is tolerated for legacy replay.  A
    non-null current value must agree with ``read_goal_turns``; v2 additionally
    requires the complete proof mapping emitted by that reader.  No integer is
    supplied from the cache hash or inferred from a neighboring row.
    """
    stats = row.get("stats")
    if stats is None:
        stats = {}
    elif not isinstance(stats, Mapping):
        return False
    value = stats.get(_COUNTDOWN_VALUE_KEY, _MISSING)
    provenance = stats.get(_COUNTDOWN_PROVENANCE_KEY, _MISSING)
    if provenance is not _MISSING and provenance is not None and not isinstance(provenance, Mapping):
        return False

    ocr = row.get("ocr")
    lines = ocr.get("neural") if isinstance(ocr, Mapping) else None
    if not isinstance(lines, list):
        return value in (_MISSING, None) and provenance in (_MISSING, None)
    try:
        from .stat_state_details import read_goal_turns

        expected, expected_provenance = read_goal_turns(lines)
    except (ImportError, TypeError, ValueError):
        return False

    if expected is None:
        # A value without a same-frame goal proof is not source-supported.
        return value in (_MISSING, None) and provenance in (_MISSING, None)
    if require_projection:
        return (
            type(value) is int
            and value == expected
            and isinstance(provenance, Mapping)
            and provenance == expected_provenance
        )
    if value in (_MISSING, None):
        # This is the only legacy migration allowance: the older parser did
        # not persist a readable countdown at all.
        return provenance in (_MISSING, None)
    if type(value) is not int or value != expected:
        return False
    # A legacy non-null value is acceptable only when the immutable OCR proves
    # the same number.  A mismatching or forged proof still fails closed.
    return provenance in (_MISSING, None) or provenance == expected_provenance


def _countdown_rows_match(
    rows: Sequence[Mapping[str, Any]],
    *,
    require_projection: bool,
) -> bool:
    return all(
        _countdown_projection_matches(row, require_projection=require_projection)
        for row in rows
    )


def _span_hashes_match(
    stored: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    version: str | None,
) -> bool:
    """Match an exact version, with one bounded legacy migration.

    Caches written before ``row_hash_version`` existed are v1.  Their opaque
    hash cannot be decomposed, so migration tries only the two representations
    the old parser could have emitted: countdown absent or countdown ``None``.
    It never substitutes a guessed integer or searches arbitrary projections.
    """
    if not isinstance(stored, list):
        return False
    if version == _ROW_HASH_VERSION_V2:
        return _hash_list_equal(stored, _span_hashes(rows, version=version))
    if version == _ROW_HASH_VERSION_V1:
        return _hash_list_equal(stored, _span_hashes(rows, version=version))
    if version is not None:
        return False
    if _hash_list_equal(stored, _span_hashes(rows, version=_ROW_HASH_VERSION_V1)):
        return True
    for representation in ("absent", "none"):
        if _hash_list_equal(
            stored,
            _span_hashes(
                rows,
                version=_ROW_HASH_VERSION_V1,
                legacy_countdown=representation,
            ),
        ):
            return True
    return False


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


def _card_identity_crop(image: Any, box: Any) -> Any | None:
    """Return the exact gameplay crop used for a card identity witness."""
    card_box = _box(box)
    if card_box is None:
        return None
    try:
        left, top, right, bottom = (int(round(value)) for value in card_box)
        crop = image.crop((left - 148, top, right - 148, bottom))
        if crop.width <= 0 or crop.height <= 0:
            return None
        return crop.convert("RGB")
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _identity_text_equal(left: Any, right: Any) -> bool:
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and re.sub(r"\s+", " ", left).strip() == re.sub(r"\s+", " ", right).strip()
    )


def _source_card_identity_proofs(
    candidate: Mapping[str, Any],
    readings: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    source_sha256: str,
) -> list[dict[str, Any]] | None:
    """Read card names from their bound gameplay pixels during preparation.

    This is intentionally separate from ``load``.  Refresh is an explicit
    source-recovery operation and may run OCR; replay only checks the emitted
    text witness and its exact RGB crop hash.  The crop is always taken from
    the gameplay pane loaded through the existing capture/source chain.
    """
    if not isinstance(candidate, Mapping):
        return None
    observation_kind = candidate.get("observation_kind")
    if (
        candidate.get("source_sha256") != source_sha256
        or not isinstance(candidate.get("name"), str)
        or not candidate["name"].strip()
        or not isinstance(candidate.get("observations"), list)
    ):
        return None
    observations = candidate["observations"]
    source_times = candidate.get("source_timestamps_ms")
    if (
        not isinstance(source_times, list)
        or len(source_times) != len(observations)
        or not source_times
        or any(type(value) is not int or value < 0 for value in source_times)
    ):
        return None
    candidate_rows = _candidate_observation_rows(candidate, readings)
    if candidate_rows is None:
        return None
    span = _source_bound_rows(
        candidate_rows,
        root,
        source_sha256=source_sha256,
    )
    if span is None or len(span) != len(observations):
        return None
    try:
        from .hint_card_identity import _load_image, _load_source_manifest
        if observation_kind == "wrapped_hint_receipt":
            from .hint_card_identity import _wrapped_static_row as static_row
        else:
            from .hint_card_identity import _static_row as static_row
        from .vision import NeuralReader
        import numpy as np

        source_manifest = _load_source_manifest(root, source_sha256=source_sha256)
        reader = NeuralReader()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        return None
    if source_manifest is None:
        return None
    model_fingerprint = getattr(reader, "fingerprint", None)
    if not isinstance(model_fingerprint, str) or _SHA256_RE.fullmatch(model_fingerprint) is None:
        return None

    image_cache: dict[str, dict[str, Any]] = {}
    proofs: list[dict[str, Any]] = []
    for observation, row in zip(observations, span):
        if not isinstance(observation, Mapping):
            return None
        item = static_row(row)
        if item is None:
            return None
        if (
            observation.get("timestamp_ms") != item["timestamp"]
            or observation.get("evidence") != item["evidence"]
            or not _identity_text_equal(item["card"].get("text"), candidate["name"])
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
        crop = _card_identity_crop(loaded["image"], item["card"].get("box"))
        if crop is None:
            return None
        try:
            result = reader.engine.text_rec(
                reader.TextRecInput(img=[np.asarray(crop)[:, :, ::-1]])
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return None
        recognized: list[tuple[str, float]] = []
        for text_value, score in zip(
            getattr(result, "txts", ()) or (),
            getattr(result, "scores", ()) or (),
        ):
            if not isinstance(text_value, str) or not _finite_number(score):
                continue
            confidence = float(score) * 100.0
            if confidence < 95.0 or confidence > 100.0:
                continue
            recognized.append((text_value.strip(), confidence))
        if len(recognized) != 1 or not _identity_text_equal(
            recognized[0][0], candidate["name"]
        ):
            return None
        proofs.append(
            {
                "timestamp_ms": item["timestamp"],
                "evidence": item["evidence"],
                "card_text": candidate["name"],
                "recognized_text": recognized[0][0],
                "confidence": round(recognized[0][1], 4),
                "crop_box": [float(value) for value in item["card"]["box"]],
                "pixel_rgb_sha256": hashlib.sha256(crop.tobytes()).hexdigest(),
                "model_fingerprint": model_fingerprint,
                "basis": _CARD_IDENTITY_PROOF_BASIS,
            }
        )
    return proofs


def _card_identity_proof_matches(
    proof: Mapping[str, Any],
    item: Mapping[str, Any],
    candidate: Mapping[str, Any],
    image: Any,
) -> bool:
    """Check one persisted card-text witness without running OCR."""
    card = item.get("card")
    if (
        not isinstance(proof, Mapping)
        or not isinstance(card, Mapping)
        or proof.get("basis") != _CARD_IDENTITY_PROOF_BASIS
        or proof.get("timestamp_ms") != item.get("timestamp")
        or proof.get("evidence") != item.get("evidence")
        or not _identity_text_equal(proof.get("card_text"), candidate.get("name"))
        or not _identity_text_equal(proof.get("recognized_text"), candidate.get("name"))
        or not _identity_text_equal(card.get("text"), candidate.get("name"))
        or not _finite_number(proof.get("confidence"))
        or not 95.0 <= float(proof["confidence"]) <= 100.0
        or not isinstance(proof.get("crop_box"), (list, tuple))
        or not _boxes_equal(proof.get("crop_box"), card.get("box"))
        or not isinstance(proof.get("pixel_rgb_sha256"), str)
        or _SHA256_RE.fullmatch(proof["pixel_rgb_sha256"]) is None
        or not isinstance(proof.get("model_fingerprint"), str)
        or _SHA256_RE.fullmatch(proof["model_fingerprint"]) is None
    ):
        return False
    crop = _card_identity_crop(image, card.get("box"))
    if crop is None:
        return False
    return hashlib.sha256(crop.tobytes()).hexdigest() == proof["pixel_rgb_sha256"]


def _row_has_non_neutral_particle_metadata(row: Mapping[str, Any]) -> bool:
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        return False
    lines = facts.get("occluded_receipt_lines")
    if isinstance(lines, list):
        for line in lines:
            if not isinstance(line, Mapping):
                continue
            if (
                isinstance(line.get("animated_overlay_boxes"), list)
                and bool(line["animated_overlay_boxes"])
            ) or line.get("animated_overlay_occluded") is True:
                return True
    overlay = facts.get("receipt_overlay_evidence")
    return (
        isinstance(overlay, Mapping)
        and isinstance(overlay.get("animated_overlay_boxes"), list)
        and bool(overlay["animated_overlay_boxes"])
    )


def _candidate_needs_card_identity_proof(
    candidate: Mapping[str, Any],
    readings: Sequence[Mapping[str, Any]],
    *,
    source_row_view: str | None = None,
) -> bool:
    if not isinstance(candidate, Mapping):
        return False
    if source_row_view == _RECEIPT_OCCLUSION_CURSOR_VIEW:
        return True
    source_times = candidate.get("source_timestamps_ms")
    if not isinstance(source_times, list):
        return False
    timestamps = set(source_times)
    return any(
        isinstance(row, Mapping)
        and row.get("source_timestamp_ms") in timestamps
        and _row_has_non_neutral_particle_metadata(row)
        for row in readings
    )


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
    stored_card_identity_proofs: Any = None,
    source_row_view: str | None = None,
    row_hash_version: str | None = None,
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
    cache_provenance = candidate.get("cache_provenance")
    if isinstance(cache_provenance, Mapping):
        if source_row_view is None:
            source_row_view = cache_provenance.get(_SOURCE_ROW_VIEW_FIELD)
        if stored_card_identity_proofs is None:
            stored_card_identity_proofs = cache_provenance.get(
                _CARD_IDENTITY_PROOFS_FIELD
            )
    if source_row_view not in (None, _RECEIPT_OCCLUSION_CURSOR_VIEW):
        return None
    start_ms, end_ms = source_times[0], source_times[-1]
    candidate_rows = _candidate_observation_rows(candidate, readings)
    if candidate_rows is None:
        return None
    span = _source_bound_rows(
        candidate_rows,
        root,
        source_sha256=source_sha256,
        source_manifest=source_manifest,
    )
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
    if not _countdown_rows_match(
        span,
        require_projection=row_hash_version == _ROW_HASH_VERSION_V2,
    ):
        return None
    if not _source_metadata_span_valid(
        span,
        root,
        source_sha256=source_sha256,
        source_manifest=source_manifest,
    ):
        return None
    hash_version = row_hash_version or _ROW_HASH_VERSION_V1
    current_span_hashes = _span_hashes(span, version=hash_version)
    if current_span_hashes is None:
        return None
    if stored_span_hashes is not None and not _span_hashes_match(
        stored_span_hashes,
        span,
        version=row_hash_version,
    ):
        return None
    requires_card_identity_proof = _candidate_needs_card_identity_proof(
        candidate,
        span,
        source_row_view=source_row_view,
    )
    if requires_card_identity_proof and not isinstance(
        stored_card_identity_proofs, list
    ):
        return None
    if stored_card_identity_proofs is not None and (
        not isinstance(stored_card_identity_proofs, list)
        or len(stored_card_identity_proofs) != len(observations)
    ):
        return None

    image_cache: dict[str, dict[str, Any]] = {}
    observation_provenance: list[dict[str, Any]] = []
    observed_names: list[str] = []
    observed_bindings: list[Mapping[str, Any]] = []
    for index, (observation, item) in enumerate(zip(observations, span_static)):
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
        if stored_card_identity_proofs is not None and not _card_identity_proof_matches(
            stored_card_identity_proofs[index],
            item,
            candidate,
            loaded["image"],
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
    result = {
        "span_hashes": current_span_hashes,
        "observation_provenance": observation_provenance,
    }
    if stored_card_identity_proofs is not None:
        result[_CARD_IDENTITY_PROOFS_FIELD] = deepcopy(stored_card_identity_proofs)
    return result


def _validate_single_line_candidate(
    candidate: Mapping[str, Any],
    readings: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    source_sha256: str,
    capture_manifest_sha256: str,
    stored_span_hashes: Any = None,
    stored_observation_provenance: Any = None,
    stored_card_identity_proofs: Any = None,
    source_row_view: str | None = None,
    row_hash_version: str | None = None,
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
    cache_provenance = candidate.get("cache_provenance")
    if isinstance(cache_provenance, Mapping):
        if source_row_view is None:
            source_row_view = cache_provenance.get(_SOURCE_ROW_VIEW_FIELD)
        if stored_card_identity_proofs is None:
            stored_card_identity_proofs = cache_provenance.get(
                _CARD_IDENTITY_PROOFS_FIELD
            )
    if source_row_view not in (None, _RECEIPT_OCCLUSION_CURSOR_VIEW):
        return None
    start_ms, end_ms = source_times[0], source_times[-1]
    candidate_rows = _candidate_observation_rows(candidate, readings)
    if candidate_rows is None:
        return None
    span = _source_bound_rows(
        candidate_rows,
        root,
        source_sha256=source_sha256,
        source_manifest=source_manifest,
    )
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
    if not _countdown_rows_match(
        span,
        require_projection=row_hash_version == _ROW_HASH_VERSION_V2,
    ):
        return None
    if not _source_metadata_span_valid(
        span,
        root,
        source_sha256=source_sha256,
        source_manifest=source_manifest,
    ):
        return None
    hash_version = row_hash_version or _ROW_HASH_VERSION_V1
    current_span_hashes = _span_hashes(span, version=hash_version)
    if current_span_hashes is None:
        return None
    if stored_span_hashes is not None and not _span_hashes_match(
        stored_span_hashes,
        span,
        version=row_hash_version,
    ):
        return None
    requires_card_identity_proof = _candidate_needs_card_identity_proof(
        candidate,
        span,
        source_row_view=source_row_view,
    )
    if requires_card_identity_proof and not isinstance(
        stored_card_identity_proofs, list
    ):
        return None
    if stored_card_identity_proofs is not None and (
        not isinstance(stored_card_identity_proofs, list)
        or len(stored_card_identity_proofs) != len(observations)
    ):
        return None

    image_cache: dict[str, dict[str, Any]] = {}
    observation_provenance: list[dict[str, Any]] = []
    observed_names: list[str] = []
    observed_bindings: list[Mapping[str, Any]] = []
    for index, (observation, item) in enumerate(zip(observations, span_static)):
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
        if stored_card_identity_proofs is not None and not _card_identity_proof_matches(
            stored_card_identity_proofs[index],
            item,
            candidate,
            loaded["image"],
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
    result = {
        "span_hashes": current_span_hashes,
        "observation_provenance": observation_provenance,
    }
    if stored_card_identity_proofs is not None:
        result[_CARD_IDENTITY_PROOFS_FIELD] = deepcopy(stored_card_identity_proofs)
    return result


def _validate_candidate(
    candidate: Mapping[str, Any],
    readings: Sequence[Mapping[str, Any]],
    root: Path,
    *,
    source_sha256: str,
    capture_manifest_sha256: str,
    stored_span_hashes: Any = None,
    stored_observation_provenance: Any = None,
    stored_card_identity_proofs: Any = None,
    source_row_view: str | None = None,
    row_hash_version: str | None = None,
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
            stored_card_identity_proofs=stored_card_identity_proofs,
            source_row_view=source_row_view,
            row_hash_version=row_hash_version,
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
            stored_card_identity_proofs=stored_card_identity_proofs,
            source_row_view=source_row_view,
            row_hash_version=row_hash_version,
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
    cache_provenance = candidate.get("cache_provenance")
    if isinstance(cache_provenance, Mapping):
        if source_row_view is None:
            source_row_view = cache_provenance.get(_SOURCE_ROW_VIEW_FIELD)
        if stored_card_identity_proofs is None:
            stored_card_identity_proofs = cache_provenance.get(
                _CARD_IDENTITY_PROOFS_FIELD
            )
    if source_row_view not in (None, _RECEIPT_OCCLUSION_CURSOR_VIEW):
        return None
    candidate_rows = _candidate_observation_rows(candidate, readings)
    if candidate_rows is None:
        return None
    span = _source_bound_rows(
        candidate_rows,
        root,
        source_sha256=source_sha256,
        source_manifest=source_manifest,
    )
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

    if not _countdown_rows_match(
        span,
        require_projection=row_hash_version == _ROW_HASH_VERSION_V2,
    ):
        return None
    if not _source_metadata_span_valid(
        span,
        root,
        source_sha256=source_sha256,
        source_manifest=source_manifest,
    ):
        return None
    hash_version = row_hash_version or _ROW_HASH_VERSION_V1
    current_span_hashes = _span_hashes(span, version=hash_version)
    if current_span_hashes is None:
        return None
    if stored_span_hashes is not None and not _span_hashes_match(
        stored_span_hashes,
        span,
        version=row_hash_version,
    ):
        return None
    requires_card_identity_proof = _candidate_needs_card_identity_proof(
        candidate,
        span,
        source_row_view=source_row_view,
    )
    if requires_card_identity_proof and not isinstance(
        stored_card_identity_proofs, list
    ):
        return None
    if stored_card_identity_proofs is not None and (
        not isinstance(stored_card_identity_proofs, list)
        or len(stored_card_identity_proofs) != len(observations)
    ):
        return None

    image_cache: dict[str, dict[str, Any]] = {}
    observation_provenance: list[dict[str, Any]] = []
    observed_names: list[str] = []
    observed_suffixes: list[str] = []
    observed_prefixes = []
    for index, (observation, item) in enumerate(zip(observations, span_static)):
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
        if stored_card_identity_proofs is not None and not _card_identity_proof_matches(
            stored_card_identity_proofs[index],
            item,
            candidate,
            loaded["image"],
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
    result = {
        "span_hashes": current_span_hashes,
        "observation_provenance": observation_provenance,
    }
    if stored_card_identity_proofs is not None:
        result[_CARD_IDENTITY_PROOFS_FIELD] = deepcopy(stored_card_identity_proofs)
    return result


def _candidate_cache_entry(
    candidate: Mapping[str, Any],
    validation: Mapping[str, Any],
    *,
    source_sha256: str,
    capture_manifest_sha256: str,
    source_row_view: str | None = None,
) -> dict[str, Any]:
    entry = deepcopy(dict(candidate))
    cache_provenance = {
        "schema": CACHE_SCHEMA,
        "source_sha256": source_sha256,
        "capture_manifest_sha256": capture_manifest_sha256,
        _ROW_HASH_VERSION_FIELD: _ROW_HASH_VERSION_V2,
        "span_rows": deepcopy(validation["span_hashes"]),
        "observations": deepcopy(validation["observation_provenance"]),
    }
    if source_row_view is not None:
        cache_provenance[_SOURCE_ROW_VIEW_FIELD] = source_row_view
    card_identity_proofs = validation.get(_CARD_IDENTITY_PROOFS_FIELD)
    if card_identity_proofs is not None:
        if not isinstance(card_identity_proofs, list):
            raise ValueError("Hint-card cache identity proof is malformed.")
        cache_provenance[_CARD_IDENTITY_PROOFS_FIELD] = deepcopy(
            card_identity_proofs
        )
    entry["cache_provenance"] = cache_provenance
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
    # Preserve global row-key validation while allowing a report to contain
    # source-bound inspection namespaces alongside the base capture.  Each
    # candidate validator scopes manifest binding to its exact observations.
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
        candidate_rows = _candidate_observation_rows(candidate, valid_rows)
        if candidate_rows is None:
            return None
        card_identity_proofs = None
        if _candidate_needs_card_identity_proof(candidate, candidate_rows):
            card_identity_proofs = _source_card_identity_proofs(
                candidate,
                candidate_rows,
                root,
                source_sha256=source_sha256,
            )
            if card_identity_proofs is None:
                return None
        validation = _validate_candidate(
            candidate,
            valid_rows,
            root,
            source_sha256=source_sha256,
            capture_manifest_sha256=capture_manifest_sha256,
            stored_card_identity_proofs=card_identity_proofs,
            row_hash_version=_ROW_HASH_VERSION_V2,
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
        _ROW_HASH_VERSION_FIELD: _ROW_HASH_VERSION_V2,
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
    # Replay bundles may include dense inspection/recovery namespaces in
    # addition to the base capture.  Check all row keys globally, then bind
    # only the exact source rows named by each cached candidate below.
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
        or payload.get(_ROW_HASH_VERSION_FIELD) not in (None, *_ROW_HASH_VERSIONS)
        or type(payload.get("candidate_count")) is not int
        or not isinstance(payload.get("candidates"), list)
        or payload["candidate_count"] != len(payload["candidates"])
    ):
        raise ValueError("Hint-card cache header is stale, foreign, or malformed.")
    entries = payload["candidates"]
    candidates: list[dict[str, Any]] = []
    spans = []
    refreshed_validation_rows: list[dict[str, Any]] | None = None
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
        source_row_view = cache_provenance.get(_SOURCE_ROW_VIEW_FIELD)
        if source_row_view not in (None, _RECEIPT_OCCLUSION_CURSOR_VIEW):
            raise ValueError("Hint-card cache candidate source-row view is unknown.")
        payload_hash_version = payload.get(_ROW_HASH_VERSION_FIELD)
        candidate_hash_version = cache_provenance.get(_ROW_HASH_VERSION_FIELD)
        if candidate_hash_version not in (None, *_ROW_HASH_VERSIONS):
            raise ValueError("Hint-card cache candidate row hash version is unknown.")
        if candidate_hash_version != payload_hash_version:
            raise ValueError("Hint-card cache row hash version is inconsistent.")
        stored_span_hashes = cache_provenance["span_rows"]
        source_times = entry.get("source_timestamps_ms")
        if (
            not isinstance(source_times, list)
            or not source_times
            or any(type(value) is not int or value < 0 for value in source_times)
        ):
            raise ValueError("Hint-card cache candidate span is malformed.")
        current_span = _candidate_observation_rows(entry, valid_rows)
        if current_span is None:
            raise ValueError("Hint-card cache candidate source span is missing.")
        current_span = _source_bound_rows(
            current_span,
            evidence_root,
            source_sha256=source_sha256,
        )
        if current_span is None or not _source_metadata_span_valid(
            current_span,
            evidence_root,
            source_sha256=source_sha256,
        ):
            raise ValueError("Hint-card cache candidate source metadata is stale.")
        validation_rows = current_span
        if source_row_view == _RECEIPT_OCCLUSION_CURSOR_VIEW:
            if candidate_hash_version != _ROW_HASH_VERSION_V2:
                raise ValueError("Refreshed hint-card cache must use source-row-v2.")
            if not _span_hashes_match(
                stored_span_hashes,
                current_span,
                version=candidate_hash_version,
            ):
                raise ValueError("Refreshed hint-card cache source rows are stale.")
            refreshed_validation_rows = _receipt_occlusion_cursor_view(current_span)
            if refreshed_validation_rows is None:
                raise ValueError("Refreshed hint-card cache particle metadata is malformed.")
            validation_rows = refreshed_validation_rows
            # The full current row fingerprint was checked above.  The
            # cursor-only structural view has a deliberately different hash,
            # so source proof validation must not compare it to that hash.
            stored_span_hashes = None
        validation = _validate_candidate(
            entry,
            validation_rows,
            evidence_root,
            source_sha256=source_sha256,
            capture_manifest_sha256=capture_manifest_sha256,
            stored_span_hashes=stored_span_hashes,
            stored_observation_provenance=cache_provenance["observations"],
            stored_card_identity_proofs=cache_provenance.get(
                _CARD_IDENTITY_PROOFS_FIELD
            ),
            source_row_view=source_row_view,
            row_hash_version=candidate_hash_version,
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


__all__ = ["CACHE_SCHEMA", "load", "main", "prepare", "save"]
