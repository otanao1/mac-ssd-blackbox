import AppKit
import SwiftUI

// Compile with -D WIDGET_LAYOUT_CHECK alongside the production Widget source.
// Uses display-cache data only and never queries the monitoring database.
@main
struct RenderWidgetLayout {
    @MainActor static func main() throws {
        let original = try JSONDecoder().decode(L1Snapshot.self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
        let now = original.generatedAt
        func variant(_ bytes: Int64?, status: L1Snapshot.Status = .ready, age: Double = 30) -> L1Snapshot {
            L1Snapshot(version: 1, generatedAt: now, samples: original.samples,
                       latest: bytes.map { L1Sample(time: now.addingTimeInterval(-age), freeBytes: $0) }, status: status)
        }
        let cases: [(String, L1Snapshot)] = [
            ("通常", variant(original.latest?.freeBytes ?? 12_345_000_000)),
            ("3桁容量・縮小", variant(245_296_025_600)),
            ("古い表示", variant(original.latest?.freeBytes ?? 0, age: 600)),
            ("取得失敗", variant(original.latest?.freeBytes ?? 0, status: .unavailable)),
            ("未設定", .empty)
        ]
        let content = VStack(alignment: .leading, spacing: 16) {
            Text("Widgetレイアウト確認：小164×164pt／中344×164pt").font(.headline)
            ForEach(Array(cases.enumerated()), id: \.offset) { _, item in
                VStack(alignment: .leading, spacing: 5) {
                    Text(item.0).font(.caption)
                    HStack(alignment: .top, spacing: 16) {
                        tile(item.1, now: now, small: true)
                        tile(item.1, now: now, small: false)
                    }
                }
            }
        }
        .padding(20).background(Color(nsColor: .windowBackgroundColor))
        .environment(\.colorScheme, .dark)
        let renderer = ImageRenderer(content: content)
        renderer.scale = 2
        guard let cg = renderer.cgImage,
              let png = NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:]) else {
            fatalError("Widget layout rendering failed")
        }
        try png.write(to: URL(fileURLWithPath: CommandLine.arguments[2]), options: .atomic)
        print("Rendered production Widget content for small / medium and five data states")
    }

    @MainActor static func tile(_ snapshot: L1Snapshot, now: Date, small: Bool) -> some View {
        L1WidgetContent(entry: L1Entry(date: now, snapshot: snapshot), small: small)
            .frame(width: small ? 132 : 312, height: 132)
            .padding(16)
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 20))
    }
}
