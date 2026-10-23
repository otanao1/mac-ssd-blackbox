import errno
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from blackbox.du import DuScanner, mount_points
from blackbox.scan import Cancelled
from blackbox.store import Store, SCHEMA
from blackbox.incidents import _evaluate, _enqueue


class DuTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blackbox-du-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.tree = self.root/'AAAA'
        self.child = self.tree/'BBBB'
        self.child.mkdir(parents=True)
        (self.child/'file').write_bytes(b'x'*4096)
        (self.tree/'direct').write_bytes(b'x'*8192)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)

    def scan(self, targets=None, **kw):
        rows = []
        scanner = DuScanner(self.db, device_check=lambda p: os.stat(p).st_dev,
                            mounts=lambda: set(), **kw)
        scanner.run(targets or [str(self.tree)+'/*'], 'L2', {}, rows.append)
        return {r['path']: r for r in rows}

    def test_parent_child_reuse_and_batched_siblings(self):
        deep=self.child/'deep';deep.mkdir();(deep/'file').write_bytes(b'x'*4096)
        for n in ('one', 'two'):
            d=self.tree/n; d.mkdir(); (d/'f').write_bytes(b'x'*4096)
        (self.tree/'one'/'BBBB').mkdir()
        (self.tree/'one'/'BBBB'/'f').write_bytes(b'x'*4096)
        calls=[]
        real=subprocess.Popen
        def popen(args, **kw):
            calls.append(args); return real(args, **kw)
        with patch('blackbox.du.subprocess.Popen', side_effect=popen):
            rows=self.scan([str(self.tree)+'/*',str(self.child)+'/*'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][-1], str(deep))
        self.assertNotIn(str(self.child),calls[1])
        self.assertIn(str(self.tree/'one'), calls[1])
        self.assertIn(str(self.tree/'two'), calls[1])
        expected=self.tree.stat().st_blocks*512+sum(rows[str(p)]['bytes'] for p in self.tree.iterdir())
        self.assertEqual(rows[str(self.tree)]['bytes'],expected)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sqlite_temp_master').fetchone()[0],0)
        self.assertTrue(all(r['logical'] is None for r in rows.values()))

    def test_hardlinks_count_each_path_and_symlink_is_not_followed(self):
        os.link(self.child/'file',self.tree/'link')
        (self.tree/'symlink').symlink_to(self.child,target_is_directory=True)
        rows=self.scan([str(self.tree)+'/*',str(self.child)+'/*'])
        self.assertEqual(rows[str(self.tree/'link')]['bytes'],(self.child/'file').stat().st_blocks*512)
        self.assertEqual(rows[str(self.tree/'symlink')]['kind'],'symlink')
        self.assertNotIn(str(self.tree/'symlink'/'file'),rows)
        self.assertEqual(rows[str(self.tree/'link')]['status'],'ok')

    def test_missing_and_stat_permission(self):
        rows=self.scan([str(self.root/'absent')])
        self.assertEqual(next(iter(rows.values()))['status'],'missing')
        with patch('blackbox.du.os.stat', side_effect=PermissionError(errno.EPERM,'denied')):
            rows=self.scan()
        self.assertEqual(rows[str(self.tree)]['status'],'error')

    def test_enumeration_error_and_failed_descendant(self):
        real=DuScanner.entries
        def entries(scanner,p):
            if p==str(self.child):raise PermissionError(errno.EACCES,'denied')
            return real(scanner,p)
        with patch.object(DuScanner,'entries',entries):
            rows=self.scan([str(self.tree)+'/*',str(self.child)+'/*'])
        self.assertEqual(rows[str(self.child)]['status'],'error')
        self.assertEqual(rows[str(self.tree)]['status'],'partial')

    def test_real_du_permission_error(self):
        self.child.chmod(0)
        self.addCleanup(self.child.chmod,0o700)
        rows=self.scan()
        self.assertEqual(rows[str(self.child)]['status'],'error')
        self.assertEqual(rows[str(self.tree)]['status'],'partial')

    def test_deep_mount_is_pruned_before_du(self):
        deep=self.child/'deep';deep.mkdir();(deep/'secret').write_bytes(b'x')
        rows=[];calls=[]
        scanner=DuScanner(self.db,device_check=lambda p:os.stat(p).st_dev,mounts=lambda:{str(deep)})
        with patch.object(scanner,'_du',wraps=scanner._du) as run:
            scanner.run([str(self.tree)+'/*'],'L2',{},rows.append)
            calls=[p for c in run.call_args_list for p in c.args[0]]
        self.assertTrue(all(not (str(deep)==p or str(deep).startswith(p+'/')) for p in calls))
        by={r['path']:r for r in rows}
        self.assertEqual(by[str(deep)]['status'],'excluded')
        self.assertNotIn(str(deep/'secret'),by)

    def test_explicit_internal_mount_has_separate_root(self):
        rows=[]
        scanner=DuScanner(self.db,device_check=lambda p:os.stat(p).st_dev,mounts=lambda:{str(self.child)})
        scanner.run([str(self.tree)+'/*',str(self.child)+'/*'],'L2',{},rows.append)
        by={r['path']:r for r in rows}
        self.assertEqual(by[str(self.child)]['status'],'ok')
        self.assertEqual(by[str(self.tree)]['bytes'],self.tree.stat().st_blocks*512+(self.tree/'direct').stat().st_blocks*512)

    def test_external_root_is_excluded(self):
        scanner=DuScanner(self.db,device_check=lambda p:(_ for _ in ()).throw(ValueError('external')),mounts=lambda:set())
        rows=[];scanner.run([str(self.tree)],'L2',{},rows.append)
        self.assertEqual(rows[0]['status'],'excluded')

    def test_cancel_before_du(self):
        count=0
        def cancelled():
            nonlocal count
            count+=1;return count>1
        scanner=DuScanner(self.db,cancelled=cancelled,device_check=lambda p:os.stat(p).st_dev,mounts=lambda:set())
        with self.assertRaises(Cancelled):scanner.run([str(self.tree)+'/*'],'L2',{},lambda r:None)

    def test_cancel_terminates_running_du_child(self):
        import sys
        children=[]
        real=subprocess.Popen
        def popen(args, **kw):
            child=real([sys.executable,'-c','import time; time.sleep(10)'],**kw)
            children.append(child)
            return child
        scanner=DuScanner(self.db,cancelled=lambda:bool(children),
                          device_check=lambda p:os.stat(p).st_dev,mounts=lambda:set())
        with patch('blackbox.du.subprocess.Popen',side_effect=popen):
            with self.assertRaises(Cancelled):scanner.run([str(self.tree)+'/*'],'L2',{},lambda r:None)
        self.assertEqual(len(children),1)
        self.assertIsNotNone(children[0].poll())

    def test_filename_with_newline_and_tab(self):
        d=self.tree/'line\n123\tpart';d.mkdir();(d/'x').write_bytes(b'x'*4096)
        rows=self.scan()
        self.assertEqual(rows[str(d)]['status'],'ok')
        self.assertGreaterEqual(rows[str(d)]['bytes'],4096)

    def test_mount_change_before_launch_never_starts_du(self):
        rows=[]
        mounts=iter([set(),{str(self.child/'new_mount')}])
        scanner=DuScanner(self.db,device_check=lambda p:os.stat(p).st_dev,mounts=lambda:next(mounts))
        with patch('blackbox.du.subprocess.Popen') as popen:
            scanner.run([str(self.tree)+'/*'],'L2',{},rows.append)
        popen.assert_not_called()
        self.assertTrue(any(r['status']=='error' for r in rows))

    def test_du_eintr_remains_error_and_parent_partial(self):
        def popen(args, **kw):
            path=args[-1]
            kw['stdout'].write(('8\t'+path+'\n').encode())
            kw['stderr'].write(('du: '+path+': Interrupted system call\n').encode())
            child=unittest.mock.Mock(returncode=1)
            child.poll.return_value=1
            return child
        with patch('blackbox.du.subprocess.Popen',side_effect=popen):rows=self.scan()
        self.assertEqual(rows[str(self.child)]['status'],'error')
        self.assertEqual(rows[str(self.tree)]['status'],'partial')
        self.assertEqual(rows[str(self.child)]['error'],'Interrupted system call')

    def test_unrecognized_output_is_not_successful_zero(self):
        def popen(args, **kw):
            kw['stdout'].write(b'broken output\n')
            child=unittest.mock.Mock(returncode=0);child.poll.return_value=0
            return child
        with patch('blackbox.du.subprocess.Popen',side_effect=popen):rows=self.scan()
        self.assertEqual(rows[str(self.tree)]['status'],'error')
        self.assertIsNone(rows[str(self.tree)]['bytes'])

    def test_mount_parse_failure_is_closed(self):
        with patch('blackbox.du.subprocess.run') as run:
            run.return_value.stdout=b'unrecognized\n'
            with self.assertRaises(ValueError):mount_points()


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.s=Store(self.path,create=True);self.addCleanup(self.s.close)

    def test_version_assignment_and_incident_baseline(self):
        self.assertEqual(self.s.rows('SELECT scan_version FROM scans WHERE id=?',(self.s.create_scan('L2',[],{}),))[0]['scan_version'],2)
        old=self.s.create_scan('L3',[],{});self.s.execute("UPDATE scans SET scan_version=1,state='succeeded',ended=0 WHERE id=?",(old,))
        item=dict(scan_ids=[],investigation=[],detected=time.time())
        with self.s.db:_enqueue(self.s,item,'L3',['/synthetic'])
        self.assertIsNone(item['investigation'][0]['baseline_id'])
        current=item['investigation'][0]['scan_id']
        self.s.execute("UPDATE scans SET state='succeeded' WHERE id=?",(current,))
        self.s.execute('INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)',(current,'/synthetic','directory',100,None,'ok',None,None,None))
        self.s.execute('INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)',(old,'/synthetic','directory',1,None,'ok',None,None,None))
        step=dict(scan_id=current,baseline_id=old);obj=dict(detection={'threshold':1})
        self.assertTrue(_evaluate(self.s,obj,step));self.assertEqual(step['compared_count'],0);self.assertIsNone(step['baseline_id'])

    def old_schema_fixture(self):
        self.s.close()
        c=sqlite3.connect(self.path/'blackbox.sqlite3')
        c.execute('ALTER TABLE scans DROP COLUMN scan_version')
        for layer in ('L2','L3','L9'):
            c.execute('INSERT INTO scans(id,layer,trigger,targets,options,state,created) VALUES(?,?,?,?,?,?,?)',
                      (layer,layer,'manual','[]','{}','pending' if layer=='L2' else 'succeeded',1))
            c.execute('INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)',(layer,'/synthetic','directory',1,1,'ok',None,None,None))
        c.execute("INSERT INTO l1(time,path) VALUES(1,'/synthetic')")
        c.execute('INSERT INTO l3_daily VALUES(?,?,?,?,?)',(1,1,'/synthetic','directory','{}'))
        c.execute('INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  ('incident',1,'DROP_SPACE','investigating','{}','{}','[]','[]','keep','["L2"]','L2',1))
        c.execute("INSERT INTO meta VALUES('due_L2','12345')")
        c.commit()
        snapshot={table:c.execute('SELECT * FROM '+table).fetchall()
                  for table in ('results','l1','l3_daily','incidents','meta')}
        snapshot['scans']=c.execute('SELECT * FROM scans').fetchall()
        c.close()
        return snapshot

    def assert_history_preserved(self, snapshot):
        c=sqlite3.connect(self.path/'blackbox.sqlite3')
        try:
            for table,rows in snapshot.items():
                if table=='scans':
                    columns=[r[1] for r in c.execute('PRAGMA table_info(scans)') if r[1]!='scan_version']
                    actual=c.execute('SELECT '+','.join(columns)+' FROM scans').fetchall()
                else:actual=c.execute('SELECT * FROM '+table).fetchall()
                self.assertEqual(actual,rows,table)
        finally:c.close()

    def test_old_schema_open_and_reference_commands_preserve_all_history(self):
        import sys
        snapshot=self.old_schema_fixture()
        self.s=Store(self.path);self.addCleanup(self.s.close)
        self.assertEqual([r['scan_version'] for r in self.s.rows('SELECT scan_version FROM scans')],[1,1,1])
        self.assert_history_preserved(snapshot)
        for args in (['history'],['status'],['results','L2'],['incidents']):
            result=subprocess.run([sys.executable,'-B','-m','blackbox','--data',str(self.path),*args],
                                  check=True,capture_output=True,text=True,timeout=10)
            json.loads(result.stdout)
            self.assert_history_preserved(snapshot)
        self.assertFalse(self.s.rows("SELECT * FROM meta WHERE key LIKE '%migration%'"))
        self.assertFalse(hasattr(self.s,'_migrate_scans'))
        ident=self.s.create_scan('L2',[],{})
        self.assertEqual(self.s.rows('SELECT scan_version FROM scans WHERE id=?',(ident,))[0]['scan_version'],2)
        self.assertEqual(self.s.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_schema_addition_is_idempotent_without_history_changes(self):
        snapshot=self.old_schema_fixture()
        for _ in range(2):
            store=Store(self.path);store.close()
            self.assert_history_preserved(snapshot)

    def test_schema_addition_rolls_back_on_destination_failure(self):
        self.old_schema_fixture()
        raw=sqlite3.connect(self.path/'blackbox.sqlite3');raw.row_factory=sqlite3.Row
        obj=Store.__new__(Store);obj.db=raw
        try:
            with patch.object(obj,'check',side_effect=[None,OSError('destination lost')]):
                with self.assertRaises(OSError):obj._ensure_scan_version()
            self.assertNotIn('scan_version',{r[1] for r in raw.execute('PRAGMA table_info(scans)')})
            self.assertEqual(raw.execute('SELECT COUNT(*) FROM scans').fetchone()[0],3)
        finally:raw.close()
