"""Bounded real-Mac benchmark: python -m tools.benchmark.

Creates and scans only a temporary fixture; never loads broad target configs.
"""
import json
import os
from pathlib import Path
import tempfile
import time
from blackbox.runtime import Monitor, request
from blackbox.store import Store

with tempfile.TemporaryDirectory(prefix='blackbox-fixture-') as temporary:
    base = Path(temporary).resolve()
    data, tree = base/'data', base/'fixture'
    data.mkdir(mode=0o700)
    tree.mkdir()
    for d in range(20):
        folder = tree/str(d)
        folder.mkdir()
        for f in range(100):
            (folder/str(f)).write_bytes(b'x'*4096 if f % 10 == 0 else b'')
    Store(data, create=True).close()
    monitor = Monitor(data, l1_path='/System/Volumes/Data')
    try:
        monitor.tick()
        for layer in ('L2','L3','L9'):
            started = time.monotonic()
            requested = request(monitor.store, layer, [str(tree)+'/*'], {'threshold': 65536, 'max_depth': 7})
            monitor.dispatch()
            while monitor.child:
                monitor.reap()
                time.sleep(.02)
            row = monitor.store.rows('SELECT * FROM scans ORDER BY created DESC LIMIT 1')[0]
            results = monitor.store.rows('SELECT kind,status,count(*) AS n FROM results WHERE scan_id=? GROUP BY kind,status',(row['id'],))
            print(json.dumps(dict(layer=layer,state=row['state'],metrics=json.loads(row['metrics']),result_counts=results,wall_seconds=time.monotonic()-started)))
            assert row['state'] == 'succeeded', row['error']
        l1 = monitor.store.rows('SELECT * FROM l1')
        assert len(l1) == 1 and l1[0]['error'] is None
        print('L1 observation: OK; fixture: 2000 files, 20 directories, 819200 payload bytes')
    finally:
        monitor.close()
