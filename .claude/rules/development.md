# 開発フローと操作境界

## Issueから受入まで

Issue本文・合意済みコメントを確認 → feature branchで実装 → 変更に応じたローカル検証とdocs差異確認 → 実装結果・制約を提示 → 承認範囲内でcommit・push・PR → ユーザーがレビュー・Merge → 必要な本番・実機確認 → 完了根拠と後片付け確認 → ユーザーの最終判断、の順とする。

- commit・push・PR作成、Issue/PR本文の変更はユーザーの許可範囲内で行う。明示的な包括指示があれば同じ操作を再承認させない。範囲外の設計判断や追加PRは具体案を示して確認する。
- Issue Closeは実装・Merge承認と分ける。PR本文は`Related to #N`を使い、自動Close記法を使わない。PR Mergeはユーザーが行う。
- mainへの直接commit・push、強制push、未整理作業の破棄を通常手順にしない。既存branchを引き継ぐ場合は作業内容とPRを照合する。
- status・diff・stashを確認して他Issueの作業を保護し、意図したファイルだけstageする。並行セッションの変更や由来不明の成果物を削除・pop・上書きしない。
- 実装結果の記録は[Issue Skill](../../.agents/skills/github-issues/SKILL.md)、PR作成前の確認は[PR Skill](../../.agents/skills/github-pull-request/SKILL.md)に従う。

## 開発環境と公開情報

- Python 3.11以上・標準ライブラリを維持する。既存の対応Pythonとプロジェクトvenvを使い、システムPython、グローバルパッケージ、PATH、シェル設定、不要なSDKを変更しない。
- 実データDB・ログ・設定・認証情報・署名情報・実画面の私的情報をGitや公開Issue/PRへ含めない。例は一般化・合成する。
- GitHubは接続ツールを優先し、不足時は既存の認証済みCLI経路を使う。認証失敗時は実行先・login shell条件を確認し、トークンの取得・表示・export・新しい認証経路の追加は行わない。
- 安全ガードの拒否は迂回しない。Hookが通過したことはユーザー承認を意味しない。Hookが動かない場合も同じ指示に従う。
- 結論・結果・判断事項を先に書き、事実・推測・未確認、今回の確認・過去の記録・ユーザー報告を区別する。定型ログや同内容のドキュメントを増やさない。
