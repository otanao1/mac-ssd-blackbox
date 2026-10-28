"""Synthetic L9 compatibility, bounded retention, and failure evidence."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from blackbox.du import DuScanner, beneath
from blackbox.scan import Scanner, Cancelled
from blackbox.store import Store
from blackbox.runtime import request
from blackbox.incidents import _evaluate_l9


class L9DuTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blackbox-l9-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.tree = self.root/'tree'
        self.tree.mkdir()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)

    def scan(self, targets=None, threshold=81920, depth=7, **kw):
        rows = []
        scanner = DuScanner(self.db, device_check=lambda p: os.stat(p).st_dev,
                            mounts=kw.pop('mounts', lambda: set()), **kw)
        scanner.run(targets or [str(self.tree)+'/*'], 'L9',
                    dict(threshold=threshold, max_depth=depth), rows.append)
        self.assertEqual(self.db.execute('SELECT count(*) FROM sqlite_temp_master').fetchone()[0], 0)
        self.assertTrue(all(r['logical'] is None for r in rows))
        self.assertEqual(len(rows), len({(r['path'], r['kind']) for r in rows}))
        return {(r['path'], r['kind']): r for r in rows}, scanner

    def test_legacy_equivalence_thresholds_depth_and_statistics(self):
        # 4KiB = exactly 5% T, 40KiB = exactly 50% T on this fixture.
        (self.tree/'tiny').write_bytes(b'')
        (self.tree/'.hidden').write_bytes(b'x'*4096)
        branch = self.tree/'branch'; branch.mkdir()
        (branch/'large').write_bytes(b'x'*40960)
        (branch/'empty').write_bytes(b'')
        below = branch/'below'; below.mkdir(); (below/'big').write_bytes(b'x'*81920)
        for depth in (1, 2, 7):
            with self.subTest(depth=depth):
                targets = [str(self.tree)+'/*', str(branch)+'/*']
                current, _ = self.scan(targets, depth=depth)
                db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
                try:
                    legacy = []
                    Scanner(db, device_check=lambda p: os.stat(p).st_dev).run(
                        targets, 'L9', dict(threshold=81920, max_depth=depth), legacy.append)
                    old = {(r['path'], r['kind']): r for r in legacy}
                    self.assertEqual(set(current), set(old))
                    for key, row in current.items():
                        for field in ('bytes', 'status', 'count', 'mean_bytes'):
                            self.assertEqual(row.get(field), old[key].get(field), (key, field))
                finally:
                    db.close()

    def test_seven_is_output_depth_not_capacity_depth(self):
        parent = self.tree
        levels = []
        for i in range(10):
            parent = parent/('d'+str(i)); parent.mkdir(); levels.append(parent)
        (parent/'deep').write_bytes(b'x'*8192)
        rows, _ = self.scan(threshold=1)
        self.assertIn((str(levels[6]), 'directory'), rows)
        self.assertNotIn((str(levels[7]), 'directory'), rows)
        expected = sum(p.stat().st_blocks*512 for p in [self.tree, *levels, parent/'deep'])
        self.assertEqual(rows[str(self.tree), 'directory']['bytes'], expected)

    def test_directory_threshold_boundary(self):
        branch = self.tree/'branch'; branch.mkdir()
        (branch/'file').write_bytes(b'x'*8192)
        size = branch.stat().st_blocks*512+(branch/'file').stat().st_blocks*512
        rows, _ = self.scan(threshold=size*2)
        self.assertIn((str(branch/'file'), 'file'), rows)
        rows, _ = self.scan(threshold=size*2+1)
        self.assertNotIn((str(branch/'file'), 'file'), rows)

    def test_small_files_are_aggregated_not_retained(self):
        for i in range(1500):
            (self.tree/str(i)).touch()
        rows, scanner = self.scan()
        stats = rows[str(self.tree), 'small_files']
        self.assertEqual((stats['count'], stats['bytes'], stats['mean_bytes']), (1500, 0, 0))
        self.assertEqual(set(scanner.rows), {str(self.tree)})
        self.assertEqual(scanner.children[str(self.tree)], [])

    def test_hardlinks_each_path_symlink_and_explicit_small_file(self):
        f = self.tree/'file'; f.write_bytes(b'x'*4096)
        other = self.tree/'other'; os.link(f, other)
        link = self.tree/'link'; link.symlink_to(self.root, target_is_directory=True)
        rows, _ = self.scan([str(self.tree)+'/*', str(f)], threshold=200000)
        self.assertEqual(rows[str(self.tree), 'small_files']['count'], 2)
        self.assertEqual(rows[str(self.tree), 'small_files']['bytes'], 2*f.stat().st_blocks*512)
        self.assertEqual(rows[str(f), 'file']['bytes'], f.stat().st_blocks*512)
        self.assertEqual(rows[str(link), 'symlink']['status'], 'ok')
        self.assertFalse(any(beneath(p, str(link)) for p, k in rows))

    def test_disjoint_du_operands_and_overlapping_targets(self):
        a = self.tree/'a'; a.mkdir(); b = a/'b'; b.mkdir()
        (b/'file').write_bytes(b'x'*4096)
        c = self.tree/'c'; c.mkdir(); (c/'file').write_bytes(b'x'*4096)
        calls = []; original = DuScanner._du
        def run(scanner, operands, device):
            calls.extend(operands); return original(scanner, operands, device)
        with patch.object(DuScanner, '_du', run):
            rows, _ = self.scan([str(self.tree)+'/*', str(a)+'/*'], depth=1, threshold=1)
        self.assertEqual(set(calls), {str(b), str(c)})
        self.assertFalse(any(beneath(a, b) for a in calls for b in calls if a != b))
        expected = sum(p.stat().st_blocks*512 for p in [self.tree,a,b,b/'file',c,c/'file'])
        self.assertEqual(rows[str(self.tree), 'directory']['bytes'], expected)

    def test_mount_below_horizon_and_explicit_mount(self):
        a = self.tree/'a'; a.mkdir(); deep = a/'deep'; deep.mkdir()
        mount = deep/'mount'; mount.mkdir(); (mount/'secret').write_bytes(b'x'*8192)
        rows, _ = self.scan(depth=1, mounts=lambda: {str(mount)})
        self.assertEqual(rows[str(mount), 'boundary']['status'], 'excluded')
        self.assertNotIn((str(mount/'secret'), 'file'), rows)
        expected = sum(p.stat().st_blocks*512 for p in [self.tree,a,deep])
        self.assertEqual(rows[str(self.tree), 'directory']['bytes'], expected)
        rows, _ = self.scan([str(self.tree)+'/*', str(mount)+'/*'], depth=1,
                            threshold=1, mounts=lambda: {str(mount)})
        self.assertEqual(rows[str(mount), 'directory']['status'], 'ok')
        self.assertEqual(rows[str(self.tree), 'directory']['bytes'], expected)
        rows, _ = self.scan([str(self.tree)+'/*', str(mount)], threshold=1,
                            mounts=lambda: {str(mount)})
        self.assertEqual(rows[str(mount), 'directory']['status'], 'ok')
        self.assertNotIn((str(mount/'secret'), 'file'), rows)

    def test_du_operand_with_newline_and_tab(self):
        leaf = self.tree/'line\n123\tpart'; leaf.mkdir()
        (leaf/'file').write_bytes(b'x'*8192)
        rows, _ = self.scan(depth=1)
        self.assertEqual(rows[str(leaf), 'directory']['status'], 'ok')
        self.assertEqual(rows[str(leaf), 'directory']['bytes'],
                         leaf.stat().st_blocks*512+(leaf/'file').stat().st_blocks*512)

    def test_permission_partial_missing_and_plain_total_evidence(self):
        a = self.tree/'a'; a.mkdir(); (a/'secret').write_bytes(b'x'*8192)
        a.chmod(0); self.addCleanup(a.chmod, 0o700)
        for target in (str(self.tree), str(self.tree)+'/*'):
            rows, _ = self.scan([target])
            self.assertEqual(rows[str(self.tree), 'directory']['status'], 'partial')
            self.assertEqual(rows[str(a), 'unknown']['status'], 'error')
        rows, _ = self.scan([str(self.root/'missing')])
        self.assertEqual(rows[str(self.root/'missing'), 'unknown']['status'], 'missing')

    def test_cancel_during_enumeration_closes_directory(self):
        (self.tree/'f').touch()
        scanner = DuScanner(cancelled=lambda: True)
        iterator = scanner.detail_entries(str(self.tree))
        with self.assertRaises(Cancelled):
            next(iterator)
        self.assertIsNone(iterator.gi_frame)

    def test_cancel_running_du_reaps_child(self):
        import sys
        leaf = self.tree/'leaf'; leaf.mkdir()
        children = []; original = subprocess.Popen
        def popen(args, **kw):
            child = original([sys.executable, '-c', 'import time; time.sleep(10)'], **kw)
            children.append(child); return child
        with patch('blackbox.du.subprocess.Popen', side_effect=popen):
            with self.assertRaises(Cancelled):
                self.scan(depth=1, cancelled=lambda: bool(children))
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))

    def test_eintr_invalid_output_and_changed_mount_are_not_success(self):
        leaf = self.tree/'leaf'; leaf.mkdir()
        def popen(args, **kw):
            path = args[-1]
            kw['stdout'].write(('8\t'+path+'\n').encode())
            kw['stderr'].write(('du: '+path+': Interrupted system call\n').encode())
            child = unittest.mock.Mock(returncode=1); child.poll.return_value=1
            return child
        with patch('blackbox.du.subprocess.Popen', side_effect=popen):
            rows, _ = self.scan(depth=1)
        self.assertEqual(rows[str(leaf), 'unknown']['status'], 'error')
        self.assertEqual(rows[str(self.tree), 'directory']['status'], 'partial')
        def invalid(args, **kw):
            kw['stdout'].write(b'invalid output\n')
            child = unittest.mock.Mock(returncode=0); child.poll.return_value=0
            return child
        with patch('blackbox.du.subprocess.Popen', side_effect=invalid):
            rows, _ = self.scan(depth=1)
        self.assertEqual(rows[str(leaf), 'unknown']['status'], 'error')
        self.assertIsNone(rows[str(leaf), 'unknown']['bytes'])
        self.assertEqual(rows[str(self.tree), 'directory']['status'], 'partial')
        with patch('blackbox.du.subprocess.Popen') as run:
            mounts = iter([set(), {str(leaf/'new')}])
            rows, _ = self.scan(depth=1, mounts=lambda: next(mounts))
        run.assert_not_called()
        self.assertTrue(any(r['status']=='error' for r in rows.values()))

    def test_old_version_not_baseline_or_cooldown_and_history_unchanged(self):
        data = self.root/'data'; data.mkdir()
        store = Store(data, create=True); self.addCleanup(store.close)
        target = str(self.tree)+'/*'; options = dict(threshold=81920, max_depth=7)
        old = store.create_scan('L9', [target], options)
        store.execute("UPDATE scans SET scan_version=1,state='succeeded',started=?,ended=? WHERE id=?",
                      (time.time()-10,time.time()-5,old))
        store.execute('INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)',
                      (old,str(self.tree),'directory',1,10,'ok',None,None,None))
        before = store.rows('SELECT * FROM scans WHERE id=?', (old,))
        self.assertEqual(request(store,'L9',[target],options)['waiting'], [target])
        current = store.create_scan('L9', [target], options)
        store.execute("UPDATE scans SET state='succeeded',started=?,ended=? WHERE id=?",
                      (time.time()-1,time.time(),current))
        store.execute('INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)',
                      (current,str(self.tree),'directory',100000,None,'ok',None,None,None))
        scan = store.rows('SELECT * FROM scans WHERE id=?',(current,))[0]
        evaluation = _evaluate_l9(store,dict(detection={'threshold':81920}),target,scan,options)
        self.assertEqual(evaluation['reason'],'no_baseline')
        self.assertIsNone(evaluation['baseline_id'])
        self.assertEqual(store.rows('SELECT * FROM scans WHERE id=?',(old,)), before)
        self.assertEqual(store.rows('SELECT logical_bytes FROM results WHERE scan_id=?',(old,))[0]['logical_bytes'],10)
        self.assertEqual(request(store,'L9',[target],options)['suppressed'],[target])

    def test_worker_flushes_partial_evidence_and_releases_lock_on_failure_or_cancel(self):
        import sys
        from blackbox.runtime import lock
        code = '''
import sys
from unittest.mock import patch
from blackbox.runtime import worker
from blackbox.scan import Cancelled

def run(scanner, targets, layer, options, emit):
    for i in range(3):
        emit(dict(path='/synthetic/'+str(i),kind='file',bytes=i,logical=None,status='ok',error=None))
    if sys.argv[4]=='cancel':
        raise Cancelled()
    raise OSError('synthetic failure')
with patch('blackbox.runtime.DuScanner.run',run), patch('blackbox.runtime.diagnostic'):
    worker(sys.argv[1],sys.argv[2],int(sys.argv[3]))
'''
        for mode, expected in (('error','failed'),('cancel','cancelled')):
            with self.subTest(mode=mode):
                data = self.root/mode; data.mkdir()
                store = Store(data, create=True)
                try:
                    ident = store.create_scan('L9',[str(self.tree)],{})
                    fd = lock(data,'scan.lock')
                    child = subprocess.Popen([sys.executable,'-B','-c',code,str(data),ident,str(fd),mode],pass_fds=(fd,),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    os.close(fd)
                    out, err = child.communicate(timeout=10)
                    self.assertEqual(child.returncode,0,err)
                    self.assertEqual(store.rows('SELECT state FROM scans WHERE id=?',(ident,))[0]['state'],expected)
                    self.assertEqual(len(store.rows('SELECT * FROM results')),3)
                    self.assertFalse(store.db.in_transaction)
                    probe = lock(data,'scan.lock'); os.close(probe)
                    self.assertEqual(store.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                finally:
                    store.close()

    def test_worker_result_batch_failure_rolls_back_and_finalizes(self):
        import sys
        from blackbox.runtime import lock
        data = self.root/'batch-failure'; data.mkdir()
        store = Store(data,create=True); self.addCleanup(store.close)
        with store.db:
            store.db.execute("CREATE TRIGGER synthetic_reject BEFORE INSERT ON results WHEN NEW.path='/synthetic/50' BEGIN SELECT RAISE(ABORT,'synthetic insert failure'); END")
        ident = store.create_scan('L9',[str(self.tree)],{})
        code = '''
import sys
from unittest.mock import patch
from blackbox.runtime import worker

def run(scanner, targets, layer, options, emit):
    for i in range(101):
        emit(dict(path='/synthetic/'+str(i),kind='file',bytes=i,status='ok'))
with patch('blackbox.runtime.DuScanner.run',run), patch('blackbox.runtime.diagnostic'):
    worker(sys.argv[1],sys.argv[2],int(sys.argv[3]))
'''
        fd = lock(data,'scan.lock')
        child = subprocess.Popen([sys.executable,'-B','-c',code,str(data),ident,str(fd)],pass_fds=(fd,),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        os.close(fd)
        out, err = child.communicate(timeout=10)
        self.assertEqual(child.returncode,0,err)
        self.assertEqual(store.rows('SELECT state FROM scans WHERE id=?',(ident,))[0]['state'],'failed')
        self.assertFalse(store.rows('SELECT * FROM results'))
        probe = lock(data,'scan.lock'); os.close(probe)
        self.assertEqual(store.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_no_write_transaction_during_walk_or_du(self):
        data = self.root/'transactions'; data.mkdir()
        store = Store(data,create=True); self.addCleanup(store.close)
        ident = store.create_scan('L9',[str(self.tree)+'/*'],{})
        leaf = self.tree/'leaf'; leaf.mkdir()
        (leaf/'f').write_bytes(b'x'*4096)
        original = DuScanner._du
        def du(scanner,operands,device):
            self.assertFalse(store.db.in_transaction)
            competitor = sqlite3.connect(data/'blackbox.sqlite3',timeout=.1)
            try:
                with competitor:
                    competitor.execute("INSERT OR REPLACE INTO meta VALUES('during_du','ok')")
            finally:
                competitor.close()
            return original(scanner,operands,device)
        def progress(path):
            self.assertFalse(store.db.in_transaction)
            store.execute('UPDATE scans SET current=? WHERE id=?',(path,ident))
        with patch.object(DuScanner,'_du',du):
            self.scan(depth=1,progress=progress)
        self.assertEqual(store.rows("SELECT value FROM meta WHERE key='during_du'")[0]['value'],'ok')

    def test_dispatched_waiting_and_old_pending_use_new_version_and_partial_state(self):
        from blackbox.runtime import Monitor
        for mode in ('waiting','old_pending'):
            with self.subTest(mode=mode):
                data = self.root/('dispatch-'+mode); data.mkdir()
                Store(data,create=True).close()
                monitor = Monitor(data)
                try:
                    target = str(self.root/('absent-'+mode))
                    if mode=='waiting':
                        request(monitor.store,'L9',[target])
                    else:
                        ident = monitor.store.create_scan('L9',[target],{})
                        monitor.store.execute('UPDATE scans SET scan_version=1 WHERE id=?',(ident,))
                    monitor.dispatch()
                    deadline = time.monotonic()+10
                    while monitor.child is not None:
                        self.assertLess(time.monotonic(),deadline)
                        monitor.reap(); time.sleep(.01)
                    rows = monitor.store.rows("SELECT * FROM scans WHERE layer='L9'")
                    self.assertEqual(len(rows),1)
                    self.assertEqual((rows[0]['scan_version'],rows[0]['state']),(2,'partial'))
                    self.assertEqual(monitor.store.rows('SELECT status FROM results')[0]['status'],'missing')
                finally:
                    monitor.close()

    def test_worker_busy_batch_is_not_retried_and_lock_is_released(self):
        import sys
        from blackbox.runtime import lock
        data = self.root/'busy'; data.mkdir()
        store = Store(data,create=True); self.addCleanup(store.close)
        ident = store.create_scan('L9',[str(self.tree)],{})
        code = '''
import sys, sqlite3
from pathlib import Path
from unittest.mock import patch
from blackbox.runtime import worker
from blackbox.store import Store

def open_store(*args,**kw):
    s = Store(*args,**kw)
    s.db.execute('PRAGMA busy_timeout=20')  # Test only; production timeout unchanged.
    return s

def run(scanner, targets, layer, options, emit):
    other = sqlite3.connect(Path(sys.argv[1])/'blackbox.sqlite3')
    other.execute('BEGIN IMMEDIATE')
    try:
        for i in range(101):
            emit(dict(path='/synthetic/'+str(i),kind='file',bytes=i,status='ok'))
    finally:
        other.rollback(); other.close()
with patch('blackbox.runtime.Store',open_store), patch('blackbox.runtime.DuScanner.run',run), patch('blackbox.runtime.diagnostic'):
    worker(sys.argv[1],sys.argv[2],int(sys.argv[3]))
'''
        fd = lock(data,'scan.lock')
        child = subprocess.Popen([sys.executable,'-B','-c',code,str(data),ident,str(fd)],pass_fds=(fd,),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        os.close(fd)
        out, err = child.communicate(timeout=10)
        self.assertEqual(child.returncode,0,err)
        scan = store.rows('SELECT state,error FROM scans WHERE id=?',(ident,))[0]
        self.assertEqual(scan['state'],'failed')
        self.assertIn('locked',scan['error'])
        self.assertFalse(store.rows('SELECT * FROM results'))
        probe = lock(data,'scan.lock'); os.close(probe)
        self.assertEqual(store.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
