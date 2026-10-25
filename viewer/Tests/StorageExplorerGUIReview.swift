import SwiftUI

// Compiles the production view/model with synthetic readings. No background
// refresh, security-scoped bookmarks, monitoring DB, or widget cache writes.
@main
struct StorageExplorerGUIReview: App {
    @StateObject private var model = ViewerModel(preview: StorageExplorerFixture.reading())
    var body: some Scene {
        WindowGroup("Storage Explorer — production UI review") { ViewerDetail(model: model) }
            .defaultSize(width: 600, height: 430).windowResizability(.contentMinSize)
    }
}
