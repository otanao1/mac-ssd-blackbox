# Dashboard / Storage Explorer

Issue #22のモック確認を経て、既存のSwiftUI・Swift Charts・読み取り専用SQLite接続へ追加したViewer機能。監視周期、スキャン、監視設定、スキーマ、保持ルールを変更しない。

## 表示と操作（Issue #31）

受入済みの[操作・比較モック](../viewer/Mockups/StorageExplorer/Issue31/README.md)をUI基準とする。Dashboardの3カードはSSD空き容量、1時間前、24時間前からの使用量増加の順で、下部のL1→L3→L2へ移動する。各段は罫線と区別したセクション枠を持ち、Dashboardへ戻れる。L1は24時間の空き容量／前回比の切替を維持する。

フォルダ使用量の「特定アプリ」（L3）はSingle・3日、「全体」（L2）はMulti・7日で開始する。全幅タブ、タイトル脇の観測周期、期間表示左／操作右の構成。両レイヤーの期間は24時間・3日・7日・30日。期間変更時はENDを固定し、前後移動は選択期間の暦日単位で、最新を超える次移動は最新に留める。最新へ戻すとENDは更新に追従する。

Singleは容量時系列と選択観測のサブフォルダ一覧。対象選択の右にフルパス、下に親・パンくずを置く。子を開くと対象容量へ切り替わる。対象ルート、または記録済み直下子だけを容量観測とし、深い失敗行から未記録の中間階層を推定しない。子一覧はそのパス自身の `/*` 指定の記録がある場合だけ表示する。記録がなければ「サブフォルダ記録なし」。ファイルや容量取得不能項目はドリルダウン不可。partial容量は「取得済み」と表示し、比較可否とは区別する。

Multiは日本語名・正式パス・細い横棒、ランキング・行明細、初期3／最大5系列、子検索・10件ページ送り、グラフ／一覧の双方向移動を維持する。選択観測で親順位・子明細が連動する。期間・モード・更新では系列・階層・選択日時・検索を可能な限り維持し、選択日時が期間外なら最も近い枠を表示する（元の選択は保持）。選択日時の変更では子ページを先頭へ戻す。

既存の右上メニューに「初期表示に戻す」を追加する。モード・期間・系列・対象・階層・選択日時・展開・検索・ページ・L1表示切替を初期化し、最新へ戻す。保存先設定は消さない。表示選択は永続化せず再起動は初期値。読み取り中の期間変更は古い応答を採用せず、最新の要求で再取得する。

## 算出と欠測

- L1の有効値・±90秒比較・同距離で過去優先・符号・境界直前1件の取得は従来どおり。Widgetの24時間スナップショット形式も不変。
- L2／L3は `trigger=periodic` のみ。L2はローカル暦日、L3はローカル1時間枠（DST重複時間は別枠）ごとの最後の要求を代表とする。時刻は `started`、未開始は `created`。
- 代表が `scan_version=2` かつ `succeeded/partial` の場合だけ結果を読む。直前枠の代表と比較し、失敗・実行中・skipped・未記録の枠を飛ばさない。manual／incidentや他レイヤーで穴埋めしない。期間の先頭比較に必要な直前枠も取得する。日付・時間演算はCalendarを使用する。
- 比較は既存の共通 `StorageDay.delta` を利用する。同じ監視対象・種類で、双方ok・非負bytesなら通常差分。対象の変更、片側欠測、NULL・負値・種類変更は比較不可。
- partial同士は深い失敗子孫のパス・種類・status・エラーが一致し、各partialに失敗子孫がある場合だけ参考差分。失敗記録なし、エラー欠落、未知status・重複行は比較不可。生エラーは照合だけに使い画面へ出さない。正常な子は通常比較を維持する。
- Multiの子は前後観測の直下1階層の和集合。片側だけの項目を0や削除としない。参考の数値・説明・薄い横棒と順位計算を維持し、親子を合算しない。
- Singleは実測容量、Multiは比較可能な増減だけを点として表示する。正常取得は小さな丸、partialは背景色で内側を覆う中抜き菱形。全系列の線より後にマーカーを描く。
- 正常比較は実線、参考は薄い実線（不透明度40%暫定）、比較不可や欠測を跨ぐ部分は細かい点線の補助線。Multiの通常／参考境界も補助線で区別する。補助線は両端の実測点を結ぶだけで、途中の点・増減値・ホバー値を生成しない。線の見た目によって比較条件を緩めない。未取得領域内部は不明と説明する。
- SSD使用量増減棒は同じ比較元／比較先時刻に対応するL1（±90秒）から算出。端点を照合できなければ表示せず、フォルダ増減と一致させない。
- 親対象一覧は最新の定期要求の有効対象リスト。定期要求がなければ最後の同レイヤー記録。設定探索は行わない。
- L3は生履歴のみ。保持期間外・欠測は表示できず、日次集約を毎時観測の代替にしない。30日分の正常データが揃う保証はない。保存・集約処理は変更しない。

## 読み取りと保護

既存のbookmark・volume/inode照合・DB/sidecarのsymlink拒否を維持。接続は`SQLITE_OPEN_READONLY`、`query_only=ON`、短い読み取りトランザクション。同じスナップショットから最新値・比較値・L2／L3を読む。SQLは対象パス・時刻・scan IDをbindし、UIに生のSQL/OSエラーを表示しない。

Widgetのキャッシュには従来のL1表示データだけを共有し、L2／L3結果・対象パス・読み取り権限は共有しない。正式パスはViewer内の一覧／ホバーにのみ表示する。公開リポジトリに実測値や端末固有情報を含めない。

## Issue #31の検証

今回の検証は合成データのみ。本番DB・正式配置・監視サービス・設定を操作していない。

- 既存#22・#26の比較テストを維持。L3の毎時代表・欠測・partial照合・通常／参考／比較不可、DSTの23／25時間日、Singleの容量と保存済み階層、補助線が増減点を捏造しないこと、END維持・期間移動・5系列上限・選択／初期化を追加確認。
- 合成SQLiteで両レイヤー取得、manual／incident除外、L3集約を代替利用しないこと、履歴外のデータなし、READONLY・書き込み拒否・DB内容不変を確認。
- 本番ViewをNSHostingViewで合成描画。600pt幅でL2／L3 × Single／Multi（Multiは5系列・子展開）、カード順とセクション、全幅タブ、日時軸、参考／補助線・中抜きマーカー、子の容量／比較値・検索・ページUIを確認。画像は[UI基準のフォルダ](../viewer/Mockups/StorageExplorer/Issue31/)へ保存する。
- 合成previewモデルで保持期間外への移動とメニュー初期化に相当する呼出しを確認。描画ハーネスは監視DB・bookmark・タイマー・共有キャッシュを使わない。ScrollView全体のオフスクリーン取得では内容が欠落する場合があるため、Dashboardと各セクションの本番部品を直接描画した。全体スクロールやジャンプ等の実GUI操作の受入は別途行う。
- 既存Python 148件成功。Unix socketテストはsandbox制約で一度失敗したため、合成環境で同じテストを通常権限で再実行して全件成功。
- Viewer／Widgetの署名なしDebugビルド成功。署名・App Group・正式配置・実データ操作・実Widgetホスト・VoiceOver実操作・ユーザーの正式画面受入は未確認。これは過去の実機受入を取り消すものではなく、今回版の検証範囲。

再実行例：

```sh
CHECK_OUTPUT=local/issue31/checks sh viewer/Tests/check-storage-explorer.sh
.venv/bin/python -m unittest discover -s tests -v
xcodebuild -project viewer/BlackboxViewer.xcodeproj -scheme BlackboxViewer -configuration Debug -derivedDataPath local/issue31/DerivedData CODE_SIGNING_ALLOWED=NO build
xcrun swiftc -swift-version 5 -module-cache-path local/issue31/ModuleCache -parse-as-library viewer/Shared/*.swift viewer/App/StorageExplorerData.swift viewer/App/DatabaseReader.swift viewer/App/ViewerModel.swift viewer/App/ViewerDetail.swift viewer/Tests/StorageExplorerFixture.swift viewer/Tests/RenderStorageExplorer.swift -o local/issue31/RenderStorageExplorer
local/issue31/RenderStorageExplorer local/issue31
```

実GUIで操作する場合は同じ本番ソースとfixtureに `StorageExplorerGUIReview.swift` を組み合わせる（RenderStorageExplorerと同時に@mainを含めない）。正式Viewerの起動・登録・配置は行わない。

## 過去の検証記録（#22）

- Python 3.11の既存テスト138件。
- `ViewerLogicChecks`：鮮度・遅延表示・Widget更新判定・欠測分断・軸日時。
- `StorageExplorerChecks`：L1の±90秒境界／同距離／符号／無効値、L2親子・片側欠測・部分取得・種類変更・v1除外、7暦日とDST、L1/L2対応、定期以外の除外。合成SQLiteでREADONLY・書き込み拒否・ファイル内容不変を検証。
- Xcode DebugのViewer＋Widgetビルド。実DBは読み取り専用で保存先照合・SELECTの動作確認のみ実施し、生レコードは出力しない。
- 本実装のViewerDetailを合成データで動かすGUIレビュー：600ptカード、5系列上限、子検索、グラフ選択と親子欠測連動、双方向ジャンプ、ウィンドウ拡大。
- 本番Widget内容の小・中サイズを5状態で合成データ描画。WidgetKitホストの長期稼働・再署名後の配置維持などは別の実機確認が必要。

検証の再実行（リポジトリルート）：

```sh
.venv/bin/python -m unittest discover -s tests -v
sh viewer/Tests/check-storage-explorer.sh
xcodebuild -project viewer/BlackboxViewer.xcodeproj -scheme BlackboxViewer -configuration Debug -derivedDataPath local/issue22/DerivedData build
```

GUIレビューは`StorageExplorerFixture.swift`と`StorageExplorerGUIReview.swift`を本番のApp/Sharedソース（Appの`@main`を除く）と一緒にコンパイルする。これらは本番Xcodeターゲットに含めない。

## Issue #26の追加検証

- 合成ロジック：親・子の参考差分、通常差分の維持、深い失敗の追加・消失、status・種類・エラー変更、失敗記録不足、パス境界、NULL・負値を確認。
- 合成SQLite：エラー文字列を含む読み取りから参考差分を確認。通常差分・READONLY・書き込み拒否・読み取り前後のDB内容不変も確認。
- Viewer／Widgetの署名なしDebugビルド成功。署名・App Group・実Widgetホスト・正式配置後の画面受入は今回未確認。
- 合成画面（600pt幅）：`NSHostingView`のオフスクリーン描画で、親ランキングの「参考」・薄い横棒、通常値の実線・丸い点との区別、参考値の破線・四角い点、欠測区間の分断、未取得領域の不明性を示す凡例に文字欠け・重なりがないことを確認。合成画像であり、正式版の画面受入ではない。
- 空画像の切り分け：同じ環境の`ImageRenderer`では単純なTextは描画できたが、最小の`ScrollView`・`ScrollViewReader`・ViewerDetailは全画素透明になった。`NSHostingView`では内容を取得できたため、空画像はこの取得方法とスクロールViewの組み合わせで再現する制約と判断した。製品Viewの変更による対処はしていない。
- 未確認：子一覧の参考表示と説明ポップオーバーの実操作、ホバー、系列切替・日付選択時の表示、VoiceOverでの読み上げ。参考／通常の境界の線分割は実装済みだが、その遷移を含む画面は未確認。

## 制約

Incident/L9連携、L3日次集約による長期表示は対象外。履歴不足や部分取得では表示できる値が限られる。L2はスキャン中の各項目の個別時刻を持たず、代表開始時刻を使用する。L1とL2は監視範囲・測定時間・APFSの特性が異なり、親合計とSSD変化は一致しない。
