import hashlib
import unittest
from unittest.mock import patch

from PIL import Image

from tests.test_gameplay import workspace_temp
from tracen_replay.race_quantity_refinement import _validate_gameplay_pixels


def fixture(root):
    source_path, gameplay_path = root / 'source.png', root / 'gameplay.png'
    source = Image.new('RGB', (1106, 1080), (230, 210, 200))
    source.putpixel((150, 3), (1, 2, 3))
    gameplay = source.crop((148, 0, 958, 1080))
    source.save(source_path)
    gameplay.save(gameplay_path)
    raw = dict(evidence='gameplay.png', gameplay_sha256=hashlib.sha256(gameplay.tobytes()).hexdigest())
    observation = dict(gameplay_evidence='gameplay.png',
                       gameplay_file_sha256=hashlib.sha256(gameplay_path.read_bytes()).hexdigest())
    return source_path, gameplay_path, raw, observation


class RacePixelValidationCacheTests(unittest.TestCase):
    def test_repeated_views_reuse_decode_but_still_hash_current_files(self):
        with workspace_temp() as root:
            args = fixture(root)
            cache = {}
            with patch('tracen_replay.race_quantity_refinement.Image.open', wraps=Image.open) as opened:
                _validate_gameplay_pixels(*args, root, cache)
                _validate_gameplay_pixels(*args, root, cache)
                self.assertEqual(opened.call_count, 2)
            self.assertEqual(len(cache), 1)
            # A new validation context decodes again; no global cache survives.
            with patch('tracen_replay.race_quantity_refinement.Image.open', wraps=Image.open) as opened:
                _validate_gameplay_pixels(*args, root, {})
                self.assertEqual(opened.call_count, 2)

    def test_changed_source_at_same_path_cannot_reuse_validation(self):
        with workspace_temp() as root:
            args = fixture(root)
            cache = {}
            _validate_gameplay_pixels(*args, root, cache)
            with Image.open(args[0]) as image:
                changed = image.convert('RGB')
            changed.putpixel((150, 3), (9, 8, 7))
            changed.save(args[0])
            with self.assertRaisesRegex(ValueError, 'crop pixels changed'):
                _validate_gameplay_pixels(*args, root, cache)

    def test_changed_gameplay_with_updated_file_hash_still_requires_matching_crop(self):
        with workspace_temp() as root:
            source, gameplay, raw, observation = fixture(root)
            cache = {}
            _validate_gameplay_pixels(source, gameplay, raw, observation, root, cache)
            with Image.open(gameplay) as image:
                changed = image.convert('RGB')
            changed.putpixel((2, 3), (9, 8, 7))
            changed.save(gameplay)
            observation['gameplay_file_sha256'] = hashlib.sha256(gameplay.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, 'crop pixels changed'):
                _validate_gameplay_pixels(source, gameplay, raw, observation, root, cache)

    def test_pixel_and_evidence_bindings_are_rechecked_on_cache_hit(self):
        with workspace_temp() as root:
            source, gameplay, raw, observation = fixture(root)
            cache = {}
            _validate_gameplay_pixels(source, gameplay, raw, observation, root, cache)
            for changed in (dict(raw, gameplay_sha256='0' * 64), dict(raw, evidence='other.png')):
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    _validate_gameplay_pixels(source, gameplay, changed, observation, root, cache)

    def test_failed_validation_does_not_populate_cache(self):
        with workspace_temp() as root:
            source, gameplay, raw, observation = fixture(root)
            cache = {}
            with self.assertRaises(ValueError):
                _validate_gameplay_pixels(source, gameplay, dict(raw, gameplay_sha256='wrong'), observation, root, cache)
            self.assertEqual(cache, {})


if __name__ == '__main__':
    unittest.main()
