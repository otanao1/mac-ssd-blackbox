"""Explicit user installation and launchd operations; never invoked on import."""
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
from xml.parsers.expat import ExpatError

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
        expected = uninstall_job(private, manifest)
        stop_owned_job(target, expected, plist)
    return dict(action=action, label=LABEL)


def plist_bytes(python, runtime, private):
    return plistlib.dumps(dict(Label=LABEL,
        ProgramArguments=[str(python), '-m', 'blackbox', '--config-dir', str(private),
                          '--common-dir', str(Path(runtime)/'config'), 'run'],
        WorkingDirectory=str(runtime), RunAtLoad=True,
        KeepAlive={'SuccessfulExit': False}, ThrottleInterval=10,
        ProcessType='Background', Umask=0o077,
        StandardOutPath='/dev/null', StandardErrorPath='/dev/null'))


def uninstall_job(private, manifest):
    """Derive identity from the installed paths, never from an unchecked plist."""
    runtime = Path(manifest['runtime'])
    private = Path(private).expanduser().absolute()
    for path in (runtime, private):
        if not path.is_absolute() or path.resolve() != path or any(c in str(path) for c in '\r\n\t'):
            raise ValueError('cannot verify installation paths')
    expected = plistlib.loads(plist_bytes(runtime/'venv/bin/python', runtime, private))
    agent = Path(manifest['agent'])
    if type(manifest['registered']) is not bool:
        raise ValueError('invalid installation registration state')
    intended = (Path.home()/'Library/LaunchAgents'/(LABEL+'.plist') if manifest['registered']
                else runtime/'launchd.plist')
    if agent != intended or agent.is_symlink():
        raise ValueError('cannot verify installation plist path')
    verify_agent(agent, expected)
    return expected


def verify_agent(path, expected):
    """A replaced shared LaunchAgent must never be deleted by this install."""
    if path.is_symlink():
        raise ValueError('cannot verify symbolic-link LaunchAgent')
    if not path.exists():
        return
    try:
        actual = plistlib.loads(path.read_bytes())
    except (ValueError, plistlib.InvalidFileException, ExpatError) as exc:
        raise ValueError('cannot verify installation plist contents') from exc
    if (not isinstance(actual, dict) or any(actual.get(key) != expected[key]
            for key in ('Label', 'ProgramArguments', 'WorkingDirectory'))
            or actual.get('Program', expected['ProgramArguments'][0]) != expected['ProgramArguments'][0]):
        raise ValueError('LaunchAgent belongs to another installation; nothing removed')


def owned_job(target, expected, agent):
    """Conservative recognition of launchctl diagnostics; unknown output aborts.

    print is not a stable API. Only exact, unambiguous top-level identity fields
    are accepted, and absence requires a specific service-not-found diagnostic.
    Never infer ownership from the shared label or an on-disk plist alone.
    """
    result = launchctl('print', target, check=False)
    if result.returncode:
        missing = f'Could not find service "{LABEL}" in domain for user gui: {os.getuid()}'
        if result.returncode == 113 and result.stderr.strip() in (missing, 'Bad request.\n'+missing):
            return False
        raise ValueError('cannot determine launchd job ownership; nothing stopped or removed')
    # Match one complete service block. Nested dictionaries are not identity fields.
    lines = result.stdout.splitlines()
    if not lines or lines[0] != target+' = {' or lines[-1] != '}':
        raise ValueError('unrecognized launchd job description; nothing stopped or removed')
    fields = {}
    depth = 1
    arguments = None
    for line in lines[1:-1]:
        if arguments is not None:
            if line == '\t}':
                fields['arguments'] = arguments
                arguments = None
            elif line.startswith('\t\t') and not line.startswith('\t\t\t'):
                arguments.append(line[2:])
            else:
                raise ValueError('unrecognized launchd arguments; nothing stopped or removed')
            continue
        if depth == 1:
            match = re.fullmatch(r'\t(path|program|working directory) = (.+)', line)
            if match:
                key, value = match.groups()
                if key in fields:
                    raise ValueError('ambiguous launchd identity; nothing stopped or removed')
                fields[key] = value
            elif line == '\targuments = {':
                if 'arguments' in fields:
                    raise ValueError('ambiguous launchd arguments; nothing stopped or removed')
                arguments = []
                continue
        # Unknown nested dictionaries are skipped, not searched for identities.
        if line.strip().endswith(' = {'):
            depth += 1
        elif line.strip() == '}':
            depth -= 1
        if depth < 1:
            raise ValueError('unrecognized launchd nesting; nothing stopped or removed')
    if arguments is not None or depth != 1 or fields != {
            'path': str(agent), 'program': expected['ProgramArguments'][0],
            'working directory': expected['WorkingDirectory'], 'arguments': expected['ProgramArguments']}:
        raise ValueError('launchd job ownership does not match this installation; nothing stopped or removed')
    return True


def stop_owned_job(target, expected, agent):
    if owned_job(target, expected, agent):
        # Recheck immediately before the label-based mutation. launchctl has no
        # atomic compare-and-bootout operation; do not change this label concurrently.
        if owned_job(target, expected, agent):
            launchctl('bootout', target)


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
    runtime, cli, agent = (Path(manifest[key]) for key in ('runtime', 'cli', 'agent'))
    # Validate protected paths and both plist/job identities before any mutation.
    if not runtime.is_absolute() or runtime.is_symlink() or runtime == Path('/'):
        raise ValueError('invalid runtime path')
    for path in (private, Path(manifest['data'])):
        if path.is_relative_to(runtime) or runtime.is_relative_to(path):
            raise ValueError('runtime overlaps protected configuration/data')
    expected = uninstall_job(private, manifest)
    stop_owned_job(domain()+'/'+LABEL, expected, agent)
    from .service import call
    try:
        call(private, 'status')
    except (FileNotFoundError, ConnectionRefusedError):
        pass
    else:
        raise ValueError('foreground service is still running; stop it before uninstall')
    if manifest['registered']:
        # Another install may have replaced the shared file after bootout.
        verify_agent(agent, expected)
        agent.unlink(missing_ok=True)
    cli.unlink(missing_ok=True)
    shutil.rmtree(runtime)
    if remove_configuration:
        shutil.rmtree(private)
    return dict(uninstalled=True, data_preserved=manifest['data'], configuration_removed=remove_configuration)
