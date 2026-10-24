import AppKit
import Charts
import SwiftUI

// A local review artifact only. Pass a display-cache JSON and an output PNG;
// this does not query SQLite or add a comparison mode to the application.
@main
struct RenderChartComparison {
    @MainActor static func main() throws {
        let snapshot = try JSONDecoder().decode(L1Snapshot.self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
        let content = VStack(alignment: .leading, spacing: 22) {
            Text("同じ実観測データの補間比較（欠測区間は両方とも分断）").font(.headline)
            Text("直線：現在の採用方式").font(.subheadline)
            L1Chart(samples: snapshot.samples, now: snapshot.generatedAt).frame(height: 220)
            Text("単調三次補間：比較用。観測点間の曲がり方が変わる").font(.subheadline)
            L1Chart(samples: snapshot.samples, now: snapshot.generatedAt, interpolation: .monotone).frame(height: 220)
        }
        .padding(24).frame(width: 1000).background(Color(nsColor: .windowBackgroundColor))
        .environment(\.colorScheme, .dark)
        let renderer = ImageRenderer(content: content)
        renderer.scale = 2
        guard let cg = renderer.cgImage else { fatalError("Chart rendering failed") }
        let bitmap = NSBitmapImageRep(cgImage: cg)
        guard let png = bitmap.representation(using: .png, properties: [:]) else { fatalError("PNG encoding failed") }
        try png.write(to: URL(fileURLWithPath: CommandLine.arguments[2]), options: .atomic)
        print("Rendered comparison of the same real samples; database unchanged")
    }
}
