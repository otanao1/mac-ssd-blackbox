"""Transactional retention, using UTC buckets and streaming aggregation."""
from datetime import datetime, timezone
import math
import json

DEFAULT_RETENTION = dict(l1_years=3, l2_years=3, l3_years=3, l9_years=3,
                         incident_years=3, l3_raw_days=30, l3_bucket_seconds=86400)


def policy(overrides=None):
    values = DEFAULT_RETENTION | (overrides or {})
    if set(values) != set(DEFAULT_RETENTION) or any(
            isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in values.values()):
        raise ValueError('retention values must be positive integers')
    return values


def years_before(now, years):
    date = datetime.fromtimestamp(now, timezone.utc)
    try:
        return date.replace(year=date.year-years).timestamp()
    except ValueError:  # February 29 -> February 28
        return date.replace(year=date.year-years, day=28).timestamp()


def maintain(store, now, settings=None):
    cfg = policy(settings)
    width = cfg['l3_bucket_seconds']
    raw_cutoff = math.floor((now-cfg['l3_raw_days']*86400)/width)*width
    store.check()
    # One transaction: summaries must commit before the corresponding raw data disappears.
    # Active investigations keep their referenced scans until evidence is evaluated.
    protected = "SELECT j.value FROM incidents i,json_each(i.scan_ids) j WHERE i.state='investigating'"
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        store.db.execute('DELETE FROM incidents WHERE detected<?', (years_before(now, cfg['incident_years']),))
        store.db.execute('DELETE FROM l1 WHERE time<?', (years_before(now, cfg['l1_years']),))
        for layer in ('L2', 'L3', 'L9'):
            cutoff = years_before(now, cfg[layer.lower()+'_years'])
            store.db.execute(f"DELETE FROM results WHERE scan_id IN (SELECT id FROM scans WHERE layer=? AND state NOT IN ('running','pending') AND coalesce(started,created)<? AND id NOT IN ({protected}))", (layer, cutoff))
            store.db.execute(f"DELETE FROM scans WHERE layer=? AND state NOT IN ('running','pending') AND coalesce(started,created)<? AND id NOT IN ({protected})", (layer, cutoff))
        # Summarize only whole buckets; never summarize a partially aged UTC day.
        days = store.db.execute(f"SELECT DISTINCT cast(coalesce(started,created)/? AS INTEGER)*? FROM scans WHERE layer='L3' AND state NOT IN ('running','pending') AND coalesce(started,created)<? AND id NOT IN ({protected}) ORDER BY 1", (width, width, raw_cutoff)).fetchall()
        for (day,) in days:
            args = (day, day+width)
            eligible = f"SELECT id FROM scans WHERE layer='L3' AND state NOT IN ('running','pending') AND coalesce(started,created)>=? AND coalesce(started,created)<? AND id NOT IN ({protected})"
            # A bucket containing protected or running scans is deferred as a whole.
            if store.db.execute(f"SELECT 1 FROM scans WHERE layer='L3' AND coalesce(started,created)>=? AND coalesce(started,created)<? AND (state IN ('pending','running') OR id IN ({protected})) LIMIT 1", args).fetchone():
                continue
            cursor = store.db.execute(f"SELECT r.*,coalesce(s.started,s.created) AS observed FROM results r JOIN scans s ON s.id=r.scan_id WHERE s.id IN ({eligible}) ORDER BY path,kind,observed,s.id", args)
            key, stats = None, None
            def save():
                if stats:
                    store.check()
                    store.db.execute('INSERT OR REPLACE INTO l3_daily VALUES(?,?,?,?,?)',
                                     (day, width, key[0], key[1], json.dumps(stats)))
            previous = None
            for row in cursor:
                pair = (row['path'], row['kind'])
                if pair != key:
                    save()
                    key, previous = pair, None
                    stats = dict(samples=0, valid=0, failures=0, first_time=None, last_time=None,
                                 first_bytes=None, last_bytes=None, min_bytes=None, max_bytes=None,
                                 mean_bytes=None, max_increase=0, max_decrease=0, sum_bytes=0)
                stats['samples'] += 1
                if row['status'] not in ('ok', 'deduplicated') or row['bytes'] is None:
                    stats['failures'] += 1
                    continue
                value = row['bytes']
                if not stats['valid']:
                    stats.update(first_time=row['observed'], first_bytes=value, min_bytes=value, max_bytes=value)
                stats['valid'] += 1
                stats.update(last_time=row['observed'], last_bytes=value,
                             min_bytes=min(stats['min_bytes'], value), max_bytes=max(stats['max_bytes'], value))
                stats['sum_bytes'] += value
                stats['mean_bytes'] = stats['sum_bytes']/stats['valid']
                if previous is not None:
                    stats['max_increase'] = max(stats['max_increase'], value-previous)
                    stats['max_decrease'] = max(stats['max_decrease'], previous-value)
                previous = value
            save()
            store.db.execute(f'DELETE FROM results WHERE scan_id IN ({eligible})', args)
            store.db.execute(f'DELETE FROM scans WHERE id IN ({eligible})', args)
        store.db.execute('DELETE FROM l3_daily WHERE bucket+width<=?', (years_before(now, cfg['l3_years']),))
        store.check()
