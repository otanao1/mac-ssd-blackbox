"""Explicit, fail-closed signed monitor bundle operations (never on import)."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

HELPER = Path('Contents/Helpers/Blackbox Monitor.app')


def helper_path(app):
    return Path(app)/HELPER/'Contents/MacOS/BlackboxMonitor'


def command(*args):
    return subprocess.run([str(x) for x in args], check=True, capture_output=True, text=True).stdout


def verify_bundle(app, reference=None):
    """Require valid sealed nested code, HR, matching team and no helper entitlement.

    The installed designated requirements, not an arbitrary candidate team, are
    the trust anchor during updates. Ad-hoc signatures cannot acquire FDA here.
    """
    app = Path(app)
    if not app.is_absolute() or app.is_symlink() or app.resolve() != app:
        raise ValueError('application must be a canonical, non-symlink path')
    info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
    if platform.system() == 'Darwin':
        def version(value):
            parts = value.split('.')
            return tuple(map(int, parts))+(0,)*(3-len(parts))
        if version(info.get('LSMinimumSystemVersion', '14.0')) > version(platform.mac_ver()[0]):
            raise ValueError('application requires a newer macOS version')
        command('/usr/bin/lipo', '-verify_arch', platform.machine(), helper_path(app))
    helper = app/HELPER
    child = plistlib.loads((helper/'Contents/Info.plist').read_bytes())
    if child['CFBundleIdentifier'] != info['CFBundleIdentifier']+'.monitor' or child['CFBundleExecutable'] != 'BlackboxMonitor':
        raise ValueError('unexpected monitor identity')
    team = None
    for item in (app, helper):
        command('/usr/bin/codesign', '--verify', '--deep', '--strict', item)
        result = subprocess.run(['/usr/bin/codesign', '-d', '--verbose=4', str(item)],
                                check=True, capture_output=True, text=True).stderr
        fields = dict(line.split('=', 1) for line in result.splitlines() if '=' in line)
        flags = re.search(r'\bflags=0x([0-9a-f]+)', result)
        current_team = fields.get('TeamIdentifier')
        if current_team in (None, 'not set') or not flags or not int(flags[1], 16) & 0x10000:
            raise ValueError('team-signed hardened runtime is required')
        if team is not None and current_team != team:
            raise ValueError('Viewer and helper must have the same signing team')
        team = current_team
        ent = command('/usr/bin/codesign', '-d', '--entitlements', ':-', item)
        entitlements = plistlib.loads(ent.encode()) if ent.strip() else {}
        if item == helper and entitlements:
            raise ValueError('monitor helper must not have entitlements')
        if reference is not None:
            old = Path(reference) if item == app else Path(reference)/HELPER
            requirements = subprocess.run(['/usr/bin/codesign', '-d', '-r-', str(old)],
                                           check=True, capture_output=True, text=True).stdout
            matches = [line.removeprefix('designated => ') for line in requirements.splitlines()
                       if line.startswith('designated => ')]
            if len(matches) != 1:
                raise ValueError('cannot determine installed designated requirement')
            command('/usr/bin/codesign', '--verify', '--strict', '-R='+matches[0], item)
    return info


def viewer_running(app):
    """Read process executable paths; no app automation, signals or registration."""
    info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
    executable = str(app/'Contents/MacOS'/info['CFBundleExecutable'])
    # ps comm is an absolute executable on macOS. Unknown failures abort update.
    output = command('/bin/ps', '-axo', 'comm=')
    return executable in (line.strip() for line in output.splitlines())


def common_config(app):
    return Path(app)/HELPER/'Contents/Resources/runtime/config'


def tree_contents(directory):
    result = {}
    for path in Path(directory).rglob('*'):
        if path.is_symlink():
            raise ValueError('common configuration must not contain symbolic links')
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = path.read_bytes()
    return result


def update(private, candidate, *, coordinator=False):
    """Run replacement from a sealed temporary copy, never the app being moved."""
    private = Path(private).expanduser().resolve(strict=True)
    manifest = json.loads((private/'installation.json').read_text())
    if manifest.get('mode') != 'bundle':
        raise ValueError('update requires a bundle installation; legacy migration is an explicit reinstall')
    app = Path(manifest['app'])
    info = verify_bundle(app)
    if info['CFBundleIdentifier'] != manifest['bundle_id']:
        raise ValueError('installed bundle identity does not match installation')
    candidate = Path(candidate).expanduser().resolve(strict=True)
    verify_bundle(candidate, reference=app)
    if coordinator:
        own_app = Path(sys.executable).resolve().parents[5]
        if own_app == app or helper_path(own_app) != Path(sys.executable).resolve():
            raise ValueError('update coordinator must run outside the installed application')
        verify_bundle(own_app, reference=app)
        return _replace(private, candidate)
    with tempfile.TemporaryDirectory(prefix='.blackbox-coordinator-', dir=app.parent) as tmp:
        copy = Path(tmp)/app.name
        shutil.copytree(app, copy, symlinks=True)
        verify_bundle(copy, reference=app)
        result = command(helper_path(copy), '--config-dir', private, 'update-app', candidate, '--coordinator')
        return json.loads(result)


def _replace(private, candidate):
    from .runtime import lock
    owner = lock(private, 'update.lock')
    try:
        return _replace_locked(private, candidate)
    finally:
        os.close(owner)


def _replace_locked(private, candidate):
    """Replace only an installed app, retaining runtime, configuration and DB.

    Candidate copy and signature checks precede stop. The old sealed bundle is
    retained at a non-.app backup path for explicit rollback with this command.
    Only an owned, previously loaded job is restarted. A stopped install stays
    stopped. Failure restores the old app before attempting the owned restart.
    """
    from . import installation as install
    from .destination import verify
    private = Path(private).expanduser().resolve(strict=True)
    manifest = json.loads((private/'installation.json').read_text())
    if manifest.get('mode') != 'bundle':
        raise ValueError('update requires a bundle installation; legacy migration is an explicit reinstall')
    app = Path(manifest['app'])
    candidate = Path(candidate).expanduser().resolve(strict=True)
    if candidate == app or candidate.is_relative_to(app) or app.is_relative_to(candidate):
        raise ValueError('candidate must be separate from the installed application')
    verify_bundle(app)
    verify_bundle(candidate, reference=app)
    if viewer_running(app):
        raise ValueError('quit Viewer before updating; monitoring is still running')
    expected = install.uninstall_job(private, manifest)
    agent = Path(manifest['agent'])
    target = install.domain()+'/'+install.installation_label(manifest)
    identity = json.loads((private/'local.json').read_text())['storage']
    storage = verify(identity)
    # Disk layout and lock paths come from the same validated storage identity.
    data = Path(manifest['data'])
    if not data.is_absolute() or data.resolve() != data or storage != data:
        raise ValueError('invalid data path')
    for protected in (private, data, Path(manifest['runtime'])):
        if protected.is_relative_to(app) or app.is_relative_to(protected):
            raise ValueError('application overlaps protected installation/data')
    backup = app.with_name(app.name+('.replaced' if candidate == app.with_name(app.name+'.previous') else '.previous'))
    if backup.exists():
        raise ValueError('previous backup exists; retain/move it before another update')
    runtime = Path(manifest['runtime'])
    config = runtime/'config'
    config_backup = runtime/('config.replaced' if backup.name.endswith('.replaced') else 'config.previous')
    if config_backup.exists() or config_backup.is_symlink():
        raise ValueError('previous common configuration backup exists')
    if tree_contents(config) != tree_contents(common_config(app)):
        raise ValueError('common configuration was modified; move overrides to private conf.d before updating')
    with tempfile.TemporaryDirectory(prefix='.blackbox-update-', dir=app.parent) as tmp:
        stage = Path(tmp)/app.name
        shutil.copytree(candidate, stage, symlinks=True)
        verify_bundle(stage, reference=app)
        with tempfile.TemporaryDirectory(prefix='.config-update-', dir=runtime) as config_tmp:
            config_stage = Path(config_tmp)/'config'
            shutil.copytree(common_config(stage), config_stage)
            was_running = install.stop_owned_job(target, expected, agent)
            locks = ExitStack()
            try:
                _replace_stopped(private, data, identity, expected, agent, app, backup, stage,
                                 config, config_backup, config_stage, locks, was_running)
            finally:
                locks.close()
        if was_running:
            try:
                install.launchctl('bootstrap', install.domain(), str(agent))
            except BaseException:
                # Do not mutate a partially restarted service or delete its code.
                raise RuntimeError('new app installed but restart failed; backup retained; inspect status before recovery')
    return dict(updated=True, restarted=was_running, backup=str(backup),
                config_backup=str(config_backup), data_preserved=True)


def _replace_stopped(private, data, identity, expected, agent, app, backup, stage,
                     config, config_backup, config_stage, locks, was_running):
    from . import installation as install
    from .runtime import lock
    from .destination import verify
    # A cleanup/ownership failure before replacement leaves the job stopped.
    # Never restart against a DB whose owner or storage identity is uncertain.
    owner = install.acquire_stopped_service(private, wait=was_running)
    locks.callback(os.close, owner)
    for name in ('monitor.lock', 'scan.lock'):
        fd = lock(data, name)
        locks.callback(os.close, fd)
    verify(identity)
    install.verify_agent(agent, expected)
    if viewer_running(app):
        raise ValueError('Viewer started during preparation; update aborted; monitor remains stopped')
    moved = False
    config_moved = False
    try:
        app.rename(backup)
        moved = True
        stage.rename(app)
        verify_bundle(app, reference=backup)
        config.rename(config_backup)
        config_moved = True
        config_stage.rename(config)
    except BaseException:
        if config_moved:
            if config.exists():
                shutil.rmtree(config)
            config_backup.rename(config)
        if moved:
            if app.exists():
                shutil.rmtree(app)
            backup.rename(app)
        if was_running:
            locks.close()
            install.launchctl('bootstrap', install.domain(), str(agent))
        raise
