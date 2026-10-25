#!/bin/sh
# All DBs written by the checks are synthetic, under ignored local/.
set -eu
cd "$(dirname "$0")/../.."
mkdir -p local/issue22
xcrun swiftc -module-cache-path local/issue22/ModuleCache -parse-as-library \
  viewer/Shared/L1Snapshot.swift viewer/App/StorageExplorerData.swift \
  viewer/App/DatabaseReader.swift viewer/Tests/StorageExplorerChecks.swift \
  -o local/issue22/StorageExplorerChecks
local/issue22/StorageExplorerChecks "$PWD/local/issue22"
xcrun swiftc -module-cache-path local/issue22/ModuleCache -parse-as-library \
  viewer/Shared/L1Snapshot.swift viewer/Tests/ViewerLogicChecks.swift \
  -o local/issue22/ViewerLogicChecks
local/issue22/ViewerLogicChecks
