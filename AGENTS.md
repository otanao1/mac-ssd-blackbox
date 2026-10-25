# mac-ssd-blackbox 開発AIの入口

作業前に[開発ルール](.claude/rules/development.md)を読む。Issue本文と合意済みコメントを正本とし、ユーザーの明示的な指示・既存の承認範囲を引き継ぐ。報告は承認ではない。

- Issue単位のfeature branchで作業する。mainへの直接commit・push、AIによるPR Mergeは行わない。Issue Closeはユーザーの明示的な指示による別工程とする。
- 実機の監視・DB・アプリ・設定を変更する前に対象と許可範囲を確認する。Hookの拒否を別ツール・別経路で迂回しない。
- 秘密情報・端末固有パス・監視データ・生ログを公開しない。BodyMap・home-labへの依存・同期はない。

## 作業に応じて読むもの

| 作業 | 参照先 |
| --- | --- |
| Issue作成・更新・結果記録 | [github-issues](.agents/skills/github-issues/SKILL.md) |
| commit・push・PR作成／更新 | [github-pull-request](.agents/skills/github-pull-request/SKILL.md) |
| Merge後の本番・実機確認 | [production-verification](.agents/skills/production-verification/SKILL.md) |
| 完了判断・残件・後片付け | [closing-issue-check](.agents/skills/closing-issue-check/SKILL.md) |
| blackbox/・config/・scripts/setup.sh等、監視・保存・配置の変更 | [実機とデータの安全条件](.claude/rules/runtime-safety.md)、[運用手順](docs/operations.md) |
| viewer/・画面・Widgetの変更 | [Viewerルール](.claude/rules/viewer.md)、[Viewer利用ガイド](viewer/README.md) |
| Skills・Rules・Hook・AI設定の変更 | [AI構成の保守](.claude/rules/ai-environment.md) |

Codexでは`.claude/rules/`の自動読込を前提にせず、上の条件に従って明示的に読む。Hookはレビューとtrustが必要で、未稼働・対象外経路でも指示は有効。仕組みと制約は[運用構成](docs/development-ai.md)を参照する。
