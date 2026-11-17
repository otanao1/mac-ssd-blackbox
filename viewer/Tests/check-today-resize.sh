#!/bin/sh
# Explicit opt-in native-window test. Synthetic DB + preview model only;
# no installed app, App Group, Widget host or monitor operation.
set -eu
cd "$(dirname "$0")/../.."
output=$(mktemp -d "${TMPDIR:-/tmp}/blackbox-today-resize.XXXXXX")
trap 'rm -rf "$output"' EXIT
.venv/bin/python -m viewer.Tests.PeriodFixture "$output"
# Exercise the production ZStack with Today initially visible, without changing
# Dashboard layout or adding a production-only testing API/state.
.venv/bin/python - "$output/ViewerDetail.swift" <<'PY'
from pathlib import Path
import sys
source = Path('viewer/App/ViewerDetail.swift').read_text()
needle = '@State private var showingToday = false'
assert source.count(needle) == 1
Path(sys.argv[1]).write_text(source.replace(needle, '@State private var showingToday = true'))
PY
xcrun swiftc -swift-version 5 -module-cache-path "$output/ModuleCache" -parse-as-library \
 viewer/Shared/*.swift viewer/App/StorageExplorerData.swift viewer/App/DatabaseReader.swift \
 viewer/App/TodayData.swift viewer/App/TodayReader.swift viewer/App/TodayView.swift \
 viewer/App/ViewerModel.swift "$output/ViewerDetail.swift" viewer/Tests/ResizeToday.swift \
 -o "$output/ResizeToday"
TZ=UTC "$output/ResizeToday" "$output" --full
TZ=UTC "$output/ResizeToday" "$output" --evidence
# This is NOT a substitute for mouse-driven live-resize acceptance.
