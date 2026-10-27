import AppKit
import SwiftUI

// Production views, synthetic records only. No App delegate, bookmarks, timers,
// shared cache writes, monitor CLI, or installed Viewer/Widget processes.
@main
struct RenderStorageExplorer {
    @MainActor static func main() async throws {
        _ = NSApplication.shared
        let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let reading = StorageExplorerFixture.reading()
        let model = ViewerModel(preview: reading, previewLoader: { StorageExplorerFixture.reading(windows: $0) })
        for layer in StorageLayer.allCases {
            let data = layer == .l2 ? reading.storage : reading.storageL3
            for mode in StorageMode.allCases {
                for metric in StorageMetric.allCases {
                    var state = StorageSelection(layer: layer)
                    state.initialize(data); state.mode = mode; state.metric = metric
                    state.series = Set(data.targets.prefix(5).map(\.id))
                    state.expanded = data.targets.first(where: \.includesChildren)?.path
                    let content = StorageExplorerSection(model: model, layer: layer, back: {}, jumpTo: { _ in }, initialSelection: state)
                        .padding(16).frame(width: 600).background(Color(nsColor: .windowBackgroundColor))
                    try render(content, to: folder.appendingPathComponent("\(layer.rawValue)-\(mode.rawValue)-\(metric == .capacity ? "Capacity" : "Delta")-600.png"), width: 600, height: mode == .single ? 940 : 870)
                }
            }
        }
        try render(StorageDashboard(model: model, jumpTo: { _ in }).padding(16).background(Color(nsColor: .windowBackgroundColor)), to: folder.appendingPathComponent("Dashboard-600.png"), width: 600, height: 140)
        // Exercise the same preview model's period requests and reset without
        // reading a real database or publishing to a Widget cache.
        model.setWindow(StorageWindow(days: 30, end: reading.l1.generatedAt.addingTimeInterval(-40*86400)), for: .l3)
        await model.refresh()
        precondition(model.reading!.storageL3.days.allSatisfy { $0.current == nil })
        model.resetStorage(); await model.refresh()
        precondition(model.storageWindows[.l3] == StorageWindow(days: 3))
        precondition(model.storageWindows[.l2] == StorageWindow(days: 7))
        model.setWindow(StorageWindow(days: 1), for: .l2)
        precondition(model.storageWindows[.l2] == StorageWindow(days: 7))
        model.setWindow(StorageWindow(days: 3), for: .l2)
        precondition(model.storageWindows[.l2] == StorageWindow(days: 7))
        model.selectSource()
        precondition(model.configurationMessage == "合成データのレビュー版では保存先設定を変更しません。")
        print("PASS: synthetic production view rendering, 600pt × Single/Multi × capacity/delta × L2/L3; history outside raw retention; model reset")
    }

    @MainActor static func render<V: View>(_ content: V, to url: URL, width: CGFloat, height: CGFloat) throws {
        let host = NSHostingView(rootView: content)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                              styleMask: [.borderless], backing: .buffered, defer: false)
        window.contentView = host
        host.frame = NSRect(x: 0, y: 0, width: width, height: height)
        host.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.4))
        guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { fatalError("No bitmap") }
        host.cacheDisplay(in: host.bounds, to: bitmap)
        guard let png = bitmap.representation(using: .png, properties: [:]) else { fatalError("No PNG") }
        try png.write(to: url)
        // Keep the hidden window alive until caching finishes, without a launch
        // services registration or any installed app's lifecycle hooks.
        window.contentView = nil
    }
}
