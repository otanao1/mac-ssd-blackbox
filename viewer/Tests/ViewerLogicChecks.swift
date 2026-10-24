import Foundation

// Run independently of the live database and WidgetKit scheduling.
@main
struct ViewerLogicChecks {
    static func main() {
        let now = Date(timeIntervalSince1970: 1_792_810_000)
        func sample(_ offset: Double, _ bytes: Int64? = 1_073_741_824) -> L1Sample {
            L1Sample(time: now.addingTimeInterval(offset), freeBytes: bytes)
        }
        func snapshot(_ latest: L1Sample?, _ status: L1Snapshot.Status = .ready,
                      generated: Date? = nil) -> L1Snapshot {
            L1Snapshot(version: 1, generatedAt: generated ?? now, samples: [], latest: latest, status: status)
        }
        let fresh = snapshot(sample(-30))
        let newYear = Date(timeIntervalSince1970: 1_609_502_400) // 2021-01-01 12:00 UTC
        precondition(L1DisplayFormat.timestamp(newYear, timeZone: TimeZone(secondsFromGMT: 0)!) == "2021/01/01 12:00:00")
        precondition(L1DisplayFormat.hourMinute(newYear, timeZone: TimeZone(secondsFromGMT: 0)!) == "12:00")
        precondition(L1DisplayFormat.monthDay(newYear, timeZone: TimeZone(secondsFromGMT: 0)!) == "01/01")
        precondition(fresh.isCurrent(at: now))
        precondition(fresh.capacityNumber(at: now) == "1.00")
        precondition(fresh.widgetStatusText(at: now) == nil)
        // Reproduce the original 181s timeline / 900s reload mismatch. The
        // renderer must describe a delayed display, not a failed live reader.
        let old = snapshot(sample(-181))
        precondition(!old.isCurrent(at: now))
        precondition(old.widgetStatusText(at: now) == "表示更新待ち・前回の観測値")
        precondition(snapshot(sample(-30), .unavailable).widgetStatusText(at: now) != old.widgetStatusText(at: now))
        precondition(snapshot(sample(-30), .stopped).widgetStatusText(at: now) != old.widgetStatusText(at: now))
        precondition(WidgetReloadState(fresh) == WidgetReloadState(snapshot(sample(-30), generated: now.addingTimeInterval(60))))
        precondition(WidgetReloadState(fresh) != WidgetReloadState(snapshot(sample(0))))
        precondition(WidgetReloadState(fresh) != WidgetReloadState(snapshot(sample(-30), .unavailable)))
        // Normal 60s observations connect; missing observations and NULL values do not.
        let points = L1PlotData.points([sample(-600), sample(-540), sample(-300), sample(-240, nil), sample(-180), sample(-120)], now: now)
        precondition(points.map(\.segment) == [0, 0, 1, 2, 2])
        precondition(L1PlotData.points([sample(-90_000), sample(1)], now: now).isEmpty)
        precondition(L1PlotData.points([sample(-180), sample(-90), sample(0)], now: now).map(\.segment) == [0, 0, 0])
        precondition(L1PlotData.points([sample(-181), sample(-90)], now: now).map(\.segment) == [0, 1])
        // A one-sample run still has a real plotted point, not a fabricated bridge.
        precondition(L1PlotData.points([sample(-60)], now: now).count == 1)
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Tokyo")!
        let dates = L1PlotData.axisDates(now: now, calendar: calendar)
        precondition(dates.count == 8)
        precondition(dates.allSatisfy { calendar.component(.hour, from: $0).isMultiple(of: 3) && calendar.component(.minute, from: $0) == 0 })
        precondition(dates.filter { calendar.component(.hour, from: $0) == 0 }.count == 1)
        calendar.timeZone = TimeZone(identifier: "America/Los_Angeles")!
        let dstDate = calendar.date(from: DateComponents(year: 2026, month: 11, day: 1, hour: 12))!
        precondition(L1PlotData.axisDates(now: dstDate, calendar: calendar).allSatisfy { calendar.component(.hour, from: $0).isMultiple(of: 3) })
        print("PASS: freshness, delayed timeline wording, reload triggers, real gap segmentation, period filtering and calendar-aligned axes")
    }
}
