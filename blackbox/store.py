"""Local SQLite persistence. No retention or Incident policy lives here."""
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
 id TEXT PRIMARY KEY, layer TEXT NOT NULL, trigger TEXT NOT NULL,
 targets TEXT NOT NULL, options TEXT NOT NULL, state TEXT NOT NULL,
 created REAL NOT NULL, started REAL, ended REAL, pid INTEGER,
 current TEXT, completed INTEGER NOT NULL DEFAULT 0,
 total INTEGER NOT NULL DEFAULT 0, cancel INTEGER NOT NULL DEFAULT 0,
 error TEXT, metrics TEXT);
CREATE INDEX IF NOT EXISTS scan_state_created ON scans(state,created);
CREATE INDEX IF NOT EXISTS scan_layer_started ON scans(layer,started);
CREATE TABLE IF NOT EXISTS results (
 scan_id TEXT NOT NULL REFERENCES scans(id), path TEXT NOT NULL,
 kind TEXT NOT NULL, bytes INTEGER, logical_bytes INTEGER,
 status TEXT NOT NULL, error TEXT, count INTEGER, mean_bytes REAL,
 PRIMARY KEY(scan_id,path,kind));
CREATE TABLE IF NOT EXISTS l1 (
 time REAL PRIMARY KEY, path TEXT NOT NULL, total INTEGER, used INTEGER,
 free INTEGER, available INTEGER, boot_id TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS waiting (
 target TEXT NOT NULL, options TEXT NOT NULL, requested REAL NOT NULL,
 PRIMARY KEY(target,options));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

class Store:
    def __init__(self, directory, *, create=False):
        self.directory = Path(directory).resolve(strict=True)
        if not self.directory.is_dir():
            raise ValueError('data directory must already exist')
        self.device = self.directory.stat().st_dev
        self.inode = self.directory.stat().st_ino
        database = self.directory / 'blackbox.sqlite3'
        if database.is_symlink():
            raise ValueError('database must not be a symbolic link')
        if create and database.exists():
            raise ValueError('data directory is already initialized')
        # Reopening never creates a fallback database after an unmount.
        self.db = sqlite3.connect(database.as_uri() + ('?mode=rwc' if create else '?mode=rw'),
                                  uri=True, timeout=5)
        os.chmod(self.directory / 'blackbox.sqlite3', 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(SCHEMA)
        self.db.commit()

    def check(self):
        st = self.directory.stat()
        if (st.st_dev, st.st_ino) != (self.device, self.inode):
            raise OSError('data destination changed; recording stopped')

    def rows(self, sql, args=()):
        self.check()
        return [dict(r) for r in self.db.execute(sql, args)]

    def execute(self, sql, args=()):
        self.check()
        with self.db:
            return self.db.execute(sql, args)

    def create_scan(self, layer, targets, options, trigger='manual', state='pending'):
        ident = uuid.uuid4().hex
        self.execute('INSERT INTO scans(id,layer,trigger,targets,options,state,created,total) VALUES(?,?,?,?,?,?,?,?)',
                     (ident, layer, trigger, json.dumps(targets), json.dumps(options), state, time.time(), len(targets)))
        return ident

    def close(self):
        self.db.close()
