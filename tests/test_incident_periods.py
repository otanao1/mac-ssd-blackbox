"""Issue #39: synthetic, durable period comparisons without filesystem scans."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from blackbox.incidents import GIB, advance, detect, details
from blackbox.retention import maintain
from blackbox.runtime import DEFAULT_OPTIONS
from blackbox.store import Store

DAY = 'DROP_SPACE_24h'
SHORT = 'DROP_SPACE_1m'


class PeriodIncidentTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.store = Store(self.directory, create=True)
        self.addCleanup(self.store.close)
        self.now = 1793000000
        self.anchor = self.now - 86400
        self.targets = dict(L2=['/synthetic/*'], L3=['/synthetic/cache/*'])

    def scan(self, layer, when, values=None, ident=None, state='succeeded', options=None, targets=None, version=2):
        if ident is None:
            ident = self.store.create_scan(layer, targets or self.targets.get(layer, ['/synthetic/cache/*']),
                                           options or DEFAULT_OPTIONS)
        self.store.execute('UPDATE scans SET started=?,ended=?,state=?,scan_version=? WHERE id=?',
                           (when, when+1, state, version, ident))
        for path, value in (values or {}).items():
            kind, size, status = value if isinstance(value, tuple) else ('directory', value, 'ok')
            self.store.execute('INSERT INTO results(scan_id,path,kind,bytes,status) VALUES(?,?,?,?,?)',
                               (ident, path, kind, size, status))
        return ident

    def l1(self, when, free, boot='boot'):
        self.store.execute('INSERT INTO l1 VALUES(?,?,?,?,?,?,?,?)',
                           (when, '/synthetic/disk', 100*GIB, 100*GIB-free, free, free, boot, None))

    def trigger(self, mixed=False, short=False, targets=None):
        if not short:
            self.l1(self.anchor, 20*GIB)
            detect(self.store, self.targets)
        if mixed or short:
            self.l1(self.now-60, 20*GIB)
            detect(self.store, self.targets)
        self.l1(self.now, 14*GIB)
        detect(self.store, self.targets if targets is None else targets)
        self.ident = details(self.store)[0]['id']
        return self.item()

    def item(self):
        return details(self.store, self.ident)[0]

    def advance(self, targets=None):
        with patch('blackbox.incidents.time.time', return_value=self.now+2):
            advance(self.store, self.targets if targets is None else targets)
        return self.item()

    def finish_broad(self, values, **kwargs):
        item = self.item()
        step = item['investigation'][-1]
        self.scan(step['layer'], self.now+1, values, ident=step['scan_id'], **kwargs)
        return self.advance()

    def test_daily_starts_l2_and_finds_plateau_growth(self):
        old = self.scan('L2', self.anchor-10, {'/synthetic/cache': 7*GIB})
        latest = self.scan('L2', self.now-10, {'/synthetic/cache': 10*GIB})
        item = self.trigger()
        self.assertEqual(item['stage'], 'L2')
        item = self.finish_broad({'/synthetic/cache': 10*GIB})
        self.assertEqual([s['layer'] for s in item['investigation']], ['L2', 'L9'])
        candidate = item['candidates'][0]
        self.assertEqual((candidate['delta'], candidate['baseline_id'], candidate['rule']), (3*GIB, old, DAY))
        self.assertNotEqual(candidate['baseline_id'], latest)

    def test_daily_normal_values_fall_back_per_path_without_chasing_growth(self):
        older = self.scan('L2', self.anchor-100, {'/synthetic/a': GIB, '/synthetic/b': 7*GIB})
        latest = self.scan('L2', self.anchor-2, {'/synthetic/a': 10*GIB, '/synthetic/b': ('directory', None, 'error')}, state='partial')
        self.trigger()
        item = self.finish_broad({'/synthetic/a': 10*GIB, '/synthetic/b': 10*GIB})
        ev = item['investigation'][0]['periods'][DAY]
        self.assertEqual(ev['compared_count'], 2)
        self.assertEqual([(c['path'], c['baseline_id']) for c in item['candidates']], [('/synthetic/b', older)])
        self.assertEqual({s['path']: s['baseline_id'] for s in ev['baseline_sources']},
                         {'/synthetic/a': latest, '/synthetic/b': older})
        self.assertEqual(ev['baseline_failures'], 1)

    def test_daily_excludes_crossing_version_state_and_other_kind(self):
        valid = self.scan('L2', self.anchor-20, {'/synthetic/cache': 7*GIB})
        for when, version, state in ((self.anchor-2, 1, 'succeeded'), (self.anchor-2, 2, 'failed'),
                                     (self.anchor, 2, 'succeeded')):
            self.scan('L2', when, {'/synthetic/cache': 10*GIB}, version=version, state=state)
        self.scan('L2', self.anchor-2, {'/synthetic/cache': ('file', 10*GIB, 'ok')})
        self.trigger()
        item = self.finish_broad({'/synthetic/cache': 10*GIB})
        self.assertEqual(item['candidates'][0]['baseline_id'], valid)

    def test_daily_allows_ended_equal_to_anchor(self):
        baseline = self.scan('L2', self.anchor-1, {'/synthetic/cache': 7*GIB})
        self.trigger()
        self.assertEqual(self.finish_broad({'/synthetic/cache': 10*GIB})['candidates'][0]['baseline_id'], baseline)

    def test_daily_does_not_run_l3_when_no_candidates_or_no_targets(self):
        self.trigger(targets={})
        item = self.advance({})
        self.assertEqual((item['state'], [s['layer'] for s in item['investigation']]), ('unidentified', ['L2']))
        self.assertEqual(item['investigation'][0]['periods'][DAY]['unavailable_reasons'], {'targets_unconfigured': 1})

    def test_daily_missing_baseline_is_not_zero_or_no_candidates(self):
        self.trigger()
        item = self.finish_broad({'/synthetic/cache': 10*GIB})
        ev = item['investigation'][0]['periods'][DAY]
        self.assertEqual((item['state'], ev['reason'], ev['compared_count']), ('unidentified', 'comparison_unavailable', 0))
        self.assertEqual(ev['unavailable_reasons'], {'baseline_value_unavailable': 1})

    def test_candidates_and_current_failure_coexist(self):
        self.scan('L2', self.anchor-1, {'/synthetic/a': GIB, '/synthetic/b': GIB})
        self.trigger()
        item = self.finish_broad({'/synthetic/a': 4*GIB, '/synthetic/b': ('directory', None, 'error')}, state='partial')
        ev = item['investigation'][0]['periods'][DAY]
        self.assertEqual((len(item['candidates']), ev['unavailable_count'], ev['compared_count']), (1, 1, 1))
        self.assertEqual(ev['unavailable_reasons'], {'current_value_unavailable': 1})

    def test_half_threshold_boundaries_and_no_parent_sum(self):
        self.scan('L2', self.anchor-1, {'/synthetic/a': 0, '/synthetic/b': 0, '/synthetic/c': 0})
        self.trigger()
        item = self.finish_broad({'/synthetic/a': 5*GIB//2-1, '/synthetic/b': 5*GIB//2,
                                 '/synthetic/c': 5*GIB//2+1})
        self.assertEqual({c['path'] for c in item['candidates']}, {'/synthetic/b', '/synthetic/c'})

    def test_mixed_preserves_short_l3_candidate_and_always_scans_daily_l2(self):
        self.scan('L3', self.now-10, {'/synthetic/cache': GIB})
        self.scan('L2', self.anchor-1, {'/synthetic/other': 7*GIB})
        item = self.trigger(mixed=True)
        self.assertEqual(item['stage'], 'L3')
        item = self.finish_broad({'/synthetic/cache': 2*GIB})
        self.assertEqual((item['stage'], len(item['candidates'])), ('L2', 1))
        item = self.finish_broad({'/synthetic/other': 10*GIB})
        self.assertEqual([s['layer'] for s in item['investigation']], ['L3', 'L2', 'L9'])
        self.assertEqual(set(item['investigation'][1]['periods']), {DAY})
        self.assertEqual({c['rule'] for c in item['candidates']}, {SHORT, DAY})
        self.assertEqual(len(self.store.rows('SELECT * FROM incidents')), 1)

    def test_mixed_l2_is_shared_and_uses_each_rule_threshold(self):
        self.scan('L2', self.anchor-10, {'/synthetic/cache': 7*GIB})
        self.scan('L2', self.now-10, {'/synthetic/cache': 9*GIB})
        self.trigger(mixed=True)
        self.finish_broad({'/synthetic/cache': 10*GIB})  # L3 has no baseline
        item = self.finish_broad({'/synthetic/cache': 10*GIB})
        periods = item['investigation'][1]['periods']
        self.assertEqual(set(periods), {DAY, SHORT})
        self.assertEqual({c['rule']: c['delta'] for c in item['candidates']}, {SHORT: GIB, DAY: 3*GIB})
        self.assertEqual(len(item['investigation'][-1]['targets']), 1)
        self.assertEqual(len(self.store.rows("SELECT * FROM scans WHERE layer='L2' AND trigger='incident'")), 1)
        self.assertEqual(item['investigation'][-1]['options']['threshold'], GIB)

    def test_mixed_failed_l2_keeps_short_candidate(self):
        self.scan('L3', self.now-10, {'/synthetic/cache': GIB})
        self.trigger(mixed=True)
        self.finish_broad({'/synthetic/cache': 2*GIB})
        item = self.finish_broad({}, state='cancelled')
        self.assertEqual((item['stage'], len(item['candidates'])), ('L9', 1))
        self.assertEqual(item['investigation'][1]['periods'][DAY]['reason'], 'comparison_unavailable')

    def test_short_latest_scan_selection_is_unchanged(self):
        self.scan('L3', self.anchor-10, {'/synthetic/cache': GIB})
        latest = self.scan('L3', self.now-10, {'/synthetic/cache': 10*GIB})
        self.trigger(short=True)
        item = self.finish_broad({'/synthetic/cache': 10*GIB})
        self.assertEqual(item['stage'], 'L2')
        ev = item['investigation'][0]['periods'][SHORT]
        self.assertEqual((ev['baseline_id'], ev['reason']), (latest, 'no_candidates'))

    def test_multiple_old_baselines_protected_across_restart_and_expiry(self):
        old = self.anchor-4*366*86400
        a = self.scan('L2', old, {'/synthetic/a': GIB})
        b = self.scan('L2', old+100, {'/synthetic/b': GIB})
        self.trigger()
        maintain(self.store, self.now)
        self.assertEqual(len(self.store.rows('SELECT * FROM scans WHERE id IN (?,?)', (a, b))), 2)
        reopened = Store(self.directory)
        try:
            with patch('blackbox.incidents.time.time', return_value=self.now+2):
                advance(reopened, self.targets)
        finally:
            reopened.close()
        item = self.finish_broad({'/synthetic/a': GIB, '/synthetic/b': GIB})
        self.assertEqual(item['state'], 'unidentified')
        maintain(self.store, self.now)
        self.assertFalse(self.store.rows('SELECT * FROM scans WHERE id IN (?,?)', (a, b)))
        ev = self.item()['investigation'][0]['periods'][DAY]
        self.assertEqual(set(ev['baselines']), {a, b})
        self.assertEqual(ev['compared_count'], 2)

    def test_old_inflight_record_keeps_legacy_route_and_json(self):
        self.scan('L3', self.now-10, {'/synthetic/cache': GIB})
        item = self.trigger(short=True)
        detection = item['detection']; detection.pop('investigation_version')
        steps = item['investigation']; steps[0].pop('periods')
        saved = json.dumps(detection)
        self.store.execute('UPDATE incidents SET detection=?,investigation=? WHERE id=?',
                           (saved, json.dumps(steps), self.ident))
        item = self.finish_broad({'/synthetic/cache': 2*GIB})
        self.assertEqual(item['stage'], 'L9')
        self.assertNotIn('periods', item['investigation'][0])
        self.assertEqual(self.store.rows('SELECT detection FROM incidents WHERE id=?', (self.ident,))[0]['detection'], saved)

    def test_daily_l9_uses_old_endpoint_and_never_replays_scan(self):
        self.scan('L2', self.anchor-1, {'/synthetic/cache': 7*GIB})
        opts = dict(threshold=5*GIB, max_depth=7)
        baseline = self.scan('L9', self.anchor-5, {'/synthetic/cache': 7*GIB}, options=opts)
        self.scan('L9', self.now-5, {'/synthetic/cache': 13*GIB}, options=opts)
        self.trigger()
        self.finish_broad({'/synthetic/cache': 13*GIB})
        self.scan('L9', self.now+5, {'/synthetic/cache': 13*GIB}, options=opts)
        item = self.advance()
        self.assertEqual(item['state'], 'identified')
        ev = item['investigation'][-1]['target_evaluations']['/synthetic/cache/*']['periods'][DAY]
        self.assertEqual((ev['baseline_id'], ev['findings'][0]['delta']), (baseline, 6*GIB))
        self.assertEqual(ev['baseline']['ended'], self.anchor-4)

    def test_mixed_l9_can_identify_day_only_using_existing_final_threshold(self):
        old = self.scan('L9', self.anchor-5, {'/synthetic/cache': 7*GIB})
        recent = self.scan('L9', self.now-5, {'/synthetic/cache': 10*GIB})
        self.scan('L2', self.anchor-1, {'/synthetic/cache': 7*GIB})
        self.trigger(mixed=True)
        self.finish_broad({})
        self.finish_broad({'/synthetic/cache': 10*GIB})
        self.scan('L9', self.now+5, {'/synthetic/cache': 10*GIB})
        item = self.advance()
        ev = item['investigation'][-1]['target_evaluations']['/synthetic/cache/*']
        self.assertEqual(item['state'], 'identified')
        self.assertEqual(ev['periods'][DAY]['baseline_id'], old)
        self.assertEqual(ev['periods'][SHORT]['baseline_id'], recent)
        self.assertEqual(ev['periods'][SHORT]['reason'], 'insufficient_evidence')
        self.assertEqual({c['rule'] for c in item['evaluation']['findings']}, {DAY})

    def test_daily_l9_ineligible_and_unrecorded_detail_remain_unavailable(self):
        self.scan('L2', self.anchor-1, {'/synthetic/cache': 7*GIB})
        opts = dict(threshold=5*GIB, max_depth=7)
        self.scan('L9', self.anchor-5, {'/synthetic/cache': 7*GIB})  # different options
        self.trigger()
        self.finish_broad({'/synthetic/cache': 13*GIB})
        self.scan('L9', self.now+5, {'/synthetic/cache': 13*GIB,
                                   '/synthetic/cache/new': ('file', 6*GIB, 'ok')}, options=opts)
        item = self.advance()
        self.assertEqual(item['state'], 'unidentified')
        ev = item['investigation'][-1]['target_evaluations']['/synthetic/cache/*']['periods'][DAY]
        self.assertEqual((ev['reason'], ev['comparison_limit']), ('no_baseline', 'no_eligible_normal_history'))
        self.assertEqual(ev['unavailable_count'], 2)

    def test_daily_l9_old_history_is_protected_until_evaluation(self):
        self.scan('L2', self.anchor-1, {'/synthetic/cache': 7*GIB})
        opts = dict(threshold=5*GIB, max_depth=7)
        baseline = self.scan('L9', self.anchor-4*366*86400, {'/synthetic/cache': 7*GIB}, options=opts)
        self.trigger()
        item = self.finish_broad({'/synthetic/cache': 13*GIB})
        self.assertIn(baseline, item['scan_ids'])
        maintain(self.store, self.now)
        self.assertTrue(self.store.rows('SELECT id FROM scans WHERE id=?', (baseline,)))
        self.scan('L9', self.now+5, {'/synthetic/cache': 13*GIB}, options=opts)
        self.assertEqual(self.advance()['state'], 'identified')
        maintain(self.store, self.now)
        self.assertFalse(self.store.rows('SELECT id FROM scans WHERE id=?', (baseline,)))
        self.assertIn(baseline, self.item()['investigation'][-1]['target_evaluations']['/synthetic/cache/*']['periods'][DAY]['baselines'])

    def test_daily_source_selection_rolls_back_with_incident_creation(self):
        baseline = self.scan('L2', self.anchor-1, {'/synthetic/cache': 7*GIB})
        self.l1(self.anchor, 20*GIB)
        detect(self.store, self.targets)
        self.l1(self.now, 14*GIB)
        self.store.execute("CREATE TRIGGER reject_incident BEFORE INSERT ON incidents BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            detect(self.store, self.targets)
        self.assertFalse(self.store.rows('SELECT * FROM incidents'))
        self.assertEqual(self.store.rows('SELECT id FROM scans'), [{'id': baseline}])
        self.store.execute('DROP TRIGGER reject_incident')
        detect(self.store, self.targets)
        self.assertEqual(len(details(self.store)), 1)
        self.assertIn(baseline, details(self.store)[0]['scan_ids'])

    def test_daily_missing_current_path_is_not_complete_no_growth(self):
        self.scan('L2', self.anchor-1, {'/synthetic/cache': GIB, '/synthetic/absent': GIB})
        self.trigger()
        item = self.finish_broad({'/synthetic/cache': GIB})
        period = item['investigation'][0]['periods'][DAY]
        self.assertEqual((period['reason'], period['unavailable_count']), ('partly_comparable', 1))
        self.assertEqual(period['unavailable_reasons'], {'current_value_missing': 1})

    def test_low_space_and_reboot_keep_l3_then_l2_and_their_thresholds(self):
        self.l1(self.now-60, 6*GIB)
        detect(self.store, self.targets)
        self.l1(self.now, 4*GIB, boot='new-boot')
        detect(self.store, self.targets)
        items = {item['type']: item for item in details(self.store)}
        for kind, threshold in (('LOW_SPACE', 5*GIB), ('REBOOT', GIB)):
            item = items[kind]
            self.assertEqual(item['stage'], 'L3')
            self.assertEqual(item['detection']['threshold'], threshold)
        for item in items.values():
            self.scan('L3', self.now+1, {}, ident=item['investigation'][-1]['scan_id'])
        with patch('blackbox.incidents.time.time', return_value=self.now+2):
            advance(self.store, self.targets)
        self.assertTrue(all(item['stage'] == 'L2' for item in details(self.store)))

    def test_l9_missing_execution_records_each_period(self):
        self.scan('L2', self.anchor-1, {'/synthetic/cache': 7*GIB})
        self.trigger(mixed=True)
        self.finish_broad({})
        self.finish_broad({'/synthetic/cache': 10*GIB})
        self.store.execute('DELETE FROM waiting')  # synthetic abandoned request
        item = self.advance()
        periods = item['investigation'][-1]['target_evaluations']['/synthetic/cache/*']['periods']
        self.assertEqual(set(periods), {SHORT, DAY})
        self.assertTrue(all(ev['reason'] == 'scan_incomplete' for ev in periods.values()))
        self.assertEqual(item['state'], 'unidentified')


if __name__ == '__main__':
    unittest.main()
