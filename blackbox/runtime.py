"""Scheduling, durable requests and child supervision and Incident/retention coordination."""
import fcntl
import json
import math
import os
from pathlib import Path
import resource
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

from .scan import Cancelled, Scanner, normalize
from .store import Store
from .du import DuScanner
from .metrics import disk_io
from .diagnostics import emit as diagnostic

DEFAULT_INTERVALS = {'L1': 60, 'L2': 86400, 'L3': 3600}
DEFAULT_OPTIONS = {'threshold': 1024**3, 'max_depth': 7}


def lock(directory, name, blocking=False):
    fd = os.open(Path(directory) / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return fd
    except BaseException:
        os.close(fd)
        raise


def options_for(options=None):
    result = DEFAULT_OPTIONS | (options or {})
    if set(result) != set(DEFAULT_OPTIONS):
        raise ValueError('unknown scan option')
    if not isinstance(result['threshold'], int) or result['threshold'] <= 0:
        raise ValueError('threshold must be a positive byte count')
    if not isinstance(result['max_depth'], int) or not 1 <= result['max_depth'] <= 7:
        raise ValueError('max_depth must be between 1 and 7')
    return result


def request(store, layer, targets, options=None, cooldown=1800):
    if layer not in ('L2', 'L3', 'L9') or not targets:
        raise ValueError('specify L2/L3/L9 and at least one target')
    normalized = sorted({p + ('/*' if c else '') for p, c in map(normalize, targets)})
    opts = options_for(options)
    if layer != 'L9':
        return {'scan_id': store.create_scan(layer, normalized, opts)}
    accepted, suppressed = [], []
    encoded = json.dumps(opts, sort_keys=True)
    store.check()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        recent = store.rows("SELECT targets,options FROM scans WHERE layer='L9' AND started>? AND state IN ('running','succeeded','partial')", (time.time()-cooldown,))
        for target in normalized:
            duplicate = any(json.loads(r['options']) == opts and target in json.loads(r['targets']) for r in recent)
            if duplicate:
                suppressed.append(target)
            else:
                store.db.execute('INSERT OR IGNORE INTO waiting VALUES(?,?,?)', (target, encoded, time.time()))
                accepted.append(target)
    return {'waiting': accepted, 'suppressed': suppressed}


def observe(store, path):
    now = time.time()
    try:
        st = os.statvfs(path)
        # df-style free/used, with unprivileged available reported separately.
        boot = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'],
                              capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        store.execute('INSERT INTO l1 VALUES(?,?,?,?,?,?,?,?)',
                      (now, path, st.f_blocks*st.f_frsize,
                       (st.f_blocks-st.f_bfree)*st.f_frsize,
                       st.f_bfree*st.f_frsize, st.f_bavail*st.f_frsize, boot, None))
    except (OSError, subprocess.SubprocessError) as exc:
        store.execute('INSERT INTO l1(time,path,error) VALUES(?,?,?)', (now, path, str(exc)))


def worker(directory, ident, inherited_lock, identity=None):
    # inherited flock survives supervisor death and prevents a second scan.
    started = time.monotonic()
    baseline = resource.getrusage(resource.RUSAGE_SELF)
    child_baseline = resource.getrusage(resource.RUSAGE_CHILDREN)
    io_start = disk_io()
    try:
        store = Store(directory, identity=identity)
        scan = store.rows('SELECT * FROM scans WHERE id=?', (ident,))[0]
    except Exception as exc:
        diagnostic('worker_start_failed', error=exc, scan_id=ident, stage='initialize')
        raise
    interrupted = False
    received_signal = None
    def stop(signum, frame):
        nonlocal interrupted, received_signal
        interrupted = True
        received_signal = signum
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def cancelled():
        return interrupted or bool(store.rows('SELECT cancel FROM scans WHERE id=?', (ident,))[0]['cancel'])
    def progress(path):
        store.execute('UPDATE scans SET current=? WHERE id=?', (path, ident))
    def emit(row):
        store.check()
        write = store.execute if scan['layer'] in ('L2', 'L3') else store.db.execute
        write('INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?,?,?)',
                      (ident, row['path'], row['kind'], row.get('bytes'), row.get('logical'),
                       row['status'], row.get('error'), row.get('count'), row.get('mean_bytes')))
    state, error = 'succeeded', None
    failure = None
    try:
        if cancelled():
            raise Cancelled()
        def target_done(n):
            store.execute('UPDATE scans SET completed=? WHERE id=?', (n, ident))
        scanner_class = DuScanner if scan['layer'] in ('L2', 'L3') else Scanner
        scanner = scanner_class(store.db, progress, cancelled, target_done=target_done)
        scanner.run(json.loads(scan['targets']), scan['layer'], json.loads(scan['options']), emit)
        if cancelled():
            raise Cancelled()
        if store.rows("SELECT 1 FROM results WHERE scan_id=? AND status NOT IN ('ok','deduplicated') LIMIT 1", (ident,)):
            state = 'partial'
    except Cancelled:
        state = 'cancelled'
    except BaseException as exc:
        failure = exc
        state, error = 'failed', str(exc)
    finally:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        elapsed = time.monotonic()-started
        child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        child_cpu = child_usage.ru_utime+child_usage.ru_stime-child_baseline.ru_utime-child_baseline.ru_stime
        cpu = usage.ru_utime+usage.ru_stime-baseline.ru_utime-baseline.ru_stime+child_cpu
        metrics = dict(elapsed_seconds=elapsed, cpu_seconds=cpu, child_cpu_seconds=child_cpu,
                       child_peak_memory_bytes=child_usage.ru_maxrss if sys.platform == 'darwin' else child_usage.ru_maxrss*1024,
                       cpu_percent=100*cpu/max(elapsed, .001),
                       peak_memory_bytes=usage.ru_maxrss if sys.platform == 'darwin' else usage.ru_maxrss*1024,
                       input_operations=usage.ru_inblock, output_operations=usage.ru_oublock)
        io_end = disk_io()
        metrics.update({k: io_end[k]-io_start[k] for k in io_end} if io_start is not None and io_end is not None else {'disk_read_bytes': None, 'disk_written_bytes': None})
        try:
            store.execute('UPDATE scans SET state=?,ended=?,error=?,metrics=?,completed=CASE WHEN ? IN (\'succeeded\',\'partial\') THEN total ELSE completed END WHERE id=?',
                          (state, time.time(), error, json.dumps(metrics), state, ident))
        except BaseException as exc:
            diagnostic('worker_finalize_failed', error=exc, scan_id=ident, stage='finalize')
            raise
        finally:
            store.close()
            os.close(inherited_lock)
        if state in ('cancelled', 'failed', 'partial'):
            reason = ('signal' if received_signal else 'user_cancel') if state == 'cancelled' else ('worker_error' if state == 'failed' else 'incomplete')
            diagnostic('worker_finished', error=failure, scan_id=ident, state=state,
                       reason=reason, signal=received_signal)


class Monitor:
    def __init__(self, directory, targets=None, intervals=None, l1_path='/System/Volumes/Data',
                 retention=None, incident_thresholds=None, notify=None, identity=None):
        self.store = Store(directory, identity=identity)
        try:
            self.owner = lock(directory, 'monitor.lock')
        except BaseException:
            self.store.close()
            raise
        self.targets = targets or {'L2': [], 'L3': []}
        self.intervals = DEFAULT_INTERVALS | (intervals or {})
        if set(self.intervals) != set(DEFAULT_INTERVALS) or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
                for v in self.intervals.values()):
            os.close(self.owner)
            self.store.close()
            raise ValueError('intervals must be positive seconds')
        self.l1_path = l1_path
        self.child = None
        self.scan_lock = None
        self.active_id = None
        self.cancel_since = None
        self.stopping = False
        from .retention import policy
        from .incidents import thresholds
        try:
            self.retention = policy(retention)
            self.incident_thresholds = thresholds(incident_thresholds)
        except BaseException:
            self.store.close()
            os.close(self.owner)
            raise
        self.notify = notify or (lambda event: print(json.dumps(event), file=sys.stderr))
        self.recording_error = None

    def reap(self):
        if not self.child:
            return
        row = self.store.rows('SELECT cancel FROM scans WHERE id=?', (self.active_id,))[0]
        if row['cancel']:
            self.cancel_since = self.cancel_since or time.monotonic()
            elapsed = time.monotonic()-self.cancel_since
            if self.child.poll() is None and elapsed > 5:
                self.child.terminate() if elapsed < 10 else self.child.kill()
        if self.child.poll() is not None:
            updated = self.store.execute("UPDATE scans SET state=CASE WHEN cancel=1 THEN 'cancelled' ELSE 'failed' END,ended=?,error='worker exited before finalizing' WHERE id=? AND state='running'", (time.time(), self.active_id))
            if updated.rowcount:
                diagnostic('worker_unexpected_exit', scan_id=self.active_id, child_pid=self.child.pid, returncode=self.child.returncode)
            os.close(self.scan_lock)
            self.scan_lock = self.child = self.active_id = self.cancel_since = None

    def dispatch(self):
        if self.child or self.stopping:
            return
        try:
            scan_fd = lock(self.store.directory, 'scan.lock')
        except BlockingIOError:
            return  # surviving worker from a previous monitor
        try:
            self.store.execute("UPDATE scans SET state=CASE WHEN cancel=1 THEN 'cancelled' ELSE 'failed' END,ended=?,error='supervisor or worker interrupted' WHERE state='running'", (time.time(),))
            self.store.check()
            with self.store.db:
                self.store.db.execute('BEGIN IMMEDIATE')
                self.store.db.execute("UPDATE scans SET state='cancelled',ended=? WHERE state='pending' AND cancel=1", (time.time(),))
                pending = self.store.rows("SELECT * FROM scans WHERE state='pending' ORDER BY created LIMIT 1")
                waiting = self.store.rows('SELECT * FROM waiting ORDER BY requested LIMIT 1')
                if waiting:
                    opts = waiting[0]['options']
                    targets = [r['target'] for r in self.store.rows('SELECT target FROM waiting WHERE options=?', (opts,))]
                    ident = uuid.uuid4().hex
                    self.store.db.execute("INSERT INTO scans(id,layer,trigger,targets,options,state,created,total) VALUES(?,'L9','request',?,?,'pending',?,?)", (ident, json.dumps(targets), opts, time.time(), len(targets)))
                    self.store.db.execute('DELETE FROM waiting WHERE options=?', (opts,))
                elif pending:
                    ident = pending[0]['id']
                else:
                    return
                self.store.db.execute("UPDATE scans SET state='skipped',ended=?,error='another request won dispatch' WHERE state='pending' AND trigger='periodic' AND id<>?", (time.time(), ident))
                self.store.db.execute("UPDATE scans SET state='running',started=? WHERE id=?", (time.time(), ident))
            self.active_id = ident
            try:
                self.child = subprocess.Popen([sys.executable, '-m', 'blackbox', '_worker', str(self.store.directory), ident, str(scan_fd), json.dumps(self.store.identity)],
                                               pass_fds=(scan_fd,), cwd=Path(__file__).resolve().parent.parent)
            except OSError as exc:
                self.store.execute("UPDATE scans SET state='failed',ended=?,error=? WHERE id=?", (time.time(), str(exc), ident))
                self.active_id = None
                raise
            self.scan_lock = scan_fd
            scan_fd = None
            self.store.execute('UPDATE scans SET pid=? WHERE id=?', (self.child.pid, ident))
        finally:
            if scan_fd is not None:
                os.close(scan_fd)

    def tick(self, now=None):
        stage = 'reconnect' if self.recording_error is not None else 'tick'
        try:
            if self.recording_error is not None:
                self.store.reconnect()
                self.store.execute("INSERT OR REPLACE INTO meta VALUES('storage_probe',?)", (str(time.time()),))
            self._tick(now)
        except (OSError, sqlite3.Error) as exc:
            if self.recording_error is None:
                diagnostic('recording_suspended', error=exc, scan_id=self.active_id, stage=stage)
                self.notify(dict(event='recording_stopped', error=str(exc)))
                if self.child and self.child.poll() is None:
                    diagnostic('worker_interrupt_requested', scan_id=self.active_id, child_pid=self.child.pid, reason='recording_error', signal=signal.SIGTERM.value)
                    try:
                        self.child.terminate()
                    except OSError as termination_error:
                        diagnostic('worker_interrupt_failed', error=termination_error, scan_id=self.active_id, child_pid=self.child.pid, reason='recording_error')
                        raise
            self.recording_error = str(exc)
            raise
        if self.recording_error is not None:
            self.recording_error = None
            diagnostic('recording_resumed')
            self.notify(dict(event='recording_resumed'))

    def _tick(self, now=None):
        from .incidents import detect, advance
        from .retention import maintain
        self.store.check()
        self.reap()
        now = time.time() if now is None else now
        for layer, interval in self.intervals.items():
            key = 'due_' + layer
            last = self.store.rows('SELECT value FROM meta WHERE key=?', (key,))
            if last and now < float(last[0]['value']):
                continue
            self.store.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(now+interval)))
            if layer == 'L1':
                observe(self.store, self.l1_path)
            elif self.targets.get(layer):
                # Requests and queued L9 have priority; periodic runs never queue.
                busy = self.child is not None or bool(self.store.rows("SELECT 1 FROM scans WHERE state IN ('running','pending') LIMIT 1")) or bool(self.store.rows('SELECT 1 FROM waiting LIMIT 1'))
                ident = self.store.create_scan(layer, self.targets[layer], DEFAULT_OPTIONS, 'periodic', 'skipped' if busy else 'pending')
                if busy:
                    self.store.execute('UPDATE scans SET ended=?,error=? WHERE id=?', (time.time(), 'another scan or request has priority', ident))
        detect(self.store, self.targets, self.incident_thresholds)
        advance(self.store, self.targets, self.incident_thresholds, self.notify)
        due = self.store.rows("SELECT value FROM meta WHERE key='due_retention'")
        if not due or now >= float(due[0]['value']):
            maintain(self.store, now, self.retention)
            self.store.execute("INSERT OR REPLACE INTO meta VALUES('due_retention',?)", (str(now+86400),))
        self.dispatch()

    def close(self):
        self.stopping = True
        try:
            if self.child:
                try:
                    self.store.execute('UPDATE scans SET cancel=1 WHERE id=?', (self.active_id,))
                    while self.child:
                        self.reap()
                        time.sleep(.05)
                except (OSError, sqlite3.Error):
                    self.child.terminate()
                    try:
                        self.child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.child.kill()
                        self.child.wait()
                    os.close(self.scan_lock)
                    self.child = None
        finally:
            self.store.close()
            os.close(self.owner)
