import SwiftUI

struct ViewerDetail: View {
    @ObservedObject var model: ViewerModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("内蔵SSD 空き容量").font(.headline).foregroundStyle(.primary)
                Spacer()
                CapacityReadout(number: model.snapshot.capacityNumber(at: model.now, previous: true))
                if !model.snapshot.isCurrent(at: model.now) { Text("前回値").foregroundStyle(.secondary) }
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .frame(maxWidth: .infinity)
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
            if !model.snapshot.isCurrent(at: model.now) {
                Text(model.snapshot.statusText(at: model.now)).font(.callout).foregroundStyle(.secondary)
            }
            HStack {
                Text("時系列推移（24時間）").font(.headline).foregroundStyle(.primary)
                Spacer()
                if let time = model.snapshot.latest?.time {
                    Text("最終観測 \(L1DisplayFormat.hourMinute(time))")
                        .font(.caption).foregroundStyle(.secondary).monospacedDigit()
                        .accessibilityLabel("最終観測 \(L1DisplayFormat.timestamp(time))")
                }
                Button { Task { await model.refresh(forceWidgetReload: true) } } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless).help("更新").accessibilityLabel("更新")
                .disabled(model.refreshing)
            }
            VStack(spacing: 5) {
                HStack {
                    Text("\(L1DisplayFormat.monthDay(model.now.addingTimeInterval(-86_400))) ～ \(L1DisplayFormat.monthDay(model.now))")
                        .font(.caption).monospacedDigit().foregroundStyle(.secondary)
                    Spacer()
                    Text("[GiB]").font(.caption2).foregroundStyle(.secondary)
                }
                L1Chart(samples: model.snapshot.samples, now: model.now).frame(minHeight: 180)
            }
            HStack {
                Spacer()
                if model.snapshot.status == .unconfigured {
                    Button("保存先を設定…") { model.selectSource() }.disabled(model.refreshing)
                } else {
                    Button("設定") { model.selectSource() }
                        .buttonStyle(.borderless).font(.caption).disabled(model.refreshing)
                }
            }
            if let message = model.configurationMessage { Text(message).font(.caption).foregroundStyle(.orange) }
            if let message = model.sharingMessage { Text(message).font(.caption).foregroundStyle(.orange) }
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .frame(minWidth: 600, minHeight: 380)
    }
}
