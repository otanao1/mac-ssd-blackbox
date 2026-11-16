"""Frozen pre-optimization selector (PR #48, 14426ed), for equivalence checks only."""
from blackbox.incident_periods import _in_targets


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
