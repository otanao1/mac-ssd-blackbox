"""Best-effort, low-frequency Unified Logging diagnostics; no file fallback."""
import errno
import json
import os
import re
import sqlite3
import subprocess
import time

SUBSYSTEM = 'io.github.otanao1.mac-ssd-blackbox'
EVENTS = {'recording_suspended', 'recording_resumed', 'worker_interrupt_requested',
          'worker_interrupt_failed', 'worker_finished', 'worker_finalize_failed',
          'worker_unexpected_exit', 'worker_start_failed', 'service_initialization_failed', 'service_initialized'}
TOKENS = {'reason': {'recording_error', 'signal', 'user_cancel', 'worker_error', 'incomplete'},
          'state': {'cancelled', 'failed', 'partial'},
          'stage': {'tick', 'reconnect', 'initialize', 'finalize'}}
_available = None


def available():
    """None means not attempted; False means the last emission failed."""
    return _available


def emit(event, *, error=None, **fields):
    """Only allowlisted metadata crosses the public log/argv boundary.

    Never pass exception text, filenames, traceback, or caller-supplied messages.
    A subprocess isolates the monitor from native logging failures. Its PID is
    different, so include the originating PID and event timestamp explicitly.
    """
    global _available
    try:
        if event not in EVENTS:
            return False
        payload = dict(event=event, origin_pid=os.getpid(), occurred_at=time.time())
        for key in ('child_pid', 'signal', 'returncode'):
            value = fields.get(key)
            if type(value) is int:
                payload[key] = value
        ident = fields.get('scan_id')
        if isinstance(ident, str) and re.fullmatch('[0-9a-f]{32}', ident):
            payload['scan_id'] = ident
        for key, allowed in TOKENS.items():
            if fields.get(key) in allowed:
                payload[key] = fields[key]
        if error is not None:
            # Class names and messages may themselves contain private data.
            kind = next((k for k in (sqlite3.OperationalError, sqlite3.IntegrityError,
                         sqlite3.DatabaseError, sqlite3.Error, OSError, ValueError)
                         if isinstance(error, k)), Exception)
            payload['exception_type'] = kind.__name__
            code = getattr(error, 'sqlite_errorcode', None)
            if isinstance(error, sqlite3.Error) and type(code) is int:
                payload['sqlite_errorcode'] = code
                name = getattr(error, 'sqlite_errorname', None)
                if isinstance(name, str) and name.startswith('SQLITE_') and getattr(sqlite3, name, None) == code:
                    payload['sqlite_errorname'] = name
            code = getattr(error, 'errno', None)
            if isinstance(error, OSError) and type(code) is int:
                payload['errno'] = code
                if code in errno.errorcode:
                    payload['errno_name'] = errno.errorcode[code]
        message = json.dumps(payload, separators=(',', ':'), sort_keys=True)
        level = 'default' if event in {'recording_resumed', 'service_initialized'} else 'error'
        result = subprocess.run(['/usr/bin/log', 'emit', '--type', level,
                                 '--subsystem', SUBSYSTEM, '--category', 'runtime',
                                 '--public', message], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=1, check=False)
        _available = result.returncode == 0
        return _available
    except Exception:
        _available = False
        return False
