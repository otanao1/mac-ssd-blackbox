"""Issue #8 tests: synthetic storage, no real launchctl or installed LaunchAgents."""
import copy
import json
import os
from pathlib import Path
import plistlib
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

from blackbox.configuration import load
from blackbox.destination import initialize, mounted_volume, verify
from blackbox.installation import control, install, uninstall, plist_bytes, LABEL
from blackbox.runtime import Monitor
from blackbox.service import Service
from blackbox.store import Store

SOURCE = Path(__file__).resolve().parents[1]


class OperationsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.common = self.root/'common'
        self.private = self.root/'private'
        self.data = self.root/'data'
        self.common.mkdir()
        self.private.mkdir(mode=0o700)
        self.data.mkdir(mode=0o700)
        shutil.copy(SOURCE/'config/settings.json', self.common/'settings.json')
        self.volume = dict(kind='disk', mount=str(self.root), filesystem='apfs', uuid='SYNTHETIC-VOLUME')
        self.mount_patch = patch('blackbox.destination.mounted_volume', return_value=self.volume)
        self.mount = self.mount_patch.start()
        self.addCleanup(self.mount_patch.stop)
        self.identity = initialize(self.data, self.root)
        self.local(dict(storage=self.identity))

    def local(self, values):
        (self.private/'local.json').write_text(json.dumps(values))

    def service(self):
        service = Service(self.common, self.private)
        self.addCleanup(service.close)
        return service

    def test_common_local_and_conf_replacement(self):
        for root in (self.common, self.private):
            (root/'l3/conf.d').mkdir(parents=True)
        (self.common/'l3/conf.d/a.conf').write_text('/synthetic/common/*\n')
        (self.private/'l3/conf.d/a.conf').write_text('# disabled\n')
        (self.private/'l3/conf.d/b.conf').write_text('/synthetic/local/*\n')
        self.local(dict(storage=self.identity, intervals=dict(L1=12), targets=dict(L2=['/synthetic/target'])))
        cfg = load(self.common, self.private)
        self.assertEqual(cfg['intervals'], dict(L1=12, L2=86400, L3=3600))
        self.assertEqual(cfg['targets']['L3'], ['/synthetic/local/*'])
        self.assertIn(str(self.private/'l3/conf.d/a.conf'), cfg['sources']['files'])
        self.assertNotIn(str(self.common/'l3/conf.d/a.conf'), cfg['sources']['files'])

    def test_invalid_configuration(self):
        for override in (dict(intervals=dict(L1=0)), dict(intervals=dict(L1=True)),
                         dict(intervals=dict(L1=float('nan'))), dict(unknown=1),
                         dict(targets=dict(L9=['/synthetic'])), dict(targets=dict(L2=['relative'])),
                         dict(retention=dict(l3_raw_days=0)), dict(incident_thresholds=dict(l9_cooldown=0)),
                         dict(storage=dict(path='/invalid')), dict(l1_path='/synthetic/*'),
                         dict(l1_path=1), dict(intervals=[]), dict(retention=None), dict(targets=None)):
            with self.subTest(override=override):
                self.local(override)
                with self.assertRaises(ValueError):
                    load(self.common, self.private)

    def test_mount_identity_disk_and_network(self):
        self.mount_patch.stop()
        disk = Mock(stdout=b'')
        disk.stdout = plistlib.dumps(dict(VolumeUUID='UUID', Internal=False, MountPoint=str(self.root), FilesystemType='apfs'))
        mounted = Mock(stdout=f'Filesystem blocks Used Available Capacity Mounted on\n/dev/disk9s1 1 1 1 0% {self.root}\n')
        with patch('blackbox.destination.subprocess.run', side_effect=[mounted, disk]) as runner:
            result = mounted_volume(self.data)
            self.assertEqual(result['uuid'], 'UUID')
            self.assertEqual(runner.call_args_list[1].args[0][:3], ['/usr/sbin/diskutil', 'info', '-plist'])
        mounted.stdout = f'//user@server/share on {self.root} (smbfs, nodev, nosuid)\n'
        with patch('blackbox.destination.subprocess.run', side_effect=[Mock(stdout='Filesystem blocks\n//server/share 1 1 1 0% /mount\n'), mounted]):
            result = mounted_volume(self.data)
            self.assertEqual(result['kind'], 'network')
            self.assertEqual(result['source'], '//user@server/share')
        self.mount_patch.start()

    def test_startup_wrong_mount_does_not_touch_database(self):
        wrong = dict(self.volume, uuid='OTHER-VOLUME')
        self.mount.return_value = wrong
        with patch('blackbox.store.sqlite3.connect') as connect:
            with self.assertRaises(OSError):
                Store(self.data, identity=self.identity)
            connect.assert_not_called()

    def test_startup_missing_db_never_creates(self):
        (self.data/'blackbox.sqlite3').unlink()
        with self.assertRaises(OSError):
            Store(self.data, identity=self.identity)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_wrong_mount_at_setup_never_creates(self):
        other = self.root/'empty'
        other.mkdir()
        with self.assertRaises(OSError):
            initialize(other, '/')
        self.assertEqual(list(other.iterdir()), [])

    def test_destination_disappears_and_recovers_without_fallback(self):
        service = self.service()
        with patch('blackbox.runtime.observe'):
            service.tick()
        original = self.root/'original'
        self.data.rename(original)
        self.data.mkdir()
        service.tick()
        self.assertIsNotNone(service.handle(dict(command='status'))['recording_error'])
        self.assertEqual(list(self.data.iterdir()), [])
        self.data.rmdir()
        original.rename(self.data)
        service.retry = 0
        with patch('blackbox.runtime.observe'):
            service.tick()
        self.assertIsNone(service.error)

    def test_startup_offline_control_and_later_recovery(self):
        self.mount.side_effect = OSError('synthetic disconnected mount')
        service = self.service()
        service.tick()
        self.assertIsNone(service.monitor)
        self.assertIn('disconnected', service.handle(dict(command='status'))['recording_error'])
        self.assertEqual(service.handle(dict(command='config'))['storage'], str(self.data))
        self.mount.side_effect = None
        service.retry = 0
        with patch('blackbox.runtime.observe'):
            service.tick()
        self.assertIsNotNone(service.monitor)
        self.assertIsNone(service.error)

    def test_remount_device_number_reconnect_requires_stable_identity(self):
        store = Store(self.data, identity=self.identity)
        self.addCleanup(store.close)
        # A previous connection's device numbers differ after an allowed remount.
        store.device = -1
        store.database_identity = (-1, self.identity['database_inode'])
        store.reconnect()
        store.execute("INSERT INTO meta VALUES('remounted','ok')")
        self.mount.return_value = dict(self.volume, uuid='WRONG')
        with self.assertRaises(OSError):
            store.reconnect()

    def test_database_and_journal_symlink_rejected(self):
        for name in ('blackbox.sqlite3-journal', 'blackbox.sqlite3-wal', 'blackbox.sqlite3-shm'):
            sidecar = self.data/name
            sidecar.symlink_to(self.root/'must-not-be-created')
            with self.assertRaises(OSError):
                Store(self.data, identity=self.identity)
            self.assertFalse((self.root/'must-not-be-created').exists())
            sidecar.unlink()
        database = self.data/'blackbox.sqlite3'
        database.rename(self.data/'original.sqlite3')
        database.write_bytes(b'')
        with self.assertRaises(OSError):
            Store(self.data, identity=self.identity)
        self.assertEqual(database.stat().st_size, 0)

    def test_reload_valid_invalid_and_storage_change(self):
        service = self.service()
        with patch('blackbox.runtime.observe'):
            service.tick()
        initial = copy.deepcopy(service.config)
        self.local(dict(storage=self.identity, intervals=dict(L1=0)))
        with self.assertRaises(ValueError):
            service.reload()
        self.assertEqual(service.config, initial)
        self.assertEqual(service.monitor.intervals['L1'], 60)
        self.local(dict(storage=self.identity, intervals=dict(L1=120), incident_thresholds=dict(l9_cooldown=42)))
        service.reload()
        self.assertEqual(service.monitor.intervals['L1'], 120)
        self.assertEqual(service.config['incident_thresholds']['l9_cooldown'], 42)
        identity = dict(self.identity, database_inode=1)
        self.local(dict(storage=identity))
        with self.assertRaises(ValueError):
            service.reload()
        self.assertEqual(service.config['storage'], self.identity)

    def test_reload_database_failure_preserves_config(self):
        service = self.service()
        with patch('blackbox.runtime.observe'):
            service.tick()
        self.local(dict(storage=self.identity, intervals=dict(L1=120)))
        with patch.object(service.monitor.store, 'check', side_effect=OSError('disconnected')):
            with self.assertRaises(OSError):
                service.reload()
        self.assertEqual(service.monitor.intervals['L1'], 60)
        self.assertEqual(service.config['intervals']['L1'], 60)

    def test_manual_scan_uses_loaded_cooldown_and_keeps_active_targets(self):
        service = self.service()
        with patch('blackbox.runtime.observe'):
            service.tick()
        before = service.handle(dict(command='scan', layer='L3', paths=['/synthetic/before']))['scan_id']
        self.local(dict(storage=self.identity, targets=dict(L3=['/synthetic/after']),
                        incident_thresholds=dict(l9_cooldown=42)))
        service.reload()
        row = service.monitor.store.rows('SELECT targets FROM scans WHERE id=?', (before,))[0]
        self.assertEqual(json.loads(row['targets']), ['/synthetic/before'])
        with patch('blackbox.service.request', return_value={}) as request:
            service.handle(dict(command='scan', layer='L9', paths=['/synthetic/after']))
            self.assertEqual(request.call_args.args[-1], 42)

    def test_plist_and_start_stop_are_mocked(self):
        raw = plist_bytes('/synthetic/python', '/synthetic/runtime', self.private)
        plist = plistlib.loads(raw)
        self.assertEqual(plist['Label'], LABEL)
        self.assertEqual(plist['StandardErrorPath'], '/dev/null')
        self.assertEqual(plist['ProgramArguments'][-1], 'run')
        (self.private/'installation.json').write_text(json.dumps(dict(agent='/synthetic/agent.plist')))
        with patch('blackbox.installation.launchctl') as runner:
            runner.return_value.returncode = 1
            control('start', self.private)
            self.assertEqual(runner.call_args.args[0], 'bootstrap')
            runner.return_value.returncode = 0
            control('start', self.private)
            self.assertEqual(runner.call_args.args[0], 'kickstart')
            control('stop', self.private)
            self.assertEqual(runner.call_args.args[0], 'bootout')

    def test_install_and_uninstall_preserve_data_and_configuration(self):
        private, runtime, cli = (self.root/name for name in ('installed-config', 'runtime', 'bin/blackbox'))
        with patch('blackbox.installation.launchctl') as launch, patch('blackbox.installation.subprocess.run') as runner:
            runner.return_value.returncode = 0
            result = install(SOURCE, self.data, self.root, private=private, runtime=runtime, cli=cli)
            launch.assert_not_called()
            self.assertFalse(result['registered'])
            self.assertTrue((runtime/'blackbox/__main__.py').exists())
            self.assertTrue((runtime/'launchd.plist').exists())
            self.assertFalse((runtime/'.git').exists())
            self.assertIn(str(runtime), cli.read_text())
            launch.return_value.returncode = 1
            uninstall(private)
        self.assertFalse(runtime.exists())
        self.assertFalse(cli.exists())
        self.assertTrue((private/'local.json').exists())
        self.assertTrue((self.data/'blackbox.sqlite3').exists())

    def test_installer_never_overwrites_existing_locations(self):
        with patch('blackbox.installation.launchctl') as launch:
            with self.assertRaises((ValueError, OSError)):
                install(SOURCE, self.data, self.root, private=self.private, runtime=self.root/'runtime', cli=self.root/'cli')
            launch.assert_not_called()

    def test_cli_history_incidents_errors_and_config_offline(self):
        def cli(*arguments, success=True):
            result = subprocess.run([sys.executable, '-m', 'blackbox', '--config-dir', str(self.private),
                                     '--common-dir', str(self.common), *arguments],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0 if success else 1, result.stderr)
            return json.loads(result.stdout if success else result.stderr)
        # No mount probe or DB access should be needed for stopped status/config.
        self.assertFalse(cli('status')['running'])
        self.assertFalse(cli('config')['loaded'])
        self.assertIn('not running', cli('reload', success=False)['error'])
        # Mock a platform probe in-process for history and incidents via installed().
        from blackbox.__main__ import installed
        from contextlib import redirect_stdout
        from io import StringIO
        for layer in ('L1', 'L2', 'L3', 'L9'):
            output = StringIO()
            with redirect_stdout(output):
                installed(Mock(command='history', config_dir=self.private, common_dir=self.common, layer=layer, limit=10))
            parsed = json.loads(output.getvalue())
            self.assertIn('l1' if layer == 'L1' else 'scans', parsed)
            if layer == 'L3':
                self.assertIn('l3_daily', parsed)
        with redirect_stdout(StringIO()):
            installed(Mock(command='incidents', config_dir=self.private, common_dir=self.common, incident_id=None, limit=20))
        with self.assertRaises(ValueError):
            installed(Mock(command='incidents', config_dir=self.private, common_dir=self.common, incident_id='absent', limit=20))

    def test_history_uses_loaded_destination_when_disk_config_is_invalid(self):
        from blackbox.__main__ import installed
        from contextlib import redirect_stdout
        from io import StringIO
        self.local(dict(storage=dict(path='/invalid-edited-destination')))
        with patch('blackbox.service.call', return_value=dict(storage_identity=self.identity)), \
             redirect_stdout(StringIO()) as output:
            installed(Mock(command='history', config_dir=self.private, common_dir=self.common, layer='L1', limit=10))
        self.assertEqual(json.loads(output.getvalue()), dict(l1=[]))

    def test_worker_receives_identity_and_completes_synthetic_scan(self):
        from blackbox.runtime import request, lock, worker
        store = Store(self.data, identity=self.identity)
        self.addCleanup(store.close)
        ident = request(store, 'L3', [str(self.data/'synthetic-missing')])['scan_id']
        fd = lock(self.data, 'scan.lock')
        # Run exactly one worker against a synthetic missing target, without
        # changing the test runner's signal handlers or scanning real paths.
        with patch('blackbox.runtime.signal.signal'):
            worker(str(self.data), ident, fd, self.identity)
        self.assertEqual(store.rows('SELECT state FROM scans WHERE id=?', (ident,))[0]['state'], 'partial')
        monitor = Monitor(self.data, identity=self.identity)
        self.addCleanup(monitor.close)
        request(store, 'L3', [str(self.data/'another-missing')])
        process = Mock(pid=123456)
        process.poll.return_value = 0
        with patch('blackbox.runtime.subprocess.Popen', return_value=process) as popen:
            monitor.dispatch()
            self.assertEqual(json.loads(popen.call_args.args[0][-1]), self.identity)
        monitor.reap()

    def test_prepared_install_real_venv_cli_and_reinstall(self):
        private, runtime, cli = (self.root/name for name in ('real-config', 'real-runtime', 'real-bin/blackbox'))
        with patch('blackbox.installation.launchctl') as launch:
            install(SOURCE, self.data, self.root, private=private, runtime=runtime, cli=cli)
            launch.assert_not_called()
            result = subprocess.run([str(cli), 'config'], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['loaded'])
            launch.return_value.returncode = 1
            uninstall(private)
            original = (private/'local.json').read_bytes()
            install(SOURCE, self.data, self.root, private=private, runtime=runtime, cli=cli)
            self.assertEqual((private/'local.json').read_bytes(), original)
            uninstall(private, remove_configuration=True)
        self.assertFalse(private.exists())
        self.assertTrue((self.data/'blackbox.sqlite3').exists())

    def test_registration_and_unregistration_in_synthetic_home_only(self):
        private, runtime, cli = (self.root/name for name in ('registered-config', 'registered-runtime', 'registered-bin/blackbox'))
        with patch('blackbox.installation.Path.home', return_value=self.root), \
             patch('blackbox.installation.launchctl') as launch:
            install(SOURCE, self.data, self.root, private=private, runtime=runtime, cli=cli, register=True)
            self.assertEqual(launch.call_args.args[0], 'bootstrap')
            agent = self.root/'Library/LaunchAgents'/(LABEL+'.plist')
            self.assertTrue(agent.exists())
            launch.return_value.returncode = 0
            uninstall(private)
            self.assertEqual(launch.call_args.args[0], 'bootout')
            self.assertFalse(agent.exists())
        self.assertTrue((self.data/'blackbox.sqlite3').exists())

    def test_socket_service_double_start_reload_and_stop_on_synthetic_data(self):
        from blackbox.service import call, address
        import time
        # The child has no real observations or real monitored targets. All launchctl
        # calls fail the test. Its destination probe is confined to this fixture.
        script = """
import json, sys
from unittest.mock import patch
from blackbox.service import run
volume = json.loads(sys.argv[3])
with patch('blackbox.destination.mounted_volume', return_value=volume), \
     patch('blackbox.runtime.observe'), \
     patch('blackbox.runtime.Monitor.dispatch'), \
     patch('blackbox.installation.launchctl', side_effect=AssertionError('real launchctl forbidden')):
    run(sys.argv[1], sys.argv[2])
"""
        args = [sys.executable, '-c', script, str(self.common), str(self.private), json.dumps(self.volume)]
        child = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic()+5
            while not Path(address(self.private)).exists() and time.monotonic() < deadline and child.poll() is None:
                time.sleep(.02)
            if child.poll() is not None:
                stdout, stderr = child.communicate(timeout=5)
                self.fail(stderr)
            status = call(self.private, 'status')
            self.assertTrue(status['running'])
            self.assertEqual(status['pid'], child.pid)
            other = subprocess.run(args, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(other.returncode, 0)
            self.assertEqual(call(self.private, 'status')['pid'], child.pid)
            self.local(dict(storage=self.identity, intervals=dict(L1=120)))
            self.assertTrue(call(self.private, 'reload')['reloaded'])
            self.local(dict(storage=self.identity, intervals=dict(L1=0)))
            with self.assertRaises(ValueError):
                call(self.private, 'reload')
            self.assertTrue(call(self.private, 'status')['running'])
            scan = call(self.private, 'scan', layer='L3', paths=[str(self.data/'synthetic-missing')])
            self.assertIn('scan_id', scan)
            self.assertTrue(call(self.private, 'stop')['stopping'])
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertFalse(Path(address(self.private)).exists())
        finally:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=10)
            if child.stdout:
                child.stdout.close()
            if child.stderr:
                child.stderr.close()


if __name__ == '__main__':
    unittest.main()
