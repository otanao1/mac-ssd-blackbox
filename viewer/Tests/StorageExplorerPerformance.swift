import AppKit
import SwiftUI
import SQLite3

// Diagnostic only: current GUI-review fixture, synthetic SQLite and hidden
// production section views. No production DB, bookmarks, cache or input events.
@main
struct StorageExplorerPerformance {
    static func clock() -> UInt64 { DispatchTime.now().uptimeNanoseconds }
    static func ms(_ start: UInt64) -> Double { Double(clock() - start) / 1_000_000 }
    @discardableResult static func measure<T>(_ name: String, _ work: () throws -> T) rethrows -> T {
        var values: [Double] = []
        var result: T!
        for _ in 0..<3 {
            let start = clock(); result = try work(); values.append(ms(start))
        }
        print(name + " ms=" + values.map { String(format: "%.2f", $0) }.joined(separator: ","))
        return result
    }
    @MainActor static func main() throws {
        _ = NSApplication.shared
        let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let reading = measure("review_fixture_all_generation") { StorageExplorerFixture.reading() }
        print("default slots L2=\(reading.storage.days.count) L3=\(reading.storageL3.days.count)")
        let model = ViewerModel(preview: reading)
        for layer in StorageLayer.allCases {
            let data = layer == .l2 ? reading.storage : reading.storageL3
            for mode in StorageMode.allCases {
                var state = StorageSelection(layer: layer)
                state.initialize(data); state.mode = mode
                let targets = mode == .single ? state.trail.last.map { [StorageTarget($0)] } ?? []
                    : data.targets.filter { state.series.contains($0.id) }.sorted { $0.path < $1.path }
                let count = measure("\(layer.rawValue)_\(mode.rawValue)_chart_input") {
                    targets.reduce(0) { count, target in
                        count + StoragePlot.segments(data.days, path: target.path, mode: mode).count
                            + StoragePlot.points(data.days, path: target.path, mode: mode).count
                    }
                }
                print("\(layer.rawValue)_\(mode.rawValue) points+segments=\(count)")
                if let day = data.days.last(where: { $0.current?.completed == true }) ?? data.days.last {
                    measure("\(layer.rawValue)_ranking_and_children") {
                        data.ranking(on: day).reduce(0) { $0 + day.children($1).count }
                    }
                }
                // Reconstruct a section using the same reading. This isolates
                // view/layout work, not an exact native click-to-present trace.
                for run in 1...3 {
                    let start = clock()
                    let content = StorageExplorerSection(model: model, layer: layer, back: {}, jumpTo: { _ in }, initialSelection: state)
                        .padding(16).frame(width: 760).background(Color(nsColor: .windowBackgroundColor))
                    let host = NSHostingView(rootView: content)
                    let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 1000),
                                          styleMask: [.borderless], backing: .buffered, defer: false)
                    window.contentView = host
                    host.frame = NSRect(x: 0, y: 0, width: 760, height: 1000)
                    host.layoutSubtreeIfNeeded()
                    let layout = ms(start)
                    let settleStart = clock()
                    RunLoop.main.run(until: Date().addingTimeInterval(0.03))
                    let settle = ms(settleStart)
                    let drawStart = clock()
                    guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { fatalError("No bitmap") }
                    host.cacheDisplay(in: host.bounds, to: bitmap)
                    let draw = ms(drawStart)
                    print(String(format: "%@_%@_view run=%d construct_layout=%.2f settle_wait=%.2f cache_draw=%.2f ms", layer.rawValue, mode.rawValue, run, layout, settle, draw))
                    window.contentView = nil
                }
            }
        }
        for run in 1...3 {
            let start = clock()
            let host = NSHostingView(rootView: ViewerDetail(model: model).background(Color(nsColor: .windowBackgroundColor)))
            let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 1000),
                                  styleMask: [.borderless], backing: .buffered, defer: false)
            window.contentView = host
            host.frame = NSRect(x: 0, y: 0, width: 760, height: 1000)
            host.layoutSubtreeIfNeeded()
            let layout = ms(start)
            let settleStart = clock()
            RunLoop.main.run(until: Date().addingTimeInterval(0.03))
            let settle = ms(settleStart)
            let drawStart = clock()
            guard let bitmap = host.bitmapImageRepForCachingDisplay(in: host.bounds) else { fatalError("No bitmap") }
            host.cacheDisplay(in: host.bounds, to: bitmap)
            print(String(format: "full_view run=%d construct_layout=%.2f settle_wait=%.2f cache_draw=%.2f ms", run, layout, settle, ms(drawStart)))
            window.contentView = nil
        }
        let url = folder.appendingPathComponent("performance-synthetic-\(UUID().uuidString).sqlite3")
        defer { try? FileManager.default.removeItem(at: url) }
        try prepareDatabase(url, reading: reading)
        _ = try measure("synthetic_sqlite_default_read") {
            try DatabaseReader.withReadOnlyConnection(url) { db in
                try DatabaseReader.readConnected(db, now: reading.l1.generatedAt)
            }
        }
    }

    static func prepareDatabase(_ url: URL, reading: ViewerReading) throws {
        var db: OpaquePointer?
        precondition(sqlite3_open(url.path, &db) == SQLITE_OK)
        defer { sqlite3_close(db) }
        precondition(sqlite3_exec(db, "CREATE TABLE l1(time REAL PRIMARY KEY,path TEXT,free INTEGER,error TEXT); CREATE TABLE scans(id TEXT PRIMARY KEY,layer TEXT,trigger TEXT,created REAL,started REAL,state TEXT,scan_version INTEGER,targets TEXT); CREATE TABLE results(scan_id TEXT,path TEXT,kind TEXT,bytes INTEGER,status TEXT,error TEXT); CREATE INDEX result_scan ON results(scan_id); BEGIN", nil, nil, nil) == SQLITE_OK)
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        func insert(_ sql: String, _ values: [Any?]) {
            var statement: OpaquePointer?
            precondition(sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK)
            defer { sqlite3_finalize(statement) }
            for (offset, value) in values.enumerated() {
                let index = Int32(offset+1)
                switch value {
                case let v as String: _ = v.withCString { sqlite3_bind_text(statement, index, $0, -1, transient) }
                case let v as Int64: sqlite3_bind_int64(statement, index, v)
                case let v as Double: sqlite3_bind_double(statement, index, v)
                default: sqlite3_bind_null(statement, index)
                }
            }
            precondition(sqlite3_step(statement) == SQLITE_DONE)
        }
        var samples = reading.l1.samples
        for (layer, data) in [(StorageLayer.l2, reading.storage), (.l3, reading.storageL3)] {
            var seen = Set<String>()
            for day in data.days {
                samples += [day.previousL1,day.currentL1].compactMap { $0 }
                for scan in [day.previous,day.current].compactMap({ $0 }) where seen.insert(scan.id).inserted {
                    let targets = try String(data: JSONEncoder().encode(scan.targets.map { $0.path + ($0.includesChildren ? "/*" : "") }), encoding: .utf8)!
                    insert("INSERT INTO scans VALUES(?,?,?,?,?,?,?,?)", [scan.id,layer.rawValue,"periodic",scan.time.timeIntervalSince1970,scan.time.timeIntervalSince1970,scan.state,Int64(scan.version),targets])
                    for row in scan.results.values {
                        insert("INSERT INTO results VALUES(?,?,?,?,?,?)", [scan.id,row.path,row.kind,row.bytes,row.status,row.error])
                    }
                }
            }
        }
        for sample in samples {
            insert("INSERT OR IGNORE INTO l1 VALUES(?,?,?,?)", [sample.time.timeIntervalSince1970,"/fixture/disk",sample.freeBytes,nil])
        }
        precondition(sqlite3_exec(db, "COMMIT", nil, nil, nil) == SQLITE_OK)
    }
}
