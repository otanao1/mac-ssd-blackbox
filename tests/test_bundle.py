"""Issue #38: synthetic apps/storage; never call real launchctl or change FDA."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from blackbox import bundle, installation
from blackbox.runtime import lock

ROOT = Path(__file__).resolve().parents[1]


def fake_app(path, version='1'):
    helper = path/bundle.HELPER
    (helper/'Contents/MacOS').mkdir(parents=True)
    bundle.helper_path(path).write_text('synthetic executable')
    info = dict(CFBundleIdentifier='io.github.otanao1.blackbox-viewer.dev.issue38',
                CFBundleExecutable='Viewer', CFBundleVersion=version)
    (path/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    (helper/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
        CFBundleIdentifier=info['CFBundleIdentifier']+'.monitor', CFBundleExecutable='BlackboxMonitor')))
    source = helper/'Contents/Resources/runtime'
    for name in ('blackbox', 'config'):
        shutil.copytree(ROOT/name, source/name, ignore=shutil.ignore_patterns('__pycache__'))
    return source


class BundleTest(unittest.TestCase):
    def test_development_fda_identifier_is_consistent_for_lifecycle(self):
        identifier = installation.VIEWER_ID+'.dev.issue38.fda'
        self.assertEqual(installation.installation_label(dict(mode='bundle', bundle_id=identifier)),
                         identifier+'.monitor')
        for suffix in ('.dev.issue38.fda.extra', '.dev.issue38fdax', '.dev.issue38.fda\n'):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                installation.installation_label(dict(mode='bundle', bundle_id=installation.VIEWER_ID+suffix))
        self.assertEqual(installation.installation_label(dict(mode='bundle', bundle_id=installation.VIEWER_ID)),
                         installation.LABEL)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.app = self.root/'Viewer.app'
        self.source = fake_app(self.app)
        self.private = self.root/'private'
        self.runtime = self.root/'runtime'
        self.cli = self.root/'bin/blackbox'
        self.data = self.root/'data'
        self.data.mkdir()
        self.volume = dict(kind='disk', mount=str(self.root), filesystem='apfs', uuid='SYNTHETIC')
        self.mount = patch('blackbox.destination.mounted_volume', return_value=self.volume).start()
        self.addCleanup(patch.stopall)
        self.signature_patch = patch('blackbox.bundle.verify_bundle', return_value={'CFBundleIdentifier': 'io.github.otanao1.blackbox-viewer.dev.issue38'})
        self.signature = self.signature_patch.start()

    def install(self):
        with patch('blackbox.installation.sys.executable', str(bundle.helper_path(self.app))):
            return installation.install(self.source, self.data, self.root, private=self.private,
                runtime=self.runtime, cli=self.cli, app_bundle=self.app)

    def prepare_update(self):
        self.install()
        self.candidate = self.root/'candidate.app'
        fake_app(self.candidate, '2')
        self.stop = patch('blackbox.installation.stop_owned_job', return_value=False).start()
        self.launch = patch('blackbox.installation.launchctl').start()
        self.viewer = patch('blackbox.bundle.viewer_running', return_value=False).start()

    def test_bundle_install_uses_sealed_code_not_copied_python(self):
        result = self.install()
        self.assertEqual(result['mode'], 'bundle')
        self.assertFalse((self.runtime/'venv').exists())
        self.assertFalse((self.runtime/'blackbox').exists())
        self.assertTrue((self.runtime/'config/settings.json').exists())
        args = plistlib.loads((self.runtime/'launchd.plist').read_bytes())['ProgramArguments']
        self.assertEqual(args[0], str(bundle.helper_path(self.app)))
        self.assertIn('-m blackbox', self.cli.read_text())
        self.assertEqual(installation.uninstall_job(self.private, result)['ProgramArguments'], args)

    def test_shared_python_cannot_install_bundle_mode(self):
        with self.assertRaisesRegex(ValueError, 'own signed helper'):
            installation.install(self.source, self.data, self.root, private=self.private,
                runtime=self.runtime, cli=self.cli, app_bundle=self.app)
        self.assertFalse(self.runtime.exists())

    def test_development_helper_rejects_production_configuration_before_service_calls(self):
        from argparse import Namespace
        from blackbox.__main__ import installed
        from blackbox.configuration import DEFAULT_PRIVATE
        with patch('blackbox.__main__.sys.executable', str(bundle.helper_path(self.app))), \
             patch('blackbox.service.call') as call, self.assertRaisesRegex(ValueError, 'separate --config-dir'):
            installed(Namespace(config_dir=DEFAULT_PRIVATE, command='status'))
        call.assert_not_called()

    def test_invalid_signature_prevents_install(self):
        self.signature.side_effect = ValueError('bad signature')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse(self.runtime.exists())

    def test_uninstall_retains_app_configuration_and_database(self):
        self.install()
        inode = (self.data/'blackbox.sqlite3').stat().st_ino
        missing = subprocess.CompletedProcess([], 113, '',
            f'Could not find service "{installation.VIEWER_ID}.dev.issue38.monitor" in domain for user gui: {os.getuid()}')
        with patch('blackbox.installation.launchctl', return_value=missing):
            installation.uninstall(self.private)
        self.assertFalse(self.runtime.exists())
        self.assertTrue(self.app.exists())
        self.assertTrue((self.private/'local.json').exists())
        self.assertEqual((self.data/'blackbox.sqlite3').stat().st_ino, inode)

    def test_start_rejects_invalid_signature_before_launchctl(self):
        self.install()
        self.signature.side_effect = ValueError('changed')
        with patch('blackbox.installation.launchctl') as launch, self.assertRaises(ValueError):
            installation.control('start', self.private)
        launch.assert_not_called()

    def test_stopped_update_preserves_db_and_config_does_not_start(self):
        self.prepare_update()
        local = (self.private/'local.json').read_bytes()
        config = (self.runtime/'config/settings.json').read_bytes()
        inode = (self.data/'blackbox.sqlite3').stat().st_ino
        result = bundle._replace(self.private, self.candidate)
        self.assertFalse(result['restarted'])
        self.launch.assert_not_called()
        self.assertEqual(plistlib.loads((self.app/'Contents/Info.plist').read_bytes())['CFBundleVersion'], '2')
        self.assertEqual((self.private/'local.json').read_bytes(), local)
        self.assertEqual((self.runtime/'config/settings.json').read_bytes(), config)
        self.assertEqual((self.data/'blackbox.sqlite3').stat().st_ino, inode)
        self.assertTrue(Path(result['backup']).exists())

    def test_running_update_restarts_only_after_locks_release(self):
        self.prepare_update()
        self.stop.return_value = True
        def check(*args, **kwargs):
            for directory, name in ((self.private, 'service.lock'), (self.data, 'monitor.lock'), (self.data, 'scan.lock')):
                fd = lock(directory, name)
                os.close(fd)
        self.launch.side_effect = check
        result = bundle._replace(self.private, self.candidate)
        self.assertTrue(result['restarted'])
        self.launch.assert_called_once_with('bootstrap', installation.domain(), str(self.runtime/'launchd.plist'))

    def test_viewer_running_prevents_stop(self):
        self.prepare_update()
        self.viewer.return_value = True
        with self.assertRaisesRegex(ValueError, 'quit Viewer'):
            bundle._replace(self.private, self.candidate)
        self.stop.assert_not_called()

    def test_bad_candidate_prevents_stop(self):
        self.prepare_update()
        self.signature.side_effect = ValueError('candidate mismatch')
        with self.assertRaises(ValueError):
            bundle._replace(self.private, self.candidate)
        self.stop.assert_not_called()

    def test_unknown_job_prevents_replacement(self):
        self.prepare_update()
        self.stop.side_effect = ValueError('not owned')
        with self.assertRaises(ValueError):
            bundle._replace(self.private, self.candidate)
        self.assertFalse(self.app.with_name('Viewer.app.previous').exists())

    def test_replacement_failure_restores_old_app(self):
        self.prepare_update()
        original = Path.rename
        def rename(source, dest):
            if source.name == self.app.name and source.parent.name.startswith('.blackbox-update-'):
                raise OSError('synthetic replacement failure')
            return original(source, dest)
        with patch('pathlib.Path.rename', rename), self.assertRaises(OSError):
            bundle._replace(self.private, self.candidate)
        self.assertEqual(plistlib.loads((self.app/'Contents/Info.plist').read_bytes())['CFBundleVersion'], '1')
        self.assertTrue((self.data/'blackbox.sqlite3').exists())

    def test_backup_collision_prevents_stop(self):
        self.prepare_update()
        self.app.with_name('Viewer.app.previous').mkdir()
        with self.assertRaisesRegex(ValueError, 'backup exists'):
            bundle._replace(self.private, self.candidate)
        self.stop.assert_not_called()

    def test_explicit_rollback_uses_retained_signed_backup(self):
        self.prepare_update()
        first = bundle._replace(self.private, self.candidate)
        rolled = bundle._replace(self.private, Path(first['backup']))
        self.assertTrue(rolled['backup'].endswith('.replaced'))
        self.assertEqual(plistlib.loads((self.app/'Contents/Info.plist').read_bytes())['CFBundleVersion'], '1')

    def test_storage_mismatch_prevents_stop(self):
        self.prepare_update()
        manifest = self.private/'installation.json'
        value = json.loads(manifest.read_text()); value['data'] = str(self.root/'wrong')
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'data path'):
            bundle._replace(self.private, self.candidate)
        self.stop.assert_not_called()

    def test_installed_process_cannot_act_as_coordinator(self):
        self.prepare_update()
        with patch('blackbox.bundle.sys.executable', str(bundle.helper_path(self.app))), self.assertRaisesRegex(ValueError, 'outside'):
            bundle.update(self.private, self.candidate, coordinator=True)
        self.stop.assert_not_called()

    def test_bundle_plist_tampering_prevents_stop(self):
        self.prepare_update()
        agent = self.runtime/'launchd.plist'
        plist = plistlib.loads(agent.read_bytes()); plist['ProgramArguments'][0] = '/other/python'
        agent.write_bytes(plistlib.dumps(plist))
        with self.assertRaisesRegex(ValueError, 'another installation'):
            bundle._replace(self.private, self.candidate)
        self.stop.assert_not_called()

    def test_development_label_is_separate_from_production(self):
        result = self.install()
        self.assertEqual(installation.installation_label(result), installation.VIEWER_ID+'.dev.issue38.monitor')
        self.assertNotEqual(installation.installation_label(result), installation.LABEL)
        self.assertEqual(installation.installation_label({'mode': 'bundle', 'bundle_id': installation.VIEWER_ID}), installation.LABEL)

    def test_common_config_update_and_rollback(self):
        self.prepare_update()
        candidate_config = bundle.common_config(self.candidate)/'l2/conf.d/common.conf'
        candidate_config.write_text('/synthetic/fixture/*\n')
        before = (self.runtime/'config/l2/conf.d/common.conf').read_bytes()
        result = bundle._replace(self.private, self.candidate)
        self.assertEqual((self.runtime/'config/l2/conf.d/common.conf').read_text(), '/synthetic/fixture/*\n')
        bundle._replace(self.private, Path(result['backup']))
        self.assertEqual((self.runtime/'config/l2/conf.d/common.conf').read_bytes(), before)

    def test_modified_common_config_is_not_discarded(self):
        self.prepare_update()
        config = self.runtime/'config/settings.json'
        config.write_text('local modifications')
        with self.assertRaisesRegex(ValueError, 'was modified'):
            bundle._replace(self.private, self.candidate)
        self.assertEqual(config.read_text(), 'local modifications')
        self.stop.assert_not_called()

    def test_live_scan_lock_prevents_replacement(self):
        self.prepare_update()
        fd = lock(self.data, 'scan.lock')
        try:
            with self.assertRaises(BlockingIOError):
                bundle._replace(self.private, self.candidate)
        finally:
            os.close(fd)
        self.assertFalse(self.app.with_name('Viewer.app.previous').exists())
        self.launch.assert_not_called()

    def test_concurrent_update_is_rejected_before_stop(self):
        self.prepare_update()
        fd = lock(self.private, 'update.lock')
        try:
            with self.assertRaises(BlockingIOError):
                bundle._replace(self.private, self.candidate)
        finally:
            os.close(fd)
        self.stop.assert_not_called()

    def test_start_stop_and_uninstall_do_not_race_update(self):
        self.install()
        fd = lock(self.private, 'update.lock')
        try:
            with patch('blackbox.installation.launchctl') as launch:
                for action in ('start', 'stop'):
                    with self.assertRaises(BlockingIOError):
                        installation.control(action, self.private)
                with self.assertRaises(BlockingIOError):
                    installation.uninstall(self.private)
                launch.assert_not_called()
        finally:
            os.close(fd)

    def test_common_config_replacement_failure_restores_app_and_config(self):
        self.prepare_update()
        original = Path.rename
        def rename(source, dest):
            if source.name == 'config' and source.parent.name.startswith('.config-update-'):
                raise OSError('synthetic config replacement failure')
            return original(source, dest)
        with patch('pathlib.Path.rename', rename), self.assertRaises(OSError):
            bundle._replace(self.private, self.candidate)
        self.assertEqual(plistlib.loads((self.app/'Contents/Info.plist').read_bytes())['CFBundleVersion'], '1')
        self.assertTrue((self.runtime/'config/settings.json').exists())

    def signature_tools(self, *, second_team='TEAM', runtime=True, helper_entitlements=None):
        self.signature_patch.stop()
        def run(args, **kwargs):
            if '-r-' in args:
                return subprocess.CompletedProcess(args, 0, 'designated => identifier "example"\n', 'Executable=synthetic')
            team = second_team if str(bundle.HELPER) in str(args[-1]) else 'TEAM'
            flags = '0x10000(runtime)' if runtime else '0x0(none)'
            return subprocess.CompletedProcess(args, 0, '', f'TeamIdentifier={team}\nCodeDirectory v=20500 flags={flags}\n')
        def command(*args):
            if '--entitlements' in args:
                ent = helper_entitlements if str(bundle.HELPER) in str(args[-1]) else {'com.apple.security.app-sandbox': True}
                return plistlib.dumps(ent or {}).decode()
            return ''
        patch('blackbox.bundle.subprocess.run', side_effect=run).start()
        return patch('blackbox.bundle.command', side_effect=command).start()

    def test_signature_requires_same_team(self):
        self.signature_tools(second_team='OTHER')
        with self.assertRaisesRegex(ValueError, 'same signing team'):
            bundle.verify_bundle(self.app)

    def test_signature_requires_hardened_runtime(self):
        self.signature_tools(runtime=False)
        with self.assertRaisesRegex(ValueError, 'hardened runtime'):
            bundle.verify_bundle(self.app)

    def test_signature_rejects_helper_app_group_entitlement(self):
        self.signature_tools(helper_entitlements={'com.apple.security.application-groups': ['synthetic.group']})
        with self.assertRaisesRegex(ValueError, 'must not have entitlements'):
            bundle.verify_bundle(self.app)

    def test_update_checks_installed_designated_requirements_from_stdout(self):
        command = self.signature_tools()
        bundle.verify_bundle(self.app, reference=self.app)
        requirements = [call.args for call in command.call_args_list if any(str(x).startswith('-R=') for x in call.args)]
        self.assertEqual(len(requirements), 2)
        self.assertTrue(all('-R=identifier "example"' in args for args in requirements))


if __name__ == '__main__':
    unittest.main()
