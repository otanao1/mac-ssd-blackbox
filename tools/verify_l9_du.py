"""Bounded synthetic L9 comparison and live-worker DB contention check.

Run: .venv/bin/python -B tools/verify_l9_du.py
No broad configs, installation, launchd, or production data. Fixtures/DBs are
removed by TemporaryDirectory; stdout contains aggregate synthetic metrics only.
"""
import collections
import json
import os
from pathlib import Path
import resource
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blackbox.du import DuScanner
from blackbox.metrics import disk_io
from blackbox.runtime import Monitor, request
from blackbox.scan import Scanner
from blackbox.store import Store

OPTIONS = dict(threshold=65536, max_depth=7)


def comparison_worker(base, tree, mode):
    data = base/('db-'+mode); data.mkdir()
    store = Store(data, create=True)
    ident = store.create_scan('L9', [str(tree)+'/*'], OPTIONS)
    counts = collections.Counter()
    transaction_start = None
    spans = []
    def trace(sql):
        nonlocal transaction_start
        if sql.startswith('INSERT OR REPLACE INTO nodes'):
            counts['temp_node_writes'] += 1
        elif sql.startswith('INSERT OR REPLACE INTO results'):
            counts['result_writes'] += 1
        if sql.startswith('BEGIN'):
            transaction_start = time.monotonic()
        elif sql == 'COMMIT':
            counts['commits'] += 1
            if transaction_start is not None:
                spans.append(time.monotonic()-transaction_start)
                transaction_start = None
    store.db.set_trace_callback(trace)
    def progress(path):
        store.execute('UPDATE scans SET current=? WHERE id=?', (path, ident))
    def done(n):
        store.execute('UPDATE scans SET completed=? WHERE id=?', (n, ident))
    pending = []
    sql = 'INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?,?,?)'
    def flush():
        if pending:
            store.check()
            with store.db:
                store.db.executemany(sql, pending)
            pending.clear()
    def emit(row):
        values = (ident,row['path'],row['kind'],row.get('bytes'),row.get('logical'),
                  row['status'],row.get('error'),row.get('count'),row.get('mean_bytes'))
        if mode == 'legacy':
            store.db.execute(sql, values)
        else:
            pending.append(values)
            if len(pending) >= 100:
                flush()
    start = time.monotonic(); usage = resource.getrusage(resource.RUSAGE_SELF)
    child_usage = resource.getrusage(resource.RUSAGE_CHILDREN); io = disk_io()
    cls = Scanner if mode == 'legacy' else DuScanner
    scanner = cls(store.db, progress, lambda: False,
                  device_check=lambda p: os.stat(p).st_dev, target_done=done)
    scanner.run([str(tree)+'/*'], 'L9', OPTIONS, emit)
    flush()
    store.db.commit()
    elapsed = time.monotonic()-start
    end_usage = resource.getrusage(resource.RUSAGE_SELF)
    end_child = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = end_usage.ru_utime+end_usage.ru_stime-usage.ru_utime-usage.ru_stime
    child_cpu = end_child.ru_utime+end_child.ru_stime-child_usage.ru_utime-child_usage.ru_stime
    io_end = disk_io()
    root_size = store.rows("SELECT bytes FROM results WHERE scan_id=? AND path=? AND kind='directory'", (ident,str(tree)))[0]['bytes']
    result = dict(mode=mode, elapsed_seconds=elapsed, cpu_seconds=cpu+child_cpu,
                  child_cpu_seconds=child_cpu, peak_memory_bytes=end_usage.ru_maxrss,
                  child_peak_memory_bytes=end_child.ru_maxrss, writes=dict(counts),
                  max_begin_to_commit_seconds=max(spans,default=0),
                  sum_begin_to_commit_seconds=sum(spans), allocated_bytes=root_size,
                  result_count=store.db.execute('SELECT count(*) FROM results').fetchone()[0],
                  temp_pages=store.db.execute('PRAGMA temp.page_count').fetchone()[0],
                  integrity=store.db.execute('PRAGMA integrity_check').fetchone()[0])
    if io and io_end:
        result.update({k:io_end[k]-io[k] for k in io})
    store.close()
    print(json.dumps(result))


def contention(base, trees):
    data = base/'contention'; data.mkdir()
    Store(data, create=True).close()
    monitor = Monitor(data, targets={'L2':[], 'L3':[]}, l1_path=str(trees[0]), intervals={'L1':.05})
    stop = threading.Event(); errors = []; writes = [0]
    def competitor():
        c = sqlite3.connect((data/'blackbox.sqlite3').as_uri()+'?mode=rw',uri=True,timeout=5)
        try:
            while not stop.is_set():
                c.execute('SELECT count(*) FROM results').fetchone()
                with c:
                    c.execute("INSERT OR REPLACE INTO meta VALUES('synthetic_competitor',?)",(str(writes[0]),))
                writes[0] += 1
                stop.wait(.005)
        except Exception as exc:
            errors.append(type(exc).__name__)
        finally:
            c.close()
    l1 = [0]; during = [0]; diagnostics = []
    real_popen = subprocess.Popen
    def popen(args, **kw):
        if len(args)>3 and args[1:4]==['-m','blackbox','_worker']:
            args = [args[0], '-B', __file__, '--runtime-worker', *args[4:]]
        return real_popen(args, **kw)
    def observe(store,path):
        l1[0] += 1
        if monitor.child is not None:
            during[0] += 1
        store.execute('INSERT INTO l1(time,path,free,available,boot_id) VALUES(?,?,?,?,?)',
                      (time.time(),path,100*1024**3,100*1024**3,'synthetic'))
    thread = threading.Thread(target=competitor); thread.start()
    try:
        with patch('blackbox.runtime.observe',side_effect=observe), patch('blackbox.runtime.diagnostic',side_effect=lambda e,**kw: diagnostics.append(e)), patch('blackbox.runtime.subprocess.Popen',side_effect=popen):
            for _ in range(3):
                request(monitor.store,'L9',[str(t)+'/*' for t in trees],OPTIONS,cooldown=0)
                monitor.tick()
                deadline = time.monotonic()+30
                while monitor.child is not None:
                    if time.monotonic()>deadline:
                        raise TimeoutError('synthetic scan exceeded 30 seconds')
                    monitor.tick(); time.sleep(.01)
    finally:
        stop.set(); thread.join(timeout=6); monitor.close()
    store = Store(data)
    scans = store.rows("SELECT state,error,scan_version,metrics FROM scans WHERE layer='L9'")
    result = dict(scans=[dict(state=s['state'],version=s['scan_version'],metrics=json.loads(s['metrics'])) for s in scans],
                  l1_samples=l1[0],l1_during_worker=during[0],competitor_transactions=writes[0],
                  competitor_errors=errors,diagnostics=diagnostics,
                  scan_errors=sum(s['error'] is not None for s in scans),
                  integrity=store.db.execute('PRAGMA integrity_check').fetchone()[0])
    assert len(scans)==3 and all(s['state']=='succeeded' and s['scan_version']==2 for s in scans), result
    assert during[0]>0 and writes[0]>0 and not errors and not diagnostics and result['integrity']=='ok', result
    store.close()
    return result


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--runtime-worker':
        from functools import partial
        from blackbox.runtime import worker
        # Synthetic fixture only: isolate capacity/DB behavior from diskutil
        # permissions. Real mount pruning, du, worker signals and locks remain.
        with patch('blackbox.runtime.DuScanner',partial(DuScanner,device_check=lambda p:os.stat(p).st_dev)):
            worker(sys.argv[2],sys.argv[3],int(sys.argv[4]))
        return
    if len(sys.argv)>1 and sys.argv[1]=='--worker':
        comparison_worker(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4]); return
    with tempfile.TemporaryDirectory(prefix='blackbox-l9-verify-',dir='/private/tmp') as temporary:
        base = Path(temporary)
        trees = []
        for label, branches, depth, files in (('wide',50,1,100),('deep',10,10,500)):
            tree = base/label; tree.mkdir(); trees.append(tree)
            for branch in range(branches):
                folder = tree/str(branch); folder.mkdir()
                for level in range(1,depth):
                    folder = folder/str(level); folder.mkdir()
                for number in range(files):
                    (folder/str(number)).write_bytes(b'x'*4096 if number%10==0 else b'')
            results=[]
            # Fresh processes, alternating order; fixtures remain warm-cache.
            for repeat in range(3):
                modes = ('legacy','du') if repeat%2==0 else ('du','legacy')
                for mode in modes:
                    runbase = base/(label+'-'+mode+'-'+str(repeat)); runbase.mkdir()
                    result = subprocess.run([sys.executable,'-B',__file__,'--worker',str(runbase),str(tree),mode],
                                            capture_output=True,text=True,check=True,timeout=30)
                    results.append(json.loads(result.stdout))
            assert len({r['allocated_bytes'] for r in results})==1
            assert len({r['result_count'] for r in results})==1
            print(json.dumps(dict(fixture=label,files=branches*files,depth=depth,comparison=results)),flush=True)
        print(json.dumps(dict(contention=contention(base,trees))),flush=True)


if __name__=='__main__':
    main()
