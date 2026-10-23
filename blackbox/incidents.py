"""L1 detection and restartable L3 -> L2 -> L9 investigation.

Findings are evidence of growth, not a guarantee of application-level root cause.
"""
import json
import math
from pathlib import Path
import time
import uuid

from .store import scan_version

GIB = 1024**3
DEFAULT_THRESHOLDS = dict(drop_minute=GIB, drop_day=5*GIB, low_space=5*GIB,
                          l9_cooldown=1800)
TERMINAL = ('succeeded', 'partial', 'failed', 'cancelled', 'skipped')


def thresholds(overrides=None):
    values = DEFAULT_THRESHOLDS | (overrides or {})
    if set(values) != set(DEFAULT_THRESHOLDS) or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
            for v in values.values()):
        raise ValueError('Incident thresholds must be positive numbers')
    if any(not isinstance(values[key], int) for key in ('drop_minute', 'drop_day', 'low_space')):
        raise ValueError('Incident byte thresholds must be integers')
    return values


def details(store, ident=None, limit=20):
    rows = store.rows('SELECT * FROM incidents WHERE id=?' if ident else
                      'SELECT * FROM incidents ORDER BY detected DESC LIMIT ?',
                      (ident,) if ident else (max(1, limit),))
    for row in rows:
        for key in ('detection', 'evaluation', 'candidates', 'investigation', 'scan_ids'):
            row[key] = json.loads(row[key])
    return rows


def note(store, ident, text):
    return store.execute('UPDATE incidents SET notes=?,updated=? WHERE id=?',
                         (text, time.time(), ident)).rowcount


def _persist(store, item):
    store.db.execute('UPDATE incidents SET state=?,evaluation=?,candidates=?,investigation=?,scan_ids=?,stage=?,updated=? WHERE id=?',
                     (item['state'], json.dumps(item['evaluation']), json.dumps(item['candidates']),
                      json.dumps(item['investigation']), json.dumps(item['scan_ids']), item['stage'],
                      time.time(), item['id']))


def _enqueue(store, item, layer, targets):
    """Queue and attach a broad investigation scan in the same transaction."""
    if not targets:
        return False
    from .runtime import DEFAULT_OPTIONS
    ident = uuid.uuid4().hex
    store.db.execute("INSERT INTO scans(id,layer,trigger,targets,options,state,created,total,scan_version) VALUES(?,?,?,?,?,'pending',?,?,?)",
                     (ident, layer, 'incident', json.dumps(targets), json.dumps(DEFAULT_OPTIONS), time.time(), len(targets), scan_version(layer)))
    item['scan_ids'].append(ident)
    baseline = store.db.execute("SELECT id FROM scans WHERE layer=? AND scan_version=? AND state IN ('succeeded','partial') AND ended<=? ORDER BY ended DESC LIMIT 1",
                                (layer, scan_version(layer), item['detected'])).fetchone()
    step = dict(layer=layer, scan_id=ident, baseline_id=baseline['id'] if baseline else None)
    if baseline:
        item['scan_ids'].append(baseline['id'])
    item['investigation'].append(step)
    item['stage'] = layer
    return True


def detect(store, targets, settings=None):
    cfg = thresholds(settings)
    marker = store.rows("SELECT value FROM meta WHERE key='incident_l1_time'")
    newest = store.rows('SELECT * FROM l1 WHERE time>? ORDER BY time LIMIT 1', (float(marker[0]['value']),)) if marker else store.rows('SELECT * FROM l1 ORDER BY time DESC LIMIT 1')
    if not newest:
        return
    current = newest[0]
    # A marker and all resulting incidents/requests commit together.
    store.check()
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        marker = store.db.execute("SELECT value FROM meta WHERE key='incident_l1_time'").fetchone()
        if marker and float(marker[0]) >= current['time']:
            return
        store.db.execute("INSERT OR REPLACE INTO meta VALUES('incident_l1_time',?)", (str(current['time']),))
        if current['error'] or current['free'] is None:
            return
        previous = store.db.execute('SELECT * FROM l1 WHERE path=? AND time<? AND error IS NULL AND free IS NOT NULL ORDER BY time DESC LIMIT 1',
                                     (current['path'], current['time'])).fetchone()
        previous = dict(previous) if previous else None
        triggers = []
        if previous:
            elapsed = current['time']-previous['time']
            drop = previous['free']-current['free']
            # Allow scheduling jitter (up to 30s), but never classify long observation gaps as minute drops.
            if 0 < elapsed <= 90 and drop*60/elapsed >= cfg['drop_minute']:
                triggers.append(('DROP_SPACE', 'DROP_SPACE_1m', cfg['drop_minute'], previous))
            if previous['free'] > cfg['low_space'] >= current['free']:
                triggers.append(('LOW_SPACE', 'entry', cfg['low_space'], previous))
            if previous['boot_id'] and current['boot_id'] and previous['boot_id'] != current['boot_id']:
                triggers.append(('REBOOT', 'boot_id', cfg['drop_minute'], previous))
        # The daily rule has its own durable clock and endpoint measurement.
        # Neither minute Incidents nor other Incident types advance this clock.
        daily_key = 'drop_space_24h:' + current['path']
        saved = store.db.execute('SELECT value FROM meta WHERE key=?', (daily_key,)).fetchone()
        daily = json.loads(saved[0]) if saved else None
        if daily is None or current['time'] >= daily['next_due']:
            if daily and daily['baseline']['free']-current['free'] >= cfg['drop_day']:
                triggers.append(('DROP_SPACE', 'DROP_SPACE_24h', cfg['drop_day'], daily['baseline']))
            # First normal sample establishes the baseline. After a gap, evaluate
            # once using real endpoints and schedule from this actual measurement;
            # do not invent values or replay missed daily deadlines.
            store.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                             (daily_key, json.dumps(dict(baseline=current, next_due=current['time']+86400))))
        # Short/day rules are a single Incident with both conditions recorded.
        for kind in dict.fromkeys(t[0] for t in triggers):
            matches = [t for t in triggers if t[0] == kind]
            first = matches[0]
            ident = uuid.uuid4().hex
            detection = dict(current=current, rules=[dict(rule=t[1], threshold=t[2], baseline=t[3]) for t in matches], threshold=first[2])
            item = dict(id=ident, detected=current['time'], state='investigating', stage='L3',
                        evaluation=dict(free_change=current['free']-first[3]['free'],
                                        elapsed_seconds=current['time']-first[3]['time']),
                        candidates=[], investigation=[], scan_ids=[])
            _enqueue(store, item, 'L3', targets.get('L3', []))
            store.db.execute('INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                             (ident, current['time'], kind, item['state'], json.dumps(detection),
                              json.dumps(item['evaluation']), '[]', json.dumps(item['investigation']), '',
                              json.dumps(item['scan_ids']), item['stage'], time.time()))


def _evaluate(store, item, step, root=None):
    scan = store.rows('SELECT * FROM scans WHERE id=?', (step['scan_id'],))
    if not scan or scan[0]['state'] not in TERMINAL:
        return False
    scan = scan[0]
    step.update(state=scan['state'], error=scan['error'], started=scan['started'], ended=scan['ended'],
                targets=json.loads(scan['targets']), options=json.loads(scan['options']))
    baseline = store.rows('SELECT started,ended,state,error,scan_version FROM scans WHERE id=?', (step.get('baseline_id'),))
    step['scan_version'] = scan['scan_version']
    if baseline and baseline[0]['scan_version'] != scan['scan_version']:
        step['baseline_id'] = None
        baseline = []
    step['baseline'] = baseline[0] if baseline else None
    # Per-path comparisons avoid treating a changed configuration or missing baseline as growth.
    scope = '' if root is None else ' AND (r.path=? OR substr(r.path,1,?)=?)'
    prefix = root.rstrip('/')+'/' if root is not None else None
    args = (step.get('baseline_id'), step['scan_id']) + ((root, len(prefix), prefix) if root is not None else ())
    rows = store.db.execute('''SELECT r.path,r.kind,r.bytes,r.status,r.error,b.bytes AS before_bytes,b.status AS before_status
        FROM results r LEFT JOIN results b ON b.scan_id=? AND b.path=r.path AND b.kind=r.kind
        WHERE r.scan_id=?''' + scope + ' ORDER BY r.path,r.kind', args)
    changes, candidates = [], []
    change_count = compared_count = 0
    threshold = item['detection']['threshold']
    failures = 0
    failure_excerpts = []
    for row in rows:
        if row['status'] not in ('ok', 'deduplicated'):
            failures += 1
            if len(failure_excerpts) < 20:
                failure_excerpts.append(dict(path=row['path'], kind=row['kind'], status=row['status'], error=row['error']))
        if row['status'] != 'ok' or row['before_status'] != 'ok' or row['bytes'] is None or row['before_bytes'] is None:
            continue
        compared_count += 1
        delta = row['bytes']-row['before_bytes']
        if delta:
            change = dict(path=row['path'], kind=row['kind'], before_bytes=row['before_bytes'], bytes=row['bytes'], delta=delta)
            change_count += 1
            changes.append(change)
            changes.sort(key=lambda r: abs(r['delta']), reverse=True)
            del changes[20:]
            if delta >= threshold*.5 and row['kind'] in ('directory', 'file'):
                candidates.append(change)
    # Keep evaluated differences, not complete raw scans. Bound excerpts, retain counts.
    step.update(compared_count=compared_count, change_count=change_count, changes=changes,
                failures=failures, failure_excerpts=failure_excerpts)
    item['candidates'] = candidates
    return True


def _l9_complete(store, scan, root):
    """A shared partial scan can still contain a completely acquired target."""
    if scan['state'] not in ('succeeded', 'partial'):
        return False
    row = store.db.execute("SELECT status,bytes FROM results WHERE scan_id=? AND path=? AND kind IN ('directory','file')",
                           (scan['id'], root)).fetchone()
    if not row or row['status'] != 'ok' or row['bytes'] is None:
        return False
    prefix = root.rstrip('/')+'/'
    return store.db.execute("""SELECT 1 FROM results WHERE scan_id=?
        AND (path=? OR substr(path,1,?)=?) AND status NOT IN ('ok','deduplicated') LIMIT 1""",
        (scan['id'], root, len(prefix), prefix)).fetchone() is None


def _evaluate_l9(store, item, target, scan, options):
    root = target.removesuffix('/*')
    baseline, incomplete = None, None
    for raw in store.db.execute("SELECT * FROM scans WHERE layer='L9' AND scan_version=? AND ended<? AND started IS NOT NULL ORDER BY ended DESC", (scan['scan_version'], scan['started'])):
        prior = dict(raw)
        if target not in json.loads(prior['targets']) or json.loads(prior['options']) != options:
            continue
        evaluation = dict(layer='L9', target=target, scan_id=scan['id'], baseline_id=prior['id'])
        compared = dict(detection=item['detection'])
        _evaluate(store, compared, evaluation, root=root)
        if prior['state'] in ('succeeded', 'partial') and evaluation['compared_count']:
            baseline = prior
            break
        if incomplete is None:
            incomplete = prior
    if baseline is None:
        baseline = incomplete
        evaluation = dict(layer='L9', target=target, scan_id=scan['id'],
                          baseline_id=baseline['id'] if baseline else None)
        compared = dict(detection=item['detection'])
        _evaluate(store, compared, evaluation, root=root)
    prefix = root.rstrip('/')+'/'
    evaluation['excerpt'] = store.rows("""SELECT * FROM results WHERE scan_id=?
        AND (path=? OR substr(path,1,?)=?) ORDER BY bytes DESC LIMIT 20""",
        (scan['id'], root, len(prefix), prefix))
    evaluation['baseline_failure_excerpts'] = store.rows("""SELECT path,kind,status,error FROM results
        WHERE scan_id=? AND (path=? OR substr(path,1,?)=?)
        AND status NOT IN ('ok','deduplicated') ORDER BY path,kind LIMIT 20""",
        (evaluation['baseline_id'], root, len(prefix), prefix))
    complete = _l9_complete(store, scan, root)
    baseline_complete = baseline is not None and _l9_complete(store, baseline, root)
    evaluation.update(scan_complete=complete, baseline_complete=baseline_complete,
                      has_baseline=baseline is not None and baseline['state'] in ('succeeded', 'partial')
                      and evaluation['compared_count'] > 0)
    # Candidates still use 0.5T. Final findings require an individual L9 delta >= T;
    # never add parent/child or separate target deltas together.
    evaluation['findings'] = [dict(change, target=target, scan_id=scan['id'], baseline_id=evaluation['baseline_id'])
        for change in compared['candidates']
        if scan['state'] in ('succeeded', 'partial') and evaluation['has_baseline']
        and change['delta'] >= item['detection']['threshold']]
    if evaluation['findings']:
        reason = 'growth_identified'
    elif not complete or (baseline is not None and not baseline_complete):
        reason = 'scan_incomplete'
    elif not evaluation['has_baseline']:
        reason = 'no_baseline'
    else:
        reason = 'insufficient_evidence'
    evaluation['reason'] = reason
    return evaluation


def advance(store, targets, settings=None, notify=None):
    from .runtime import request, options_for
    cfg = thresholds(settings)
    for item in details_active(store):
        finished = False
        store.check()
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            if item['stage'] in ('L3', 'L2'):
                step = item['investigation'][-1] if item['investigation'] else None
                if step and not _evaluate(store, item, step):
                    continue
                if item['candidates']:
                    item['stage'] = 'L9_request'
                elif item['stage'] == 'L3' and _enqueue(store, item, 'L2', targets.get('L2', [])):
                    pass
                else:
                    item.update(state='unidentified', stage='done')
                    finished = True
                _persist(store, item)
        if item['stage'] == 'L9_request':
            paths = sorted({(c['path'] if c['kind'] == 'directory' else str(Path(c['path']).parent))+'/*' for c in item['candidates']})
            opts = options_for(dict(threshold=int(item['detection']['threshold'])))
            # Existing request dedup/cooldown is authoritative. Retrying after a crash is safe.
            result = request(store, 'L9', paths, opts, cfg['l9_cooldown'])
            item['investigation'].append(dict(layer='L9', targets=paths, options=opts,
                                              requested=time.time(), request=result, scans={}))
            item['stage'] = 'L9'
            with store.db:
                _persist(store, item)
        elif item['stage'] == 'L9':
            step = item['investigation'][-1]
            unresolved = False
            step['target_evaluations'] = {}
            for target in step['targets']:
                found = None
                for raw in store.db.execute("SELECT * FROM scans WHERE layer='L9' AND started>=? ORDER BY started DESC", (step['requested']-cfg['l9_cooldown'],)):
                    scan = dict(raw)
                    if target in json.loads(scan['targets']) and json.loads(scan['options']) == step['options']:
                        found = scan
                        break
                if not found:
                    waiting = store.rows('SELECT 1 FROM waiting WHERE target=? AND options=?', (target, json.dumps(step['options'], sort_keys=True)))
                    unresolved |= bool(waiting)
                    step['target_evaluations'][target] = dict(scan_id=None, state=None,
                        scan_complete=False, baseline_id=None, baseline_complete=False, has_baseline=False, findings=[],
                        reason=None if waiting else 'scan_incomplete')
                    continue
                if found['id'] not in item['scan_ids']:
                    item['scan_ids'].append(found['id'])
                step['scans'][found['id']] = {k: found[k] for k in ('state', 'error', 'started', 'ended', 'targets', 'options')}
                if found['state'] not in TERMINAL:
                    unresolved = True
                    step['target_evaluations'][target] = dict(scan_id=found['id'], state=found['state'],
                        scan_complete=False, baseline_id=None, baseline_complete=False, has_baseline=False, findings=[], reason=None)
                    continue
                evaluation = _evaluate_l9(store, item, target, found, step['options'])
                if evaluation['baseline_id'] and evaluation['baseline_id'] not in item['scan_ids']:
                    item['scan_ids'].append(evaluation['baseline_id'])
                step['target_evaluations'][target] = evaluation
            if not unresolved:
                evaluations = list(step['target_evaluations'].values())
                findings = [finding for ev in evaluations if ev['reason']=='growth_identified' for finding in ev['findings']]
                reasons = list(dict.fromkeys(ev['reason'] for ev in evaluations))
                reason = 'growth_identified' if findings else next(
                    (r for r in ('scan_incomplete', 'no_baseline', 'insufficient_evidence') if r in reasons),
                    'insufficient_evidence')
                item.update(state='identified' if findings else 'unidentified', stage='done')
                item['evaluation'].update(reason=reason, reasons=reasons, findings=findings,
                    conclusion='Observed L9 directory/file growth; application root cause is not guaranteed' if findings else 'Insufficient comparable or complete L9 evidence')
                finished = True
            with store.db:
                _persist(store, item)
        if finished and notify:
            notify(dict(event='incident_completed', incident_id=item['id'], state=item['state']))


def details_active(store):
    # Only active records are decoded; ordinary history remains in SQLite.
    ids = store.rows("SELECT id FROM incidents WHERE state='investigating' ORDER BY detected")
    for row in ids:
        yield details(store, row['id'])[0]
