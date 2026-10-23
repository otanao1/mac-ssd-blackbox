import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from blackbox.store import Store
from blackbox.runtime import Monitor, DEFAULT_OPTIONS
from blackbox.retention import maintain, years_before, policy
from blackbox.incidents import detect, advance, details, note, thresholds, GIB


def utc(date):
    return datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)/'data'
        self.data.mkdir()
        self.store = Store(self.data, create=True)
        self.addCleanup(self.store.close)
        self.now = utc('2026-10-23T12:00:00')
        self.targets = dict(L3=['/synthetic/cache/*'], L2=['/synthetic/*'])

    def l1(self, when, free=20*GIB, boot='synthetic-boot', error=None):
        self.store.execute('INSERT INTO l1 VALUES(?,?,?,?,?,?,?,?)',
                           (when, '/synthetic/volume', 100*GIB, 100*GIB-free, free, free, boot, error))

    def scan(self, layer='L3', when=None, values=None, state='succeeded', ident=None):
        if ident is None:
            ident = self.store.create_scan(layer, self.targets.get(layer, ['/synthetic/cache/*']), DEFAULT_OPTIONS)
        when = self.now if when is None else when
        self.store.execute('UPDATE scans SET created=?,started=?,ended=?,state=? WHERE id=?',
                           (when, when, when+1, state, ident))
        for path, value in (values or {}).items():
            self.store.execute('INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)',
                               (ident, path, 'directory', value, value, 'ok' if value is not None else 'error', None, None, None))
        return ident

    def trigger(self, targets=None):
        self.l1(self.now-60, 20*GIB)
        self.l1(self.now, 18*GIB)
        detect(self.store, self.targets if targets is None else targets)
        return details(self.store)[0]

    def test_calendar_year_boundary_and_raw_retention(self):
        self.assertEqual(years_before(utc('2024-02-29'), 3), utc('2021-02-28'))
        cutoff = years_before(self.now, 3)
        for when in (cutoff-1, cutoff, self.now):
            self.l1(when)
        old = self.scan('L2', cutoff-1)
        kept = self.scan('L2', cutoff)
        pending = self.scan('L2', cutoff-100, state='pending')
        l9 = self.scan('L9', cutoff-1, {'/synthetic': 10})
        maintain(self.store, self.now)
        self.assertEqual([r['time'] for r in self.store.rows('SELECT time FROM l1 ORDER BY time')], [cutoff, self.now])
        self.assertFalse(self.store.rows('SELECT * FROM scans WHERE id IN (?,?)', (old, l9)))
        self.assertEqual(len(self.store.rows('SELECT * FROM scans WHERE id IN (?,?)', (kept, pending))), 2)
        self.assertFalse(self.store.rows('SELECT * FROM results WHERE scan_id=?', (l9,)))
        self.assertFalse(self.store.rows('PRAGMA foreign_key_check'))

    def test_daily_aggregation_extremes_failures_and_idempotence(self):
        day = utc('2026-09-20')
        for n, value in enumerate((100, 900, 200, None, 500)):
            self.scan(when=day+n*3600, values={'/synthetic/cache': value})
        recent = self.scan(when=self.now-29*86400, values={'/synthetic/cache': 800})
        maintain(self.store, self.now)
        row = self.store.rows('SELECT * FROM l3_daily')[0]
        summary = json.loads(row['summary'])
        self.assertEqual(row['bucket'], day)
        self.assertEqual((summary['first_bytes'], summary['last_bytes']), (100, 500))
        self.assertEqual((summary['min_bytes'], summary['max_bytes'], summary['mean_bytes']), (100, 900, 425))
        self.assertEqual((summary['max_increase'], summary['max_decrease']), (800, 700))
        self.assertEqual((summary['samples'], summary['valid'], summary['failures']), (5, 4, 1))
        self.assertEqual(self.store.rows('SELECT id FROM scans'), [{'id': recent}])
        maintain(self.store, self.now)
        self.assertEqual(self.store.rows('SELECT * FROM l3_daily'), [row])
        maintain(self.store, utc('2030-10-23'))
        self.assertFalse(self.store.rows('SELECT * FROM l3_daily'))

    def test_whole_bucket_and_custom_retention(self):
        day = utc('2026-09-23')  # cutoff is noon; defer the entire UTC day
        ident = self.scan(when=day+3600, values={'/synthetic/cache': 1})
        maintain(self.store, self.now)
        self.assertTrue(self.store.rows('SELECT id FROM scans WHERE id=?', (ident,)))
        maintain(self.store, self.now, dict(l3_raw_days=1, l3_bucket_seconds=3600))
        self.assertEqual(self.store.rows('SELECT width FROM l3_daily'), [{'width': 3600}])
        for value in (0, -1, True, float('nan'), 1.5):
            with self.assertRaises(ValueError):
                policy(dict(l3_raw_days=value))
        with self.assertRaises(ValueError):
            thresholds(dict(unknown=1))

    def test_aggregation_failure_rolls_back_raw_and_summary(self):
        self.scan(when=self.now-40*86400, values={'/synthetic/cache': 10})
        self.store.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON scans BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            maintain(self.store, self.now)
        self.assertFalse(self.store.rows('SELECT * FROM l3_daily'))
        self.assertEqual(len(self.store.rows('SELECT * FROM results')), 1)

    def test_detection_rules_idempotence_and_low_entry(self):
        self.l1(self.now-86400, 20*GIB)
        detect(self.store, self.targets)
        self.l1(self.now-60, 7*GIB)
        detect(self.store, self.targets)
        self.l1(self.now, 4*GIB, 'synthetic-new-boot')
        detect(self.store, self.targets)
        incidents = details(self.store)
        self.assertEqual({i['type'] for i in incidents}, {'DROP_SPACE', 'LOW_SPACE', 'REBOOT'})
        drop = next(i for i in incidents if i['type']=='DROP_SPACE')
        self.assertEqual([r['rule'] for r in drop['detection']['rules']], ['DROP_SPACE_1m', 'DROP_SPACE_24h'])
        detect(self.store, self.targets)
        self.assertEqual(len(details(self.store)), 3)
        self.l1(self.now+60, 4*GIB, 'synthetic-new-boot')
        detect(self.store, self.targets)
        self.assertEqual(len([i for i in details(self.store) if i['type']=='LOW_SPACE']), 1)
        self.l1(self.now+120, 8*GIB, 'synthetic-new-boot')
        detect(self.store, self.targets)
        self.l1(self.now+180, 4*GIB, 'synthetic-new-boot')
        detect(self.store, self.targets)
        self.assertEqual(len([i for i in details(self.store) if i['type']=='LOW_SPACE']), 2)

    def test_minute_drop_plateau_120_minutes_after_completion_or_restart(self):
        self.l1(self.now-60, 20*GIB)
        self.l1(self.now, 14*GIB)
        detect(self.store, self.targets)
        self.assertEqual(len(details(self.store)), 1)
        self.assertEqual(len(self.store.rows('SELECT * FROM scans')), 1)
        # A plateau has no new minute drop, even after completion and restart.
        self.store.execute("UPDATE incidents SET state='unidentified',stage='done'")
        reopened = Store(self.data)
        try:
            for minute in range(1, 121):
                self.l1(self.now+60*minute, 14*GIB)
                detect(reopened, self.targets)
                if minute == 60:
                    self.assertEqual(len(details(reopened, limit=100)), 1)
            self.assertEqual(len(details(reopened, limit=100)), 1)
            self.assertEqual(len(reopened.rows('SELECT * FROM scans')), 1)
        finally:
            reopened.close()

    def test_new_minute_drop_is_detected_without_repeating_old_day_drop(self):
        self.l1(self.now-60, 20*GIB)
        for minute, free in enumerate((14, 14, 13, 13)):
            self.l1(self.now+60*minute, free*GIB)
            detect(self.store, self.targets)
        items = sorted(details(self.store), key=lambda i: i['detected'])
        self.assertEqual(len(items), 2)
        self.assertEqual([r['rule'] for r in items[0]['detection']['rules']], ['DROP_SPACE_1m'])
        self.assertEqual([r['rule'] for r in items[1]['detection']['rules']], ['DROP_SPACE_1m'])
        self.assertEqual(len(self.store.rows('SELECT * FROM scans')), 2)

    def daily_state(self, store=None):
        return json.loads((store or self.store).rows(
            "SELECT value FROM meta WHERE key='drop_space_24h:/synthetic/volume'")[0]['value'])

    def test_minute_incident_does_not_change_daily_deadline_or_baseline(self):
        self.l1(self.now, 20*GIB)
        detect(self.store, {})
        original = self.daily_state()
        self.l1(self.now+60, 14*GIB)
        detect(self.store, {})
        self.assertEqual(self.daily_state(), original)
        for minute in range(2, 12):
            self.l1(self.now+60*minute, 14*GIB-(minute-1)*(GIB//2))
            detect(self.store, {})
        self.assertEqual(len(details(self.store)), 1)  # no early daily evaluation
        self.assertEqual(self.daily_state(), original)
        self.l1(self.now+86400, 9*GIB)
        detect(self.store, {})
        items = sorted(details(self.store), key=lambda i: i['detected'])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1]['detection']['rules'][0]['rule'], 'DROP_SPACE_24h')
        self.assertEqual(items[1]['detection']['rules'][0]['baseline']['free'], 20*GIB)
        self.assertEqual(items[1]['evaluation']['free_change'], -11*GIB)
        self.assertEqual(self.daily_state()['next_due'], self.now+2*86400)

    def test_daily_plateau_recovery_and_new_drop(self):
        for day, free in enumerate((20, 14, 14, 20, 14, 14)):
            self.l1(self.now+86400*day, free*GIB)
            detect(self.store, {})
            self.assertEqual(self.daily_state()['baseline']['time'], self.now+86400*day)
        items = sorted(details(self.store), key=lambda i: i['detected'])
        self.assertEqual([i['detected'] for i in items], [self.now+86400, self.now+4*86400])
        self.assertTrue(all(i['detection']['rules'][0]['rule']=='DROP_SPACE_24h' for i in items))

    def test_daily_once_per_24h_uses_endpoints_not_maximum(self):
        self.l1(self.now, 20*GIB)
        detect(self.store, {})
        original = self.daily_state()
        for hour in range(1, 24):
            self.l1(self.now+hour*3600, 30*GIB)
            detect(self.store, {})
            self.assertEqual(self.daily_state(), original)
        self.l1(self.now+86400, 16*GIB)  # down only 4GiB from the daily endpoint
        detect(self.store, {})
        self.assertFalse(details(self.store))
        daily = self.daily_state()
        self.assertEqual(daily['baseline']['free'], 16*GIB)
        self.assertEqual(daily['next_due'], self.now+2*86400)
        detect(self.store, {})  # same observation must not advance the clock
        self.assertEqual(self.daily_state(), daily)
        self.l1(self.now+2*86400, 11*GIB)
        detect(self.store, {})
        self.assertEqual(len(details(self.store)), 1)
        self.assertEqual(details(self.store)[0]['evaluation']['free_change'], -5*GIB)

    def test_daily_restart_error_and_long_gap_keep_real_endpoints(self):
        self.l1(self.now, 20*GIB)
        detect(self.store, {})
        original = self.daily_state()
        reopened = Store(self.data)
        try:
            self.l1(self.now+86400, 0, error='synthetic missing observation')
            detect(reopened, {})
            self.assertEqual(self.daily_state(reopened), original)
            self.l1(self.now+3*86400+60, 14*GIB)
            detect(reopened, {})
            item = details(reopened)[0]
            self.assertEqual([r['rule'] for r in item['detection']['rules']], ['DROP_SPACE_24h'])
            self.assertEqual(item['evaluation']['elapsed_seconds'], 3*86400+60)
            self.assertEqual(item['detection']['rules'][0]['baseline']['time'], self.now)
            self.assertEqual(self.daily_state(reopened)['next_due'], self.now+4*86400+60)
            detect(reopened, {})
            self.assertEqual(len(details(reopened)), 1)
        finally:
            reopened.close()

    def test_daily_and_minute_same_observation_queue_one_investigation(self):
        self.l1(self.now, 20*GIB)
        detect(self.store, self.targets)
        self.l1(self.now+86400-60, 19*GIB)
        detect(self.store, self.targets)
        self.l1(self.now+86400, 14*GIB)
        detect(self.store, self.targets)
        items = details(self.store)
        self.assertEqual(len(items), 1)
        self.assertEqual([r['rule'] for r in items[0]['detection']['rules']], ['DROP_SPACE_1m', 'DROP_SPACE_24h'])
        self.assertEqual(len(self.store.rows('SELECT * FROM scans')), 1)
        self.assertEqual(self.daily_state()['baseline']['free'], 14*GIB)

    def test_daily_state_and_marker_roll_back_if_incident_write_fails(self):
        self.l1(self.now, 20*GIB)
        detect(self.store, {})
        original = self.daily_state()
        self.l1(self.now+86400, 14*GIB)
        self.store.execute("CREATE TRIGGER reject_daily BEFORE INSERT ON incidents BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            detect(self.store, {})
        self.assertEqual(self.daily_state(), original)
        self.assertEqual(float(self.store.rows("SELECT value FROM meta WHERE key='incident_l1_time'")[0]['value']), self.now)
        self.store.execute('DROP TRIGGER reject_daily')
        detect(self.store, {})
        detect(self.store, {})
        self.assertEqual(len(details(self.store)), 1)
        self.assertEqual(self.daily_state()['next_due'], self.now+2*86400)

    def test_detection_jitter_gaps_errors_and_initial_sample(self):
        self.l1(self.now-61, 20*GIB)
        detect(self.store, self.targets)
        self.assertFalse(details(self.store))
        self.l1(self.now, 18*GIB)
        detect(self.store, self.targets)
        self.assertEqual(len(details(self.store)), 1)
        self.l1(self.now+300, 16*GIB)
        detect(self.store, self.targets)
        self.assertEqual(len(details(self.store)), 1)  # not a minute drop
        self.l1(self.now+360, 0, error='synthetic unavailable')
        detect(self.store, self.targets)
        self.assertEqual(len(details(self.store)), 1)

    def test_l3_l9_completion_evidence_survives_raw_expiry(self):
        baseline = self.scan(when=self.now-3600, values={'/synthetic/cache': GIB})
        item = self.trigger()
        incident_id = item['id']
        l3 = item['investigation'][0]['scan_id']
        self.scan(ident=l3, values={'/synthetic/cache': 3*GIB})
        advance(self.store, self.targets)
        self.assertEqual(details(self.store, incident_id)[0]['stage'], 'L9')
        self.assertEqual(len(self.store.rows('SELECT * FROM waiting')), 1)
        l9 = self.scan('L9', self.now+10, {'/synthetic/cache': 3*GIB})
        self.store.execute('UPDATE scans SET options=? WHERE id=?', (json.dumps(DEFAULT_OPTIONS), l9))
        # Freeze request time for synthetic detection-time data.
        step = details(self.store, incident_id)[0]['investigation']
        step[-1]['requested'] = self.now+1
        self.store.execute('UPDATE incidents SET investigation=? WHERE id=?', (json.dumps(step), incident_id))
        events = []
        advance(self.store, self.targets, notify=events.append)
        item = details(self.store, incident_id)[0]
        self.assertEqual(item['state'], 'identified')
        self.assertIn(l9, item['scan_ids'])
        self.assertIn(baseline, item['scan_ids'])
        self.assertEqual(item['candidates'][0]['delta'], 2*GIB)
        self.assertEqual(events[0]['state'], 'identified')
        note(self.store, incident_id, 'Synthetic investigation note')
        # Independent lifetimes: expire ordinary scans after one year, Incident after three.
        maintain(self.store, utc('2028-01-01'), dict(l1_years=1, l2_years=1, l3_years=1, l9_years=1))
        self.assertFalse(self.store.rows('SELECT * FROM scans'))
        item = details(self.store, incident_id)[0]
        self.assertTrue(item['investigation'][-1]['scans'][l9]['excerpt'])
        self.assertEqual(item['notes'], 'Synthetic investigation note')
        maintain(self.store, utc('2030-01-01'))
        self.assertFalse(details(self.store))

    def test_l3_then_l2_unidentified_and_restart(self):
        item = self.trigger()
        self.scan(ident=item['investigation'][0]['scan_id'], values={'/synthetic/cache': 3*GIB})
        advance(self.store, self.targets)
        item = details(self.store, item['id'])[0]
        self.assertEqual(item['stage'], 'L2')
        self.scan('L2', ident=item['investigation'][-1]['scan_id'], state='failed')
        reopened = Store(self.data)
        try:
            events = []
            advance(reopened, self.targets, notify=events.append)
            self.assertEqual(details(reopened, item['id'])[0]['state'], 'unidentified')
            self.assertEqual(events[0]['event'], 'incident_completed')
        finally:
            reopened.close()

    def test_no_targets_finish_unidentified(self):
        item = self.trigger({})
        advance(self.store, {})
        self.assertEqual(details(self.store, item['id'])[0]['state'], 'unidentified')

    def test_active_investigation_protects_baseline_until_evaluated(self):
        baseline = self.scan(when=self.now-40*86400, values={'/synthetic/cache': 1})
        item = self.trigger()
        maintain(self.store, self.now)
        self.assertTrue(self.store.rows('SELECT id FROM scans WHERE id=?', (baseline,)))
        self.scan(ident=item['investigation'][0]['scan_id'], state='failed')
        advance(self.store, {})
        maintain(self.store, self.now)
        self.assertFalse(self.store.rows('SELECT id FROM scans WHERE id=?', (baseline,)))
        self.assertTrue(details(self.store, item['id'])[0]['investigation'])

    def test_destination_failure_recovery_notifications_and_no_fallback(self):
        events = []
        monitor = Monitor(self.data, notify=events.append)
        self.addCleanup(monitor.close)
        old = self.data.with_name('original')
        self.data.rename(old)
        self.data.mkdir()
        for _ in range(2):
            with self.assertRaises(OSError):
                monitor.tick()
        self.assertFalse(list(self.data.iterdir()))
        self.assertEqual([e['event'] for e in events], ['recording_stopped'])
        self.data.rmdir()
        old.rename(self.data)
        with patch('blackbox.runtime.observe'):
            monitor.tick()
        self.assertIsNone(monitor.recording_error)
        self.assertEqual([e['event'] for e in events], ['recording_stopped', 'recording_resumed'])

    def test_write_failure_and_same_destination_recovery(self):
        events = []
        monitor = Monitor(self.data, notify=events.append)
        self.addCleanup(monitor.close)
        with patch.object(monitor.store, 'execute', side_effect=sqlite3.OperationalError('synthetic disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                monitor.tick()
        with patch('blackbox.runtime.observe'):
            monitor.tick()
        self.assertEqual([e['event'] for e in events], ['recording_stopped', 'recording_resumed'])

    def test_detection_transaction_rolls_back_marker_and_request(self):
        self.l1(self.now-60, 20*GIB)
        self.l1(self.now, 18*GIB)
        self.store.execute("CREATE TRIGGER reject_incident BEFORE INSERT ON incidents BEGIN SELECT RAISE(ABORT,'synthetic write failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            detect(self.store, self.targets)
        self.assertFalse(self.store.rows('SELECT * FROM scans'))
        self.assertFalse(self.store.rows("SELECT * FROM meta WHERE key='incident_l1_time'"))
        self.store.execute('DROP TRIGGER reject_incident')
        detect(self.store, self.targets)
        self.assertEqual(len(details(self.store)), 1)

    def test_l2_candidates_distinct_l9_options_and_cancelled_result(self):
        self.scan('L2', self.now-86400, {'/synthetic/cache': GIB})
        self.scan('L3', self.now-3600, {'/synthetic/cache': GIB})
        self.l1(self.now-60, 7*GIB)
        detect(self.store, self.targets)
        self.l1(self.now, 4*GIB)  # DROP + LOW; both investigations share a target
        with patch('blackbox.incidents.time.time', return_value=self.now):
            detect(self.store, self.targets)
            for item in details(self.store):
                self.scan(ident=item['investigation'][0]['scan_id'], values={'/synthetic/cache': GIB})
            advance(self.store, self.targets)
            for item in details(self.store):
                self.assertEqual(item['stage'], 'L2')
                self.scan('L2', ident=item['investigation'][-1]['scan_id'], values={'/synthetic/cache': 7*GIB})
            advance(self.store, self.targets)
            # Thresholds differ between DROP and LOW, so these are distinct acquisition contents.
            self.assertEqual(len(self.store.rows('SELECT * FROM waiting')), 2)
            for waiting in self.store.rows('SELECT * FROM waiting'):
                ident = self.scan('L9', self.now+10, {'/synthetic/cache': 7*GIB}, state='cancelled')
                self.store.execute('UPDATE scans SET targets=?,options=? WHERE id=?',
                                   (json.dumps([waiting['target']]), waiting['options'], ident))
            self.store.execute('DELETE FROM waiting')
            advance(self.store, self.targets)
        self.assertTrue(all(i['state']=='unidentified' for i in details(self.store)))

    def test_l9_cooldown_reuses_evidence_and_preserves_comparison(self):
        self.scan(when=self.now-3600, values={'/synthetic/cache': GIB})
        prior = self.scan('L9', self.now-120, {'/synthetic/cache': GIB})
        recent = self.scan('L9', self.now-60, {'/synthetic/cache': 3*GIB})
        item = self.trigger()
        self.scan(ident=item['investigation'][0]['scan_id'], values={'/synthetic/cache': 3*GIB})
        with patch('blackbox.incidents.time.time', return_value=self.now):
            advance(self.store, self.targets)
            self.assertFalse(self.store.rows('SELECT * FROM waiting'))
            advance(self.store, self.targets)
        item = details(self.store, item['id'])[0]
        self.assertEqual(item['state'], 'identified')
        self.assertEqual(item['investigation'][-1]['scans'][recent]['changes'][0]['delta'], 2*GIB)
        self.assertIn(prior, item['scan_ids'])

    def test_unprocessed_observations_are_detected_in_order(self):
        self.l1(self.now-120, 20*GIB)
        detect(self.store, {})
        self.l1(self.now-60, 18*GIB)
        self.l1(self.now, 18*GIB)
        detect(self.store, {})
        self.assertEqual(details(self.store)[0]['detected'], self.now-60)
        detect(self.store, {})
        self.assertEqual(len(details(self.store)), 1)
        self.assertEqual(float(self.store.rows("SELECT value FROM meta WHERE key='incident_l1_time'")[0]['value']), self.now)

    def test_two_incidents_share_l9_and_wait_for_completion(self):
        self.scan(when=self.now-3600, values={'/synthetic/cache': GIB})
        self.trigger()
        self.l1(self.now+60, 16*GIB)
        with patch('blackbox.incidents.time.time', return_value=self.now+60):
            detect(self.store, self.targets)
            for item in details(self.store):
                self.scan(ident=item['investigation'][0]['scan_id'], values={'/synthetic/cache': 3*GIB})
            advance(self.store, self.targets)
            self.assertEqual(len(self.store.rows('SELECT * FROM waiting')), 1)
            l9 = self.scan('L9', self.now+70, {'/synthetic/cache': 3*GIB}, state='running')
            self.store.execute('DELETE FROM waiting')
            advance(self.store, self.targets)
            self.assertTrue(all(i['state']=='investigating' for i in details(self.store)))
            self.store.execute("UPDATE scans SET state='succeeded' WHERE id=?", (l9,))
            advance(self.store, self.targets)
        self.assertEqual(len(details(self.store)), 2)
        self.assertTrue(all(i['state']=='identified' and l9 in i['scan_ids'] for i in details(self.store)))

    def test_real_workers_complete_unidentified_investigation(self):
        events = []
        missing = str(self.data/'synthetic-missing')
        monitor = Monitor(self.data, dict(L3=[missing], L2=[missing]), notify=events.append)
        self.addCleanup(monitor.close)
        now = time.time()
        self.l1(now-60, 20*GIB)
        self.l1(now, 18*GIB)
        deadline = time.monotonic()+5
        with patch('blackbox.runtime.observe'):
            while time.monotonic() < deadline:
                monitor.tick()
                items = details(self.store)
                if items and items[0]['state'] != 'investigating':
                    break
                time.sleep(.02)
        self.assertEqual(items[0]['state'], 'unidentified')
        self.assertEqual([s['layer'] for s in items[0]['investigation']], ['L3', 'L2'])
        self.assertEqual([s['state'] for s in items[0]['investigation']], ['partial', 'partial'])
        self.assertEqual(events[-1]['event'], 'incident_completed')

    def test_database_replacement_is_rejected(self):
        database = self.data/'blackbox.sqlite3'
        database.rename(self.data/'original.sqlite3')
        database.write_bytes(b'')
        with self.assertRaises(OSError):
            self.store.execute("INSERT INTO meta VALUES('unsafe','write')")
        self.assertEqual(database.stat().st_size, 0)


if __name__ == '__main__':
    unittest.main()
