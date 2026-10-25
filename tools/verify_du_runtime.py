"""Short synthetic integration check; no launchd, installs, or production paths.

Run from the checkout: python3 tools/verify_du_runtime.py
All writes are under a newly created temporary directory, retained for review.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blackbox.runtime import Monitor, observe
from blackbox.store import Store
from blackbox.incidents import GIB, details


def main():
    root = Path(tempfile.mkdtemp(prefix='blackbox-du-runtime-', dir='/private/tmp'))
    data, tree = root/'data', root/'fixture'
    data.mkdir()
    nested = tree/'child'
    nested.mkdir(parents=True)
    for number in range(100):
        (nested/str(number)).write_bytes(b'x'*4096)
    initial = Store(data, create=True)
    initial.close()
    events, diagnostic_events, errors = [], [], []
    stop = threading.Event()
    writes = [0]

    def competitor():
        connection = sqlite3.connect((data/'blackbox.sqlite3').as_uri()+'?mode=rw', uri=True, timeout=5)
        try:
            while not stop.is_set():
                connection.execute('SELECT COUNT(*) FROM scans').fetchone()
                with connection:
                    connection.execute("INSERT OR REPLACE INTO meta VALUES('synthetic_competitor',?)", (str(writes[0]),))
                writes[0] += 1
                stop.wait(.02)
        except BaseException as exc:
            errors.append(type(exc).__name__)
        finally:
            connection.close()

    monitor = Monitor(data, {'L2':[str(tree)+'/*',str(nested)+'/*'], 'L3':[str(nested)+'/*']},
                      intervals=dict(L1=.25), l1_path=str(tree),
                      notify=events.append)
    counter = [0]
    def synthetic_observe(store, path):
        counter[0] += 1
        free = (20 if counter[0] == 1 else 18)*GIB
        store.execute('INSERT INTO l1 VALUES(?,?,?,?,?,?,?,?)',
                      (time.time(),path,100*GIB,100*GIB-free,free,free,'synthetic-boot',None))

    thread = threading.Thread(target=competitor)
    thread.start()
    try:
        with patch('blackbox.runtime.observe', side_effect=synthetic_observe), \
             patch('blackbox.runtime.diagnostic', side_effect=lambda event, **kw: diagnostic_events.append(event)):
            until = time.monotonic()+10
            simulated_due = {'L2': 0, 'L3': 0}
            while time.monotonic() < until:
                # Exercise persisted overdue deadlines without waiting for real
                # daily/hourly slots or changing the production schedule.
                for layer, interval in (('L2', 1.7), ('L3', 1.1)):
                    if time.monotonic() >= simulated_due[layer]:
                        monitor.store.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('due_'+layer, str(time.time()-1)))
                        simulated_due[layer] = time.monotonic()+interval
                monitor.tick()
                time.sleep(.05)
            # Drain accepted requests without creating new periodic scans.
            monitor.intervals['L1'] = 1000
            monitor.targets = {'L2': [], 'L3': []}
            monitor.store.execute("INSERT OR REPLACE INTO meta VALUES('due_L1',?)", (str(time.time()+1000),))
            until = time.monotonic()+10
            while time.monotonic() < until:
                monitor.tick()
                if not monitor.child and not monitor.store.rows("SELECT 1 FROM scans WHERE state='pending'"):
                    break
                time.sleep(.05)
    finally:
        stop.set()
        thread.join(timeout=10)
        monitor.close()

    store = Store(data)
    # Separately exercise actual L1 observation; Incident fixture above uses synthetic free space.
    observe(store, str(tree))
    result = dict(
        evidence=str(root),
        L1_samples=store.db.execute('SELECT COUNT(*) FROM l1').fetchone()[0],
        real_L1_success=store.db.execute('SELECT error FROM l1 ORDER BY time DESC LIMIT 1').fetchone()[0] is None,
        scans=[dict(r) for r in store.db.execute('SELECT layer,state,scan_version,COUNT(*) AS count FROM scans GROUP BY layer,state,scan_version')],
        incidents=[dict(type=i['type'],state=i['state'],stage=i['stage']) for i in details(store)],
        competitor_transactions=writes[0], competitor_errors=errors,
        recording_failures=[e for e in diagnostic_events if e in ('recording_suspended','worker_interrupt_requested')],
        integrity=store.db.execute('PRAGMA integrity_check').fetchone()[0])
    store.close()
    (root/'summary.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    assert result['real_L1_success'] and result['L1_samples'] >= 10
    assert result['competitor_transactions'] >= 10 and not errors and not result['recording_failures']
    assert result['integrity'] == 'ok'
    assert all(any(r['layer']==layer and r['state']=='succeeded' for r in result['scans']) for layer in ('L2','L3'))
    assert result['incidents'] and all(i['stage']=='done' for i in result['incidents'])
    assert not any(r['state'] in ('failed','cancelled','running','pending') for r in result['scans'])


if __name__ == '__main__':
    main()
