"""Build source-bound choice observations for the full-recording producer.

``full_recording.analyze_frames`` stores the immutable OCR sidecar and the
810x1080 gameplay crop, but the ordinary path historically did not run the
choice-only pixel observer.  This adapter is the bounded bridge between those
existing artifacts and :mod:`event_choice_commitment`:

* it considers only likely dialogue candidates before opening a crop;
* it verifies the sidecar timestamp, gameplay-pixel hash, crop size, and proof
  path before invoking ``choice_evidence.observe``;
* it accepts already validated choice-inspection rows and coalesces duplicates;
* it emits menu observations separately from committed choices, so a preview
  can never be counted as an action and the same choice cannot be emitted twice.

The adapter does not run OCR, invent option text, use reward effects, or infer
selection from a future outcome.  A missing or unverified source artifact is
skipped with an audit reason rather than promoted.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image

from .choice_evidence import observe
from .event_choice_commitment import reconstruct_committed_choices


SCHEMA = "tracen-replay/event-choice-source-adapter-v1"
GAMEPLAY_SIZE = (810, 1080)


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
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    left, top, right, bottom = result
    if not left < right or not top < bottom:
        return None
    return result


def _lines(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(line) for line in value if isinstance(line, Mapping) and _text(line.get("text"))
            and _box(line) is not None]


def _raw_lines(raw: Mapping[str, Any] | None, reading: Mapping[str, Any]) -> list[dict[str, Any]]:
    if isinstance(raw, Mapping):
        lines = _lines(raw.get("lines"))
        if lines:
            return lines
    ocr = reading.get("ocr")
    if isinstance(ocr, Mapping):
        # Parsed rows retain the immutable neural lines under this key for
        # fresh in-memory workers.  It is a fallback only; source verification
        # still requires the raw sidecar or an explicit fresh row.
        return _lines(ocr.get("neural"))
    return []


def _candidate_lines(lines: Iterable[Mapping[str, Any]]) -> bool:
    """Cheap candidate gate before opening a source crop.

    Dialogue cards occupy the lower gameplay pane.  The gate intentionally
    uses only geometry and visible punctuation/line density; it has no event
    vocabulary and therefore does not turn a particular recording into a
    special case.  Pixel inspection remains the authority.
    """

    lower = []
    for line in lines:
        box = _box(line)
        text = _text(line.get("text")) if isinstance(line, Mapping) else None
        if box is None or not text:
            continue
        left, top, right, bottom = box
        if 240 <= left <= 740 and 540 <= top <= 880:
            lower.append((text, box))
    if not lower:
        return False
    if any("?" in text or "？" in text for text, _box in lower):
        return True
    if len(lower) >= 2:
        tops = [box[1] for _text_value, box in lower]
        if max(tops) - min(tops) >= 50:
            return True
    # Wrapped option text, a speaker line, and a dialogue line commonly appear
    # together even when punctuation is partly obscured by the cursor.  Requiring
    # three lines avoids opening most ordinary hub frames with one lower label.
    return len(lower) >= 3


def _evidence_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if _text(value) else []
    if isinstance(value, (list, tuple)):
        return list(dict.fromkeys(_text(item) for item in value if _text(item)))
    return []


_SOURCE_HASH_KEYS = (
    "source_sha256",
    "gameplay_sha256",
    "source_gameplay_sha256",
    "source_frame_sha256",
    "proof_sha256",
)

_SOURCE_HASH_ALIASES = {
    "source_sha256": "source",
    "gameplay_sha256": "gameplay",
    "source_gameplay_sha256": "gameplay",
    "source_frame_sha256": "source_frame",
    "proof_sha256": "proof",
}


def _normalized_evidence(value: Any) -> tuple[str, ...]:
    """Return the exact proof namespace used for source identity.

    Callers pass paths from different producer stages (for example a cache row
    and a fresh in-memory row).  Slash normalization is enough here because
    these are already root-relative paths at the adapter boundary; resolving a
    second root in this merge function would risk turning unrelated paths into
    the same basename.  An empty tuple means there is no auditable proof and
    therefore is never mergeable.
    """

    return tuple(sorted({item.replace("\\", "/") for item in _evidence_values(value)}))


def _source_hashes(row: Mapping[str, Any]) -> dict[str, str]:
    result = {}
    for key in _SOURCE_HASH_KEYS:
        value = _text(row.get(key))
        if value:
            identity_key = _SOURCE_HASH_ALIASES[key]
            normalized = value.casefold()
            if identity_key in result and result[identity_key] != normalized:
                result[identity_key] = "__conflicting_source_hashes__"
            else:
                result[identity_key] = normalized
    return result


def _source_hashes_compatible(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool:
    left_hashes, right_hashes = _source_hashes(left), _source_hashes(right)
    if any(value == "__conflicting_source_hashes__"
           for value in (*left_hashes.values(), *right_hashes.values())):
        return False
    return all(left_hashes[key] == right_hashes[key]
               for key in set(left_hashes) & set(right_hashes))


def _same_physical_source(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Whether two rows prove the same physical source frame.

    A timestamp is deliberately absent from this predicate: repeated OCR
    passes can share a timestamp while referring to different frame files.
    Exact evidence identity is required, and any hash supplied by both rows
    must agree.  Missing metadata never manufactures an identity.
    """

    left_evidence = _normalized_evidence(left.get("evidence"))
    right_evidence = _normalized_evidence(right.get("evidence"))
    if not left_evidence or left_evidence != right_evidence:
        return False
    return _source_hashes_compatible(left, right)


def _root_relative(value: Any, root: Path) -> str | None:
    """Canonicalize a proof path and reject paths outside ``root``.

    Evidence paths are part of the source identity.  In particular, two
    ``gameplay/frame-000001.png`` files under different replay namespaces are
    unrelated even when their basenames and timestamps match.
    """

    text = _text(value)
    if not text:
        return None
    normalized = text.replace("\\", "/")
    candidate = Path(normalized)
    root = root.resolve()
    try:
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        relative = resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return relative.as_posix()


def _same_path(left: Any, right: Any, root: Path | None = None) -> bool:
    """Compare exact root-relative proof paths; never compare basenames alone."""

    if root is not None:
        left_text, right_text = _root_relative(left, root), _root_relative(right, root)
    else:
        left_text = _text(left).replace("\\", "/") if _text(left) else None
        right_text = _text(right).replace("\\", "/") if _text(right) else None
    return bool(left_text and right_text and left_text == right_text)


def _raw_candidates(root: Path, evidence: str) -> list[Path]:
    """Return the one namespace-preserving neural sidecar location."""

    relative = _root_relative(evidence, root)
    if relative is None:
        return []
    evidence_path = Path(relative)
    parts = list(evidence_path.parts)
    try:
        gameplay_index = max(index for index, part in enumerate(parts) if part.casefold() == "gameplay")
    except ValueError:
        return []
    # Preserve every namespace component and replace only the cache's
    # ``gameplay`` component with its sibling ``neural`` component.
    sidecar_relative = Path(*parts[:gameplay_index], "neural", *parts[gameplay_index + 1:])
    sidecar_relative = sidecar_relative.with_suffix(".json")
    sidecar = (root / sidecar_relative).resolve()
    try:
        sidecar.relative_to(root.resolve())
    except ValueError:
        return []
    return [sidecar]


def _raw_index(
    root: Path,
    raw_rows: Any,
) -> dict[tuple[int, str], Mapping[str, Any] | None]:
    exact: dict[tuple[int, str], Mapping[str, Any] | None] = {}
    if isinstance(raw_rows, Mapping):
        values = list(raw_rows.values())
    elif isinstance(raw_rows, Iterable) and not isinstance(raw_rows, (str, bytes)):
        values = list(raw_rows)
    else:
        values = []
    for raw in values:
        if not isinstance(raw, Mapping) or type(raw.get("source_timestamp_ms")) is not int:
            continue
        time = raw["source_timestamp_ms"]
        evidence = _text(raw.get("evidence")) or ""
        relative = _root_relative(evidence, root)
        if relative is not None:
            key = (time, relative)
            if key not in exact:
                exact[key] = raw
                continue
            previous = exact[key]
            if previous is None:
                continue
            # The same source key can arrive from cache and fresh memory.  A
            # shared sealed gameplay hash proves that those rows are the same
            # pixels; conflicting hashes must remain an explicit ambiguity.
            previous_hashes = _source_hashes(previous)
            current_hashes = _source_hashes(raw)
            if (not set(previous_hashes) & set(current_hashes)
                    or not _source_hashes_compatible(previous, raw)):
                exact[key] = None
    return exact


def _raw_for(
    root: Path,
    reading: Mapping[str, Any],
    exact: Mapping[tuple[int, str], Mapping[str, Any] | None],
) -> Mapping[str, Any] | None:
    timestamp = reading.get("source_timestamp_ms")
    evidence = _text(reading.get("evidence"))
    relative = _root_relative(evidence, root)
    if type(timestamp) is int and relative is not None:
        key = (timestamp, relative)
        if key in exact:
            # ``None`` is a deliberate duplicate-source conflict marker; do
            # not fall through to a basename or alternate sidecar guess.
            raw = exact[key]
            return raw if isinstance(raw, Mapping) else None
    if evidence:
        for path in _raw_candidates(root, evidence):
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, Mapping):
                    # A copied capture keeps sidecar paths relative to its
                    # own namespace, while assembled readings are root-relative.
                    # Resolve using the exact sidecar location, never a basename.
                    raw_evidence = _text(value.get("evidence"))
                    if raw_evidence and not _same_path(raw_evidence, evidence, root):
                        parts = path.relative_to(root).parts
                        neural_index = max(i for i, part in enumerate(parts)
                                           if part == "neural")
                        namespace = root.joinpath(*parts[:neural_index])
                        local_relative = _root_relative(raw_evidence, namespace)
                        if local_relative is not None:
                            resolved = _root_relative(str(namespace / local_relative), root)
                            if resolved == relative:
                                value = dict(value, evidence=resolved)
                    return value
    return None


def _proof_path(root: Path, evidence: Any) -> Path | None:
    text = _text(evidence)
    if not text:
        return None
    root = root.resolve()
    path = (root / Path(text.replace("\\", "/"))).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path if path.is_file() else None


def _verified_pane(
    root: Path,
    reading: Mapping[str, Any],
    raw: Mapping[str, Any],
) -> tuple[Image.Image | None, str | None]:
    timestamp = reading.get("source_timestamp_ms")
    if type(timestamp) is not int or raw.get("source_timestamp_ms") != timestamp:
        return None, "timestamp_mismatch"
    reading_evidence = _text(reading.get("evidence"))
    raw_evidence = _text(raw.get("evidence"))
    reading_relative = _root_relative(reading_evidence, root)
    raw_relative = _root_relative(raw_evidence, root)
    if reading_relative is None or raw_relative is None:
        return None, "evidence_path_outside_root"
    if reading_relative != raw_relative:
        return None, "evidence_path_mismatch"
    proof = _proof_path(root, reading_relative)
    if proof is None:
        return None, "missing_gameplay_proof"
    from .frame_cache import open_rgb, rgb_sha256
    try:
        pane = open_rgb(proof)
    except (OSError, ValueError):
        return None, "unreadable_gameplay_proof"
    if pane.size != GAMEPLAY_SIZE:
        return None, "unexpected_gameplay_size"
    expected = _text(raw.get("gameplay_sha256"))
    if not expected:
        return None, "missing_gameplay_hash"
    if rgb_sha256(proof) != expected:
        return None, "gameplay_pixels_changed"
    return pane, None


# A verified observation per proof file and every input that produced it. An
# analysis builds its choice observations before and after boundary recovery,
# over mostly the same frames; the pane check and the pixel observation are
# the same for a file whose path, size and modification time did not change.
_VERIFIED_OBSERVATIONS: dict[tuple[Any, ...], tuple[bool, str | None, dict[str, Any] | None]] = {}


def _observe_verified(
    root: Path,
    reading: Mapping[str, Any],
    raw: Mapping[str, Any],
    lines: list[dict[str, Any]],
    time: int,
    evidence: str,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """``_verified_pane`` then ``same_frame_choice_observation``, remembered per proof file."""

    key = None
    relative = _root_relative(_text(reading.get("evidence")), root)
    proof = _proof_path(root, relative) if relative is not None else None
    if proof is not None:
        try:
            stat = proof.stat()
        except OSError:
            stat = None
        if stat is not None:
            key = (str(root), str(proof), stat.st_size, stat.st_mtime_ns, time,
                   raw.get("source_timestamp_ms"), _text(reading.get("evidence")), _text(raw.get("evidence")),
                   _text(raw.get("gameplay_sha256")), evidence,
                   json.dumps(lines, sort_keys=True, default=str))
            known = _VERIFIED_OBSERVATIONS.get(key)
            if known is not None:
                return known[0], known[1], deepcopy(known[2])
    pane, reason = _verified_pane(root, reading, raw)
    row = None
    if pane is not None:
        row = same_frame_choice_observation(pane, lines, source_timestamp_ms=time, evidence=evidence)
    result = (pane is not None, reason, row)
    if key is not None:
        _VERIFIED_OBSERVATIONS[key] = (result[0], result[1], deepcopy(row))
    return result


def same_frame_choice_observation(
    pane: Image.Image,
    lines: Iterable[Mapping[str, Any]],
    *,
    source_timestamp_ms: int,
    evidence: str,
) -> dict[str, Any] | None:
    """Generate one candidate-driven observation from one verified crop.

    The function is reusable by a fresh worker that already has the raw OCR
    result and crop in memory.  It never runs OCR and returns ``None`` for a
    frame with no card/selection signal.
    """

    if pane.size != GAMEPLAY_SIZE:
        raise ValueError("Expected isolated 810x1080 gameplay pixels.")
    if type(source_timestamp_ms) is not int or source_timestamp_ms < 0:
        raise ValueError("source_timestamp_ms must be a nonnegative integer.")
    evidence = _text(evidence)
    if not evidence:
        raise ValueError("evidence is required for a source-bound observation.")
    normalized_lines = _lines(list(lines) if lines is not None else [])
    if not _candidate_lines(normalized_lines):
        return None
    observed = observe(pane, normalized_lines, include_slots=True)
    if not any((observed.get("offered_card_candidates"), observed.get("offered_card_slots"),
                observed.get("selected_card_candidates"), observed.get("selection_mark_pairs"))):
        return None
    observed.update(
        source_timestamp_ms=source_timestamp_ms,
        evidence=evidence,
        observation_basis="verified_same_frame_choice_pixels",
    )
    return observed


def _existing_row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, Mapping) or type(row.get("source_timestamp_ms")) is not int:
        return None
    if row.get("screen_boundary"):
        return {
            "source_timestamp_ms": row["source_timestamp_ms"],
            "evidence": _evidence_values(row.get("evidence")),
            "screen_boundary": True,
        }
    observation = row.get("choice_observation")
    if isinstance(observation, Mapping):
        result = deepcopy(dict(observation))
        result["source_timestamp_ms"] = row["source_timestamp_ms"]
        evidence = _evidence_values(row.get("evidence")) or _evidence_values(observation.get("evidence"))
        result["evidence"] = evidence
        return result
    if any(key in row for key in ("offered_card_candidates", "offered_card_slots", "selection_mark_pairs")):
        result = deepcopy(dict(row))
        result["evidence"] = _evidence_values(result.get("evidence"))
        return result
    return None


def _observation_signal_score(value: Mapping[str, Any]) -> int:
    return (3 * int(bool(value.get("selection_mark_pairs")))
            + 2 * int(bool(value.get("offered_card_slots")))
            + int(bool(value.get("offered_card_candidates")))
            + int(bool(value.get("selected_card_candidates"))))


def _merge_same_source_observations(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> dict[str, Any]:
    """Combine parser channels only after physical-source verification."""

    if _observation_signal_score(right) > _observation_signal_score(left):
        chosen, other = right, left
    else:
        chosen, other = left, right
    merged = deepcopy(dict(chosen))
    # A refined row and a fresh row can expose different channels from the
    # same pixels.  Retain both only after _same_physical_source has proved
    # their evidence namespace and supplied hashes are compatible.
    for key in ("offered_card_slots", "offered_card_candidates", "selected_card_candidates",
                "selection_mark_pairs"):
        if not merged.get(key) and other.get(key):
            merged[key] = deepcopy(other[key])
        elif key == "selection_mark_pairs" and merged.get(key) and other.get(key):
            # Preserve disagreement; temporal reconstruction will abstain when
            # more than one valid mark remains on the same source frame.
            merged[key] = deepcopy(merged[key]) + [
                deepcopy(item) for item in other[key] if item not in merged[key]
            ]
    evidence = list(dict.fromkeys(_evidence_values(merged.get("evidence"))
                                  + _evidence_values(other.get("evidence"))))
    merged["evidence"] = evidence
    for key in _SOURCE_HASH_KEYS:
        if not merged.get(key) and other.get(key):
            merged[key] = deepcopy(other[key])
    if other.get("observation_conflict"):
        merged["observation_conflict"] = True
    return merged


def merge_choice_observations(*sources: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate parser rows without collapsing distinct proof paths.

    Cache and fresh producers can report the same frame, but timestamp alone
    is not an identity.  Rows merge only when their exact evidence path set is
    equal and any shared source hashes agree.  Different proofs at one
    timestamp remain visible and are marked ``observation_conflict`` so the
    commitment stage can abstain instead of silently combining channels.
    """

    by_time: dict[int, list[dict[str, Any]]] = {}
    for source in sources:
        if not isinstance(source, Iterable) or isinstance(source, (str, bytes)):
            continue
        for raw in source:
            row = _existing_row(raw)
            if row is None:
                continue
            time = row["source_timestamp_ms"]
            existing_rows = by_time.setdefault(time, [])
            if row.get("screen_boundary"):
                # A known screen boundary is an explicit state transition.  It
                # supersedes ambiguous same-time observations and prevents an
                # old menu from crossing into a different screen.
                if not existing_rows or not all(item.get("screen_boundary") for item in existing_rows):
                    by_time[time] = [row]
                    continue
                match_index = next(
                    (index for index, item in enumerate(existing_rows)
                     if _same_physical_source(item, row)),
                    None,
                )
                if match_index is None:
                    existing_rows.append(row)
                else:
                    existing_rows[match_index] = _merge_same_source_observations(
                        existing_rows[match_index], row)
                continue
            if any(item.get("screen_boundary") for item in existing_rows):
                continue
            match_index = next(
                (index for index, item in enumerate(existing_rows)
                 if _same_physical_source(item, row)),
                None,
            )
            if match_index is None:
                existing_rows.append(row)
            else:
                existing_rows[match_index] = _merge_same_source_observations(
                    existing_rows[match_index], row)

    result: list[dict[str, Any]] = []
    for time in sorted(by_time):
        rows = by_time[time]
        if len(rows) > 1:
            proofs = [list(_normalized_evidence(row.get("evidence"))) for row in rows]
            for row in rows:
                row["observation_conflict"] = True
                row["conflicting_proofs"] = deepcopy(proofs)
        result.extend(rows)
    return sorted(
        result,
        key=lambda row: (
            row["source_timestamp_ms"],
            _normalized_evidence(row.get("evidence")),
        ),
    )


def _choice_signature(choice: Mapping[str, Any]) -> tuple[Any, ...] | None:
    options = choice.get("options")
    selected_index = choice.get("selected_index")
    first = choice.get("first_seen_ms")
    observed = choice.get("selection_observed_ms")
    if (not isinstance(options, list) or not options or
            any(not _text(option) for option in options) or
            type(selected_index) is not int or not 0 <= selected_index < len(options) or
            type(first) is not int or type(observed) is not int):
        return None
    return (first, observed, selected_index,
            tuple(" ".join(_text(option).casefold().split()) for option in options))


def merge_committed_choices(*sources: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Coalesce equivalent committed outputs from cache and fresh producers.

    The full-recording worker may have an older choice reconstruction result as
    well as this adapter's result.  Equivalent events are represented once only
    when their exact proof identity is shared.  Same-looking events from
    different proof files are ambiguous and are omitted so a caller cannot
    accidentally report a duplicate or misattributed commitment.  Rows without
    a committed selection witness or auditable proof are ignored.
    """

    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for source in sources:
        if not isinstance(source, Iterable) or isinstance(source, (str, bytes)):
            continue
        for raw in source:
            if not isinstance(raw, Mapping) or raw.get("kind") not in ("dialogue_choice", "dialogue_response"):
                continue
            if (raw.get("selection_state") != "committed"
                    and not raw.get("selection_marks")
                    and not (isinstance(raw.get("selected_state"), Mapping)
                             and raw["selected_state"].get("verified") is True)):
                continue
            signature = _choice_signature(raw)
            if signature is None or not _normalized_evidence(raw.get("evidence")):
                continue
            bucket = grouped.setdefault(signature, [])
            match_index = next(
                (index for index, item in enumerate(bucket)
                 if _same_physical_source(item, raw)),
                None,
            )
            if match_index is None:
                bucket.append(deepcopy(dict(raw)))
                continue
            match = bucket[match_index]
            old_score = (int(bool(match.get("selection_state") == "committed"))
                         + int(bool(match.get("selection_marks"))))
            new_score = (int(bool(raw.get("selection_state") == "committed"))
                         + int(bool(raw.get("selection_marks"))))
            chosen, other = (raw, match) if new_score > old_score else (match, raw)
            merged = deepcopy(dict(chosen))
            evidence = list(dict.fromkeys(_evidence_values(merged.get("evidence"))
                                          + _evidence_values(other.get("evidence"))))
            merged["evidence"] = evidence
            for key in _SOURCE_HASH_KEYS:
                if not merged.get(key) and other.get(key):
                    merged[key] = deepcopy(other[key])
            # Preserve the explicit commitment boundary when a legacy cache
            # row and a fresh source row are coalesced.  The report adapter
            # uses this field to emit an action rather than a preview; it
            # must never derive commitment from option text or effects.
            if (merged.get("selection_state") == "committed"
                    or merged.get("selection_marks")
                    or (isinstance(merged.get("selected_state"), Mapping)
                        and merged["selected_state"].get("verified") is True)):
                merged["phase"] = "committed"
            bucket[match_index] = merged

    # A signature supported by multiple exact proof identities is ambiguous.
    # Drop the whole group rather than selecting by input order or timestamp.
    result = [rows[0] for rows in grouped.values() if len(rows) == 1]
    for item in result:
        # Rows that did not need a merge still need the same projection
        # contract as reconstructed rows.  This covers source-verified legacy
        # commitments loaded from a cache without trusting their label/name.
        if (item.get("selection_state") == "committed"
                or item.get("selection_marks")
                or (isinstance(item.get("selected_state"), Mapping)
                    and item["selected_state"].get("verified") is True)):
            item["phase"] = "committed"
    result.sort(key=lambda item: (item["selection_observed_ms"], item["first_seen_ms"]))
    return result


def build_choice_observations(
    readings: Iterable[Mapping[str, Any]],
    root: str | Path,
    *,
    raw_rows: Iterable[Mapping[str, Any]] | Mapping[Any, Mapping[str, Any]] | None = None,
    existing: Iterable[Mapping[str, Any]] = (),
    include_boundaries: bool = True,
) -> dict[str, Any]:
    """Produce verified choice rows and committed events from cache/fresh data.

    ``raw_rows`` is optional for a fresh worker.  When omitted, the adapter
    resolves the immutable ``neural/<frame-id>.json`` sidecar beside the cache.
    Existing rows from ``inspect_choices.load`` are assumed source-verified by
    that loader and are merged before reconstruction.
    """

    root = Path(root).resolve()
    readings = list(readings)
    exact = _raw_index(root, raw_rows)
    generated: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for reading in readings:
        if not isinstance(reading, Mapping) or type(reading.get("source_timestamp_ms")) is not int:
            counts["malformed_reading"] += 1
            continue
        time = reading["source_timestamp_ms"]
        evidence = _text(reading.get("evidence"))
        screen = _text(reading.get("screen"))
        if include_boundaries and screen and screen != "unknown":
            generated.append({"source_timestamp_ms": time, "evidence": evidence or "",
                              "screen_boundary": True})
            counts["known_screen_boundary"] += 1
            continue
        raw = _raw_for(root, reading, exact)
        if raw is None:
            counts["missing_raw_sidecar"] += 1
            continue
        lines = _raw_lines(raw, reading)
        if not _candidate_lines(lines):
            counts["candidate_gate_skipped"] += 1
            continue
        verified, reason, row = _observe_verified(root, reading, raw, lines, time,
                                                  evidence or _text(raw.get("evidence")) or "")
        if not verified:
            counts[reason or "source_verification_failed"] += 1
            continue
        if row is None:
            counts["pixel_candidate_skipped"] += 1
            continue
        # Carry the sealed source identity into the observation.  This lets a
        # later cache/fresh merge distinguish two files that happen to share a
        # timestamp and lets a changed sidecar hash force abstention.
        for key in ("source_sha256", "gameplay_sha256", "source_frame_sha256", "proof_sha256"):
            value = _text(raw.get(key))
            if value:
                row[f"source_{key}" if key == "gameplay_sha256" else key] = value
        generated.append(row)
        counts["verified_choice_observations"] += 1
    readings_existing = []
    for reading in readings:
        if not isinstance(reading, Mapping):
            continue
        facts = reading.get("facts")
        if not isinstance(facts, Mapping):
            continue
        observation = facts.get("choice_observation")
        if not isinstance(observation, Mapping):
            continue
        readings_existing.append(dict(observation,
                                      source_timestamp_ms=reading.get("source_timestamp_ms"),
                                      evidence=reading.get("evidence", observation.get("evidence", ""))))
    merged = merge_choice_observations(generated, readings_existing, existing)
    commitment_audit: dict[str, Any] = {}
    committed = reconstruct_committed_choices(merged, audit=commitment_audit)
    return {
        "schema_version": SCHEMA,
        "observations": merged,
        "committed_choices": committed,
        "source_observation_count": len(generated),
        "merged_observation_count": len(merged),
        "committed_choice_count": len(committed),
        "audit": {
            "candidate_gate": "lower_dialogue_geometry_or_question_punctuation",
            "source_verification": "sidecar_timestamp_gameplay_hash_and_crop_size",
            "counts": dict(counts),
            "preview_observations_are_not_committed": True,
            "commitment": commitment_audit,
        },
    }


__all__ = [
    "SCHEMA",
    "GAMEPLAY_SIZE",
    "same_frame_choice_observation",
    "merge_choice_observations",
    "merge_committed_choices",
    "build_choice_observations",
]
