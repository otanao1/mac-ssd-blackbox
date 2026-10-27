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
        func referenceDay(_ old: [StorageResult], _ new: [StorageResult]) -> StorageDay {
            StorageDay(date: today, previous: scan("old", yesterday, old), current: scan("new", currentTime, new), previousL1: nil, currentL1: nil)
        }
        let failure = StorageResult(path: a.path + "/child/deep/blocked", kind: "unknown", bytes: nil, status: "error", error: "permission denied")
        let beforePartial = [row(a.path, 10*gib, "partial"), row(a.path + "/child", 2*gib, "partial"), failure]
        let afterPartial = [row(a.path, 12*gib, "partial"), row(a.path + "/child", 4*gib, "partial"), failure]
        let reference = referenceDay(beforePartial, afterPartial)
        precondition(reference.delta(path: a.path, parent: a).gib == 2)
        precondition(reference.delta(path: a.path, parent: a).isReference)
        precondition(reference.delta(path: a.path + "/child", parent: a).gib == 2)
        precondition(reference.delta(path: a.path + "/child", parent: a).isReference)
        precondition(!day.delta(path: a.path, parent: a).isReference)
        for status in ["error", "missing", "excluded"] {
            let same = StorageResult(path: failure.path, kind: failure.kind, bytes: nil, status: status, error: "permission denied")
            precondition(referenceDay([beforePartial[0], same], [afterPartial[0], same]).delta(path: a.path, parent: a).isReference)
        }
        var mutations: [[StorageResult]] = [Array(afterPartial.dropLast()), afterPartial + [StorageResult(path: a.path + "/another/deep", kind: "unknown", bytes: nil, status: "missing", error: "gone")]]
        for changed in [StorageResult(path: failure.path, kind: "unknown", bytes: nil, status: "error", error: "changed"),
                        StorageResult(path: failure.path, kind: "unknown", bytes: nil, status: "missing", error: failure.error),
                        StorageResult(path: failure.path, kind: "boundary", bytes: nil, status: "error", error: failure.error),
                        StorageResult(path: failure.path, kind: "unknown", bytes: nil, status: "error"),
                        StorageResult(path: a.path + "-sibling/blocked", kind: "unknown", bytes: nil, status: "error", error: failure.error)] {
            mutations.append(Array(afterPartial.dropLast()) + [changed])
        }
        for bytes: Int64? in [nil, -1] { mutations.append([row(a.path, bytes, "partial"), afterPartial[1], failure]) }
        mutations.append([row(a.path, 12*gib, "partial", kind: "file"), failure])
        for changed in mutations {
            precondition(referenceDay(beforePartial, changed).delta(path: a.path, parent: a).gib == nil)
            precondition(referenceDay(changed, beforePartial).delta(path: a.path, parent: a).gib == nil)
        }
        precondition(referenceDay([beforePartial[0]], [afterPartial[0]]).delta(path: a.path, parent: a).gib == nil)
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
        try extensionChecks()
        print("PASS: L1 ±90s/ties/sign, L2 parents/children/partial/missing/version, shared time anchors, 7 calendar days, readonly SQL")
    }

    static func databaseChecks() throws {
        let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let url = folder.appendingPathComponent("storage-checks-\(UUID().uuidString).sqlite3")
        defer { try? FileManager.default.removeItem(at: url) }
        var connection: OpaquePointer?
        precondition(sqlite3_open(url.path, &connection) == SQLITE_OK)
        let db = connection!
        func execute(_ sql: String) { precondition(sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK) }
        execute("CREATE TABLE l1(time REAL PRIMARY KEY,path TEXT,free INTEGER,error TEXT); CREATE TABLE scans(id TEXT PRIMARY KEY,layer TEXT,trigger TEXT,created REAL,started REAL,state TEXT,scan_version INTEGER,targets TEXT); CREATE TABLE results(scan_id TEXT,path TEXT,kind TEXT,bytes INTEGER,status TEXT,error TEXT);")
        let epoch = now.timeIntervalSince1970
        execute("INSERT INTO l1 VALUES(\(epoch),'/fixture/disk',900,NULL),(\(epoch-3600-90),'/fixture/disk',1200,NULL),(\(epoch-3600+90),'/fixture/disk',1100,NULL),(\(epoch-3600),'/fixture/disk',NULL,'error'),(\(epoch-86400+90),'/fixture/disk',1500,NULL),(\(epoch-10),'/another/disk',999999,NULL)")
        // Latest path must be used throughout; an earlier observation on another path is ignored.
        var utc = Calendar(identifier: .gregorian); utc.timeZone = TimeZone(secondsFromGMT: 0)!
        let day = utc.startOfDay(for: now).timeIntervalSince1970
        let targets = "[\"/fixture/parent/*\"]"
        for (id,time,trigger,state,size) in [("previous",day-86400+3600,"periodic","partial",100), ("current",day+3600,"periodic","partial",130), ("manual",day+7200,"manual","succeeded",9000), ("incident",day+7300,"incident","succeeded",9999)] {
            let recordedTargets = trigger == "periodic" ? targets : "[\"/fixture/manual-only\"]"
            execute("INSERT INTO scans VALUES('\(id)','L2','\(trigger)',\(time-10),\(time),'\(state)',2,'\(recordedTargets)')")
            execute("INSERT INTO results VALUES('\(id)','/fixture/parent','directory',\(size),'ok',NULL),('\(id)','/fixture/parent/child','directory',\(size),'ok',NULL)")
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
        // Exercise raw error preservation through SQL, using only this synthetic DB.
        precondition(sqlite3_open(url.path, &connection) == SQLITE_OK)
        precondition(sqlite3_exec(connection, "UPDATE results SET status='partial' WHERE path='/fixture/parent'; INSERT INTO results VALUES('previous','/fixture/parent/deep/blocked','unknown',NULL,'error','denied'),('current','/fixture/parent/deep/blocked','unknown',NULL,'error','denied');", nil, nil, nil) == SQLITE_OK)
        sqlite3_close(connection)
        let partialBefore = try Data(contentsOf: url)
        try DatabaseReader.withReadOnlyConnection(url) { db in
            let reading = try DatabaseReader.readConnected(db, now: now.addingTimeInterval(60), calendar: utc)
            let delta = reading.storage.days.last!.delta(path: "/fixture/parent", parent: reading.storage.targets[0])
            precondition(delta.isReference && delta.gib == 30.0/Double(gib))
            precondition(reading.storage.days.last!.children(reading.storage.targets[0]).first!.delta.isReference == false)
        }
        let partialAfter = try Data(contentsOf: url)
        precondition(partialAfter == partialBefore)
    }
    static func extensionChecks() throws {
        precondition(StorageLayer.l2.periodDays == [7, 30])
        precondition(StorageLayer.l3.periodDays == [1, 3, 7, 30])
        var cal = Calendar(identifier: .gregorian); cal.timeZone = TimeZone(secondsFromGMT: 0)!
        let end = cal.date(from: DateComponents(year: 2026, month: 4, day: 21, hour: 18, minute: 30))!
        let parent = StorageTarget("/fixture/root/*")
        let nested = StorageTarget("/fixture/root/child/*")
        func scan(_ hour: Int, _ bytes: Int64, status: String = "ok", failure: String = "denied", state: String = "partial") -> StorageScan {
            let time = cal.date(byAdding: .hour, value: hour, to: cal.dateInterval(of: .hour, for: end)!.start)!.addingTimeInterval(600)
            let rows = [StorageResult(path: parent.path, kind: "directory", bytes: bytes*gib, status: status),
                        StorageResult(path: nested.path, kind: "directory", bytes: gib, status: "ok"),
                        StorageResult(path: nested.path+"/grandchild", kind: "directory", bytes: gib, status: "ok"),
                        StorageResult(path: parent.path+"/blocked/deep", kind: "unknown", bytes: nil, status: "error", error: failure)]
            return StorageScan(id: "hour-\(hour)", time: time, state: state, version: 2,
                               targets: [parent,nested], results: Dictionary(uniqueKeysWithValues: rows.map { ($0.path,$0) }))
        }
        let scans = [scan(-5, 10),scan(-4, 11),scan(-3, 12, status: "partial"),
                     scan(-2, 13, status: "partial"),scan(-1, 14, status: "partial", failure: "changed"),scan(0, 15)]
        let data = StorageSnapshot.make(targets: [parent,nested], scans: scans, l1: [], now: end,
                                        calendar: cal, layer: .l3, window: StorageWindow(days: 1))
        precondition(data.days.count == 24)
        let days = Array(data.days.suffix(6))
        precondition(days[1].comparison(parent.path).gib == 1)
        precondition(days[2].comparison(parent.path).gib == nil)
        precondition(days[3].comparison(parent.path).isReference)
        precondition(days[4].comparison(parent.path).gib == nil)
        let capacity = StoragePlot.points(days, path: parent.path, metric: .capacity)
        let delta = StoragePlot.points(days, path: parent.path, metric: .delta)
        precondition(capacity.count == 6 && capacity.filter(\.partial).count == 3)
        precondition(delta.count == 2 && delta.last!.reference)
        // Cardinality changes never change the metric or selected observation.
        for layer in StorageLayer.allCases {
            var state = StorageSelection(layer: layer); state.initialize(data)
            state.selectedDate = days[3].date; state.trail.append(nested.path)
            let selectedSeries = state.series
            for mode in StorageMode.allCases {
                state.mode = mode
                for metric in StorageMetric.allCases {
                    state.metric = metric
                    let points = StoragePlot.points(days, path: parent.path, metric: state.metric)
                    precondition(points.map(\.value) == (metric == .capacity ? [10,11,12,13,14,15] : [1,1]))
                    precondition(state.metric.alternate.alternate == metric)
                    precondition(state.metric.alternate.rawValue == (metric == .capacity ? "差分" : "容量"))
                    state.metric = state.metric.alternate
                    state.initialize(data)
                    precondition(state.mode == mode && state.metric == metric.alternate)
                    precondition(state.series == selectedSeries && state.trail.last == nested.path && state.selectedDate == days[3].date)
                }
            }
        }
        let lines = StoragePlot.segments(days, path: parent.path, metric: .capacity)
        precondition(lines.contains { $0.style == .reference } && lines.contains { $0.style == .incomparable })
        precondition(StoragePlot.segments(days, path: parent.path, metric: .delta).allSatisfy { $0.style == .incomparable })
        precondition(days.last!.subfolders(parent.path).count == 1)
        precondition(days.last!.subfolders(nested.path).count == 1)
        precondition(days.last!.capacity(parent.path+"/blocked/deep") == nil)
        precondition(days.last!.subfolders(parent.path+"/blocked").isEmpty)
        var gapScans = scans; gapScans.remove(at: 3)
        let gap = StorageSnapshot.make(targets: [parent], scans: gapScans, l1: [], now: end, calendar: cal,
                                       layer: .l3, window: StorageWindow(days: 1))
        precondition(gap.days[gap.days.count-2].comparison(parent.path).gib == nil)
        let pending = scan(0, 99, state: "pending")
        let inProgress = StorageSnapshot.make(targets: [parent], scans: scans.dropLast()+[pending], l1: [], now: end,
                                              calendar: cal, layer: .l3, window: StorageWindow(days: 1))
        precondition(inProgress.days.last!.capacity(parent.path) == nil)
        for metric in StorageMetric.allCases {
            precondition(!StoragePlot.points(inProgress.days, path: parent.path, metric: metric).contains { $0.date == inProgress.days.last!.date })
            precondition(StoragePlot.points(data.days, path: "/fixture/missing", metric: metric).isEmpty)
        }
        let past = StorageWindow(days: 3).moving(-1, now: end, calendar: cal)
        precondition(past.end == cal.date(byAdding: .day, value: -3, to: end))
        precondition(past.moving(1, now: end, calendar: cal).end == nil)
        var resized = past; resized.days = 30
        precondition(resized.end == past.end)
        let empty = StorageSnapshot.make(targets: [parent], scans: scans, l1: [], now: end, calendar: cal,
                                         layer: .l3, window: resized)
        precondition(empty.days.allSatisfy { $0.current == nil })
        var selection = StorageSelection(layer: .l3); selection.initialize(data)
        selection.trail.append(nested.path); selection.selectedDate = days[1].date
        selection.childQueries[parent.path] = "child"; selection.childPages[parent.path] = 1
        selection.metric = .delta; selection.mode = .multi
        selection.initialize(data)
        precondition(selection.metric == .delta && selection.mode == .multi)
        precondition(selection.trail.count == 2 && selection.childPages[parent.path] == 1)
        for i in 0..<10 { _ = selection.toggle("/extra-\(i)") }
        precondition(selection.series.count == 5)
        selection = StorageSelection(layer: .l3); selection.initialize(data)
        precondition(selection.mode == .single && selection.metric == .capacity && selection.trail.count == 1 && selection.selectedDate == nil && selection.childQueries.isEmpty)
        precondition(StorageSelection(layer: .l2).mode == .multi && StorageSelection(layer: .l2).metric == .delta)
        cal.timeZone = TimeZone(identifier: "America/Los_Angeles")!
        for (month,day,count) in [(3,8,23),(11,1,25)] {
            let dst = cal.date(from: DateComponents(year: 2026, month: month, day: day, hour: 18))!
            let hourly = StorageSnapshot.make(targets: [], scans: [], l1: [], now: dst, calendar: cal,
                                              layer: .l3, window: StorageWindow(days: 1))
            precondition(hourly.days.count == count && Set(hourly.days.map(\.date)).count == count)
        }
        let decreasing = StorageSnapshot.make(targets: [parent,nested], scans: [scan(-1, 15),scan(0, 14)], l1: [], now: end,
                                              calendar: cal, layer: .l3, window: StorageWindow(days: 1))
        precondition(decreasing.days.last!.comparison(parent.path).gib == -1)
        let invalidBytes = StorageSnapshot.make(targets: [parent,nested], scans: [scan(-1, 15),scan(0, -1)], l1: [], now: end,
                                                calendar: cal, layer: .l3, window: StorageWindow(days: 1))
        for snapshot in [data, gap, inProgress, empty, decreasing, invalidBytes] { preparedChecks(snapshot) }
        preparedChecks(.empty)
        try hourlyDatabaseChecks(end: end)
        print("PASS: L3 hourly/DST/gaps/reference; Single/Multi independent capacity/delta, defaults, state/drilldown; dotted connectors without fabricated deltas; END/navigation; selection/reset")
    }

    // Independent pre-optimization construction is the regression oracle:
    // preserve ordering, explanations, every observation and every connector.
    static func preparedChecks(_ data: StorageSnapshot) {
        func same(_ a: StorageDelta, _ b: StorageDelta) {
            precondition(a.gib == b.gib && a.reason == b.reason && a.isReference == b.isReference)
        }
        func same(_ a: StoragePlotPoint, _ b: StoragePlotPoint) {
            precondition(a.date == b.date && a.value == b.value && a.partial == b.partial && a.reference == b.reference)
        }
        for day in data.days {
            let ranked = data.targets.sorted { a, b in
                let x = day.delta(path: a.path, parent: a).gib, y = day.delta(path: b.path, parent: b).gib
                return x == y ? a.path < b.path : (x ?? -.infinity) > (y ?? -.infinity)
            }
            let prepared = StorageRanking(targets: data.targets, day: day)
            precondition(prepared.targets == ranked)
            precondition(prepared.maximum == max(0.001, ranked.compactMap { day.delta(path: $0.path, parent: $0).gib }.map(abs).max() ?? 1))
            for target in data.targets { same(prepared.deltas[target.path]!, day.delta(path: target.path, parent: target)) }
        }
        let unavailable = StorageRanking(targets: data.targets, day: nil)
        precondition(unavailable.targets == data.targets && unavailable.maximum == 1)
        for delta in unavailable.deltas.values { precondition(delta.gib == nil && delta.reason == "定期観測なし") }
        let paths = Set(data.targets.map(\.path) + data.days.flatMap { $0.current?.results.keys.map { $0 } ?? [] } + ["/fixture/unrecorded"])
        for path in paths { for metric in StorageMetric.allCases {
            let expected: [StoragePlotPoint] = data.days.compactMap { day in
                let delta = day.comparison(path)
                if metric == .capacity {
                    guard let row = day.capacity(path), let bytes = row.bytes else { return nil }
                    return StoragePlotPoint(date: day.date, value: Double(bytes)/1_073_741_824, partial: row.status == "partial", reference: delta.isReference)
                }
                guard let value = delta.gib else { return nil }
                return StoragePlotPoint(date: day.date, value: value, partial: day.current?.results[path]?.status == "partial", reference: delta.isReference)
            }
            let prepared = StoragePlot.make(data.days, path: path, metric: metric)
            precondition(prepared.points.count == expected.count)
            for (a,b) in zip(prepared.points, expected) { same(a,b) }
            let indices = Dictionary(uniqueKeysWithValues: data.days.enumerated().map { ($0.element.date, $0.offset) })
            let segments = Array(zip(expected, expected.dropFirst()))
            precondition(prepared.segments.count == segments.count)
            for (actual,pair) in zip(prepared.segments,segments) {
                let (a,b) = pair, index = indices[b.date]!
                let delta = data.days[index].comparison(path)
                let style: StorageLineStyle = index != indices[a.date]! + 1 || delta.gib == nil || (metric == .delta && a.reference != b.reference)
                    ? .incomparable : delta.isReference ? .reference : .normal
                same(actual.before,a); same(actual.after,b); precondition(actual.style == style)
            }
        }}
    }

    static func hourlyDatabaseChecks(end: Date) throws {
        let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        let url = folder.appendingPathComponent("hourly-\(UUID().uuidString).sqlite3")
        defer { try? FileManager.default.removeItem(at: url) }
        var connection: OpaquePointer?
        precondition(sqlite3_open(url.path, &connection) == SQLITE_OK)
        func exec(_ sql: String) { precondition(sqlite3_exec(connection, sql, nil, nil, nil) == SQLITE_OK) }
        exec("CREATE TABLE l1(time REAL,path TEXT,free INTEGER,error TEXT); CREATE TABLE scans(id TEXT,layer TEXT,trigger TEXT,created REAL,started REAL,state TEXT,scan_version INTEGER,targets TEXT); CREATE TABLE results(scan_id TEXT,path TEXT,kind TEXT,bytes INTEGER,status TEXT,error TEXT); CREATE TABLE l3_daily(bucket REAL,summary TEXT);")
        let epoch = end.timeIntervalSince1970
        exec("INSERT INTO l1 VALUES(\(epoch),'/fixture/disk',1000,NULL)")
        for (id,age,trigger,state,size) in [("a",7200.0,"periodic","succeeded",10),("b",3600.0,"periodic","succeeded",11),("c",0.0,"periodic","succeeded",12),("manual",1.0,"manual","succeeded",9000),("incident",2.0,"incident","succeeded",8000)] {
            exec("INSERT INTO scans VALUES('\(id)','L3','\(trigger)',\(epoch-age),\(epoch-age),'\(state)',2,'[\"/fixture/root/*\"]'); INSERT INTO results VALUES('\(id)','/fixture/root','directory',\(size), 'ok',NULL)")
        }
        exec("INSERT INTO l3_daily VALUES(0,'{\"last_bytes\":999999}')")
        sqlite3_close(connection)
        let before = try Data(contentsOf: url)
        try DatabaseReader.withReadOnlyConnection(url) { db in
            let reading = try DatabaseReader.readConnected(db, now: end)
            precondition(reading.storage.targets.isEmpty)
            precondition(reading.storageL3.targets.count == 1)
            precondition(reading.storageL3.days.last!.comparison("/fixture/root").gib == 1/Double(gib))
            let old = try DatabaseReader.readConnected(db, now: end, windows: [.l3: StorageWindow(days: 30, end: end.addingTimeInterval(-86400*40))])
            precondition(old.storageL3.days.allSatisfy { $0.current == nil })
            precondition(sqlite3_exec(db, "DELETE FROM results", nil, nil, nil) != SQLITE_OK)
        }
        let after = try Data(contentsOf: url)
        precondition(after == before)
    }

}
