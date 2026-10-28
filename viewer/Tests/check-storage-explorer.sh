#!/bin/sh
# All DBs written by the checks are synthetic, under ignored local/.
set -eu
cd "$(dirname "$0")/../.."
output=${CHECK_OUTPUT:-local/issue22}
mkdir -p "$output"
xcrun swiftc -module-cache-path "$output/ModuleCache" -parse-as-library \
  viewer/Shared/L1Snapshot.swift viewer/App/StorageExplorerData.swift \
  viewer/App/TodayData.swift viewer/App/DatabaseReader.swift viewer/Tests/StorageExplorerChecks.swift \
  -o "$output/StorageExplorerChecks"
"$output/StorageExplorerChecks" "$PWD/$output"
xcrun swiftc -module-cache-path "$output/ModuleCache" -parse-as-library \
  viewer/Shared/L1Snapshot.swift viewer/Tests/ViewerLogicChecks.swift \
  -o "$output/ViewerLogicChecks"
"$output/ViewerLogicChecks"
