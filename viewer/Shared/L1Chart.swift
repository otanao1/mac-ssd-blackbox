import Charts
import SwiftUI

struct L1Chart: View {
    let samples: [L1Sample]
    let now: Date
    var compact = false
    // Linear is the production default. The comparison renderer can request
    // monotone without changing the UI or introducing a user setting.
    var interpolation: InterpolationMethod = .linear
    var lineColor: Color = .accentColor

    private var points: [L1PlotPoint] { L1PlotData.points(samples, now: now) }

    var body: some View {
        let plotPoints = points
        let singletons = Set(Dictionary(grouping: plotPoints, by: \.segment)
            .filter { $0.value.count == 1 }.keys)
        let axisDates = L1PlotData.axisDates(now: now)
        Chart {
            if !compact {
                ForEach(axisDates, id: \.self) { date in
                    let midnight = Calendar.current.component(.hour, from: date) == 0
                    // RuleMarks are confined to the plot. AxisGridLine can
                    // extend through multiline axis labels in Swift Charts.
                    RuleMark(x: .value("時刻", date))
                        .lineStyle(StrokeStyle(lineWidth: midnight ? 1 : 0.5, dash: midnight ? [] : [3, 3]))
                        .foregroundStyle(Color.secondary.opacity(midnight ? 0.5 : 0.3))
                }
            }
            ForEach(plotPoints) { point in
                LineMark(x: .value("時刻", point.time), y: .value("空き容量 (GiB)", point.gib),
                         series: .value("区間", point.segment))
                    .interpolationMethod(interpolation)
                    .foregroundStyle(lineColor)
                if singletons.contains(point.segment) {
                    // A LineMark with a single observation has no visible line.
                    // Preserve that observation without connecting across the gap.
                    PointMark(x: .value("時刻", point.time), y: .value("空き容量 (GiB)", point.gib))
                        .symbolSize(compact ? 6 : 12)
                        .foregroundStyle(lineColor)
                }
            }
        }
        .chartXScale(domain: now.addingTimeInterval(-86_400)...now)
        .chartYScale(domain: .automatic(includesZero: true))
        .chartXAxis {
            if !compact {
                AxisMarks(values: axisDates) { value in
                    if let date = value.as(Date.self) {
                        let nearStart = date.timeIntervalSince(now.addingTimeInterval(-86_400)) < 2700
                        let nearEnd = now.timeIntervalSince(date) < 2700
                        AxisValueLabel(centered: false, anchor: nearStart ? .topLeading : (nearEnd ? .topTrailing : .top),
                                       collisionResolution: .disabled) {
                            Text(L1DisplayFormat.hourMinute(date)).font(.caption2).monospacedDigit()
                        }
                    }
                }
            }
        }
        .chartYAxis {
            if !compact { AxisMarks(position: .trailing) }
        }
        .chartLegend(.hidden)
        .overlay {
            if points.isEmpty { Text("直近24時間のデータがありません").font(.caption).foregroundStyle(.secondary) }
        }
        .accessibilityLabel("直近24時間のSSD空き容量、単位GiB")
    }
}

struct CapacityReadout: View {
    let number: String
    var size: CGFloat = 38

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Text(number).font(.system(size: size, weight: .regular)).monospacedDigit()
                .lineLimit(1).minimumScaleFactor(0.65).layoutPriority(1)
            Text("GiB").font(.system(size: 13, weight: .regular)).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
    }
}
