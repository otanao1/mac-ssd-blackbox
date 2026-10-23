"""No launchd calls: independent synthetic installations and mocked job replies."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from blackbox.installation import LABEL, domain, plist_bytes, uninstall
from blackbox.runtime import lock


def description(private, runtime, agent):
    arguments = plistlib.loads(plist_bytes(runtime/'venv/bin/python', runtime, private))['ProgramArguments']
    return '\n'.join([
        domain()+'/'+LABEL+' = {', '\tactive count = 0', '\tpath = '+str(agent),
        '\tprogram = '+arguments[0], '\targuments = {',
        *['\t\t'+arg for arg in arguments], '\t}',
        '\tworking directory = '+str(runtime), '\tstate = not running', '}',
    ])


def absent():
    return subprocess.CompletedProcess([], 113, '',
        f'Bad request.\nCould not find service "{LABEL}" in domain for user gui: {os.getuid()}\n')


class UninstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root/'home'
        self.home.mkdir()
        self.patch_home = patch('blackbox.installation.Path.home', return_value=self.home)
        self.patch_home.start()
        self.addCleanup(self.patch_home.stop)
        launch_patch = patch('blackbox.installation.launchctl')
        self.launch = launch_patch.start()
        self.addCleanup(launch_patch.stop)
        self.launch.return_value = absent()
        self.a = self.installation('A')
        self.b = self.installation('B')

    def installation(self, name, registered=False):
        base = self.root/name
        private, runtime, cli, data = (base/p for p in ('config', 'runtime', 'bin/blackbox', 'data'))
        private.mkdir(parents=True)
        runtime.mkdir()
        cli.parent.mkdir()
        cli.write_text('synthetic executable')
        data.mkdir()
        (data/'evidence').write_text('preserved')
        agent = (self.home/'Library/LaunchAgents'/(LABEL+'.plist') if registered else runtime/'launchd.plist')
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_bytes(plist_bytes(runtime/'venv/bin/python', runtime, private))
        manifest = dict(runtime=str(runtime), cli=str(cli), data=str(data), agent=str(agent), registered=registered)
        (private/'installation.json').write_text(json.dumps(manifest))
        return dict(private=private, runtime=runtime, cli=cli, data=data, agent=agent)

    def loaded(self, install):
        return subprocess.CompletedProcess([], 0,
            description(install['private'], install['runtime'], install['agent']), '')

    def assert_preserved(self, install):
        for key in ('private', 'runtime', 'cli', 'data', 'agent'):
            self.assertTrue(install[key].exists(), key)
        self.assertEqual((install['data']/'evidence').read_text(), 'preserved')

    def assert_no_mutation(self):
        self.assertTrue(all(c.args[0] == 'print' for c in self.launch.call_args_list))

    def test_unregistered_without_job_removes_only_its_own_runtime(self):
        uninstall(self.b['private'])
        self.assert_no_mutation()
        self.assertFalse(self.b['runtime'].exists())
        self.assertFalse(self.b['cli'].exists())
        self.assertTrue(self.b['private'].exists())
        self.assertTrue((self.b['data']/'evidence').exists())
        self.assert_preserved(self.a)

    def test_unregistered_with_other_loaded_install_aborts_without_stopping_it(self):
        self.launch.return_value = self.loaded(self.a)
        with self.assertRaisesRegex(ValueError, 'ownership'):
            uninstall(self.b['private'], remove_configuration=True)
        self.assert_no_mutation()
        self.assert_preserved(self.a)
        self.assert_preserved(self.b)

    def test_registered_owner_stops_before_deleting_its_agent(self):
        registered = self.installation('registered', registered=True)
        self.launch.return_value = self.loaded(registered)
        result = uninstall(registered['private'])
        self.assertEqual([c.args[0] for c in self.launch.call_args_list], ['print', 'print', 'bootout'])
        self.assertFalse(registered['agent'].exists())
        self.assertFalse(registered['runtime'].exists())
        self.assertEqual(result['data_preserved'], str(registered['data']))
        self.assert_preserved(self.a)

    def test_unregistered_but_started_owner_is_not_mistaken_for_never_loaded(self):
        self.launch.return_value = self.loaded(self.b)
        uninstall(self.b['private'])
        self.assertEqual(self.launch.call_args.args[0], 'bootout')
        self.assert_preserved(self.a)

    def test_same_plist_origin_does_not_prove_loaded_job_ownership(self):
        registered = self.installation('registered', registered=True)
        # The on-disk plist is ours, but launchd still holds a different definition
        # loaded from that shared path by a previous installation.
        output = description(self.a['private'], self.a['runtime'], registered['agent'])
        self.launch.return_value = subprocess.CompletedProcess([], 0, output, '')
        with self.assertRaisesRegex(ValueError, 'ownership'):
            uninstall(registered['private'])
        self.assert_no_mutation()
        self.assert_preserved(registered)
        self.assert_preserved(self.a)

    def test_registered_agent_without_loaded_job_can_be_removed(self):
        registered = self.installation('registered', registered=True)
        uninstall(registered['private'])
        self.assert_no_mutation()
        self.assertFalse(registered['agent'].exists())
        self.assertTrue(registered['data'].exists())

    def test_shared_plist_replaced_by_other_install_aborts_before_query(self):
        registered = self.installation('registered', registered=True)
        registered['agent'].write_bytes(self.a['agent'].read_bytes())
        before = registered['agent'].read_bytes()
        self.launch.return_value = self.loaded(registered)
        with self.assertRaisesRegex(ValueError, 'another installation'):
            uninstall(registered['private'])
        self.launch.assert_not_called()
        self.assertEqual(registered['agent'].read_bytes(), before)
        self.assert_preserved(registered)
        self.assert_preserved(self.a)

    def test_unknown_incomplete_and_conflicting_loaded_identity_aborts(self):
        valid = self.loaded(self.b).stdout
        bad = [
            '', 'unrecognized diagnostic',
            valid.replace('\tprogram = ', '\tmissing program = '),
            valid.replace(str(self.b['runtime']), str(self.a['runtime'])),
            valid.replace(str(self.b['private']), str(self.a['private'])),
            valid.replace(str(self.b['agent']), str(self.a['agent'])),
            valid.replace('\targuments = {', '\tother arguments = {'),
            valid.replace('\tstate = not running', '\tprogram = /wrong/python'),
            valid.replace('\tstate = not running', '\tenvironment = {\n\t\tprogram = /wrong/python\n\t}').replace('\tprogram = '+str(self.b['runtime']/'venv/bin/python')+'\n', ''),
            valid.replace('\tworking directory = ', '\tmissing directory = '),
            valid.replace('\t\t-m', '\t\t0 = -m'),
        ]
        for output in bad:
            with self.subTest(output=output):
                self.launch.reset_mock()
                self.launch.return_value = subprocess.CompletedProcess([], 0, output, '')
                with self.assertRaises(ValueError):
                    uninstall(self.b['private'])
                self.assert_no_mutation()
                self.assert_preserved(self.b)

    def test_query_failure_or_unrecognized_absence_aborts(self):
        for response in (subprocess.CompletedProcess([], 1, '', 'Operation not permitted'),
                         subprocess.CompletedProcess([], 113, '', 'Could not find domain'),
                         subprocess.CompletedProcess([], 113, '', 'unknown error')):
            with self.subTest(response=response):
                self.launch.reset_mock()
                self.launch.return_value = response
                with self.assertRaisesRegex(ValueError, 'cannot determine'):
                    uninstall(self.b['private'])
                self.assert_no_mutation()
                self.assert_preserved(self.b)
        for error in (OSError('query unavailable'), subprocess.TimeoutExpired('launchctl', 30)):
            with self.subTest(error=error):
                self.launch.side_effect = error
                with self.assertRaises(type(error)):
                    uninstall(self.b['private'])
                self.assert_no_mutation()
                self.assert_preserved(self.b)
                self.launch.side_effect = None

    def test_job_changed_between_queries_never_boots_out_other_install(self):
        self.launch.side_effect = [self.loaded(self.b), self.loaded(self.a)]
        with self.assertRaisesRegex(ValueError, 'ownership'):
            uninstall(self.b['private'])
        self.assert_no_mutation()
        self.assert_preserved(self.a)
        self.assert_preserved(self.b)

    def test_agent_replaced_during_bootout_is_not_deleted(self):
        registered = self.installation('registered', registered=True)
        def launch(action, *args, **kwargs):
            if action == 'bootout':
                registered['agent'].write_bytes(self.a['agent'].read_bytes())
            return self.loaded(registered)
        self.launch.side_effect = launch
        with self.assertRaisesRegex(ValueError, 'another installation'):
            uninstall(registered['private'])
        self.assertEqual(registered['agent'].read_bytes(), self.a['agent'].read_bytes())
        self.assert_preserved(registered)
        self.assert_preserved(self.a)

    def test_job_disappearing_during_recheck_needs_no_bootout(self):
        self.launch.side_effect = [self.loaded(self.b), absent()]
        uninstall(self.b['private'])
        self.assert_no_mutation()
        self.assertFalse(self.b['runtime'].exists())
        self.assert_preserved(self.a)

    def test_registered_missing_agent_still_checks_loaded_definition(self):
        registered = self.installation('registered', registered=True)
        registered['agent'].unlink()
        self.launch.return_value = self.loaded(registered)
        uninstall(registered['private'])
        self.assertEqual(self.launch.call_args.args[0], 'bootout')
        self.assertFalse(registered['runtime'].exists())
        self.assertTrue(registered['data'].exists())

    def test_bootout_failure_does_not_delete_files(self):
        def launch(action, *args, **kwargs):
            if action == 'bootout':
                raise subprocess.CalledProcessError(1, 'bootout')
            return self.loaded(self.b)
        self.launch.side_effect = launch
        with self.assertRaises(subprocess.CalledProcessError):
            uninstall(self.b['private'])
        self.assert_preserved(self.b)

    def test_missing_or_malformed_manifest_and_plist_cannot_stop_job(self):
        agent_bytes = self.b['agent'].read_bytes()
        for raw in (b'not a plist', b'<?xml version="1.0"?><plist><dict>', plistlib.dumps(dict(Label=LABEL))):
            self.b['agent'].write_bytes(raw)
            with self.assertRaises(ValueError):
                uninstall(self.b['private'])
            self.launch.assert_not_called()
        self.b['agent'].write_bytes(agent_bytes)
        manifest_path = self.b['private']/'installation.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['agent'] = str(self.a['agent'])
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'plist path'):
            uninstall(self.b['private'])
        self.launch.assert_not_called()

    def test_bootout_waits_for_service_cleanup(self):
        registered = self.installation('registered', registered=True)
        state = dict(fd=lock(registered['private'], 'service.lock'), closing=True)
        def release():
            if state['fd'] is not None:
                os.close(state['fd'])
                state['fd'] = None
        self.addCleanup(release)
        self.launch.return_value = self.loaded(registered)
        def status(*args):
            # Reproduce the observed race: the service can still answer after
            # bootout returns, while its final cleanup retains service.lock.
            if state['closing']:
                return dict(running=True)
            raise FileNotFoundError('control socket removed after cleanup')
        def finish_shutdown(seconds):
            self.assert_preserved(registered)
            state['closing'] = False
            release()
        with patch('time.sleep', side_effect=finish_shutdown) as sleep, \
             patch('blackbox.service.call', side_effect=status):
            result = uninstall(registered['private'])
        self.assertTrue(result['uninstalled'])
        sleep.assert_called_once()
        self.assertEqual(self.launch.call_args.args[0], 'bootout')
        self.assertFalse(registered['runtime'].exists())
        self.assertFalse(registered['agent'].exists())
        self.assertTrue(registered['data'].exists())

    def test_shutdown_timeout_preserves_every_file(self):
        fd = lock(self.b['private'], 'service.lock')
        self.addCleanup(os.close, fd)
        self.launch.return_value = self.loaded(self.b)
        with patch('time.monotonic', side_effect=[0, 15]), \
             patch('time.sleep') as sleep, patch('blackbox.service.call') as call:
            with self.assertRaisesRegex(ValueError, 'timed out'):
                uninstall(self.b['private'], remove_configuration=True)
        sleep.assert_not_called()
        call.assert_not_called()
        self.assert_preserved(self.b)
        self.assertEqual(self.launch.call_args.args[0], 'bootout')

    def test_foreground_lock_without_owned_bootout_is_not_waited_or_stopped(self):
        fd = lock(self.b['private'], 'service.lock')
        self.addCleanup(os.close, fd)
        with patch('time.sleep') as sleep, patch('blackbox.service.call') as call:
            with self.assertRaisesRegex(ValueError, 'foreground service'):
                uninstall(self.b['private'])
        sleep.assert_not_called()
        call.assert_not_called()
        self.assert_no_mutation()
        self.assert_preserved(self.b)

    def test_service_lock_is_held_during_file_deletion(self):
        import shutil
        remove = shutil.rmtree
        def checked_remove(path):
            with self.assertRaises(BlockingIOError):
                lock(self.b['private'], 'service.lock')
            remove(path)
        with patch('blackbox.installation.shutil.rmtree', side_effect=checked_remove):
            uninstall(self.b['private'])
        fd = lock(self.b['private'], 'service.lock')
        os.close(fd)
        self.assertTrue(self.b['data'].exists())

    def test_socket_error_releases_lock_without_deleting(self):
        for error in (OSError('control unavailable'), None):
            with self.subTest(error=error):
                with patch('blackbox.service.call', side_effect=error, return_value=dict(running=True)):
                    with self.assertRaises(OSError if error else ValueError):
                        uninstall(self.b['private'])
                fd = lock(self.b['private'], 'service.lock')
                os.close(fd)
                self.assert_preserved(self.b)


if __name__ == '__main__':
    unittest.main()
