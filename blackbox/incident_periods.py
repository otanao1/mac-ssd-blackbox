"""Period evidence for new Incidents; no additional queue, schema or scan policy."""
import json
from pathlib import PurePosixPath

from .store import scan_version

DAY = 'DROP_SPACE_24h'


def _metadata(scan):
    return {key: scan[key] for key in ('started', 'ended', 'state', 'error', 'scan_version')}


def _in_targets(path, targets):
    return any(path == target.removesuffix('/*') or
               (target.endswith('/*') and str(PurePosixPath(path).parent) == target[:-2])
               for target in targets)


def _daily_sources(store, item, period, targets, version):
    """Freeze only latest usable rows per path/kind, protecting their source scans."""
    sources, first_seen, failures = {}, set(), []
    failure_count = 0
    baselines = {}
    rows = store.db.execute("""SELECT r.*,s.started,s.ended,s.state,s.error AS scan_error,s.scan_version
        FROM scans s JOIN results r ON r.scan_id=s.id
        WHERE s.layer='L2' AND s.scan_version=? AND s.state IN ('succeeded','partial')
        AND s.ended<=? ORDER BY s.ended DESC,s.id DESC,r.path,r.kind""",
        (version, period['baseline_time']))
    for row in rows:
        if not _in_targets(row['path'], targets):
            continue
        key = (row['path'], row['kind'])
        if key not in first_seen:
            first_seen.add(key)
            if row['status'] != 'ok' or row['bytes'] is None:
                failure_count += 1
                if len(failures) < 20:
                    failures.append(dict(scan_id=row['scan_id'], path=row['path'], kind=row['kind'],
                                         status=row['status'], error=row['error'], ended=row['ended']))
        if key in sources or row['status'] != 'ok' or row['bytes'] is None:
            continue
        sources[key] = dict(path=row['path'], kind=row['kind'], baseline_id=row['scan_id'])
        baselines[row['scan_id']] = dict(started=row['started'], ended=row['ended'], state=row['state'],
                                         error=row['scan_error'], scan_version=row['scan_version'])
    period.update(baseline_sources=list(sources.values()), baselines=baselines,
                  baseline_failures=failure_count, baseline_failure_excerpts=failures)
    for ident in baselines:
        if ident not in item['scan_ids']:
            item['scan_ids'].append(ident)


def needs_l2(item):
    return any(rule['rule'] == DAY for rule in item['detection']['rules']) or not item['candidates']


def prepare(store, item, step, targets):
    periods = {}
    for rule in item['detection']['rules']:
        name = rule['rule']
        if name == DAY and step['layer'] != 'L2':
            continue
        if name != DAY and step['layer'] == 'L2' and item['candidates']:
            # A short-period L3 candidate already exists; L2 is for the day only.
            continue
        period = dict(rule=name, threshold=rule['threshold'], baseline_time=rule['baseline']['time'])
        if name == DAY:
            _daily_sources(store, item, period, targets, scan_version('L2'))
        else:
            ident = step.get('baseline_id')
            baseline = store.rows('SELECT * FROM scans WHERE id=?', (ident,))
            period.update(baseline_id=ident, baselines={ident: _metadata(baseline[0])} if baseline else {})
        periods[name] = period
    step.update(periods=periods, targets=targets)


def _reason(compared, unavailable, candidates):
    if candidates:
        return 'candidates_found'
    if unavailable:
        return 'comparison_unavailable' if compared == 0 else 'partly_comparable'
    return 'no_candidates'


def evaluate(store, item, step):
    if not step:
        return True
    scan = store.rows('SELECT * FROM scans WHERE id=?', (step['scan_id'],)) if step.get('scan_id') else []
    if scan and scan[0]['state'] not in ('succeeded', 'partial', 'failed', 'cancelled', 'skipped'):
        return False
    scan = scan[0] if scan else None
    if scan:
        step.update(_metadata(scan), targets=json.loads(scan['targets']), options=json.loads(scan['options']))
    all_candidates = []
    for name, period in step['periods'].items():
        source_map = {(s['path'], s['kind']): s['baseline_id'] for s in period.get('baseline_sources', [])}
        changes, candidates, gaps, failure_excerpts = [], [], {}, []
        compared = change_count = failures = unavailable = 0
        seen, used = set(), set()

        def gap(reason, path=None, kind=None, status=None, error=None):
            nonlocal unavailable
            unavailable += 1
            gaps[reason] = gaps.get(reason, 0) + 1
            if len(failure_excerpts) < 20:
                failure_excerpts.append(dict(reason=reason, path=path, kind=kind, status=status, error=error))

        if not scan:
            gap('targets_unconfigured' if not step['targets'] else 'scan_missing')
        elif scan['state'] not in ('succeeded', 'partial'):
            gap('scan_incomplete', error=scan['error'])
        else:
            count = 0
            for row in store.db.execute('SELECT * FROM results WHERE scan_id=? ORDER BY path,kind', (scan['id'],)):
                count += 1
                key = (row['path'], row['kind'])
                seen.add(key)
                if row['status'] == 'deduplicated':
                    continue
                if row['status'] != 'ok' or row['bytes'] is None:
                    failures += 1
                    gap('current_value_unavailable', row['path'], row['kind'], row['status'], row['error'])
                    continue
                ident = source_map.get(key) if name == DAY else period.get('baseline_id')
                if period['baselines'].get(ident, {}).get('scan_version') != scan['scan_version']:
                    ident = None
                before = store.db.execute('SELECT bytes,status FROM results WHERE scan_id=? AND path=? AND kind=?',
                                          (ident, *key)).fetchone()
                if not before or before['status'] != 'ok' or before['bytes'] is None:
                    gap('baseline_value_unavailable', *key, before['status'] if before else None)
                    continue
                compared += 1
                used.add(ident)
                delta = row['bytes'] - before['bytes']
                if not delta:
                    continue
                change_count += 1
                change = dict(path=row['path'], kind=row['kind'], before_bytes=before['bytes'], bytes=row['bytes'],
                              delta=delta, rule=name, layer=step['layer'], scan_id=scan['id'], baseline_id=ident)
                changes.append(change)
                changes.sort(key=lambda value: abs(value['delta']), reverse=True)
                del changes[20:]
                if delta >= period['threshold'] * .5 and row['kind'] in ('directory', 'file'):
                    candidates.append(change)
            if not count:
                gap('current_results_missing')
            for path, kind in source_map.keys() - seen:
                gap('current_value_missing', path, kind)
            # A completely missing configured root must not look like a complete zero-growth comparison.
            for target in step['targets']:
                root = target.removesuffix('/*')
                if not any(path == root or (target.endswith('/*') and str(PurePosixPath(path).parent) == root)
                           for path, _ in seen):
                    gap('current_target_missing', root)
            if not compared and not unavailable:
                gap('no_normal_comparison')
        period.update(compared_count=compared, change_count=change_count, changes=changes, candidates=candidates,
                      failures=failures, failure_excerpts=failure_excerpts, unavailable_count=unavailable,
                      unavailable_reasons=gaps, used_baseline_ids=sorted(used),
                      reason=_reason(compared, unavailable, candidates))
        all_candidates.extend(candidates)
    item['candidates'].extend(all_candidates)
    step['compared_count'] = sum(p['compared_count'] for p in step['periods'].values())
    return True


def evaluate_l9(store, item, target, scan, options):
    from .incidents import _evaluate_l9
    periods = {}
    for rule in item['detection']['rules']:
        name = rule['rule']
        ev = _evaluate_l9(store, item, target, scan, options,
                          baseline_time=rule['baseline']['time'] if name == DAY else None)
        ev.update(rule=name, baseline_time=rule['baseline']['time'], threshold=item['detection']['threshold'])
        ev['baselines'] = {ev['baseline_id']: ev['baseline']} if ev['baseline_id'] else {}
        for change in ev['changes'] + ev['findings']:
            change['rule'] = name
        root = target.removesuffix('/*')
        prefix = root.rstrip('/') + '/'
        gaps = []
        unavailable_count = 0
        for row in store.db.execute("""SELECT r.path,r.kind,r.status,b.status AS before_status
            FROM results r LEFT JOIN results b ON b.scan_id=? AND b.path=r.path AND b.kind=r.kind
            WHERE r.scan_id=? AND (r.path=? OR substr(r.path,1,?)=?)
            AND (r.status!='ok' OR r.bytes IS NULL OR b.status IS NULL OR b.status!='ok' OR b.bytes IS NULL)
            AND r.status!='deduplicated' ORDER BY r.path,r.kind""",
            (ev['baseline_id'], scan['id'], root, len(prefix), prefix)):
            unavailable_count += 1
            if len(gaps) < 20:
                gaps.append(dict(row))
        ev.update(unavailable_count=unavailable_count, failure_comparison_excerpts=gaps)
        if scan['state'] not in ('succeeded', 'partial'):
            ev['comparison_limit'] = 'scan_incomplete'
            ev['unavailable_count'] = max(1, unavailable_count)
        elif not ev['has_baseline']:
            ev['comparison_limit'] = 'no_eligible_normal_history' if ev['baseline_id'] is None else 'no_common_normal_paths'
        elif gaps:
            ev['comparison_limit'] = 'detail_values_unavailable'
        periods[name] = ev
    findings = [finding for ev in periods.values() for finding in ev['findings']]
    representative = next((ev for ev in periods.values() if ev['findings']), None) or next(
        (ev for reason in ('scan_incomplete', 'no_baseline', 'insufficient_evidence')
         for ev in periods.values() if ev['reason'] == reason))
    result = dict(representative)
    result.update(periods=periods, findings=findings)
    return result


def pending_l9(item, target, *, waiting, scan=None):
    return {rule['rule']: dict(rule=rule['rule'], target=target, scan_id=scan['id'] if scan else None,
                              started=scan['started'] if scan else None, ended=None,
                              baseline_time=rule['baseline']['time'], threshold=item['detection']['threshold'],
                              reason=None if waiting else 'scan_incomplete', compared_count=0,
                              unavailable_count=0 if waiting else 1, comparison_limit=None if waiting else 'scan_missing',
                              findings=[], baselines={}) for rule in item['detection']['rules']}


def protect_l9_history(store, item, targets, options, requested):
    """Protect potentially selectable history using the existing Incident ID list.

    Only the newest Scan for any normal path can win the target-level lookup.
    Keep the latest incomplete attempt as well, for the existing failure evidence.
    """
    for rule in item['detection']['rules']:
        cutoff = rule['baseline']['time'] if rule['rule'] == DAY else requested
        for target in targets:
            seen, first = set(), True
            root = target.removesuffix('/*')
            prefix = root.rstrip('/') + '/'
            for raw in store.db.execute("""SELECT * FROM scans WHERE layer='L9' AND scan_version=?
                AND ended<=? AND started IS NOT NULL ORDER BY ended DESC""", (scan_version('L9'), cutoff)):
                if target not in json.loads(raw['targets']) or json.loads(raw['options']) != options:
                    continue
                keys = set()
                if raw['state'] in ('succeeded', 'partial'):
                    keys = {(r['path'], r['kind']) for r in store.db.execute("""SELECT path,kind FROM results
                        WHERE scan_id=? AND status='ok' AND bytes IS NOT NULL
                        AND (path=? OR substr(path,1,?)=?)""", (raw['id'], root, len(prefix), prefix))}
                if first or keys - seen:
                    if raw['id'] not in item['scan_ids']:
                        item['scan_ids'].append(raw['id'])
                seen.update(keys)
                first = False
