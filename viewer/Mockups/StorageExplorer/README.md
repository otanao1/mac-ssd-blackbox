# Storage Explorer：最終モック

Issue #22の最終確認用。合意したDashboard・L2構成を維持し、24時間／1時間比較のカード名・実比較時刻と、前日定期観測がない場合の欠測を反映済み。L1比較は本実装と同じ`StorageMath`を使う。すべて架空データで、監視DB・設定には接続しない。

```sh
sh viewer/Mockups/StorageExplorer/build.sh
open local/issue22/BlackboxViewerMock.app
```

本実装は[Viewer説明](../../README.md)、算出・検証方法は[Storage Explorer仕様](../../../docs/storage-explorer-viewer.md)を参照。

- 最新観測日時はDashboard右上。比較カードは2行を保ち、実際に使用した比較元時刻を灰色で数値の左へ表示。
- L1は24時間の空き容量／増減切替、0:00だけの点線、右上[GiB]。
- L2はB案ランキング、初期3／最大5折れ線、別枠のL1棒、7日間固定。親行で系列選択し、子件数から展開・検索・ページ送り。
- グラフクリックで日付選択し、親・子の値を連動。4/18は当日観測なし、4/19は前日観測なしで、どちらも差分と棒は欠測。別の日で埋めない。
- グラフ／リストアイコンで相互移動。正式パス＋現在容量の2行ホバー、比較元／比較先の2行ⓘ。
- 右上メニューの更新・設定はモック応答のみ。「モック検証」の設定は実監視に影響しない。

静止画：

```sh
local/issue22/StorageExplorerMock-final --render --section cards --width 600 --height 105 "$PWD/local/issue22/final-comparison-cards.png"
local/issue22/StorageExplorerMock-final --render --width 600 --height 1130 "$PWD/local/issue22/final-mock.png"
```

既存の`--day 0..6`、`--children`、`--changes`、`--sparse`、`--missing-hour`も利用可能。GUIに専用コンパクトモードはない。静止画のスクロール／入力欄だけは描画用表現へ置き換える。本実装のGUIレビューは`viewer/Tests/StorageExplorerGUIReview.swift`を使用し、合成データで本番Viewを検証する。
