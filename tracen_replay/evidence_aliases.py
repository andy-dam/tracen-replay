"""Validate source-image aliases for a separately bound evaluation pass.

The source review documents and a worker report can refer to the same captured
frame through different relative paths.  This module provides a deliberately
small bridge for that case.  It does not make an observation true, choose an
amount, or change a phase: it only adds a source-reference image to an already
adapted effect when the effect identity, source video, bytes, and source frame
timestamp all agree.

The resolver is kept independent from the evaluator so the ordinary strict
grade can remain unchanged.  Callers may use the returned copy for a separate
source-identity grade and retain ``diagnostics`` as the audit record.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any, Iterable, Mapping


_SCHEMA_VERSION = "tracen-replay/evidence-alias-validation-v1"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SUPPORTED_EFFECT_KINDS = frozenset({"stat_change", "performance_change"})


def _strict_sha256(value: object) -> str | None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        return None
    return value.lower()


def _source_sha256(value: Mapping[str, Any]) -> str | None:
    source = value.get("source")
    if isinstance(source, Mapping):
        candidate = source.get("sha256")
        digest = _strict_sha256(candidate)
        if digest is not None:
            return digest
    return _strict_sha256(value.get("source_sha256"))


def _canonical_relative_path(value: object) -> str | None:
    """Return a safe slash-normalized relative path, or ``None``.

    Evidence IDs are source-root-relative paths.  Windows drive paths,
    rooted paths, parent traversal, and empty components are rejected before
    a filesystem path is constructed.  Backslashes are normalized so an
    evidence record written on Windows has the same identity as one written
    on a POSIX worker.
    """

    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
    ):
        return None
    parts = posix.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _resolve_evidence_path(root: Path, value: object) -> tuple[Path | None, str | None, str | None]:
    """Resolve a root-contained evidence file.

    The third return value is a compact failure reason suitable for audit
    diagnostics.  ``resolve(strict=True)`` also catches symlinks that escape
    the supplied evidence root.
    """

    relative = _canonical_relative_path(value)
    if relative is None:
        return None, None, "unsafe_or_malformed_path"
    try:
        path = (root / Path(*PurePosixPath(relative).parts)).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None, relative, "missing_or_unresolvable_file"
    try:
        path.relative_to(root)
    except ValueError:
        return None, relative, "path_escapes_evidence_root"
    if not path.is_file():
        return None, relative, "evidence_is_not_file"
    return path, relative, None


def _evidence_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    if isinstance(value, tuple):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _valid_timestamp(value: object) -> bool:
    return type(value) is int and value >= 0


def _source_evidence_times(report: Mapping[str, Any], source_sha256: str) -> dict[str, set[int]]:
    """Index source-root evidence paths by explicit source timestamps.

    Only a mapping carrying ``source_timestamp_ms`` and ``evidence`` is used.
    Generic UI timestamps are intentionally ignored because they do not prove
    which captured source frame an image represents.
    """

    result: dict[str, set[int]] = {}

    def add_binding(value: object) -> None:
        if not isinstance(value, Mapping):
            return
        timestamp = value.get("source_timestamp_ms")
        nested_source = value.get("source_sha256")
        source_ok = nested_source is None or _strict_sha256(nested_source) == source_sha256
        if not _valid_timestamp(timestamp) or not source_ok:
            return
        for evidence in _evidence_values(value.get("evidence")):
            relative = _canonical_relative_path(evidence)
            if relative is not None:
                result.setdefault(relative, set()).add(timestamp)

    # ``gameplay_tracking.readings`` is the authoritative report namespace for
    # frame timestamps.  Do not recurse through the complete report: event
    # provenance and mirrored diagnostics can repeat an image ID without
    # proving that it was observed at the stated source time.  Inspection
    # merge rejections are explicitly source-bound children of a reading and
    # are retained because they carry the rejected inspection frame's binding.
    tracking = report.get("gameplay_tracking")
    readings = tracking.get("readings") if isinstance(tracking, Mapping) else None
    if isinstance(readings, list):
        for reading in readings:
            add_binding(reading)
            if isinstance(reading, Mapping):
                parent_source = reading.get("source_sha256")
                if (parent_source is not None
                        and _strict_sha256(parent_source) != source_sha256):
                    # Hashless inspection children inherit the reading's
                    # source identity; they cannot escape a rejected parent.
                    continue
                rejections = reading.get("inspection_merge_rejections")
                if isinstance(rejections, list):
                    for rejection in rejections:
                        add_binding(rejection)
    return result


def _effect_signature(value: object) -> tuple[str, str, str, str] | None:
    if not isinstance(value, Mapping) or value.get("category") != "effect":
        return None
    phase = value.get("phase")
    payload = value.get("payload")
    if not isinstance(phase, str) or not isinstance(payload, Mapping):
        return None
    kind = payload.get("kind")
    field = payload.get("field")
    if (
        not isinstance(kind, str)
        or kind not in _SUPPORTED_EFFECT_KINDS
        or not isinstance(field, str)
        or not field
    ):
        return None
    # Amount is deliberately omitted from the identity.  Evidence aliases
    # must still be attached when the predicted amount is wrong or unknown so
    # the evaluator can grade that field rather than misclassifying it as a
    # missing effect.
    return ("effect", phase, kind, field)


def _valid_interval(value: Mapping[str, Any]) -> tuple[int, int] | None:
    start = value.get("start_ms")
    end = value.get("end_ms")
    if not _valid_timestamp(start) or not _valid_timestamp(end) or start > end:
        return None
    return start, end


def _reference_effects(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    effects: list[dict[str, Any]] = []
    cases = document.get("cases")
    if not isinstance(cases, list):
        return effects
    for case in cases:
        if not isinstance(case, Mapping):
            continue
        for observation in case.get("observations", ()):
            if not isinstance(observation, Mapping):
                continue
            signature = _effect_signature(observation)
            interval = _valid_interval(observation)
            evidence = tuple(
                relative
                for item in _evidence_values(observation.get("evidence"))
                if (relative := _canonical_relative_path(item)) is not None
            )
            if signature is None or interval is None or not evidence:
                continue
            if observation.get("status") not in {None, "observed"}:
                continue
            effects.append(
                {
                    "id": observation.get("id"),
                    "signature": signature,
                    "amount": observation["payload"].get("amount"),
                    "interval": interval,
                    "evidence": evidence,
                }
            )
    return effects


def _prediction_effects(prediction: Mapping[str, Any]) -> Iterable[tuple[int, dict[str, Any]]]:
    observations = prediction.get("observations")
    if not isinstance(observations, list):
        return ()
    result: list[tuple[int, dict[str, Any]]] = []
    for index, observation in enumerate(observations):
        if isinstance(observation, Mapping) and _effect_signature(observation) is not None:
            result.append((index, observation))
    return result


def _image_hash(path: Path, cache: dict[Path, str | None]) -> str | None:
    if path not in cache:
        try:
            cache[path] = sha256(path.read_bytes()).hexdigest()
        except OSError:
            cache[path] = None
    return cache[path]


def _context(document: object, report: object, prediction: object, evidence_root: object) -> tuple[
    Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Path, str
]:
    if not isinstance(document, Mapping):
        raise ValueError("validated_document must be an object")
    if not isinstance(report, Mapping):
        raise ValueError("report must be an object")
    if not isinstance(prediction, Mapping):
        raise ValueError("prediction must be an object")
    source_sha = _source_sha256(document)
    report_sha = _source_sha256(report)
    if source_sha is None:
        raise ValueError("validated_document has no valid source_sha256")
    if report_sha is None or report_sha != source_sha:
        raise ValueError("report source_sha256 does not match validated_document")
    prediction_sha = prediction.get("source_sha256")
    if prediction_sha is not None:
        normalized_prediction_sha = _strict_sha256(prediction_sha)
        if normalized_prediction_sha != source_sha:
            raise ValueError("prediction source_sha256 does not match validated_document")
    if isinstance(evidence_root, Path):
        root = evidence_root
    elif isinstance(evidence_root, str):
        root = Path(evidence_root)
    else:
        raise ValueError("evidence_root must be a path")
    if not root.is_absolute():
        raise ValueError("evidence_root must be absolute")
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("evidence_root is not resolvable") from None
    if not root.is_dir():
        raise ValueError("evidence_root must be a directory")
    return document, report, prediction, root, source_sha


def resolve_evidence_aliases(
    validated_document: Mapping[str, Any],
    report: Mapping[str, Any],
    prediction: Mapping[str, Any],
    evidence_root: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a copied prediction with only source-proven aliases added.

    An alias is accepted only for an already-present applied/observed effect
    with the exact same phase, effect kind, and field as a validated source
    observation.  The amount remains part of the prediction and is reported
    separately so a wrong amount is graded as wrong rather than as a missing
    effect.  Both image paths must resolve under ``evidence_root``;
    the reference path's bytes must match ``image_sha256`` and the candidate
    path's bytes must match those same bytes.  Each path must have one explicit
    common ``source_timestamp_ms`` in the report, inside the reference
    observation interval.

    Invalid top-level source identity is an input error and raises
    ``ValueError``.  Individual evidence failures are recorded and abstain
    from aliasing, allowing the caller to retain a useful strict result.
    """

    document, source_report, source_prediction, root, source_sha = _context(
        validated_document, report, prediction, evidence_root
    )
    copied = deepcopy(source_prediction)
    diagnostics: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "source_sha256": source_sha,
        "evidence_root": str(root),
        "alias_count": 0,
        "reference_effect_count": 0,
        "candidate_effect_count": 0,
        "aliases": [],
        "rejections": {},
    }
    rejection_counts: Counter[str] = Counter()

    def reject(reason: str) -> None:
        rejection_counts[reason] += 1

    image_map_raw = document.get("image_sha256")
    image_map: dict[str, str] = {}
    if isinstance(image_map_raw, Mapping):
        for raw_path, raw_digest in image_map_raw.items():
            relative = _canonical_relative_path(raw_path)
            digest = _strict_sha256(raw_digest)
            if relative is not None and digest is not None:
                image_map[relative] = digest
    if not image_map:
        diagnostics["rejections"] = {"no_valid_reference_image_hashes": 1}
        return copied, diagnostics

    times = _source_evidence_times(source_report, source_sha)
    references = _reference_effects(document)
    candidates = list(_prediction_effects(copied))
    diagnostics["reference_effect_count"] = len(references)
    diagnostics["candidate_effect_count"] = len(candidates)
    digest_cache: dict[Path, str | None] = {}
    accepted: dict[tuple[int, str, str], dict[str, Any]] = {}

    for candidate_index, candidate in candidates:
        candidate_signature = _effect_signature(candidate)
        if candidate_signature is None:
            continue
        # When an adapted effect carries amount-specific evidence, do not
        # treat phase-only evidence as proof of its numeric amount.  The
        # reference observation's declared evidence remains the field-specific
        # source proof supplied by the validated document.
        if "amount_evidence" in candidate:
            # An explicit empty/invalid amount-evidence field is an
            # abstention signal.  Falling back to phase evidence here would
            # turn a shared frame into unearned numeric proof.
            candidate_evidence_values = _evidence_values(candidate.get("amount_evidence"))
        else:
            candidate_evidence_values = _evidence_values(candidate.get("evidence"))
        candidate_evidence = tuple(
            relative
            for item in candidate_evidence_values
            if (relative := _canonical_relative_path(item)) is not None
        )
        if not candidate_evidence:
            continue
        for reference in references:
            if reference["signature"] != candidate_signature:
                continue
            ref_start, ref_end = reference["interval"]
            for reference_evidence in reference["evidence"]:
                expected_hash = image_map.get(reference_evidence)
                if expected_hash is None:
                    reject("reference_image_hash_missing")
                    continue
                ref_path, ref_relative, error = _resolve_evidence_path(root, reference_evidence)
                if error is not None or ref_path is None or ref_relative is None:
                    reject("reference_" + (error or "path_invalid"))
                    continue
                if _image_hash(ref_path, digest_cache) != expected_hash:
                    reject("reference_bytes_mismatch")
                    continue
                ref_times = times.get(ref_relative, set())
                if len(ref_times) != 1:
                    reject("reference_timestamp_missing_or_ambiguous")
                    continue
                (reference_timestamp,) = tuple(ref_times)
                if not ref_start <= reference_timestamp <= ref_end:
                    reject("reference_timestamp_outside_interval")
                    continue
                for candidate_evidence_path in candidate_evidence:
                    if candidate_evidence_path == reference_evidence:
                        continue
                    # Reject other source moments before opening their image
                    # files. Most full-report observations are outside this
                    # reference frame; hashing them cannot establish an alias.
                    candidate_times = times.get(candidate_evidence_path, set())
                    if len(candidate_times) != 1:
                        reject("candidate_timestamp_missing_or_ambiguous")
                        continue
                    (candidate_timestamp,) = tuple(candidate_times)
                    if candidate_timestamp != reference_timestamp:
                        reject("source_timestamp_mismatch")
                        continue
                    candidate_path, candidate_relative, error = _resolve_evidence_path(
                        root, candidate_evidence_path
                    )
                    if error is not None or candidate_path is None or candidate_relative is None:
                        reject("candidate_" + (error or "path_invalid"))
                        continue
                    if _image_hash(candidate_path, digest_cache) != expected_hash:
                        reject("candidate_bytes_mismatch")
                        continue
                    reference_id = reference.get("id")
                    key = (candidate_index, reference_evidence, candidate_relative)
                    candidate_amount = candidate["payload"].get("amount")
                    reference_amount = reference["amount"]
                    accepted[key] = {
                        "prediction_index": candidate_index,
                        "prediction_id": candidate.get("id"),
                        "reference_id": reference_id,
                        "reference_evidence": reference_evidence,
                        "candidate_evidence": candidate_relative,
                        "field": candidate_signature[3],
                        "candidate_amount": candidate_amount,
                        "reference_amount": reference_amount,
                        "sha256": expected_hash,
                        "source_timestamp_ms": reference_timestamp,
                        "reference_interval_ms": [ref_start, ref_end],
                        "basis": "same_source_same_timestamp_verified_file_bytes",
                    }

    observations = copied.get("observations")
    if isinstance(observations, list):
        for (candidate_index, reference_evidence, _candidate_evidence), proof in accepted.items():
            if not (0 <= candidate_index < len(observations)):
                reject("candidate_index_invalid")
                continue
            candidate = observations[candidate_index]
            if not isinstance(candidate, dict):
                reject("candidate_is_not_mutable_object")
                continue
            evidence = candidate.get("evidence")
            if isinstance(evidence, list):
                if reference_evidence not in evidence:
                    evidence.append(reference_evidence)
            elif isinstance(evidence, str):
                if evidence != reference_evidence:
                    candidate["evidence"] = [evidence, reference_evidence]
            else:
                candidate["evidence"] = [reference_evidence]
            amount_evidence = candidate.get("amount_evidence")
            if isinstance(amount_evidence, list):
                if reference_evidence not in amount_evidence:
                    amount_evidence.append(reference_evidence)
            elif isinstance(amount_evidence, str) and amount_evidence != reference_evidence:
                candidate["amount_evidence"] = [amount_evidence, reference_evidence]
            diagnostics["aliases"].append(proof)

    diagnostics["aliases"].sort(
        key=lambda value: (
            value["prediction_index"],
            value["reference_evidence"],
            value["candidate_evidence"],
        )
    )
    diagnostics["alias_count"] = len(diagnostics["aliases"])
    diagnostics["rejections"] = dict(sorted(rejection_counts.items()))
    return copied, diagnostics


__all__ = ["resolve_evidence_aliases"]
