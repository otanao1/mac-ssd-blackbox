# 「今日の状況」Viewer

Issue #34、受入済みの `viewer/Mockups/Today/Issue34/index.html` に基づく。
正式Viewの利用方法は [Viewer利用ガイド](../viewer/README.md#今日の状況) を参照。

## 読み取りと表示規則

- Dashboardとは独立した画面。Viewer上部の「今日の状況」で開き、「Dashboardへ」で戻る。Widgetの遷移先・キャッシュ形式・Dashboardの比較やStorage Explorerは維持する。
- 初期日はローカルの今日。Calendarの暦日加算で前日・翌日へ移動し、未来は表示しない。SQL範囲は日付開始以上・翌日開始未満かつ現在時刻以下。夏時間の日を固定86400秒で区切らない。タブは日付変更でも保持、Scan展開は日付変更で初期化する。
- L1は表示日の最新観測と同じ対象パスを使う。最初と最後の有効な `free`・`total` から使用量 `total - free` と増減を算出する。双方の総容量が一致し、別時刻の2観測がある場合だけ増減を表示。欠測・容量変更・比較元不足を0や補間で補わない。端点間の純増減であり、連続観測を保証しない。
- 最終観測は失敗を含む実際の表示日最終L1時刻。ⓘには計算元の有効端点日時を表示する。記録のない日は「L1観測なし」、1点だけ等は「比較可能な観測なし」。
- Incidentは `detected` の表示日順、Scanは `started` の表示日順。未開始pending・skipped要求は実行回数に含めず、実行中・部分取得・失敗・中止は記録どおり表示する。
- ScanはL2/L3/L9の定期・Incident起因のみ集計。L1・無関係な手動要求は除外する。L9は既存runtimeが `trigger=request` で作成するため、保存されたinvestigationの実行ID（`scan_id`、`scans`、`target_evaluations`）からIncidentとの関連を確認する。検知日が別日のIncidentも照合する。`scan_ids` には比較元も混在するので、それだけで起因を判定しない。共有実行は一意IDで1回、定期Scanを共有した場合は定期のまま集計する。
- Incidentの3状態とL9代表理由は保存値を表示し、Viewerで再判定・再評価・更新しない。未特定AはL2の評価完了・比較件数あり・候補なしの保存経路、B/C/Dは保存済み評価理由に対応。旧形式・破損JSON・分類根拠不足は「分類記録なし」とし、件数から隠さない。
- 原因特定は保存されたfindingsを個別に表示。未特定BはL2/L3候補、AはL2/L3の保存差分、C/Dは対象別L9差分から最大の正の増加を参考表示する。親子・複数対象の差分は合算しない。前後容量・閾値の欠落を推測しない。比率は特定/Bのみで、実際のSSD空き容量減少量がある場合に算出する。
- 関連Scanは調査実行だけを並べる。比較元は明細に表示し、関連実行と混同しない。全体partialでも対象別特定の記録を維持する。対象/比較元の不完全・取得失敗・中止等は保存された状態・失敗抜粋で示し、生エラーは表示しない。

## 実装と負荷・安全性

`TodayData.swift` は保存JSONの表示モデル、`TodayReader.swift` は日付別SELECTと明細SELECT、`TodayView.swift` は画面・共通Scan展開を担当する。`ViewerModel` は日付選択と取得世代を管理し、古い日付・保存先の非同期結果を破棄する。既存の約60秒更新と手動更新で再取得する。取得失敗を「記録なし」とは扱わない。

DB接続は既存のbookmark・保存先/inode・sidecar照合を `withSource` で共有し、`SQLITE_OPEN_READONLY`・`query_only`・短い読取トランザクションを維持。全てUIスレッド外で実行する。監視DBのコピー、修復、スキーマ/保持変更、追加Scanは行わない。Widgetには日付別データ・パス・Incidentを渡さない。

当日件数と一覧は全該当行から取得する。L9の全結果を初期画面で読み込まず、展開時だけ容量・状態を読む。明細は総件数/失敗等件数と、失敗優先・容量降順の先頭100件を明示する。通常履歴が保持期限で消えたScanは、Incident保存済みのmetadata・changes・excerpt・failure_excerptsを表示する。未保存の証跡は「記録なし」。

## 確認

- `sh viewer/Tests/check-today.sh`：runtimeのSCHEMAから合成DBを生成。日付境界、欠測、A〜D、旧/破損JSON、共有・別日検知のL9 request、比較元/手動要求の除外、状態/件数、保持期限後の証跡、明細100件上限、SQL書込拒否とDBバイト不変、DST・年越しを確認。
- `CHECK_OUTPUT=local/issue34/regression sh viewer/Tests/check-storage-explorer.sh`：既存Viewerロジック・Storage Explorerの回帰確認。
- `sh viewer/Tests/render-today.sh`（`RenderToday.swift`）：合成DBを読んだ正式Viewの600/1000pt描画・ダーク表示、両タブ、展開したD証跡、空の日、previewモデルの日付移動。アプリdelegate・監視設定・本番DB・Widgetキャッシュを使わない。
- 署名なしのViewer/Widgetビルドはコンパイル確認。Personal Team署名/App Group共有・実Widgetホスト・実DB・実画面の操作受入は別途確認する。HTMLモックの受入はこれらの検証を代替しない。

Mergeだけでは正式アプリは更新されない。配置・起動終了・登録更新は別承認の工程。
