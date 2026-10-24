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
    @Environment(\.widgetFamily) private var family

    var body: some View {
        L1WidgetContent(entry: entry, small: family == .systemSmall)
            .containerBackground(.fill.tertiary, for: .widget)
            .widgetURL(SharedSnapshot.detailURL)
    }
}

// The exact content is also rendered at constrained small / medium sizes by
// the local layout checker. No alternate user-facing mode is introduced.
struct L1WidgetContent: View {
    let entry: L1Entry
    let small: Bool

    private func observedTime(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: small ? 5 : 7) {
            L1Chart(samples: entry.snapshot.samples, now: entry.date, compact: true, lineColor: .orange)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            if let status = entry.snapshot.widgetStatusText(at: entry.date) {
                Text(status)
                    .font(.system(size: small ? 9 : 11))
                    .foregroundStyle(.secondary).lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(alignment: .firstTextBaseline, spacing: small ? 4 : 8) {
                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Image(systemName: "clock")
                    Text(entry.snapshot.latest.map { observedTime($0.time) } ?? "—")
                        .monospacedDigit()
                }
                .font(.system(size: small ? 10 : 11)).foregroundStyle(.secondary)
                .fixedSize()
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(entry.snapshot.latest.map { "最終観測 \(L1DisplayFormat.timestamp($0.time))" } ?? "最終観測なし")

                Spacer(minLength: small ? 4 : 8)

                HStack(alignment: .firstTextBaseline, spacing: 3) {
                    Text(entry.snapshot.capacityNumber(at: entry.date, previous: true))
                        .font(.system(size: small ? 24 : 32, weight: .regular))
                        .monospacedDigit().lineLimit(1).minimumScaleFactor(0.65)
                        .layoutPriority(1)
                    Text("GiB").font(.system(size: small ? 10 : 12)).foregroundStyle(.secondary)
                        .fixedSize()
                }
                .accessibilityElement(children: .combine)
            }
        }
    }
}

#if !WIDGET_LAYOUT_CHECK
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
#endif
