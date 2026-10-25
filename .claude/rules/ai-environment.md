---
paths:
  - "AGENTS.md"
  - "CLAUDE.md"
  - ".agents/**"
  - ".claude/**"
  - ".codex/**"
  - "scripts/ai/**"
  - "tests/ai/**"
---

# AI運用構成の保守

[構成・適用範囲](../../docs/development-ai.md)を読む。常時必要な入口と核心の安全条件をAGENTSに、定型手順をSkillに、条件付き詳細をRulesに置く。RulesのCodex自動読込は前提にしない。追加時はAGENTSまたは呼出し元からの参照を設ける。

- Skillのname/description、相対リンク、JSON設定、Hook呼出し先を確認する。既存Close Skillとその検証記録を保持する。共有はこのリポジトリ内に限定する。
- Hookは文字列検査の補助であり完全なshell解析や権限管理ではない。コードで承認済みと推定したり、例外token・環境変数の許可フラグ・共通配布基盤を追加したりしない。
- 拒否対象のコマンドを含むテスト・文書・PR本文は編集ツールで書き、本文をshellで実行させない。shellを使う外部投稿はbody-file等を使う。
- Hook変更後は`.venv/bin/python -m unittest discover -s tests/ai -v`を実行する。拒否対象は入力データとして与え、実コマンドを実行しない。構文・JSON・サブディレクトリからのパス解決も確認する。
- 新しいセッションでAGENTS・代表Skill・必要なRuleの認識と読み込みを確認する。Hookの自動発火はtrustと設定読込を確認してから別に評価し、単体試験を発火成功と混同しない。trustを自動登録・迂回しない。
