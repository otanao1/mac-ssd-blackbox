"""Development harness for #6. The supported installed CLI belongs to #8."""
import argparse
import json
from pathlib import Path
import signal
import sqlite3
import sys
import time
from .runtime import Monitor, request, worker
from .store import Store


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '_worker':
        worker(sys.argv[2], sys.argv[3], int(sys.argv[4]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True, help='existing private data directory')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--config', type=Path, help='local JSON config, outside Git')
    req = sub.add_parser('scan')
    req.add_argument('layer', choices=['L2', 'L3', 'L9'])
    req.add_argument('paths', nargs='+')
    req.add_argument('--threshold', type=int, default=1024**3)
    req.add_argument('--max-depth', type=int, default=7)
    sub.add_parser('init')
    sub.add_parser('status')
    hist = sub.add_parser('history')
    hist.add_argument('--limit', type=int, default=20)
    result = sub.add_parser('results')
    result.add_argument('scan_id')
    cancel = sub.add_parser('cancel')
    cancel.add_argument('scan_id')
    sub.add_parser('clear-waiting')
    args = parser.parse_args()
    if args.command == 'run':
        config = json.loads(args.config.read_text()) if args.config else {}
        targets = config.get('targets', {'L2': [], 'L3': []})
        for layer, directory in config.get('conf_dirs', {}).items():
            targets.setdefault(layer, [])
            for path in sorted(Path(directory).glob('*.conf')):
                targets[layer].extend(line.strip() for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#'))
        monitor = Monitor(args.data, targets, config.get('intervals'), config.get('l1_path', '/System/Volumes/Data'),
                          retention=config.get('retention'), incident_thresholds=config.get('incident_thresholds'))
        stopped = False
        def stop(signum, frame):
            nonlocal stopped
            stopped = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            while not stopped:
                try:
                    monitor.tick()
                except (OSError, sqlite3.Error):
                    pass
                time.sleep(.2)
        finally:
            monitor.close()
        return
    store = Store(args.data, create=args.command == 'init')
    try:
        if args.command == 'init':
            output = {'initialized': True}
        elif args.command == 'scan':
            output = request(store, args.layer, args.paths, {'threshold': args.threshold, 'max_depth': args.max_depth})
        elif args.command == 'status':
            scans = store.rows("SELECT * FROM scans WHERE state IN ('running','pending') ORDER BY created")
            for scan in scans:
                scan['elapsed_seconds'] = time.time()-scan['started'] if scan['started'] else None
            output = dict(scans=scans, waiting=store.rows('SELECT * FROM waiting ORDER BY requested'))
        elif args.command == 'history':
            output = dict(scans=store.rows('SELECT * FROM scans ORDER BY created DESC LIMIT ?', (max(1, args.limit),)), l1=store.rows('SELECT * FROM l1 ORDER BY time DESC LIMIT ?', (max(1, args.limit),)))
        elif args.command == 'results':
            # Stream potentially large results, rather than loading history into memory.
            print('[')
            separator = ''
            for row in store.db.execute('SELECT * FROM results WHERE scan_id=? ORDER BY path,kind', (args.scan_id,)):
                print(separator + json.dumps(dict(row)), end='')
                separator = ',\n'
            print('\n]')
            return
        elif args.command == 'cancel':
            output = dict(updated=store.execute("UPDATE scans SET cancel=1 WHERE id=? AND state IN ('pending','running')", (args.scan_id,)).rowcount)
        else:
            output = dict(removed=store.execute('DELETE FROM waiting').rowcount)
        print(json.dumps(output, indent=2))
    finally:
        store.close()

if __name__ == '__main__':
    main()
