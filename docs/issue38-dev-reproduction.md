# Issue #38 DEV環境の再現

新規DEV環境用の手順。成功済み受入を再実施する指示ではない。稼働中の38FDAへ重ねてinstallせず、本番のDB・設定・キャッシュを参照/コピーしない。ビルドは配置ではなく、FDA・登録・起動・更新・削除は対象と承認範囲を確認してから行う。

## 共通ソースからのビルド

既存のXcodeプロジェクトと`tools/build_monitor_bundle.py`を使用する。`viewer/Local.xcconfig`は非公開の既存署名設定。以下の変数は毎回新しく確認したDEV専用値であり、本番の既定private/runtime/CLIを使わない。

```sh
DEV_ID=io.github.otanao1.blackbox-viewer.dev.issue38.fda
DEV_GROUP="${DEVELOPMENT_TEAM}.${DEV_ID}"
DEV_ROOT="$PWD/local/issue38/dev-new"
```

`DEV_ROOT`は外付けSSD上の未使用領域とする。制御socketはprivateパス長に上限があるため深くしすぎない。元の共通ソースをコピーして書き換えたり、署名後に実行ファイル名・Bundle IDを差し替えたりしない。

```sh
xcodebuild -project viewer/BlackboxViewer.xcodeproj -scheme BlackboxViewer \
  -configuration Release -derivedDataPath "$DEV_ROOT/DerivedData" \
  -destination 'platform=macOS,arch=arm64' ONLY_ACTIVE_ARCH=YES \
  REGISTER_APP_WITH_LAUNCH_SERVICES=NO \
  BLACKBOX_BUNDLE_ID="$DEV_ID" BLACKBOX_APP_GROUP="$DEV_GROUP" \
  BLACKBOX_DISPLAY_NAME='Blackbox Viewer DEV Issue38 FDA' \
  BLACKBOX_WIDGET_DISPLAY_NAME='Blackbox Widget DEV Issue38 FDA' \
  BLACKBOX_WIDGET_TITLE='SSD Issue38 FDA — 検証専用' \
  BLACKBOX_URL_SCHEME=blackbox-viewer-dev-issue38-fda \
  BLACKBOX_WIDGET_KIND=BlackboxL1WidgetIssue38FDA \
  BLACKBOX_BUILD_VERSION=1 build

.venv/bin/python -B tools/build_monitor_bundle.py \
  --viewer "$DEV_ROOT/DerivedData/Build/Products/Release/BlackboxViewer.app" \
  --output "$DEV_ROOT/BlackboxViewerDEV38.app" \
  --python "$PYTHON_INPUT" --identity "$SIGN_IDENTITY" \
  --licenses "$LICENSE_INPUT" --minimum-macos "$MINIMUM_MACOS"
```

Team/署名者/Python/ライセンス入力は非公開で明示する。上のarchitecture・最低OSは**今回確認した環境向けの例**。入力の全Mach-OとViewerのarchitecture・最低OSを揃える。26用入力を14対応とみなさず、14向けは別の適合入力が必要。詳細は[提供範囲](issue38-release-checklist.md#利用配布の範囲とゲート)を参照。

通常パッケージは通常Scannerで、manifestの`runtime_profile`は`standard`。過去のFDA限定アダプターを再現する必要がある場合のみ、パッケージ工程に`--fda-access-only`を追加する。Issue番号付きDEV ID以外では拒否し、manifestは`dev-fda-access-only`となる。対象はBluetooth/Mailの合意済みルートだけで、du出力の容量・配下名は破棄しエラー種別だけを記録する。ビルドだけではScanしない。このプロファイルを本番用成果物として配布しない。

共通ヘルパー名は`Blackbox Monitor.app` / `BlackboxMonitor`のまま。識別は外側のDEV Bundle IDと監視Bundle ID/ジョブラベルで分離できる。過去の38FDA専用実体名・文字列置換の手順は不要。診断ビルド6のキャッシュパス表示/ログも通常DEV・本番には含めない。

完成アプリの正規配置先をユーザーApplications内のDEV専用名で1か所に決め、起動/FDA選択前にBundle IDの解決先・実体パス・Widget登録を確認する。入力アプリ・更新ステージ・旧コピーは実行/登録せず、復旧用はアーカイブで保持する。同じIDのコピーが複数ある時に曖昧なアプリ名で起動しない。
Xcodeの通常buildは出力アプリをLaunch Servicesへ自動登録する場合があるため、上の設定で抑止し、ビルドログと実登録を確認する。不要な生成コピーが登録された場合はその生成パスだけを解除し、既存の正式登録を一括初期化しない。未導入の正式ID候補も、検証後はZIPで保持して選択対象を増やさない。

## 固定42 GiB合成DBと識別JSON

```sh
.venv/bin/python -B tools/prepare_widget_fixture.py \
  --root "$DEV_ROOT/widget-fixture" --mount /Volumes/SSD
```

`DEV_ROOT`までの親は事前に用意し、fixtureルートは新規でなければ拒否する。コード定義のSQLiteスキーマから作成し、本番/既存DBをコピーしない。L1は60秒刻みの44→43→42.5→42 GiB、Scanは0件。実体からvolume・directory inode・DB inodeを生成し、SQLite整合性と値を確認する。

Viewerで選ぶ組み合わせは、生成された`widget-fixture/viewer-source.json`と`widget-fixture/data`。ファイルの移動・コピー・DB置換後に旧inodeを流用せず、新規fixtureとして生成する。このJSONはViewerの識別情報入力であり、監視起動用設定全体ではない。

固定DBには追記しない。Widgetの時計は最終L1観測時刻であり、現在時刻やキャッシュ生成時刻ではない。値と時刻が固定なのは仕様。監視継続/再ログイン後の時刻進行は次のライブDEV DBで確認する。

## ライブDEV監視DB

1. DEV専用の新規data/空のfixture/private/runtime/CLIを用意する。macOSの一時領域に依存せず、成功した外付けSSD配置を基準とする。dataは空ディレクトリとして用意し、実体の識別生成には既存installを使う。
2. 正規配置したDEVアプリ自身の標準名ヘルパーで、全ての保存場所を明示してinstallする。最初は`--register`なし。共有Pythonで同梱版installを代行しない。

   ```sh
   "$DEV_APP/Contents/Helpers/Blackbox Monitor.app/Contents/MacOS/BlackboxMonitor" \
     --config-dir "$DEV_ROOT/private" install --app-bundle "$DEV_APP" \
     --storage "$DEV_ROOT/data" --mount /Volumes/SSD \
     --runtime "$DEV_ROOT/runtime" --cli "$DEV_ROOT/bb"
   ```

3. 既存`tools.verify_monitor_bundle.prepare_test_configuration()`で共通と同名のprivate confを空にし、JSONのL2/L3対象も無効化する。共通設定そのものは変更しない。更新候補の追加conf名も同時にマスクし、`require_no_periodic_targets()`で実ローダーのL2/L3が0件と確認する。**署名済みのアプリ内へ書き込まない**。引数commonには外側runtimeの`config`を指定する。
4. `l1_path`はDEV専用空fixture、L1周期は60秒を明示する。L1は`statvfs`による所属ボリュームの実容量観測で、ファイル列挙/du Scanではない。ここに表示される容量は固定合成値ではない。全領域の広域Scanを暗黙に開始しない。
5. 起動前に保存先識別・アプリ/ラベル/private/runtime/CLI/plistの一致を確認し、承認後にDEVだけ起動する。`bb start`による現在セッションへのbootstrapとログイン時登録は別。ログイン受入が必要なら、ログイン用plist配置と`installation.json`の登録状態を既存登録処理と整合させ、所有関係検証後に適用する。既存installへ`--register`を追加して再実行しても昇格しない。
6. Viewerで**監視用private/local.jsonと同じdata**を選ぶ。DEV DBの新規L1→Viewer→DEV App Group cache→DEV Widgetの時刻進行をログアウト前に確認する。Viewer終了中は監視/L1だけが継続し、Widget更新は止まる。ログイン後はViewer未起動の監視/L1/FDA確認を先に行い、その後Viewerを開く。

既存DEV履歴を移す必要がある場合は、承認された停止・ロック解放後にSQLite Backup APIで新規保存先へ移し、テーブル件数・整合性を確認する。新実体の識別情報は再生成し、旧環境は復旧用に保持。実行ファイル/設定/LaunchAgentの移行先を一括照合する。これは本番DBをfixtureへ複製する許可ではない。

## 操作と証跡の境界

- 本番DB・本番設定・正式Viewer/Widget・正式App GroupはDEV検証へ使わない。実行前にパスだけでなくBundle ID、表示名、Widget kind、署名、App Group、CLIとplist引数を照合する。
- Codexの制限付き実行で署名信頼評価が失敗しても、検証コードを弱めない。利用可能な承認付きサンドボックス外実行を使用し、許可されなければ通常Terminalへ引き継ぐ。Hook拒否を別経路で迂回しない。
- App Groupファイルの直接読取拒否に対してFDAを増やさない。必要なら承認したDEVアプリ内診断を使う。OSログや実パスを公開しない。
- 一時領域のViewer保存先エラーと旧Widget表示の根本原因は未特定。成功した外付けSSDの構成を再現し、既知失敗操作や成功済み受入を繰り返さない。
- 端末固有の入力、署名、配置、生成されたDB/JSON、生ログはGit管理外へ保持する。公開するのはコード/手順と一般化した結果だけ。[最終整理](issue38-release-checklist.md#dev成果物の最終整理未実行)も参照。
