"""Synthetic search/evidence equivalence and concurrent-writer probe; no scans or monitor.

Run from the repository: .venv/bin/python -m tools.benchmark_daily_sources
"""
import argparse
import copy
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import threading
import time

from blackbox.incident_periods import _daily_sources, evaluate
from blackbox.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from daily_sources_reference import _daily_sources as before_sources

ANCHOR = 1800000000


def measure(store, selector):
    writer_result = {}

    def writer():
        db = sqlite3.connect(str(store.directory / 'blackbox.sqlite3'), timeout=5)
        start = time.perf_counter()
        try:
            with db:
                db.execute("INSERT OR REPLACE INTO meta VALUES('synthetic_writer_probe','1')")
            writer_result['result'] = 'committed'
        except sqlite3.OperationalError as error:
            writer_result['result'] = str(error)
        finally:
            writer_result['seconds'] = time.perf_counter() - start
            db.close()

    item = dict(scan_ids=[], candidates=[])
    period = dict(rule='DROP_SPACE_24h', threshold=5*1024**3, baseline_time=ANCHOR)
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        worker = threading.Thread(target=writer)
        worker.start()  # Always attempts a write while the selector owns the writer lock.
        start = time.perf_counter()
        selector(store, item, period, ['/synthetic/*'], 2)
        elapsed = time.perf_counter() - start
    worker.join()
    selected = copy.deepcopy((item, period))
    step = dict(layer='L2', scan_id='current', targets=['/synthetic/*'], periods={period['rule']: period})
    evaluate(store, item, step)
    return elapsed, writer_result, (selected, item, step)


def run(scans, paths):
    with tempfile.TemporaryDirectory(prefix='blackbox-daily-search-') as directory:
        store = Store(directory, create=True)
        try:
            with store.db:
                for n in range(scans):
                    ident = f'history-{n:05d}'
                    ended = ANCHOR-(scans-n)*43200  # 2,190 scans span three years at two/day.
                    store.db.execute("""INSERT INTO scans(id,layer,trigger,targets,options,state,created,started,ended,scan_version)
                        VALUES(?,'L2','periodic','["/synthetic/*"]','{}','partial',?,?,?,2)""",
                        (ident, ended-10, ended-10, ended))
                    store.db.executemany("""INSERT INTO results(scan_id,path,kind,bytes,status)
                        VALUES(?,?,'directory',?,?)""", (
                        (ident, f'/synthetic/folder{p:05d}', None if p%10 == 0 and n == scans-1 else n*1024+p,
                         'error' if p%10 == 0 and n == scans-1 else 'ok') for p in range(paths)))
                current = store.create_scan('L2', ['/synthetic/*'], {})
                store.db.execute("UPDATE scans SET id='current',started=?,ended=?,state='partial' WHERE id=?",
                                 (ANCHOR+86400, ANCHOR+86401, current))
                store.db.executemany("""INSERT INTO results(scan_id,path,kind,bytes,status)
                    VALUES('current',?,'directory',?,?)""", (
                    (f'/synthetic/folder{p:05d}', None if p%13 == 0 else scans*1024+p+3*1024**3,
                     'error' if p%13 == 0 else 'ok') for p in range(paths+1)))
            schema = store.db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            old_seconds, old_writer, expected = measure(store, before_sources)
            times, writers = [], []
            for _ in range(3):
                elapsed, writer, actual = measure(store, _daily_sources)
                if actual != expected:
                    raise AssertionError('Selected sources, candidates, gaps, evidence or protected IDs changed')
                times.append(elapsed)
                writers.append(writer)
            assert schema == store.db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            plans = {
                'scans': [dict(r) for r in store.db.execute("""EXPLAIN QUERY PLAN SELECT id,started,ended,state,error,scan_version
                    FROM scans WHERE layer='L2' AND scan_version=? AND state IN ('succeeded','partial') AND ended<=?
                    ORDER BY ended DESC,id DESC""", (2, ANCHOR))],
                'results': [dict(r) for r in store.db.execute("""EXPLAIN QUERY PLAN SELECT scan_id,path,kind,bytes,status,error
                    FROM results WHERE scan_id=? ORDER BY path,kind""", ('history-00000',))]}
            return dict(scans=scans, history_result_rows=scans*paths, before_seconds=old_seconds,
                        after_seconds=times, after_median=statistics.median(times), before_writer=old_writer,
                        after_writers=writers, selection_evaluation_protection_equal=True, schema_unchanged=True,
                        candidate_count=len(expected[1]['candidates']),
                        unavailable_count=expected[2]['periods']['DROP_SPACE_24h']['unavailable_count'], plans=plans)
        finally:
            store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scans', type=int, default=2190)
    parser.add_argument('--paths', type=int, default=1000)
    args = parser.parse_args()
    if args.scans < 2 or args.paths < 1:
        parser.error('scans >= 2 and paths >= 1 required')
    print(json.dumps(run(args.scans, args.paths), indent=2))
