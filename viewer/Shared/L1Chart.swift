import Charts
import SwiftUI

struct L1Chart: View {
    let samples: [L1Sample]
    let now: Date
    var compact = false

    private struct Point: Identifiable {
        let time: Date
        let gib: Double
        let segment: Int
        var id: Date { time }
    }

    private var points: [Point] {
        var result: [Point] = []
        var segment = 0
        var previous: Date?
        for sample in samples where sample.time >= now.addingTimeInterval(-86_400) && sample.time <= now {
            guard let bytes = sample.freeBytes else { segment += 1; previous = nil; continue }
            if let previous, sample.time.timeIntervalSince(previous) > 150 { segment += 1 }
            result.append(Point(time: sample.time, gib: Double(bytes) / 1_073_741_824, segment: segment))
            previous = sample.time
        }
        return result
    }

    var body: some View {
        Chart(points) { point in
            LineMark(x: .value("時刻", point.time), y: .value("空き容量 (GiB)", point.gib),
                     series: .value("区間", point.segment))
                .interpolationMethod(.linear)
        }
        .chartXScale(domain: now.addingTimeInterval(-86_400)...now)
        .chartYScale(domain: .automatic(includesZero: true))
        .chartXAxis(compact ? .hidden : .automatic)
        .chartYAxis(compact ? .hidden : .automatic)
        .chartLegend(.hidden)
        .overlay {
            if points.isEmpty { Text("直近24時間のデータがありません").font(.caption).foregroundStyle(.secondary) }
        }
        .accessibilityLabel("直近24時間のSSD空き容量、単位GiB")
    }
}
