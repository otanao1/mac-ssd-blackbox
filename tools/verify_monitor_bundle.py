#!/usr/bin/env python3
"""Small synthetic integration check; no FDA changes or production jobs/data.

Input MUST be a DEV Issue38 bundle. All install paths, common-target overrides,
DB and scan files are temporary. Default mode does not bootstrap launchd.
--launchd uses only a temporary Issue38 job. Neither mode launches Viewer,
registers a Widget, or performs a broad scan. Requires macOS codesign, diskutil and Unix sockets.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackbox.bundle import helper_path, verify_bundle
from blackbox.destination import mounted_volume
from blackbox.runtime import lock


def verify(app, candidate=None):
    app = Path(app).expanduser().resolve(strict=True)
    info = verify_bundle(app)
    helper_root = app/'Contents/Helpers/Blackbox Monitor.app'
    manifest = json.loads((helper_root/'Contents/Resources/runtime-manifest.json').read_text())
    for relative, digest in manifest['files'].items():
        assert hashlib.sha256((helper_root/relative).read_bytes()).hexdigest() == digest, relative
    if info['CFBundleIdentifier'] != 'io.github.otanao1.blackbox-viewer.dev.issue38':
        raise ValueError('only the DEV Issue38 application is accepted')
    with tempfile.TemporaryDirectory(prefix='blackbox-issue38-') as tmp:
        root = Path(tmp).resolve()
        source_app = app
        app = root/'Blackbox Viewer DEV Issue38.app'
        shutil.copytree(source_app, app, symlinks=True)
        helper = helper_path(app)
        private, runtime, data, fixture = (root/n for n in ('private', 'runtime', 'data', 'fixture'))
        data.mkdir(mode=0o700); fixture.mkdir()
        for i in range(32):
            (fixture/f'sample-{i}').write_bytes(b'x'*4096)
        env = dict(os.environ, PYTHONPATH=str(root/'poison'), PYTHONHOME='/nonexistent', PYTHONUSERBASE=str(root/'poison'))
        poison = root/'poison'
        (poison/'blackbox').mkdir(parents=True)
        (poison/'blackbox/__init__.py').write_text('raise RuntimeError("untrusted module imported")\n')
        (poison/'sqlite3.py').write_text('raise RuntimeError("untrusted sqlite3 imported")\n')
        def call(*args):
            p = subprocess.run([str(helper), *map(str, args)], capture_output=True, text=True,
                               check=True, env=env, cwd=poison, timeout=40)
            return json.loads(p.stdout)
        installed = call('--config-dir', private, 'install', '--app-bundle', app,
                         '--storage', data, '--mount', mounted_volume(data)['mount'],
                         '--runtime', runtime, '--cli', root/'bb')
        expected_label = info['CFBundleIdentifier']+'.monitor'
        assert plistlib.loads((runtime/'launchd.plist').read_bytes())['Label'] == expected_label
        # Empty replacement files disable bundled broad targets; only our fixture is scanned.
        for layer in ('l2', 'l3'):
            directory = private/layer/'conf.d'; directory.mkdir(parents=True)
            (directory/'common.conf').write_text('')
        local = json.loads((private/'local.json').read_text())
        local.update(l1_path=str(fixture), intervals={'L1': .2})
        (private/'local.json').write_text(json.dumps(local))
        before = (data/'blackbox.sqlite3').stat().st_ino
        base = ['--config-dir', private, '--common-dir', runtime/'config']
        process = subprocess.Popen([str(helper), *map(str, base), 'run'], env=env, cwd=poison,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic()+20
            while True:
                status = call(*base, 'status')
                if status.get('running'): break
                if process.poll() is not None: raise RuntimeError(process.stderr.read())
                if time.monotonic() > deadline: raise TimeoutError('monitor startup')
                time.sleep(.1)
            ident = call(*base, 'scan', 'L2', str(fixture)+'/*')['scan_id']
            deadline = time.monotonic()+40
            while True:
                history = call(*base, 'history', '--layer', 'L2')
                scans = history if isinstance(history, list) else history.get('scans', [])
                result = next((row for row in scans if row['id'] == ident), None)
                if result and result['state'] not in ('pending', 'running'): break
                if time.monotonic() > deadline: raise TimeoutError('fixture worker')
                time.sleep(.1)
            assert result['state'] == 'succeeded', result['state']
            l1 = call(*base, 'history', '--layer', 'L1')
            assert l1['l1']
        finally:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=20)
            process.stderr.close()
        assert process.returncode == 0
        for directory, name in ((private, 'service.lock'), (data, 'monitor.lock'), (data, 'scan.lock')):
            fd = lock(directory, name); os.close(fd)
        assert (data/'blackbox.sqlite3').stat().st_ino == before
        updated = False
        if candidate:
            candidate = Path(candidate).expanduser().resolve(strict=True)
            info = verify_bundle(candidate, reference=app)
            if info['CFBundleIdentifier'] != installed['bundle_id']:
                raise ValueError('candidate must be the same DEV Issue38 application')
            result = call(*base, 'update-app', candidate)
            assert result['updated'] and not result['restarted']
            assert (data/'blackbox.sqlite3').stat().st_ino == before
            result = call(*base, 'update-app', result['backup'])
            assert result['updated'] and not result['restarted']
            updated = True
        tampered = root/'Tampered.app'
        shutil.copytree(app, tampered, symlinks=True)
        code = tampered/'Contents/Helpers/Blackbox Monitor.app/Contents/Resources/runtime/blackbox/__init__.py'
        code.write_text(code.read_text()+'\n# synthetic seal violation\n')
        rejected = subprocess.run([str(helper_path(tampered)), '--help'], capture_output=True,
                                  timeout=20, env=env)
        assert rejected.returncode == 78
        return dict(signature=True, manifest_hashes_verified=True, isolated_environment=True, shared_python_required=False,
                    bundle_install=True, production_label_used=False, native_worker_scan='succeeded',
                    scan_files=32, graceful_stop=True, locks_released=True, database_inode_preserved=True,
                    signed_update_and_rollback=updated, tampered_resources_rejected=True,
                    fda_on_tested=False, launchd_tested=False, viewer_widget_started=False,
                    temporary_files_removed=True)


def verify_launchd(app, candidate):
    """Temporarily bootstrap ONLY the isolated Issue38 label, without RunAtLoad placement."""
    from blackbox.installation import control, acquire_stopped_service
    app, candidate = (Path(p).expanduser().resolve(strict=True) for p in (app, candidate))
    info = verify_bundle(app)
    other = verify_bundle(candidate, reference=app)
    if info['CFBundleIdentifier'] != 'io.github.otanao1.blackbox-viewer.dev.issue38' or other['CFBundleIdentifier'] != info['CFBundleIdentifier']:
        raise ValueError('only the DEV Issue38 application is accepted')
    root = Path(tempfile.mkdtemp(prefix='blackbox-issue38-launchd-', dir='/private/tmp')).resolve()
    private, runtime, data, fixture = (root/n for n in ('private', 'runtime', 'data', 'fixture'))
    may_be_loaded = False
    cleanup_safe = True
    try:
        local_app = root/'Blackbox Viewer DEV Issue38.app'
        shutil.copytree(app, local_app, symlinks=True)
        helper = helper_path(local_app)
        data.mkdir(mode=0o700); fixture.mkdir()
        for i in range(32):
            (fixture/f'sample-{i}').write_bytes(b'x'*4096)
        base = ['--config-dir', private, '--common-dir', runtime/'config']
        def call(*args):
            result = subprocess.run([str(helper), *map(str, base), *map(str, args)],
                                    capture_output=True, text=True, timeout=60)
            if result.returncode: raise RuntimeError(result.stderr)
            return json.loads(result.stdout)
        call('install', '--app-bundle', local_app, '--storage', data,
             '--mount', mounted_volume(data)['mount'], '--runtime', runtime, '--cli', root/'bb')
        for layer in ('l2', 'l3'):
            directory = private/layer/'conf.d'; directory.mkdir(parents=True)
            (directory/'common.conf').write_text('')
        local = json.loads((private/'local.json').read_text())
        local.update(l1_path=str(fixture), intervals={'L1': .2})
        (private/'local.json').write_text(json.dumps(local))
        before = (data/'blackbox.sqlite3').stat().st_ino
        def wait_status(running):
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                try:
                    status = call('status')
                except RuntimeError as exc:
                    # A socket accepted immediately before shutdown can close without JSON.
                    if running or 'Expecting value:' not in str(exc): raise
                    time.sleep(.1)
                    continue
                if status.get('running', False) == running: return status
                time.sleep(.1)
            raise TimeoutError('isolated launchd status')
        may_be_loaded = True
        call('start')
        first = wait_status(True)
        ident = call('scan', 'L2', str(fixture)+'/*')['scan_id']
        deadline = time.monotonic()+20
        while True:
            scan = next(row for row in call('history', '--layer', 'L2')['scans'] if row['id'] == ident)
            if scan['state'] not in ('pending', 'running'): break
            if time.monotonic() > deadline: raise TimeoutError('isolated launchd worker')
            time.sleep(.1)
        assert scan['state'] == 'succeeded'
        update = call('update-app', candidate)
        assert update['restarted']
        second = wait_status(True)
        assert first['pid'] != second['pid']
        restored = call('update-app', update['backup'])
        assert restored['restarted']
        third = wait_status(True)
        assert third['pid'] != second['pid']
        assert (data/'blackbox.sqlite3').stat().st_ino == before
        call('stop')
        wait_status(False)
        owner = acquire_stopped_service(private, wait=True); os.close(owner)
        result = call('uninstall')
        assert result['uninstalled'] and not runtime.exists()
        assert (data/'blackbox.sqlite3').stat().st_ino == before
        may_be_loaded = False
        return dict(launchd_start=True, native_worker_scan='succeeded', running_update=True,
                    running_rollback=True, pid_changed_each_restart=True, database_inode_preserved=True,
                    uninstall_preserves_data=True, production_label_used=False,
                    persistent_launchagent_written=False, fda_on_tested=False, relogin_tested=False,
                    temporary_files_removed=True)
    finally:
        if may_be_loaded:
            try:
                control('stop', private)
                owner = acquire_stopped_service(private, wait=True); os.close(owner)
                for name in ('monitor.lock', 'scan.lock'):
                    fd = lock(data, name); os.close(fd)
            except Exception:
                cleanup_safe = False
                print('Isolated job cleanup could not be verified; retained workspace: '+str(root), file=sys.stderr)
        if cleanup_safe:
            shutil.rmtree(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True)
    parser.add_argument('--candidate', help='optional signed DEV candidate for stopped update/rollback')
    parser.add_argument('--launchd', action='store_true', help='temporary isolated Issue38 job; requires candidate')
    args = parser.parse_args()
    if args.launchd and not args.candidate: parser.error('--launchd requires --candidate')
    result = verify_launchd(args.app, args.candidate) if args.launchd else verify(args.app, args.candidate)
    print(json.dumps(result, indent=2))
