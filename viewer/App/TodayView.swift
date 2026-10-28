import SwiftUI

struct TodayView: View {
    @ObservedObject var model: ViewerModel
    @State private var tab = 0
    init(model: ViewerModel, initialTab: Int = 0) {
        self.model = model; _tab = State(initialValue: initialTab)
    }
    private var data: TodaySnapshot? { model.todayReading }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                if let message = model.todayMessage { Text(message).foregroundStyle(.secondary) }
                if let data, data.day == model.selectedToday {
                    summary(data)
                    HStack(spacing: 3) {
                        listTab("Incident一覧", value: 0)
                        listTab("Scan一覧", value: 1)
                    }.padding(3).background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))
                    if tab == 0 {
                        if data.incidents.isEmpty { Text("表示日のIncident記録はありません。").foregroundStyle(.secondary) }
                        LazyVStack(spacing: 10) {
                            ForEach(data.incidents) { incident in incidentCard(incident, data: data) }
                        }.id(data.day)
                    } else {
                        scanList(data).id(data.day)
                    }
                    Text("ローカル日付 · GiB ／ Incident減少量比は因果関係の説明率ではありません。")
                        .font(.caption2).foregroundStyle(.secondary)
                } else if model.todayLoading { ProgressView("表示日の記録を読み込み中") }
            }.padding(16)
        }
        .toolbar { ToolbarItem { Button { Task { await model.refreshToday() } } label: { Image(systemName: "arrow.clockwise") }.help("表示日のデータを更新").disabled(model.todayLoading) } }
    }
    private func listTab(_ title: String, value: Int) -> some View {
        Button { tab = value } label: {
            Text(title).font(.callout).frame(maxWidth: .infinity).padding(.vertical, 5)
                .background(tab == value ? Color(nsColor: .controlBackgroundColor) : .clear, in: RoundedRectangle(cornerRadius: 4))
                .contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityValue(tab == value ? "選択中" : "未選択")
    }
    private var header: some View {
        ViewThatFits(in: .horizontal) {
            HStack { titleBlock; Spacer(); dateNavigation }
            VStack(alignment: .leading, spacing: 8) { titleBlock; HStack { Spacer(); dateNavigation } }
        }
    }
    private var titleBlock: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("今日の状況").font(.headline)
            Text("L1 最終観測 \(TodayFormat.stamp(data?.latestObservation))").font(.caption2).foregroundStyle(.secondary)
        }
    }
    private var dateNavigation: some View {
        HStack(spacing: 8) {
            Button("前日") { model.moveToday(by: -1) }
            Text(model.selectedToday, format: .dateTime.year().month(.twoDigits).day(.twoDigits).weekday()).monospacedDigit().font(.caption)
            Button("翌日") { model.moveToday(by: 1) }.disabled(model.selectedToday >= Calendar.current.startOfDay(for: model.now))
        }.fixedSize()
    }
    private func summary(_ data: TodaySnapshot) -> some View {
        HStack(alignment: .top, spacing: 8) {
            VStack(spacing: 8) {
                HStack(alignment: .top, spacing: 8) {
                    HStack(spacing: 3) {
                        Text("SSD使用量増減").font(.system(size: 12))
                        InfoButton(title: "計算に使用したL1観測日時", message: "最初のL1観測：\(TodayFormat.stamp(data.first?.time))\n最後のL1観測：\(TodayFormat.stamp(data.last?.time))")
                    }.padding(.top, 4)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(data.usageChange.map { TodayFormat.signed($0) } ?? "データなし").font(.system(size: 22, weight: .medium)).foregroundStyle(.orange).monospacedDigit().lineLimit(1).minimumScaleFactor(0.7)
                        Text(data.usageChange != nil ? "\(TodayFormat.capacity(data.first?.used)) → \(TodayFormat.capacity(data.last?.used))" : data.last == nil ? "L1観測なし" : "比較可能な観測なし")
                            .font(.system(size: 10)).foregroundStyle(.secondary).monospacedDigit().fixedSize(horizontal: false, vertical: true)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }.todaySurface()
                HStack(alignment: .center, spacing: 8) {
                    Text("Scan").font(.system(size: 12))
                    Text("\(data.scans.count)回").font(.system(size: 22, weight: .medium)).monospacedDigit()
                    VStack(alignment: .leading, spacing: 2) {
                        Text("定期 \(data.scans.filter { $0.trigger == "periodic" }.count) / Incident \(data.scans.filter { $0.trigger == "incident" }.count)")
                        Text(["L2", "L3", "L9"].map { layer in "\(layer) \(data.scans.filter { $0.layer == layer }.count)" }.joined(separator: " / "))
                    }.font(.system(size: 10)).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading)
                }.todaySurface()
            }.frame(maxWidth: .infinity)
            VStack(alignment: .leading, spacing: 8) {
                HStack { Text("Incident").font(.system(size: 12)); Text("\(data.incidents.count)件").font(.system(size: 22, weight: .medium)); Text("表示日の総数").font(.caption2).foregroundStyle(.secondary) }
                HStack(spacing: 6) {
                    statusCount("原因特定", state: "identified", icon: "checkmark", data: data)
                    statusCount("原因未特定", state: "unidentified", icon: "questionmark", data: data)
                    statusCount("一次調査中", state: "investigating", icon: "clock", data: data)
                }
                TodayReasonButton()

                HStack { ForEach(["A", "B", "C", "D"], id: \.self) { c in Text("\(c)：\(data.categoryCount(c))件").frame(maxWidth: .infinity, alignment: .leading) } }.font(.caption2)
                let unknown = data.incidents.filter { $0.state == "unidentified" && $0.category == nil }.count
                if unknown > 0 { Text("分類記録なし \(unknown)件").font(.caption2).foregroundStyle(.secondary) }
            }.todaySurface().frame(maxWidth: .infinity)
        }
    }
    private func statusCount(_ label: String, state: String, icon: String, data: TodaySnapshot) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 4) { Image(systemName: icon).font(.caption); Text("\(data.count(state))").font(.system(size: 22, weight: .medium)).monospacedDigit() }.foregroundStyle(statusColor(state))
            Text(label).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1).minimumScaleFactor(0.75)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(6)
            .overlay(RoundedRectangle(cornerRadius: 5).stroke(.quaternary))
    }
    private func statusColor(_ state: String) -> Color { state == "identified" ? .green : state == "investigating" ? .blue : .secondary }
    private func incidentCard(_ incident: TodayIncident, data: TodaySnapshot) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(alignment: .top) {
                Text(TodayFormat.clock(incident.detected)).font(.system(size: 15, weight: .semibold)).monospacedDigit()
                Text(incident.conditions).font(.caption).foregroundStyle(.secondary)
                Spacer(minLength: 4)
                Text(incident.status + (incident.category.map { " " + $0 } ?? "")).font(.caption).foregroundStyle(statusColor(incident.state)).padding(4).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 5))
            }
            HStack(spacing: 14) {
                Text(TodayFormat.signed(incident.freeChange)).font(.system(size: 23, weight: .medium)).foregroundStyle((incident.freeChange ?? 0) < 0 ? .red : .secondary).monospacedDigit()
                VStack(alignment: .leading, spacing: 0) { Text("SSD空き容量"); Text("\(TodayFormat.capacity(incident.freeBefore)) → \(TodayFormat.capacity(incident.freeAfter))") }.font(.system(size: 11)).foregroundStyle(.secondary).monospacedDigit()
            }
            Text(incident.resultText).font(.callout).fixedSize(horizontal: false, vertical: true)
            ForEach(Array(incident.findings.enumerated()), id: \.offset) { _, finding in
                findingRow(finding, incident: incident)
            }
            Text("関連Scan").font(.caption2).foregroundStyle(.secondary)
            if incident.relatedIDs.isEmpty { Text("関連Scanの記録なし／実行待ち").font(.caption).foregroundStyle(.secondary) }
            ForEach(incident.relatedIDs, id: \.self) { id in
                if let scan = data.related[id] ?? incident.archivedScan(id) {
                    TodayScanDisclosure(model: model, scan: scan, evidence: incident.evidence(for: id))
                } else { Text("Scan \(id)：通常履歴・保存済み明細なし").font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
            }
            Text("Incident \(incident.id)").font(.system(size: 9)).foregroundStyle(.tertiary).textSelection(.enabled)
        }.padding(14).background(.background, in: RoundedRectangle(cornerRadius: 9)).overlay(RoundedRectangle(cornerRadius: 9).stroke(.quaternary))
    }
    private func findingRow(_ finding: IncidentJSON, incident: TodayIncident) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(incident.state == "identified" ? "増加箇所" : incident.category == "B" ? "参考：L2/L3の候補" : "参考：最大増加箇所").font(.caption2).foregroundStyle(.secondary)
                Text(TodayFormat.path(finding["path"].text ?? "パス記録なし")).font(.caption).textSelection(.enabled)
            }.frame(maxWidth: .infinity, alignment: .leading)
            Text(TodayFormat.signed(finding["delta"].number)).font(.system(size: incident.state == "identified" ? 23 : 12, weight: .medium)).foregroundStyle(incident.state == "identified" ? .orange : .secondary).monospacedDigit()
            VStack(alignment: .leading, spacing: 1) {
                Text("実容量 \(TodayFormat.capacity(finding["before_bytes"].number)) → \(TodayFormat.capacity(finding["bytes"].number))")
                if (incident.state == "identified" || incident.category == "B"), let change = incident.freeChange, change < 0, let delta = finding["delta"].number {
                    Text(String(format: "Incident減少量比 %.1f%%", delta / -change * 100))
                }
            }.font(.system(size: 10)).foregroundStyle(.secondary).monospacedDigit()
        }.padding(9).background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 6))
            .foregroundStyle(incident.state == "identified" ? .primary : .secondary)
    }
    private func scanList(_ data: TodaySnapshot) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack { Text("時刻").frame(width: 78, alignment: .leading); Text("Scan").frame(width: 36, alignment: .leading); Text("起因").frame(width: 65, alignment: .leading); Text("結果"); Spacer(); Text("所要時間") }.font(.caption2).foregroundStyle(.secondary).padding(.leading, 18).padding(.vertical, 8)
            if data.scans.isEmpty { Text("表示日のScan実行記録はありません。").foregroundStyle(.secondary) }
            ForEach(data.scans) { scan in TodayScanDisclosure(model: model, scan: scan, evidence: [], daily: true) }
        }
    }
}

struct TodayScanDisclosure: View {
    @ObservedObject var model: ViewerModel
    let scan: TodayScan
    let evidence: [IncidentJSON]
    var daily = false
    @State private var expanded = false
    init(model: ViewerModel, scan: TodayScan, evidence: [IncidentJSON], daily: Bool = false, initiallyExpanded: Bool = false) {
        self.model = model; self.scan = scan; self.evidence = evidence; self.daily = daily
        _expanded = State(initialValue: initiallyExpanded)
    }
    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Scan \(scan.id)").textSelection(.enabled)
                Text("実行 \(TodayFormat.stamp(scan.started)) → \(TodayFormat.stamp(scan.ended)) ／ \(TodayFormat.state(scan.state))")
                Text("対象：\(scan.targets.map(TodayFormat.path).joined(separator: "、"))").textSelection(.enabled)
                if scan.layer == "L9", let depth = scan.options["max_depth"].number { Text(String(format: "最大%.0f階層", depth)) }
                if scan.hasError { Text("実行エラーの記録あり（生ログは表示しません）").foregroundStyle(.secondary) }
                if scan.archived { Text("通常履歴なし。Incidentに保存された証跡・抜粋を表示。").foregroundStyle(.secondary) }
                ForEach(Array(evidence.enumerated()), id: \.offset) { _, item in savedEvidence(item) }
                if let detail = model.todayDetails[scan.id] {
                    Text("取得結果 \(detail.count)件 ／ 失敗等 \(detail.failures)件\(detail.count > detail.rows.count ? "（明細は先頭100件）" : "")").foregroundStyle(.secondary)
                    ForEach(detail.rows) { row in
                        HStack(alignment: .top) {
                            Text(TodayFormat.path(row.path)).frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
                            Text(TodayFormat.capacity(row.bytes)).monospacedDigit()
                            Text(TodayFormat.state(row.state)).foregroundStyle(.secondary)
                        }
                    }
                } else if model.todayDetailErrors.contains(scan.id) {
                    Text("取得結果を読み込めませんでした。再度展開するか更新してください。").foregroundStyle(.secondary)
                } else if !scan.archived { Text("取得結果を読み込み中／記録なし").foregroundStyle(.secondary) }
            }.font(.caption).padding(10).frame(maxWidth: .infinity, alignment: .leading).background(Color.primary.opacity(0.04))
        } label: {
            HStack(spacing: daily ? 0 : 10) {
                if daily {
                    Text(TodayFormat.clock(scan.started)).frame(width: 78, alignment: .leading)
                    Text(scan.layer).frame(width: 36, alignment: .leading)
                    Text(scan.origin).frame(width: 65, alignment: .leading)
                } else {
                    Text(scan.layer); Text(TodayFormat.clock(scan.started)).foregroundStyle(.secondary)
                }
                Text(TodayFormat.state(scan.state)).foregroundStyle(.secondary)
                Spacer(minLength: 4)
                Text(scan.duration).foregroundStyle(.secondary)
            }.font(.caption).padding(.vertical, 6).contentShape(Rectangle())
        }.task(id: "\(scan.id)-\(expanded)-\(model.todayRevision)") {
            if expanded && !scan.archived { await model.loadTodayScan(scan.id) }
        }.padding(.vertical, 2)
    }
    private func savedEvidence(_ item: IncidentJSON) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            if let target = item["target"].text { Text("評価対象：\(TodayFormat.path(target))").textSelection(.enabled) }
            Text("比較元Scan：\(item["baseline_id"].text ?? "記録なし")").textSelection(.enabled)
            if let reason = item["reason"].text {
                Text("対象別評価：\(reasonText(reason))")
            }
            if let complete = item["scan_complete"].flag { Text("対象取得：\(complete ? "完全" : "不完全") ／ 比較元：\(item["baseline_complete"].flag == true ? "完全" : "不完全または記録なし")") }
            if let failures = item["failures"].number { Text(String(format: "対象内の取得失敗 %.0f件", failures) + " ／ 比較元の失敗抜粋 \(item["baseline_failure_excerpts"].array.count)件") }
            ForEach(Array(item["changes"].array.enumerated()), id: \.offset) { _, change in
                Text("\(TodayFormat.path(change["path"].text ?? "パス記録なし"))：\(TodayFormat.capacity(change["before_bytes"].number)) → \(TodayFormat.capacity(change["bytes"].number))（\(TodayFormat.signed(change["delta"].number))）").textSelection(.enabled)
            }
            ForEach(Array((item["failure_excerpts"].array + item["baseline_failure_excerpts"].array).enumerated()), id: \.offset) { _, failure in
                Text("取得失敗：\(TodayFormat.path(failure["path"].text ?? "パス記録なし")) ／ \(TodayFormat.state(failure["status"].text ?? "error"))").foregroundStyle(.secondary).textSelection(.enabled)
            }
            if scan.archived {
                ForEach(Array(item["excerpt"].array.enumerated()), id: \.offset) { _, row in
                    Text("\(TodayFormat.path(row["path"].text ?? "パス記録なし"))：\(TodayFormat.capacity(row["bytes"].number)) ／ \(TodayFormat.state(row["status"].text ?? ""))").textSelection(.enabled)
                }
            }
        }
    }
    private func reasonText(_ reason: String) -> String {
        switch reason {
        case "growth_identified": return "増加箇所を特定"
        case "no_baseline": return "比較可能な過去L9なし"
        case "insufficient_evidence": return "L9の増加量が特定閾値未満"
        case "scan_incomplete": return "対象・比較元の取得不完全／失敗／中止"
        default: return "評価記録不明"
        }
    }
}
private extension View {
    func todaySurface() -> some View {
        self.padding(10).background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 6))
    }
}

private struct TodayReasonButton: View {
    @State private var showing = false
    var body: some View {
        Button("未特定の内訳 ⓘ") { showing.toggle() }.buttonStyle(.plain).foregroundStyle(.blue).font(.caption)
            .popover(isPresented: $showing) {
                Text("A：L3/L2で候補なし\nB：L9の比較可能な過去記録なし\nC：L9の増加量が特定閾値未満\nD：対象や比較元の取得不完全・失敗・中止等\n\n全体partialだけではDに分類せず、既存の対象別評価を表示します。代表理由はD → B → Cの優先順。")
                    .font(.callout).padding(16).frame(width: 330, alignment: .leading)
            }
    }
}
