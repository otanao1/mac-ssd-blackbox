"""Issue38 test ONLY. Discard capacities; never spool stderr or retain child names."""
import os
import subprocess
import threading
import time
from .du import DuScanner

ALLOWED = {'/Library/Bluetooth', os.path.expanduser('~/Library/Mail')}


def access_only(self, operands, device):
    for path in operands:
        if path not in ALLOWED:
            raise ValueError('privacy probe accepts only the two approved roots')
        self.pulse(path, True)
        flags = set()
        child = subprocess.Popen(['/usr/bin/du', '-P', '-x', '-l', '-s', '-B', '512', path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                 env={**os.environ, 'LC_ALL': 'C'})
        # Fixed-size byte chunks, no decoded paths or diagnostic files.
        def drain():
            tail = b''
            while True:
                chunk = child.stderr.read(4096)
                if not chunk:
                    break
                sample = tail + chunk
                if b'Operation not permitted' in sample: flags.add('EPERM')
                if b'Permission denied' in sample: flags.add('EACCES')
                tail = sample[-32:]
        reader = threading.Thread(target=drain)
        reader.start()
        started = time.monotonic()
        try:
            while child.poll() is None:
                self.pulse(path)
                if time.monotonic()-started > 90:
                    raise TimeoutError('limited access probe timed out')
                time.sleep(.1)
        finally:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()
            reader.join(timeout=5)
            child.stderr.close()
        if child.returncode == 0:
            self.record(path, 'directory', size=None)
        else:
            reason = '+'.join(sorted(flags)) or 'DU_FAILURE'
            self.record(path, 'unknown', status='error', error=reason)
            self.bad.add(path)


DuScanner._du = access_only
