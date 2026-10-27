import SwiftUI

// Compiles the production view/model with synthetic readings. No background
// refresh, security-scoped bookmarks, monitoring DB, or widget cache writes.
@main
struct StorageExplorerGUIReview: App {
    @StateObject private var model = ViewerModel(preview: StorageExplorerFixture.reading(), previewLoader: { StorageExplorerFixture.reading(windows: $0) })
    var body: some Scene {
        WindowGroup("Storage Explorer — 合成データ・GUIレビュー") { ViewerDetail(model: model) }
            .defaultSize(width: 760, height: 720).windowResizability(.contentMinSize)
            .commands { CommandGroup(replacing: .newItem) {} }
    }
}
