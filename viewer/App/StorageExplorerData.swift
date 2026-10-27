import Foundation

enum StorageLayer: String, CaseIterable, Sendable {
    case l3 = "L3", l2 = "L2"
    var component: Calendar.Component { self == .l2 ? .day : .hour }
    var title: String { self == .l2 ? "フォルダ使用量（全体）" : "フォルダ使用量（特定アプリ）" }
    var cadence: String { self == .l2 ? "日次観測" : "毎時観測" }
    var defaultDays: Int { self == .l2 ? 7 : 3 }
    var periodDays: [Int] { self == .l2 ? [7, 30] : [1, 3, 7, 30] }
}

struct StorageWindow: Equatable, Sendable {
    var days: Int
    var end: Date? = nil // nil follows the latest period; never persisted.
    func moving(_ direction: Int, now: Date, calendar: Calendar = .current) -> StorageWindow {
        let candidate = calendar.date(byAdding: .day, value: days * direction, to: end ?? now)!
        return StorageWindow(days: days, end: candidate >= now ? nil : candidate)
    }
}

enum StorageMode: String, CaseIterable { case single = "Single", multi = "Multi" }

struct StorageSelection {
    var mode: StorageMode
    var series: Set<String> = []
    var initialized = false
    var selectedDate: Date?
    var trail: [String] = []
    var expanded: String?
    var childQueries: [String: String] = [:]
    var childPages: [String: Int] = [:]
    init(layer: StorageLayer) { mode = layer == .l2 ? .multi : .single }
    mutating func initialize(_ data: StorageSnapshot) {
        guard !data.targets.isEmpty else { return }
        if initialized {
            let valid = Set(data.targets.map(\.id))
            series.formIntersection(valid)
            if let root = trail.first, !valid.contains(root) { trail = [data.targets[0].path] }
            if let id = expanded, !valid.contains(id) { expanded = nil }
            return
        }
        let day = data.days.last { $0.current?.completed == true } ?? data.days.last
        series = Set((day.map { data.ranking(on: $0) } ?? data.targets).prefix(3).map(\.id))
        trail = data.targets.first.map { [$0.path] } ?? []
        initialized = true
    }
    mutating func toggle(_ id: String) -> Bool {
        if series.contains(id) { series.remove(id); return true }
        guard series.count < 5 else { return false }
        series.insert(id); return true
    }
}

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
    var error: String? = nil
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
    var isReference: Bool = false
    static let referenceExplanation = "取得済み範囲の参考差分です。取得失敗領域は前後で一致しますが、未取得領域内部の増減は不明です。全体の確定差分ではありません。"
    var displayText: String { (isReference ? "参考 " : "") + StorageMath.signed(gib) }
    var explanation: String? { isReference ? Self.referenceExplanation : reason }
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
    // Only explicit targets and their recorded immediate children are capacity
    // observations. Deep failure diagnostics do not prove a recorded subtree.
    func sourceTarget(for path: String, in scan: StorageScan?) -> StorageTarget? {
        guard let scan else { return nil }
        return scan.targets.first { $0.path == path }
            ?? scan.targets.first { $0.includesChildren && (path as NSString).deletingLastPathComponent == $0.path }
    }
    func capacity(_ path: String) -> StorageResult? {
        guard let current, current.completed, sourceTarget(for: path, in: current) != nil,
              let row = current.results[path], ["ok", "partial"].contains(row.status),
              let bytes = row.bytes, bytes >= 0 else { return nil }
        return row
    }
    func comparison(_ path: String) -> StorageDelta {
        guard let target = sourceTarget(for: path, in: current) ?? sourceTarget(for: path, in: previous) else {
            return .missing("監視対象または記録済みの子項目ではありません")
        }
        return delta(path: path, parent: target)
    }
    func subfolders(_ path: String) -> [StorageResult] {
        guard let current, current.completed,
              current.targets.contains(where: { $0.path == path && $0.includesChildren }) else { return [] }
        return current.results.values.filter {
            ($0.path as NSString).deletingLastPathComponent == path
        }.sorted { ($0.bytes ?? -1) == ($1.bytes ?? -1) ? $0.path < $1.path : ($0.bytes ?? -1) > ($1.bytes ?? -1) }
    }
    var usageGiB: Double? {
        guard previous?.completed == true, current?.completed == true,
              let a = previousL1?.freeBytes, let b = currentL1?.freeBytes else { return nil }
        return Double(a - b) / 1_073_741_824
    }
    var usageReason: String? {
        if previous?.completed != true { return "比較元の有効な定期観測なし" }
        if current?.completed != true { return "比較先の有効な定期観測なし" }
        return usageGiB == nil ? "定期観測時刻の±90秒以内に有効なL1観測なし" : nil
    }
    var comparisonText: String {
        "比較元：\(StorageMath.stamp(previous?.completed == true ? previous?.time : nil))\n比較先：\(StorageMath.stamp(current?.completed == true ? current?.time : nil))"
    }
    func delta(path: String, parent: StorageTarget) -> StorageDelta {
        guard let current, current.completed else { return .missing("比較先の有効な定期観測なし") }
        guard let previous, previous.completed else { return .missing("比較元の有効な定期観測なし") }
        guard previous.targets.contains(where: { $0.path == parent.path }), current.targets.contains(where: { $0.path == parent.path }) else {
            return .missing("比較する観測で監視対象が異なります")
        }
        if path != parent.path {
            guard previous.targets.contains(where: { $0.path == parent.path && $0.includesChildren }),
                  current.targets.contains(where: { $0.path == parent.path && $0.includesChildren }) else { return .missing("比較元または比較先に子項目の記録なし") }
        }
        guard let a = previous.results[path] else { return .missing("比較元に記録なし") }
        guard let b = current.results[path] else { return .missing("比較先に記録なし") }
        guard a.kind == b.kind else { return .missing("項目の種類が変わったため比較不可") }
        guard let before = a.bytes, let after = b.bytes, before >= 0, after >= 0 else {
            return .missing("有効な容量の記録なし")
        }
        if a.usable && b.usable {
            return StorageDelta(gib: Double(after - before) / 1_073_741_824, reason: nil)
        }
        guard a.status == "partial", b.status == "partial",
              let oldFailures = failureRegions(previous, beneath: path),
              let newFailures = failureRegions(current, beneath: path), oldFailures == newFailures else {
            return .missing("取得失敗領域が異なるか、失敗記録が不十分なため比較不可")
        }
        return StorageDelta(gib: Double(after - before) / 1_073_741_824, reason: nil, isReference: true)
    }
    private struct FailureRegion: Equatable {
        let kind: String
        let status: String
        let error: String
    }
    private func failureRegions(_ scan: StorageScan, beneath path: String) -> [String: FailureRegion]? {
        let prefix = path == "/" ? "/" : path + "/"
        let rows = scan.results.values.filter { $0.path == path || $0.path.hasPrefix(prefix) }
        var failures: [String: FailureRegion] = [:]
        for row in rows where row.status != "ok" && row.status != "partial" {
            guard ["error", "missing", "excluded"].contains(row.status),
                  let error = row.error, !error.isEmpty else { return nil }
            failures[row.path] = FailureRegion(kind: row.kind, status: row.status, error: error)
        }
        // A partial row without a preserved failure descendant is insufficient history.
        guard !failures.isEmpty, rows.filter({ $0.status == "partial" }).allSatisfy({ row in
            failures.keys.contains { $0.hasPrefix(row.path == "/" ? "/" : row.path + "/") }
        }) else { return nil }
        return failures
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
    static func make(targets: [StorageTarget], scans: [StorageScan], l1: [L1Sample], now: Date,
                     calendar: Calendar = .current, layer: StorageLayer = .l2,
                     window: StorageWindow = StorageWindow(days: 7)) -> StorageSnapshot {
        let end = min(window.end ?? now, now)
        let component = layer.component
        let last = calendar.dateInterval(of: component, for: end)!.start
        let start = calendar.date(byAdding: .day, value: -window.days, to: last)!
        let bySlot = Dictionary(grouping: scans.filter { $0.time <= end }, by: {
            calendar.dateInterval(of: component, for: $0.time)!.start
        }).mapValues { $0.max { a, b in a.time == b.time ? a.id < b.id : a.time < b.time }! }
        var days: [StorageDay] = []
        var slot = calendar.date(byAdding: component, value: 1, to: start)!
        while slot <= last {
            let before = calendar.date(byAdding: component, value: -1, to: slot)!
            let a = bySlot[before], b = bySlot[slot]
            days.append(StorageDay(date: slot, previous: a, current: b,
                                   previousL1: a.flatMap { StorageMath.nearest(l1, to: $0.time) },
                                   currentL1: b.flatMap { StorageMath.nearest(l1, to: $0.time) }))
            slot = calendar.date(byAdding: component, value: 1, to: slot)!
        }
        return StorageSnapshot(targets: targets, days: days,
                               latest: days.compactMap(\.current).filter(\.completed).max { $0.time < $1.time })
    }

}
struct ViewerReading: Sendable {
    let l1: L1Snapshot
    let latestValid: L1Sample?
    let dayComparison: L1Comparison?
    let hourComparison: L1Comparison?
    let predecessor: L1Sample?
    let storage: StorageSnapshot
    var storageL3: StorageSnapshot = .empty
    var storageWindows: [StorageLayer: StorageWindow] = [:]
}

struct StoragePlotPoint: Identifiable {
    let date: Date
    let value: Double
    let partial: Bool
    let reference: Bool
    var id: Date { date }
}
enum StorageLineStyle { case normal, reference, incomparable }
struct StoragePlotSegment: Identifiable {
    let before: StoragePlotPoint
    let after: StoragePlotPoint
    let style: StorageLineStyle
    var id: Date { after.date }
}
enum StoragePlot {
    static func points(_ days: [StorageDay], path: String, mode: StorageMode) -> [StoragePlotPoint] {
        days.compactMap { day in
            let delta = day.comparison(path)
            if mode == .single {
                guard let row = day.capacity(path), let bytes = row.bytes else { return nil }
                return StoragePlotPoint(date: day.date, value: Double(bytes)/1_073_741_824,
                                        partial: row.status == "partial", reference: delta.isReference)
            }
            guard let value = delta.gib else { return nil }
            return StoragePlotPoint(date: day.date, value: value,
                                    partial: day.current?.results[path]?.status == "partial", reference: delta.isReference)
        }
    }
    static func segments(_ days: [StorageDay], path: String, mode: StorageMode) -> [StoragePlotSegment] {
        let points = points(days, path: path, mode: mode)
        let indices = Dictionary(uniqueKeysWithValues: days.enumerated().map { ($0.element.date, $0.offset) })
        return zip(points, points.dropFirst()).map { a, b in
            let index = indices[b.date]!
            let adjacent = index == indices[a.date]! + 1
            let delta = days[index].comparison(path)
            let style: StorageLineStyle
            if !adjacent || delta.gib == nil || (mode == .multi && a.reference != b.reference) { style = .incomparable }
            else { style = delta.isReference ? .reference : .normal }
            return StoragePlotSegment(before: a, after: b, style: style)
        }
    }
}
