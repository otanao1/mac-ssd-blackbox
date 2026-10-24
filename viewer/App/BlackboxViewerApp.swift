import AppKit
import SwiftUI
import WidgetKit

final class ViewerAppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationWillTerminate(_ notification: Notification) {
        let previous = SharedSnapshot.read()
        let stopped = L1Snapshot(version: 1, generatedAt: Date(), samples: previous.samples,
                                 latest: previous.latest, status: .stopped)
        try? SharedSnapshot.write(stopped)
        WidgetCenter.shared.reloadTimelines(ofKind: SharedSnapshot.widgetKind)
    }
}

@main
struct BlackboxViewerApp: App {
    @NSApplicationDelegateAdaptor(ViewerAppDelegate.self) private var delegate
    @StateObject private var model = ViewerModel()

    var body: some Scene {
        WindowGroup("Blackbox Viewer", id: "l1", for: String.self) { _ in
            ViewerDetail(model: model)
                .handlesExternalEvents(preferring: ["blackbox-viewer://l1"], allowing: ["blackbox-viewer://l1"])
                .onOpenURL { url in
                    if url.scheme == "blackbox-viewer", url.host == "l1" {
                        NSApp.activate(ignoringOtherApps: true)
                    }
                }
        } defaultValue: { "l1" }
        .defaultSize(width: 760, height: 440)
        .handlesExternalEvents(matching: ["blackbox-viewer://l1"])
        .commands { CommandGroup(replacing: .newItem) {} }

        MenuBarExtra {
            ViewerMenu(model: model)
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "internaldrive")
                Text(model.snapshot.capacityText(at: model.now))
                    .font(.system(size: 13, weight: .regular)).monospacedDigit()
            }
        }
        .menuBarExtraStyle(.menu)
    }
}

private struct ViewerMenu: View {
    @ObservedObject var model: ViewerModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("Viewerを開く") {
            openWindow(id: "l1", value: "l1")
            NSApp.activate(ignoringOtherApps: true)
        }
        Button("設定…") {
            openWindow(id: "l1", value: "l1")
            NSApp.activate(ignoringOtherApps: true)
            model.selectSource()
        }.disabled(model.refreshing)
        Button("終了") { NSApp.terminate(nil) }
    }
}
