"""One metadata walk, shared by L2/L3/L9. Never read file contents."""
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import time

class Cancelled(Exception):
    pass


def normalize(spec):
    children = spec.endswith('/*')
    value = spec[:-2] if children else spec
    if not value or not (value.startswith('/') or value == '~' or value.startswith('~/')):
        raise ValueError('targets must be absolute paths or ~/ paths')
    if any(c in value for c in '*?['):
        raise ValueError('only a terminal /* is supported')
    return str(Path(value).expanduser().resolve()), children


def internal_device(path):
    """Fail closed: explicit roots must also be on an internal disk."""
    # diskutil accepts a device/mount point, not an arbitrary directory.
    df = subprocess.run(['/bin/df', '-P', path], check=True, capture_output=True,
                        text=True, timeout=10)
    lines = df.stdout.splitlines()
    device = lines[1].split()[0] if len(lines) > 1 else ''
    if not device.startswith('/dev/disk'):
        raise ValueError('target is not backed by a local disk device')
    output = subprocess.run(['/usr/sbin/diskutil', 'info', '-plist', device],
                            check=True, capture_output=True, timeout=10)
    info = plistlib.loads(output.stdout)
    if info.get('Internal') is not True:
        raise ValueError('target is not on an internal disk')
    return os.stat(path).st_dev


class Scanner:
    def __init__(self, db, progress=lambda p: None, cancelled=lambda: False,
                 device_check=internal_device, target_done=lambda n: None):
        self.db = db
        self.progress = progress
        self.cancelled = cancelled
        self.device_check = device_check
        self.target_done = target_done
        self.last = 0
        db.execute('PRAGMA temp_store=FILE')
        db.executescript('''
          CREATE TEMP TABLE nodes(path TEXT PRIMARY KEY, parent TEXT, kind TEXT,
            bytes INTEGER, logical INTEGER, status TEXT, error TEXT);
          CREATE INDEX temp.node_parent ON nodes(parent);
          CREATE TEMP TABLE seen(dev INTEGER, ino INTEGER, PRIMARY KEY(dev,ino));
        ''')

    def pulse(self, path, force=False):
        if force or time.monotonic() - self.last >= .2:
            if self.cancelled():
                raise Cancelled()
            self.progress(path)
            self.last = time.monotonic()

    def node(self, path, parent, kind, size=None, logical=None, status='ok', error=None):
        self.db.execute('INSERT OR REPLACE INTO nodes VALUES(?,?,?,?,?,?,?)',
                        (path, parent, kind, size, logical, status, error))

    def walk(self, path, parent, expected_device, dir_fd=None, name=None):
        self.pulse(path)
        try:
            st = os.stat(name if dir_fd is not None else path, dir_fd=dir_fd,
                         follow_symlinks=False)
            if st.st_dev != expected_device or (parent is not None and os.path.ismount(path)):
                self.node(path, parent, 'boundary', status='excluded', error='mount boundary')
                return 0, 0, False
            kind = 'directory' if stat.S_ISDIR(st.st_mode) else ('file' if stat.S_ISREG(st.st_mode) else 'symlink' if stat.S_ISLNK(st.st_mode) else 'special')
            size, logical = st.st_blocks * 512, st.st_size
            if kind == 'file' and st.st_nlink > 1:
                inserted = self.db.execute('INSERT OR IGNORE INTO seen VALUES(?,?)', (st.st_dev, st.st_ino)).rowcount
                if not inserted:
                    self.node(path, parent, kind, 0, 0, 'deduplicated')
                    return 0, 0, False
            partial = False
            if kind == 'directory':
                fd = os.open(name if dir_fd is not None else path,
                             os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
                try:
                    actual = os.fstat(fd)
                    if (actual.st_dev, actual.st_ino) != (st.st_dev, st.st_ino):
                        raise OSError('directory changed during scan')
                    with os.scandir(fd) as entries:
                        for entry in entries:
                            a, b, bad = self.walk(os.path.join(path, entry.name), path,
                                                  expected_device, fd, entry.name)
                            size += a
                            logical += b
                            partial |= bad
                finally:
                    os.close(fd)
            self.node(path, parent, kind, size, logical, 'partial' if partial else 'ok')
            return size, logical, partial
        except (OSError, RecursionError) as exc:
            self.node(path, parent, 'unknown', status='missing' if isinstance(exc, FileNotFoundError) else 'error', error=str(exc))
            return 0, 0, True

    def run(self, targets, layer, options, emit):
        specs = sorted(set(normalize(s) for s in targets))
        roots = []
        for path, _ in specs:
            if not any(path == r or path.startswith(r.rstrip('/') + '/') for r in roots):
                roots.append(path)
        # A nested target separated by a mount is a separate explicit root.
        # No subtree is walked twice: the parent walk stops at that boundary.
        for path, _ in specs:
            if path in roots:
                continue
            ancestor = Path(path)
            while str(ancestor) not in roots and ancestor.parent != ancestor:
                if os.path.ismount(str(ancestor)):
                    roots.append(path)
                    break
                ancestor = ancestor.parent
        for root in roots:
            self.pulse(root, True)
            if not os.path.lexists(root):
                self.node(root, None, 'unknown', status='missing', error='target does not exist')
                continue
            try:
                device = self.device_check(root)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                self.node(root, None, 'boundary', status='excluded', error=str(exc))
                continue
            self.walk(root, None, device)
        emitted = set()

        def record(row):
            self.pulse(row['path'])
            if row['path'] not in emitted:
                emit(dict(row))
                emitted.add(row['path'])

        for index, (path, children) in enumerate(specs):
            self.target_done(index)
            self.pulse(path, True)
            row = self.db.execute('SELECT * FROM nodes WHERE path=?', (path,)).fetchone()
            if row is None:
                emit(dict(path=path, kind='unknown', bytes=None, logical=None,
                          status='excluded', error='unavailable beneath excluded or failed ancestor'))
                continue
            record(row)
            if layer == 'L9' and children:
                threshold = options.get('threshold', 1024**3)
                self.detail(path, 0, options.get('max_depth', 7), threshold, record, emit)
            elif children:
                for child in self.db.execute('SELECT * FROM nodes WHERE parent=? ORDER BY path', (path,)):
                    record(child)
        # Preserve inaccessible paths even when their parent requested only a total.
        for row in self.db.execute("SELECT * FROM nodes WHERE status IN ('error','missing','excluded')"):
            record(row)
        self.target_done(len(specs))

    def detail(self, path, depth, limit, threshold, record, emit):
        if depth >= limit:
            return
        count = total = 0
        for row in self.db.execute('SELECT * FROM nodes WHERE parent=? ORDER BY path', (path,)):
            self.pulse(row['path'])
            if row['kind'] == 'file' and row['status'] in ('ok', 'deduplicated') and row['bytes'] < threshold * .05:
                count += 1
                total += row['bytes']
                continue
            record(row)
            if row['kind'] == 'directory' and (row['bytes'] or 0) >= threshold * .5:
                self.detail(row['path'], depth + 1, limit, threshold, record, emit)
        if count:
            emit(dict(path=path, kind='small_files', bytes=total, logical=None,
                      status='ok', error=None, count=count, mean_bytes=total/count))
