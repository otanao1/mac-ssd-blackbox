import Foundation

struct L1Comparison: Sendable {
    let latest: L1Sample
    let previous: L1Sample
    var usageGiB: Double { Double(previous.freeBytes! - latest.freeBytes!) / 1_073_741_824 }
}

enum StorageMath {
    static let tolerance: TimeInterval = 90
    // Caller supplies only one monitored path. Prefer the earlier sample on a tie.
    static func nearest(_ samples: [L1Sample], to time: Date) -> L1Sample? {
        samples.filter { $0.freeBytes != nil && $0.freeBytes! >= 0 && abs($0.time.timeIntervalSince(time)) <= tolerance }
            .min { a, b in
                let x = abs(a.time.timeIntervalSince(time)), y = abs(b.time.timeIntervalSince(time))
                return x == y ? a.time < b.time : x < y
            }
    }
    static func comparison(latest: L1Sample?, hours: Int, samples: [L1Sample]) -> L1Comparison? {
        guard let latest, latest.freeBytes != nil, latest.freeBytes! >= 0,
              let previous = nearest(samples, to: latest.time.addingTimeInterval(Double(-hours) * 3600)) else { return nil }
        return L1Comparison(latest: latest, previous: previous)
    }
    static func signed(_ value: Double?) -> String {
        guard let value else { return "—" }
        return String(format: "%+.2f GiB", abs(value) < 0.005 ? 0 : value)
    }
    static func stamp(_ date: Date?) -> String {
        guard let date else { return "データなし" }
        return "\(L1DisplayFormat.monthDay(date)) \(L1DisplayFormat.hourMinute(date))"
    }
}

struct StorageTarget: Identifiable, Hashable, Sendable {
    let path: String
    let includesChildren: Bool
    var id: String { path }
    init(_ specification: String) {
        includesChildren = specification.hasSuffix("/*")
        path = includesChildren ? String(specification.dropLast(2)) : specification
    }
    var name: String {
        let names = ["/Applications": "アプリケーション", "/Library": "システムライブラリ",
                     "/Library/Application Support": "システムアプリサポート", "/Users": "ユーザー",
                     "/opt": "追加ソフトウェア", "/opt/homebrew": "Homebrew", "/opt/homebrew/Cellar": "Homebrewパッケージ",
                     "/usr": "Unixツール", "/usr/local": "ローカルツール", "/private/tmp": "一時ファイル",
                     "/private/var": "システム作業領域", "/System/Volumes/VM": "仮想メモリ", "/cores": "コアダンプ"]
        if let name = names[path] { return name }
        let components = path.split(separator: "/")
        if components.count >= 2, components[0] == "Users" {
            let tail = components.dropFirst(2).joined(separator: "/")
            let userNames = ["": "ホーム", "Library": "ユーザーライブラリ", "Library/Application Support": "アプリケーションサポート（ユーザー）",
                             "Library/CloudStorage": "クラウドストレージ", "Library/Developer/Xcode/DerivedData": "開発ビルドキャッシュ",
                             "Library/Caches/Homebrew": "Homebrewキャッシュ"]
            if let name = userNames[tail] { return name }
        }
        return (path as NSString).lastPathComponent
    }
}

struct StorageResult: Sendable {
    let path: String
    let kind: String
    let bytes: Int64?
    let status: String
    var usable: Bool { status == "ok" && bytes != nil && bytes! >= 0 }
}
struct StorageScan: Sendable {
    let id: String
    let time: Date
    let state: String
    let version: Int
    let targets: [StorageTarget]
    var results: [String: StorageResult]
    var completed: Bool { version == 2 && (state == "succeeded" || state == "partial") }
}
struct StorageDelta: Sendable {
    let gib: Double?
    let reason: String?
    static func missing(_ reason: String) -> StorageDelta { StorageDelta(gib: nil, reason: reason) }
}
struct StorageChild: Identifiable, Sendable {
    let path: String
    let delta: StorageDelta
    var id: String { path }
    var name: String { (path as NSString).lastPathComponent }
}
struct StorageDay: Identifiable, Sendable {
    let date: Date
    let previous: StorageScan?
    let current: StorageScan?
    let previousL1: L1Sample?
    let currentL1: L1Sample?
    var id: Date { date }
    var usageGiB: Double? {
        guard previous?.completed == true, current?.completed == true,
              let a = previousL1?.freeBytes, let b = currentL1?.freeBytes else { return nil }
        return Double(a - b) / 1_073_741_824
    }
    var usageReason: String? {
        if previous?.completed != true { return "前日の有効な定期観測なし" }
        if current?.completed != true { return "この日の有効な定期観測なし" }
        return usageGiB == nil ? "L2観測時刻の±90秒以内に有効なL1観測なし" : nil
    }
    var comparisonText: String {
        "比較元：\(StorageMath.stamp(previous?.completed == true ? previous?.time : nil))\n比較先：\(StorageMath.stamp(current?.completed == true ? current?.time : nil))"
    }
    func delta(path: String, parent: StorageTarget) -> StorageDelta {
        guard let current, current.completed else { return .missing("この日の有効な定期観測なし") }
        guard let previous, previous.completed else { return .missing("前日の有効な定期観測なし") }
        guard previous.targets.contains(where: { $0.path == parent.path }), current.targets.contains(where: { $0.path == parent.path }) else {
            return .missing("比較する観測で監視対象が異なります")
        }
        if path != parent.path {
            guard previous.targets.contains(where: { $0.path == parent.path && $0.includesChildren }),
                  current.targets.contains(where: { $0.path == parent.path && $0.includesChildren }) else { return .missing("比較元または比較先に子項目の記録なし") }
        }
        guard let a = previous.results[path] else { return .missing("比較元に記録なし") }
        guard let b = current.results[path] else { return .missing("比較先に記録なし") }
        guard a.usable, b.usable else { return .missing("一部取得・取得失敗・対象外のため比較不可") }
        guard a.kind == b.kind else { return .missing("項目の種類が変わったため比較不可") }
        return StorageDelta(gib: Double(b.bytes! - a.bytes!) / 1_073_741_824, reason: nil)
    }
    func children(_ parent: StorageTarget) -> [StorageChild] {
        guard parent.includesChildren else { return [] }
        // Union of the two observations: an absent child is unknown, never zero/deleted.
        let paths = Set(Array(current?.results.keys ?? Dictionary<String, StorageResult>().keys) + Array(previous?.results.keys ?? Dictionary<String, StorageResult>().keys))
        return paths.filter { ($0 as NSString).deletingLastPathComponent == parent.path }
            .map { StorageChild(path: $0, delta: delta(path: $0, parent: parent)) }
            .sorted { a, b in
                if a.delta.gib == b.delta.gib { return a.path < b.path }
                return (a.delta.gib ?? -.infinity) > (b.delta.gib ?? -.infinity)
            }
    }
}
struct StorageSnapshot: Sendable {
    let targets: [StorageTarget]
    let days: [StorageDay]
    let latest: StorageScan?
    static let empty = StorageSnapshot(targets: [], days: [], latest: nil)
    func ranking(on day: StorageDay) -> [StorageTarget] {
        targets.sorted { a, b in
            let x = day.delta(path: a.path, parent: a).gib, y = day.delta(path: b.path, parent: b).gib
            return x == y ? a.path < b.path : (x ?? -.infinity) > (y ?? -.infinity)
        }
    }
    static func make(targets: [StorageTarget], scans: [StorageScan], l1: [L1Sample], now: Date, calendar: Calendar = .current) -> StorageSnapshot {
        let today = calendar.startOfDay(for: now)
        let byDay = Dictionary(grouping: scans.filter { $0.time <= now }, by: { calendar.startOfDay(for: $0.time) })
            .mapValues { $0.max { a, b in a.time == b.time ? a.id < b.id : a.time < b.time }! }
        let days = (-6...0).map { offset -> StorageDay in
            let date = calendar.date(byAdding: .day, value: offset, to: today)!
            let before = calendar.date(byAdding: .day, value: -1, to: date)!
            let a = byDay[before], b = byDay[date]
            return StorageDay(date: date, previous: a, current: b,
                              previousL1: a.flatMap { StorageMath.nearest(l1, to: $0.time) },
                              currentL1: b.flatMap { StorageMath.nearest(l1, to: $0.time) })
        }
        return StorageSnapshot(targets: targets, days: days, latest: scans.filter(\.completed).max { $0.time < $1.time })
    }
}
struct ViewerReading: Sendable {
    let l1: L1Snapshot
    let latestValid: L1Sample?
    let dayComparison: L1Comparison?
    let hourComparison: L1Comparison?
    let predecessor: L1Sample?
    let storage: StorageSnapshot
}
