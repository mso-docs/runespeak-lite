"""Failure/retry checks for first-run setup, without downloads or installs."""
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('linux_launcher', Path(__file__).resolve().parents[1]/'scripts/linux-launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LinuxPackageTests(unittest.TestCase):
    def test_failed_setup_can_retry_and_ready_install_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)/'runtime'
            wheel = Path(directory)/'app.whl'
            with patch.object(launcher.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'pip')):
                with self.assertRaises(subprocess.CalledProcessError):
                    launcher.install_runtime(runtime, wheel, io.StringIO())
            self.assertFalse((runtime/'.ready').exists())
            with patch.object(launcher.subprocess, 'run') as run:
                launcher.install_runtime(runtime, wheel, io.StringIO())
                self.assertTrue((runtime/'.ready').exists())
                self.assertTrue(any('--require-hashes' in call.args[0] for call in run.call_args_list))
                count = run.call_count
                launcher.install_runtime(runtime, wheel, io.StringIO())
                self.assertEqual(run.call_count, count)
