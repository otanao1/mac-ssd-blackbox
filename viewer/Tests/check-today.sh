#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
output=local/issue34
mkdir -p "$output"
.venv/bin/python -m viewer.Tests.TodayFixture "$output"
xcrun swiftc -module-cache-path "$output/ModuleCache" -parse-as-library \
 viewer/Shared/L1Snapshot.swift viewer/App/StorageExplorerData.swift viewer/App/DatabaseReader.swift \
 viewer/App/TodayData.swift viewer/App/TodayReader.swift viewer/Tests/TodayChecks.swift -o "$output/TodayChecks"
"$output/TodayChecks" "$PWD/$output"
