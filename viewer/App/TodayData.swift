import Foundation

// Tolerant, read-only view of existing incident JSON. Older records can omit
// individual fields; absent values never become zero or a new evaluation.
indirect enum IncidentJSON: Decodable, Sendable {
    case object([String: IncidentJSON]), array([IncidentJSON]), string(String), number(Double), bool(Bool), null
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([String: IncidentJSON].self) { self = .object(v) }
        else { self = .array(try c.decode([IncidentJSON].self)) }
    }
    subscript(_ key: String) -> IncidentJSON { if case .object(let v) = self { return v[key] ?? .null }; return .null }
    var text: String? { if case .string(let v) = self { return v }; return nil }
    var number: Double? { if case .number(let v) = self, v.isFinite { return v }; return nil }
    var flag: Bool? { if case .bool(let v) = self { return v }; return nil }
    var array: [IncidentJSON] { if case .array(let v) = self { return v }; return [] }
    var object: [String: IncidentJSON] { if case .object(let v) = self { return v }; return [:] }
    static func parse(_ text: String) -> IncidentJSON { (try? JSONDecoder().decode(Self.self, from: Data(text.utf8))) ?? .null }
}

enum TodayFormat {
    static let gib = 1_073_741_824.0
    static func capacity(_ bytes: Double?) -> String { bytes.map { String(format: "%.2f GiB", $0 / gib) } ?? "記録なし" }
    static func signed(_ bytes: Double?) -> String { bytes.map { String(format: "%@%.2f GiB", $0 < 0 ? "−" : "+", abs($0) / gib) } ?? "記録なし" }
    static func stamp(_ date: Date?) -> String {
        guard let date else { return "—" }
        let f = DateFormatter(); f.dateFormat = "yyyy/MM/dd HH:mm:ss"; return f.string(from: date)
    }
    static func clock(_ date: Date?) -> String {
        guard let date else { return "—" }
        let f = DateFormatter(); f.dateFormat = "HH:mm:ss"; return f.string(from: date)
    }
    static func path(_ path: String) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return path == home ? "~" : path.hasPrefix(home + "/") ? "~" + path.dropFirst(home.count) : path
    }
    static func state(_ state: String) -> String {
        switch state {
        case "succeeded": return "正常完了"
        case "partial": return "部分取得"
        case "failed": return "失敗"
        case "cancelled": return "中止"
        case "skipped": return "実行省略"
        case "running": return "実行中"
        case "pending": return "待機中"
        case "ok": return "正常取得"
        case "deduplicated": return "重複除外"
        case "error": return "取得失敗"
        case "missing": return "対象なし"
        case "excluded": return "対象外"
        default: return "状態記録不明"
        }
    }
    static func period(_ rule: String) -> String {
        switch rule {
        case "DROP_SPACE_1m": return "短期（1分）"
        case "DROP_SPACE_24h": return "24時間"
        case "entry": return "低容量"
        case "boot_id": return "再起動"
        default: return "期間記録なし"
        }
    }
    static func comparisonReason(_ reason: String?) -> String {
        switch reason {
        case "candidates_found": return "増加候補あり"
        case "no_candidates": return "正常比較・候補なし"
        case "comparison_unavailable": return "比較不能"
        case "partly_comparable": return "一部比較不能・比較できた範囲では候補なし"
        case "growth_identified": return "L9で増加箇所を特定"
        case "no_baseline": return "比較可能な過去L9なし"
        case "insufficient_evidence": return "L9の個別増加が特定閾値未満"
        case "scan_incomplete": return "取得不完全・失敗・中止"
        default: return "評価待ち／記録なし"
        }
    }
    static func comparisonLimit(_ reason: String) -> String {
        switch reason {
        case "targets_unconfigured": return "対象未設定"
        case "scan_missing": return "Scan記録なし"
        case "scan_incomplete": return "Scan取得不完全"
        case "current_value_unavailable": return "現在値が取得不能"
        case "current_value_missing": return "過去に記録されたパスの現在値なし"
        case "baseline_value_unavailable": return "同一パス・種別の過去正常値なし"
        case "current_results_missing", "current_target_missing": return "現在の対象結果なし"
        case "no_eligible_normal_history": return "同じ対象・方式・取得条件で基準以前の正常比較履歴なし"
        case "no_common_normal_paths": return "過去履歴との共通正常パスなし"
        case "no_normal_comparison": return "正常比較できる結果なし"
        case "detail_values_unavailable": return "一部詳細パスの現在値・過去値が未記録または取得不能"
        default: return "比較情報不足"
        }
    }
}

struct TodayL1: Sendable {
    let time: Date
    let free: Double
    let total: Double?
    var used: Double? { total.flatMap { $0 >= free ? $0 - free : nil } }
}
struct TodayScan: Identifiable, Sendable {
    let id: String
    let layer: String
    var trigger: String
    let state: String
    let started: Date?
    let ended: Date?
    let targets: [String]
    let options: IncidentJSON
    var archived = false
    var hasError = false
    var origin: String { trigger == "periodic" ? "定期" : trigger == "incident" ? "Incident" : trigger == "manual" ? "手動" : "起因記録なし" }
    var duration: String {
        guard let started, let ended, ended >= started else { return "—" }
        return String(format: "%.1f秒", ended.timeIntervalSince(started))
    }
}
struct TodayResult: Identifiable, Sendable {
    let path: String
    let kind: String
    let bytes: Double?
    let state: String
    var id: String { path + "\u{0}" + kind }
}
struct TodayScanDetail: Sendable {
    let rows: [TodayResult]
    let count: Int
    let failures: Int
}
struct TodayIncident: Identifiable, Sendable {
    let id: String
    let detected: Date
    let type: String
    let state: String
    let stage: String
    let detection: IncidentJSON
    let evaluation: IncidentJSON
    let candidates: IncidentJSON
    let investigation: IncidentJSON
    var steps: [IncidentJSON] { investigation.array }
    var isPeriodAware: Bool { detection["investigation_version"].number == 2 }
    var periodEvaluations: [IncidentJSON] {
        steps.flatMap { step in
            let records = step["layer"].text == "L9" ? step["target_evaluations"].object.sorted { $0.key < $1.key }.map(\.value) : [step]
            return records.flatMap { record in
                record["periods"].object.sorted { $0.key < $1.key }.map { _, period in
                    var value = period.object
                    for key in ["scan_id", "started", "ended", "target"] where value[key] == nil { value[key] = record[key] }
                    value["layer"] = step["layer"]
                    return IncidentJSON.object(value)
                }
            }
        }
    }
    var targets: [IncidentJSON] { steps.flatMap { $0["target_evaluations"].object.sorted { $0.key < $1.key }.map(\.value) } }
    var status: String { state == "identified" ? "原因特定" : state == "investigating" ? "一次調査中" : state == "unidentified" ? "原因未特定" : "状態記録不明" }
    var reason: String? {
        if let reason = evaluation["reason"].text { return reason }
        return ["scan_incomplete", "no_baseline", "insufficient_evidence"].first { reason in targets.contains { $0["reason"].text == reason } }
    }
    var category: String? {
        if isPeriodAware { return nil } // Period results can mix candidates and unavailable paths.
        guard state == "unidentified" else { return nil }
        switch reason {
        case "scan_incomplete": return "D"
        case "no_baseline": return "B"
        case "insufficient_evidence": return "C"
        default:
            // A is only the completed L2 no-candidate path, not any old/missing JSON.
            guard case .array(let savedCandidates) = candidates else { return nil }
            let l2 = steps.last { $0["layer"].text == "L2" }
            return !steps.contains { $0["layer"].text == "L9" } && savedCandidates.isEmpty &&
                (l2?["compared_count"].number ?? 0) > 0 && ["succeeded", "partial"].contains(l2?["state"].text ?? "") ? "A" : nil
        }
    }
    var threshold: Double? { detection["threshold"].number }
    var freeBefore: Double? { detection["rules"].array.first?["baseline"]["free"].number }
    var freeAfter: Double? { detection["current"]["free"].number }
    var freeChange: Double? { evaluation["free_change"].number }
    var route: String {
        steps.compactMap { step -> String? in
            guard let layer = step["layer"].text else { return nil }
            let raw = step["options"].object.isEmpty ? step["scans"].object.sorted { $0.key < $1.key }.first?.value["options"] ?? .null : step["options"]
            let options = raw.text.map(IncidentJSON.parse) ?? raw
            guard layer == "L9", let depth = options["max_depth"].number else { return layer }
            return layer + String(format: "（最大%.0f階層）", depth)
        }.joined(separator: " → ")
    }
    var conditions: String {
        let values = detection["rules"].array.map { rule -> String in
            let threshold = TodayFormat.capacity(rule["threshold"].number)
            switch rule["rule"].text {
            case "DROP_SPACE_1m", "minute": return "1分間に\(threshold)以上減少"
            case "DROP_SPACE_24h", "day": return "24時間に\(threshold)以上減少"
            case "entry": return "空き容量\(threshold)以下に突入"
            case "boot_id": return "再起動を検知"
            default: return "検知条件の詳細記録なし"
            }
        }
        return values.isEmpty ? "検知条件の詳細記録なし" : values.joined(separator: " / ")
    }
    var changes: [IncidentJSON] {
        if state == "identified" { return evaluation["findings"].array }
        if category == "B" { return candidates.array }
        if category == "A" { return steps.flatMap { $0["changes"].array } }
        return targets.flatMap { $0["changes"].array }
    }
    var findings: [IncidentJSON] {
        let positive = changes.filter { ($0["delta"].number ?? 0) > 0 }
        if state == "identified" { return positive } // never sum parent/child findings
        return positive.max { ($0["delta"].number ?? 0) < ($1["delta"].number ?? 0) }.map { [$0] } ?? []
    }
    var resultText: String {
        let route = self.route.isEmpty ? "調査経路の記録なし" : self.route
        if state == "investigating" {
            return stage == "L9_request" ? "\(route)で候補を検出。L9の実行待ち。" : "\(route)のScanで一次調査中。"
        }
        if isPeriodAware {
            return state == "identified" ? "\(route)で増加箇所を特定。特定した期間と他の期間の不足は期間別評価を確認。L1減少全量の説明を保証しません。" : "\(route)で一次調査を終了。候補なしと比較不能は期間別評価を確認。"
        }
        if state == "identified" { return "\(route)のScanで増加箇所を特定。" }
        let maximum = TodayFormat.capacity(findings.first?["delta"].number)
        if ["A", "C"].contains(category ?? ""), findings.first?["delta"].number == nil {
            return "\(route)を実施。一次調査を終了。比較可能な増加箇所・増加量の保存記録なし。"
        }
        switch category {
        case "A": return "\(route)を実施。最大増加量（\(maximum)）は候補判定閾値（\(TodayFormat.capacity(threshold.map { $0 * 0.5 }))）未満。一次調査を終了。"
        case "B": return "\(route)を実施。候補のL9に比較可能な過去記録がなく、特定できず。"
        case "C": return "\(route)を実施。L9最大増加量（\(maximum)）は特定閾値（\(TodayFormat.capacity(threshold))）未満。"
        case "D":
            var messages: [String] = []
            if targets.contains(where: { $0["scan_complete"].flag == false }) { messages.append("L9対象の取得不完全。") }
            if targets.contains(where: { $0["baseline_id"].text != nil && $0["baseline_complete"].flag == false }) { messages.append("比較元にも取得不完全の記録。") }
            let states = steps.compactMap { $0["state"].text } + steps.flatMap { $0["scans"].object.values.compactMap { $0["state"].text } }
            if states.contains("failed") { messages.append("Scan失敗の記録あり。") }
            if states.contains("cancelled") { messages.append("Scan中止の記録あり。") }
            let failures = targets.compactMap { $0["failures"].number }.map { String(format: "%.0f", $0) }
            if !failures.isEmpty { messages.append("対象別の取得失敗 \(failures.joined(separator: " / "))件。") }
            return messages.isEmpty ? "Scan不完全の評価記録あり。詳細は関連Scanを確認。" : messages.joined()
        default: return "一次調査を終了。分類に必要な評価記録なし。"
        }
    }
    var relatedIDs: [String] {
        let ids = steps.flatMap { step in
            [step["scan_id"].text].compactMap { $0 } + Array(step["scans"].object.keys).sorted() + step["target_evaluations"].object.values.compactMap { $0["scan_id"].text }
        }
        var seen: Set<String> = []
        return ids.filter { seen.insert($0).inserted }
    }
    func evidence(for id: String) -> [IncidentJSON] {
        if isPeriodAware { return periodEvaluations.filter { $0["scan_id"].text == id } }
        return steps.filter { $0["scan_id"].text == id } + targets.filter { $0["scan_id"].text == id }
    }
    func archivedScan(_ id: String) -> TodayScan? {
        for step in steps {
            let saved = step["scan_id"].text == id ? step : step["scans"][id]
            guard let state = saved["state"].text, let layer = step["layer"].text else { continue }
            // L9's saved metadata stores targets/options as JSON strings.
            let targets = saved["targets"].text.map(IncidentJSON.parse) ?? saved["targets"]
            let options = saved["options"].text.map(IncidentJSON.parse) ?? saved["options"]
            return TodayScan(id: id, layer: layer, trigger: "incident", state: state,
                             started: saved["started"].number.map(Date.init(timeIntervalSince1970:)),
                             ended: saved["ended"].number.map(Date.init(timeIntervalSince1970:)),
                             targets: targets.array.compactMap(\.text), options: options, archived: true, hasError: saved["error"].text != nil)
        }
        return nil
    }
}
struct TodaySnapshot: Sendable {
    let day: Date
    let first: TodayL1?
    let last: TodayL1?
    let latestObservation: Date?
    let incidents: [TodayIncident]
    let scans: [TodayScan]
    let related: [String: TodayScan]
    var usageChange: Double? {
        guard let first, let last, first.time < last.time, let a = first.used, let b = last.used,
              first.total == last.total else { return nil }
        return b - a
    }
    func count(_ state: String) -> Int { incidents.filter { $0.state == state }.count }
    func categoryCount(_ category: String) -> Int { incidents.filter { $0.category == category }.count }
    static func selectedDay(_ date: Date?, now: Date, calendar: Calendar = .current) -> Date {
        min(calendar.startOfDay(for: date ?? now), calendar.startOfDay(for: now))
    }
    static func move(_ day: Date, by amount: Int, now: Date, calendar: Calendar = .current) -> Date {
        selectedDay(calendar.date(byAdding: .day, value: amount, to: day), now: now, calendar: calendar)
    }
}
