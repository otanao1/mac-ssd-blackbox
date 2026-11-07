"""Verification must never start a service with bundled periodic targets."""
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from blackbox.configuration import load
from blackbox.bundle import helper_path
from tools import verify_monitor_bundle as verify_tool

ROOT = Path(__file__).resolve().parents[1]
DEV_ID = 'io.github.otanao1.blackbox-viewer.dev.issue38'


class VerificationConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.common = self.root/'common'
        shutil.copytree(ROOT/'config', self.common)
        self.private = self.root/'private'
        self.private.mkdir()
        (self.private/'local.json').write_text('{}')
        self.fixture = self.root/'fixture'
        self.fixture.mkdir()

    def test_all_real_common_conf_names_are_disabled(self):
        self.assertGreater(len(load(self.common, self.private)['targets']['L3']), 0)
        verify_tool.prepare_test_configuration(self.common, self.private, self.fixture)
        self.assertEqual(verify_tool.require_no_periodic_targets(self.common, self.private), {'L2': 0, 'L3': 0})
        for layer in ('l2', 'l3'):
            for path in (self.common/layer/'conf.d').glob('*.conf'):
                self.assertEqual((self.private/layer/'conf.d'/path.name).read_text(), '')

    def test_json_targets_and_private_extra_conf_are_also_disabled(self):
        local = {'targets': {'L2': ['/synthetic/private'], 'L3': ['/synthetic/private']},
                 'incident_thresholds': {'low_space': 123}}
        (self.private/'local.json').write_text(json.dumps(local))
        settings = self.common/'settings.json'
        common = json.loads(settings.read_text())
        common['targets'] = {'L2': ['/synthetic/common'], 'L3': ['/synthetic/common']}
        settings.write_text(json.dumps(common))
        for layer in ('l2', 'l3'):
            directory = self.private/layer/'conf.d'; directory.mkdir(parents=True)
            (directory/'extra.conf').write_text('/synthetic/extra\n')
        verify_tool.prepare_test_configuration(self.common, self.private, self.fixture)
        cfg = load(self.common, self.private)
        self.assertEqual(cfg['targets'], {'L2': [], 'L3': []})
        self.assertEqual(cfg['incident_thresholds']['low_space'], 123)
        self.assertEqual(cfg['l1_path'], str(self.fixture))

    def test_update_only_conf_names_are_masked_before_first_start(self):
        candidate = self.root/'candidate-common'
        shutil.copytree(self.common, candidate)
        for layer in ('l2', 'l3'):
            (candidate/layer/'conf.d/new-release.conf').write_text('/synthetic/new-release/*\n')
        verify_tool.prepare_test_configuration(self.common, self.private, self.fixture, other_common=[candidate])
        for common in (self.common, candidate):
            self.assertEqual(verify_tool.require_no_periodic_targets(common, self.private), {'L2': 0, 'L3': 0})
        # Simulate the updater replacing the common directory, then rollback.
        shutil.rmtree(self.common); shutil.copytree(candidate, self.common)
        self.assertEqual(verify_tool.require_no_periodic_targets(self.common, self.private), {'L2': 0, 'L3': 0})

    def test_unmasked_late_conf_is_rejected(self):
        verify_tool.prepare_test_configuration(self.common, self.private, self.fixture)
        (self.common/'l3/conf.d/late.conf').write_text('/synthetic/late/*\n')
        with self.assertRaisesRegex(ValueError, 'zero periodic'):
            verify_tool.require_no_periodic_targets(self.common, self.private)

    def test_guard_survives_python_optimization(self):
        result = subprocess.run([sys.executable, '-O', '-c',
            'import sys;from tools.verify_monitor_bundle import require_no_periodic_targets;'
            'require_no_periodic_targets(sys.argv[1],sys.argv[2])', str(self.common), str(self.private)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('zero periodic', result.stderr)

    def test_private_symlink_is_not_overwritten(self):
        directory = self.private/'l3/conf.d'; directory.mkdir(parents=True)
        sentinel = self.root/'sentinel'; sentinel.write_text('unchanged')
        (directory/'dev-ai.conf').symlink_to(sentinel)
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            verify_tool.prepare_test_configuration(self.common, self.private, self.fixture)
        self.assertEqual(sentinel.read_text(), 'unchanged')

    def fake_app(self, name):
        app = self.root/name
        binary = helper_path(app); binary.parent.mkdir(parents=True)
        binary.write_text('synthetic executable; must never run')
        resources = binary.parents[1]/'Resources'
        resources.mkdir()
        (resources/'runtime-manifest.json').write_text('{"files": {}}')
        shutil.copytree(self.common, resources/'runtime/config')
        return app

    def test_both_service_entry_points_recheck_targets_before_start(self):
        app = self.fake_app('input.app'); candidate = self.fake_app('candidate.app')
        prepare = verify_tool.prepare_test_configuration
        for launchd in (False, True):
            with self.subTest(launchd=launchd):
                calls = []
                def fake_install(args, **kwargs):
                    calls.append(list(map(str, args)))
                    self.assertIn('install', args)
                    def arg(name): return Path(args[args.index(name)+1])
                    private, runtime = arg('--config-dir'), arg('--runtime')
                    private.mkdir(); runtime.mkdir()
                    shutil.copytree(self.common, runtime/'config')
                    (private/'local.json').write_text('{}')
                    (runtime/'launchd.plist').write_bytes(plistlib.dumps({'Label': DEV_ID+'.monitor'}))
                    (arg('--storage')/'blackbox.sqlite3').touch()
                    return subprocess.CompletedProcess(args, 0, json.dumps({'bundle_id': DEV_ID}), '')
                def prepare_then_corrupt(common, private, fixture, **kwargs):
                    prepare(common, private, fixture, **kwargs)
                    (Path(common)/'l2/conf.d/late.conf').write_text('/synthetic/must-not-scan/*\n')
                with patch.object(verify_tool, 'verify_bundle', return_value={'CFBundleIdentifier': DEV_ID}), \
                     patch.object(verify_tool, 'mounted_volume', return_value={'mount': '/synthetic'}), \
                     patch.object(verify_tool.subprocess, 'run', side_effect=fake_install), \
                     patch.object(verify_tool.subprocess, 'Popen') as spawn, \
                     patch.object(verify_tool, 'prepare_test_configuration', side_effect=prepare_then_corrupt):
                    with self.assertRaisesRegex(ValueError, 'zero periodic'):
                        if launchd: verify_tool.verify_launchd(app, candidate)
                        else: verify_tool.verify(app, candidate)
                spawn.assert_not_called()
                self.assertEqual(len(calls), 1)  # install only; no start/bootstrap/update


if __name__ == '__main__':
    unittest.main()
