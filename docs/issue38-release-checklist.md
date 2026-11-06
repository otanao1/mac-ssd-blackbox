# Issue #38: 同梱版の受入・導入準備

Issue [#38](https://github.com/otanao1/mac-ssd-blackbox/issues/38)の「現在の進捗・残件（2026-11-07更新）」を正本とする。PR #44のMerge、実機反映、Issue Closeは別の判断。以下は手順であり、実施済みの本番操作ではない。

## 実装とDEV受入の対応

| 要素 | PRの共通ソース | 受入で使用したDEV版・扱い |
| --- | --- | --- |
| 識別 | Bundle ID・表示名・App Group・URL scheme・Widget kindはビルド設定で指定 | 38FDAは全て専用値。`.dev.issue38.fda`は共通のジョブ名検証でも許可。正式版の既定値は変更しない |
| ヘルパー名 | `Contents/Helpers/Blackbox Monitor.app/Contents/MacOS/BlackboxMonitor` | 過去のFDAコピーは専用の実体名へ変更していた。今後の再現は共通の標準名を使い、署名後の文字列置換は行わない |
| FDA判定 | 通常Scannerが容量と結果を保存 | 過去のDEV v5/v6は取得可否アダプターを使用。容量・配下名は保存しない。新規ビルドで再現する場合だけ`--fda-access-only`を明示。正式IDでは拒否し、既定のパッケージには入れない |
| Widget診断 | パスを含まない既存キャッシュ。通常UIへ診断表示を追加しない | ビルド6はアプリ内でキャッシュパス・時刻・容量・読み書き成否を表示/記録。変更ソース・署名済み旧版・診断記録は非公開の作業領域に保持。本番向けコードに取り込まない |
| 設定・DB | インストール時に実体から保存先識別を生成 | DEVはprivate/runtime/CLI/DB/LaunchAgentを分離。固定合成DBと追記中の監視DBも区別する |

2026-11-07の照合では、配置済みv5の共通Pythonとの差分は`__main__.py`、`bundle.py`、`installation.py`のDEV識別名対応と、追加の取得可否アダプター。診断ビルド6は同じ監視ヘルパーを保持し、Viewer/Widgetに限って診断を追加した。残りの共通Pythonは一致。診断結果は、Viewerの書き込み/直後の読み戻しとWidgetの読み取りが同じDEVパス・DBの観測値に一致し、観測が進行することを示した。

### 受入結果（再実施しない）

- 過去の共通実装に対する隔離検証：実worker→du、環境混入拒否、改変拒否、更新・復旧、ロック解放、DB inode/データ保持。
- DEV限定FDA：Bluetooth/MailのON成功とOFFのEPERM。アダプターのため、本番全域の容量集計・全権限エラー解消を実証したものではない。
- Viewerメニュー通常終了後の監視・L1継続、独立した合成L1のViewer/Widget実表示。
- v4→v5：同一署名要件で更新、監視再開・L1継続、FDA再設定なしのBluetooth成功。
- 再ログイン：Viewer未起動でDEV LaunchAgent自動開始・新規L1・Bluetooth成功。その後Viewer起動、共有キャッシュ/Widgetの読み取り一致、実画面の時刻進行をユーザー確認。監視L1間隔は60秒。

再ログインと最終のライブWidget受入は診断ビルド6、監視ヘルパーはv5由来。正式IDの本番配布物の導入受入とは区別する。Viewer終了後にWidgetの新しい観測を更新する機能は追加しない。

### 開発時の既知事象

| 事象 | 判定・対応 | 正式実装への扱い |
| --- | --- | --- |
| 制限付きCodex環境の署名信頼評価失敗 | 承認付きサンドボックス外実行で`verify_bundle()`・ネイティブヘルパーが成功。通常Terminalでも成功 | 検証は弱化しない。内部原因の追加調査は不要。通常起動環境で失敗するなら導入を止める |
| Widgetの古い固定値 | DEV診断版とDEV拡張プロセス入替後、最新DB→キャッシュ→Widget・実画面を確認 | 動作は解消。変更が同時だったため旧原因は未特定。共有方式の変更・再追加の反復は不要 |
| 一時領域の保存先選択エラー | 外付けSSD上の新規独立環境で回避、ライブ監視DBでも成功 | 根本原因は未特定。一時領域の再試行を受入の前提にしない。将来その配置を必要とする場合は別途確認 |
| App Groupファイルの外部読み取り拒否 | Codex/Terminalの権限と、entitlementを持つアプリ内の読み取りは別。アプリ内診断で成功 | FDAを増やして読めるようにしない。診断表示・OSログは本番版へ含めない |
| GUI操作制約 | 保存先選択はCodexでも実施できた。Widget実画面はユーザー確認 | 実装不具合と区別。成功済みのユーザー操作を反復しない |
| 検証での本番DB誤選択 | 過去に誤選択・読み取りがあった。その後DEV専用環境に切替 | 「本番データを一度も参照していない」とは記録しない。本番DB/設定をfixtureへ流用しない |

## 利用・配布の範囲とゲート

| 条件 | 今回の実装完成に必要 | 導入・配布前の条件 |
| --- | --- | --- |
| 現在のMacでの本人ビルド・本人利用 | 同梱・署名・最低OS/architecture拒否検査、ライセンス文書同梱、DEVと正式の区別 | 正式ID・診断なし・通常Scannerの配布物を生成し検証。本番切替は別承認 |
| macOS 14向け同梱版 | 既存Viewerの14対応宣言を勝手に変更しない。26/arm64実測を14成功としない | Pythonと全依存Mach-Oが14以下・必要architectureを満たす入力、および対象OS上の確認が必要。現行DEV入力では不可。14で使う要件が今回必須なら未完了 |
| Developer ID・notarization | 自分のMacでビルドするDEV受入を外部配布確認と扱わない | 他者へバイナリ配布する時点で署名方法・Gatekeeper・notarization・配布チャネルを確認。同一Team要件だけで配布可能とは判定しない |
| 再配布ライセンス | 選んだ入力と同梱依存・ライセンスを把握する。フォルダが空でないだけで法的適合を宣言しない | 外部配布前に各入力の版・通知・対応ソース提供等を確認。今回外部配布を含めるならここも完了条件 |

DEV入力の確認済み内訳はCPython 3.11.8、arm64、最低macOS 26。非system dylibはlibpython、OpenSSL 3のlibssl/libcrypto、GNU readline 8.3。Python・OpenSSL・readlineのライセンス文書が同梱されている。CPythonの取り込みソフトウェアに対する通知も選んだビルド入力に照らして確認する。readlineのGPL条件を含め、文書のコピーだけで外部配布の条件充足とは扱わない。依存を除去・別実装へ置換する変更は今回行わない。

根拠：[Appleの配布方法](https://help.apple.com/xcode/mac/current/en.lproj/dev31de635e5.html)、[notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)、[Pythonのライセンスと取り込みソフトウェア](https://docs.python.org/3.11/license.html)、[GNUのGPL FAQ](https://www.gnu.org/licenses/gpl-faq.en.html)。本人利用と他者への提供を分け、将来配布物の適合を未確認のまま保証しない。

## 本番切替の事前確認と実施順序

以下は別途承認後の運用。現在の本番値を推測したコマンドは実行しない。対象の旧CLI・private・runtime・LaunchAgent・DB・マウント・Viewer配置先と、復旧先を明示してから実施する。

1. **入力と対象を確定**：正式IDのViewer/Widget、既存App Group・URL scheme・kindを維持した通常ビルドを用意。`--fda-access-only`・診断コード・DEV IDを含めない。署名/指定要件・対象OS/architecture・ライセンスを確認。同梱は既存`tools/build_monitor_bundle.py`、初回導入は`scripts/setup.sh --app-bundle`を使う。開発コピーを正式版として再利用しない。
2. **停止前に計画を照合**：旧共通Config、local.jsonとconf.dの上書き、実際の保存先識別と保持条件を確認。新しい共通Configと既存privateの組み合わせを既存ローダーで照合し、Update追加や意図しない対象増加がないか確認。`--register`は配置だけでなく直ちにbootstrapするので、設定/FDAの準備前に実行しない。既存環境へinstallを重ねることもできない。
3. **Viewerと監視を停止**：既存Viewerは通常終了。旧CLIで監視を停止し、所有ジョブ・worker終了、service/monitor/scanロック解放を確認。更新中の観測空白を許容する時間帯で行う。
4. **復旧用保管**：停止済みの旧runtime全体（venvを含む）、旧CLI、LaunchAgent、private設定、旧Viewer/Widgetを保管。DBはSQLite Backup API等で整合したバックアップを取得し、整合性・履歴/Incident保持を確認。稼働中DBの単純ファイルコピーは使わない。元DBは同じ実体・場所のまま使い、通常の切戻しで置き換えない。
5. **設定保持uninstall→同梱版install**：旧CLIの`uninstall`（`--remove-configuration`なし）で旧ジョブ/runtime/CLIを除去し、privateとデータを残す。同じprivate/runtime/CLI/データを明示して同梱版をinstallする。`scripts/setup.sh --app-bundle "$APP" --storage "$DATA" --mount "$MOUNT" --config-dir "$PRIVATE" --runtime "$RUNTIME" --cli "$CLI" --register`。これらの変数は承認時に確定した本番対象であり、DEVの値を転用しない。DB inodeとstorage識別値の手編集はしない。
6. **FDAと登録**：新しい正式Viewer実体へのFDAはユーザー操作。旧共有PythonのFDA変更/削除は別判断。FDA付与を新方式の起動前に済ませるか、登録後に対象ジョブだけを停止・再開して新プロセスで確認する。ON表示だけで成功とはせず、承認した限定対象で確認する。再ログイン確認が続くなら途中でOFFへ戻さない。
7. **起動後確認**：新しい実行主体・ジョブ所有関係、L1進行・error、保存先inodeと既存履歴/Incident、意図した周期・対象を確認。Viewerに既存設定と同じDBを選び、読み取りとWidget反映を確認。元DB・App Groupが同じなら、既存Viewerのbookmark/表示キャッシュを不要に消さない。実画面の最終確認はユーザー。

### PR #43のUpdate反映

新同梱版はPR #43を含む共通Configをruntimeへ配布する。共通`l2/conf.d/common.conf`と同名のprivateファイルがあると全体を置換するため、Updateが有効になるとは限らない。旧privateを一律削除せず、差分を確認して必要な変更を別途承認する。新方式の初回起動前に実効対象を確認し、すでに導入済みのruntimeへConfigを配布する場合はバックアップ後、既存のreloadを使う。

Updateは独立ボリュームとして通常のL2周期で取得され、走査時間・I/O・保存量が増える可能性がある。外部マウント境界、親子重複防止、error/partialは維持。共通設定配布だけで実Updateの取得成功とはしない。追加の手動広域Scanは自動では行わず、承認した確認と次の定期結果で判定する。対象外理由と過去Scanの照合は[既存手順](operations.md#scan取得範囲の確認)を使う。

### 更新・アンインストール・切戻し

- **同梱版同士の更新**：既存`update-app`。旧アプリを`.previous`、共通設定を`config.previous`へ保持し、同じDBで再開。既存バックアップを上書きしない。ローカル設定・履歴・Incident・保存先は保持する。指定要件や配置先が変わればFDAの再承認が必要になる場合がある。
- **同梱版から保持済み同梱版へ復旧**：同じ`update-app`に`.previous`を渡す。旧版/置換前共通Configは`.replaced`等へ保持する。今回DBスキーマ変更はない。将来互換性が変わる場合は別計画。
- **旧Python方式へ切戻し**：新DEVではなく本番対象の同梱監視だけを停止し、ロック解放後、設定保持uninstall。保管した旧runtime/venv・CLIを**元の絶対パス**に戻す。新方式のinstallation.jsonだけを残さず、旧privateと旧LaunchAgentも対応する記録から戻す。新方式の既存登録が残ったまま旧ジョブをbootstrapしない。元の同じDBを再利用し、旧実行主体・L1・履歴・Viewer/Widgetを確認する。旧方式のFDAは実際の旧状態に従いユーザーが確認する。バックアップDBの復元は破損等の必要性がある場合の別操作。
- **uninstallの範囲**：ジョブ/runtime/CLIを除去するが、DB・履歴・アプリは保持。`--remove-configuration`は明示された時のみ。FDA、Widget配置、アプリ/コンテナ・登録の削除は別操作。停止やuninstallをデータ削除の承認と扱わない。

## DEV成果物の最終整理（未実行）

現在のDEV監視・ログイン登録・FDA ON・診断版・表示キャッシュは、承認前に停止/削除/変更しない。最終整理では実体パス・Bundle ID・plist引数・読込済み実行ファイルを照合し、正式版や共有ホストを一括初期化しない。

| 対象 | 扱い・終了条件 |
| --- | --- |
| 現役DEV監視DB/private/runtime/CLIとログイン用plist | 証跡採取・本番移行判断まで保持。ユーザー承認後にDEVだけ停止・登録解除。DB/履歴削除は別判断 |
| 診断ビルド6・旧v5/v4・更新/復旧アーカイブ | 差分照合と復旧用に保持。不要コピーは削除/登録解除前に現役でないことを確認。診断表示を正式版へ引き継がない |
| 固定合成DB・識別JSON・生成記録 | 再現スクリプトと受入記録を保持すれば実体は整理候補。現在のライブDBと混同しない |
| ビルド出力・ステージングコピー・旧一時環境 | 最終レビュー後の整理候補。同一IDコピーをFinder/FDAの選択候補に残さない。外付け領域の稼働中実体は削除しない |
| DEV Widget配置・App Group/コンテナ・FDA項目 | ユーザーの画面操作/承認が必要な項目をまとめて提示。正式版と分離してから整理。App Groupファイルの外部読取権限を追加しない |

非公開作業領域の実体一覧・バックアップ場所・保持理由はローカル記録に残す。公開Issueには端末パス、実DB、監視容量や生ログ、署名者を掲載しない。
