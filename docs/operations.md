# CLI・設定・launchd・セットアップ（Issue #8）

Python 3.11以上・標準ライブラリのみ。#6のMonitor/Scanと#7のSQLite・保持・Incidentを利用する。
追加パッケージ、システムPythonの変更、sudo、PATHやシェル設定の自動変更は行わない。

## 初回セットアップ

GitHubから取得したclone内で、既存の対応Pythonと保存先を明示する。
保存先は事前に用意した専用ディレクトリを指定する。存在しない保存先を自動作成しない。
ディレクトリは0700を推奨し、外部SSD/NASなら意図したマウントを利用者が確認する。

```sh
git clone https://github.com/otanao1/mac-ssd-blackbox.git
cd mac-ssd-blackbox
BLACKBOX_PYTHON=/path/to/existing/python3.11 ./scripts/setup.sh \
  --storage '/Volumes/Archive/blackbox-data' --mount '/Volumes/Archive'
```

保存先・マウントの引数は必須。未指定の場合は設定せずusageエラーにする。
`--mount`は実際のボリュームのマウント点。macOSの`/Users`等はAPFS firmlinkによりDataボリュームに属するため、
内蔵SSDのホーム配下を保存先にするときも、実際の所属先（通常`/System/Volumes/Data`）を指定する。
実行前に設定内容と保存先を確認する。設定用対話プロンプトに依存せず、明示引数を確認として扱う。

標準配置：

| 用途 | 配置 |
|---|---|
| 実行コード・専用venv・共通設定・準備済みplist | `~/Library/Application Support/mac-ssd-blackbox-runtime/` |
| ローカル設定・インストール情報・制御socket/lock | `~/Library/Application Support/mac-ssd-blackbox/` |
| CLI | `~/.local/bin/mac-ssd-blackbox` |
| DB・SQLite journal等・スキャン待機状態 | 必須指定した保存先 |

`--runtime`、`--cli`、グローバル引数`--config-dir`で変更可能。配置先同士やclone・データ領域との入れ子は禁止する。
例：`python -m blackbox --config-dir /path/to/private install --storage ... --mount ...`。
venvはインストール先で作成し、cloneのvenvや個人データはコピーしない。
既存の実行コード／CLIは上書きしない。設定を残してuninstallした後は、同じ配置先・保存先へ再インストールできる。
Python本体はコピーしないため、指定した元Pythonは維持する。

**既定のセットアップは準備のみ。launchdへ登録せず、監視も開始しない。**
`--register`を明示した場合だけ、`~/Library/LaunchAgents/io.github.otanao1.mac-ssd-blackbox.plist`を配置し、
GUIログインユーザーのlaunchdへbootstrapして監視を開始する。既存plistは上書きしない。
この登録・実常駐の手順は今回の開発では実施しておらず、実機確認は別途行う。

## 操作

以下の`bb`は説明用略記で、実際には`~/.local/bin/mac-ssd-blackbox`を使う。
出力は既定でJSON。`--json`も受け付ける。エラーはstderrのJSONと終了コード1、usageエラーは2。

```sh
bb start                         # 未登録なら準備済みplistをbootstrap、登録済みならkickstart
bb status                        # 監視の稼働・PID・記録停止理由・スキャン進捗・L9待機
bb reload                        # 設定全体を検証して反映
bb config                        # 常駐側が読み込んだ設定ファイルとディレクトリ
bb scan L3 '/path/to/target/*'
bb scan L9 '/path/to/target/*' --threshold 1073741824 --max-depth 7
bb cancel SCAN_ID
bb clear-waiting
bb history --layer L1 --limit 20  # L2/L3/L9も指定可能
bb history --layer L3             # 生履歴と保持処理後のl3_dailyを含む
bb results SCAN_ID                # 大量結果はストリーム出力
bb incidents --limit 20
bb incidents INCIDENT_ID
bb stop                          # bootout。startで再開
```

`start`・`stop`は実環境のlaunchdを変更する。準備のみのインストールから`start`するとそのログイン中のジョブを登録するが、
LaunchAgentsへplistを配置しないため次回ログイン時の自動登録はしない。ログイン時の自動起動が必要なら初回に`--register`を指定する。
ユーザーLaunchAgentなのでログイン前には監視しない。`stop`後もLaunchAgentsのplistを残した場合は次のログイン時に再開する。
クラッシュ時はlaunchdが再起動する。正常終了は再起動しない。
plistは絶対パスを使用し、stdout/stderrを`/dev/null`へ送り、内蔵SSDのログファイルへ監視情報を蓄積しない。
通知は既存のJSONイベントを利用し、launchd運用時の状態・停止理由は`status`から確認する。GUI通知は追加しない。

常駐側はローカル設定領域の0600 Unix socketとflockで制御する。設定領域は0700とし同一ユーザーだけが操作する。
既存の保存先monitor.lock/scan.lockも維持する。スキャン要求とL9待機は既存DBに保存する。
保存先が未接続でも制御socketを維持し、5秒間隔で再試行する。履歴参照は保存先が必要だが、`status`と`config`は利用できる。
常駐中の履歴参照・中止は、ディスク上の編集途中の設定ではなく常駐側の読み込み済み保存先を使う。
停止中の`status`は`running:false`、`config`は`loaded:false`と読み込み候補を返す。
`run`は開発・診断用のフォアグラウンド実行で、終了はCtrl-C/SIGTERM。通常運用ではlaunchdを利用する。
`--data`付き操作は#6の開発ハーネス互換で、正式な保存先識別・設定統合を利用しない。運用には使わない。

## 設定とreload

共通の`config/settings.json`へローカル`local.json`を再帰的に上書きする。
オブジェクト内の未指定キーは共通値を継承し、配列は置換する。未知キー・無効値・不正パスは拒否する。
全体設定の項目は同梱`settings.json`を参照。保存先の`storage`はセットアップが生成するため、手作業で識別値を書き換えない。

```json
{
  "intervals": {"L3": 7200},
  "incident_thresholds": {"l9_cooldown": 1800},
  "retention": {"l3_raw_days": 30}
}
```

この例の項目を生成済み`local.json`へ追記する。保存先の項目は残す。
L2/L3は共通・ローカルそれぞれの`l2/conf.d/*.conf`、`l3/conf.d/*.conf`から読む。
同名のローカルファイルは共通ファイル全体を置換する。空ファイルで無効化でき、別名なら追加となる。
空行と`#`で始まる行を除き1行1対象。`/path`、`/path/*`、`~`の記法は既存Scanと共通。
存在しない対象や権限エラーは既存どおり結果に記録し、成功した容量として扱わない。
同梱の広域設定を有効にした運用ではホームやLibraryを走査する。テストはこの設定を使用しない。
L9の動的対象は設定へ書かず、DBの既存waiting表を使う。

reloadは全設定を検証し、失敗時は稼働中の設定を維持する。変更した周期の次回期限は反映時点＋新周期にする。
実行中・待機中のスキャンの対象／取得条件、すでに作成されたIncidentの証跡は書き換えない。
新しい定期・手動・Incident要求へ新設定を適用する。手動L9にも読み込み済みcooldownを使用する。
保存先変更はreloadで拒否する。停止して、保存先を検証した新しいセットアップで明示的に切り替える。
データの自動移動・同期は行わない。

## 保存先の安全性と復旧

初回に存在・書き込み（flush/fsyncを含む）・明示マウントとの一致を確認する。
ローカルディスクはVolumeUUID、NASはmount出力の接続元・ファイルシステム・マウント点を保存する。
さらにディレクトリとDBのinodeをローカル設定へ保持する。UUID等の取得不能や不一致は保存を開始しない。
DBを開く前に検証するため、別の保存先に同名DBがあっても採用しない。
既存DBも利用できるが、バックアップや置換DBを勝手に同じ保存先として採用しない。

稼働中は#7のdevice/inode・書き込み失敗検知を利用する。切断時に記録を停止し、所有子の終了を要求する。
再接続では永続識別を再検証し、同じ保存先・ディレクトリ・DBならdevice番号の変更を許容する。
通常オープン／復旧はSQLiteの`mode=rw`とし、自動mkdir・DB再作成・内蔵SSDへのフォールバックはしない。
DBとjournal/WAL/SHMのシンボリックリンクも拒否する。
ネットワークサーバーが再接続でinodeを変更する場合や接続元表記が変わる場合は、安全側に停止を続ける。
その場合は停止し、保存先を確認して設定を再作成する。識別情報を自動で緩和しない。
SQLiteのロック／書き込みを正しく提供するファイルシステムが必要で、全NAS製品の互換性は保証しない。

SQLite TEMPの内蔵SSDへの書き込みは許容する。既存の`temp_store=FILE`を維持し、
`temp_store_directory`やTEMP外付け配置用の測定設定は製品へ導入しない。
監視履歴・Incident・永続DB・journal等は指定先に置き、設定・制御socket/lockはローカル設定領域に置く。

## 削除

```sh
bb uninstall                         # 常駐停止・登録解除・実行環境/CLI削除。設定とデータを残す
bb uninstall --remove-configuration  # さらにローカル設定を削除。監視データは残す
```

cloneの`scripts/uninstall.sh`も利用できる（`BLACKBOX_PYTHON`を明示）。
コマンド自身が削除されるため、二度目のuninstallではcloneから
`python -m blackbox --config-dir /path/to/private uninstall ...`を使う。
削除前に、ロード済みジョブのplist元パス・実行プログラム・引数（設定ディレクトリを含む）・作業ディレクトリが
削除対象のインストール情報と一致することを確認する。共通ラベルや保存済みの`registered`だけでは所属とみなさない。
`registered:false`でも後から`start`したジョブは、所属が一致すれば停止・登録解除する。
ロード済みジョブが存在しないと確認できた場合は、bootoutせず削除する。
別の環境のジョブ、不明な照会エラー、所属情報の欠損・不一致では、停止・登録解除とファイル削除を中断する。
登録用plistの削除時にも所属を照合し、別の環境に置換された共通plistを削除しない。
この所属確認はCLIの`stop`にも適用する。

複数の配置先を作成できるが、launchdのラベルは共通のまま。一つのGUIドメインで複数環境を同時登録する機能は追加しない。
別環境のジョブが同じラベルで存在する間は、uninstallが安全側に中断する場合がある。
所属確認には`launchctl print`の診断出力を保守的に照合する。出力は安定したAPIではないため、
未対応の形式では自動停止・削除を中断する。今回の自動テストはモックであり、実機の出力形式は未検証。
照合直後にも所属を再確認するが、launchctlに原子的な所属照合付きbootoutはないため、
同じラベルに対する別のインストール・登録変更を並行実行しない。
稼働中のフォアグラウンドプロセスが残る場合も削除を拒否する。先にCtrl-C/SIGTERMで終了する。
監視データを削除するオプションは設けない。必要なら停止確認後に利用者が指定保存先だけを別途削除する。

手動で削除する場合（実環境を変更するので必要なときに利用者が実施）：

1. GUIログイン中の本人として、ロード済みジョブの実行環境と設定先が削除対象と一致することを確認する。所属を確認できなければ停止・登録解除しない。一致を確認できた場合だけ`launchctl bootout gui/$(id -u)/io.github.otanao1.mac-ssd-blackbox`で停止・登録解除する。ジョブが存在しなければ不要。
2. `~/Library/LaunchAgents/io.github.otanao1.mac-ssd-blackbox.plist`の内容が削除対象の実行環境・設定先を指すことを確認してから削除する。別環境のplistや所属不明なファイルは削除しない。
3. `installation.json`で配置先を確認し、実行用ディレクトリとCLIを削除する。
4. 設定が不要な場合だけローカル設定領域を削除する。
5. 監視データ削除は独立した判断とし、保存先と対象を再確認して行う。

セットアップ中に失敗した場合も同じ区分で片付ける。保存先のDBを自動削除しない。

## 検証範囲

`python -m unittest discover -s tests -v`で#6/#7の回帰と#8のテストを実行する。
Unix socketの作成が禁止されたサンドボックスでは、テスト専用領域のsocket通信を許可した環境で実行する。
#8は専用一時ディレクトリ・合成DB・モックしたマウント情報／launchctlを利用する。
一時ディレクトリの実行環境への配置、専用venv、CLI、制御socket、二重起動、reload、
保存先切断／復旧と再マウント識別、アンインストール時のデータ保持を確認する。
登録経路のテストもテスト用ホーム内に限定し、実LaunchAgentsへ配置しない。
複数配置先、未登録環境と後からstartした環境、別ジョブ／共通plistの置換、照会失敗・不明形式、
照合間のジョブ変更、bootout失敗時に停止・削除が安全側になることもモックで確認する。

実環境へのlaunchd登録・登録解除・常駐起動、ログイン／Mac再起動、実外部SSD/NASの切断・再マウント、
広域実データ走査・長時間運転は未実施。模擬検証をこれらの実機確認済みとは扱わない。
launchdの基本構成は[Appleの説明](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)に沿う。
