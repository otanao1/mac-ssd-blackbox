"""Generate #39 Viewer evidence through real Incident logic, using synthetic rows only."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch

from blackbox.incidents import GIB, advance, detect, details
from blackbox.runtime import DEFAULT_OPTIONS
from blackbox.store import Store


def create(folder):
    directory = Path(folder) / 'period-data'
    directory.mkdir(parents=True, exist_ok=False)
    store = Store(directory, create=True)
    now = datetime(2026, 10, 28, 1, tzinfo=timezone.utc).timestamp()
    anchor = now - 86400
    targets = dict(L3=['/fixture/cache/*'], L2=['/fixture/*'])

    def scan(layer, when, values, ident=None):
        ident = ident or store.create_scan(layer, targets.get(layer, [p+'/*' for p in values]), DEFAULT_OPTIONS)
        store.execute("UPDATE scans SET started=?,ended=?,state='succeeded' WHERE id=?", (when, when+1, ident))
        for path, value in values.items():
            store.execute('INSERT INTO results(scan_id,path,kind,bytes,status) VALUES(?,?,?,?,?)',
                          (ident, path, 'directory', value, 'ok'))
        return ident

    def observe(when, free):
        store.execute('INSERT INTO l1 VALUES(?,?,?,?,?,?,?,?)',
                      (when, '/fixture/disk', 100*GIB, 100*GIB-free, free, free, 'fixture-boot', None))
        detect(store, targets)

    try:
        scan('L2', anchor-100, {'/fixture/other': 7*GIB})
        scan('L3', now-100, {'/fixture/cache': GIB})
        scan('L9', anchor-100, {'/fixture/cache': GIB, '/fixture/other': 7*GIB})
        scan('L9', now-100, {'/fixture/cache': 2*GIB, '/fixture/other': 10*GIB})
        observe(anchor, 20*GIB)
        observe(now-60, 20*GIB)
        observe(now, 14*GIB)
        ident = details(store)[0]['id']
        with patch('blackbox.incidents.time.time', return_value=now+2):
            step = details(store, ident)[0]['investigation'][-1]
            scan('L3', now+1, {'/fixture/cache': 2*GIB}, step['scan_id'])
            advance(store, targets)
            step = details(store, ident)[0]['investigation'][-1]
            scan('L2', now+1, {'/fixture/other': 10*GIB, '/fixture/unrecorded': 9*GIB}, step['scan_id'])
            advance(store, targets)
            scan('L9', now+5, {'/fixture/cache': 2*GIB, '/fixture/other': 10*GIB})
            advance(store, targets)
        item = details(store, ident)[0]
        assert item['state'] == 'identified'
        (Path(folder) / 'period-evidence.json').write_text(json.dumps(item, ensure_ascii=False, indent=2)+'\n')
    finally:
        store.close()
    print('Created synthetic period fixture through detect/advance (no scanner or monitor)')


if __name__ == '__main__':
    create(sys.argv[1])
