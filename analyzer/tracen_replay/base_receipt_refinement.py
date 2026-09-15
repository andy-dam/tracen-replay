"""Source-bound receipt rereads for selected base neural observations.

This module deliberately keeps the reread output separate from ``neural``.
The files in ``neural`` are immutable model observations and are discovered by
several cache readers with a broad ``*.json`` glob.  A base receipt refinement
is therefore an optional sidecar under ``base-receipt-refinement``.  Its three
OCR views are correlated views of one source image; they are evidence for a
same-frame reread, not three independent observations.

The loader is intentionally narrow.  It reuses the receipt consensus rules,
does not provide expected text or a name catalog, and only applies a
consensus that changes the source text.  Downstream callers must run their
source-pixel occlusion pass after this function.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

from .vision import within


REFINEMENT_DIR = "base-receipt-refinement"
RECEIPT_BAND = (250, 770, 850, 1000)

# These are the development probe geometries.  They are recorded in every
# generated view so a later report can distinguish a crop configuration from
# a source frame.  Geometry agreement is never labelled as correctness.
DEFAULT_CROP_GEOMETRIES = (
    dict(id="tight-h4-v0", left=4, right=4, top=0, bottom=0),
    dict(id="tight-h0-v0", left=0, right=0, top=0, bottom=0),
    dict(id="tight-h2-vminus2", left=2, right=2, top=-2, bottom=-2),
)


def fingerprint(raw):
    """Return the immutable observation fingerprint used by refinements."""

    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


def file_fingerprint(path):
    """Hash the exact evidence file bound to a refinement."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def gameplay_fingerprint(path):
    """Hash decoded RGB gameplay pixels, separately from the PNG file hash."""

    from PIL import Image

    with Image.open(path) as image:
        image=image.convert("RGB")
        if image.size != (810, 1080):
            raise ValueError("Base receipt evidence is not an 810x1080 gameplay pane.")
        return hashlib.sha256(image.tobytes()).hexdigest()


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _valid_view(view):
    if not isinstance(view, dict) or not isinstance(view.get("text"), str):
        return False
    confidence = view.get("confidence")
    return (type(confidence) in (int, float) and math.isfinite(confidence)
            and 0 <= confidence <= 100)


def _consensus(views):
    """Recompute a source-bound receipt consensus from recorded OCR views."""

    if not isinstance(views, list) or len(views) != 3 or not all(_valid_view(v) for v in views):
        return None
    # Import lazily so full_recording can import this adapter inside its cache
    # loop without creating a full_recording -> refine_receipts cycle.
    from .refine_receipts import consensus, receipt

    try:
        accepted = consensus(views)
    except (KeyError, TypeError, ValueError):
        return None
    if not accepted or not isinstance(accepted.get("text"), str):
        return None
    # Keep this guard local to the adapter as well as in consensus.  It makes
    # the sidecar fail closed if an older consensus implementation is loaded.
    if receipt(accepted["text"]) is None:
        return None
    return accepted


def _required_provenance(raw, extra, original, evidence_path, source_frame_path):
    if not isinstance(extra, dict) or extra.get("version") != 1:
        raise ValueError("Base receipt refinement version mismatch.")
    if extra.get("raw_sha256") != fingerprint(original):
        raise ValueError("Base receipt refinement source mismatch.")
    if not isinstance(original, dict) or not isinstance(original.get("lines"), list):
        raise ValueError("Base receipt refinement source has no immutable lines.")
    if not isinstance(raw, dict) or not isinstance(raw.get("lines"), list):
        raise ValueError("Base receipt refinement target has no lines.")
    if len(raw["lines"]) != len(original["lines"]):
        raise ValueError("Base receipt refinement line count mismatch.")

    for key in ("source_timestamp_ms", "evidence", "evidence_sha256",
                "source_frame_evidence", "source_frame_sha256", "gameplay_sha256",
                "source_model_sha256", "source_engine_fingerprint"):
        if key not in extra:
            raise ValueError(f"Base receipt refinement missing {key} provenance.")
    if extra["source_timestamp_ms"] != original.get("source_timestamp_ms"):
        raise ValueError("Base receipt refinement timestamp mismatch.")
    if extra["evidence"] != original.get("evidence"):
        raise ValueError("Base receipt refinement evidence path mismatch.")
    if extra["evidence_sha256"] is None:
        raise ValueError("Base receipt refinement gameplay evidence hash is missing.")
    if extra["source_frame_sha256"] != original.get("source_frame_sha256"):
        raise ValueError("Base receipt refinement source-frame hash mismatch.")
    if extra["gameplay_sha256"] != original.get("gameplay_sha256"):
        raise ValueError("Base receipt refinement gameplay hash mismatch.")
    if extra["source_model_sha256"] != original.get("model_sha256"):
        raise ValueError("Base receipt refinement source model mismatch.")
    if extra["source_engine_fingerprint"] != original.get("engine_fingerprint"):
        raise ValueError("Base receipt refinement source engine mismatch.")
    if extra.get("independent_observations") is not False:
        raise ValueError("Base receipt refinement cannot claim independent observations.")

    refinement_model = extra.get("refinement_model_sha256", extra.get("model_sha256"))
    refinement_engine = extra.get("refinement_engine_fingerprint", extra.get("engine_fingerprint"))
    if refinement_model is None or refinement_engine is None:
        raise ValueError("Base receipt refinement model identity is missing.")

    if evidence_path is None:
        raise ValueError("Base receipt refinement gameplay evidence path is required.")
    evidence_path = Path(evidence_path)
    if not evidence_path.is_file():
        raise ValueError("Base receipt refinement gameplay evidence is missing.")
    if file_fingerprint(evidence_path) != extra["evidence_sha256"]:
        raise ValueError("Base receipt refinement gameplay evidence changed.")
    if gameplay_fingerprint(evidence_path) != extra["gameplay_sha256"] or extra["gameplay_sha256"] != original.get("gameplay_sha256"):
        raise ValueError("Base receipt refinement gameplay pixels changed.")

    if source_frame_path is None:
        raise ValueError("Base receipt refinement source-frame path is required.")
    source_frame_path = Path(source_frame_path)
    if not source_frame_path.is_file():
        raise ValueError("Base receipt refinement source frame is missing.")
    if file_fingerprint(source_frame_path) != extra["source_frame_sha256"]:
        raise ValueError("Base receipt refinement source frame changed.")


def _line_item(item, index, source_lines, geometries):
    if not isinstance(item, dict):
        raise ValueError("Invalid base receipt refinement line.")
    if type(index) is not int or not 0 <= index < len(source_lines):
        raise ValueError("Invalid base receipt refinement line index.")
    source_box = item.get("source_box", item.get("line_box"))
    if source_box is not None and source_box != source_lines[index].get("box"):
        raise ValueError("Base receipt refinement line geometry mismatch.")
    views = item.get("views")
    if not isinstance(views, list) or len(views) != 3:
        raise ValueError("Base receipt refinement requires three same-frame views.")
    if not all(_valid_view(view) for view in views):
        raise ValueError("Invalid base receipt refinement OCR view.")
    declared={geometry["id"]:geometry for geometry in geometries}
    observed=[]
    for view in views:
        crop=view.get("crop_geometry")
        if not isinstance(crop, dict) or crop.get("coordinate_space") != "gameplay_pane":
            raise ValueError("Base receipt refinement crop provenance is missing.")
        geometry=declared.get(crop.get("id"))
        if geometry is None or crop.get("source_box") != source_lines[index].get("box"):
            raise ValueError("Base receipt refinement crop geometry mismatch.")
        expected=_crop_geometry(source_lines[index],geometry)
        if crop.get("crop_box") != expected:
            raise ValueError("Base receipt refinement crop geometry mismatch.")
        observed.append(crop["id"])
    if len(set(observed)) != len(geometries) or set(observed) != set(declared):
        raise ValueError("Base receipt refinement crop geometry set mismatch.")
    return views


def apply(raw, refinement, *, original=None, evidence_path=None, source_frame_path=None):
    """Apply a validated base receipt sidecar without overwriting prior work.

    ``original`` is the immutable neural observation used to create the
    sidecar.  ``raw`` may already contain another refinement, which is why the
    fingerprint is checked against ``original`` while each target line is
    compared with it before applying.  A changed target line is recorded as a
    collision and retained exactly as supplied.  ``evidence_path`` is the
    gameplay PNG; ``source_frame_path`` is the captured source frame used for
    the separate source-frame hash.
    """

    original = raw if original is None else original
    _required_provenance(raw, refinement, original, evidence_path, source_frame_path)
    source_lines = original["lines"]
    target_lines = raw["lines"]
    geometries = _validated_geometries(refinement.get("crop_geometries"))
    result = dict(raw, lines=[dict(line) for line in target_lines])
    seen = set()
    applied = []
    collisions = []
    unaccepted = []

    items = refinement.get("lines")
    if not isinstance(items, list):
        raise ValueError("Base receipt refinement has no line list.")
    for item in items:
        index = item.get("index") if isinstance(item, dict) else None
        if type(index) is not int or index in seen:
            raise ValueError("Invalid or duplicate base receipt refinement line index.")
        seen.add(index)
        views = _line_item(item, index, source_lines, geometries)
        accepted = _consensus(views)
        if accepted is None:
            unaccepted.append(index)
            continue

        source_line = source_lines[index]
        if accepted["text"].strip() == source_line.get("text", "").strip():
            # Same-text confidence promotion is the responsibility of the
            # existing outcome refinement.  This adapter is for text changes.
            unaccepted.append(index)
            continue
        current_line = target_lines[index]
        if current_line != source_line:
            collisions.append(dict(index=index, reason="prior_refinement",
                                   source_text=source_line.get("text"),
                                   current_text=current_line.get("text"),
                                   candidate_text=accepted["text"]))
            continue
        result["lines"][index] = dict(
            current_line,
            text=accepted["text"],
            confidence=accepted["confidence"],
            original_text=source_line.get("text"),
            original_confidence=source_line.get("confidence"),
            receipt_crop_views=copy.deepcopy(views),
            base_receipt_refined=True,
        )
        applied.append(index)

    prior = result.get("base_receipt_refinement")
    metadata = dict(prior) if isinstance(prior, dict) else {}
    metadata.update(
        version=1,
        raw_sha256=refinement["raw_sha256"],
        source_timestamp_ms=refinement["source_timestamp_ms"],
        evidence=refinement["evidence"],
        evidence_sha256=refinement["evidence_sha256"],
        independent_observations=False,
        applied_line_indices=applied,
        unaccepted_line_indices=unaccepted,
        collisions=collisions,
    )
    result["base_receipt_refinement"] = metadata
    return result


def load(raw, path, *, original=None, evidence_path=None, source_frame_path=None):
    """Load and apply a base sidecar, returning ``raw`` when it is absent."""

    path = Path(path)
    if not path.exists():
        return raw
    try:
        refinement = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid base receipt refinement artifact.") from exc
    return apply(raw, refinement, original=original, evidence_path=evidence_path,
                 source_frame_path=source_frame_path)


def _crop_geometry(line, geometry, pane_size=(810, 1080)):
    left, top, right, bottom = line["box"]
    width, height = pane_size
    x0 = max(0, int(left) - 148 - int(geometry["left"]))
    y0 = max(0, int(top) - int(geometry["top"]))
    x1 = min(width, int(right) - 148 + int(geometry["right"]))
    y1 = min(height, int(bottom) + int(geometry["bottom"]))
    if x1 <= x0 or y1 <= y0:
        raise ValueError("Base receipt crop geometry is empty.")
    return [x0, y0, x1, y1]


def _validated_geometries(geometries):
    if not isinstance(geometries, list) or len(geometries) != 3:
        raise ValueError("Base receipt refinement requires three crop geometries.")
    result=[];ids=set()
    for geometry in geometries:
        if not isinstance(geometry, dict):
            raise ValueError("Invalid base receipt crop geometry.")
        if not isinstance(geometry.get("id"), str) or not geometry["id"] or geometry["id"] in ids:
            raise ValueError("Invalid or duplicate base receipt crop geometry id.")
        ids.add(geometry["id"])
        for key in ("left", "right", "top", "bottom"):
            value=geometry.get(key)
            if type(value) is not int or value < 0 and key in ("left", "right"):
                raise ValueError("Invalid base receipt crop geometry padding.")
        result.append(dict(geometry))
    return result


def _selected_frames(root, start_ms, end_ms, frame_ids=None):
    root = Path(root)
    wanted = set(frame_ids or ())
    report_path = root / "report.json"
    selected = []
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        frames = report.get("frames", [])
        for frame in frames:
            frame_id = frame.get("id")
            timestamp = frame.get("source_timestamp_ms")
            if not isinstance(frame_id, str) or type(timestamp) is not int:
                continue
            if not start_ms <= timestamp < end_ms or (wanted and frame_id not in wanted):
                continue
            selected.append((frame_id, timestamp, root / "neural" / (frame_id + ".json"), frame.get("evidence")))
    else:
        for path in sorted((root / "neural").glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            frame_id = path.stem
            timestamp = raw.get("source_timestamp_ms")
            if type(timestamp) is int and start_ms <= timestamp < end_ms and (not wanted or frame_id in wanted):
                selected.append((frame_id, timestamp, path, raw.get("source_frame_evidence")))
    selected.sort(key=lambda item: (item[1], item[0]))
    if wanted:
        found = {frame_id for frame_id, _, _, _ in selected}
        missing = sorted(wanted - found)
        if missing:
            raise ValueError("Requested frame is outside the bounded selection: " + ", ".join(missing))
    return selected


def generate(root, *, start_ms, end_ms, frame_ids=None, geometries=DEFAULT_CROP_GEOMETRIES,
             reader=None, model_dir=".local/models/rapidocr"):
    """Generate sidecars only for the explicitly bounded neural frame range.

    The function is intentionally separate from cache loading.  Calling it
    runs OCR for selected frames; callers should inspect the generated views
    before wiring them into a report.  Existing sidecars are left untouched.
    """

    root = Path(root)
    if type(start_ms) is not int or type(end_ms) is not int or end_ms <= start_ms:
        raise ValueError("Base receipt generation requires a non-empty time range.")
    geometries = _validated_geometries([dict(geometry) for geometry in geometries])
    selected = _selected_frames(root, start_ms, end_ms, frame_ids)
    destination = root / REFINEMENT_DIR
    destination.mkdir(parents=True, exist_ok=True)
    summary = dict(stage="base_receipt_refinement", start_ms=start_ms, end_ms=end_ms,
                   selected_frames=len(selected), written=0, skipped_existing=0,
                   ocr_views=0)
    if not selected:
        return summary
    if reader is None:
        from .vision import NeuralReader
        reader = NeuralReader(model_dir)

    for frame_id, timestamp, raw_path, source_frame_evidence in selected:
        if not raw_path.is_file():
            raise ValueError(f"Missing neural observation: {frame_id}")
        target = destination / raw_path.name
        if target.exists():
            summary["skipped_existing"] += 1
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        evidence_path = root / raw.get("evidence", "")
        source_frame_path = root / (source_frame_evidence or "")
        if raw.get("source_timestamp_ms") != timestamp:
            raise ValueError("Base receipt source timestamp mismatch.")
        if not evidence_path.is_file():
            raise ValueError("Base receipt gameplay evidence is missing.")
        evidence_sha256 = file_fingerprint(evidence_path)
        if not source_frame_evidence or not source_frame_path.is_file():
            raise ValueError("Base receipt source frame evidence is missing.")
        if file_fingerprint(source_frame_path) != raw.get("source_frame_sha256"):
            raise ValueError("Base receipt source frame hash mismatch.")
        if not raw.get("gameplay_sha256") or "model_sha256" not in raw or "engine_fingerprint" not in raw:
            raise ValueError("Base receipt source provenance is incomplete.")
        if gameplay_fingerprint(evidence_path) != raw["gameplay_sha256"]:
            raise ValueError("Base receipt gameplay pixels changed.")

        candidates = [
            (index, line) for index, line in enumerate(raw.get("lines", []))
            if isinstance(line, dict) and isinstance(line.get("box"), list)
            and within(line, RECEIPT_BAND)
        ]
        observations = []
        images = []
        view_metadata = []
        if candidates:
            with reader.Image.open(evidence_path) as opened:
                pane = opened.convert("RGB")
                if pane.size != (810, 1080):
                    raise ValueError("Base receipt generation requires gameplay-pane evidence.")
                if hashlib.sha256(pane.tobytes()).hexdigest() != raw["gameplay_sha256"]:
                    raise ValueError("Base receipt gameplay pixels changed.")
                for index, line in candidates:
                    for geometry in geometries:
                        crop_box = _crop_geometry(line, geometry, pane.size)
                        crop = pane.crop(tuple(crop_box))
                        images.append(reader.np.array(crop)[:, :, ::-1])
                        view_metadata.append((index, geometry, crop_box, list(line["box"])))
            result = reader.engine.text_rec(reader.TextRecInput(img=images))
            texts = list(getattr(result, "txts", []))
            scores = list(getattr(result, "scores", []))
            if len(texts) != len(images) or len(scores) != len(images):
                raise ValueError("Base receipt OCR returned an incomplete view set.")
            by_line = {}
            for (index, geometry, crop_box, source_box), text, score in zip(view_metadata, texts, scores):
                confidence = float(score) * 100 if float(score) <= 1 else float(score)
                if not isinstance(text, str) or not math.isfinite(confidence):
                    raise ValueError("Base receipt OCR returned an invalid view.")
                by_line.setdefault(index, []).append(dict(
                    text=text,
                    confidence=round(confidence, 4),
                    crop_geometry=dict(id=geometry["id"], source_box=source_box,
                                       crop_box=crop_box, coordinate_space="gameplay_pane"),
                ))
            observations = [dict(index=index, source_box=list(line["box"]), views=by_line[index])
                            for index, line in candidates]

        sidecar = dict(
            version=1,
            stage="base_receipt_refinement",
            source_frame_id=frame_id,
            source_timestamp_ms=timestamp,
            evidence=raw["evidence"],
            evidence_sha256=evidence_sha256,
            source_frame_evidence=source_frame_evidence,
            source_frame_sha256=raw["source_frame_sha256"],
            gameplay_sha256=raw["gameplay_sha256"],
            raw_sha256=fingerprint(raw),
            source_model_sha256=raw["model_sha256"],
            source_engine_fingerprint=raw["engine_fingerprint"],
            refinement_model_sha256=getattr(reader, "models", {}),
            refinement_engine_fingerprint=getattr(reader, "fingerprint", None),
            # Keep the conventional names too, while the explicit source and
            # refinement names above make provenance unambiguous.
            model_sha256=getattr(reader, "models", {}),
            engine_fingerprint=getattr(reader, "fingerprint", None),
            independent_observations=False,
            selection=dict(start_ms=start_ms, end_ms=end_ms, frame_id=frame_id),
            crop_geometries=[dict(geometry) for geometry in geometries],
            lines=observations,
        )
        _write_json(target, sidecar)
        summary["written"] += 1
        summary["ocr_views"] += sum(len(item["views"]) for item in observations)
    return summary


# Keep the refinement-oriented name used by the other bounded CLI modules;
# ``generate`` remains the descriptive API for callers.
refine = generate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="recording analysis directory")
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--frame-id", action="append", dest="frame_ids", default=[])
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"))
    args = parser.parse_args(argv)
    summary = generate(args.output, start_ms=args.start_ms, end_ms=args.end_ms,
                       frame_ids=args.frame_ids, model_dir=args.model_dir)
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
