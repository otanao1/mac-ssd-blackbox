---
name: production-verification
description: mac-ssd-blackboxのPRがMergeされたとき、本番・実機への反映確認項目を整理するときに使う。監視runtime・DB・設定・Viewer/Widget・手動配置の影響から確認項目を提案する。実環境の操作・変更は実行しない。
---

# Merge後の本番・実機確認項目

BodyMap版と同じく、このSkillの役割は**確認項目の提案**に限定する。提案は実行承認・実施結果ではない。操作は[開発ルール](../../../.claude/rules/development.md)と[実機安全条件](../../../.claude/rules/runtime-safety.md)に従う。

対象PRを最新のGitHub情報から特定し、Merge状態・base・変更ファイルとPR本文の影響欄を照合する。対象が一意でなければ推測せず確認する。本文とdiffが違う場合はdiffも根拠に含める。

| 変更 | 提案する確認 |
| --- | --- |
| 監視runtime・CLI・launchd | cloneと稼働版の区別、反映の要否、既存status等による確認、周期・待機・保存状態。登録・起動停止は別承認。 |
| DB形式・保持・Incident | 合成DBでの互換性、既存データへの影響、復旧方針。実DBの変更・保持処理を検証目的で動かさない。 |
| 設定・保存先 | 稼働側の読込設定と配置の照合、反映方法、保存先識別と復旧の未確認範囲。切断・置換を勝手に試さない。 |
| Viewer・Widget | ビルド・署名とApp Group、正式配置と旧登録、実行実体、変更した操作と期待表示。実画面の受入担当はユーザーとし、合成描画と分ける。 |
| 手動配置・登録・復旧 | 必要な対象・影響・実施者・承認・復旧方法。既存手順を参照し、操作済みとしない。 |
| AI指示・Skill・Rule・Hook、docsのみ | 必要に応じた新セッションでの読込・trust確認。製品の監視・DB・Viewerの本番確認が不要ならその理由を示す。 |

出力は対象PRと、各確認項目の理由・担当・期待結果・未実施／確認済みの根拠・ユーザー確認待ちを短く示す。該当しない確認を増やさない。

[運用手順](../../../docs/operations.md)、[Viewer開発メモ](../../../docs/project-development-viewer-notes.md)を参照し、既存の未確認を今回確認済みに書き換えない。結果記録は[Issue Skill](../github-issues/SKILL.md)、受入・終了判断は[Close確認](../closing-issue-check/SKILL.md)へつなぐ。IssueのClose・PRのMergeは行わない。
