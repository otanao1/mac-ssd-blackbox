---
name: github-pull-request
description: mac-ssd-blackboxでcommit・push・PR作成や既存PR更新の前に使う。Issueとの対応、変更範囲、承認、docs、検証結果、本番への影響とMerge後の作業を確認する。
---

# commit・push・PRの確認

[開発ルール](../../../.claude/rules/development.md)に従う。実装の報告と承認を分け、既に与えられた具体的なcommit・push・PR作成／更新の許可は引き継ぐ。

1. `git status --short --branch`・diff・stashと対象Issue・既存PRを確認し、他Issueの変更を含めない。mainへ直接commit・pushせず、対象のfeature branchを使う。既存PR更新の指示なら同じbranchを使い、別PRを増やさない。
2. [Issue Skill](../github-issues/SKILL.md)の結果分類に従って、変更に必要な検証と完了条件の根拠を確認する。変更がない範囲の再テストや実機操作を機械的に増やさない。
3. README・関連docs・Viewer利用ガイドとの差異を確認し、必要な更新を同じPRへ含める。更新不要なら根拠を示す。
4. 許可範囲内で意図したファイルのみstageし、staged diffを確認してcommit、対象feature branchへpushする。push結果と追跡状態を確認する。
5. [PRテンプレート](../../../.github/PULL_REQUEST_TEMPLATE.md)の項目を使って日本語で作成／更新する。`Related to #N`で紐付け、自動Close記法は使わない。レビュー変更で範囲が変わった場合はタイトル・本文を最終実装に合わせる。
6. 作成／更新後にPRのhead/base・差分範囲・本文を読み直す。MergeやIssue Closeは実行しない。

本番影響は、監視runtime・DB/保持・設定/保存先・Viewer/Widget・手動配置/登録に分けて必要性を判断する。Mergeやビルド成功だけでインストール・実機反映済みとしない。Merge後の確認項目は[production-verification](../production-verification/SKILL.md)を参照する。

PR本文やcommit本文をshellのコードとして渡さない。接続ツールの構造化引数を優先し、CLIではファイルに書いて`--body-file`・`git commit -F`で渡す。
