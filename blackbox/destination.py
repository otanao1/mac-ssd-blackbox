"""Fail-closed destination identification. No mount or unmount operations."""
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile


def mounted_volume(directory):
    try:
        return _mounted_volume(directory)
    except (subprocess.SubprocessError, plistlib.InvalidFileException) as exc:
        raise OSError('cannot query storage volume: '+str(exc)) from exc


def _mounted_volume(directory):
    path = Path(directory).resolve(strict=True)
    # df resolves APFS firmlinks (e.g. /Users belongs to the Data volume).
    lines = subprocess.run(['/bin/df', '-P', str(path)], capture_output=True,
                           text=True, check=True, timeout=10).stdout.splitlines()
    source = lines[1].split()[0] if len(lines) > 1 else ''
    if source.startswith('/dev/'):
        raw = subprocess.run(['/usr/sbin/diskutil', 'info', '-plist', source],
                             capture_output=True, check=True, timeout=10).stdout
        info = plistlib.loads(raw)
        if not all(info.get(key) for key in ('VolumeUUID', 'MountPoint', 'FilesystemType')):
            raise OSError('storage volume has no stable UUID/mount/filesystem')
        return dict(kind='disk', mount=info['MountPoint'], filesystem=info['FilesystemType'],
                    uuid=info['VolumeUUID'])
    output = subprocess.run(['/sbin/mount'], capture_output=True, text=True, check=True, timeout=10).stdout
    candidates = []
    for line in output.splitlines():
        match = re.fullmatch(r'(.+) on (.+) \(([^, )]+).*\)', line)
        if match:
            source, mount, filesystem = match.groups()
            if path.is_relative_to(mount):
                candidates.append((len(mount), source, mount, filesystem))
    if not candidates:
        raise OSError('cannot identify storage mount')
    _, source, mount, filesystem = max(candidates)
    if filesystem not in ('smbfs', 'nfs', 'afpfs', 'webdav'):
        raise OSError('unsupported storage filesystem identity')
    return dict(kind='network', mount=mount, filesystem=filesystem, source=source)


def validate_identity(identity):
    if not isinstance(identity, dict) or set(identity) != {'path', 'volume', 'directory_inode', 'database_inode'}:
        raise ValueError('storage must contain path, volume, directory_inode, database_inode; run setup')
    if not isinstance(identity['path'], str) or not Path(identity['path']).is_absolute():
        raise ValueError('storage path must be absolute')
    for key in ('directory_inode', 'database_inode'):
        if type(identity[key]) is not int or identity[key] <= 0:
            raise ValueError('invalid storage inode')
    volume = identity['volume']
    if not isinstance(volume, dict) or volume.get('kind') not in ('disk', 'network'):
        raise ValueError('invalid storage volume')
    keys = {'mount', 'filesystem', 'kind', 'uuid' if volume['kind'] == 'disk' else 'source'}
    if set(volume) != keys or any(not isinstance(v, str) or not v for v in volume.values()):
        raise ValueError('invalid storage volume identity')
    if not Path(volume['mount']).is_absolute():
        raise ValueError('storage mount must be absolute')


def verify(identity, *, volume=True):
    validate_identity(identity)
    directory = Path(identity['path'])
    # Refuse path redirection, including changed symlink parents.
    if str(directory.resolve(strict=True)) != str(directory):
        raise OSError('storage path changed')
    if volume and mounted_volume(directory) != identity['volume']:
        raise OSError('intended storage volume is unavailable')
    database = directory / 'blackbox.sqlite3'
    if (not directory.is_dir() or directory.stat().st_ino != identity['directory_inode']
            or database.is_symlink() or database.stat().st_ino != identity['database_inode']):
        raise OSError('storage directory or database was replaced')
    return directory


def initialize(directory, expected_mount):
    """Only explicit setup may create a DB; the destination directory must exist."""
    from .store import Store
    directory = Path(directory).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError('storage directory must already exist')
    volume = mounted_volume(directory)
    if volume['mount'] != str(Path(expected_mount).expanduser().resolve(strict=True)):
        raise OSError('storage is not on the explicitly selected mount')
    before = directory.stat()
    # Access checks alone do not establish writeability (ACLs, read-only mounts, quotas).
    with tempfile.TemporaryFile(dir=directory) as probe:
        probe.write(b'blackbox storage probe\n')
        probe.flush()
        os.fsync(probe.fileno())
    after = directory.stat()
    if mounted_volume(directory) != volume or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise OSError('storage changed during setup')
    database = directory / 'blackbox.sqlite3'
    with_store = Store(directory, create=not database.exists())
    with_store.close()
    identity = dict(path=str(directory), volume=volume, directory_inode=before.st_ino,
                    database_inode=database.stat().st_ino)
    verify(identity)
    return identity
