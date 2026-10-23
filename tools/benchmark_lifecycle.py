"""Small synthetic retention benchmark. All fixtures live on the repository volume."""
import json
from pathlib import Path
import resource
import sys
import tempfile
import time

from blackbox.metrics import disk_io
from blackbox.store import Store
from blackbox.retention import maintain
from blackbox.incidents import detect, advance, details


def measure(label, operation):
    usage = resource.getrusage(resource.RUSAGE_SELF)
    io = disk_io()
    start = time.monotonic()
    operation()
    elapsed = time.monotonic()-start
    end = resource.getrusage(resource.RUSAGE_SELF)
    io_end = disk_io()
    cpu = end.ru_utime+end.ru_stime-usage.ru_utime-usage.ru_stime
    print(json.dumps(dict(operation=label, elapsed_seconds=elapsed, cpu_seconds=cpu,
                          cpu_percent=100*cpu/max(elapsed, .001),
                          peak_memory_bytes=end.ru_maxrss if sys.platform=='darwin' else end.ru_maxrss*1024,
                          disk_io={k: io_end[k]-io[k] for k in io} if io is not None and io_end is not None else None)))


with tempfile.TemporaryDirectory(prefix='blackbox-lifecycle-', dir=Path(__file__).resolve().parents[1]) as temporary:
    store = Store(temporary, create=True)
    try:
        now = time.time()
        with store.db:
            for hour in range(45*24):
                when = now-(hour+1)*3600
                ident = f'synthetic-{hour}'
                store.db.execute("INSERT INTO scans(id,layer,trigger,targets,options,state,created,started,ended) VALUES(?,'L3','synthetic','[]','{}','succeeded',?,?,?)", (ident, when, when, when+1))
                store.db.executemany('INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)',
                                     [(ident, f'/synthetic/cache/{p}', 'directory', 1000+(hour%24)*100, 1000, 'ok', None, None, None) for p in range(8)])
            store.db.executemany('INSERT INTO l1 VALUES(?,?,?,?,?,?,?,?)',
                                 [(now-60*n, '/synthetic/volume', 1000000, 500000, 500000, 500000, 'synthetic-boot', None) for n in range(10000)])
        measure('retention_1080_scans_8640_results_10000_l1', lambda: maintain(store, now))
        assert store.rows('SELECT count(*) AS n FROM l3_daily')[0]['n'] > 0
        measure('retention_noop', lambda: maintain(store, now))
        store.execute('INSERT INTO l1 VALUES(?,?,?,?,?,?,?,?)', (now+60, '/synthetic/volume', 1000000, 900000, 100000, 100000, 'synthetic-boot', None))
        measure('incident_detection_and_unidentified', lambda: (detect(store, {}, dict(drop_minute=100000, drop_day=900000, low_space=50000)), advance(store, {})))
        assert details(store)[0]['state']=='unidentified'
        print(json.dumps(dict(database_bytes=(Path(temporary)/'blackbox.sqlite3').stat().st_size,
                              synthetic_only=True, integrity=store.rows('PRAGMA integrity_check')[0]['integrity_check'])))
    finally:
        store.close()
