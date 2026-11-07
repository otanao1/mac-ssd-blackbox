"""Packaging profiles never execute protected-area scans or signing commands."""
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from blackbox.du import DuScanner
from tools.build_monitor_bundle import prepare_runtime_profile


class BundleProfileTest(unittest.TestCase):
    def test_standard_profile_unchanged_and_production_rejects_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            self.assertEqual(prepare_runtime_profile(runtime, 'io.github.otanao1.blackbox-viewer'), 'standard')
            self.assertEqual(list(runtime.iterdir()), [])
            for ident in ['io.github.otanao1.blackbox-viewer', 'io.github.otanao1.blackbox-viewer.dev.issue38.fda.extra']:
                with self.subTest(ident=ident), self.assertRaisesRegex(ValueError, 'DEV bundle ID'):
                    prepare_runtime_profile(runtime, ident, True)
            self.assertEqual(list(runtime.iterdir()), [])

    def test_dev_profile_explicit_import_and_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp); (runtime/'blackbox').mkdir()
            main = runtime/'blackbox/__main__.py'; main.write_text('from .store import Store\n')
            self.assertEqual(prepare_runtime_profile(runtime,
                'io.github.otanao1.blackbox-viewer.dev.issue38.fda', True), 'dev-fda-access-only')
            self.assertIn('from . import _fda_privacy_adapter', main.read_text())
            self.assertTrue((runtime/'blackbox/_fda_privacy_adapter.py').is_file())

    def load_adapter(self):
        path = Path(__file__).resolve().parents[1]/'tools/fixtures/fda_access_only.py'
        spec = importlib.util.spec_from_file_location('blackbox._test_fda_adapter', path)
        module = importlib.util.module_from_spec(spec)
        original = DuScanner._du
        self.addCleanup(setattr, DuScanner, '_du', original)
        spec.loader.exec_module(module)
        return module

    def test_access_adapter_discards_capacity_and_collapses_private_error(self):
        adapter = self.load_adapter()
        for code, stderr in [(0, b''), (1, b'/private/child-name: Operation not permitted\n')]:
            child = Mock(); child.stderr = io.BytesIO(stderr); child.returncode = code; child.poll.return_value = code
            scanner = Mock(); scanner.bad = set()
            with patch.object(adapter.subprocess, 'Popen', return_value=child) as popen:
                adapter.access_only(scanner, ['/Library/Bluetooth'], 1)
            self.assertIs(popen.call_args.kwargs['stdout'], adapter.subprocess.DEVNULL)
            if code == 0:
                scanner.record.assert_called_once_with('/Library/Bluetooth', 'directory', size=None)
            else:
                scanner.record.assert_called_once_with('/Library/Bluetooth', 'unknown', status='error', error='EPERM')
            self.assertNotIn('child-name', str(scanner.record.call_args))

    def test_access_adapter_rejects_other_targets_before_child(self):
        adapter = self.load_adapter()
        with patch.object(adapter.subprocess, 'Popen') as child, self.assertRaises(ValueError):
            adapter.access_only(Mock(), ['/synthetic/not-approved'], 1)
        child.assert_not_called()
