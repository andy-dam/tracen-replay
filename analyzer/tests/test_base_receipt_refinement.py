import copy
import hashlib
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from PIL import Image

from tracen_replay.base_receipt_refinement import (
    DEFAULT_CROP_GEOMETRIES,
    REFINEMENT_DIR,
    apply,
    fingerprint,
    generate,
    load,
)


@contextmanager
def workspace_temp():
    root=Path(".local/test-runs")/uuid.uuid4().hex
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _raw(root, frame_id="frame", timestamp=100, text="Energy went down by 8."):
    evidence_name=frame_id+".png"
    evidence_path=root/evidence_name
    source_name=frame_id+".jpg"
    source_path=root/source_name
    image=Image.new("RGB",(810,1080),"white")
    image.save(evidence_path)
    source_path.write_bytes(("source-frame-"+frame_id).encode())
    return dict(
        lines=[dict(text=text, confidence=99, box=[315, 820, 620, 850])],
        regions={},
        header="",
        current_grid=False,
        result_grid=False,
        source_timestamp_ms=timestamp,
        evidence=evidence_name,
        source_frame_sha256=_file_hash(source_path),
        gameplay_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
        model_sha256={"base": "model"},
        engine_fingerprint="base-engine",
    )


def _refinement(raw, evidence_path, source_frame_path, text="Energy went down by 18.", confidence=98):
    views=[]
    for geometry in DEFAULT_CROP_GEOMETRIES:
        views.append(dict(
            text=text,
            confidence=confidence,
            crop_geometry=dict(
                id=geometry["id"],
                source_box=list(raw["lines"][0]["box"]),
                crop_box=[
                    max(0,raw["lines"][0]["box"][0]-148-geometry["left"]),
                    max(0,raw["lines"][0]["box"][1]-geometry["top"]),
                    min(810,raw["lines"][0]["box"][2]-148+geometry["right"]),
                    min(1080,raw["lines"][0]["box"][3]+geometry["bottom"]),
                ],
                coordinate_space="gameplay_pane",
            ),
        ))
    return dict(
        version=1,
        stage="base_receipt_refinement",
        source_frame_id="frame",
        source_timestamp_ms=raw["source_timestamp_ms"],
        evidence=raw["evidence"],
        evidence_sha256=_file_hash(evidence_path),
        source_frame_evidence=Path(source_frame_path).name,
        source_frame_sha256=raw["source_frame_sha256"],
        gameplay_sha256=raw["gameplay_sha256"],
        raw_sha256=fingerprint(raw),
        source_model_sha256=raw["model_sha256"],
        source_engine_fingerprint=raw["engine_fingerprint"],
        refinement_model_sha256={"reread": "model"},
        refinement_engine_fingerprint="reread-engine",
        independent_observations=False,
        crop_geometries=[dict(geometry) for geometry in DEFAULT_CROP_GEOMETRIES],
        lines=[dict(index=0, source_box=list(raw["lines"][0]["box"]), views=views)],
    )


class _FakeInput:
    def __init__(self, img):
        self.img=img


class _FakePane:
    size=(810,1080)

    def __init__(self,pixels):
        self.pixels=pixels

    def __enter__(self):
        return self

    def __exit__(self,*args):
        return False

    def convert(self,mode):
        return self

    def tobytes(self):
        return self.pixels

    def crop(self,box):
        return self


class _FakeImage:
    @staticmethod
    def open(path):
        return _FakePane(Path(path).read_bytes())


class _FakeArray:
    def __getitem__(self, key):
        return self


class _FakeNumpy:
    @staticmethod
    def array(value):
        return _FakeArray()


class _FakeEngine:
    def __init__(self):
        self.calls=[]

    def text_rec(self, request):
        self.calls.append(len(request.img))
        return SimpleNamespace(
            txts=["Energy went down by 18."] * len(request.img),
            scores=[0.98] * len(request.img),
        )


class _FakeReader:
    Image=Image
    np=_FakeNumpy
    TextRecInput=_FakeInput
    models={"reread": "model"}
    fingerprint="reread-engine"

    def __init__(self):
        self.engine=_FakeEngine()


class BaseReceiptRefinementTests(unittest.TestCase):
    def test_valid_text_replacement_preserves_immutable_source_and_one_frame_proof(self):
        with workspace_temp() as root:
            raw=_raw(root)
            before=copy.deepcopy(raw)
            extra=_refinement(raw,root/raw["evidence"],root/"frame.jpg")

            result=apply(raw,extra,evidence_path=root/raw["evidence"],source_frame_path=root/"frame.jpg")

            self.assertEqual(raw,before)
            line=result["lines"][0]
            self.assertEqual(line["text"],"Energy went down by 18.")
            self.assertEqual(line["original_text"],"Energy went down by 8.")
            self.assertEqual(line["original_confidence"],99)
            self.assertEqual(line["confidence"],98)
            self.assertEqual(len(line["receipt_crop_views"]),3)
            self.assertTrue(result["base_receipt_refinement"]["independent_observations"] is False)
            self.assertEqual(result["base_receipt_refinement"]["applied_line_indices"],[0])
            self.assertEqual(result["base_receipt_refinement"]["collisions"],[])

    def test_load_is_optional_and_uses_separate_sidecar_path(self):
        with workspace_temp() as root:
            raw=_raw(root)
            sidecar=root/REFINEMENT_DIR/"frame.json"
            sidecar.parent.mkdir()
            sidecar.write_text(json.dumps(_refinement(raw,root/raw["evidence"],root/"frame.jpg")),encoding="utf-8")

            self.assertIs(load(raw,root/REFINEMENT_DIR/"missing.json",evidence_path=root/raw["evidence"],source_frame_path=root/"frame.jpg"),raw)
            result=load(raw,sidecar,evidence_path=root/raw["evidence"],source_frame_path=root/"frame.jpg")
            self.assertEqual(result["lines"][0]["text"],"Energy went down by 18.")

    def test_provenance_mismatch_is_rejected_before_any_line_change(self):
        with workspace_temp() as root:
            raw=_raw(root)
            extra=_refinement(raw,root/raw["evidence"],root/"frame.jpg")
            changed=copy.deepcopy(raw)
            changed["lines"][0]["text"]="Energy went down by 9."
            with self.assertRaisesRegex(ValueError,"source mismatch"):
                apply(changed,extra,evidence_path=root/raw["evidence"],source_frame_path=root/"frame.jpg")

            root_path=root/raw["evidence"]
            root_path.write_bytes(b"changed evidence")
            with self.assertRaisesRegex(ValueError,"evidence changed"):
                apply(raw,extra,evidence_path=root_path,source_frame_path=root/"frame.jpg")

    def test_recorded_crop_box_must_match_declared_geometry_and_source_box(self):
        with workspace_temp() as root:
            raw=_raw(root)
            extra=_refinement(raw,root/raw["evidence"],root/"frame.jpg")
            extra["lines"][0]["views"][2]["crop_geometry"]["crop_box"][1]+=1

            with self.assertRaisesRegex(ValueError,"crop geometry mismatch"):
                apply(raw,extra,evidence_path=root/raw["evidence"],source_frame_path=root/"frame.jpg")

    def test_malformed_or_preview_text_is_not_promoted(self):
        with workspace_temp() as root:
            raw=_raw(root,text="Speed +5")
            extra=_refinement(raw,root/raw["evidence"],root/"frame.jpg",text="Speed +6")

            result=apply(raw,extra,evidence_path=root/raw["evidence"],source_frame_path=root/"frame.jpg")

            self.assertEqual(result["lines"],raw["lines"])
            self.assertEqual(result["base_receipt_refinement"]["applied_line_indices"],[])
            self.assertEqual(result["base_receipt_refinement"]["unaccepted_line_indices"],[0])

    def test_prior_refinement_collision_is_recorded_without_overwrite(self):
        with workspace_temp() as root:
            original=_raw(root)
            current=copy.deepcopy(original)
            current["lines"][0].update(confidence=97,refined=True)
            extra=_refinement(original,root/original["evidence"],root/"frame.jpg")

            result=apply(current,extra,original=original,evidence_path=root/original["evidence"],source_frame_path=root/"frame.jpg")

            self.assertEqual(result["lines"][0],current["lines"][0])
            collision=result["base_receipt_refinement"]["collisions"][0]
            self.assertEqual(collision["index"],0)
            self.assertEqual(collision["reason"],"prior_refinement")
            self.assertEqual(collision["candidate_text"],"Energy went down by 18.")
            self.assertEqual(result["base_receipt_refinement"]["applied_line_indices"],[])

    def test_occluded_prior_line_remains_abstained_on_collision(self):
        with workspace_temp() as root:
            original=_raw(root)
            current=copy.deepcopy(original)
            current["lines"][0].update(confidence=0,overlay_occluded=True)
            extra=_refinement(original,root/original["evidence"],root/"frame.jpg")

            result=apply(current,extra,original=original,evidence_path=root/original["evidence"],source_frame_path=root/"frame.jpg")

            self.assertEqual(result["lines"][0]["confidence"],0)
            self.assertTrue(result["lines"][0]["overlay_occluded"])
            self.assertEqual(len(result["base_receipt_refinement"]["collisions"]),1)

    def test_generation_is_bounded_to_requested_frames_and_records_crop_geometry(self):
        with workspace_temp() as root:
            neural=root/"neural"
            neural.mkdir()
            frames=[]
            for frame_id,timestamp in (("before",99),("inside",150),("at-end",200)):
                raw=_raw(root,frame_id=frame_id,timestamp=timestamp)
                (neural/(frame_id+".json")).write_text(json.dumps(raw),encoding="utf-8")
                frames.append(dict(id=frame_id,source_timestamp_ms=timestamp,evidence=frame_id+".jpg"))
            (root/"report.json").write_text(json.dumps(dict(frames=frames)),encoding="utf-8")
            reader=_FakeReader()

            summary=generate(root,start_ms=100,end_ms=200,reader=reader)

            target=root/REFINEMENT_DIR/"inside.json"
            self.assertEqual(summary["selected_frames"],1)
            self.assertEqual(summary["written"],1)
            self.assertEqual(summary["ocr_views"],3)
            self.assertEqual(reader.engine.calls,[3])
            self.assertTrue(target.is_file())
            self.assertFalse((root/REFINEMENT_DIR/"before.json").exists())
            self.assertFalse((root/REFINEMENT_DIR/"at-end.json").exists())
            sidecar=json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(sidecar["source_frame_id"],"inside")
            self.assertEqual(sidecar["selection"],dict(start_ms=100,end_ms=200,frame_id="inside"))
            self.assertIs(sidecar["independent_observations"],False)
            self.assertEqual(len(sidecar["lines"]),1)
            self.assertEqual(
                [view["crop_geometry"]["id"] for view in sidecar["lines"][0]["views"]],
                [geometry["id"] for geometry in DEFAULT_CROP_GEOMETRIES],
            )
            self.assertEqual(sidecar["lines"][0]["views"][2]["crop_geometry"]["crop_box"],
                             [165,822,474,848])
            self.assertNotIn("accepted", sidecar["lines"][0])


if __name__ == "__main__":
    unittest.main()
