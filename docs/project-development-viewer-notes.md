# Blackbox Viewer 開発メモ

Viewerの開発・保守で再利用する技術メモです。ビルドと基本操作は[利用ガイド](../viewer/README.md)、監視エンジンの導入は[operations](operations.md)を参照してください。
本メモは既存実装とPR #20までの検証結果を整理したもので、仕様の追加や検証完了の宣言ではありません。
背景は[Issue #19](https://github.com/otanao1/mac-ssd-blackbox/issues/19)、実装・レビューの経緯は[PR #20](https://github.com/otanao1/mac-ssd-blackbox/pull/20)を参照してください。

## 実装の責務

| 場所 | 責務 |
| --- | --- |
| `viewer/App` | 更新モデル、保存先選択、読み取り専用SQLite参照、詳細画面、MenuBarExtra。 |
| `viewer/Shared` | パスを含まない表示モデル、24時間グラフ、App Group内の表示キャッシュ。 |
| `viewer/Widget` | 表示キャッシュを読むTimelineProviderと小・中サイズの描画。 |
| `viewer/Tests` | DBを使わないロジック確認とローカル描画確認。アプリ・拡張ターゲットには含めない。 |

更新モデルはアプリのStateObjectが所有し、ウィンドウの表示・非表示に結び付けません。
ウィンドウを閉じても更新を継続し、プロセス終了で更新を停止します。別の常駐ヘルパーはありません。

## 日付別Incident・Scan画面

Issue #34の「今日の状況」は `TodayData.swift`・`TodayReader.swift`・`TodayView.swift` に分離。既存Dashboard・Widgetと同じ保存先の読取保護を `DatabaseReader.withSource` で共有し、日付別取得の失敗を既存Dashboardの成功/失敗へ混在させない。表示規則・検証方法は[今日の状況](today-viewer.md)を参照。

## Personal TeamとApp Group

現行の[Config.xcconfig](../viewer/Config.xcconfig)は、Git管理外の `Local.xcconfig` からTeamを受け取り、
App Groupを `<Team ID>.io.github.otanao1.blackbox-viewer` として両ターゲットで共有します。
Viewer・WidgetのentitlementsとInfo.plistは同じ設定値を参照します。

実機試作では、無料のPersonal Teamにより次の組み合わせで署名・共有を確認しました。

```xcconfig
DEVELOPMENT_TEAM = YOUR_TEAM_ID
CODE_SIGN_IDENTITY = Apple Development
REGISTER_APP_GROUPS = NO
```

`REGISTER_APP_GROUPS = NO` は今回確認したTeam ID形式のmacOS App Groupを使う設定です。
有料Programや配布用署名の成立、すべてのPersonal Team環境での成立まで確認したものではありません。
ad-hoc署名だけでApp Groupが動作することも前提にしません。

調査では「ビルド成功」「Viewer・埋め込みWidget両方の署名検証」「実際のApp Groupへの保存・読み出し」を分けて確認します。
ビルドが通っても共有の成功とは限りません。共有先を解決できない場合は警告とし、任意の代替パスへ書き込みません。
個人のTeam・署名設定やXcodeのユーザー設定はGitへ追加しません。

## WidgetKitの表示更新とキャッシュ

### 取得・保存・表示を分けて切り分ける

1. 監視のL1観測時刻が進んでいるか。
2. Viewerがその観測を取得し、App Groupの表示キャッシュを更新しているか。
3. Widgetが新しいタイムラインを読み込み、表示に反映しているか。

表示キャッシュは `l1-display.json`。バージョン、生成時刻、観測履歴、最新観測、状態のみを共有し、元DBのパスやbookmarkは含めません。
原子的なJSON保存の完了後に再読み込みを要求します。Widgetは元DBを参照しません。
読み込み時には形式バージョン、サイズ（2,000,000バイト以下）、観測数（10,000件以下）を確認し、不正な場合は未設定相当の表示モデルに戻します。

### 古さ判定と再読み込み要求の整合

旧実装では180秒超の観測を古いと判定する一方、再読み込み要求を900秒抑制していました。
共有キャッシュが更新されても古いタイムラインが警告を表示し得るため、現行実装は次の規則です。

- 新しい観測・状態変更で要求。同じ観測時刻・空き容量・状態の再保存だけでは要求しない。
- 手動更新は強制的に要求する。
- Providerは最新観測の181秒後のエントリを必要に応じて追加し、架空の容量値を作らず古い表示へ切り替える。
- Providerの通常の次回更新希望は15分後。要求・希望時刻どおりの描画は保証しない。
- 「表示更新待ち・前回の観測値」、取得失敗、Viewer終了を別の表示として扱う。

通常終了ではキャッシュに `stopped` を保存して再読み込みを要求します。異常終了時は最終観測時刻による古さ判定が必要です。
これらは表示の整合を保つ処理であり、Widgetの即時・毎分更新を保証する処理ではありません。

## Widgetが旧版のまま表示される場合

データが更新されるのに色・見出し・配置だけ旧版なら、共有キャッシュに加えて**読み込み済みの拡張実体**を確認します。
PR #20の実機では登録パスは正しくても、プロセスが再ビルド前の実行ファイルとdebug dylibを保持していました。
読み込み済みファイルとディスク上の成果物のinodeの不一致から、旧バイナリの継続使用を確認しています。

1. 起動中Viewerの実体と、今回ビルドしたアプリのパスを照合する。
2. Widget拡張の登録先と重複登録の有無を確認する。
3. 実行中拡張の開始時刻・読み込み済みファイルを、ディスク上の拡張と照合する。パス一致だけで新版と判断しない。
4. 旧版と確認できた当該Blackbox Widgetプロセスのみを通常終了し、同じ成果物のViewerから手動更新する。
5. 新プロセスが最新成果物を読み込んだことと実画面の両方を確認する。
6. 必要なら当該Widgetだけを削除し、デスクトップの「ウィジェットを編集」から再追加する。

対象拡張の更新後、新しいinodeとの一致と、オレンジのグラフ・時計・見出しなしの表示を確認済みです。
Viewer・Widgetのビルド番号はそろえて更新しますが、それだけで既存プロセスの置換を保証しません。
他アプリのWidgetやNotification Center、Dockの設定を一括初期化する対処は行いません。

## Dockのアプリアイコンが更新されない場合

FinderとDockを別々に確認します。Finderで新版が見えても、Dockが旧アイコンを保持することがあります。

1. AppIconのターゲット設定、ビルド成果物の `AppIcon.icns`・`Assets.car`、Info.plistのアイコン指定を確認する。
2. Finderの「情報を見る」で新アイコンを確認する。
3. 起動中Viewerの実体を確認し、同じ成果物を終了・再起動する。必要なら当該アプリだけをLaunch Servicesへ再登録する。
4. それでもDockだけ旧版なら、Dockプロセスのみの通常終了・macOSによる再起動を検討する。Dockが一時的に消えるため、作業中でないことを確認して行う。
5. Dockが戻った後に実画面を確認する。設定ファイルの削除・初期化は不要。

今回の実機ではViewerの再起動・当該アプリの再登録だけでは変わらず、Dockプロセスの再起動後に新表示をユーザー確認しました。
同じ症状が常に同じ原因とは限らないため、最初からDockを再起動するのではなく、成果物・起動実体・Finder表示の順に確認します。

## Swift Chartsとメニューバーの描画

### 軸と補助線

現行表示はY軸0始まり、3時間ごとのX軸、0:00だけ通常の点線より少し太い実線です。
縦線は `RuleMark` で描画領域に限定します。今回の軸構成では `AxisGridLine` がラベル領域まで伸びたため、X軸側はラベルのみとしています。
左端・右端に近いラベルのアンカーを内側へ寄せて欠けを防ぎ、日付は軸ラベルに重ねずグラフ上の期間表示へ分離しています。
狭い600pt幅と期間境界の端点をローカル描画で確認しました。新たなレイアウト変更時は端点も再確認してください。

### 欠測と補間

表示処理と実データの欠測を分けて調べます。毎分観測に対して30秒の遅れを許容し、90秒超の間隔またはNULLで系列を分けます。
1点だけの系列はLineMarkでは線にならないためPointMarkで表示し、前後の系列へつなぎません。
調査時は150秒超の7区間に加え100〜147秒の4区間があり、現行表示では計11区間を分断しました。これはその観測期間の結果であり、固定の欠測数ではありません。
監視周期や取得処理を変更して分断を隠すことはしていません。

同じ実データの直線・単調三次補間を比較したところ、24時間表示での差は小さく、急変の形を変える利点を確認できませんでした。
現行は直線です。補間変更時は急変の時刻・規模と欠測の保持を優先して実データで比較し、画面確認なしに変更しません。

### メニューバー

MenuBarExtraのラベルに `Label` を使った際、アイコンだけになり数値が表示されませんでした。
現行は `HStack` にアイコンとTextを明示し、数値にRegular・等幅数字を使用しています。
メニュー項目・設定選択・ウィンドウ再表示は、ラベルの描画確認と分けて実機確認します。

## 読み取り専用アクセスの維持

[DatabaseReader.swift](../viewer/App/DatabaseReader.swift)は `SQLITE_OPEN_READONLY`、`sqlite3_db_readonly`、接続単位の `query_only` を使用します。
短い読み取りトランザクションで最新値と直近24時間を同じスナップショットとして取得し、接続を閉じます。busy待機は100msまでです。
SQLite処理はUIスレッド外で実行します。

`immutable`、ロック無効化、WAL切替、スキーマ変更、DBコピーは行いません。
DELETE journal方式でリカバリーが必要でもViewerが書き込んで復旧せず、取得失敗として扱います。
保存先とDBの識別照合、DB・sidecarのシンボリックリンク拒否を維持し、実パス・生エラーを画面や公開ログへ出さないようにします。

## ビルドと確認方法

DerivedData・描画成果物は空きのある外付けSSD等のGit管理外領域へ置き、内蔵SSDの空きをビルド前後で確認します。
macOS実機のみを対象にする場合は、不要なSimulator・SDKを追加しません。
署名情報・監視DB・履歴・実データ入りの比較画像・端末固有パスを公開しないでください。

| 確認用ソース | 目的 |
| --- | --- |
| [ViewerLogicChecks.swift](../viewer/Tests/ViewerLogicChecks.swift) | DBなしで鮮度、遅延時の文言、再読み込み判定、NULL・90秒境界の分断、期間境界、3時間軸・日付・夏時間・年初の日時表記を確認。 |
| [RenderChartComparison.swift](../viewer/Tests/RenderChartComparison.swift) | 表示キャッシュを入力して直線と単調三次補間を比較。元DBは参照しない。 |
| [RenderWidgetLayout.swift](../viewer/Tests/RenderWidgetLayout.swift) | `-D WIDGET_LAYOUT_CHECK` で本番Widget内容を小164×164pt／中344×164ptに描画。通常・3桁容量・古い表示・取得失敗・未設定を比較。 |

確認用ソースはアプリ・Widgetターゲットに含めません。描画成功はWidgetKitホストでの動作確認の代わりにはなりません。

### 検証範囲

PR #20までに確認した事項と残る確認を区別します。これは過去の検証範囲であり、以後のビルドの結果を保証しません。

| 区分 | 範囲 |
| --- | --- |
| 実機で確認済み | Xcode 27.0、macOS arm64のDebugビルドとPersonal Team署名、Viewer・Widgetの署名検証、実L1グラフ、App Group共有。 |
| 実機・ユーザー確認済み | メニューバーのアイコン＋容量・メニュー操作、Widgetの値とグラフ・Viewer遷移、新Widgetレイアウト、Finder・Dockの新アイコン。 |
| 短時間の動作確認済み | ウィンドウを閉じた64秒間にキャッシュとL1観測が約60秒進行。終了時のプロセス消滅と `stopped` 保存。再起動後の保存先保持。 |
| ロジック・描画確認済み | 上記ロジックチェック、小・中Widgetの5状態の文字欠け・重なり、Viewerの600pt幅での軸端ラベルと補助線。 |
| 未確認・追加検証が必要 | 長時間稼働・実機負荷の継続評価、Widgetの全状態・更新間隔・異常終了後の挙動、外付け切断・再接続・同名別ボリューム・DB置換・bookmark失効・ロック競合。 |

試作方式の正式採用やL3可視化は、この検証だけで決定していません。

## アイコン素材と更新

[AppIcon.appiconset](../viewer/App/Assets.xcassets/AppIcon.appiconset)はユーザー提供のCanva画像（576×576 PNG）を基に、
組み込みimagegenで外周背景を透過・余白調整したものです。銀色SSD・オレンジの折れ線・濃い角丸タイルを維持しています。
16〜1024pxの表現を収録し、原画像の解像度を超える表現は編集・拡大したものです。
ViewerのDebug／Release両構成でAppIconを指定しています。PR #20時点の共通ビルド番号は3です。

素材を再調整する場合の元の編集指示：

```text
Edit target: supplied macOS app icon. Preserve the existing design as closely as possible: silver SSD drive, orange angular graph, dark rounded-square tile. Only remove the outer rectangular gray background OUTSIDE the rounded square, replacing it with genuine transparency; retain the rounded-square tile and soft subtle shadow. Center the tile, with balanced macOS icon margins, about 80% of full square canvas width. Produce a crisp 1024x1024 PNG. Do not add text, change orange graph path, redraw the drive, add details, or alter the core design. Use case background-extraction.
```
