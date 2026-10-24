import SwiftUI
import WidgetKit

struct L1Entry: TimelineEntry {
    let date: Date
    let snapshot: L1Snapshot
}

struct L1Provider: TimelineProvider {
    func placeholder(in context: Context) -> L1Entry { L1Entry(date: Date(), snapshot: .empty) }

    func getSnapshot(in context: Context, completion: @escaping (L1Entry) -> Void) {
        completion(L1Entry(date: Date(), snapshot: context.isPreview ? .empty : SharedSnapshot.read()))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<L1Entry>) -> Void) {
        let now = Date()
        let snapshot = SharedSnapshot.read()
        var entries = [L1Entry(date: now, snapshot: snapshot)]
        // Mark cached data stale without pretending future capacity readings exist.
        if let observed = snapshot.latest?.time {
            let staleAt = observed.addingTimeInterval(181)
            if staleAt > now { entries.append(L1Entry(date: staleAt, snapshot: snapshot)) }
        }
        completion(Timeline(entries: entries, policy: .after(now.addingTimeInterval(900))))
    }
}

struct L1WidgetView: View {
    let entry: L1Entry

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text("SSD空き容量").font(.caption)
            Text(entry.snapshot.capacityText(at: entry.date, previous: true))
                .font(.title2).bold().monospacedDigit().minimumScaleFactor(0.6)
            if !entry.snapshot.isCurrent(at: entry.date) {
                Text(entry.snapshot.statusText(at: entry.date))
                    .font(.caption2).foregroundStyle(.secondary).lineLimit(2)
            }
            L1Chart(samples: entry.snapshot.samples, now: entry.date, compact: true)
            if let time = entry.snapshot.latest?.time {
                Text(time, style: .time).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .containerBackground(.fill.tertiary, for: .widget)
        .widgetURL(SharedSnapshot.detailURL)
    }
}

@main
struct BlackboxWidget: Widget {
    let kind = SharedSnapshot.widgetKind

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: L1Provider()) { entry in L1WidgetView(entry: entry) }
            .configurationDisplayName("SSD空き容量")
            .description("BlackboxのL1履歴を表示します。Viewer常駐中にデータを更新します。")
            .supportedFamilies([.systemSmall, .systemMedium])
    }
}
