#!/usr/bin/env python3
"""Create a NEW isolated Viewer fixture. No installed config, DB or service access."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackbox.destination import initialize, verify
from blackbox.store import SCHEMA


def prepare(root, mount, *, now=None):
    root = Path(root).expanduser().absolute()
    # Never accept an existing directory, even if empty, or redirected parents.
    if root.exists() or root.is_symlink() or root.parent.resolve(strict=True) != root.parent:
        raise ValueError('fixture root must be new, with an existing canonical parent')
    root.mkdir(mode=0o700)
    data = root/'data'
    data.mkdir(mode=0o700)
    db = data/'blackbox.sqlite3'
    epoch = int(time.time() if now is None else now)
    gib = 1024**3
    values = [44, 43, 42.5, 42]
    with sqlite3.connect(db) as connection:
        connection.executescript(SCHEMA)
        connection.executemany('INSERT INTO l1 VALUES (?,?,?,?,?,?,?,?)', [
            (epoch-180+i*60, '/synthetic/issue38/widget', 256*gib,
             int((256-free)*gib), int(free*gib), int(free*gib), 'synthetic', None)
            for i, free in enumerate(values)])
    os.chmod(db, 0o600)
    identity = initialize(data, mount)
    verify(identity)
    config = root/'viewer-source.json'
    config.write_text(json.dumps({'storage': identity}, indent=2)+'\n')
    config.chmod(0o600)
    with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as connection:
        if (connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok'
                or connection.execute('SELECT count(*) FROM scans').fetchone()[0] != 0
                or connection.execute('SELECT free FROM l1 ORDER BY time').fetchall()
                != [(int(v*gib),) for v in values]):
            raise ValueError('synthetic fixture validation failed')
    result = dict(configuration=str(config), database_folder=str(data),
                  free_gib=values, scans=0, latest_observation=epoch)
    (root/'fixture-record.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path, help='new dedicated directory; never reused')
    parser.add_argument('--mount', required=True, type=Path, help='explicit expected storage mount')
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.mount), indent=2))
