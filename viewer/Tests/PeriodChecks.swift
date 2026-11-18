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
            // Presentation groups exact path+kind only, and keeps each saved
            // candidate/finding and period without summing or reclassification.
            precondition(item.decreaseRules.first?["rule"].text == "DROP_SPACE_24h")
            precondition(item.decreaseText(item.decreaseRules[0]).contains("24.00時間で6.00 GiB減少"))
            precondition(item.decreaseText(item.decreaseRules[1]).contains("1.00分で6.00 GiB減少"))
            precondition(item.places.count == 2)
            let place = item.places.first { $0.path == "/fixture/other" }!
            precondition(place.comparisons.count == 2)
            precondition(place.comparisons.filter(\.finalFinding).count == 1)
            precondition(place.comparisons.allSatisfy { $0.rule == "DROP_SPACE_24h" })
            precondition(place.comparisons.allSatisfy { $0.change["delta"].number == 3 * TodayFormat.gib })
            precondition(place.comparisons.allSatisfy { $0.baseline["ended"].number != nil && $0.evidence["ended"].number != nil })
            precondition(place.comparisons[0].label.contains("候補"))
            precondition(place.comparisons[1].label.contains("特定"))
            precondition(item.unavailablePeriods.contains { $0["layer"].text == "L2" })
            precondition(TodayFormat.observation(.null) == "観測日時の記録なし")
            precondition(TodayFormat.elapsed(nil) == "経過時間の記録なし")
            // Different kinds and parent/child paths remain separate. Even a
            // huge L9 candidate is not a finding unless saved as one.
            var candidate = l2["candidates"].array[0].object
            candidate["kind"] = .string("file")
            candidate["baseline_id"] = .string("older-path-source")
            var parent = candidate; parent["path"] = .string("/fixture")
            var daily = l2.object
            daily["candidates"] = .array(l2["candidates"].array + [.object(candidate), .object(parent)])
            var l9 = other[0].object; l9["findings"] = .array([])
            l9["candidates"] = .array([.object(candidate)])
            let custom = TodayIncident(id: "presentation", detected: item.detected, type: item.type,
                state: "unidentified", stage: "done", detection: item.detection, evaluation: .null, candidates: .null,
                investigation: .array([
                    .object(["layer": .string("L2"), "periods": .object(["DROP_SPACE_24h": .object(daily)])]),
                    .object(["layer": .string("L9"), "target_evaluations": .object(["test": .object([
                        "periods": .object(["DROP_SPACE_1m": .object(l9)])])])])]))
            precondition(custom.places.count == 3)
            precondition(custom.places.flatMap(\.comparisons).count == 3)
            precondition(custom.places.flatMap(\.comparisons).allSatisfy { !$0.finalFinding })
            let missing = custom.places.first { $0.path == "/fixture/other" && $0.kind == "file" }!.comparisons[0]
            precondition(missing.baselineID == "older-path-source")
            precondition(missing.interval.contains("観測日時の記録なし"))


            precondition(item.relatedIDs.allSatisfy { !item.evidence(for: $0).isEmpty })
            precondition(TodayFormat.comparisonReason("no_candidates").contains("正常比較"))
            precondition(TodayFormat.comparisonReason("comparison_unavailable") == "比較不能")
        }
        let after = try Data(contentsOf: url)
        precondition(after == before)
        print("PASS: runtime-generated mixed Incident, period thresholds/candidates/gaps, L9 day-only findings, source IDs/times, read-only bytes unchanged")
    }
}
