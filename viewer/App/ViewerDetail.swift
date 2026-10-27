import Charts
import SwiftUI

struct InfoButton: View {
    let title: String
    let message: String
    @State private var showing = false
    var body: some View {
        Button { showing.toggle() } label: {
            Image(systemName: "info.circle").font(.system(size: 11)).foregroundStyle(.secondary)
                .frame(width: 14, height: 16).contentShape(Rectangle())
        }.buttonStyle(.plain).help(message).accessibilityLabel(title)
            .popover(isPresented: $showing) {
                VStack(alignment: .leading, spacing: 10) {
                    Text(message).font(.callout).textSelection(.enabled)
                }.padding(16).frame(width: 310, alignment: .leading)
            }
    }
}
struct LegendFlow: Layout {
    var spacing: CGFloat = 12
    private func frames(_ subviews: Subviews, width: CGFloat) -> ([CGRect], CGFloat) {
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        var result: [CGRect] = []
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > width * 0.75 { x = 0; y += rowHeight + 7; rowHeight = 0 }
            result.append(CGRect(x: x, y: y, width: min(width, size.width), height: size.height))
            x += size.width + spacing; rowHeight = max(rowHeight, size.height)
        }
        for rowY in Set(result.map(\.minY)) {
            let rowWidth = result.filter { $0.minY == rowY }.map(\.maxX).max() ?? 0
            for index in result.indices where result[index].minY == rowY {
                result[index].origin.x += max(0, (width - rowWidth) / 2)
            }
        }
        return (result, y + rowHeight)
    }
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 568
        return CGSize(width: width, height: frames(subviews, width: width).1)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        for (view, frame) in zip(subviews, frames(subviews, width: bounds.width).0) {
            view.place(at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY), anchor: .topLeading, proposal: ProposedViewSize(frame.size))
        }
    }
}

struct ViewerDetail: View {
    @ObservedObject var model: ViewerModel
    @State private var change = false
    var body: some View {
        ScrollViewReader { outer in
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    dashboard(outer).id("dashboard")
                    if model.snapshot.status != .ready || !model.snapshot.isCurrent(at: model.now) {
                        Text(model.snapshot.statusText(at: model.now)).font(.caption).foregroundStyle(.secondary)
                    }
                    if let message = model.configurationMessage { Text(message).font(.caption).foregroundStyle(.orange) }
                    if let message = model.sharingMessage { Text(message).font(.caption).foregroundStyle(.orange) }
                    l1(outer).id("L1")
                    ForEach(StorageLayer.allCases, id: \.self) { layer in
                        StorageExplorerSection(model: model, layer: layer, back: { jump(outer, "dashboard") }, jumpTo: { jump(outer, $0) })
                            .id(layer.rawValue)
                    }
                }.padding(16)
            }
        }.frame(minWidth: 600, minHeight: 380)
        .toolbar {
            ToolbarItem {
                Menu {
                    Button("データを更新") { Task { await model.refresh(forceWidgetReload: true) } }
                    Button("初期表示に戻す") { change = false; model.resetStorage() }
                    Button("設定…") { model.selectSource() }
                } label: { Image(systemName: "slider.horizontal.3") }
                    .help("更新・設定").accessibilityLabel("更新・設定").disabled(model.refreshing)
            }
        }
    }
    private func jump(_ proxy: ScrollViewProxy, _ id: String) {
        withAnimation(.easeInOut(duration: 0.25)) { proxy.scrollTo(id, anchor: .top) }
    }
    private func dashboard(_ outer: ScrollViewProxy) -> some View {
        StorageDashboard(model: model) { jump(outer, $0) }
    }
    private func l1(_ outer: ScrollViewProxy) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("SSD空き容量").font(.headline)
                Text("1分間隔").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Dashboardへ ↑") { jump(outer, "dashboard") }.buttonStyle(.plain).foregroundStyle(.blue)
            }
            Picker("表示", selection: $change) {
                Text("SSD空き容量").tag(false)
                Text("空き容量の増減").tag(true)
            }.pickerStyle(.segmented)
            HStack {
                Text("\(StorageMath.stamp(model.now.addingTimeInterval(-86400))) – \(StorageMath.stamp(model.now))")
                Spacer(); Text("GiB")
            }.font(.caption2).foregroundStyle(.secondary)
            L1Chart(samples: (model.reading?.predecessor.map { [$0] } ?? []) + model.snapshot.samples,
                    now: model.now, lineColor: .orange, midnightOnly: true, showsChanges: change).frame(height: 155)
        }.padding(14).background(.background, in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(.quaternary))
    }
}

struct StorageDashboard: View {
    @ObservedObject var model: ViewerModel
    let jumpTo: (String) -> Void
    var body: some View {
        let reading = model.reading
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Dashboard").font(.headline)
                Spacer()
                Text("最新観測 \(StorageMath.stamp(reading?.latestValid?.time))").font(.system(size: 10)).foregroundStyle(.secondary)
            }
            HStack(spacing: 8) {
                Button { jumpTo("L1") } label: {
                    card("現在のSSD空き容量", number: reading?.latestValid?.freeBytes.map { String(format: "%.2f GiB", Double($0)/1_073_741_824) } ?? "データなし")
                }
                Button { jumpTo("L3") } label: {
                    card("1時間前からの容量増加", number: reading?.hourComparison.map { StorageMath.signed($0.usageGiB) } ?? "データなし", comparison: reading?.hourComparison)
                }
                Button { jumpTo("L2") } label: {
                    card("24時間前からの容量増加", number: reading?.dayComparison.map { StorageMath.signed($0.usageGiB) } ?? "データなし", comparison: reading?.dayComparison)
                }
            }.buttonStyle(.plain)
        }
    }
    private func card(_ title: String, number: String, comparison: L1Comparison? = nil) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 12)).lineLimit(1).minimumScaleFactor(0.8).frame(maxWidth: .infinity, alignment: .leading)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                if let comparison {
                    Text(StorageMath.stamp(comparison.previous.time)).font(.system(size: 8)).foregroundStyle(.secondary).monospacedDigit()
                        .lineLimit(1).help("比較に使用した観測日時")
                        .accessibilityLabel("比較元の観測日時 \(L1DisplayFormat.timestamp(comparison.previous.time))")
                }
                Spacer(minLength: 0)
                Text(number).font(.system(size: 22, weight: .medium)).monospacedDigit()
                    .lineLimit(1).minimumScaleFactor(0.65).layoutPriority(1)
            }
        }.padding(.horizontal, 10).padding(.vertical, 9)
            .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 6))
    }
}

struct StorageExplorerSection: View {
    @ObservedObject var model: ViewerModel
    let layer: StorageLayer
    let back: () -> Void
    let jumpTo: (String) -> Void
    @State private var state: StorageSelection
    @State private var limitMessage = false
    @State private var innerJump = 0
    init(model: ViewerModel, layer: StorageLayer, back: @escaping () -> Void, jumpTo: @escaping (String) -> Void, initialSelection: StorageSelection? = nil) {
        self.model = model; self.layer = layer; self.back = back; self.jumpTo = jumpTo
        _state = State(initialValue: initialSelection ?? StorageSelection(layer: layer))
    }
    private var data: StorageSnapshot {
        guard let reading = model.reading else { return .empty }
        if let loaded = reading.storageWindows[layer], loaded != window { return .empty }
        return layer == .l2 ? reading.storage : reading.storageL3
    }
    private var window: StorageWindow { model.storageWindows[layer] ?? StorageWindow(days: layer.defaultDays) }
    private var end: Date { min(window.end ?? model.now, model.now) }
    private var day: StorageDay? {
        if let selected = state.selectedDate {
            return data.days.min { abs($0.date.timeIntervalSince(selected)) < abs($1.date.timeIntervalSince(selected)) }
        }
        return data.days.last { $0.current?.completed == true } ?? data.days.last
    }
    private var ranked: [StorageTarget] { day.map { data.ranking(on: $0) } ?? data.targets }
    private var maximum: Double { max(0.001, ranked.compactMap { day?.delta(path: $0.path, parent: $0).gib }.map(abs).max() ?? 1) }
    private var series: [StorageTarget] { data.targets.filter { state.series.contains($0.id) }.sorted { $0.path < $1.path } }
    private var singlePath: String? { state.trail.last }
    private var singleTarget: StorageTarget? { singlePath.map(StorageTarget.init) }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(layer.title).font(.headline)
                Text(layer.cadence).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Dashboardへ ↑", action: back).buttonStyle(.plain).foregroundStyle(.blue)
            }
            HStack(spacing: 3) {
                ForEach(StorageMode.allCases, id: \.self) { mode in
                    Button { state.mode = mode } label: {
                        Text(mode.rawValue).frame(maxWidth: .infinity).padding(.vertical, 5)
                            .background(state.mode == mode ? Color.primary.opacity(0.12) : .clear,
                                        in: RoundedRectangle(cornerRadius: 5))
                            .contentShape(Rectangle())
                    }.buttonStyle(.plain).accessibilityLabel(mode.rawValue)
                        .accessibilityValue(state.mode == mode ? "選択中" : "未選択")
                }
            }.padding(3).background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
            periodControls
            if state.mode == .single { singleNavigation }
            HStack {
                Text(state.mode == .single ? singleTarget?.name ?? "対象なし" : state.metric.axisTitle).font(.subheadline.weight(.medium))
                Spacer()
                Text("\(state.metric.axisTitle)（GiB）").font(.caption).foregroundStyle(.secondary)
                Button { state.metric = state.metric.alternate } label: {
                    Label(state.metric.alternate.rawValue, systemImage: "arrow.left.arrow.right")
                }.help("グラフを\(state.metric.alternate.rawValue)に切替")
                    .accessibilityLabel("\(state.metric.alternate.rawValue)に切替")
                    .accessibilityValue("現在は\(state.metric.rawValue)表示")
            }
            plot.frame(height: 230).id("plot-" + layer.rawValue)
            legend
            HStack {
                Text("選択観測：\(StorageMath.stamp(day?.current?.time))").font(.caption)
                if let day { InfoButton(title: "比較の詳細", message: day.comparisonText) }
                Spacer()
                icon("list.bullet", "一覧へ") { innerJump += 1; jumpTo("ranking-" + layer.rawValue) }
            }
            if !data.days.contains(where: { $0.current?.completed == true }) {
                Text("この期間の定期観測データなし\(layer == .l3 ? "（毎時生履歴のみ表示）" : "")")
                    .font(.callout).foregroundStyle(.secondary)
            }
            if state.mode == .single {
                if let path = singlePath, StoragePlot.points(data.days, path: path, metric: state.metric).isEmpty {
                    Text("この対象の有効な\(state.metric.rawValue)記録なし").font(.caption).foregroundStyle(.secondary)
                }
                singleChildren.id("ranking-" + layer.rawValue)
            }
            else {
                HStack {
                    Text("増減ランキング").font(.subheadline.weight(.medium))
                    Spacer(); Text("\(state.series.count)/5系列").font(.caption)
                    icon("chart.xyaxis.line", "グラフへ") { jumpTo("plot-" + layer.rawValue) }
                }
                if limitMessage { Text("最大5系列です。選択中の系列を解除してください。").font(.caption).foregroundStyle(.orange) }
                ranking.id("ranking-" + layer.rawValue)
            }
        }.padding(14).background(.background, in: RoundedRectangle(cornerRadius: 9))
            .overlay(RoundedRectangle(cornerRadius: 9).stroke(.quaternary))
            .onChange(of: data.targets, initial: true) { _, _ in state.initialize(data) }
            .onChange(of: model.storageRevision) { _, _ in state.initialize(data) }
            .onChange(of: model.reading?.l1.generatedAt) { _, _ in state.initialize(data) }
            .onChange(of: model.resetID) { _, _ in state = StorageSelection(layer: layer); state.initialize(data); limitMessage = false }
    }
    private var periodControls: some View {
        ViewThatFits(in: .horizontal) {
            HStack { periodText; Spacer(minLength: 4); periodButtons }
            VStack(alignment: .leading) { periodText; HStack { Spacer(); periodButtons } }
        }
    }
    private var periodText: some View {
        Text("\(StorageMath.stamp(Calendar.current.date(byAdding: .day, value: -window.days, to: end))) – \(StorageMath.stamp(end))")
            .font(.system(size: 10)).foregroundStyle(.secondary).monospacedDigit().fixedSize()
    }
    private var periodButtons: some View {
        HStack(spacing: 5) {
            Picker("期間", selection: Binding(get: { window.days }, set: { days in
                var next = window; next.days = days
                if next.end == nil { next.end = end }
                model.setWindow(next, for: layer)
            })) {
                ForEach(layer.periodDays, id: \.self) { days in
                    Text(days == 1 ? "24時間" : "\(days)日間").tag(days)
                }
            }.labelsHidden().frame(width: 84)
            Button("← 前") { model.setWindow(window.moving(-1, now: model.now), for: layer) }
            Button("次 →") { model.setWindow(window.moving(1, now: model.now), for: layer) }.disabled(window.end == nil)
            Button("最新へ") { model.setWindow(StorageWindow(days: window.days), for: layer) }.disabled(window.end == nil)
        }.controlSize(.small)
    }
    private var singleNavigation: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Picker("監視対象", selection: Binding(get: { state.trail.first ?? "" }, set: { state.trail = [$0] })) {
                    ForEach(data.targets) { Text($0.name).tag($0.path) }
                }.frame(maxWidth: 260)
                Text(singlePath ?? "").font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary).textSelection(.enabled)
            }
            HStack(alignment: .top) {
                Button("↑ 親へ") { if state.trail.count > 1 { state.trail.removeLast() } }.disabled(state.trail.count < 2)
                LegendFlow(spacing: 4) {
                    ForEach(Array(state.trail.enumerated()), id: \.offset) { index, path in
                        Button(StorageTarget(path).name + (index < state.trail.count-1 ? " /" : "")) {
                            state.trail = Array(state.trail.prefix(index+1))
                        }.buttonStyle(.plain).foregroundStyle(.blue)
                    }
                }
            }.font(.caption)
        }
    }
    private var singleChildren: some View {
        VStack(alignment: .leading) {
            if let path = singlePath, let day {
                StorageChildren(target: StorageTarget(path + "/*"), day: day, single: true,
                                query: queryBinding(path), page: pageBinding(path)) { state.trail.append($0) }
            } else { Text("サブフォルダ記録なし").font(.caption).foregroundStyle(.secondary) }
        }
    }
    private func queryBinding(_ path: String) -> Binding<String> {
        Binding(get: { state.childQueries[path] ?? "" }, set: { state.childQueries[path] = $0 })
    }
    private func pageBinding(_ path: String) -> Binding<Int> {
        Binding(get: { state.childPages[path] ?? 0 }, set: { state.childPages[path] = $0 })
    }
    private func icon(_ symbol: String, _ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: symbol).frame(width: 26, height: 24) }
            .buttonStyle(.plain).foregroundStyle(.blue).help(title).accessibilityLabel(title)
    }
    private func dateLabel(_ date: Date) -> String { StorageMath.stamp(date) }
    private var plottedTargets: [StorageTarget] { state.mode == .single ? singleTarget.map { [$0] } ?? [] : series }
    private var plot: some View {
        Chart {
            RuleMark(y: .value("0 GiB", 0)).foregroundStyle(.secondary.opacity(0.25))
            if let day { RuleMark(x: .value("選択日時", day.date)).lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3])).foregroundStyle(.secondary.opacity(0.5)) }
            if state.metric == .delta {
                ForEach(data.days) { day in
                    if let value = day.usageGiB {
                        BarMark(x: .value("日時", day.date), y: .value("SSD使用量増減", value),
                                width: .fixed(max(1, min(25, 300 / Double(max(1, data.days.count))))))
                            .foregroundStyle(.gray.opacity(0.28))
                    }
                }
            }
            // All lines first; opaque-backed markers are drawn above every series.
            ForEach(plottedTargets) { target in
                ForEach(StoragePlot.segments(data.days, path: target.path, metric: state.metric)) { segment in
                    ForEach([segment.before, segment.after]) { point in
                        LineMark(x: .value("日時", point.date), y: .value(state.metric.axisTitle, point.value),
                                 series: .value("区間", target.id + String(segment.id.timeIntervalSince1970)))
                            .foregroundStyle(plotColor(target.id).opacity(segment.style == .reference ? 0.4 : 1))
                            .lineStyle(StrokeStyle(lineWidth: segment.style == .incomparable ? 1 : 1.8,
                                                   lineCap: .round, dash: segment.style == .incomparable ? [1, 4] : []))
                    }
                }
            }
            ForEach(plottedTargets) { target in
                ForEach(StoragePlot.points(data.days, path: target.path, metric: state.metric)) { point in
                    PointMark(x: .value("日時", point.date), y: .value(state.metric.axisTitle, point.value))
                        .foregroundStyle(plotColor(target.id))
                        .symbol {
                            if point.partial {
                                StorageDiamond().fill(.background)
                                    .overlay(StorageDiamond().stroke(plotColor(target.id), lineWidth: 0.75)).frame(width: 6, height: 6)
                            } else { Circle().fill(plotColor(target.id)).frame(width: 5, height: 5) }
                        }
                        .accessibilityLabel("\(target.name)、\(point.partial ? (state.metric == .capacity ? "一部取得、取得済み容量" : "一部取得") : "正常取得")、\(point.reference ? "参考比較" : "")")
                        .accessibilityValue(state.metric.axisTitle + " " + (state.metric == .delta ? StorageMath.signed(point.value) : String(format: "%.2f GiB", point.value)))
                }
            }
        }
        .chartXScale(domain: plotDomain)
        .chartYScale(domain: .automatic(includesZero: true))
        .chartYAxis { AxisMarks(position: .trailing) }
        .chartXAxis {
            AxisMarks(values: axisDates) { axis in
                if let date = axis.as(Date.self) {
                    AxisValueLabel(centered: false, anchor: date == axisDates.first ? .topLeading : date == axisDates.last ? .topTrailing : .top) {
                        VStack(spacing: 0) {
                            Text(L1DisplayFormat.monthDay(date))
                            Text(L1DisplayFormat.hourMinute(date))
                        }.font(.system(size: 10)).monospacedDigit()
                    }
                }
            }
        }

        .chartLegend(.hidden)
        .chartOverlay { proxy in
            GeometryReader { geometry in
                Rectangle().fill(.clear).contentShape(Rectangle()).gesture(SpatialTapGesture().onEnded { event in
                    guard let plot = proxy.plotFrame else { return }
                    let frame = geometry[plot]
                    guard frame.contains(event.location), let date: Date = proxy.value(atX: event.location.x-frame.minX) else { return }
                    state.selectedDate = data.days.min { abs($0.date.timeIntervalSince(date)) < abs($1.date.timeIntervalSince(date)) }?.date
                })
            }
        }
        .overlay(alignment: .topLeading) {
            if state.metric == .delta, let reason = day?.usageReason {
                InfoButton(title: "SSD使用量増減の欠測理由", message: reason).padding(4)
            }
        }
        .accessibilityLabel(state.metric == .capacity ? "容量時系列、GiB" : "容量差分、GiB")
        .accessibilityAction(named: "前の観測を選択") { moveObservation(-1) }
        .accessibilityAction(named: "次の観測を選択") { moveObservation(1) }
    }
    private var plotDomain: ClosedRange<Date> {
        let first = data.days.first?.date ?? Calendar.current.date(byAdding: .day, value: -window.days, to: end)!
        let last = data.days.last?.date ?? end
        let padding: TimeInterval = layer == .l2 ? 43200 : 1800
        return first.addingTimeInterval(-padding)...last.addingTimeInterval(padding)
    }
    private var axisDates: [Date] {
        let dates = data.days.map(\.date)
        guard !dates.isEmpty else { return [] }
        return Array(Set([0, dates.count/3, dates.count*2/3, dates.count-1])).sorted().map { dates[$0] }
    }
    private func moveObservation(_ offset: Int) {
        guard let date = day?.date, let index = data.days.firstIndex(where: { $0.date == date }) else { return }
        state.selectedDate = data.days[min(max(index+offset, 0), data.days.count-1)].date
    }
    private func plotColor(_ id: String) -> Color { state.mode == .single ? .orange : color(id) }
    private var legend: some View {
        VStack(alignment: .leading, spacing: 5) {
            LegendFlow(spacing: 12) {
                if state.metric == .delta { Text("▰ SSD使用量増減").foregroundStyle(.secondary) }
                ForEach(plottedTargets) { target in
                    HStack(spacing: 5) {
                        Circle().fill(plotColor(target.id)).frame(width: 5, height: 5)
                        Text(target.name).foregroundStyle(.secondary)
                    }
                }
            }
            Text("薄い実線：参考比較（取得済み範囲）。点線：比較不可の補助線、途中の値なし。● 正常取得／◇ 一部取得。")
                .foregroundStyle(.secondary)
                .help(StorageDelta.referenceExplanation)
        }.font(.system(size: 10))
    }
    private var ranking: some View {
        ScrollViewReader { inner in
            ScrollView {
                VStack(spacing: 0) {
                    ForEach(ranked) { target in
                        rankRow(target).id(target.id)
                        if state.expanded == target.id, let day {
                            StorageChildren(target: target, day: day, query: queryBinding(target.path), page: pageBinding(target.path)).padding(.horizontal, 14).padding(.vertical, 10)
                                .background(Color.primary.opacity(0.025))
                        }
                        Divider()
                    }
                }
            }.frame(height: 300).clipped()
                .onChange(of: innerJump) { _, _ in scrollToParent(inner) }
                .onChange(of: day?.date) { _, _ in scrollToParent(inner) }
        }
    }
    private func scrollToParent(_ proxy: ScrollViewProxy) {
        if let id = state.expanded ?? ranked.first?.id { withAnimation { proxy.scrollTo(id, anchor: .top) } }
    }
    private func color(_ id: String) -> Color {
        [Color.orange, .blue, .purple, .green, .pink][(series.firstIndex(where: { $0.id == id }) ?? 0) % 5]
    }
    private func toggle(_ id: String) { limitMessage = !state.toggle(id) }
    private func rankRow(_ target: StorageTarget) -> some View {
        let delta = day?.delta(path: target.path, parent: target) ?? .missing("定期観測なし")
        let children = day?.children(target) ?? []
        let capacity = day?.capacity(target.path)
        let capacityText = capacity.flatMap { row in row.bytes.map { (row.status == "partial" ? "取得済み " : "") + String(format: "%.2f GiB", Double($0)/1_073_741_824) } } ?? "—"
        return HStack(spacing: 7) {
            Button { toggle(target.id) } label: {
                Image(systemName: state.series.contains(target.id) ? "checkmark.square.fill" : "square").font(.system(size: 12))
                    .foregroundStyle(state.series.contains(target.id) ? color(target.id) : .secondary).frame(width: 18, height: 26)
            }.buttonStyle(.plain).accessibilityLabel("\(target.name)の系列を切替")
            VStack(spacing: 2) {
                HStack(spacing: 5) {
                    Button { toggle(target.id) } label: {
                        HStack(spacing: 8) {
                            Text(target.name).font(.system(size: 12)).lineLimit(1).frame(maxWidth: 140, alignment: .leading)
                            Text(target.path).font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
                                .lineLimit(1).truncationMode(.middle).frame(maxWidth: .infinity, alignment: .leading)
                        }.contentShape(Rectangle())
                    }.buttonStyle(.plain).foregroundStyle(.primary).help("\(target.path)\n選択観測の容量：\(capacityText)")
                    HStack(spacing: 3) {
                        Text(delta.displayText).font(.system(size: 12, weight: .medium)).monospacedDigit()
                        if let reason = delta.explanation { InfoButton(title: delta.isReference ? "参考差分について" : "比較できない理由", message: reason) }
                    }.frame(width: 140, alignment: .trailing)
                }
                rankBar(delta.gib).opacity(delta.isReference ? 0.45 : 1).frame(height: 3)
            }
            Button { state.expanded = state.expanded == target.id ? nil : target.id } label: {
                HStack(spacing: 3) {
                    Text(target.includesChildren ? (children.isEmpty ? "—" : "\(children.count)件") : "—").font(.system(size: 10)).monospacedDigit()
                    Image(systemName: state.expanded == target.id ? "chevron.down" : "chevron.right").font(.system(size: 9))
                }.foregroundStyle(.secondary).frame(width: 48, height: 26, alignment: .trailing)
            }.buttonStyle(.plain).disabled(!target.includesChildren).accessibilityLabel("\(target.name)の子項目")
        }.padding(.horizontal, 8).padding(.vertical, 4)
    }
    private func rankBar(_ value: Double?) -> some View {
        GeometryReader { geometry in
            let half = geometry.size.width / 2
            Path { path in path.move(to: CGPoint(x: half, y: 0)); path.addLine(to: CGPoint(x: half, y: 3)) }.stroke(.secondary.opacity(0.5))
            if let value {
                let width = abs(value)/maximum * max(0, half-2)
                Rectangle().fill((value >= 0 ? Color.orange : Color.blue).opacity(0.7)).frame(width: width, height: 3).offset(x: value >= 0 ? half : half-width)
            }
        }
    }

}

private struct StorageChildren: View {
    let target: StorageTarget
    let day: StorageDay
    var single = false
    @Binding var query: String
    @Binding var page: Int
    var open: ((String) -> Void)? = nil
    private var children: [StorageChild] {
        single ? day.subfolders(target.path).map { StorageChild(path: $0.path, delta: day.comparison($0.path)) } : day.children(target)
    }
    private var filtered: [StorageChild] { children.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) } }
    private var pageCount: Int { max(1, (filtered.count+9)/10) }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\(target.name)のサブフォルダ").font(.system(size: 12, weight: .medium))
            if !children.isEmpty {
                HStack(spacing: 8) {
                    TextField("子項目をフィルタ", text: $query).textFieldStyle(.plain).font(.system(size: 12))
                    Text("\(filtered.count) / \(children.count)件").font(.system(size: 10)).monospacedDigit().foregroundStyle(.tertiary)
                }.padding(.horizontal, 9).padding(.vertical, 6)
                    .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 5))
                    .overlay(RoundedRectangle(cornerRadius: 5).stroke(.quaternary))
            }
            ForEach(Array(filtered.dropFirst(min(page, pageCount-1)*10).prefix(10))) { child in
                HStack {
                    Text(child.name).lineLimit(1).truncationMode(.middle).help(child.path)
                    Spacer()
                    if single, let row = day.capacity(child.path), let bytes = row.bytes {
                        Text((row.status == "partial" ? "取得済み " : "") + String(format: "%.2f GiB", Double(bytes)/1_073_741_824)).monospacedDigit()
                    }
                    Text(child.delta.displayText).monospacedDigit()
                    if let reason = child.delta.explanation { InfoButton(title: child.delta.isReference ? "参考差分について" : "比較できない理由", message: reason) }
                    else { Color.clear.frame(width: 14, height: 14) }
                    if single {
                        Button("開く →") { open?(child.path) }
                            .disabled(day.capacity(child.path)?.kind != "directory")
                    }
                }.font(.system(size: 12)).frame(height: 20)
            }
            if filtered.isEmpty { Text(children.isEmpty ? "サブフォルダ記録なし" : "一致する子項目がありません").font(.caption).foregroundStyle(.secondary) }
            if !children.isEmpty {
                HStack(spacing: 12) {
                    Spacer()
                    Button("前へ") { page = max(0, page-1) }.disabled(page == 0)
                    Text("\(min(page+1, pageCount)) / \(pageCount)").monospacedDigit().foregroundStyle(.secondary)
                    Button("次へ") { page += 1 }.disabled(page+1 >= pageCount)
                }.buttonStyle(.plain).font(.system(size: 10)).padding(.top, 3)
            }
        }.onChange(of: query) { _, _ in page = 0 }
            .onChange(of: day.date) { _, _ in page = 0 }
    }
}

private struct StorageDiamond: Shape {
    func path(in rect: CGRect) -> Path {
        Path { p in
            p.move(to: CGPoint(x: rect.midX, y: rect.minY))
            p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
            p.addLine(to: CGPoint(x: rect.midX, y: rect.maxY))
            p.addLine(to: CGPoint(x: rect.minX, y: rect.midY)); p.closeSubpath()
        }
    }
}
