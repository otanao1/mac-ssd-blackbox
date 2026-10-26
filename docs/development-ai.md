# blackboxの開発AI運用構成

Related to [Issue #27](https://github.com/otanao1/mac-ssd-blackbox/issues/27)。Issue更新・PR・実機確認・Close確認を、既存の開発と安全条件に沿って再利用する初回構成です。実装はPR #28へまとめ、Merge・Issue Closeは行いません。

## 調査したBodyMapの構成

GitHubの既定branchから、調査時点のcommit [`0da70e2`](https://github.com/otanao1/body-map/tree/0da70e215c3929f9cd0968d9bcb74bea4fb08e82)を固定して参照しました。接続ツールで各ファイルを取得し、既存認証済みCLIでrecursive treeの`truncated=false`を確認しています。BodyMap・home-labのファイルは変更していません。

- `AGENTS.md` → `CLAUDE.md`を共通指示として読み、GitHub運用・複数AI・記載ルール等のdocsへ参照。CLAUDEにフロー・操作権限・main保護・本番境界がある。
- 全5 Skill：`github-issues`、`github-pull-request`、`production-verification`、`preview-verification`、`closing-issue-check`。PR SkillがIssue Skill・PRテンプレート・migration Ruleを参照。Preview確認は環境docs・Issue記録・本番確認へ、本番確認とClose確認は役割を分けて連携する。
- 全3 Rule：`api-migrations`、`claude-hooks`、`claude-rules-guideline`。Claude Codeのpath条件による読込が前提。Codex側の自動読込はBodyMapでも未対応と記録され、重要なmigrationの入口はCLAUDEにも残されている。
- 全3 Hookと対応する回帰テスト：Git/main/PR Merge保護、Cloudflare本番操作保護、Keychain直接操作保護。GitとKeychainの両Hookは本番操作Hook内のshell解析関数をimportするため、ファイル単体のコピーでは動かない。
- `.claude/settings.json`と`.codex/hooks.json`が同じHookを呼ぶ。`.agents/skills`は`.claude/skills`への相対symlink。Codex Hookはgit rootからパス解決し、trustが必要。Bashのみの補助ガードで、エラー・対象外経路・script内部等の限界がある。
- 関連設定と運用docs：PR/Issueテンプレート、`project-codex-notes`、`project-gitissue-ai`、`project-aicommon-workflow-ai`、`project-writing-rules-ai`、`project-productionsafety-bg`、`project-produpdate-procedure`、`project-devlandscape`、`project-localdev-guide-ai`、`project-claude-notes`・`project-claude-usage-ai`、`project-onboarding`を確認。実機の認証先・個人設定やサービスIDはblackboxへコピーしない。

## 比較と導入判断

| 要素 | 分類 | blackboxでの扱い・理由 |
| --- | --- | --- |
| AGENTS/CLAUDE入口、開発フロー・承認 | 導入・調整が必要 | 短いAGENTSを正面入口、CLAUDEは同じ入口への参照にする。既存の許可を引き継ぎ、PRレビュー・Merge・最終Closeを区別する。 |
| Issue更新Skill | 導入・調整が必要 | BodyMapの本文保存・差分・空本文防止・再取得と検証分類を流用。blackboxにないラベル・動画ID規則は持ち込まない。 |
| PR Skillとテンプレート | 導入・調整が必要 | docs差異・Issue紐付け・承認・検証記録を流用。影響欄をruntime・DB/保持・設定/保存先・Viewer/Widget・手動操作へ調整する。 |
| Issueテンプレート | 既存の仕組みで対応済み | 既存Issueの自由形式を維持し、必要な記載項目はIssue Skillで案内する。フォームや必須欄の管理を増やさない。 |
| 本番確認Skill | 導入・調整が必要 | BodyMap版の「提案のみ」を維持。Cloudflareの自動deployを前提にせず、cloneと実機稼働版を区別する。 |
| Close確認Skill | 既存の仕組みで対応済み | 初回commitと#22の別セッション検証を保持。新しい開発Ruleへの参照のみ追加。 |
| path別Rules | 導入・調整が必要 | 開発・runtime安全・Viewer・AI構成保守に分離。CodexはAGENTSの条件表から明示読込する。 |
| Git保護Hook・Keychain直接操作Hook | 導入・調整が必要 | BodyMapの判定ロジックとテストを流用。必要なshell解析のみ抽出し、Cloudflare依存を除去。保護branch・不明branchの小さな補正のみ行う。 |
| 保存先・マウント・停止/削除時の所属確認、Viewer READONLY | 既存の仕組みで対応済み | 製品コードとoperations・Viewer開発メモが正本。Ruleから参照し、同じ機構をAI用に再実装しない。 |
| Cloudflare本番Hook、D1 migration Rule・Preview例外 | blackboxには不要 | Workers/D1/Previewがない。Git/Keychainに必要な解析部分だけを独立させる。 |
| Preview Skill・環境作成削除・ブラウザ拡張・添付コマンド | blackboxには不要 | 独立Preview環境なし。画面確認・合成エビデンスと実機受入の区別だけViewer Ruleへ取り込む。 |
| Token/effort管理、AI役割表、Perplexity資料、認証ラッパー作成 | blackboxには不要 | 製品と今回の運用整備に不要。既存の認証・モデル・個人環境を変更しない。 |

## 配置と参照関係

```text
CLAUDE.md → AGENTS.md → .claude/rules/development.md
                      → 作業条件に応じたruntime-safety/viewer/ai-environment Rule
                      → .agents/skills/（実ファイル4本）
.claude/skills ───────→ ../.agents/skills（同じPJ内の参照）

github-issues → 開発Rule・Close確認
github-pull-request → Issue Skill・PRテンプレート・本番確認
production-verification → 実機Rule・operations・Viewerメモ・Issue/Close確認
closing-issue-check → 開発Rule・既存operations・Viewerメモ

.codex/hooks.json ─────┐
.claude/settings.json ├→ scripts/ai/block_main_git.py
                     └→ scripts/ai/block_keychain_access.py
                           ↓ 共通import
                       scripts/ai/shell_syntax.py
```

Skillは`$github-issues`、`$github-pull-request`、`$production-verification`、`$closing-issue-check`で明示利用できます。Rulesの自動読込とSkillの認識を混同せず、CodexではAGENTSとSkillからの参照で詳細Ruleを読みます。`.claude/skills`の相対リンクはPJ内の重複を避けるためで、外部共通Skillとの連携ではありません。

## Hookの対象・起動条件・限界

BodyMapのGit・Keychain保護を流用し、`Bash`（Codexの`exec_command`を含む）に適用します。main/masterへのpush、保護branchまたはbranch不明時のcommit・通常push、CLI/API表現によるPR Merge、Keychainコマンドの直接実行を拒否します。非保護branchのリモート削除が通過する場合も、ユーザー承認が別途必要です。

Codexはプロジェクト設定のtrustとHook定義のreview/trustが必要です。新規／変更Hookが未承認ならスキップされます。CLIの`/hooks`で2本のコマンドと状態を確認してください。trustの自動登録・迂回やグローバル設定変更は行いません。Claude Code側の実行も、このPRでは実機検証していません。

Hookは完全な権限制御ではありません。MCP・接続ツール、任意script内部、alias・関数・未知のwrapper、複雑なshellの状態変更、コマンドごとの別リポジトリ指定等には限界があります。現在branchはHook実行cwdで調べ、任意の`cd`や別リポジトリへの`git -C`を完全には追跡しません。読取エラー・不正payload・script不在・timeout等で止まらない場合もあります。**通過や未発火を承認とみなさず、経路を問わずRuleを守ります。**

本番runtime・DB・保存先・アプリ配置・launchdやmacOS登録情報を変更する権限は追加しません。今回の検証はコマンド文字列を入力データとして渡すもので、拒否対象コマンドを実行しません。

## 検証

- Hook：`.venv/bin/python -m unittest discover -s tests/ai -q`で72件成功（BodyMap由来71件と保護／不明branchの補正1件）。実行入口へ合成JSONを渡し、読み取り入力の通過と、保護branch宛push・PR Merge・Keychain直接操作入力のdeny出力も確認。入力文字列を実行していない。
- 構成：Python構文、JSON、Ruby YAMLパーサによる4 Skill/4 Ruleの形式、相対リンク、共通import、同一PJ内symlinkを確認して成功。Hookコマンドはrootと`viewer/`からそれぞれ解決・実行できた。端末固有の非公開パス・元サービスID・認証項目名をコピーしていないことも確認。既存PythonにはPyYAMLがないため、Skill形式は従来同様の代替確認とし、依存を追加していない。
- 新しいCodex CLIプロセスを`--ephemeral --sandbox read-only`で起動し、開始時にAGENTS内容が含まれること、4 Skillの一覧掲載、`github-issues`・`github-pull-request`・`production-verification`の明示読込と利用を確認した。Issue記録案（未投稿）、ローカル差分確認、未Mergeの変更に対するMerge後の確認項目案を出力し、4 Ruleも読み込めた。既存Close Skillは差分確認のため読み込まれた。
- 上記新セッションはファイル編集・GitHub接続・投稿・実機変更・trust変更を行わず終了。Git内部の一時キャッシュ作成拒否の警告はあったが、読み取り結果は取得でき、迂回はしていない。Hook trustの警告は表示されなかったが、trust状態・自動発火の証明とは扱わない。Claude Codeのpath条件による自動読込・Hook実利用は未検証。
- 既存の[Close Skill検証](closing-issue-check.md)は保持する。自然文からの自動選択の追加試験は不要とのユーザー判断を引き継ぐ。

## 今回見送る候補と今後の実開発での確認

- 実機コマンド・SQL・launchd・アプリ登録への専用Hook：許可範囲・対象識別を安全に機械判定する方法が未確定。既存Ruleと製品安全条件で対応し、実際の必要性が出たときだけ追加を検討する。
- MCP経由のMerge/Close・外部書込のHook、Codex execpolicy：ツール名・引数・実行環境との対応に追加設計が必要。今回のBashガードに完全保護を装わせず、運用ルールを適用する。
- 承認状態の永続管理・許可token・全端末成果物台帳・自動配布／同期・自動後片付け：今回不要。新規管理機構は作らない。
- 今後の実開発では、実Issue本文の競合時更新、PR更新とdocs確認、Merge後の実機反映計画、保持・判定不能を伴うClose判断、Hookのtrust後の自動発火と誤検知を確認する。今回は実投稿・危険操作・削除による検証を行わない。

候補のための新規Issueは作成しません。公式仕様の参照：[AGENTS](https://learn.chatgpt.com/docs/agent-configuration/agents-md)、[Skills](https://learn.chatgpt.com/docs/build-skills)、[Hooks](https://learn.chatgpt.com/docs/hooks)。
