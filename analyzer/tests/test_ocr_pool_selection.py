import unittest
from unittest.mock import patch

from tracen_replay import full_recording


class OcrPoolSelectionTests(unittest.TestCase):
    def test_directml_readers_get_their_own_processes(self):
        self.assertEqual(full_recording.ocr_pool_for_device('dml'), 'process')
        self.assertEqual(full_recording.ocr_pool_for_device('cpu'), 'thread')

    def test_default_follows_the_detected_device(self):
        with patch.object(full_recording, 'OCR_DEVICE', 'dml'):
            self.assertEqual(full_recording.ocr_pool_for_device(), 'process')
        with patch.object(full_recording, 'OCR_DEVICE', 'cpu'):
            self.assertEqual(full_recording.ocr_pool_for_device(), 'thread')

    def test_cuda_readers_get_their_own_processes_too(self):
        self.assertEqual(full_recording.ocr_pool_for_device('cuda'), 'process')

    def test_device_selection_falls_back_to_cpu_without_a_gpu_provider(self):
        import types
        from tracen_replay import vision

        def fake_runtime(*providers):
            return types.SimpleNamespace(get_available_providers=lambda: list(providers))

        cases = [
            ({'TRACEN_REPLAY_OCR_DEVICE': 'auto'}, ('CPUExecutionProvider',), 'cpu'),
            ({'TRACEN_REPLAY_OCR_DEVICE': 'auto'}, ('DmlExecutionProvider', 'CPUExecutionProvider'), 'dml'),
            ({'TRACEN_REPLAY_OCR_DEVICE': 'auto'}, ('CUDAExecutionProvider', 'CPUExecutionProvider'), 'cuda'),
            ({'TRACEN_REPLAY_OCR_DEVICE': 'auto'}, ('DmlExecutionProvider', 'CUDAExecutionProvider'), 'dml'),
            ({}, ('CPUExecutionProvider',), 'cpu'),
            ({'TRACEN_REPLAY_OCR_DEVICE': 'cpu'}, ('DmlExecutionProvider',), 'cpu'),
            ({'TRACEN_REPLAY_OCR_DEVICE': 'cuda'}, ('CUDAExecutionProvider',), 'cuda'),
        ]
        for env, providers, expected in cases:
            with patch.dict('os.environ', env, clear=False), patch.dict('sys.modules', {'onnxruntime': fake_runtime(*providers)}):
                if 'TRACEN_REPLAY_OCR_DEVICE' not in env:
                    import os
                    os.environ.pop('TRACEN_REPLAY_OCR_DEVICE', None)
                self.assertEqual(vision.ocr_device(), expected, (env, providers))
        for env, providers in [({'TRACEN_REPLAY_OCR_DEVICE': 'dml'}, ('CPUExecutionProvider',)),
                               ({'TRACEN_REPLAY_OCR_DEVICE': 'cuda'}, ('DmlExecutionProvider',)),
                               ({'TRACEN_REPLAY_OCR_DEVICE': 'tpu'}, ('CPUExecutionProvider',))]:
            with patch.dict('os.environ', env, clear=False), patch.dict('sys.modules', {'onnxruntime': fake_runtime(*providers)}):
                with self.assertRaises(ValueError):
                    vision.ocr_device()

    def test_engine_parameters_only_carry_the_cuda_switch_on_cuda(self):
        # PARAMS is hashed into every reader's cache fingerprint: the DirectML
        # and CPU parameter sets must stay byte-for-byte what earlier runs used.
        from tracen_replay import vision
        dml, cpu, cuda = (vision.engine_params(d) for d in ('dml', 'cpu', 'cuda'))
        self.assertEqual(dml, {'Global.use_cls': False, 'Det.limit_side_len': 736, 'Det.limit_type': 'max',
                               'EngineConfig.onnxruntime.intra_op_num_threads': 2,
                               'EngineConfig.onnxruntime.inter_op_num_threads': 1, 'Global.log_level': 'warning',
                               'EngineConfig.onnxruntime.use_dml': True})
        self.assertEqual(cpu, dict(dml, **{'EngineConfig.onnxruntime.use_dml': False}))
        self.assertEqual(cuda, dict(cpu, **{'EngineConfig.onnxruntime.use_cuda': True}))
        self.assertEqual(vision.PARAMS, vision.engine_params(vision.OCR_DEVICE))

    def test_unknown_pool_is_rejected(self):
        with self.assertRaises(ValueError):
            full_recording.analyze_frames(dict(frames=[]), full_recording.Path('.'), pool='fork')


if __name__ == '__main__':
    unittest.main()
