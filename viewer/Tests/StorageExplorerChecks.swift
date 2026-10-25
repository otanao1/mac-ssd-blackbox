import Foundation
import SQLite3

@main
struct StorageExplorerChecks {
    static let gib: Int64 = 1_073_741_824
    static let now = Date(timeIntervalSince1970: 1_776_787_200) // synthetic fixed epoch
    static func main() throws {
        let t = now.addingTimeInterval(-3600)
        func sample(_ seconds: Double, _ bytes: Int64? = 10*gib) -> L1Sample { L1Sample(time: t.addingTimeInterval(seconds), freeBytes: bytes) }
        precondition(StorageMath.nearest([sample(-90), sample(90)], to: t)?.time == sample(-90).time)
        precondition(StorageMath.nearest([sample(90)], to: t) != nil)
        precondition(StorageMath.nearest([sample(-90)], to: t) != nil)
        precondition(StorageMath.nearest([sample(90.001), sample(-90.001), sample(0, nil)], to: t) == nil)
        let comparison = StorageMath.comparison(latest: L1Sample(time: now, freeBytes: 9*gib), hours: 1, samples: [sample(0)])
        precondition(comparison?.usageGiB == 1)
        precondition(StorageMath.comparison(latest: L1Sample(time: now, freeBytes: 9*gib), hours: 24, samples: [sample(0)]) == nil)
        precondition(StorageMath.comparison(latest: L1Sample(time: now, freeBytes: -1), hours: 1, samples: [sample(0)]) == nil)
        let deltas = L1PlotData.changes([sample(-180, gib), sample(-90, 2*gib), sample(1, 3*gib), sample(61, nil), sample(121, gib)], now: now)
        precondition(deltas.count == 1 && deltas[0].gib == 1)
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let a = StorageTarget("/fixture/parent/*"), b = StorageTarget("/fixture/other")
        func row(_ path: String, _ bytes: Int64?, _ status: String = "ok", kind: String = "directory") -> StorageResult {
            StorageResult(path: path, kind: kind, bytes: bytes, status: status)
        }
        let today = calendar.startOfDay(for: now)
        let yesterday = calendar.date(byAdding: .day, value: -1, to: today)!.addingTimeInterval(15*3600)
        let currentTime = today.addingTimeInterval(15*3600)
        let oldRows = [row(a.path, 10*gib), row(a.path+"/child", 2*gib), row(a.path+"/gone", gib), row(b.path, 0)]
        let newRows = [row(a.path, 12*gib), row(a.path+"/child", gib), row(a.path+"/new", gib), row(a.path+"/deep/error", nil, "error"), row(b.path, 0)]
        func scan(_ id: String, _ time: Date, _ rows: [StorageResult], state: String = "partial", version: Int = 2) -> StorageScan {
            StorageScan(id: id, time: time, state: state, version: version, targets: [a,b], results: Dictionary(uniqueKeysWithValues: rows.map { ($0.path,$0) }))
        }
        let previous = scan("previous", yesterday, oldRows), current = scan("current", currentTime, newRows)
        let ss = StorageSnapshot.make(targets: [a,b], scans: [previous,current], l1: [L1Sample(time: yesterday.addingTimeInterval(-90), freeBytes: 30*gib), L1Sample(time: currentTime.addingTimeInterval(90), freeBytes: 27*gib)], now: currentTime.addingTimeInterval(180), calendar: calendar)
        let day = ss.days.last!
        precondition(day.delta(path: a.path, parent: a).gib == 2)
        precondition(day.delta(path: b.path, parent: b).gib == 0)
        precondition(day.children(a).count == 3) // deep descendants are not direct children
        precondition(day.children(a).first(where: { $0.name == "child" })?.delta.gib == -1)
        precondition(day.children(a).first(where: { $0.name == "gone" })?.delta.gib == nil)
        precondition(day.children(a).first(where: { $0.name == "new" })?.delta.gib == nil)
        precondition(day.usageGiB == 3)
        precondition(ss.days.count == 7)
        let withoutPreviousDay = StorageSnapshot.make(targets: [a], scans: [scan("older", yesterday.addingTimeInterval(-86400), oldRows),current], l1: [], now: currentTime, calendar: calendar)
        precondition(withoutPreviousDay.days.last!.delta(path: a.path, parent: a).gib == nil)
        precondition(withoutPreviousDay.days.last!.usageGiB == nil)
        let partial = scan("partial", currentTime, [row(a.path, 12*gib, "partial"), row(a.path+"/child", gib)])
        let partialDay = StorageDay(date: today, previous: previous, current: partial, previousL1: nil, currentL1: nil)
        precondition(partialDay.delta(path: a.path, parent: a).gib == nil)
        precondition(partialDay.delta(path: a.path+"/child", parent: a).gib == -1)
        let kindChange = scan("changed", currentTime, [row(a.path+"/child", 0, kind: "file")])
        precondition(StorageDay(date: today, previous: previous, current: kindChange, previousL1: nil, currentL1: nil).delta(path: a.path+"/child", parent: a).gib == nil)
        let oldVersion = scan("v1", yesterday, oldRows, version: 1)
        precondition(StorageDay(date: today, previous: oldVersion, current: current, previousL1: nil, currentL1: nil).usageGiB == nil)
        let unknown = StorageDay(date: today, previous: previous, current: scan("failed", currentTime, newRows, state: "failed"), previousL1: nil, currentL1: nil)
        precondition(unknown.delta(path: a.path, parent: a).gib == nil)
        let tooFar = StorageSnapshot.make(targets: [a], scans: [previous,current], l1: [L1Sample(time: yesterday, freeBytes: 30*gib),L1Sample(time: currentTime.addingTimeInterval(90.001), freeBytes: 27*gib)], now: currentTime.addingTimeInterval(180), calendar: calendar)
        precondition(tooFar.days.last!.usageGiB == nil)
        calendar.timeZone = TimeZone(identifier: "America/Los_Angeles")!
        let dst = calendar.date(from: DateComponents(year: 2026, month: 11, day: 1, hour: 18))!
        let dstDays = StorageSnapshot.make(targets: [], scans: [], l1: [], now: dst, calendar: calendar).days
        precondition(dstDays.count == 7 && Set(dstDays.map { calendar.startOfDay(for: $0.date) }).count == 7)
        try databaseChecks()
        print("PASS: L1 ±90s/ties/sign, L2 parents/children/partial/missing/version, shared time anchors, 7 calendar days, readonly SQL")
    }

    static func databaseChecks() throws {
        let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let url = folder.appendingPathComponent("storage-checks-\(UUID().uuidString).sqlite3")
        var connection: OpaquePointer?
        precondition(sqlite3_open(url.path, &connection) == SQLITE_OK)
        let db = connection!
        func execute(_ sql: String) { precondition(sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK) }
        execute("CREATE TABLE l1(time REAL PRIMARY KEY,path TEXT,free INTEGER,error TEXT); CREATE TABLE scans(id TEXT PRIMARY KEY,layer TEXT,trigger TEXT,created REAL,started REAL,state TEXT,scan_version INTEGER,targets TEXT); CREATE TABLE results(scan_id TEXT,path TEXT,kind TEXT,bytes INTEGER,status TEXT);")
        let epoch = now.timeIntervalSince1970
        execute("INSERT INTO l1 VALUES(\(epoch),'/fixture/disk',900,NULL),(\(epoch-3600-90),'/fixture/disk',1200,NULL),(\(epoch-3600+90),'/fixture/disk',1100,NULL),(\(epoch-3600),'/fixture/disk',NULL,'error'),(\(epoch-86400+90),'/fixture/disk',1500,NULL),(\(epoch-10),'/another/disk',999999,NULL)")
        // Latest path must be used throughout; an earlier observation on another path is ignored.
        var utc = Calendar(identifier: .gregorian); utc.timeZone = TimeZone(secondsFromGMT: 0)!
        let day = utc.startOfDay(for: now).timeIntervalSince1970
        let targets = "[\"/fixture/parent/*\"]"
        for (id,time,trigger,state,size) in [("previous",day-86400+3600,"periodic","partial",100), ("current",day+3600,"periodic","partial",130), ("manual",day+7200,"manual","succeeded",9000), ("incident",day+7300,"incident","succeeded",9999)] {
            let recordedTargets = trigger == "periodic" ? targets : "[\"/fixture/manual-only\"]"
            execute("INSERT INTO scans VALUES('\(id)','L2','\(trigger)',\(time-10),\(time),'\(state)',2,'\(recordedTargets)')")
            execute("INSERT INTO results VALUES('\(id)','/fixture/parent','directory',\(size),'ok'),('\(id)','/fixture/parent/child','directory',\(size),'ok')")
            execute("INSERT INTO l1 VALUES(\(time),'/fixture/disk',\(10000-size),NULL)")
        }
        execute("INSERT INTO l1 VALUES(\(epoch+30),'/fixture/disk',NULL,'error'),(\(epoch+1000),'/future/disk',1,NULL)")
        sqlite3_close(db)
        let before = try Data(contentsOf: url)
        try DatabaseReader.withReadOnlyConnection(url) { db in
            precondition(sqlite3_db_readonly(db, "main") == 1)
            precondition(sqlite3_exec(db,"INSERT INTO l1 VALUES(1,'x',0,NULL)",nil,nil,nil) == SQLITE_READONLY)
            let reading = try DatabaseReader.readConnected(db, now: now.addingTimeInterval(60), calendar: {
                var cal = Calendar(identifier: .gregorian); cal.timeZone = TimeZone(secondsFromGMT: 0)!; return cal
            }())
            precondition(reading.latestValid?.freeBytes == 900)
            precondition(reading.l1.latest?.freeBytes == nil)
            precondition(reading.hourComparison?.previous.freeBytes == 1200)
            precondition(reading.dayComparison?.previous.freeBytes == 1500)
            let current = reading.storage.days.last!
            precondition(current.current?.id == "current")
            precondition(current.previous?.id == "previous")
            precondition(current.delta(path: "/fixture/parent", parent: reading.storage.targets[0]).gib == 30.0/Double(gib))
            precondition(current.usageGiB == 30.0/Double(gib))
        }
        let after = try Data(contentsOf: url)
        precondition(after == before)
    }
}
