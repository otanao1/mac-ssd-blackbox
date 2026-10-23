"""L2/L3 allocated capacity: disjoint du operands, selected rows only.

No filesystem-node TEMP tables, file-content reads, or name-based exclusions.
L9 continues to use the detailed Python scanner.
"""
import errno
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

from .scan import Cancelled, internal_device, normalize


def mount_points():
    """Read the mount table before planning; fail closed on unknown output."""
    result = subprocess.run(['/sbin/mount'], check=True, capture_output=True, timeout=10)
    points = set()
    for line in os.fsdecode(result.stdout).splitlines():
        location, sep, _ = line.rpartition(' (')
        _, on, path = location.partition(' on ')
        if not sep or not on or not path.startswith('/'):
            raise ValueError('cannot parse mount table')
        # mount renders whitespace/backslashes as octal escapes on macOS.
        path = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), path)
        points.add(path.rstrip('/') or '/')
    return points


def beneath(path, root):
    return path.startswith(root.rstrip('/') + '/')


class DuScanner:
    def __init__(self, db=None, progress=lambda p: None, cancelled=lambda: False,
                 device_check=internal_device, target_done=lambda n: None,
                 mounts=mount_points):
        self.progress, self.cancelled = progress, cancelled
        self.device_check, self.target_done, self.mounts = device_check, target_done, mounts
        self.last = 0

    def pulse(self, path, force=False):
        if force or time.monotonic()-self.last >= .2:
            if self.cancelled():
                raise Cancelled()
            self.progress(path)
            self.last = time.monotonic()

    def record(self, path, kind, size=None, status='ok', error=None):
        self.rows[path] = dict(path=path, kind=kind, bytes=size, logical=None,
                               status=status, error=error)

    def failure(self, path, exc):
        self.record(path, 'unknown', status='missing' if isinstance(exc, FileNotFoundError)
                    else 'error', error=str(exc))
        self.bad.add(path)

    @staticmethod
    def metadata(path):
        st = os.stat(path, follow_symlinks=False)
        kind = ('directory' if stat.S_ISDIR(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode)
                else 'symlink' if stat.S_ISLNK(st.st_mode) else 'special')
        return st, kind

    def entries(self, path):
        # Validate directory identity around enumeration; never follow a directory symlink.
        before = os.stat(path, follow_symlinks=False)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            after = os.fstat(fd)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                raise OSError('directory changed during scan')
            with os.scandir(fd) as entries:
                return [os.path.join(path, entry.name) for entry in entries]
        finally:
            os.close(fd)

    def du(self, operands, device):
        # Absolute operands cannot be parsed as options. Chunk below ARG_MAX,
        # including a conservative allowance for the inherited environment.
        limit = max(4096, min(65536, os.sysconf('SC_ARG_MAX')//4))
        batch, size = [], 0
        for path in operands:
            amount = len(os.fsencode(path))+1
            if batch and size+amount > limit:
                self._du(batch, device)
                batch, size = [], 0
            batch.append(path)
            size += amount
        if batch:
            self._du(batch, device)

    def _du(self, operands, device):
        self.pulse(operands[0], True)
        current_mounts = self.mounts()
        for path in operands:
            st, _ = self.metadata(path)
            if st.st_dev != device or (path not in self.roots and (path in current_mounts or os.path.ismount(path))) or any(beneath(mount, path) for mount in current_mounts):
                raise OSError("mount boundary changed before du")
        # Temporary command pipes are spooled, not persistent diagnostic log files.
        # This bounds RAM and prevents stdout/stderr pipe deadlocks.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            child = subprocess.Popen(['/usr/bin/du', '-P', '-x', '-l', '-s', '-B', '512', *operands],
                                     stdout=output, stderr=errors, env={**os.environ, 'LC_ALL': 'C'})
            try:
                while child.poll() is None:
                    self.pulse(operands[0])
                    time.sleep(.05)
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
            if child.returncode not in (0, 1):
                raise OSError('du failed with exit status ' + str(child.returncode))
            output.seek(0)
            raw = output.read()
            # -s produces only known operands. Match full byte paths (including
            # embedded newlines), never interpret arbitrary lines as path records.
            expected = sorted((os.fsencode(p) for p in operands), key=len, reverse=True)
            received = set()
            while raw:
                number, sep, rest = raw.partition(b'\t')
                if not sep or not number.strip().isdigit():
                    raise OSError('unrecognized du output')
                path = next((p for p in expected if rest.startswith(p+b'\n')), None)
                if path is None or path in received:
                    raise OSError('unexpected du result path')
                received.add(path)
                decoded = os.fsdecode(path)
                try:
                    _, kind = self.metadata(decoded)
                    self.record(decoded, kind, int(number)*512)
                except OSError as exc:
                    self.failure(decoded, exc)
                raw = rest[len(path)+1:]
            errors.seek(0)
            error_count = 0
            for raw_line in errors:
                self.pulse(operands[0])
                line = os.fsdecode(raw_line.rstrip(b'\n'))
                detail, sep, reason = line.rpartition(': ')
                if not detail.startswith('du: ') or not sep:
                    raise OSError('unrecognized du error output')
                path = detail[4:]
                if not any(path == p or beneath(path, p) for p in operands):
                    raise OSError('unexpected du error path')
                state = 'missing' if reason == 'No such file or directory' else 'error'
                self.record(path, 'unknown', status=state, error=reason)
                self.bad.add(path)
                error_count += 1
            if child.returncode and not error_count:
                raise OSError('du failed without a usable error record')
            for path in operands:
                if os.fsencode(path) not in received and path not in self.rows:
                    self.record(path, 'unknown', status='error', error='du did not return a total')
                    self.bad.add(path)

    def aggregate(self, path, device, explicit=False):
        self.pulse(path)
        try:
            st, kind = self.metadata(path)
            if st.st_dev != device or (not explicit and (path in self.boundaries or os.path.ismount(path))):
                self.record(path, 'boundary', status='excluded', error='mount boundary')
                return
            nested = [p for p in self.targets | self.boundaries if beneath(p, path)]
            if kind != 'directory':
                # Allocation of non-directories needs only metadata; symlinks are never followed.
                self.record(path, kind, st.st_blocks*512)
                return
            if not nested and not self.specs.get(path, False):
                self.du([path], device)
                return
            children = self.entries(path)
            self.children[path] = children
            remaining = []
            excluded = set()
            for child in children:
                self.pulse(child)
                try:
                    cs, ck = self.metadata(child)
                    if cs.st_dev != device or child in self.boundaries or os.path.ismount(child):
                        self.record(child, 'boundary', status='excluded', error='mount boundary')
                        excluded.add(child)
                    elif any(child == p or beneath(p, child) for p in nested):
                        # Resolve descendants first; this child is never passed to the parent's du.
                        self.aggregate(child, device)
                    elif ck == 'directory':
                        remaining.append(child)
                    else:
                        self.record(child, ck, cs.st_blocks*512)
                except OSError as exc:
                    self.failure(child, exc)
            self.du(remaining, device)
            total = st.st_blocks*512 + sum(self.rows[p]['bytes'] or 0 for p in children
                                           if p in self.rows and p not in excluded)
            self.record(path, 'directory', total)
        except OSError as exc:
            self.failure(path, exc)

    def run(self, targets, layer, options, emit):
        if layer not in ('L2', 'L3'):
            raise ValueError('du aggregation supports L2/L3')
        self.specs = dict(sorted(set(normalize(s) for s in targets)))
        self.targets = set(self.specs)
        self.rows, self.children, self.bad = {}, {}, set()
        self.boundaries = self.mounts()
        roots = []
        for path in sorted(self.targets):
            ancestors = [r for r in roots if beneath(path, r)]
            if not ancestors or any(beneath(path, mount) or path == mount for mount in self.boundaries
                                    if any(beneath(mount, r) for r in ancestors)):
                roots.append(path)
        self.roots = set(roots)
        for path in roots:
            self.pulse(path, True)
            try:
                # lstat preserves errno; lexists would turn EPERM into a false missing.
                self.metadata(path)
                device = self.device_check(path)
            except FileNotFoundError as exc:
                self.failure(path, exc)
                continue
            except (ValueError, subprocess.SubprocessError) as exc:
                self.record(path, 'boundary', status='excluded', error=str(exc))
                continue
            except OSError as exc:
                self.failure(path, exc)
                continue
            self.aggregate(path, device, explicit=True)
        keep = set(self.targets)
        for path, children in self.specs.items():
            if children:
                keep.update(self.children.get(path, []))
        keep.update(p for p, r in self.rows.items() if r['status'] in ('error', 'missing', 'excluded'))
        for index, path in enumerate(self.specs):
            self.target_done(index)
            if path not in self.rows:
                self.record(path, 'unknown', status='excluded', error='unavailable beneath excluded or failed ancestor')
        for path in keep:
            if path not in self.rows:
                self.record(path, 'unknown', status='excluded', error='unavailable beneath excluded or failed ancestor')
        for path in sorted(keep):
            self.pulse(path)
            row = self.rows[path]
            if row['kind'] == 'directory' and any(beneath(p, path) for p in self.bad):
                row['status'] = 'partial'
            emit(row)
        self.target_done(len(self.specs))
