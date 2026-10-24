import AppKit
import Combine
import Foundation
import UniformTypeIdentifiers
import WidgetKit

@MainActor
final class ViewerModel: ObservableObject {
    @Published private(set) var snapshot = L1Snapshot.empty
    @Published private(set) var now = Date()
    @Published private(set) var configurationMessage: String?
    @Published private(set) var sharingMessage: String?
    @Published private(set) var refreshing = false
    private var source: DatabaseSource?
    private var selectingSource = false
    private var timer: AnyCancellable?
    private var wake: AnyCancellable?
    private var lastReloadState: WidgetReloadState?
    private let sourceKey = "databaseSource.v1"

    init() {
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
        guard !refreshing, !selectingSource else { return }
        now = Date()
        guard let source else {
            publish(snapshot, forceWidgetReload: forceWidgetReload)
            return
        }
        refreshing = true
        defer { refreshing = false; now = Date() }
        let date = now
        // File access and SQLite never run on the UI thread.
        let result = await Task.detached(priority: .utility) { () -> L1Snapshot? in
            try? DatabaseReader.read(source, now: date)
        }.value
        if let result {
            snapshot = result
        } else {
            snapshot = L1Snapshot(version: 1, generatedAt: date, samples: snapshot.samples,
                                  latest: snapshot.latest, status: .unavailable)
        }
        publish(snapshot, forceWidgetReload: forceWidgetReload)
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
            snapshot = .empty
            configurationMessage = nil
            Task { await refresh() }
        } catch {
            // Do not expose paths or raw SQLite / filesystem error strings.
            configurationMessage = "保存先を確認できませんでした。既存設定と対応するDBフォルダを選び直してください。"
        }
    }
}
