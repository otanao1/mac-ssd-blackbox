"""Explicit user installation and launchd operations; never invoked on import."""
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys

from .configuration import DEFAULT_PRIVATE
from .destination import initialize

LABEL = 'io.github.otanao1.mac-ssd-blackbox'
DEFAULT_RUNTIME = Path.home() / 'Library/Application Support/mac-ssd-blackbox-runtime'
DEFAULT_CLI = Path.home() / '.local/bin/mac-ssd-blackbox'


def launchctl(*arguments, check=True):
    return subprocess.run(['/bin/launchctl', *arguments], capture_output=True, text=True, check=check, timeout=30)


def domain():
    return f'gui/{os.getuid()}'


def control(action, private):
    manifest = json.loads((Path(private) / 'installation.json').read_text())
    plist = Path(manifest['agent'])
    target = domain()+'/'+LABEL
    if action == 'start':
        if launchctl('print', target, check=False).returncode:
            launchctl('bootstrap', domain(), str(plist))
        else:
            launchctl('kickstart', target)
    elif action == 'stop':
        if not launchctl('print', target, check=False).returncode:
            launchctl('bootout', target)
    return dict(action=action, label=LABEL)


def plist_bytes(python, runtime, private):
    return plistlib.dumps(dict(Label=LABEL,
        ProgramArguments=[str(python), '-m', 'blackbox', '--config-dir', str(private),
                          '--common-dir', str(Path(runtime)/'config'), 'run'],
        WorkingDirectory=str(runtime), RunAtLoad=True,
        KeepAlive={'SuccessfulExit': False}, ThrottleInterval=10,
        ProcessType='Background', Umask=0o077,
        StandardOutPath='/dev/null', StandardErrorPath='/dev/null'))


def install(source, directory, mount, *, private=DEFAULT_PRIVATE, runtime=DEFAULT_RUNTIME,
            cli=DEFAULT_CLI, register=False):
    source = Path(source).resolve(strict=True)
    private, runtime, cli = (Path(p).expanduser().resolve() for p in (private, runtime, cli))
    from .service import address
    address(private)
    if sys.version_info < (3, 11):
        raise ValueError('Python 3.11 or later is required')
    # Refuse destructive replacement or overlap with data/the source checkout.
    data = Path(directory).expanduser().resolve(strict=True)
    locations = [source, data, private, runtime, cli]
    for index, left in enumerate(locations):
        for right in locations[index+1:]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise ValueError('source, data, configuration, runtime and CLI locations must not overlap')
    if runtime.exists() or cli.exists() or cli.is_symlink() or private.is_symlink():
        raise ValueError('runtime or CLI already exists; uninstall the runtime first')
    retained = None
    if private.exists():
        old = json.loads((private/'installation.json').read_text())
        if old['runtime'] != str(runtime) or old['cli'] != str(cli) or old['data'] != str(data):
            raise ValueError('retained configuration belongs to another installation')
        retained = json.loads((private/'local.json').read_text())
        from .destination import verify
        verify(retained['storage'])
    agent = Path.home() / 'Library/LaunchAgents' / (LABEL+'.plist')
    if register and agent.exists():
        raise ValueError('LaunchAgent already exists')
    identity = initialize(data, mount)
    if retained is not None and retained['storage'] != identity:
        raise ValueError('storage identity changed; retained configuration was not replaced')
    runtime.mkdir(mode=0o700, parents=True)
    private.mkdir(mode=0o700, parents=True, exist_ok=retained is not None)
    for name in ('blackbox', 'config'):
        shutil.copytree(source/name, runtime/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'local*.json'))
    # No packages/network/global Python changes; isolate an existing supported interpreter.
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(runtime/'venv')], check=True)
    python = runtime/'venv/bin/python'
    if retained is None:
        (private/'local.json').write_text(json.dumps(dict(storage=identity), indent=2)+'\n')
    os.chmod(private/'local.json', 0o600)
    prepared = runtime/'launchd.plist'
    prepared.write_bytes(plist_bytes(python, runtime, private))
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text('#!/bin/sh\ncd '+shlex.quote(str(runtime))+' || exit 1\nexec '+shlex.quote(str(python))+
                   ' -m blackbox --config-dir '+shlex.quote(str(private))+' --common-dir '+
                   shlex.quote(str(runtime/'config'))+' "$@"\n')
    cli.chmod(0o700)
    manifest = dict(runtime=str(runtime), cli=str(cli), agent=str(agent if register else prepared),
                    registered=register, data=str(data))
    (private/'installation.json').write_text(json.dumps(manifest, indent=2)+'\n')
    if register:
        agent.parent.mkdir(parents=True, exist_ok=True)
        agent.write_bytes(prepared.read_bytes())
        launchctl('bootstrap', domain(), str(agent))
    return dict(installed=True, **manifest, configuration=str(private))


def uninstall(private, *, remove_configuration=False):
    private = Path(private).expanduser().absolute()
    manifest = json.loads((private/'installation.json').read_text())
    # Stop through launchd before deleting executables; failure aborts removal.
    control('stop', private)
    from .service import call
    try:
        call(private, 'status')
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    else:
        raise ValueError('foreground service is still running; stop it before uninstall')
    runtime, cli, agent = (Path(manifest[key]) for key in ('runtime', 'cli', 'agent'))
    # Only paths recorded by our own installer are removed. Data is never removed.
    if not runtime.is_absolute() or runtime.is_symlink() or runtime == Path('/'):
        raise ValueError('invalid runtime path')
    for path in (private, Path(manifest['data'])):
        if path.is_relative_to(runtime) or runtime.is_relative_to(path):
            raise ValueError('runtime overlaps protected configuration/data')
    if manifest['registered']:
        agent.unlink(missing_ok=True)
    cli.unlink(missing_ok=True)
    shutil.rmtree(runtime)
    if remove_configuration:
        shutil.rmtree(private)
    return dict(uninstalled=True, data_preserved=manifest['data'], configuration_removed=remove_configuration)
