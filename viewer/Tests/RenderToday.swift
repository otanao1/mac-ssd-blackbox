import AppKit
import SwiftUI

// Synthetic production views only: no app delegate, timer, bookmark, cache,
// production application launch, registration, or monitor access.
@main
struct RenderToday {
    @MainActor static func main() async throws {
        _ = NSApplication.shared
        let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let url = folder.appendingPathComponent("today-fixture.sqlite3")
        // Use UTC only for the fixture loader; production uses the local Calendar.
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = .init(secondsFromGMT: 0)!
        let day = calendar.date(from: DateComponents(year: 2026, month: 10, day: 28))!
        let fixedNow = day.addingTimeInterval(7200)
        let loader: (Date, Date) -> TodaySnapshot = { selected, _ in
            try! DatabaseReader.withReadOnlyConnection(url) { db in
                try DatabaseReader.readTodayConnected(db, day: selected, now: fixedNow, calendar: calendar)
            }
        }
        let l1 = L1Snapshot(version: 1, generatedAt: fixedNow, samples: [], latest: nil, status: .ready)
        let reading = ViewerReading(l1: l1, latestValid: nil, dayComparison: nil, hourComparison: nil, predecessor: nil, storage: .empty)
        let model = ViewerModel(preview: reading, previewToday: loader)
        precondition(model.todayReading?.incidents.count == 9)
        for width in [600.0, 1000.0] {
            try render(TodayView(model: model).environment(\.colorScheme, .light), to: folder.appendingPathComponent("Today-Incidents-\(Int(width)).png"), width: width, height: 3000)
            try render(TodayView(model: model, initialTab: 1).environment(\.colorScheme, .light), to: folder.appendingPathComponent("Today-Scans-\(Int(width)).png"), width: width, height: 700)
        }
        let incident = model.todayReading!.incidents.first { $0.id == "D" }!
        let scan = model.todayReading!.related["shared"]!
        try render(TodayScanDisclosure(model: model, scan: scan, evidence: incident.evidence(for: scan.id), initiallyExpanded: true).padding(16), to: folder.appendingPathComponent("Today-Expanded-600.png"), width: 600, height: 530)
        try render(TodayView(model: model).environment(\.colorScheme, .dark), to: folder.appendingPathComponent("Today-Dark-600.png"), width: 600, height: 950, dark: true)
        model.moveToday(by: -1); await model.refreshToday()
        precondition(model.todayReading?.incidents.count == 1)
        model.moveToday(by: -1); await model.refreshToday()
        precondition(model.todayReading?.incidents.isEmpty == true)
        try render(TodayView(model: model).environment(\.colorScheme, .light), to: folder.appendingPathComponent("Today-Empty-600.png"), width: 600, height: 450)
        model.moveToday(by: 5); await model.refreshToday()
        precondition(model.todayDate == nil && model.todayReading?.incidents.count == 9)
        print("PASS: synthetic production Today views 600/1000pt, incident/scan tabs, expanded D evidence, empty day, model calendar navigation/future guard")
    }
    @MainActor static func render<V: View>(_ content: V, to url: URL, width: CGFloat, height: CGFloat, dark: Bool = false) throws {
        let host = NSHostingView(rootView: content.environment(\.colorScheme, dark ? .dark : .light).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading).background(Color(nsColor: .windowBackgroundColor)))
        host.sizingOptions = []
        host.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height), styleMask: [.borderless], backing: .buffered, defer: false)
        window.appearance = host.appearance
        window.contentView = host; host.frame = NSRect(x: 0, y: 0, width: width, height: height)
        host.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))
        guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { fatalError("No bitmap") }
        host.cacheDisplay(in: host.bounds, to: bitmap)
        try bitmap.representation(using: .png, properties: [:])!.write(to: url)
        window.contentView = nil
    }
}
