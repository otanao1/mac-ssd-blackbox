#!/bin/sh
# Production views with synthetic DB/preview model only. Never launches the app.
set -eu
cd "$(dirname "$0")/../.."
output=local/issue34
mkdir -p "$output"
.venv/bin/python -m viewer.Tests.TodayFixture "$output"
xcrun swiftc -swift-version 5 -module-cache-path "$output/ModuleCache" -parse-as-library \
 viewer/Shared/*.swift viewer/App/StorageExplorerData.swift viewer/App/DatabaseReader.swift \
 viewer/App/TodayData.swift viewer/App/TodayReader.swift viewer/App/TodayView.swift \
 viewer/App/ViewerModel.swift viewer/App/ViewerDetail.swift viewer/Tests/RenderToday.swift \
 -o "$output/RenderToday"
TZ=UTC "$output/RenderToday" "$output"
