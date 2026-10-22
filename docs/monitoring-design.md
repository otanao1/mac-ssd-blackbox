# 監視設計（MVP初期案）

詳細・更新の正本は [Issue #2](https://github.com/otanao1/mac-ssd-blackbox/issues/2)。MVPスコープは [#1](https://github.com/otanao1/mac-ssd-blackbox/issues/1)、保持・Incidentのライフサイクルは [#3](https://github.com/otanao1/mac-ssd-blackbox/issues/3)。本書は設計案であり、実装済み機能ではない。

## レイヤー

| 層 | 対象・目的 | 周期 |
| --- | --- | --- |
| L1 | 内蔵SSD全体の空き容量・使用量、Incident検知 | 毎分 |
| L2 | 広域・Common監視。未知の容量増加箇所の発見 | 毎日 |
| L3 | 重点監視。指定アプリ・キャッシュ・一時領域 | 毎時 |
| L9 | Incident時の詳細調査 | 必要時 |

旧 L2＝重点・毎時、旧 L3＝広域・日次を入れ替えた。Incident時の追加調査は **L3 → 必要ならL2 → L9** の順。

## Scanと設定

L2・L3・L9は共通Scan処理を使う。同じスキャン実行内に親子パスがあれば再帰走査をまとめ、結果を再利用する。異なるレイヤー・実行時刻の走査は独立とする。原則sudo不要。外部SSD・NASなどを意図せず横断しない。VMボリュームは明示的な設定のみで走査する。実装言語・コマンドは未決定（Pythonは候補、duに限定しない）。

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

## Incident・履歴

IncidentTypeは `DROP_SPACE`、`LOW_SPACE`、`REBOOT`。暫定ルールは1分1GiB減少／24時間5GiB減少、空き5GiB以下への突入、再起動検知。値は未確定。

IncidentではL3を追加取得して過去結果と比較し、候補がなければL2を追加取得して比較する。候補が見つかればL9へ。L2でも候補がなければ原因未特定と通知する。

L9はIncident検知閾値の50%以上の下位ディレクトリを深掘り（最大7階層、暫定）。ファイルは検知閾値の5%以上を個別記録し、未満は件数・平均容量を記録する。L9の再取得は同じパス・取得内容について原則30分間抑止（暫定）。

L1は走査と独立。L2/L3/L9の走査は相互排他。競合する定期L2/L3はスキップし、L9は対象を集約して待機する。スキャン実行履歴・測定結果をscan IDで関連付ける。保持期間・Incident状態や証跡保全は#3で検討する。
