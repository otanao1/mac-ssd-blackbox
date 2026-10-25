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
    @State private var selection: Set<String> = []
    @State private var initialized = false
    @State private var selectedDate: Date?
    @State private var expanded: String?
    @State private var limitMessage = false
    @State private var innerJump = 0
    private var data: StorageSnapshot { model.reading?.storage ?? .empty }
    private var day: StorageDay? {
        if let selectedDate, let day = data.days.first(where: { $0.date == selectedDate }) { return day }
        if let latest = data.latest, let day = data.days.first(where: { Calendar.current.isDate($0.date, inSameDayAs: latest.time) }) { return day }
        return data.days.last
    }
    private var ranked: [StorageTarget] { day.map { data.ranking(on: $0) } ?? data.targets }
    private var maximum: Double { max(0.001, ranked.compactMap { day?.delta(path: $0.path, parent: $0).gib }.map(abs).max() ?? 1) }
    private var series: [StorageTarget] { data.targets.filter { selection.contains($0.id) }.sorted { $0.path < $1.path } }

    var body: some View {
        ScrollViewReader { outer in
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    dashboard { jump(outer, "ranking") }
                    if model.snapshot.status != .ready || !model.snapshot.isCurrent(at: model.now) {
                        Text(model.snapshot.statusText(at: model.now)).font(.caption).foregroundStyle(.secondary)
                    }
                    if let message = model.configurationMessage { Text(message).font(.caption).foregroundStyle(.orange) }
                    if let message = model.sharingMessage { Text(message).font(.caption).foregroundStyle(.orange) }
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("Storage Explorer").font(.title3.weight(.semibold))
                            Spacer()
                            Text("L2").font(.caption).foregroundStyle(.secondary)
                            icon("chart.xyaxis.line", "7日間グラフへ") { jump(outer, "daily") }
                        }.id("ranking")
                        if let day {
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text("\(dateLabel(day.date)) の増減").font(.headline)
                                Text(day.previous?.completed == true ? "比較対象は\(dateLabel(day.previous!.time))" : "比較対象なし")
                                    .font(.caption).foregroundStyle(.secondary)
                                InfoButton(title: "比較の詳細", message: day.comparisonText)
                                Spacer(minLength: 0)
                                Text("\(selection.count)/5系列").font(.caption2).foregroundStyle(.tertiary)
                            }
                        }
                        if limitMessage { Text("最大5系列です。選択中の系列を解除してください。").font(.caption).foregroundStyle(.orange) }
                        if data.targets.isEmpty { Text("L2の観測記録がありません").font(.callout).foregroundStyle(.secondary) }
                        else { ranking }
                        HStack {
                            Text("日次使用量増減（7日間）").font(.headline)
                            Spacer()
                            Text("[GiB]").font(.caption2).foregroundStyle(.secondary)
                            icon("list.bullet", "ランキングへ") { innerJump += 1; jump(outer, "ranking") }
                        }.id("daily")
                        daily.frame(height: 230)
                        legend
                    }.padding(.top, 12)
                }.padding(16)
            }
        }.frame(minWidth: 600, minHeight: 380)
        .toolbar {
            ToolbarItem {
                Menu {
                    Button("データを更新") { Task { await model.refresh(forceWidgetReload: true) } }
                    Button("設定…") { model.selectSource() }
                } label: { Image(systemName: "slider.horizontal.3") }
                    .help("更新・設定").accessibilityLabel("更新・設定").disabled(model.refreshing)
            }
        }
        .onChange(of: data.targets, initial: true) { _, targets in
            if targets.isEmpty { selection = []; initialized = false; expanded = nil; return }
            if !initialized && !targets.isEmpty {
                selection = Set(ranked.prefix(3).map(\.id)); initialized = true
            } else { selection.formIntersection(Set(targets.map(\.id))) }
            if let expanded, !targets.contains(where: { $0.id == expanded }) { self.expanded = nil }
        }
    }
    private func jump(_ proxy: ScrollViewProxy, _ id: String) { withAnimation(.easeInOut(duration: 0.25)) { proxy.scrollTo(id, anchor: .top) } }
    private func icon(_ symbol: String, _ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: symbol).font(.system(size: 14)).frame(width: 28, height: 26).contentShape(Rectangle()) }
            .buttonStyle(.plain).foregroundStyle(.blue).help(title).accessibilityLabel(title)
    }
    private func dateLabel(_ date: Date) -> String { L1DisplayFormat.monthDay(date) }
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
    private func dashboard(openL2: @escaping () -> Void) -> some View {
        let reading = model.reading
        let latest = reading?.latestValid
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Dashboard").font(.headline)
                Spacer()
                Text("最新観測 \(StorageMath.stamp(latest?.time))").font(.system(size: 10)).foregroundStyle(.secondary).monospacedDigit()
            }
            HStack(alignment: .top, spacing: 8) {
                card("現在のSSD空き容量", number: latest?.freeBytes.map { String(format: "%.2f GiB", Double($0)/1_073_741_824) } ?? "データなし")
                Button(action: openL2) {
                    card("24時間前からの容量増加", number: reading?.dayComparison.map { StorageMath.signed($0.usageGiB) } ?? "データなし", comparison: reading?.dayComparison)
                }.buttonStyle(.plain).help("Storage Explorer（L2）へ")
                card("1時間前との容量増加", number: reading?.hourComparison.map { StorageMath.signed($0.usageGiB) } ?? "データなし", comparison: reading?.hourComparison)
                    .help("L3へのリンクは未提供です。")
            }
            Text("内蔵SSD 時系列推移（24時間）").font(.system(size: 13, weight: .semibold))
            HStack(spacing: 8) {
                ForEach([false, true], id: \.self) { candidate in
                    Button { change = candidate } label: {
                        Text(candidate ? "SSD空き容量の増減" : "SSD空き容量").font(.caption).padding(.horizontal, 9).padding(.vertical, 5)
                            .background(change == candidate ? Color.accentColor.opacity(0.2) : .clear, in: RoundedRectangle(cornerRadius: 5))
                    }.buttonStyle(.plain)
                }
                Spacer(minLength: 0)
            }
            VStack(spacing: 4) {
                HStack {
                    Text("\(dateLabel(model.now.addingTimeInterval(-86400))) – \(dateLabel(model.now))").font(.caption2).foregroundStyle(.secondary)
                    Spacer()
                    Text("[GiB]").font(.caption2).foregroundStyle(.secondary)
                }
                L1Chart(samples: (reading?.predecessor.map { [$0] } ?? []) + model.snapshot.samples, now: model.now,
                        lineColor: .orange, midnightOnly: true, showsChanges: change).frame(height: 155)
            }
        }
    }
    private var ranking: some View {
        ScrollViewReader { inner in
            ScrollView {
                VStack(spacing: 0) {
                    ForEach(ranked) { target in
                        rankRow(target).id(target.id)
                        if expanded == target.id, let day {
                            StorageChildren(target: target, day: day).padding(.horizontal, 14).padding(.vertical, 10)
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
        if let id = expanded ?? ranked.first?.id { withAnimation { proxy.scrollTo(id, anchor: .top) } }
    }
    private func color(_ id: String) -> Color {
        [Color.orange, .blue, .purple, .green, .pink][(series.firstIndex(where: { $0.id == id }) ?? 0) % 5]
    }
    private func toggle(_ id: String) {
        if selection.contains(id) { selection.remove(id); limitMessage = false }
        else if selection.count < 5 { selection.insert(id); limitMessage = false }
        else { limitMessage = true }
    }
    private func rankRow(_ target: StorageTarget) -> some View {
        let delta = day?.delta(path: target.path, parent: target) ?? .missing("定期観測なし")
        let children = day?.children(target) ?? []
        let capacity = data.latest?.results[target.path]
        let capacityText = capacity?.usable == true ? String(format: "%.2f GiB", Double(capacity!.bytes!)/1_073_741_824) : "—"
        return HStack(spacing: 7) {
            Button { toggle(target.id) } label: {
                Image(systemName: selection.contains(target.id) ? "checkmark.square.fill" : "square").font(.system(size: 12))
                    .foregroundStyle(selection.contains(target.id) ? color(target.id) : .secondary).frame(width: 18, height: 26)
            }.buttonStyle(.plain).accessibilityLabel("\(target.name)の系列を切替")
            VStack(spacing: 2) {
                HStack(spacing: 5) {
                    Button { toggle(target.id) } label: {
                        HStack(spacing: 8) {
                            Text(target.name).font(.system(size: 12)).lineLimit(1).frame(maxWidth: 140, alignment: .leading)
                            Text(target.path).font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary)
                                .lineLimit(1).truncationMode(.middle).frame(maxWidth: .infinity, alignment: .leading)
                        }.contentShape(Rectangle())
                    }.buttonStyle(.plain).foregroundStyle(.primary).help("\(target.path)\n現在容量：\(capacityText)")
                    HStack(spacing: 3) {
                        Text(StorageMath.signed(delta.gib)).font(.system(size: 12, weight: .medium)).monospacedDigit()
                        if let reason = delta.reason { InfoButton(title: "比較できない理由", message: reason) }
                    }.frame(width: 104, alignment: .trailing)
                }
                rankBar(delta.gib).frame(height: 3)
            }
            Button { expanded = expanded == target.id ? nil : target.id } label: {
                HStack(spacing: 3) {
                    Text(target.includesChildren ? (children.isEmpty && day?.current?.completed != true ? "—" : "\(children.count)件") : "—").font(.system(size: 10)).monospacedDigit()
                    Image(systemName: expanded == target.id ? "chevron.down" : "chevron.right").font(.system(size: 9))
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
    private struct DailyPoint: Identifiable {
        let date: Date
        let value: Double
        let segment: Int
        var id: Date { date }
    }
    private func points(_ target: StorageTarget) -> [DailyPoint] {
        var segment = 0
        return data.days.compactMap { day in
            guard let value = day.delta(path: target.path, parent: target).gib else { segment += 1; return nil }
            return DailyPoint(date: day.date, value: value, segment: segment)
        }
    }
    private var daily: some View {
        let dates = data.days.map(\.date)
        let start = dates.first ?? Calendar.current.startOfDay(for: model.now.addingTimeInterval(-6*86400))
        let end = dates.last ?? model.now
        return Chart {
            ForEach(dates, id: \.self) { date in
                RuleMark(x: .value("日付", date)).lineStyle(StrokeStyle(lineWidth: 0.5, dash: [3, 3])).foregroundStyle(Color.secondary.opacity(0.3))
            }
            RuleMark(y: .value("0 GiB", 0)).foregroundStyle(Color.secondary.opacity(0.5))
            if let day { RuleMark(x: .value("選択日", day.date)).lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3])).foregroundStyle(Color.secondary.opacity(0.7)) }
            ForEach(data.days) { day in
                if let value = day.usageGiB {
                    BarMark(x: .value("日付", day.date), y: .value("SSD使用量増減", value), width: .fixed(25)).foregroundStyle(.gray.opacity(0.28))
                }
            }
            ForEach(series) { target in
                ForEach(points(target)) { point in
                    LineMark(x: .value("日付", point.date), y: .value("GiB", point.value), series: .value("区間", "\(target.id)-\(point.segment)"))
                        .foregroundStyle(color(target.id)).lineStyle(StrokeStyle(lineWidth: 2))
                    PointMark(x: .value("日付", point.date), y: .value("GiB", point.value)).foregroundStyle(color(target.id)).symbolSize(24)
                }
            }
        }.chartXScale(domain: start.addingTimeInterval(-43200)...end.addingTimeInterval(43200))
            .chartYScale(domain: .automatic(includesZero: true))
            .chartYAxis { AxisMarks(position: .trailing) }
            .chartXAxis { AxisMarks(values: dates) { axis in
                AxisValueLabel(centered: false, anchor: .top, collisionResolution: .disabled) {
                    if let date = axis.as(Date.self) { Text(dateLabel(date)).font(.caption2).fontWeight(day?.date == date ? .bold : .regular).monospacedDigit() }
                }
            } }
            .chartLegend(.hidden)
            .chartOverlay { proxy in
                GeometryReader { geometry in
                    Rectangle().fill(.clear).contentShape(Rectangle()).gesture(SpatialTapGesture().onEnded { event in
                        guard let plot = proxy.plotFrame else { return }
                        let frame = geometry[plot]
                        guard frame.contains(event.location), let date: Date = proxy.value(atX: event.location.x-frame.minX) else { return }
                        selectedDate = dates.min { abs($0.timeIntervalSince(date)) < abs($1.timeIntervalSince(date)) }
                    })
                }
            }
            .overlay(alignment: .topLeading) {
                if let reason = day?.usageReason {
                    InfoButton(title: "SSD使用量増減の欠測理由", message: reason).padding(4)
                }
            }
            .accessibilityLabel("7日間の使用量増減、GiB")
            .accessibilityAction(named: "前の日を選択") { moveDay(-1) }
            .accessibilityAction(named: "次の日を選択") { moveDay(1) }
    }
    private func moveDay(_ offset: Int) {
        guard let date = day?.date, let index = data.days.firstIndex(where: { $0.date == date }) else { return }
        selectedDate = data.days[min(max(index+offset, 0), data.days.count-1)].date
    }
    private var legend: some View {
        LegendFlow(spacing: 12) {
            HStack(spacing: 5) {
                Rectangle().fill(.gray.opacity(0.5)).frame(width: 14, height: 8)
                Text("SSD使用量増減").foregroundStyle(.secondary)
            }.fixedSize()
            ForEach(series) { target in
                HStack(spacing: 5) {
                    ZStack {
                        Rectangle().fill(color(target.id)).frame(width: 16, height: 2)
                        Circle().fill(color(target.id)).frame(width: 5, height: 5)
                    }.frame(width: 16, height: 8)
                    Text(target.name).foregroundStyle(.secondary)
                }.fixedSize()
            }
        }.font(.system(size: 10)).padding(.vertical, 6)
    }
}

private struct StorageChildren: View {
    let target: StorageTarget
    let day: StorageDay
    @State private var query = ""
    @State private var page = 0
    private var children: [StorageChild] { day.children(target) }
    private var filtered: [StorageChild] { children.filter { query.isEmpty || $0.name.localizedCaseInsensitiveContains(query) } }
    private var pageCount: Int { max(1, (filtered.count+9)/10) }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\(target.name)の子項目").font(.system(size: 12, weight: .medium))
            HStack(spacing: 8) {
                TextField("子項目をフィルタ", text: $query).textFieldStyle(.plain).font(.system(size: 12))
                Text("\(filtered.count) / \(children.count)件").font(.system(size: 10)).monospacedDigit().foregroundStyle(.tertiary)
            }.padding(.horizontal, 9).padding(.vertical, 6)
                .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 5))
                .overlay(RoundedRectangle(cornerRadius: 5).stroke(.quaternary))
            ForEach(Array(filtered.dropFirst(min(page, pageCount-1)*10).prefix(10))) { child in
                HStack {
                    Text(child.name).lineLimit(1).truncationMode(.middle).help(child.path)
                    Spacer()
                    Text(StorageMath.signed(child.delta.gib)).monospacedDigit()
                    if let reason = child.delta.reason { InfoButton(title: "比較できない理由", message: reason) }
                    else { Color.clear.frame(width: 14, height: 14) }
                }.font(.system(size: 12)).frame(height: 20)
            }
            if filtered.isEmpty { Text(children.isEmpty ? "子項目の記録がありません" : "一致する子項目がありません").font(.caption).foregroundStyle(.secondary) }
            HStack(spacing: 12) {
                Spacer()
                Button("前へ") { page = max(0, page-1) }.disabled(page == 0)
                Text("\(min(page+1, pageCount)) / \(pageCount)").monospacedDigit().foregroundStyle(.secondary)
                Button("次へ") { page += 1 }.disabled(page+1 >= pageCount)
            }.buttonStyle(.plain).font(.system(size: 10)).padding(.top, 3)
        }.onChange(of: query) { _, _ in page = 0 }
            .onChange(of: day.date) { _, _ in page = 0 }
    }
}
