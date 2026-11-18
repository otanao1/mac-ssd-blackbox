import AppKit
import SwiftUI

// Real NSWindow/NSHostingView with a synthetic, read-only preview model. No
// production app delegate, bookmark, App Group, Widget cache or monitor access.
// setFrame exercises window/layout changes, NOT mouse-driven live-resize events.
@main
struct ResizeToday {
    @MainActor static func main() throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.accessory)
        let directory = URL(fileURLWithPath: CommandLine.arguments[1])
        let url = directory.appendingPathComponent("period-data/blackbox.sqlite3")
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = .init(secondsFromGMT: 0)!
        let day = calendar.date(from: DateComponents(year: 2026, month: 10, day: 28))!
        let now = day.addingTimeInterval(7200)
        let original = try Data(contentsOf: url)
        let loader: (Date, Date) -> TodaySnapshot = { selected, _ in
            try! DatabaseReader.withReadOnlyConnection(url) { db in
                try DatabaseReader.readTodayConnected(db, day: selected, now: now, calendar: calendar)
            }
        }
        let l1 = L1Snapshot(version: 1, generatedAt: now, samples: [], latest: nil, status: .ready)
        let reading = ViewerReading(l1: l1, latestValid: nil, dayComparison: nil, hourComparison: nil, predecessor: nil, storage: .empty)
        let model = ViewerModel(preview: reading, previewToday: loader)
        let evidenceOnly = CommandLine.arguments.contains("--evidence")
        let incident = model.todayReading!.incidents[0]
        let root = CommandLine.arguments.contains("--full") ? AnyView(ViewerDetail(model: model)) : evidenceOnly ? AnyView(ScrollView {
            VStack(alignment: .leading) {
                ForEach(Array(incident.periodEvaluations.enumerated()), id: \.offset) { _, item in PeriodEvidenceView(item: item) }
            }.padding()
        }) : AnyView(TodayView(model: model))
        let host = NSHostingView(rootView: root)
        let window = NSWindow(contentRect: NSRect(x: 50, y: 50, width: 760, height: 440), styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
        host.sizingOptions = []
        window.title = "Issue 39 · Synthetic resize check"
        window.contentMinSize = NSSize(width: 600, height: 380)
        window.contentView = host
        window.makeKeyAndOrderFront(nil)
        // Bound a stuck main thread without killing any other application.
        DispatchQueue.global().asyncAfter(deadline: .now() + 45) {
            fputs("FAIL: resize process exceeded 45 second watchdog\n", stderr)
            _exit(2)
        }
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))
        let start = Date()
        var worst = 0.0
        var sizes: Set<String> = []
        for index in 0..<80 {
            let sample = Date()
            let width = index % 4 < 2 ? 600.0 : 1000.0
            let height = index % 2 == 0 ? 440.0 : 850.0
            window.setFrame(NSRect(x: 50, y: 50, width: width, height: height), display: true)
            host.layoutSubtreeIfNeeded()
            RunLoop.main.run(until: Date().addingTimeInterval(0.02))
            precondition(window.frame.width >= 600 && window.frame.height >= 380)
            sizes.insert("\(window.frame.width)x\(window.frame.height)")
            worst = max(worst, Date().timeIntervalSince(sample))
        }
        precondition(sizes.count >= 4, "Window sizes did not change as requested")
        let after = try Data(contentsOf: url)
        precondition(after == original)
        print(String(format: "PASS: %@, 80 NSWindow size changes, %.3fs total, %.3fs slowest, synthetic DB unchanged; mouse live resize NOT tested", evidenceOnly ? "expanded period evidence" : "Today screen", Date().timeIntervalSince(start), worst))
        window.orderOut(nil)
        window.contentView = nil
    }
}
