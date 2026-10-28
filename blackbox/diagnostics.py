"""Best-effort, low-frequency Unified Logging diagnostics; no file fallback."""
import errno
import json
import math
import os
import plistlib
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
OPERATIONS = frozenset({
    'store_initialize', 'storage_identity', 'storage_path', 'storage_sidecar',
    'volume_df', 'volume_diskutil', 'volume_plist', 'volume_mount',
    'volume_mount_parse', 'storage_volume_identity', 'storage_inode_identity',
    'sqlite_open', 'database_permissions', 'database_identity', 'sqlite_setup',
    'scan_lookup',
})


def mark_operation(error, operation):
    """Best-effort metadata only: preserve the exception and the innermost tag."""
    try:
        if type(operation) is str and operation in OPERATIONS:
            previous = getattr(error, '_diagnostic_operation', None)
            if type(previous) is not str or previous not in OPERATIONS:
                error._diagnostic_operation = operation
    except Exception:
        pass


def _exception_type(error, initialization=False):
    kinds = ((sqlite3.OperationalError, 'OperationalError'),
             (sqlite3.IntegrityError, 'IntegrityError'),
             (sqlite3.DatabaseError, 'DatabaseError'), (sqlite3.Error, 'Error'),
             (OSError, 'OSError'), (ValueError, 'ValueError'))
    if initialization:
        kinds = ((subprocess.TimeoutExpired, 'TimeoutExpired'),
                 (subprocess.CalledProcessError, 'CalledProcessError'),
                 (plistlib.InvalidFileException, 'InvalidFileException')) + kinds
    return next((label for kind, label in kinds if isinstance(error, kind)), 'Exception')


def _initialization_details(payload, error, fallback):
    # Explicit causes only, at most four objects, including the outer exception.
    chain, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen and len(chain) < 4:
        chain.append(error)
        seen.add(id(error))
        error = error.__cause__
    operation = fallback if type(fallback) is str and fallback in OPERATIONS else None
    for error in chain:
        candidate = getattr(error, '_diagnostic_operation', None)
        if type(candidate) is str and candidate in OPERATIONS:
            operation = candidate
    if operation is not None:
        payload['operation'] = operation
    if len(chain) > 1:
        payload['cause_type'] = _exception_type(chain[-1], initialization=True)
        for cause in reversed(chain[1:]):
            code = getattr(cause, 'errno', None) if isinstance(cause, OSError) else None
            if type(code) is int:
                payload['cause_errno'] = code
                if code in errno.errorcode:
                    payload['cause_errno_name'] = errno.errorcode[code]
                break
    for cause in reversed(chain):
        if isinstance(cause, subprocess.TimeoutExpired):
            seconds = cause.timeout
            if type(seconds) in (int, float) and 0 < seconds <= 3600 and math.isfinite(seconds):
                payload['timeout_seconds'] = seconds
            break
        if isinstance(cause, subprocess.CalledProcessError):
            if type(cause.returncode) is int:
                payload['returncode'] = cause.returncode
            break


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
            payload['exception_type'] = _exception_type(error, event == 'worker_start_failed')
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
        if event == 'worker_start_failed':
            _initialization_details(payload, error, fields.get('operation'))
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
