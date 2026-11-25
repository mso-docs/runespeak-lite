import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from runespeak import worker


class ThreadChoiceTests(unittest.TestCase):
    def test_leaves_most_cores_to_the_system(self):
        with patch.dict(os.environ, {'RUNESPEAK_THREADS': ''}):
            self.assertEqual(worker.synthesis_threads(12), 3)   # Intel i7 with 6 cores / 12 threads.
            self.assertEqual(worker.synthesis_threads(4), 2)
            self.assertEqual(worker.synthesis_threads(1), 1)

    def test_environment_override(self):
        with patch.dict(os.environ, {'RUNESPEAK_THREADS': '6'}):
            self.assertEqual(worker.synthesis_threads(12), 6)
        with patch.dict(os.environ, {'RUNESPEAK_THREADS': 'lots'}):
            self.assertEqual(worker.synthesis_threads(12), 3)


class WeightFormatTests(unittest.TestCase):
    def test_full_precision_on_intel_and_8_bit_on_arm(self):
        with patch.dict(os.environ, {'RUNESPEAK_QUANT': ''}):
            self.assertEqual(worker.weight_format('x86_64'), 'f32')
            self.assertEqual(worker.weight_format('AMD64'), 'f32')
            self.assertEqual(worker.weight_format('arm64'), 'q8')
            self.assertEqual(worker.weight_format('aarch64'), 'q8')

    def test_environment_override(self):
        with patch.dict(os.environ, {'RUNESPEAK_QUANT': 'q8'}):
            self.assertEqual(worker.weight_format('x86_64'), 'q8')


class WorkerProcessTests(unittest.TestCase):
    def test_worker_lowers_priority_and_caps_threads_before_loading(self):
        calls = []

        class FakeTTS:
            def __init__(self, *args, **kwargs):
                calls.append(('load', kwargs.get('quant')))

            def save(self, path, text, voice=None):
                Path(path).write_bytes(b'')
                calls.append(('save', text))

        fake = types.SimpleNamespace(TTS=FakeTTS, set_num_threads=lambda n: calls.append(('threads', n)))
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder) / 'job.json'
            job.write_text(json.dumps({'chunks': ['One.', 'Two.'], 'directory': folder}))
            with patch.dict(sys.modules, {'ptts': fake}), patch.object(sys, 'argv', ['worker', str(job)]), \
                    patch.object(worker, 'lower_priority', side_effect=lambda: calls.append(('nice',))), \
                    patch.object(worker, 'synthesis_threads', return_value=4), \
                    patch.object(worker, 'weight_format', return_value='q8'), \
                    patch('builtins.print'):
                worker.main()
        self.assertEqual(calls[:3], [('nice',), ('threads', 4), ('load', 'q8')])
        self.assertEqual([c[1] for c in calls if c[0] == 'save'], ['One.', 'Two.'])

    @unittest.skipIf(sys.platform == 'win32', 'POSIX niceness')
    def test_lower_priority_uses_nice_on_posix(self):
        with patch.object(os, 'nice') as nice:
            worker.lower_priority()
        nice.assert_called_once_with(10)


if __name__ == '__main__':
    unittest.main()
