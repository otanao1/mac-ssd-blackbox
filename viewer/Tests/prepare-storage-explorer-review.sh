#!/bin/sh
# Synthetic GUI only. Does not launch/register/replace the installed app.
set -eu
cd "$(dirname "$0")/../.."
output=local/issue31/GUIReview
app="$output/StorageExplorerReview.app"
mkdir -p "$app/Contents/MacOS"
xcrun swiftc -swift-version 5 -module-cache-path local/issue31/ModuleCache -parse-as-library \
  viewer/Shared/*.swift viewer/App/StorageExplorerData.swift viewer/App/DatabaseReader.swift \
  viewer/App/ViewerModel.swift viewer/App/ViewerDetail.swift \
  viewer/Tests/StorageExplorerFixture.swift viewer/Tests/StorageExplorerGUIReview.swift \
  -o "$app/Contents/MacOS/StorageExplorerReview"
cat > "$app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>io.github.otanao1.blackbox-viewer.issue31-review</string>
<key>CFBundleName</key><string>StorageExplorerReview</string>
<key>CFBundleDisplayName</key><string>Storage Explorer Review (Synthetic)</string>
<key>CFBundleExecutable</key><string>StorageExplorerReview</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
printf 'Prepared synthetic review app: %s\n' "$app"
