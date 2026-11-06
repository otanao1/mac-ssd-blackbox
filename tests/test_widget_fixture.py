"""Fixture generation uses a mocked volume and new synthetic directories only."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from tools.prepare_widget_fixture import prepare


class WidgetFixtureTest(unittest.TestCase):
    def test_new_fixture_identity_and_read_only_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            volume = dict(kind='disk', mount=str(parent), filesystem='apfs', uuid='SYNTHETIC')
            with patch('blackbox.destination.mounted_volume', return_value=volume):
                result = prepare(parent/'new-fixture', parent, now=1800000000)
            identity = json.loads(Path(result['configuration']).read_text())['storage']
            db = Path(result['database_folder'])/'blackbox.sqlite3'
            self.assertEqual(identity['database_inode'], db.stat().st_ino)
            self.assertEqual(identity['directory_inode'], db.parent.stat().st_ino)
            self.assertEqual(identity['volume'], volume)
            with sqlite3.connect(db.as_uri()+'?mode=ro', uri=True) as c:
                self.assertEqual(c.execute('SELECT free FROM l1 ORDER BY time').fetchall(),
                                 [(int(v*1024**3),) for v in [44, 43, 42.5, 42]])
                self.assertEqual(c.execute('SELECT count(*) FROM scans').fetchone()[0], 0)
                with self.assertRaises(sqlite3.OperationalError):
                    c.execute('DELETE FROM l1')

    def test_existing_or_redirected_root_is_never_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp).resolve()
            sentinel = parent/'sentinel'; sentinel.mkdir()
            (sentinel/'keep').write_text('unchanged')
            link = parent/'link'; link.symlink_to(sentinel)
            for root in (sentinel, link, link/'new'):
                with self.subTest(root=root), self.assertRaises(ValueError):
                    prepare(root, parent)
            self.assertEqual((sentinel/'keep').read_text(), 'unchanged')
