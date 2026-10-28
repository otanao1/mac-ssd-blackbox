import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers
import WidgetKit

@MainActor
final class ViewerModel: ObservableObject {
    @Published private(set) var snapshot = L1Snapshot.empty
    @Published private(set) var reading: ViewerReading?
    @Published private(set) var now = Date()
    @Published private(set) var configurationMessage: String?
    @Published private(set) var sharingMessage: String?
    @Published private(set) var refreshing = false
    @Published private(set) var storageWindows: [StorageLayer: StorageWindow] = [.l2: StorageWindow(days: 7), .l3: StorageWindow(days: 3)]
    @Published private(set) var resetID = 0
    @Published private(set) var storageRevision = 0
    @Published private(set) var todayReading: TodaySnapshot?
    @Published private(set) var todayDate: Date?
    @Published private(set) var todayMessage: String?
    @Published private(set) var todayLoading = false
    @Published private(set) var todayDetails: [String: TodayScanDetail] = [:]
    @Published private(set) var todayDetailErrors: Set<String> = []
    @Published private(set) var todayRevision = 0
    private var todayGeneration = 0
    private var todayDetailLoading: Set<String> = []
    private var previewToday: ((Date, Date) -> TodaySnapshot)?
    var selectedToday: Date { TodaySnapshot.selectedDay(todayDate, now: now) }
    private var pendingRefresh = false
    private var previewMode = false
    private var previewLoader: (([StorageLayer: StorageWindow]) -> ViewerReading)?
    private var source: DatabaseSource?
    private var selectingSource = false
    private var timer: AnyCancellable?
    private var wake: AnyCancellable?
    private var lastReloadState: WidgetReloadState?
    private let sourceKey = "databaseSource.v1"

    // A synthetic reading lets the standalone review harness exercise the production UI
    // without bookmarks, timers, shared-cache writes or a monitoring database.
    init(preview: ViewerReading? = nil, previewLoader: (([StorageLayer: StorageWindow]) -> ViewerReading)? = nil, previewToday: ((Date, Date) -> TodaySnapshot)? = nil) {
        if let preview {
            self.previewToday = previewToday
            todayReading = previewToday?(preview.l1.generatedAt, preview.l1.generatedAt)
            previewMode = true; self.previewLoader = previewLoader; reading = preview; snapshot = preview.l1; now = preview.l1.generatedAt
            return
        }
        if let data = UserDefaults.standard.data(forKey: sourceKey) {
            source = try? JSONDecoder().decode(DatabaseSource.self, from: data)
        }
        // Owned by the app's StateObject, not by a Window's .task or .onDisappear.
        timer = Timer.publish(every: 60, tolerance: 10, on: .main, in: .common)
            .autoconnect().sink { [weak self] _ in Task { @MainActor in await self?.refresh() } }
        wake = NSWorkspace.shared.notificationCenter.publisher(for: NSWorkspace.didWakeNotification)
            .receive(on: RunLoop.main).sink { [weak self] _ in Task { @MainActor in await self?.refresh() } }
        Task { [weak self] in await self?.refresh() }
    }

    func refresh(forceWidgetReload: Bool = false) async {
        guard !selectingSource else { return }
        guard !refreshing else { pendingRefresh = true; return }
        if previewMode {
            if let previewLoader { reading = previewLoader(storageWindows) }
            await refreshToday()
            return
        }
        now = Date()
        guard let source else {
            publish(snapshot, forceWidgetReload: forceWidgetReload)
            await refreshToday()
            return
        }
        refreshing = true
        defer {
            refreshing = false; now = Date()
            if pendingRefresh { pendingRefresh = false; Task { await self.refresh() } }
        }
        let date = now
        let windows = storageWindows
        let revision = storageRevision
        // File access and SQLite never run on the UI thread.
        let result = await Task.detached(priority: .utility) { () -> ViewerReading? in
            try? DatabaseReader.readAll(source, now: date, windows: windows)
        }.value
        guard revision == storageRevision else { pendingRefresh = true; return }
        if let result {
            reading = result
            snapshot = result.l1
        } else {
            snapshot = L1Snapshot(version: 1, generatedAt: date, samples: snapshot.samples,
                                  latest: snapshot.latest, status: .unavailable)
        }
        publish(snapshot, forceWidgetReload: forceWidgetReload)
        await refreshToday()
    }

    func moveToday(by amount: Int) {
        let day = TodaySnapshot.move(selectedToday, by: amount, now: now)
        guard day != selectedToday else { return }
        todayDate = day == Calendar.current.startOfDay(for: now) ? nil : day
        invalidateToday()
        Task { await refreshToday() }
    }

    private func invalidateToday() {
        todayGeneration += 1
        todayReading = nil; todayMessage = nil; todayLoading = false
        todayDetails = [:]; todayDetailErrors = []; todayDetailLoading = []
        todayRevision += 1
    }

    func refreshToday() async {
        if !previewMode { now = Date() }
        todayGeneration += 1
        let generation = todayGeneration, day = selectedToday, date = now
        todayLoading = true
        if previewMode {
            todayReading = previewToday?(day, date)
            todayLoading = false
            return
        }
        guard let source else {
            todayMessage = "保存先が未設定です。既存の監視設定とDBフォルダを選択してください。"
            todayLoading = false
            return
        }
        let result = await Task.detached(priority: .utility) {
            try? DatabaseReader.readToday(source, day: day, now: date)
        }.value
        guard generation == todayGeneration, day == selectedToday else { return }
        todayLoading = false
        todayReading = result
        todayMessage = result == nil ? "表示日のデータを取得できません。保存先と接続状態を確認してください。" : nil
        todayDetails = [:]; todayDetailErrors = []; todayDetailLoading = []
        todayRevision += 1
    }

    func loadTodayScan(_ id: String) async {
        guard todayDetails[id] == nil, !todayDetailLoading.contains(id), let source, !previewMode else { return }
        let generation = todayGeneration
        todayDetailLoading.insert(id)
        let result = await Task.detached(priority: .utility) { try? DatabaseReader.readTodayScan(source, id: id) }.value
        guard generation == todayGeneration else { return }
        todayDetailLoading.remove(id)
        if let result { todayDetails[id] = result; todayDetailErrors.remove(id) }
        else { todayDetailErrors.insert(id) }
    }

    func setWindow(_ window: StorageWindow, for layer: StorageLayer) {
        guard layer.periodDays.contains(window.days) else { return }
        storageWindows[layer] = window
        storageRevision += 1
        Task { await refresh() }
    }

    func resetStorage() {
        storageWindows = [.l2: StorageWindow(days: 7), .l3: StorageWindow(days: 3)]
        resetID += 1
        storageRevision += 1
        Task { await refresh() }
    }

    private func publish(_ value: L1Snapshot, forceWidgetReload: Bool = false) {
        do {
            try SharedSnapshot.write(value)
            sharingMessage = nil
            let state = WidgetReloadState(value)
            // Notify only after the atomic write, on new observations / state
            // changes. WidgetKit still controls when the timeline is rendered.
            if forceWidgetReload || state != lastReloadState {
                WidgetCenter.shared.reloadTimelines(ofKind: SharedSnapshot.widgetKind)
                lastReloadState = state
            }
        } catch {
            sharingMessage = "Widget共有を利用できません。署名・App Groupの設定を確認してください。"
        }
    }

    func selectSource() {
        guard !previewMode else {
            configurationMessage = "合成データのレビュー版では保存先設定を変更しません。"
            return
        }
        guard !refreshing, !selectingSource else { return }
        selectingSource = true
        defer { selectingSource = false }
        // Two explicit grants: read the existing identity, then access its DB folder.
        // No scanning, monitor CLI invocation, directory creation, or config edits.
        let configPanel = NSOpenPanel()
        configPanel.title = "監視のlocal.jsonを選択"
        configPanel.message = "保存先の識別情報を読み取ります。設定は変更しません。"
        configPanel.allowedContentTypes = [.json]
        configPanel.allowsMultipleSelection = false
        guard configPanel.runModal() == .OK, let configURL = configPanel.url else { return }
        let granted = configURL.startAccessingSecurityScopedResource()
        defer { if granted { configURL.stopAccessingSecurityScopedResource() } }
        do {
            let configuration = try JSONDecoder().decode(DatabaseSource.Configuration.self,
                                                        from: Data(contentsOf: configURL))
            guard configuration.storage.volume.kind == "disk" else {
                configurationMessage = "この試作はローカルディスク・外付けSSDの保存先が対象です。"
                return
            }
            let folderPanel = NSOpenPanel()
            folderPanel.title = "同じ監視DBの保存フォルダを選択"
            folderPanel.message = "選択した設定の保存先と照合し、読み取り専用で参照します。"
            folderPanel.canChooseDirectories = true
            folderPanel.canChooseFiles = false
            folderPanel.allowsMultipleSelection = false
            folderPanel.canCreateDirectories = false
            guard folderPanel.runModal() == .OK, let folder = folderPanel.url else { return }
            let folderGranted = folder.startAccessingSecurityScopedResource()
            defer { if folderGranted { folder.stopAccessingSecurityScopedResource() } }
            _ = try DatabaseReader.validate(folder, identity: configuration.storage)
            let bookmark = try folder.bookmarkData(options: [.withSecurityScope, .securityScopeAllowOnlyReadAccess],
                                                   includingResourceValuesForKeys: nil, relativeTo: nil)
            let selected = DatabaseSource(bookmark: bookmark, identity: configuration.storage)
            let encoded = try JSONEncoder().encode(selected)
            UserDefaults.standard.set(encoded, forKey: sourceKey)
            source = selected
            invalidateToday()
            snapshot = .empty
            reading = nil
            configurationMessage = nil
            Task { await refresh() }
        } catch {
            // Do not expose paths or raw SQLite / filesystem error strings.
            configurationMessage = "保存先を確認できませんでした。既存設定と対応するDBフォルダを選び直してください。"
        }
    }
}
