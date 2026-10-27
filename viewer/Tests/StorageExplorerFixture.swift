import Foundation

// Synthetic only. Never reads configuration, the monitor DB, or user paths.
enum StorageExplorerFixture {
    static func reading(windows: [StorageLayer: StorageWindow] = [:]) -> ViewerReading {
        let calendar = Calendar.current
        let now = calendar.date(from: DateComponents(year: 2026, month: 4, day: 21, hour: 18))!
        let gib: Double = 1_073_741_824
        let paths = ["/Users/example", "/Users/example/Library/*", "/Users/example/Library/Application Support/*", "/Applications/*", "/opt/homebrew/Cellar/*", "/private/tmp/*", "/Library/*", "/opt/homebrew/*"]
        let targets = paths.map(StorageTarget.init)
        func free(_ date: Date) -> Int64 {
            Int64((44 - date.timeIntervalSince(now)/86400*2.1 + sin(date.timeIntervalSince(now)/10800)*0.4)*gib)
        }
        let samples = (-1500...0).map { minute -> L1Sample in
            let date = now.addingTimeInterval(Double(minute)*60)
            return L1Sample(time: date, freeBytes: (-600 ... -590).contains(minute) ? nil : free(date))
        }
        var scans: [StorageScan] = []
        var anchors: [L1Sample] = []
        let today = calendar.startOfDay(for: now)
        for offset in -35...0 where offset != -3 {
            let time = calendar.date(byAdding: .day, value: offset, to: today)!.addingTimeInterval(15*3600)
            var results: [String: StorageResult] = [:]
            for (index,target) in targets.enumerated() {
                let bytes = Int64((25 + Double(index)*2 + Double(offset+35)*Double(8-index)*0.35)*gib)
                results[target.path] = StorageResult(path: target.path, kind: "directory", bytes: bytes,
                                                      status: index == 0 || index == 7 ? "partial" : "ok")
                if index == 0 || index == 7 {
                    let blocked = target.path + "/deep/blocked"
                    results[blocked] = StorageResult(path: blocked, kind: "unknown", bytes: nil, status: "error", error: "synthetic permission denied")
                }
                if target.includesChildren {
                    for child in 1...26 {
                        let path = target.path + String(format: "/Sample %03d", child)
                        results[path] = StorageResult(path: path, kind: "directory", bytes: Int64((0.5 + Double(child)/10 + Double(offset+35)*Double(child%5)/20)*gib), status: child%7 == 0 ? "partial" : "ok")
                        if child%7 == 0 {
                            let blocked = path + "/deep/blocked"
                            results[blocked] = StorageResult(path: blocked, kind: "unknown", bytes: nil, status: "error", error: "synthetic permission denied")
                        }
                    }
                }
            }
            scans.append(StorageScan(id: "synthetic-\(offset)", time: time, state: "partial", version: 2, targets: targets, results: results))
            anchors.append(L1Sample(time: time.addingTimeInterval(20), freeBytes: free(time.addingTimeInterval(20))))
        }
        let l3Targets = targets.filter(\.includesChildren)
        let base = scans.last!
        let hourly = (-750...0).compactMap { hour -> StorageScan? in
            guard hour % 17 != 0 else { return nil }
            let time = now.addingTimeInterval(Double(hour)*3600 - 600)
            var rows = base.results
            for path in rows.keys {
                let row = rows[path]!
                guard let bytes = row.bytes else { continue }
                let partial = row.status == "partial" || (hour % 9 < -3 && path == l3Targets[0].path)
                rows[path] = StorageResult(path: path, kind: row.kind, bytes: bytes + Int64(sin(Double(hour)/4 + Double(path.utf8.reduce(0) { $0 + Int($1) } % 19)/5)*gib), status: partial ? "partial" : "ok")
                if partial {
                    let blocked = path + "/deep/blocked"
                    rows[blocked] = StorageResult(path: blocked, kind: "unknown", bytes: nil, status: "error", error: hour % 13 == 0 ? "synthetic changed failure" : "synthetic permission denied")
                }
            }
            return StorageScan(id: "hourly-\(hour)", time: time, state: "partial", version: 2, targets: l3Targets, results: rows)
        }
        let latest = samples.last!
        return ViewerReading(l1: L1Snapshot(version: 1, generatedAt: now, samples: samples.filter { $0.time >= now.addingTimeInterval(-86400) }, latest: latest, status: .ready),
                             latestValid: latest, dayComparison: StorageMath.comparison(latest: latest, hours: 24, samples: samples),
                             hourComparison: StorageMath.comparison(latest: latest, hours: 1, samples: samples),
                             predecessor: samples.last { $0.time < now.addingTimeInterval(-86400) },
                             storage: StorageSnapshot.make(targets: targets, scans: scans, l1: anchors, now: now, window: windows[.l2] ?? StorageWindow(days: 7)),
                             storageL3: StorageSnapshot.make(targets: l3Targets, scans: hourly, l1: anchors, now: now, layer: .l3, window: windows[.l3] ?? StorageWindow(days: 3)),
                             storageWindows: [.l2: windows[.l2] ?? StorageWindow(days: 7), .l3: windows[.l3] ?? StorageWindow(days: 3)])
    }
}
