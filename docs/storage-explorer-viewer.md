# Dashboard / Storage Explorer

Issue #22のモック確認を経て、既存のSwiftUI・Swift Charts・読み取り専用SQLite接続へ追加したViewer機能。監視周期、スキャン、監視設定、スキーマ、保持ルールを変更しない。

## 表示と操作（Issue #31）

受入済みの[操作・比較モック](../viewer/Mockups/StorageExplorer/Issue31/README.md)をUI基準とする。Dashboardの3カードはSSD空き容量、1時間前、24時間前からの使用量増加の順で、下部のL1→L3→L2へ移動する。各段は罫線と区別したセクション枠を持ち、Dashboardへ戻れる。L1は24時間の空き容量／前回比の切替を維持する。

フォルダ使用量の「特定アプリ」（L3）はSingle・容量・3日、「全体」（L2）はMulti・差分・7日で開始する。全幅タブ、タイトル脇の観測周期、期間表示左／操作右の構成。GUIレビュー後の合意でL2は7日・30日、L3は24時間・3日・7日・30日。期間変更時はENDを固定し、前後移動は選択期間の暦日単位で、最新を超える次移動は最新に留める。最新へ戻すとENDは更新に追従する。

Single／Multiと容量／差分を独立させ、各レイヤーで4組み合わせを使える。グラフ右上に相互矢印＋切替先名のボタンを1つ配置し、現在値は隣の「容量／容量差分（GiB）」で示す。Multiの容量表示でも増減ランキング・行明細は維持する。

Singleは容量または差分の時系列と選択観測のサブフォルダ一覧。対象選択の右にフルパス、下に親・パンくずを置く。子を開くと容量／差分を維持してその子のグラフへ切り替わる。対象ルート、または記録済み直下子だけを容量観測とし、深い失敗行から未記録の中間階層を推定しない。子一覧はそのパス自身の `/*` 指定の記録がある場合だけ表示する。記録がなければ「サブフォルダ記録なし」。ファイルや容量取得不能項目はドリルダウン不可。partial容量は「取得済み」と表示し、比較可否とは区別する。

Multiは日本語名・正式パス・細い横棒、ランキング・行明細、初期3／最大5系列、子検索・10件ページ送り、グラフ／一覧の双方向移動を維持する。選択観測で親順位・子明細が連動する。期間・モード・更新では系列・階層・選択日時・検索を可能な限り維持し、選択日時が期間外なら最も近い枠を表示する（元の選択は保持）。選択日時の変更では子ページを先頭へ戻す。

既存の右上メニューに「初期表示に戻す」を追加する。モード・容量／差分・期間・系列・対象・階層・選択日時・展開・検索・ページ・L1表示切替を初期化し、最新へ戻す。保存先設定は消さない。表示選択は永続化せず再起動は初期値。読み取り中の期間変更は古い応答を採用せず、最新の要求で再取得する。

## 算出と欠測

- L1の有効値・±90秒比較・同距離で過去優先・符号・境界直前1件の取得は従来どおり。Widgetの24時間スナップショット形式も不変。
- L2／L3は `trigger=periodic` のみ。L2はローカル暦日、L3はローカル1時間枠（DST重複時間は別枠）ごとの最後の要求を代表とする。時刻は `started`、未開始は `created`。
- 代表が `scan_version=2` かつ `succeeded/partial` の場合だけ結果を読む。直前枠の代表と比較し、失敗・実行中・skipped・未記録の枠を飛ばさない。manual／incidentや他レイヤーで穴埋めしない。期間の先頭比較に必要な直前枠も取得する。日付・時間演算はCalendarを使用する。
- 比較は既存の共通 `StorageDay.delta` を利用する。同じ監視対象・種類で、双方ok・非負bytesなら通常差分。対象の変更、片側欠測、NULL・負値・種類変更は比較不可。
- partial同士は深い失敗子孫のパス・種類・status・エラーが一致し、各partialに失敗子孫がある場合だけ参考差分。失敗記録なし、エラー欠落、未知status・重複行は比較不可。生エラーは照合だけに使い画面へ出さない。正常な子は通常比較を維持する。
- Multiの子は前後観測の直下1階層の和集合。片側だけの項目を0や削除としない。参考の数値・説明・薄い横棒と順位計算を維持し、親子を合算しない。
- 容量表示は実測容量、差分表示は比較可能な増減だけを点として表示する。正常取得は小さな丸、partialは背景色で内側を覆う中抜き菱形。全系列の線より後にマーカーを描く。
- 正常比較は実線、参考は薄い実線（不透明度40%暫定）、比較不可や欠測を跨ぐ部分は細かい点線の補助線。差分表示の通常／参考境界も補助線で区別する。補助線は両端の実測点を結ぶだけで、途中の点・増減値・ホバー値を生成しない。線の見た目によって比較条件を緩めない。未取得領域内部は不明と説明する。
- SSD使用量増減棒はSingle／Multiとも差分グラフだけに表示し、容量グラフには重ねない。同じ比較元／比較先時刻に対応するL1（±90秒）から算出。端点を照合できなければ表示せず、フォルダ増減と一致させない。
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

### 合成GUIレビュー版の準備・起動

```sh
REVIEW_OUTPUT=local/issue31/GUIReview/MetricToggle sh viewer/Tests/prepare-storage-explorer-review.sh
open local/issue31/GUIReview/MetricToggle/StorageExplorerReview.app
```

本番とは別ID `io.github.otanao1.blackbox-viewer.issue31-review`、App Group・本番entitlements・Widget・App delegateなしで、本番Viewと合成previewモデルを動かす。既存署名や正式配置を変更しない。「設定…」は合成モードで実DB選択に進まず、更新も合成データだけを再生成する。終了時も本番のWidget終了状態を書かない。再現用fixtureの基準時刻は2026/04/21 18:00で、実際の現在時刻ではない。

既存レビュー版は保持し、新版はMetricToggle配下に分離する。起動中の旧レビュー版はユーザーが終了してから新版を開く。

カードのジャンプ、L2／L3 × Single／Multi × 容量／差分、期間選択・前後／最新移動、グラフの観測選択、ユーザーライブラリ→Application Supportのドリルダウンと親／パンくず、Multiの5系列上限・子検索／ページ、「初期表示に戻す」を確認する。検証成果物はユーザーの受入完了まで保持する。

### 容量／差分の独立切替の追加検証

- `StorageMetric`をモードと独立した選択状態に追加。モード切替・データ再初期化で表示値や階層・観測・系列を維持し、リセットではL3 Single・容量、L2 Multi・差分へ戻ることを合成ロジックで確認。
- 既存の通常／参考／比較不可の回帰テストと読み取り専用SQLite検証が成功。比較関数とDB取得処理は変更していない。差分点は比較可能な時刻だけ、容量点は取得済み容量だけを使う。
- L2／L3 × Single／Multi × 容量／差分の8通りを600pt幅で合成描画。Multiは5系列。トグルの切替先・GiB表記・対象凡例、容量ではSSD差分棒を混在させないことを確認。実GUIでの新トグル操作とユーザー再受入は未実施。
- Viewer／Widgetの署名なしDebugビルドと合成レビュー版ビルドを実施。本番環境・起動中アプリの操作は行わない。前回の性能計測は下記の過去結果で、今回再計測・最適化はしていない。Python監視テストも今回は再実行していない。

### Merge前GUIレビューの修正・処理時間調査

ユーザーから、タブの文字以外がクリックできないことと、L2の短期間選択肢の削除を指摘。その他の開発版GUI操作・表示は受入OKとの報告を受領した。本番での受入とは区別する。

- Buttonラベルの全幅・上下余白を含めてRectangleのcontentShapeを指定。合成GUIでL2／L3とも文字から離れた余白からモード切替を確認。
- L2の期間を7日・30日に限定。L3は4種類を維持。UIとモデルで同じ選択肢を使用し、L2に1日／3日の要求を渡しても採用しないことをテスト。
- 合成GUIのメニューでL2の2種類、L3の4種類を確認。ここまでの修正以外の表示・比較ロジックは変更しない。

処理時間はレビュー版と同じ非最適化コンパイル・合成データ（L2 7枠／L3 72枠、Multi 3系列）で各3回計測した。以下は今回の範囲であり、実データ・Release版・全履歴長の保証ではない。

| 処理 | 実測時間 |
| --- | --- |
| レビュー用全合成履歴の生成＋snapshot組立 | 330〜337ms |
| グラフ入力（点・線分、比較判定を含む） | L2 4〜5ms、L3 2.5〜25.3ms |
| 選択時点の順位＋子一覧 | L2 約8ms、L3 約8〜10ms |
| 全画面の同期構築・layout | 459〜532ms |
| 全画面のオフスクリーン描画要求 | 307〜468ms |
| 合成SQLiteの初期期間読み取り | 29〜31ms |

画面構築には比較入力等も含むため、全行を単純加算しない。描画測定前のRunLoop待機約30msは表から除外した。各セクション単体では温まった状態の構築＋描画が約0.20〜0.42秒、最初のL3 Singleは約0.77秒だった。全画面オフスクリーン取得には既述の内容欠落制約があり、この計測は実クリックから画面提示までの正確な遅延計測ではない。

固定の1〜2秒待機を入れた仕様ではない。期間変更・更新では、レビュー版のpreviewLoaderが表示範囲によらず約750時間の合成履歴全体をメインスレッドで作り直し、その後にUIを再構築するため、約0.33秒＋画面構築／描画約0.77〜1.00秒の仕事がある。窓変更の通知と読み取り完了の通知で複数回のView更新が生じる可能性もある。レビュー版のこの経路ではDB読み取りは行わない。

モード切替は状態だけを変更し、合成データ再生成もSQL読み取りも行わない。Chartの構築・layout・描画と、computed propertyの再評価が対象になる。ユーザーが観測したモード切替の1〜2秒全体は、上記の独立した構成要素測定だけでは完全に説明・再現できていない。必要なら実クリックのCPU／フレーム計測で残りを切り分ける。

改善候補は、まずレビュー専用の合成履歴を再利用して期間だけ抽出すること。製品側では点・線分の重複比較計算の再利用やChart再構築範囲の縮小が候補だが、比較入力は数十msで、先にUI更新の実測が必要。今回はこれらの最適化は適用していない。描画仕様・比較条件を変える最適化や大きな構成変更は不要と判断している。

再計測は、RenderStorageExplorerのコンパイル例の最後の入力を `viewer/Tests/StorageExplorerPerformance.swift`、出力を `local/issue31/StorageExplorerPerformance` に替え、同実行ファイルへ `local/issue31` を引数として渡す。合成SQLiteだけを一時作成して自分のファイルを終了時に整理する。計測ハーネスは製品ターゲットに含めない。

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
