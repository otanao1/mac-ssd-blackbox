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

    func capacityText(at date: Date, previous: Bool = false) -> String {
        guard let bytes = latest?.freeBytes, isCurrent(at: date) || previous else { return "—" }
        return String(format: "%.2f GiB", Double(bytes) / 1_073_741_824)
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
