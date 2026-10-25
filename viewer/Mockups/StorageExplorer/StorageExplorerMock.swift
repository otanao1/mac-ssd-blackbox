// Issue #22 review-only executable. All values, dates and child names are synthetic.
// No SQLite, monitoring configuration, app group or production target is accessed.
import AppKit
import Charts
import SwiftUI

struct Target: Identifiable {
    let id: Int
    let name: String
    let path: String
    let delta: Double?
    let children: Int?
    var values: [Double?] {
        guard let base = delta else { return Array(repeating: nil, count: 7) }
        return [base * 0.3 + Double(id % 4) * 0.15, -base * 0.25, base * 0.7, nil, nil, base * 0.5, delta]
    }
}
let targets: [Target] = {
    let names = ["ホーム", "ユーザーライブラリ", "アプリケーションサポート（ユーザー）", "アプリケーション", "Homebrewパッケージ", "一時ファイル", "システムライブラリ", "Homebrew", "システムアプリサポート", "ユーザー", "クラウドストレージ", "開発ビルドキャッシュ", "Homebrewキャッシュ", "追加ソフトウェア", "システム作業領域", "Unixツール", "ローカルツール", "仮想メモリ", "コアダンプ"]
    let paths = ["~", "~/Library", "~/Library/Application Support", "/Applications", "/opt/homebrew/Cellar", "/private/tmp", "/Library", "/opt/homebrew", "/Library/Application Support", "/Users", "~/Library/CloudStorage", "~/Library/Developer/Xcode/DerivedData", "~/Library/Caches/Homebrew", "/opt", "/private/var", "/usr", "/usr/local", "/System/Volumes/VM", "/cores"]
    let deltas: [Double?] = [4.2, 3.1, 2.4, 1.2, 0.8, 0.4, 0.2, 0.1, 0, -0.1, -0.2, -0.3, -0.6, -0.8, -1.1, -1.4, -2.0, nil, nil]
    return names.indices.map { Target(id: $0, name: names[$0], path: paths[$0], delta: deltas[$0], children: [11,12,18].contains($0) ? nil : ($0 == 2 ? 249 : 20 + $0 * 3)) }
}()
func signed(_ value: Double?) -> String { value.map { String(format: "%+.2f GiB", $0) } ?? "—" }
let mockNow = Calendar.current.date(from: DateComponents(year: 2026, month: 4, day: 21, hour: 18))!
func day(_ index: Int) -> Date { Calendar.current.date(byAdding: .day, value: index - 6, to: Calendar.current.startOfDay(for: mockNow))! }
struct DailyPoint: Identifiable {
    let index: Int
    let value: Double
    let segment: Int
    var id: Int { index }
}
func points(_ values: [Double?]) -> [DailyPoint] {
    var segment = 0
    return values.enumerated().compactMap { index, value in
        guard let value else { segment += 1; return nil }
        return DailyPoint(index: index, value: value, segment: segment)
    }
}

// ImageRenderer cannot render AppKit-backed ScrollView content; use the same
// content without scrolling for static review images.
struct ReviewScroll<Content: View>: View {
    @ViewBuilder let content: () -> Content
    var body: some View {
        if CommandLine.arguments.contains("--render") { content().clipped() }
        else { ScrollView { content() } }
    }
}

func option(_ name: String) -> String? {
    guard let index = CommandLine.arguments.firstIndex(of: name), index + 1 < CommandLine.arguments.count else { return nil }
    return CommandLine.arguments[index + 1]
}
func dateLabel(_ index: Int) -> String {
    let c = Calendar.current.dateComponents([.month, .day], from: day(index))
    return "\(c.month!)/\(c.day!)"
}
let isRendering = CommandLine.arguments.contains("--render")

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

struct StorageMock: View {
    @State private var selection: Set<Int> = [0, 1, 2]
    @State private var change = CommandLine.arguments.contains("--changes")
    @State private var mockAction: String?
    @State private var sparse = CommandLine.arguments.contains("--sparse")
    @State private var missingHour = CommandLine.arguments.contains("--missing-hour")
    @State private var expanded: Int? = CommandLine.arguments.contains("--children") ? 2 : nil
    @State private var selectedDay = Int(option("--day") ?? "6") ?? 6
    @State private var limitMessage = false
    @State private var innerJump = 0
    var samples: [L1Sample] {
        (0...288).map { i in
            let missing = (100...107).contains(i) || (missingHour && (275...277).contains(i))
            let value: Int64? = missing ? nil : Int64((44 + sin(Double(i)/30) * 3 - Double(i)/180) * 1_073_741_824)
            return L1Sample(time: mockNow.addingTimeInterval(Double(i - 288) * 300), freeBytes: value)
        }
    }
    private func comparison(hours: Int) -> L1Comparison? {
        let latest = samples.last { $0.freeBytes != nil && $0.freeBytes! >= 0 }
        return StorageMath.comparison(latest: latest, hours: hours, samples: samples)
    }
    private func usageDelta(hours: Int) -> Double? { comparison(hours: hours)?.usageGiB }
    private func value(_ target: Target) -> Double? { sparse ? nil : target.values[selectedDay] }
    private func reason(_ target: Target) -> String? {
        if sparse { return "前回の日次観測なし" }
        if selectedDay == 3 { return "この日の日次観測なし" }
        if selectedDay == 4 { return "前日の日次観測なし" }
        if target.delta == nil { return "一部取得・比較不可" }
        return nil
    }
    private var ranked: [Target] {
        targets.sorted {
            let a = value($0), b = value($1)
            if a == b { return $0.id < $1.id }
            if a == nil { return false }; if b == nil { return true }
            return a! > b!
        }
    }
    private var barMaximum: Double { max(0.01, ranked.compactMap(value).map(abs).max() ?? 1) }
    private var rankHeight: CGFloat { CommandLine.arguments.contains("--ranking-only") ? 440 : 300 }

    var body: some View {
        ScrollViewReader { outer in
            ReviewScroll {
                VStack(alignment: .leading, spacing: 14) {
                    if !CommandLine.arguments.contains("--ranking-only") {
                        l1Section { jump(outer, "ranking") }
                    }
                    if !CommandLine.arguments.contains("--l1-only") {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("Storage Explorer").font(.title3.weight(.semibold))
                            Spacer()
                            Text("L2").font(.caption).foregroundStyle(.secondary)
                            icon("chart.xyaxis.line", "7日間グラフへ") { jump(outer, "daily") }
                        }.id("ranking")
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text("\(dateLabel(selectedDay)) の増減").font(.headline)
                            Text(sparse || selectedDay == 4 ? "比較対象なし" : "比較対象は\(dateLabel(selectedDay-1))")
                                .font(.caption).foregroundStyle(.secondary)
                            InfoButton(title: "比較の詳細", message: comparisonDetails)
                            Spacer(minLength: 0)
                            Text("\(selection.count)/5系列").font(.caption2).foregroundStyle(.tertiary)
                        }
                        if limitMessage {
                            Text("最大5系列です。選択中の系列を解除してください。").font(.caption).foregroundStyle(.orange)
                        }
                        ranking
                        if !CommandLine.arguments.contains("--ranking-only") {
                            HStack {
                                Text("日次使用量増減（7日間）").font(.headline)
                                Spacer()
                                Text("[GiB]").font(.caption2).foregroundStyle(.secondary)
                                icon("list.bullet", "ランキングへ") { innerJump += 1; jump(outer, "ranking") }
                            }.id("daily")
                            daily.frame(height: 230)
                            legend
                        }
                    }.padding(.top, 12)
                    }
                }.padding(16)
            }
        }.frame(minWidth: 600, minHeight: 380)
        .toolbar {
            ToolbarItem {
                Menu {
                    Button("データを更新") { mockAction = "データを更新する操作のモックです。観測値や実DBは変更しません。" }
                    Button("設定…") { mockAction = "設定を開く操作のモックです。監視設定は変更しません。" }
                    Divider()
                    Menu("モック検証") {
                        Toggle("日次履歴不足", isOn: $sparse)
                        Toggle("1時間前データなし", isOn: $missingHour)
                    }
                } label: { Image(systemName: "slider.horizontal.3") }
                    .help("更新・設定")
                    .accessibilityLabel("更新・設定")
            }
        }
        .alert("モック操作", isPresented: Binding(get: { mockAction != nil }, set: { if !$0 { mockAction = nil } })) {
            Button("閉じる", role: .cancel) { mockAction = nil }
        } message: { Text(mockAction ?? "") }
    }
    private func jump(_ proxy: ScrollViewProxy, _ id: String) {
        withAnimation(.easeInOut(duration: 0.25)) { proxy.scrollTo(id, anchor: .top) }
    }
    private func icon(_ symbol: String, _ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: symbol).font(.system(size: 14)).frame(width: 28, height: 26).contentShape(Rectangle()) }
            .buttonStyle(.plain).foregroundStyle(.blue).help(title).accessibilityLabel(title)
    }
    var comparisonDetails: String {
        let before = selectedDay-1
        func stamp(_ index: Int) -> String {
            "\(L1DisplayFormat.monthDay(day(index))) \(L1DisplayFormat.hourMinute(day(index).addingTimeInterval(15 * 3600)))"
        }
        return "比較元：\(sparse || selectedDay == 4 ? "データなし" : stamp(before))\n比較先：\(selectedDay == 3 ? "データなし" : stamp(selectedDay))"
    }
    private func card(_ title: String, _ number: String, hours: Int? = nil) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 12)).lineLimit(1).minimumScaleFactor(0.85)
                .frame(maxWidth: .infinity, alignment: .leading)
            HStack(alignment: .firstTextBaseline, spacing: 2) {
                if let hours, let comparison = comparison(hours: hours) {
                    Text(StorageMath.stamp(comparison.previous.time))
                        .font(.system(size: 8)).foregroundStyle(.secondary).monospacedDigit().lineLimit(1)
                }
                Spacer(minLength: 0)
                Text(number).font(.system(size: 22, weight: .medium)).monospacedDigit().lineLimit(1).minimumScaleFactor(0.65).layoutPriority(1)
            }
        }.padding(.horizontal, 10).padding(.vertical, 9)
            .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 6))
    }
    func cards(openL2: @escaping () -> Void) -> some View {
        HStack(alignment: .top, spacing: 8) {
            card("現在のSSD空き容量", String(format: "%.2f GiB", Double(samples.last!.freeBytes!)/1_073_741_824))
            Button(action: openL2) {
                card("24時間前からの容量増加", signed(usageDelta(hours: 24)), hours: 24)
            }.buttonStyle(.plain).help("Storage Explorer（L2）へ")
            card("1時間前との容量増加", usageDelta(hours: 1).map { signed($0) } ?? "データなし", hours: 1)
                .help(missingHour ? "比較時刻の観測なし。L3へのリンクは未提供です。" : "L3へのリンクは未提供です。")
        }
    }
    private func l1Section(openL2: @escaping () -> Void) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Dashboard").font(.headline)
                Spacer()
                Text("最新観測 04/21 18:00").font(.system(size: 10)).foregroundStyle(.secondary)
            }
            cards(openL2: openL2)
            HStack(alignment: .firstTextBaseline) {
                Text("内蔵SSD 時系列推移（24時間）").font(.system(size: 13, weight: .semibold))
                Spacer(minLength: 4)
            }
            HStack(spacing: 8) {
                ForEach([false, true], id: \.self) { candidate in
                    Button { change = candidate } label: {
                        Text(candidate ? "SSD空き容量の増減" : "SSD空き容量").font(.caption)
                            .padding(.horizontal, 9).padding(.vertical, 5)
                            .background(change == candidate ? Color.accentColor.opacity(0.2) : .clear, in: RoundedRectangle(cornerRadius: 5))
                    }.buttonStyle(.plain)
                }
                Spacer(minLength: 0)
            }
            L1ReviewPlot(samples: samples, change: change).frame(height: 175)
        }
    }
    private var ranking: some View {
        ScrollViewReader { inner in
            ReviewScroll {
                VStack(spacing: 0) {
                    ForEach(ranked) { target in
                        rankRow(target).id("row-\(target.id)")
                        if expanded == target.id {
                            ChildList(target: target, dayIndex: selectedDay, unavailable: reason(target))
                                .padding(.horizontal, 14).padding(.vertical, 10).background(Color.primary.opacity(0.025))
                        }
                        Divider()
                    }
                }
            }.frame(height: rankHeight, alignment: .top).clipped()
                .onChange(of: innerJump) { _, _ in
                    withAnimation { inner.scrollTo("row-\(expanded ?? ranked.first!.id)", anchor: .top) }
                }
                .onChange(of: selectedDay) { _, _ in
                    // Preserve expanded parent and bring it into the inner viewport after sorting.
                    withAnimation { inner.scrollTo("row-\(expanded ?? ranked.first!.id)", anchor: .top) }
                }
        }
    }
    private func rankRow(_ target: Target) -> some View {
        HStack(spacing: 7) {
            Button { toggle(target.id) } label: {
                Image(systemName: selection.contains(target.id) ? "checkmark.square.fill" : "square")
                    .font(.system(size: 12))
                    .foregroundStyle(selection.contains(target.id) ? seriesColor(target.id) : .secondary)
                    .frame(width: 18, height: 26).contentShape(Rectangle())
            }.buttonStyle(.plain).accessibilityLabel("\(target.name)の系列を切替")
            VStack(spacing: 2) {
                HStack(spacing: 5) {
                    Button { toggle(target.id) } label: {
                        HStack(spacing: 8) {
                            Text(target.name).font(.system(size: 12)).lineLimit(1).frame(maxWidth: 140, alignment: .leading)
                            pathLabel(target.path).frame(maxWidth: .infinity, alignment: .leading)
                        }.contentShape(Rectangle())
                    }.buttonStyle(.plain).foregroundStyle(.primary)
                        .help(hoverText(target))
                    HStack(spacing: 3) {
                        Text(signed(value(target))).font(.system(size: 12, weight: .medium)).monospacedDigit()
                        if let explanation = reason(target) { InfoButton(title: "比較できない理由", message: explanation) }
                    }.frame(width: 104, alignment: .trailing)
                }
                rankBar(value(target)).frame(height: 3)
            }
            Button { expanded = expanded == target.id ? nil : target.id } label: {
                HStack(spacing: 3) {
                    Text(target.children.map { "\($0)件" } ?? "—").font(.system(size: 10)).monospacedDigit()
                    Image(systemName: expanded == target.id ? "chevron.down" : "chevron.right").font(.system(size: 9))
                }.foregroundStyle(.secondary).frame(width: 48, height: 26, alignment: .trailing).contentShape(Rectangle())
            }.buttonStyle(.plain).disabled(target.children == nil)
                .help(target.children == nil ? "直下子項目の記録なし" : "子項目を一覧内に展開")
                .accessibilityLabel("\(target.name)の子項目\(target.children.map { " \($0)件" } ?? "なし")")
        }.padding(.horizontal, 8).padding(.vertical, 4)

    }
    func hoverText(_ target: Target) -> String {
        "\(target.path)\n現在容量：\(42 + target.id).00 GiB"
    }
    private func pathLabel(_ path: String) -> some View {
        Text(path).font(.system(size: 10, design: .monospaced)).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
    }
    private func seriesColor(_ id: Int) -> Color {
        let index = selection.sorted().firstIndex(of: id) ?? 0
        return [Color.orange, .blue, .purple, .green, .pink][index % 5]
    }
    private func toggle(_ id: Int) {
        if selection.contains(id) { selection.remove(id); limitMessage = false }
        else if selection.count < 5 { selection.insert(id); limitMessage = false }
        else { limitMessage = true }
    }
    private func rankBar(_ value: Double?) -> some View {
        GeometryReader { g in
            let half = g.size.width / 2
            Path { p in p.move(to: CGPoint(x: half, y: 0)); p.addLine(to: CGPoint(x: half, y: g.size.height)) }.stroke(.secondary.opacity(0.5))
            if let value {
                let width = abs(value)/barMaximum * max(0, half-2)
                Rectangle().fill((value >= 0 ? Color.orange : Color.blue).opacity(0.7))
                    .frame(width: width, height: g.size.height)
                    .offset(x: value >= 0 ? half : half-width, y: 0)
            }
        }
    }
    var daily: some View {
        Chart {
            ForEach(0...6, id: \.self) { index in
                RuleMark(x: .value("日付ガイド", day(index)))
                    .lineStyle(StrokeStyle(lineWidth: 0.5, dash: [3, 3]))
                    .foregroundStyle(Color.secondary.opacity(0.3))
            }
            RuleMark(y: .value("0 GiB", 0)).foregroundStyle(.secondary)
            RuleMark(x: .value("選択日", day(selectedDay))).foregroundStyle(.primary.opacity(0.5)).lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
            if !sparse {
                ForEach(points([2.8, -1.8, 5.1, nil, nil, 3.3, 4.8])) { p in
                    BarMark(x: .value("日", day(p.index)), y: .value("GiB", p.value), width: .fixed(25)).foregroundStyle(.gray.opacity(0.28))
                }
                ForEach(targets.filter { selection.contains($0.id) }) { target in
                    ForEach(points(target.values)) { p in
                        LineMark(x: .value("日", day(p.index)), y: .value("GiB", p.value), series: .value("対象・区間", "\(target.id)-\(p.segment)"))
                            .foregroundStyle(seriesColor(target.id)).lineStyle(StrokeStyle(lineWidth: 2))
                        PointMark(x: .value("日", day(p.index)), y: .value("GiB", p.value)).foregroundStyle(seriesColor(target.id)).symbolSize(24)
                    }
                }
            }
        }.chartXScale(domain: day(0).addingTimeInterval(-43200)...day(6).addingTimeInterval(43200))
            .chartYScale(domain: -3...6)
            .chartYAxis { AxisMarks(position: .trailing) }
            .chartXAxis { AxisMarks(values: (0...6).map(day)) { axis in
                AxisValueLabel(centered: false, anchor: .top, collisionResolution: .disabled) { if let date = axis.as(Date.self) { Text(dateLabel((0...6).min(by: { abs(day($0).timeIntervalSince(date)) < abs(day($1).timeIntervalSince(date)) }) ?? 6)).font(.caption2).fontWeight(Calendar.current.isDate(date, inSameDayAs: day(selectedDay)) ? .bold : .regular).foregroundStyle(Calendar.current.isDate(date, inSameDayAs: day(selectedDay)) ? Color.primary : Color.secondary).monospacedDigit() } }
            } }
            .chartLegend(.hidden)
            .chartOverlay { proxy in
                GeometryReader { geometry in
                    Rectangle().fill(.clear).contentShape(Rectangle())
                        .gesture(SpatialTapGesture().onEnded { event in
                            guard let plot = proxy.plotFrame else { return }
                            let frame = geometry[plot]
                            guard frame.contains(event.location), let date: Date = proxy.value(atX: event.location.x-frame.minX) else { return }
                            selectedDay = (0...6).min(by: { abs(day($0).timeIntervalSince(date)) < abs(day($1).timeIntervalSince(date)) }) ?? 6
                        })
                }
            }
            .overlay { if sparse { Text("前回の日次観測がありません").font(.caption).foregroundStyle(.secondary) } }
    }
    var legend: some View {
        LegendFlow(spacing: 12) {
            HStack(spacing: 5) {
                Rectangle().fill(.gray.opacity(0.5)).frame(width: 14, height: 8)
                Text("SSD使用量増減").foregroundStyle(.secondary)
            }.fixedSize()
            ForEach(targets.filter { selection.contains($0.id) }) { t in
                HStack(spacing: 5) {
                    ZStack {
                        Rectangle().fill(seriesColor(t.id)).frame(width: 16, height: 2)
                        Circle().fill(seriesColor(t.id)).frame(width: 5, height: 5)
                    }.frame(width: 16, height: 8)
                    Text(t.name).foregroundStyle(.secondary)
                }.fixedSize()
            }
        }.font(.system(size: 10)).padding(.vertical, 6)
    }
}

// Review-only chart. Production L1Chart.swift is unchanged.
struct L1ReviewPlot: View {
    let samples: [L1Sample]
    var change = false
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("4/20 – 4/21").font(.caption2).monospacedDigit().foregroundStyle(.secondary)
                Spacer()
                Text("[GiB]").font(.caption2).foregroundStyle(.secondary)
            }
        Chart {
            ForEach(L1PlotData.axisDates(now: mockNow).filter { Calendar.current.component(.hour, from: $0) == 0 }, id: \.self) { date in
                RuleMark(x: .value("日付変更", date))
                    .lineStyle(StrokeStyle(lineWidth: 0.7, dash: [3, 3]))
                    .foregroundStyle(Color.secondary.opacity(0.5))
            }
            if change {
                RuleMark(y: .value("0", 0)).foregroundStyle(.secondary)
                ForEach(Array(samples.enumerated()), id: \.offset) { i, sample in
                    if i > 0, let current = sample.freeBytes, let previous = samples[i-1].freeBytes {
                        BarMark(x: .value("時刻", sample.time), y: .value("GiB", Double(current-previous)/1_073_741_824), width: .fixed(2))
                            .foregroundStyle(current >= previous ? Color.orange : Color.blue)
                    }
                }
            } else {
                ForEach(points(samples.map { $0.freeBytes.map { Double($0)/1_073_741_824 } })) { p in
                    LineMark(x: .value("時刻", samples[p.index].time), y: .value("GiB", p.value), series: .value("区間", p.segment)).foregroundStyle(.orange)
                }
            }
        }.chartXScale(domain: mockNow.addingTimeInterval(-86400)...mockNow)
            .chartYScale(domain: change ? -0.15...0.15 : 0...50)
            .chartYAxis { AxisMarks(position: .trailing, values: change ? [-0.1, 0, 0.1] : [0, 20, 40]) }
            .chartXAxis {
                AxisMarks(values: L1PlotData.axisDates(now: mockNow)) { axis in
                    if let date = axis.as(Date.self) {
                        let atStart = date == mockNow.addingTimeInterval(-86400)
                        let atEnd = date == mockNow
                        AxisValueLabel(centered: false, anchor: atStart ? .topLeading : (atEnd ? .topTrailing : .top), collisionResolution: .disabled) {
                            Text(L1DisplayFormat.hourMinute(date)).font(.system(size: 9)).monospacedDigit()
                        }
                    }
                }
            }
            .chartLegend(.hidden)
            .accessibilityLabel(change ? "24時間のSSD空き容量の増減、単位GiB" : "24時間のSSD空き容量、単位GiB")
        }
    }
}

struct ChildList: View {
    let target: Target
    let dayIndex: Int
    let unavailable: String?
    @State private var query = ""
    @State private var page = 0
    private var items: [Int] { (0..<(target.children ?? 0)).filter { query.isEmpty || String(format: "サンプル項目 %03d", $0 + 1).contains(query) } }
    private func childDelta(_ index: Int) -> Double? {
        if unavailable != nil || index % 7 == 0 { return nil }
        return Double(10-index%20)/20 * [0.3, -0.25, 0.7, 0, -0.15, 0.5, 1][dayIndex]
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("\(target.name)の子項目").font(.system(size: 12, weight: .medium))
            HStack(spacing: 8) {
                if isRendering {
                    Text("子項目をフィルタ").font(.system(size: 11)).foregroundStyle(.tertiary)
                    Spacer()
                } else {
                    TextField("子項目をフィルタ", text: $query).textFieldStyle(.plain)
                        .font(.system(size: 12)).onChange(of: query) { _, _ in page = 0 }
                }
                Text("\(items.count) / \(target.children ?? 0)件").font(.system(size: 10)).monospacedDigit().foregroundStyle(.tertiary)
                    .accessibilityLabel("絞り込み\(items.count)件、全\(target.children ?? 0)件")
            }.padding(.horizontal, 9).padding(.vertical, 6)
                .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 5))
                .overlay(RoundedRectangle(cornerRadius: 5).stroke(.quaternary))
            ForEach(Array(items.dropFirst(page * 10).prefix(10)), id: \.self) { i in
                HStack {
                    Text(String(format: "サンプル項目 %03d", i+1)); Spacer()
                    Text(signed(childDelta(i))).monospacedDigit()
                    if childDelta(i) == nil { InfoButton(title: "比較できない理由", message: unavailable ?? "一部取得・比較不可") }
                    else { Color.clear.frame(width: 14, height: 14) }
                }.font(.system(size: 12)).frame(height: 20)
            }
            if items.isEmpty { Text("一致する子項目がありません").font(.caption).foregroundStyle(.secondary) }
            HStack(spacing: 12) {
                Spacer()
                Button("前へ") { page -= 1 }.disabled(page == 0)
                Text("\(page + 1) / \(max(1, (items.count + 9)/10))").monospacedDigit().foregroundStyle(.secondary)
                Button("次へ") { page += 1 }.disabled((page+1)*10 >= items.count)
            }.buttonStyle(.plain).font(.system(size: 10)).padding(.top, 3)
        }.onChange(of: dayIndex) { _, _ in page = 0 }
    }
}

@main
struct MockApp: App {
    init() {
        if isRendering {
            let width = Double(option("--width") ?? "600") ?? 600
            let height = Double(option("--height") ?? "1380") ?? 1380
            let section = option("--section") ?? "all"
            let content: AnyView
            if section == "children" {
                content = AnyView(ChildList(target: targets[2], dayIndex: Int(option("--day") ?? "6") ?? 6, unavailable: nil).padding(16))
            } else if section == "graph" {
                let mock = StorageMock()
                content = AnyView(VStack(alignment: .leading, spacing: 10) {
                    HStack { Text("日次使用量増減（7日間）").font(.headline); Spacer(); Text("[GiB]").font(.caption2).foregroundStyle(.secondary); Image(systemName: "list.bullet").foregroundStyle(.blue) }
                    mock.daily.frame(height: 230)
                    mock.legend
                }.padding(16))
            } else if section == "cards" {
                content = AnyView(StorageMock().cards(openL2: {}).padding(16))
            } else if section == "details" {
                let mock = StorageMock()
                content = AnyView(VStack(alignment: .leading, spacing: 20) {
                    Text("ホバー表示の見本").font(.caption).foregroundStyle(.secondary)
                    Text(mock.hoverText(targets[2])).font(.callout).padding(12).background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 6))
                    Text("ⓘ表示の見本").font(.caption).foregroundStyle(.secondary)
                    Text(mock.comparisonDetails).font(.callout).padding(12).background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 6))
                }.frame(maxWidth: .infinity, alignment: .leading).padding(16))
            } else if section == "menu" {
                content = AnyView(VStack(alignment: .leading, spacing: 14) {
                    Text("右上メニュー（表示見本）").font(.caption).foregroundStyle(.secondary)
                    Text("データを更新")
                    Text("設定…")
                    Divider()
                    Text("モック検証  ›")
                    Text("日次履歴不足\n1時間前データなし").font(.caption).foregroundStyle(.secondary).padding(.leading, 16)
                }.font(.callout).padding(16))
            } else { content = AnyView(StorageMock()) }
            let renderer = ImageRenderer(content: content.frame(width: width, height: height, alignment: .top).background(Color(nsColor: .windowBackgroundColor)).environment(\.colorScheme, .dark))
            renderer.scale = 2
            guard let cg = renderer.cgImage, let png = NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:]) else { fatalError("Render failed") }
            try! png.write(to: URL(fileURLWithPath: CommandLine.arguments.last!))
            exit(0)
        }
    }
    var body: some Scene {
        WindowGroup("Blackbox Viewer") { StorageMock() }
            .defaultSize(width: 600, height: 420)
            .windowResizability(.contentMinSize)
    }
}
