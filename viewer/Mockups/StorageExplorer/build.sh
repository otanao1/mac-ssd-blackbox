#!/bin/sh
# Standalone synthetic-data review app; production targets are untouched.
set -eu
cd "$(dirname "$0")/../../.."
mkdir -p local/issue22
xcrun swiftc -module-cache-path local/issue22/ModuleCache -parse-as-library \
  viewer/Shared/L1Snapshot.swift \
  viewer/App/StorageExplorerData.swift \
  viewer/Mockups/StorageExplorer/StorageExplorerMock.swift \
  -o local/issue22/StorageExplorerMock-final
python3 - <<'PY'
from pathlib import Path
import plistlib
import shutil
root = Path('local/issue22')
contents = root / 'BlackboxViewerMock.app/Contents'
(contents / 'MacOS').mkdir(parents=True, exist_ok=True)
shutil.copy2(root / 'StorageExplorerMock-final', contents / 'MacOS/BlackboxViewerMock')
(contents / 'Info.plist').write_bytes(plistlib.dumps(dict(
    CFBundleIdentifier='org.example.blackbox.storage-review-v5',
    CFBundleName='Blackbox Viewer', CFBundleDisplayName='Blackbox Viewer', CFBundleExecutable='BlackboxViewerMock',
    CFBundlePackageType='APPL', NSHighResolutionCapable=True)))
print('Review app: local/issue22/BlackboxViewerMock.app')
PY
