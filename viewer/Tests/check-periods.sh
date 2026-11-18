#!/bin/sh
# Real Incident evaluation on synthetic rows; no monitor, scans, app launch or production source.
set -eu
cd "$(dirname "$0")/../.."
output=$(mktemp -d "${TMPDIR:-/tmp}/blackbox-periods.XXXXXX")
trap 'rm -rf "$output"' EXIT
.venv/bin/python -m viewer.Tests.PeriodFixture "$output"
xcrun swiftc -module-cache-path "$output/ModuleCache" -parse-as-library \
 viewer/Shared/L1Snapshot.swift viewer/App/StorageExplorerData.swift viewer/App/DatabaseReader.swift \
 viewer/App/TodayData.swift viewer/App/TodayReader.swift viewer/Tests/PeriodChecks.swift -o "$output/PeriodChecks"
"$output/PeriodChecks" "$output"
