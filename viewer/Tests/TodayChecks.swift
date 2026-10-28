import Foundation
import SQLite3

@main
struct TodayChecks {
    static func main() throws {
        let url = URL(fileURLWithPath: CommandLine.arguments[1]).appendingPathComponent("today-fixture.sqlite3")
        let before = try Data(contentsOf: url)
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let day = calendar.date(from: DateComponents(year: 2026, month: 10, day: 28))!
        let now = day.addingTimeInterval(7200)
        try DatabaseReader.withReadOnlyConnection(url) { db in
            let data = try DatabaseReader.readTodayConnected(db, day: day, now: now, calendar: calendar)
            precondition(data.day == day && data.incidents.count == 9)
            precondition(data.scans.count == 8)
            precondition(data.scans.filter { $0.trigger == "periodic" }.count == 3)
            precondition(data.scans.filter { $0.layer == "L9" }.count == 3)
            precondition(data.scans.first?.id == "crossday")
            precondition(!data.scans.contains { ["pending","manual","tomorrow","yesterday","not-a-scan","unknown-trigger","future-today"].contains($0.id) })
            precondition(data.scans.first { $0.id == "crossday" }?.trigger == "incident")
            precondition(!data.scans.contains { ["manual", "unrelated-request"].contains($0.id) })
            precondition(data.first?.free == 100 * TodayFormat.gib && data.last?.free == 98 * TodayFormat.gib)
            precondition(data.usageChange == 2 * TodayFormat.gib)
            precondition(data.latestObservation == day.addingTimeInterval(3700)) // failed latest remains observable, not a valid endpoint
            for category in ["A","B","C","D"] { precondition(data.incidents.first { $0.id == category }?.category == category) }
            let identified = data.incidents.first { $0.id == "identified" }!
            precondition(identified.status == "原因特定" && identified.category == nil && identified.findings.count == 1)
            precondition(identified.relatedIDs == ["shared"] && data.related["shared"]?.state == "partial")
            precondition(!identified.relatedIDs.contains("old")) // baseline IDs aren't investigation executions
            precondition(data.count("identified") == 2 && data.count("investigating") == 1 && data.count("unidentified") == 6)
            precondition(data.incidents.first { $0.id == "old" }?.category == nil)
            let archived = data.incidents.first { $0.id == "archived" }!
            precondition(archived.archivedScan("archived")?.targets == ["/fixture/app/*"])
            precondition(archived.evidence(for: "archived").first?["excerpt"].array.count == 1)
            precondition(data.related["archived"] == nil)
            let detail = try DatabaseReader.readTodayScanConnected(db, id: "shared")
            precondition(detail.count == 105 && detail.rows.count == 100 && detail.failures == 1)
            precondition(detail.rows.first?.state == "error")
            let yesterday = try DatabaseReader.readTodayConnected(db, day: day.addingTimeInterval(-86400), now: now, calendar: calendar)
            precondition(yesterday.incidents.count == 1 && yesterday.scans.count == 1 && yesterday.usageChange == TodayFormat.gib)
            let empty = try DatabaseReader.readTodayConnected(db, day: day.addingTimeInterval(-2*86400), now: now, calendar: calendar)
            precondition(empty.incidents.isEmpty && empty.scans.isEmpty && empty.last == nil && empty.latestObservation == nil)
            let future = try DatabaseReader.readTodayConnected(db, day: day.addingTimeInterval(86400), now: now, calendar: calendar)
            precondition(future.day == day && future.incidents.count == data.incidents.count)
            precondition(sqlite3_exec(db, "DELETE FROM incidents", nil, nil, nil) == SQLITE_READONLY)
        }
        let after = try Data(contentsOf: url)
        precondition(after == before)
        let point = TodayL1(time: day, free: 100, total: 200)
        let single = TodaySnapshot(day: day, first: point, last: point, latestObservation: day, incidents: [], scans: [], related: [:])
        precondition(single.usageChange == nil)
        let resized = TodaySnapshot(day: day, first: point, last: TodayL1(time: day.addingTimeInterval(1), free: 90, total: 300), latestObservation: day, incidents: [], scans: [], related: [:])
        precondition(resized.usageChange == nil)
        var dst = calendar; dst.timeZone = TimeZone(identifier: "America/Los_Angeles")!
        let spring = dst.date(from: DateComponents(year: 2026, month: 3, day: 8))!
        let tomorrow = TodaySnapshot.move(spring, by: 1, now: spring.addingTimeInterval(3*86400), calendar: dst)
        precondition(tomorrow.timeIntervalSince(spring) == 23*3600)
        let autumn = dst.date(from: DateComponents(year: 2026, month: 11, day: 1))!
        precondition(TodaySnapshot.move(autumn, by: 1, now: autumn.addingTimeInterval(3*86400), calendar: dst).timeIntervalSince(autumn) == 25*3600)
        let yearEnd = calendar.date(from: DateComponents(year: 2026, month: 12, day: 31))!
        precondition(calendar.component(.year, from: TodaySnapshot.move(yearEnd, by: 1, now: yearEnd.addingTimeInterval(86400), calendar: calendar)) == 2027)
        print("PASS: Today date boundaries/DST/year, L1 endpoints/missing, A–D/old JSON, shared/archived scans, trigger/state/counts, bounded lazy detail, read-only bytes unchanged")
    }
}
