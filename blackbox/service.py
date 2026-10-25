"""A foreground Monitor, with a private Unix control socket for the installed CLI."""
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import time

from .configuration import load
from .diagnostics import emit as diagnostic, available as diagnostic_available
from .runtime import Monitor, lock, request


def address(private):
    path = str(Path(private).absolute() / 'control.sock')
    if len(os.fsencode(path)) >= 104:
        raise ValueError('configuration path too long for a Unix control socket')
    return path


def call(private, command, **fields):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(15)
        client.connect(address(private))
        client.sendall(json.dumps(dict(command=command, **fields)).encode() + b'\n')
        with client.makefile('rb') as stream:
            raw = stream.readline(1024*1024)
        response = json.loads(raw)
        if 'error' in response:
            raise ValueError(response['error'])
        return response['result']


class Service:
    def __init__(self, common, private):
        self.common, self.private = Path(common), Path(private)
        self.config = load(common, private)
        if not self.config.get('storage'):
            raise ValueError('storage is required; run setup')
        self.monitor = None
        self.error = None
        self.stopped = False
        self.started = time.time()
        self.retry = 0

    def reload(self):
        candidate = load(self.common, self.private)
        if candidate.get('storage') != self.config['storage']:
            raise ValueError('storage changes require stop, setup verification, and restart')
        if self.monitor:
            # Commit deadline changes before swapping in-memory configuration.
            store = self.monitor.store
            store.check()
            with store.db:
                for layer, interval in candidate['intervals'].items():
                    if layer == 'L1' and interval != self.config['intervals'][layer]:
                        store.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                                         ('due_'+layer, str(time.time()+interval)))
            self.monitor.targets = candidate['targets']
            self.monitor.intervals = candidate['intervals']
            self.monitor.l1_path = candidate['l1_path']
            self.monitor.retention = candidate['retention']
            self.monitor.incident_thresholds = candidate['incident_thresholds']
        self.config = candidate
        return dict(reloaded=True, sources=candidate['sources'])

    def tick(self):
        if time.monotonic() < self.retry:
            return
        initializing = self.monitor is None
        try:
            if self.monitor is None:
                cfg = self.config
                self.monitor = Monitor(cfg['storage']['path'], cfg['targets'], cfg['intervals'], cfg['l1_path'],
                                       retention=cfg['retention'], incident_thresholds=cfg['incident_thresholds'],
                                       identity=cfg['storage'])
            self.monitor.tick()
            if initializing and self.error is not None:
                diagnostic('service_initialized')
            if self.error is not None:
                print(json.dumps(dict(event='service_recording_resumed')), flush=True)
            self.error = None
        except (OSError, sqlite3.Error, ValueError) as exc:
            if self.error is None and initializing:
                diagnostic('service_initialization_failed', error=exc, stage='initialize')
            if self.error is None:
                print(json.dumps(dict(event='service_recording_stopped', error=str(exc))), flush=True)
            self.error = str(exc)
            self.retry = time.monotonic()+5

    def handle(self, message):
        command = message['command']
        if command == 'stop':
            self.stopped = True
            return dict(stopping=True)
        if command == 'reload':
            return self.reload()
        if command == 'config':
            return dict(loaded=True, sources=self.config['sources'], storage=self.config['storage']['path'],
                        storage_identity=self.config['storage'])
        if command == 'status':
            output = dict(running=True, pid=os.getpid(), started=self.started,
                          recording_error=self.error, sources=self.config['sources'], scans=[], waiting=[],
                          diagnostic_logging=diagnostic_available())
            if self.monitor:
                output['recording_error'] = self.error or self.monitor.recording_error
                try:
                    output['scans'] = self.monitor.store.rows("SELECT * FROM scans WHERE state IN ('running','pending') ORDER BY created")
                    for scan in output['scans']:
                        scan['elapsed_seconds'] = time.time()-scan['started'] if scan['started'] else None
                    output['waiting'] = self.monitor.store.rows('SELECT * FROM waiting ORDER BY requested')
                except (OSError, sqlite3.Error) as exc:
                    output['recording_error'] = str(exc)
            return output
        if command == 'periodic-l2':
            if self.monitor is None or self.error:
                raise ValueError('recording unavailable')
            return self.monitor.request_periodic_l2()
        if command == 'scan':
            if self.monitor is None or self.error:
                raise ValueError('recording unavailable')
            return request(self.monitor.store, message['layer'], message['paths'], message.get('options'),
                           self.config['incident_thresholds']['l9_cooldown'])
        raise ValueError('unknown service command')

    def close(self):
        if self.monitor:
            self.monitor.close()


def run(common, private):
    private = Path(private).absolute()
    # Setup owns creation of this private directory; a daemon never creates data paths.
    info = private.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('configuration directory must be owned by this user and mode 0700')
    owner = lock(private, 'service.lock')
    server = None
    bound = False
    service = None
    previous = {}
    try:
        service = Service(common, private)
        server = socket.socket(socket.AF_UNIX)
        path = Path(address(private))
        path.unlink(missing_ok=True)  # protected by the service flock
        server.bind(str(path))
        bound = True
        os.chmod(path, 0o600)
        server.listen(4)
        server.settimeout(.2)
        def stop(signum, frame):
            service.stopped = True
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, stop)
        while not service.stopped:
            service.tick()
            try:
                client, _ = server.accept()
            except socket.timeout:
                continue
            with client:
                client.settimeout(1)
                try:
                    with client.makefile('rb') as stream:
                        raw = stream.readline(65537)
                    if len(raw) > 65536 or not raw.endswith(b'\n'):
                        raise ValueError('invalid control request')
                    response = dict(result=service.handle(json.loads(raw)))
                except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
                    response = dict(error=str(exc))
                try:
                    client.sendall(json.dumps(response).encode()+b'\n')
                except OSError:
                    pass
    finally:
        try:
            if service:
                service.close()
        finally:
            if server:
                server.close()
            if bound:
                Path(address(private)).unlink(missing_ok=True)
            os.close(owner)
            for sig, handler in previous.items():
                signal.signal(sig, handler)
