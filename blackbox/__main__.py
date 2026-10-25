"""mac-ssd-blackbox CLI. Output is JSON; diagnostics use stderr."""
import argparse
import json
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from .runtime import Monitor, request, worker
from .store import Store


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '_worker':
        worker(sys.argv[2], sys.argv[3], int(sys.argv[4]), json.loads(sys.argv[5]) if len(sys.argv) > 5 else None)
        return
    parser = argparse.ArgumentParser(description=__doc__)
    from .configuration import DEFAULT_CONFIG, DEFAULT_PRIVATE
    parser.add_argument('--data', help='development harness only: existing private data directory')
    parser.add_argument('--config-dir', type=Path, default=DEFAULT_PRIVATE)
    parser.add_argument('--common-dir', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--json', action='store_true', help='JSON output (also the default)')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--config', type=Path, help='local JSON config, outside Git')
    req = sub.add_parser('scan')
    req.add_argument('layer', choices=['L2', 'L3', 'L9'])
    req.add_argument('paths', nargs='+')
    req.add_argument('--threshold', type=int, default=1024**3)
    req.add_argument('--max-depth', type=int, default=7)
    sub.add_parser('periodic-l2', help='request configured periodic L2 without changing its schedule')
    sub.add_parser('init')
    sub.add_parser('status')
    hist = sub.add_parser('history')
    hist.add_argument('--limit', type=int, default=20)
    hist.add_argument('--layer', choices=['L1', 'L2', 'L3', 'L9'])
    result = sub.add_parser('results')
    result.add_argument('scan_id')
    cancel = sub.add_parser('cancel')
    cancel.add_argument('scan_id')
    sub.add_parser('clear-waiting')
    for command in ('start', 'stop', 'reload', 'config'):
        sub.add_parser(command)
    incident = sub.add_parser('incidents')
    incident.add_argument('incident_id', nargs='?')
    incident.add_argument('--limit', type=int, default=20)
    setup = sub.add_parser('install')
    setup.add_argument('--storage', type=Path, required=True, help='existing private storage directory')
    setup.add_argument('--mount', type=Path, required=True, help='explicitly confirmed mount point')
    from .installation import DEFAULT_RUNTIME, DEFAULT_CLI
    setup.add_argument('--runtime', type=Path, default=DEFAULT_RUNTIME)
    setup.add_argument('--cli', type=Path, default=DEFAULT_CLI)
    setup.add_argument('--register', action='store_true', help='place LaunchAgent and bootstrap (starts monitoring)')
    remove = sub.add_parser('uninstall')
    remove.add_argument('--remove-configuration', action='store_true')
    args = parser.parse_args()
    if not args.data:
        return installed(args)
    if args.command in ('start', 'stop', 'reload', 'config', 'install', 'uninstall', 'periodic-l2'):
        parser.error('this command requires installed configuration, without --data')
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
            output = history(store, args.layer, args.limit)
        elif args.command == 'incidents':
            from .incidents import details
            output = details(store, args.incident_id, args.limit)
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

def history(store, layer, limit):
    limit = max(1, limit)
    output = {}
    if layer in (None, 'L1'):
        output['l1'] = store.rows('SELECT * FROM l1 ORDER BY time DESC LIMIT ?', (limit,))
    if layer != 'L1':
        output['scans'] = store.rows('SELECT * FROM scans '+('WHERE layer=? ' if layer else '')+
                                    'ORDER BY created DESC LIMIT ?', (layer, limit) if layer else (limit,))
    if layer in (None, 'L3'):
        output['l3_daily'] = store.rows('SELECT * FROM l3_daily ORDER BY bucket DESC LIMIT ?', (limit,))
    return output


def installed(args):
    from .configuration import load
    from .installation import control, install, uninstall
    from .service import call, run
    if args.command == 'install':
        output = install(Path(__file__).resolve().parent.parent, args.storage, args.mount,
                         private=args.config_dir, runtime=args.runtime, cli=args.cli, register=args.register)
    elif args.command == 'uninstall':
        output = uninstall(args.config_dir, remove_configuration=args.remove_configuration)
    elif args.command in ('start', 'stop'):
        if args.command == 'start':
            load(args.common_dir, args.config_dir)
        output = control(args.command, args.config_dir)
    elif args.command == 'run':
        if args.config:
            raise ValueError('use --config-dir for installed configuration')
        return run(args.common_dir, args.config_dir)
    elif args.command in ('reload', 'config', 'status', 'scan', 'periodic-l2'):
        fields = {}
        if args.command == 'scan':
            fields = dict(layer=args.layer, paths=args.paths,
                          options=dict(threshold=args.threshold, max_depth=args.max_depth))
        try:
            output = call(args.config_dir, args.command, **fields)
        except (FileNotFoundError, ConnectionRefusedError):
            if args.command == 'status':
                output = dict(running=False, scans=[], waiting=[])
            elif args.command == 'config':
                output = dict(running=False, loaded=False, sources=load(args.common_dir, args.config_dir)['sources'])
            else:
                raise ValueError('monitor is not running; start it first')
    else:
        try:
            identity = call(args.config_dir, 'config')['storage_identity']
        except (FileNotFoundError, ConnectionRefusedError):
            identity = load(args.common_dir, args.config_dir).get('storage')
        if not identity:
            raise ValueError('storage is required; run install')
        if args.command == 'init':
            raise ValueError('installed storage is initialized only by install')
        store = Store(identity['path'], identity=identity)
        try:
            if args.command == 'history':
                output = history(store, args.layer, args.limit)
            elif args.command == 'incidents':
                from .incidents import details
                output = details(store, args.incident_id, args.limit)
                if args.incident_id and not output:
                    raise ValueError('Incident not found')
            elif args.command == 'cancel':
                output = dict(updated=store.execute("UPDATE scans SET cancel=1 WHERE id=? AND state IN ('pending','running')", (args.scan_id,)).rowcount)
            elif args.command == 'clear-waiting':
                output = dict(removed=store.execute('DELETE FROM waiting').rowcount)
            elif args.command == 'results':
                print('[')
                separator = ''
                for row in store.db.execute('SELECT * FROM results WHERE scan_id=? ORDER BY path,kind', (args.scan_id,)):
                    store.check()
                    print(separator+json.dumps(dict(row)), end='')
                    separator = ',\n'
                print('\n]')
                return
        finally:
            store.close()
    print(json.dumps(output, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, sqlite3.Error, subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps(dict(error=str(exc))), file=sys.stderr)
        sys.exit(1)
