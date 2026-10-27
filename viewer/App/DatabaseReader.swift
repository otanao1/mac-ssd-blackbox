import Foundation
import SQLite3

// Persisted only in the Viewer's sandbox. Never copied into the widget cache.
struct DatabaseSource: Codable, Sendable {
    struct Volume: Codable, Sendable { let kind: String; let uuid: String? }
    struct Identity: Codable, Sendable {
        let path: String
        let volume: Volume
        let directory_inode: UInt64
        let database_inode: UInt64
    }
    struct Configuration: Decodable { let storage: Identity }
    let bookmark: Data
    let identity: Identity
}

enum DatabaseReader {
    enum Failure: Error { case inaccessible, changed, unsupported, sqlite }

    static func validate(_ folder: URL, identity: DatabaseSource.Identity) throws -> URL {
        guard identity.volume.kind == "disk", let uuid = identity.volume.uuid else { throw Failure.unsupported }
        let db = folder.appendingPathComponent("blackbox.sqlite3")
        guard folder.standardizedFileURL.path == identity.path,
              folder.resolvingSymlinksInPath().path == identity.path,
              db.resolvingSymlinksInPath().path == db.path else { throw Failure.changed }
        let volume = try folder.resourceValues(forKeys: [.volumeUUIDStringKey]).volumeUUIDString
        let parent = try FileManager.default.attributesOfItem(atPath: folder.path)
        let file = try FileManager.default.attributesOfItem(atPath: db.path)
        guard volume?.caseInsensitiveCompare(uuid) == .orderedSame,
              parent[.type] as? FileAttributeType == .typeDirectory,
              (parent[.systemFileNumber] as? NSNumber)?.uint64Value == identity.directory_inode,
              (file[.systemFileNumber] as? NSNumber)?.uint64Value == identity.database_inode,
              file[.type] as? FileAttributeType == .typeRegular else { throw Failure.changed }
        // Reject redirected SQLite sidecars as well as a redirected database.
        for suffix in ["-journal", "-wal", "-shm"] {
            let sidecar = URL(fileURLWithPath: db.path + suffix)
            if let attributes = try? FileManager.default.attributesOfItem(atPath: sidecar.path),
               attributes[.type] as? FileAttributeType == .typeSymbolicLink { throw Failure.changed }
        }
        return db
    }

    static func read(_ source: DatabaseSource, now: Date) throws -> L1Snapshot {
        try readAll(source, now: now).l1
    }

    static func readAll(_ source: DatabaseSource, now: Date, windows: [StorageLayer: StorageWindow] = [:]) throws -> ViewerReading {
        var stale = false
        let folder = try URL(resolvingBookmarkData: source.bookmark, options: [.withSecurityScope, .withoutUI],
                             relativeTo: nil, bookmarkDataIsStale: &stale)
        guard !stale, folder.startAccessingSecurityScopedResource() else { throw Failure.inaccessible }
        defer { folder.stopAccessingSecurityScopedResource() }
        let url = try validate(folder, identity: source.identity)
        return try withReadOnlyConnection(url) { db in
            let result = try readConnected(db, now: now, windows: windows)
            _ = try validate(folder, identity: source.identity)
            return result
        }
    }

    // Also used by fixture checks. No writable fallback, schema setup, or disk PRAGMAs.
    static func withReadOnlyConnection<T>(_ url: URL, body: (OpaquePointer) throws -> T) throws -> T {
        var connection: OpaquePointer?
        guard sqlite3_open_v2(url.path, &connection, SQLITE_OPEN_READONLY, nil) == SQLITE_OK,
              let db = connection else {
            if let connection { sqlite3_close(connection) }
            throw Failure.sqlite
        }
        defer { sqlite3_close(db) }
        sqlite3_busy_timeout(db, 100)
        guard sqlite3_db_readonly(db, "main") == 1,
              sqlite3_exec(db, "PRAGMA query_only=ON", nil, nil, nil) == SQLITE_OK,
              sqlite3_exec(db, "BEGIN", nil, nil, nil) == SQLITE_OK else { throw Failure.sqlite }
        defer { sqlite3_exec(db, "ROLLBACK", nil, nil, nil) }
        return try body(db)
    }

    enum Argument { case number(Double), string(String) }
    static func query<T>(_ db: OpaquePointer, _ sql: String, _ args: [Argument] = [], row: (OpaquePointer) throws -> T) throws -> [T] {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else { throw Failure.sqlite }
        defer { sqlite3_finalize(statement) }
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        for (offset, arg) in args.enumerated() {
            let index = Int32(offset + 1)
            let result: Int32
            switch arg {
            case .number(let value): result = sqlite3_bind_double(statement, index, value)
            case .string(let value): result = value.withCString { sqlite3_bind_text(statement, index, $0, -1, transient) }
            }
            guard result == SQLITE_OK else { throw Failure.sqlite }
        }
        var rows: [T] = []
        var result = sqlite3_step(statement)
        while result == SQLITE_ROW { rows.append(try row(statement)); result = sqlite3_step(statement) }
        guard result == SQLITE_DONE else { throw Failure.sqlite }
        return rows
    }
    private static func string(_ statement: OpaquePointer, _ index: Int32) throws -> String {
        guard let value = sqlite3_column_text(statement, index) else { throw Failure.sqlite }
        return String(cString: value)
    }
    private static func sample(_ statement: OpaquePointer) -> L1Sample {
        let valid = sqlite3_column_type(statement, 1) != SQLITE_NULL && sqlite3_column_type(statement, 2) == SQLITE_NULL
        let bytes: Int64? = valid ? sqlite3_column_int64(statement, 1) : nil
        return L1Sample(time: Date(timeIntervalSince1970: sqlite3_column_double(statement, 0)),
                        freeBytes: bytes.flatMap { $0 >= 0 ? $0 : nil })
    }
    private static func nearest(_ db: OpaquePointer, path: String, time: Date, now: Date) throws -> L1Sample? {
        let epoch = time.timeIntervalSince1970
        return try query(db, "SELECT time,free,error FROM l1 WHERE path=? AND time>=? AND time<=? AND free>=0 AND error IS NULL ORDER BY abs(time-?),time LIMIT 1",
                         [.string(path), .number(epoch-90), .number(min(epoch+90, now.timeIntervalSince1970)), .number(epoch)], row: sample).first
    }
    static func readConnected(_ db: OpaquePointer, now: Date, calendar: Calendar = .current, windows: [StorageLayer: StorageWindow] = [:]) throws -> ViewerReading {
        let latestRows = try query(db, "SELECT time,free,error,path FROM l1 WHERE time<=? ORDER BY time DESC LIMIT 1", [.number(now.timeIntervalSince1970)]) { statement in
            (sample(statement), try string(statement, 3))
        }
        let latest = latestRows.first?.0
        let path = latestRows.first?.1 ?? ""
        let start = now.addingTimeInterval(-86400).timeIntervalSince1970
        let samples = try query(db, "SELECT time,free,error FROM l1 WHERE path=? AND time>=? AND time<=? ORDER BY time LIMIT 10000",
                                [.string(path), .number(start), .number(now.timeIntervalSince1970)], row: sample)
        let predecessor = try query(db, "SELECT time,free,error FROM l1 WHERE path=? AND time<? ORDER BY time DESC LIMIT 1", [.string(path), .number(start)], row: sample).first
        let latestValid = try query(db, "SELECT time,free,error FROM l1 WHERE path=? AND time<=? AND free>=0 AND error IS NULL ORDER BY time DESC LIMIT 1", [.string(path), .number(now.timeIntervalSince1970)], row: sample).first
        func comparison(_ hours: Int) throws -> L1Comparison? {
            guard let latestValid, let previous = try nearest(db, path: path, time: latestValid.time.addingTimeInterval(Double(-hours)*3600), now: now) else { return nil }
            return L1Comparison(latest: latestValid, previous: previous)
        }
        let storage = try readStorage(db, layer: .l2, window: windows[.l2] ?? StorageWindow(days: 7),
                                      l1Path: path, now: now, calendar: calendar)
        let storageL3 = try readStorage(db, layer: .l3, window: windows[.l3] ?? StorageWindow(days: 3),
                                        l1Path: path, now: now, calendar: calendar)
        return try ViewerReading(l1: L1Snapshot(version: 1, generatedAt: now, samples: samples, latest: latest, status: .ready),
                                 latestValid: latestValid, dayComparison: comparison(24), hourComparison: comparison(1),
                                 predecessor: predecessor, storage: storage, storageL3: storageL3, storageWindows: [.l2: windows[.l2] ?? StorageWindow(days: 7), .l3: windows[.l3] ?? StorageWindow(days: 3)])
    }
    private static func readStorage(_ db: OpaquePointer, layer: StorageLayer, window: StorageWindow,
                                    l1Path: String, now: Date, calendar: Calendar) throws -> StorageSnapshot {
        let targets: [StorageTarget] = try query(db, "SELECT targets FROM scans WHERE layer=? AND created<=? ORDER BY CASE WHEN trigger='periodic' THEN 0 ELSE 1 END,created DESC,id DESC LIMIT 1", [.string(layer.rawValue), .number(now.timeIntervalSince1970)]) { statement in
            try JSONDecoder().decode([String].self, from: Data(string(statement, 0).utf8)).map(StorageTarget.init)
        }.first ?? []
        let end = min(window.end ?? now, now)
        let last = calendar.dateInterval(of: layer.component, for: end)!.start
        let since = calendar.date(byAdding: .day, value: -window.days, to: last)!
        var scans = try query(db, "SELECT id,coalesce(started,created),state,scan_version,targets FROM scans WHERE layer=? AND trigger='periodic' AND coalesce(started,created)>=? AND coalesce(started,created)<=? ORDER BY coalesce(started,created),id",
                              [.string(layer.rawValue), .number(since.timeIntervalSince1970), .number(end.timeIntervalSince1970)]) { statement in
            StorageScan(id: try string(statement, 0), time: Date(timeIntervalSince1970: sqlite3_column_double(statement, 1)),
                        state: try string(statement, 2), version: Int(sqlite3_column_int(statement, 3)),
                        targets: try JSONDecoder().decode([String].self, from: Data(string(statement, 4).utf8)).map(StorageTarget.init), results: [:])
        }
        // Only representative day/hour scans need results. Keep failed/pending representatives as gaps.
        let representativeIDs = Set(Dictionary(grouping: scans, by: { calendar.dateInterval(of: layer.component, for: $0.time)!.start }).values.compactMap { $0.last?.id })
        var anchors: [L1Sample] = []
        for index in scans.indices where representativeIDs.contains(scans[index].id) && scans[index].completed {
            let results = try query(db, "SELECT path,kind,bytes,status,error FROM results WHERE scan_id=?", [.string(scans[index].id)]) { statement in
                StorageResult(path: try string(statement, 0), kind: try string(statement, 1),
                              bytes: sqlite3_column_type(statement, 2) == SQLITE_NULL ? nil : sqlite3_column_int64(statement, 2), status: try string(statement, 3),
                              error: sqlite3_column_type(statement, 4) == SQLITE_NULL ? nil : try string(statement, 4))
            }
            for result in results {
                if scans[index].results[result.path] != nil {
                    scans[index].results[result.path] = StorageResult(path: result.path, kind: "unknown", bytes: nil, status: "ambiguous")
                } else { scans[index].results[result.path] = result }
            }
            if let anchor = try nearest(db, path: l1Path, time: scans[index].time, now: now) { anchors.append(anchor) }
        }
        // Observation targets are taken from the recorded effective target list, not a new scan/config edit.
        let uniqueTargets = Dictionary(grouping: targets, by: \.path).values.compactMap { group in
            group.first(where: \.includesChildren) ?? group.first
        }.sorted { $0.path < $1.path }
        return StorageSnapshot.make(targets: uniqueTargets, scans: scans, l1: anchors, now: now, calendar: calendar, layer: layer, window: window)
    }

}
