import SwiftUI

struct ViewerDetail: View {
    @ObservedObject var model: ViewerModel

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                Text("SSD空き容量").font(.title2)
                Spacer()
                Text(model.snapshot.capacityText(at: model.now, previous: true))
                    .font(.largeTitle).monospacedDigit()
                if !model.snapshot.isCurrent(at: model.now) { Text("前回値").foregroundStyle(.secondary) }
            }
            Text(model.snapshot.statusText(at: model.now)).foregroundStyle(.secondary)
            if let time = model.snapshot.latest?.time {
                Text("最終観測: \(time.formatted(date: .abbreviated, time: .standard))")
                    .font(.caption).foregroundStyle(.secondary)
            }
            L1Chart(samples: model.snapshot.samples, now: model.now)
                .frame(minHeight: 180)
            HStack {
                Text("直近24時間 · GiB").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("保存先を設定…") { model.selectSource() }.disabled(model.refreshing)
                Button("更新") { Task { await model.refresh() } }.disabled(model.refreshing)
            }
            if let message = model.configurationMessage { Text(message).font(.caption).foregroundStyle(.orange) }
            if let message = model.sharingMessage { Text(message).font(.caption).foregroundStyle(.orange) }
            Text("ウィンドウを閉じても更新を続けます。メニューバーの「終了」で停止します。")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(24)
        .frame(minWidth: 600, minHeight: 380)
    }
}
