import Foundation
import SQLite3

extension DatabaseReader {
    static func readToday(_ source: DatabaseSource, day: Date, now: Date) throws -> TodaySnapshot {
        try withSource(source) { db in try readTodayConnected(db, day: day, now: now) }
    }
    static func readTodayScan(_ source: DatabaseSource, id: String) throws -> TodayScanDetail {
        try withSource(source) { db in try readTodayScanConnected(db, id: id) }
    }
    private static func todayString(_ s: OpaquePointer, _ i: Int32) -> String {
        sqlite3_column_text(s, i).map { String(cString: $0) } ?? ""
    }
    private static func todayNumber(_ s: OpaquePointer, _ i: Int32) -> Double? {
        sqlite3_column_type(s, i) == SQLITE_NULL ? nil : sqlite3_column_double(s, i)
    }
    private static func todayScan(_ s: OpaquePointer) -> TodayScan {
        TodayScan(id: todayString(s, 0), layer: todayString(s, 1), trigger: todayString(s, 2), state: todayString(s, 3),
                  started: todayNumber(s, 4).map(Date.init(timeIntervalSince1970:)), ended: todayNumber(s, 5).map(Date.init(timeIntervalSince1970:)),
                  targets: IncidentJSON.parse(todayString(s, 6)).array.compactMap(\.text), options: IncidentJSON.parse(todayString(s, 7)),
                  hasError: sqlite3_column_type(s, 8) != SQLITE_NULL)
    }
    static func readTodayConnected(_ db: OpaquePointer, day: Date, now: Date, calendar: Calendar = .current) throws -> TodaySnapshot {
        let day = TodaySnapshot.selectedDay(day, now: now, calendar: calendar)
        let next = calendar.date(byAdding: .day, value: 1, to: day)!
        let args: [Argument] = [.number(day.timeIntervalSince1970), .number(next.timeIntervalSince1970), .number(now.timeIntervalSince1970)]
        // Half-open calendar intervals handle DST and midnight, including last-day scans
        // still running after midnight. Pending/unstarted requests aren't executions.
        var scans = try query(db, "SELECT id,layer,trigger,state,started,ended,targets,options,error FROM scans WHERE started>=? AND started<? AND started<=? AND layer IN ('L2','L3','L9') AND trigger IN ('periodic','incident','request','manual') ORDER BY started DESC,id", args, row: todayScan)
        let incidents = try query(db, "SELECT id,detected,type,state,stage,detection,evaluation,candidates,investigation FROM incidents WHERE detected>=? AND detected<? AND detected<=? ORDER BY detected DESC,id", args) { s in
            TodayIncident(id: todayString(s, 0), detected: Date(timeIntervalSince1970: sqlite3_column_double(s, 1)),
                          type: todayString(s, 2), state: todayString(s, 3), stage: todayString(s, 4),
                          detection: IncidentJSON.parse(todayString(s, 5)), evaluation: IncidentJSON.parse(todayString(s, 6)),
                          candidates: IncidentJSON.parse(todayString(s, 7)), investigation: IncidentJSON.parse(todayString(s, 8)))
        }
        // L9 dispatch persists trigger='request' even for incident investigations.
        // Resolve actual investigation links, including Incidents detected on another day.
        // scan_ids alone also contains baselines, so it is only a coarse SQL prefilter.
        var linkedExecutions = Set(incidents.flatMap(\.relatedIDs))
        for scan in scans where ["request", "manual"].contains(scan.trigger) && !linkedExecutions.contains(scan.id) {
            let investigations = try query(db, "SELECT investigation FROM incidents WHERE instr(scan_ids,?)>0", [.string(scan.id)]) { s in
                IncidentJSON.parse(todayString(s, 0))
            }
            for investigation in investigations {
                let saved = TodayIncident(id: "", detected: day, type: "", state: "", stage: "", detection: .null, evaluation: .null, candidates: .null, investigation: investigation)
                linkedExecutions.formUnion(saved.relatedIDs)
            }
        }
        scans = scans.compactMap { scan in
            if ["periodic", "incident"].contains(scan.trigger) { return scan }
            guard linkedExecutions.contains(scan.id) else { return nil }
            var relatedScan = scan; relatedScan.trigger = "incident"; return relatedScan
        }
        let latest = try query(db, "SELECT time,path FROM l1 WHERE time>=? AND time<? AND time<=? ORDER BY time DESC LIMIT 1", args) { s in
            (Date(timeIntervalSince1970: sqlite3_column_double(s, 0)), todayString(s, 1))
        }.first
        let l1Args = args + [.string(latest?.1 ?? "")]
        func endpoint(_ order: String) throws -> TodayL1? {
            try query(db, "SELECT time,free,total FROM l1 WHERE time>=? AND time<? AND time<=? AND path=? AND error IS NULL AND free>=0 ORDER BY time \(order) LIMIT 1", l1Args) { s in
                TodayL1(time: Date(timeIntervalSince1970: sqlite3_column_double(s, 0)), free: sqlite3_column_double(s, 1), total: todayNumber(s, 2))
            }.first
        }
        var related = Dictionary(uniqueKeysWithValues: scans.map { ($0.id, $0) })
        for id in Set(incidents.flatMap(\.relatedIDs)).sorted() where related[id] == nil {
            related[id] = try query(db, "SELECT id,layer,trigger,state,started,ended,targets,options,error FROM scans WHERE id=? AND layer IN ('L2','L3','L9')", [.string(id)], row: todayScan).first
        }
        return try TodaySnapshot(day: day, first: endpoint("ASC"), last: endpoint("DESC"), latestObservation: latest?.0,
                                 incidents: incidents, scans: scans, related: related)
    }
    static func readTodayScanConnected(_ db: OpaquePointer, id: String) throws -> TodayScanDetail {
        let count = try query(db, "SELECT count(*),coalesce(sum(CASE WHEN status NOT IN ('ok','deduplicated') THEN 1 ELSE 0 END),0) FROM results WHERE scan_id=?", [.string(id)]) { s in
            (Int(sqlite3_column_int(s, 0)), Int(sqlite3_column_int(s, 1)))
        }.first!
        // Bound full L9 result reads; failures first, then largest saved capacities.
        let rows = try query(db, "SELECT path,kind,bytes,status FROM results WHERE scan_id=? ORDER BY CASE WHEN status NOT IN ('ok','deduplicated') THEN 0 ELSE 1 END,bytes DESC,path,kind LIMIT 100", [.string(id)]) { s in
            TodayResult(path: todayString(s, 0), kind: todayString(s, 1), bytes: todayNumber(s, 2), state: todayString(s, 3))
        }
        return TodayScanDetail(rows: rows, count: count.0, failures: count.1)
    }
}
