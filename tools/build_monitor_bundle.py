#!/usr/bin/env python3
"""Package an explicit CPython 3.11 build into a COPY of a signed Viewer.

No downloads, installation, registration or permission changes. Build inputs must
include redistributable licenses for Python and each non-system dependency.
"""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HELPER = Path('Contents/Helpers/Blackbox Monitor.app')


def run(*args):
    return subprocess.run([str(x) for x in args], check=True, capture_output=True,
                          text=True).stdout


def dependencies(path):
    return [line.strip().split(' (', 1)[0]
            for line in run('/usr/bin/otool', '-L', path).splitlines()[1:]]


def rpaths(path):
    return re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset',
                      run('/usr/bin/otool', '-l', path))


def version(value):
    return tuple(map(int, value.split('.')))+(0,)*(3-len(value.split('.')))


def prepare_runtime_profile(runtime, bundle_id, fda_access_only=False):
    """Explicit DEV-only instrumentation; default packages always use real Scanner."""
    if not fda_access_only:
        return 'standard'
    if not re.fullmatch(r'io\.github\.otanao1\.blackbox-viewer\.dev\.issue[0-9]+(?:\.fda)?', bundle_id):
        raise ValueError('FDA access-only profile requires an Issue-specific DEV bundle ID')
    adapter = Path(__file__).parent/'fixtures/fda_access_only.py'
    shutil.copy2(adapter, runtime/'blackbox/_fda_privacy_adapter.py')
    main = runtime/'blackbox/__main__.py'
    source = main.read_text()
    anchor = 'from .store import Store\n'
    if source.count(anchor) != 1:
        raise ValueError('cannot locate DEV adapter import point')
    main.write_text(source.replace(anchor, anchor+'from . import _fda_privacy_adapter  # explicit DEV profile only\n'))
    return 'dev-fda-access-only'


def package(viewer, output, python, identity, licenses, minimum_macos=None, fda_access_only=False):
    viewer, output, python, licenses = (Path(p).expanduser().resolve() for p in
                                         (viewer, output, python, licenses))
    if output.exists() or output == viewer or output.is_relative_to(viewer):
        raise ValueError('output must be a new path outside the input application')
    if not licenses.is_dir() or not any(licenses.iterdir()):
        raise ValueError('provide a directory of redistribution licenses')
    info = plistlib.loads((viewer/'Contents/Info.plist').read_bytes())
    if fda_access_only and not re.fullmatch(
            r'io\.github\.otanao1\.blackbox-viewer\.dev\.issue[0-9]+(?:\.fda)?', info['CFBundleIdentifier']):
        raise ValueError('FDA access-only profile requires an Issue-specific DEV bundle ID')
    minimum = minimum_macos or info['LSMinimumSystemVersion']
    if version(minimum) < version(info['LSMinimumSystemVersion']):
        raise ValueError('cannot lower Viewer minimum macOS version')
    architectures = run('/usr/bin/lipo', '-archs', viewer/'Contents/MacOS'/info['CFBundleExecutable']).split()
    run('/usr/bin/codesign', '--verify', '--deep', '--strict', viewer)
    details = subprocess.run(['/usr/bin/codesign', '-d', '--verbose=4', str(viewer)],
                             check=True, capture_output=True, text=True).stderr
    teams = re.findall(r'^TeamIdentifier=(.+)$', details, re.MULTILINE)
    if len(teams) != 1 or teams[0] == 'not set':
        raise ValueError('input Viewer must have a team signature')
    requirement_output = run('/usr/bin/codesign', '-d', '-r-', viewer)
    requirements = [line.removeprefix('designated => ') for line in requirement_output.splitlines()
                    if line.startswith('designated => ')]
    if len(requirements) != 1:
        raise ValueError('cannot determine Viewer designated requirement')
    # Only query the selected build input; never discover a Python via PATH.
    metadata = json.loads(run(python, '-I', '-c',
        'import sys,sysconfig,json;print(json.dumps(dict(version=list(sys.version_info[:3]),'
        'prefix=sys.base_prefix,include=sysconfig.get_path("include"))))'))
    if metadata['version'][:2] != [3, 11]:
        raise ValueError('this packaging recipe requires CPython 3.11')
    prefix = Path(metadata['prefix'])
    output.parent.mkdir(parents=True, exist_ok=True)
    # Failed builds never leave a seemingly complete application at output.
    with tempfile.TemporaryDirectory(prefix='blackbox-package-', dir=output.parent) as tmp:
        app = Path(tmp)/output.name
        shutil.copytree(viewer, app, symlinks=True)
        helper = app/HELPER
        if helper.exists():
            shutil.rmtree(helper)
        resources, frameworks = helper/'Contents/Resources', helper/'Contents/Frameworks'
        binary = helper/'Contents/MacOS/BlackboxMonitor'
        binary.parent.mkdir(parents=True)
        frameworks.mkdir(parents=True)
        stdlib = prefix/'lib/python3.11'
        ignored = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', 'site-packages',
                                         'test', 'tests', 'idlelib', 'tkinter', 'ensurepip', 'lib-dynload')
        shutil.copytree(stdlib, resources/'Python/lib/python3.11', ignore=ignored)
        modules = frameworks/'PythonModules'
        modules.mkdir()
        for src in sorted((stdlib/'lib-dynload').glob('*.so')):
            shutil.copy2(src, modules/src.name)
        runtime = resources/'runtime'
        for name in ('blackbox', 'config'):
            shutil.copytree(ROOT/name, runtime/name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'local*.json'))
        runtime_profile = prepare_runtime_profile(runtime, info['CFBundleIdentifier'], fda_access_only)
        shutil.copytree(licenses, resources/'Licenses')
        lib = prefix/'lib/libpython3.11.dylib'
        shutil.copy2(lib, frameworks/lib.name)
        originals = {lib.resolve(): frameworks/lib.name}
        queue = [(p, stdlib/'lib-dynload'/p.name) for p in modules.glob('*.so')]
        queue.append((frameworks/lib.name, lib))
        objects = []
        for dest, source in queue:
            for dep in dependencies(dest):
                if dep.startswith(('/usr/lib/', '/System/')):
                    continue
                if not dep.startswith('/'):
                    raise ValueError('unsupported build dependency: '+dep)
                src = Path(dep).resolve(strict=True)
                if src == source.resolve():
                    continue
                target = originals.get(src)
                if target is None:
                    target = frameworks/src.name
                    if target.exists():
                        raise ValueError('dependency basename collision: '+src.name)
                    shutil.copy2(src, target)
                    originals[src] = target
                    queue.append((target, src))
                run('/usr/bin/install_name_tool', '-change', dep, '@rpath/'+target.name, dest)
            if dest.suffix == '.dylib':
                run('/usr/bin/install_name_tool', '-id', '@rpath/'+dest.name, dest)
            # Relocated references use only the native main executable's rpath.
            for path in dict.fromkeys(rpaths(dest)):
                run('/usr/bin/install_name_tool', '-delete_rpath', path, dest)
            objects.append(dest)
        run('/usr/bin/xcrun', 'clang', '-Werror', '-I'+metadata['include'],
            ROOT/'viewer/Monitor/main.c', '-L'+str(frameworks), '-lpython3.11',
            '-framework', 'Security', '-framework', 'CoreFoundation',
            '-mmacosx-version-min='+minimum,
            *[flag for arch in architectures for flag in ('-arch', arch)],
            '-Wl,-rpath,@executable_path/../Frameworks', '-o', binary)
        for dep in dependencies(binary):
            if dep.startswith('/') and not dep.startswith(('/usr/lib/', '/System/')):
                if Path(dep).resolve() != lib.resolve():
                    raise ValueError('unexpected helper dependency')
                run('/usr/bin/install_name_tool', '-change', dep,
                    '@rpath/libpython3.11.dylib', binary)
        for obj in objects+[binary]:
            allowed_rpaths = ['@executable_path/../Frameworks'] if obj == binary else []
            if rpaths(obj) != allowed_rpaths:
                raise ValueError('external or unexpected runtime search path: '+obj.name)
            if not set(architectures) <= set(run('/usr/bin/lipo', '-archs', obj).split()):
                raise ValueError('runtime architecture does not cover Viewer: '+obj.name)
            load = run('/usr/bin/otool', '-l', obj)
            minima = re.findall(r'\bminos ([0-9.]+)', load)
            minima += re.findall(r'cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([0-9.]+)', load)
            if not minima or any(version(v) > version(minimum) for v in minima):
                raise ValueError('runtime requires newer macOS than declared: '+obj.name)
            for dep in dependencies(obj):
                if not dep.startswith(('/usr/lib/', '/System/', '@rpath/')):
                    raise ValueError('unrelocated runtime dependency')
            run('/usr/bin/codesign', '--force', '--options', 'runtime', '--sign', identity, obj)
        helper_info = dict(CFBundleIdentifier=info['CFBundleIdentifier']+'.monitor',
                           CFBundleName=info.get('CFBundleDisplayName', info['CFBundleName'])+' Monitor',
                           CFBundleExecutable='BlackboxMonitor', CFBundlePackageType='APPL',
                           CFBundleVersion=info['CFBundleVersion'],
                           CFBundleShortVersionString=info['CFBundleShortVersionString'],
                           LSUIElement=True, LSMinimumSystemVersion=minimum)
        (helper/'Contents/Info.plist').write_bytes(plistlib.dumps(helper_info))
        # Relative names/hashes only: do not disclose build machine paths.
        manifest = dict(python_version=metadata['version'], code_objects=len(objects)+1,
                        runtime_profile=runtime_profile,
                        architectures=architectures, minimum_macos=minimum,
                        native_source_sha256=hashlib.sha256((ROOT/'viewer/Monitor/main.c').read_bytes()).hexdigest(),
                        native_binary_verified_by_codesign=True,
                        files={p.relative_to(helper).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted(helper.rglob('*')) if p.is_file() and p != binary})
        (resources/'runtime-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        run('/usr/bin/codesign', '--force', '--options', 'runtime', '--sign', identity, helper)
        helper_details = subprocess.run(['/usr/bin/codesign', '-d', '--verbose=4', str(helper)],
                                       check=True, capture_output=True, text=True).stderr
        if re.findall(r'^TeamIdentifier=(.+)$', helper_details, re.MULTILINE) != teams:
            raise ValueError('helper and Viewer signing teams must match')
        # Preserve Viewer entitlements; the helper deliberately has none.
        entitlement = subprocess.run(['/usr/bin/codesign', '-d', '--entitlements', ':-', str(viewer)],
                                     capture_output=True, check=True).stdout
        ent = Path(tmp)/'viewer-entitlements.plist'
        ent.write_bytes(entitlement)
        if minimum != info['LSMinimumSystemVersion']:
            info['LSMinimumSystemVersion'] = minimum
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        run('/usr/bin/codesign', '--force', '--options', 'runtime', '--entitlements', ent,
            '--sign', identity, app)
        run('/usr/bin/codesign', '--verify', '--deep', '--strict', app)
        run('/usr/bin/codesign', '--verify', '--strict', '-R='+requirements[0], app)
        app.rename(output)
    return dict(packaged=True, python_version=metadata['version'], code_objects=len(objects)+1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('viewer', 'output', 'python', 'identity', 'licenses'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--minimum-macos', help='explicitly raise packaged minimum when build dependencies require it')
    parser.add_argument('--fda-access-only', action='store_true',
                        help='DEV ONLY: discard capacities/child names for limited Bluetooth/Mail access checks')
    print(json.dumps(package(**vars(parser.parse_args()))))
