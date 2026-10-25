import Foundation

struct L1Sample: Codable, Identifiable, Sendable {
    let time: Date
    let freeBytes: Int64?
    var id: Date { time }
}

struct L1Snapshot: Codable, Sendable {
    enum Status: String, Codable, Sendable { case ready, unconfigured, unavailable, stopped }
    let version: Int
    let generatedAt: Date
    let samples: [L1Sample]
    let latest: L1Sample?
    let status: Status

    static let empty = L1Snapshot(version: 1, generatedAt: .distantPast,
                                 samples: [], latest: nil, status: .unconfigured)

    func isCurrent(at date: Date) -> Bool {
        guard status == .ready, let latest, latest.freeBytes != nil else { return false }
        return (0...180).contains(date.timeIntervalSince(latest.time))
    }

    func capacityNumber(at date: Date, previous: Bool = false) -> String {
        guard let bytes = latest?.freeBytes, isCurrent(at: date) || previous else { return "—" }
        return String(format: "%.2f", Double(bytes) / 1_073_741_824)
    }

    func capacityText(at date: Date, previous: Bool = false) -> String {
        let number = capacityNumber(at: date, previous: previous)
        return number == "—" ? number : number + " GiB"
    }

    func widgetStatusText(at date: Date) -> String? {
        if isCurrent(at: date) { return nil }
        if status == .ready, latest?.freeBytes != nil {
            // A timeline can be older than the healthy shared cache. Do not
            // diagnose monitor / database failure from an old rendered entry.
            return "表示更新待ち・前回の観測値"
        }
        return statusText(at: date)
    }

    func statusText(at date: Date) -> String {
        switch status {
        case .unconfigured: return "Viewerで保存先を設定してください"
        case .unavailable: return "取得できません。保存先・接続を確認してください"
        case .stopped: return "Viewerは終了しています（前回の観測値）"
        case .ready:
            return isCurrent(at: date) ? "L1空き容量" : "最新の観測値を取得できていません"
        }
    }
}

// Shared by rendering and the standalone logic checks. Gaps are never interpolated.
struct L1PlotPoint: Identifiable {
    let time: Date
    let gib: Double
    let segment: Int
    var id: Date { time }
}

enum L1PlotData {
    // L1 is sampled every 60s. Allow 30s of scheduling jitter, but do not
    // silently bridge an absent observation with a two-minute line.
    static let maximumConnectedInterval: TimeInterval = 90

    static func points(_ samples: [L1Sample], now: Date) -> [L1PlotPoint] {
        var result: [L1PlotPoint] = []
        var segment = 0
        var previous: Date?
        for sample in samples where sample.time >= now.addingTimeInterval(-86_400) && sample.time <= now {
            guard let bytes = sample.freeBytes else { segment += 1; previous = nil; continue }
            if let previous, sample.time.timeIntervalSince(previous) > maximumConnectedInterval { segment += 1 }
            result.append(L1PlotPoint(time: sample.time, gib: Double(bytes) / 1_073_741_824, segment: segment))
            previous = sample.time
        }
        return result
    }

    static func changes(_ samples: [L1Sample], now: Date) -> [L1PlotPoint] {
        let ordered = samples.sorted { $0.time < $1.time }
        guard ordered.count > 1 else { return [] }
        return zip(ordered, ordered.dropFirst()).compactMap { previous, current in
            guard current.time >= now.addingTimeInterval(-86400), current.time <= now,
                  let a = previous.freeBytes, let b = current.freeBytes,
                  a >= 0, b >= 0, current.time > previous.time,
                  current.time.timeIntervalSince(previous.time) <= maximumConnectedInterval else { return nil }
            return L1PlotPoint(time: current.time, gib: Double(b-a)/1_073_741_824, segment: 0)
        }
    }

    static func axisDates(now: Date, calendar: Calendar = .current) -> [Date] {
        let start = now.addingTimeInterval(-86_400)
        var cursor = calendar.startOfDay(for: start)
        var dates: [Date] = []
        while cursor <= now {
            if cursor >= start, calendar.component(.hour, from: cursor).isMultiple(of: 3) {
                dates.append(cursor)
            }
            guard let next = calendar.date(byAdding: .hour, value: 1, to: cursor), next > cursor else { break }
            cursor = next
        }
        return dates
    }
}

struct WidgetReloadState: Equatable {
    let status: L1Snapshot.Status
    let observedAt: Date?
    let freeBytes: Int64?

    init(_ snapshot: L1Snapshot) {
        status = snapshot.status
        observedAt = snapshot.latest?.time
        freeBytes = snapshot.latest?.freeBytes
    }
}

enum L1DisplayFormat {
    static func timestamp(_ date: Date, timeZone: TimeZone = .current) -> String {
        format(date, pattern: "yyyy/MM/dd HH:mm:ss", timeZone: timeZone)
    }

    static func hourMinute(_ date: Date, timeZone: TimeZone = .current) -> String {
        format(date, pattern: "HH:mm", timeZone: timeZone)
    }

    static func monthDay(_ date: Date, timeZone: TimeZone = .current) -> String {
        format(date, pattern: "MM/dd", timeZone: timeZone)
    }

    private static func format(_ date: Date, pattern: String, timeZone: TimeZone) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = timeZone
        // Calendar year / 24-hour time, not ISO week-year or ambiguous 12-hour time.
        formatter.dateFormat = pattern
        return formatter.string(from: date)
    }
}

enum SharedSnapshot {
    static let widgetKind = "BlackboxL1Widget"
    static let detailURL = URL(string: "blackbox-viewer://l1")!

    static func location() throws -> URL {
        guard let group = Bundle.main.object(forInfoDictionaryKey: "BlackboxAppGroup") as? String,
              !group.isEmpty, !group.contains("$("), !group.hasPrefix("."),
              let container = FileManager.default.containerURL(
                forSecurityApplicationGroupIdentifier: group) else {
            throw CocoaError(.fileNoSuchFile)
        }
        return container.appendingPathComponent("l1-display.json")
    }

    static func read() -> L1Snapshot {
        guard let url = try? location(),
              let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
              let size = attributes[.size] as? NSNumber, size.intValue <= 2_000_000,
              let data = try? Data(contentsOf: url),
              let snapshot = try? JSONDecoder().decode(L1Snapshot.self, from: data),
              snapshot.version == 1, snapshot.samples.count <= 10_000 else { return .empty }
        return snapshot
    }

    static func write(_ snapshot: L1Snapshot) throws {
        // This is a display cache in the app group, never the monitoring database.
        try JSONEncoder().encode(snapshot).write(to: location(), options: .atomic)
    }
}
