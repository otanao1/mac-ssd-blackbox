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
        var stale = false
        let folder = try URL(resolvingBookmarkData: source.bookmark, options: [.withSecurityScope, .withoutUI],
                             relativeTo: nil, bookmarkDataIsStale: &stale)
        guard !stale, folder.startAccessingSecurityScopedResource() else { throw Failure.inaccessible }
        defer { folder.stopAccessingSecurityScopedResource() }
        let url = try validate(folder, identity: source.identity)
        var connection: OpaquePointer?
        guard sqlite3_open_v2(url.path, &connection, SQLITE_OPEN_READONLY, nil) == SQLITE_OK,
              let db = connection else {
            if let connection { sqlite3_close(connection) }
            throw Failure.sqlite
        }
        defer { sqlite3_close(db) }
        sqlite3_busy_timeout(db, 100)
        guard sqlite3_db_readonly(db, "main") == 1,
              sqlite3_exec(db, "PRAGMA query_only=ON", nil, nil, nil) == SQLITE_OK else { throw Failure.sqlite }
        // A short transaction makes latest + history one consistent snapshot. No writes or PRAGMA changes on disk.
        guard sqlite3_exec(db, "BEGIN", nil, nil, nil) == SQLITE_OK else { throw Failure.sqlite }
        defer { sqlite3_exec(db, "ROLLBACK", nil, nil, nil) }
        var latestStatement: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT time,free,error,path FROM l1 ORDER BY time DESC LIMIT 1", -1,
                                 &latestStatement, nil) == SQLITE_OK else { throw Failure.sqlite }
        defer { sqlite3_finalize(latestStatement) }
        let latestResult = sqlite3_step(latestStatement)
        guard latestResult == SQLITE_ROW || latestResult == SQLITE_DONE else { throw Failure.sqlite }
        var latest: L1Sample?
        var monitoredPath = ""
        if latestResult == SQLITE_ROW {
            latest = sample(latestStatement)
            guard let text = sqlite3_column_text(latestStatement, 3) else { throw Failure.sqlite }
            monitoredPath = String(cString: text)
        }
        var statement: OpaquePointer?
        let sql = "SELECT time,free,error FROM l1 WHERE time>=? AND time<=? AND path=? ORDER BY time LIMIT 10000"
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else { throw Failure.sqlite }
        defer { sqlite3_finalize(statement) }
        sqlite3_bind_double(statement, 1, now.addingTimeInterval(-86_400).timeIntervalSince1970)
        sqlite3_bind_double(statement, 2, now.timeIntervalSince1970)
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        _ = monitoredPath.withCString { sqlite3_bind_text(statement, 3, $0, -1, transient) }
        var samples: [L1Sample] = []
        var result = sqlite3_step(statement)
        while result == SQLITE_ROW { samples.append(sample(statement)); result = sqlite3_step(statement) }
        guard result == SQLITE_DONE else { throw Failure.sqlite }
        _ = try validate(folder, identity: source.identity)
        return L1Snapshot(version: 1, generatedAt: now, samples: samples, latest: latest, status: .ready)
    }

    private static func sample(_ statement: OpaquePointer?) -> L1Sample {
        let valid = sqlite3_column_type(statement, 1) != SQLITE_NULL && sqlite3_column_type(statement, 2) == SQLITE_NULL
        let bytes: Int64? = valid ? sqlite3_column_int64(statement, 1) : nil
        return L1Sample(time: Date(timeIntervalSince1970: sqlite3_column_double(statement, 0)),
                        freeBytes: bytes.flatMap { $0 >= 0 ? $0 : nil })
    }
}
