import errno
import json
import signal
import sqlite3
import subprocess
import unittest
from unittest.mock import Mock, patch

from blackbox import diagnostics, runtime, service

IDENT = 'a' * 32


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.runner = patch('blackbox.diagnostics.subprocess.run').start()
        self.addCleanup(patch.stopall)
        self.runner.return_value.returncode = 0

    def payload(self):
        return json.loads(self.runner.call_args.args[0][-1])

    def test_public_payload_excludes_private_exception_and_fields(self):
        exc = OSError(errno.EACCES, 'private@example.org', '/Users/private/secret')
        self.assertTrue(diagnostics.emit('recording_suspended', error=exc,
                        scan_id=IDENT, path='/Users/private', reason='secret', stage='tick'))
        payload = self.payload()
        self.assertEqual(payload['errno_name'], 'EACCES')
        self.assertEqual(payload['scan_id'], IDENT)
        self.assertEqual(payload['exception_type'], 'OSError')
        encoded = str(self.runner.call_args)
        for secret in ('private@example.org', '/Users/private', 'secret'):
            self.assertNotIn(secret, encoded)
        self.assertEqual(self.runner.call_args.kwargs['timeout'], 1)
        self.assertIn(diagnostics.SUBSYSTEM, self.runner.call_args.args[0])

    def test_sqlite_code_is_preserved_without_message(self):
        try:
            sqlite3.connect(':memory:').execute('SELECT * FROM secret_table')
        except sqlite3.Error as exc:
            diagnostics.emit('recording_suspended', error=exc)
        self.assertEqual(self.payload()['sqlite_errorname'], 'SQLITE_ERROR')
        self.assertNotIn('secret_table', str(self.runner.call_args))

    def test_unknown_exception_class_and_invalid_id_not_exposed(self):
        secret_type = type('private_account', (Exception,), {})
        diagnostics.emit('worker_finalize_failed', error=secret_type('/secret'), scan_id='/secret')
        self.assertEqual(self.payload()['exception_type'], 'Exception')
        self.assertNotIn('scan_id', self.payload())
        self.assertNotIn('private_account', str(self.runner.call_args))

    def test_logging_failures_are_contained(self):
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired('log', 1), OSError()):
            self.runner.side_effect = failure
            self.assertFalse(diagnostics.emit('recording_suspended'))
            self.assertFalse(diagnostics.available())
        self.runner.side_effect = None
        self.runner.return_value.returncode = 64
        self.assertFalse(diagnostics.emit('recording_suspended'))

    def test_unknown_event_is_not_sent(self):
        self.assertFalse(diagnostics.emit('/private/path'))
        self.runner.assert_not_called()

    def test_recovery_uses_default_level_and_origin_metadata(self):
        diagnostics.emit('recording_resumed')
        self.assertIn('default', self.runner.call_args.args[0])
        self.assertIn('origin_pid', self.payload())
        self.assertIn('occurred_at', self.payload())


    def test_wrapped_timeout_keeps_operation_without_private_command_or_output(self):
        secret = 'NEVER_PUBLIC_SECRET'
        cause = subprocess.TimeoutExpired(['/private/'+secret], 10,
                                          output=secret.encode(), stderr=secret.encode())
        outer = OSError(secret)
        outer.__cause__ = cause
        diagnostics.mark_operation(cause, 'volume_diskutil')
        diagnostics.mark_operation(outer, 'storage_identity')
        diagnostics.emit('worker_start_failed', error=outer, stage='initialize',
                         operation='store_initialize', scan_id=IDENT)
        payload = self.payload()
        self.assertEqual(payload['operation'], 'volume_diskutil')
        self.assertEqual(payload['exception_type'], 'OSError')
        self.assertEqual(payload['cause_type'], 'TimeoutExpired')
        self.assertEqual(payload['timeout_seconds'], 10)
        self.assertNotIn('returncode', payload)
        self.assertNotIn('errno', payload)
        self.assertNotIn(secret, str(self.runner.call_args))
        self.runner.assert_called_once()

    def test_exit_status_and_cause_errno_are_not_confused_with_outer_errno(self):
        for code in (1, -9):
            with self.subTest(returncode=code):
                cause = subprocess.CalledProcessError(code, ['/private/NEVER_PUBLIC'],
                                                     output=b'NEVER_PUBLIC', stderr=b'NEVER_PUBLIC')
                outer = OSError('NEVER_PUBLIC'); outer.__cause__ = cause
                diagnostics.emit('worker_start_failed', error=outer, operation='volume_df')
                self.assertEqual(self.payload()['returncode'], code)
                self.assertEqual(self.payload()['cause_type'], 'CalledProcessError')
                self.assertNotIn('timeout_seconds', self.payload())
                self.assertNotIn('NEVER_PUBLIC', str(self.runner.call_args))
        cause = PermissionError(errno.EACCES, 'NEVER_PUBLIC', '/private/NEVER_PUBLIC')
        outer = OSError(errno.EIO, 'NEVER_PUBLIC'); outer.__cause__ = cause
        diagnostics.emit('worker_start_failed', error=outer, operation='storage_path')
        self.assertEqual(self.payload()['errno'], errno.EIO)
        self.assertEqual(self.payload()['cause_errno'], errno.EACCES)
        self.assertEqual(self.payload()['cause_errno_name'], 'EACCES')
        self.assertNotIn('NEVER_PUBLIC', str(self.runner.call_args))

    def test_invalid_operations_and_numeric_fields_are_omitted(self):
        for operation in ('/private/NEVER_PUBLIC', ['NEVER_PUBLIC'], None):
            exc = OSError(); exc._diagnostic_operation = operation
            diagnostics.emit('worker_start_failed', error=exc, operation=operation)
            self.assertNotIn('operation', self.payload())
        for seconds in (True, 'NEVER_PUBLIC', float('nan'), float('inf'), -1, 0, 3601, 10**1000):
            diagnostics.emit('worker_start_failed', error=subprocess.TimeoutExpired('NEVER_PUBLIC', seconds))
            self.assertNotIn('timeout_seconds', self.payload())
            self.assertNotIn('NEVER_PUBLIC', str(self.runner.call_args))
        diagnostics.emit('worker_start_failed', error=subprocess.CalledProcessError(True, 'NEVER_PUBLIC'))
        self.assertNotIn('returncode', self.payload())

    def test_cause_chain_is_bounded_cycle_safe_and_ignores_implicit_context(self):
        private_type = type('NEVER_PUBLIC_CLASS', (Exception,), {})
        outer = OSError('NEVER_PUBLIC'); inner = private_type('NEVER_PUBLIC')
        outer.__cause__ = inner; inner.__cause__ = outer
        diagnostics.emit('worker_start_failed', error=outer)
        self.assertEqual(self.payload()['cause_type'], 'Exception')
        self.assertNotIn('NEVER_PUBLIC', str(self.runner.call_args))
        chain = [OSError('NEVER_PUBLIC') for _ in range(4)]
        hidden = subprocess.TimeoutExpired('NEVER_PUBLIC', 10)
        for left, right in zip(chain, chain[1:]+[hidden]): left.__cause__ = right
        diagnostics.mark_operation(hidden, 'volume_diskutil')
        diagnostics.emit('worker_start_failed', error=chain[0], operation='store_initialize')
        self.assertEqual(self.payload()['operation'], 'store_initialize')
        self.assertNotIn('timeout_seconds', self.payload())
        outer = OSError(); outer.__context__ = hidden
        diagnostics.emit('worker_start_failed', error=outer)
        self.assertNotIn('cause_type', self.payload())
        self.assertNotIn('timeout_seconds', self.payload())

    def test_other_events_do_not_gain_initialization_fields(self):
        error = OSError(); error.__cause__ = subprocess.TimeoutExpired('NEVER_PUBLIC', 10)
        diagnostics.mark_operation(error, 'volume_diskutil')
        diagnostics.emit('recording_suspended', error=error, operation='volume_diskutil')
        self.assertEqual(self.payload()['exception_type'], 'OSError')
        for key in ('operation', 'cause_type', 'timeout_seconds', 'cause_errno'):
            self.assertNotIn(key, self.payload())

    def test_operation_tagging_preserves_exception_and_tolerates_unwritable_metadata(self):
        error = OSError(errno.EIO, 'NEVER_PUBLIC')
        args = error.args
        diagnostics.mark_operation(error, 'storage_path')
        diagnostics.mark_operation(error, 'store_initialize')
        self.assertEqual(error._diagnostic_operation, 'storage_path')
        self.assertEqual(error.args, args)
        class Unwritable(OSError):
            def __setattr__(self, name, value):
                if name == '_diagnostic_operation': raise RuntimeError('NEVER_PUBLIC')
                super().__setattr__(name, value)
        error = Unwritable('NEVER_PUBLIC')
        diagnostics.mark_operation(error, 'storage_path')
        diagnostics.emit('worker_start_failed', error=error, operation='store_initialize')
        self.assertEqual(self.payload()['operation'], 'store_initialize')
        self.assertEqual(self.payload()['exception_type'], 'OSError')
        self.assertNotIn('NEVER_PUBLIC', str(self.runner.call_args))


class RuntimeDiagnosticsTests(unittest.TestCase):
    def monitor(self):
        m = runtime.Monitor.__new__(runtime.Monitor)
        m.recording_error = None
        m.active_id = IDENT
        m.child = Mock(pid=123)
        m.child.poll.return_value = None
        m.notify = Mock()
        m.store = Mock()
        m._tick = Mock(side_effect=sqlite3.OperationalError('database is locked'))
        return m

    @patch('blackbox.runtime.diagnostic')
    def test_exception_interrupt_recovery_and_no_repeat(self, log):
        m = self.monitor()
        with self.assertRaises(sqlite3.Error):
            m.tick()
        self.assertEqual([c.args[0] for c in log.call_args_list],
                         ['recording_suspended', 'worker_interrupt_requested'])
        m.child.terminate.assert_called_once()
        with self.assertRaises(sqlite3.Error):
            m.tick()
        self.assertEqual(log.call_count, 2)
        m._tick.side_effect = None
        m.tick()
        self.assertEqual(log.call_args.args[0], 'recording_resumed')
        self.assertIsNone(m.recording_error)
        m.store.reconnect.assert_called()

    @patch('blackbox.runtime.diagnostic')
    def test_termination_failure_keeps_original_behavior_and_diagnostic(self, log):
        m = self.monitor()
        m.child.terminate.side_effect = ProcessLookupError()
        with self.assertRaises(ProcessLookupError):
            m.tick()
        self.assertEqual(log.call_args.args[0], 'worker_interrupt_failed')

    @patch('blackbox.runtime.diagnostic')
    @patch('blackbox.runtime.os.close')
    @patch('blackbox.runtime.disk_io', return_value=None)
    @patch('blackbox.runtime.DuScanner')
    @patch('blackbox.runtime.Store')
    @patch('blackbox.runtime.signal.signal')
    def test_worker_signal_and_user_cancel_distinguished(self, signals, store, scanner, io, close, log):
        for signalled in (False, True):
            log.reset_mock()
            db = store.return_value
            db.rows.side_effect = lambda sql, args=(): ([{'targets': '["/synthetic"]', 'layer': 'L2', 'options': '{}'}]
                if 'SELECT * FROM scans' in sql else [{'cancel': 0}])
            def scan(*args):
                if signalled:
                    signals.call_args_list[-1].args[1](signal.SIGTERM, None)
                raise runtime.Cancelled()
            scanner.return_value.run.side_effect = scan
            runtime.worker('/synthetic', IDENT, 7)
            event = log.call_args
            self.assertEqual(event.kwargs['state'], 'cancelled')
            self.assertEqual(event.kwargs['reason'], 'signal' if signalled else 'user_cancel')
            self.assertEqual(event.kwargs['signal'], signal.SIGTERM if signalled else None)

    @patch('blackbox.diagnostics.subprocess.run', side_effect=FileNotFoundError())
    def test_backend_failure_does_not_change_monitor_error_or_termination(self, runner):
        m = self.monitor()
        with self.assertRaisesRegex(sqlite3.OperationalError, 'database is locked'):
            m.tick()
        m.child.terminate.assert_called_once()
        self.assertEqual(m.recording_error, 'database is locked')

    @patch('blackbox.runtime.diagnostic')
    @patch('blackbox.runtime.disk_io', return_value=None)
    @patch('blackbox.runtime.Store', side_effect=OSError(errno.EIO, 'private'))
    def test_worker_start_failure_is_logged_and_propagated(self, store, io, log):
        with self.assertRaises(OSError):
            runtime.worker('/synthetic', IDENT, 7)
        self.assertEqual(log.call_args.args[0], 'worker_start_failed')
        self.assertEqual(log.call_args.kwargs['scan_id'], IDENT)

    @patch('blackbox.runtime.diagnostic')
    @patch('blackbox.runtime.os.close')
    @patch('blackbox.runtime.disk_io', return_value=None)
    @patch('blackbox.runtime.DuScanner')
    @patch('blackbox.runtime.Store')
    @patch('blackbox.runtime.signal.signal')
    def test_worker_finalize_failure_is_logged_even_when_db_unwritable(self, signals, store, scanner, io, close, log):
        db = store.return_value
        db.rows.side_effect = [[{'targets': '["/synthetic"]', 'layer': 'L2', 'options': '{}'}], [{'cancel': 0}], [{'cancel': 0}], []]
        db.execute.side_effect = sqlite3.OperationalError('database is locked')
        with self.assertRaises(sqlite3.Error):
            runtime.worker('/synthetic', IDENT, 7)
        self.assertEqual(log.call_args.args[0], 'worker_finalize_failed')
        db.close.assert_called_once()
        close.assert_called_once_with(7)

    @patch('blackbox.service.diagnostic')
    @patch('blackbox.service.Monitor', side_effect=OSError('private path'))
    def test_service_initialization_failure_is_logged_once(self, monitor, log):
        s = service.Service.__new__(service.Service)
        s.monitor = None
        s.error = None
        s.retry = 0
        s.config = dict(storage={'path': '/synthetic'}, targets={}, intervals={}, l1_path='/synthetic', retention={}, incident_thresholds={})
        with patch('builtins.print'):
            s.tick()
            s.retry = 0
            s.tick()
        log.assert_called_once()
        self.assertEqual(log.call_args.args[0], 'service_initialization_failed')


    @patch('blackbox.runtime.disk_io', return_value=None)
    @patch('blackbox.runtime.DuScanner')
    @patch('blackbox.runtime.Store')
    def test_initialization_diagnostic_failure_preserves_original_exception(self, store, scanner, io):
        original = OSError(errno.EIO, 'NEVER_PUBLIC')
        store.side_effect = original
        with patch('blackbox.runtime.diagnostic', side_effect=RuntimeError('diagnostic failed')):
            with self.assertRaises(OSError) as caught:
                runtime.worker('/synthetic', IDENT, 7)
        self.assertIs(caught.exception, original)
        scanner.assert_not_called()

    @patch('blackbox.runtime.diagnostic')
    @patch('blackbox.runtime.disk_io', return_value=None)
    @patch('blackbox.runtime.DuScanner')
    @patch('blackbox.runtime.Store')
    def test_scan_lookup_failure_has_distinct_fallback_operation(self, store, scanner, io, log):
        original = sqlite3.OperationalError('NEVER_PUBLIC')
        store.return_value.rows.side_effect = original
        with self.assertRaises(sqlite3.Error) as caught:
            runtime.worker('/synthetic', IDENT, 7)
        self.assertIs(caught.exception, original)
        self.assertEqual(log.call_args.kwargs['operation'], 'scan_lookup')
        log.assert_called_once()
        scanner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
