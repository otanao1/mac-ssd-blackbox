"""Common JSON defaults, private overrides, and conf.d targets."""
import copy
import json
import math
from pathlib import Path

from .incidents import thresholds
from .retention import policy
from .runtime import DEFAULT_INTERVALS
from .scan import normalize

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'config'
DEFAULT_PRIVATE = Path.home() / 'Library/Application Support/mac-ssd-blackbox'


def merge(base, override):
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load(common=DEFAULT_CONFIG, private=DEFAULT_PRIVATE):
    common, private = Path(common).absolute(), Path(private).absolute()
    files = [common / 'settings.json', private / 'local.json']
    values = {}
    used = []
    for file in files:
        if not file.exists():
            if file == files[0]:
                raise ValueError(f'missing common configuration: {file}')
            continue
        raw = json.loads(file.read_text())
        if not isinstance(raw, dict):
            raise ValueError('configuration must be a JSON object')
        values = merge(values, raw)
        used.append(str(file))
    allowed = {'intervals', 'l1_path', 'targets', 'retention', 'incident_thresholds', 'storage'}
    if set(values) - allowed:
        raise ValueError('unknown configuration keys: ' + ', '.join(sorted(set(values)-allowed)))
    for key in ('intervals', 'targets', 'retention', 'incident_thresholds'):
        if key in values and not isinstance(values[key], dict):
            raise ValueError(f'{key} must be a JSON object')
    if 'l1_path' in values and not isinstance(values['l1_path'], str):
        raise ValueError('l1_path must be a path string')
    intervals = DEFAULT_INTERVALS | values.get('intervals', {})
    if set(intervals) != set(DEFAULT_INTERVALS) or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0
            for v in intervals.values()):
        raise ValueError('intervals must be positive finite seconds')
    values['intervals'] = intervals
    values['retention'] = policy(values.get('retention'))
    values['incident_thresholds'] = thresholds(values.get('incident_thresholds'))
    l1, children = normalize(values.get('l1_path', '/System/Volumes/Data'))
    if children:
        raise ValueError('l1_path must be a plain absolute path')
    values['l1_path'] = l1
    targets = values.get('targets', {})
    if not isinstance(targets, dict) or set(targets)-{'L2', 'L3'}:
        raise ValueError('targets may only contain L2 and L3')
    directories = []
    for layer in ('L2', 'L3'):
        # Same-name local files replace common files; empty files disable a common list.
        selected = {}
        for root in (common, private):
            directory = root / layer.lower() / 'conf.d'
            directories.append(str(directory))
            if directory.exists() and not directory.is_dir():
                raise ValueError(f'not a configuration directory: {directory}')
            selected.update({p.name: p for p in sorted(directory.glob('*.conf'))})
        paths = targets.get(layer, [])
        if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
            raise ValueError('targets must be lists of path strings')
        paths = list(paths)
        for file in selected.values():
            used.append(str(file))
            paths.extend(line.strip() for line in file.read_text().splitlines()
                         if line.strip() and not line.lstrip().startswith('#'))
        values.setdefault('targets', {})[layer] = sorted({p + ('/*' if c else '') for p, c in map(normalize, paths)})
    storage = values.get('storage')
    if storage is not None:
        from .destination import validate_identity
        validate_identity(storage)
    values['sources'] = dict(files=used, directories=directories, common=str(common), private=str(private))
    return values
