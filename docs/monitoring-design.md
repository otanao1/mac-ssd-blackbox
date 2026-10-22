# 監視設計（MVP初期案）

詳細・更新の正本は [Issue #2](https://github.com/otanao1/mac-ssd-blackbox/issues/2)。MVPスコープは [#1](https://github.com/otanao1/mac-ssd-blackbox/issues/1)、保持・Incidentのライフサイクルは [#3](https://github.com/otanao1/mac-ssd-blackbox/issues/3)。本書は設計の正本を要約する。#6の実装範囲・起動方法・制約は[監視実行基盤](core-runtime.md)を参照。

## レイヤー

| 層 | 対象・目的 | 周期 |
| --- | --- | --- |
| L1 | 内蔵SSD全体の空き容量・使用量、Incident検知 | 毎分 |
| L2 | 広域・Common監視。未知の容量増加箇所の発見 | 毎日 |
| L3 | 重点監視。指定アプリ・キャッシュ・一時領域 | 毎時 |
| L9 | Incident時の詳細調査 | 必要時 |

旧 L2＝重点・毎時、旧 L3＝広域・日次を入れ替えた。Incident時の追加調査は **L3 → 必要ならL2 → L9** の順。

## Scanと設定

L2・L3・L9は共通Scan処理を使う。同じスキャン実行内に親子パスがあれば再帰走査をまとめ、結果を再利用する。異なるレイヤー・実行時刻の走査は独立とする。原則sudo不要。外部SSD・NASなどを意図せず横断しない。VMボリュームは明示的な設定のみで走査する。#6ではPython標準ライブラリによるメタデータ走査を採用した。取得容量・安全条件の詳細は[監視実行基盤](core-runtime.md)を参照。

- `config/l2/conf.d/`：L2（日次・Common）の設定
- `config/l3/conf.d/`：L3（毎時・重点）の設定
- `/path`：指定パス全体の合計容量を記録
- `/path/*`：指定パス自身の合計容量と直下のファイル・フォルダ別容量を記録
- `~` はユーザーホームとして解決。隠し項目も対象。存在しないパスはスキップ。

汎用設定はPublicリポジトリに同梱するが、個人情報・ID・端末固有値を含む設定は公開しない。ファイル名は例示であり、実装時に調整可能。

### L2（日次・広域）の初期19指定

```text
~/*
~/Library/*
~/Library/Application Support/*
/Users/*
/Library/*
/Library/Application Support/*
/opt/*
/opt/homebrew/*
/opt/homebrew/Cellar/*
/usr/local/*
/private/var/*
/cores
~/Library/Developer/Xcode/DerivedData
~/Library/Caches/Homebrew
/private/tmp/*
/Applications/*
/System/Volumes/VM/*
/usr/*
~/Library/CloudStorage/*
```

`/System/*`、`/bin`、`/sbin`、`/dev` は初期対象外。VM指定は暫定。全データの網羅は保証しない。

### L3（毎時・重点）の初期15指定

| 設定ファイル案 | 監視対象 |
| --- | --- |
| `evernote.conf` | `~/Library/Application Support/Evernote/*` |
| `amazon-photos.conf` | `~/Library/Application Support/Amazon Cloud Drive/*` |
| `adobe.conf` | `~/Library/Application Support/Adobe/*`、`~/Library/Caches/Adobe/*`、`/Library/Application Support/Adobe/*` |
| `google-drive.conf` | `~/Library/Application Support/Google/DriveFS/*` |
| `notion.conf` | `~/Library/Application Support/Notion/*`、`~/Library/Caches/notion.id/*` |
| `temp.conf` | `/private/var/tmp/*`、`/private/var/folders/*` |
| `dev-ai.conf` | `~/.codex`、`~/.claude` |
| `dev-cache.conf` | `~/.npm`、`~/.cache/*` |
| `docker.conf` | `~/Library/Containers/com.docker.docker` |

存在・取得負荷・監視効果は実装・実測時に確認する。

### L9の動的対象

- L9対象は永続的な監視設定として `conf.d` には置かない。
- Incident処理で生成された実行対象としてローカルデータ領域に保持する。
- パス表現はL2/L3と共通化する。
- 待機リスト・実行リスト・再取得抑止の状態と関連付けて管理する。

## Incident・履歴

IncidentTypeは `DROP_SPACE`、`LOW_SPACE`、`REBOOT`。**MVP初期設定として採用する**検知条件は、`DROP_SPACE`＝1分で1GiB減少または24時間で5GiB減少、`LOW_SPACE`＝空き5GiB以下への突入、`REBOOT`＝起動識別子の変化。閾値は最適値と断定せず、実測後に調整できるようにする。

設定のデフォルト値はコード内またはGit管理する共通設定に置き、**Git管理外のローカル設定ファイルで必要な項目だけ上書き可能**とする。全体設定には監視周期、Incident閾値、保存先、保持期間、L9再取得抑止時間などを持たせる。設定形式・ファイル名・配置パスの細部は実装時に決める。DBによる設定管理はMVPでは必須としない。

IncidentではL3を追加取得して過去結果と比較し、候補がなければL2を追加取得して比較する。候補が見つかればL9へ。L2でも候補がなければ原因未特定と通知する。

L9はIncident検知閾値の50%以上の下位ディレクトリを深掘り（最大7階層、暫定）。ファイルは検知閾値の5%以上を個別記録し、未満は件数・平均容量を記録する。L9の再取得は同じパス・取得内容について原則30分間抑止（暫定）。

L1は走査と独立。L2/L3/L9の走査は相互排他。競合する定期L2/L3はスキップし、L9は対象を集約して待機する。スキャン実行履歴・測定結果をscan IDで関連付ける。保持期間・Incident状態や証跡保全は#3で基本方針を整理した（以下参照）。

## CLI・常駐・実行制御（Issue #1で合意したMVP方針）

### CLI

MVPのCLIでは次を扱う。

- 監視：開始、停止、リロード、状態確認
- スキャン：手動実行、実行中確認、中止
- スキャン履歴：L1/L2/L3/L9を確認
- Incident：一覧、詳細
- 機械可読出力：JSON等
- 現在読み込んでいる設定ファイル・設定ディレクトリの確認

実行中スキャンでは、レイヤー、開始時刻、現在処理中の対象、完了対象数/全対象数、経過時間、中止要求の有無などを確認できるようにする。総量を算出できる場合のみ進捗率も表示する。専用exportコマンドと、人向けの高度な監視設定一覧はMVP必須としない。具体的なコマンド名・階層は実装時に決める。

### 常駐プロセスとスキャンプロセス

- `launchd` で軽量な常駐プロセスを起動・監視する。
- 常駐プロセスはスケジュール、Incident判定、L2/L3/L9の排他、L9待機リスト、子プロセス監視を担当する。
- L1は常駐プロセス側で毎分取得する。
- L2/L3/L9は別プロセスで実行し、終了時にプロセスごと終了する。
- 通常のスキャン中止はCLI経由とし、監視本体をkillする運用を前提にしない。
- スキャン結果や履歴全体を常駐プロセスのメモリに抱え続けない。

## 履歴・Incidentの保管（Issue #3で合意した基本方針）

### 通常履歴

- **L1（毎分）**：生データを集約せず保持。最終保存期間は3年。
- **L2（毎日）**：日次データを集約せず保持。最終保存期間は3年。
- **L3（毎時）**：直近30日は毎時データを保持。それより古いデータは日次に集約し、最終保存期間は3年。
- L3の日次集約では短期的な変動を見失わないよう評価情報を残す。具体的な集約項目・方式は未確定。
- 期限超過データは削除する。期間・粒度は設定可能とする（設定ファイル形式・削除実行方式は実装時判断）。

### Incidentの記録と状態

- Incidentは検知情報（ID、日時、Type、条件）、容量変化の評価結果、L2/L3/L9による調査の評価結果、原因候補、調査状況・メモを保持する。
- 生ログ全体の複製は原則不要。調査上必要なら、生ログ・コマンド出力の抜粋などをレポート内に記載できる。Markdown/HTML/DBなどの具体的な保存形式は未決定。
- 関連するスキャンIDをIncidentに記録する。関連付け専用の構造はMVPで設けない。通常履歴の削除後も評価結果そのものはIncident内に残す。
- 状態は次の3つ。自動調査が終了した時点で `identified` または `unidentified` とする。原因特定結果の別フラグは設けない。

| 内部値 | 日本語表示 |
| --- | --- |
| `investigating` | 自動調査中 |
| `identified` | 調査完了・原因特定 |
| `unidentified` | 調査完了・原因未特定 |

- Incidentの保存期限は3年間。通常履歴とは独立して期限管理する。

### 保存先・書き込み障害

- 保存先は内蔵SSDを標準とし、設定により外部SSDやNASも指定できる。
- 保存先の自動切替・データ同期はMVPでは行わない。
- **設定された保存先に正常に書き込めない場合は、記録を停止してエラーを通知し、復旧後に再開する。** 外部SSD・NASについては、意図したマウント先であることを確認し、意図せず内蔵SSDに書き込まないようにする。
- mac-ssd-blackbox自身の保存データ合計サイズに独自の上限は設けない。保存期限によって管理する。

## 配布・インストール（Issue #1で合意したMVP方針）

- GitHubから取得し、セットアップスクリプトで実行用ディレクトリへ配置する。
- cloneしたGit作業ディレクトリをそのまま常駐実行には使わない。
- セットアップで実行環境、設定領域、データ領域、CLI、`launchd` 登録を準備する。
- 初回セットアップでは設定内容の確認を促し、**保存先は必須指定**とする。
- 保存先の存在・書き込み可否を確認し、外部SSD/NASの場合は意図したマウント先であることを確認してから監視を開始する。
- アンインストール手段を用意する。少なくとも手動削除手順を明記する。
- 常駐停止、`launchd` 登録解除、実行環境、設定、監視データの削除を区別する。
- 実行環境の削除と監視データの削除は分離し、アンインストール時に監視データを自動削除することを前提にしない。
- Homebrew等の配布はMVP後の候補とする。

### 公開・プライバシー

- プログラムと汎用設定は公開可能。個人用設定、実測履歴、Incident・調査レポート、実機ログはGit管理対象外とし、ローカルで保存する。
- 公開Issue・PR・文書などへ実測ログを掲載する場合は、ユーザー名を含むパス、端末固有情報、アカウント情報等の機微情報を除去する。
- MVPではログ自動匿名化の専用機構は設けない。Gitリポジトリ外の保存データにも非公開方針を適用する。

## 実装Issue

- #6: core monitoring and scan execution
- #7: storage, retention, and Incident lifecycle
- #8: CLI, configuration, daemon, and installation

> 本書には#7・#8を含むMVP全体の設計方針を記す。実装済み範囲は[監視実行基盤](core-runtime.md)を参照。
