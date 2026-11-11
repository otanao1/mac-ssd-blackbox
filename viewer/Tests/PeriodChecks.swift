import Foundation

@main
struct PeriodChecks {
    static func main() throws {
        let url = URL(fileURLWithPath: CommandLine.arguments[1]).appendingPathComponent("period-data/blackbox.sqlite3")
        let before = try Data(contentsOf: url)
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = .init(secondsFromGMT: 0)!
        let day = calendar.date(from: DateComponents(year: 2026, month: 10, day: 28))!
        try DatabaseReader.withReadOnlyConnection(url) { db in
            let data = try DatabaseReader.readTodayConnected(db, day: day, now: day.addingTimeInterval(7200), calendar: calendar)
            precondition(data.incidents.count == 1)
            let item = data.incidents[0]
            precondition(item.isPeriodAware && item.state == "identified" && item.category == nil)
            precondition(item.detection["rules"].array.count == 2)
            precondition(item.route.hasPrefix("L3 → L2 → L9"))
            precondition(item.periodEvaluations.count == 6) // L3 + L2 + two periods × two L9 targets
            let l2 = item.periodEvaluations.first { $0["layer"].text == "L2" }!
            precondition(l2["rule"].text == "DROP_SPACE_24h")
            precondition(l2["candidates"].array.count == 1 && l2["unavailable_count"].number == 1)
            precondition(l2["baselines"].object.count == 1 && l2["started"].number != nil)
            let other = item.periodEvaluations.filter { $0["target"].text == "/fixture/other/*" }
            precondition(other.count == 2)
            precondition(other.first { $0["rule"].text == "DROP_SPACE_1m" }?["reason"].text == "insufficient_evidence")
            precondition(other.first { $0["rule"].text == "DROP_SPACE_24h" }?["reason"].text == "growth_identified")
            precondition(other[0]["baseline_id"].text != other[1]["baseline_id"].text)
            precondition(item.resultText.contains("全量"))
            precondition(item.relatedIDs.allSatisfy { !item.evidence(for: $0).isEmpty })
            precondition(TodayFormat.comparisonReason("no_candidates").contains("正常比較"))
            precondition(TodayFormat.comparisonReason("comparison_unavailable") == "比較不能")
        }
        let after = try Data(contentsOf: url)
        precondition(after == before)
        print("PASS: runtime-generated mixed Incident, period thresholds/candidates/gaps, L9 day-only findings, source IDs/times, read-only bytes unchanged")
    }
}
