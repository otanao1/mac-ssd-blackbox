---
paths:
  - "blackbox/**"
  - "config/**"
  - "scripts/**"
  - "tools/**"
  - "tests/**"
---

# 監視エンジン・実機データの安全条件

正本は[運用手順](../../docs/operations.md)、[実行基盤](../../docs/core-runtime.md)、[保存・Incident](../../docs/storage-incidents.md)。既存の保存先照合・停止復旧・所属確認を弱めない。

- テストは専用の一時ディレクトリ・合成DB・限定したfixture・モックlaunchctlで行う。既存のPythonで`.venv/bin/python -m unittest discover -s tests -v`を使う。実ホームや同梱の広域L2/L3対象を検証用に走査しない。
- 実際のsetup、launchd登録、start/stop/reload、追加スキャン、保存先変更、DB・設定への書き込み、uninstall、停止・削除・登録解除は個別の実環境変更として扱う。対象・影響とユーザーの許可範囲を確認し、テスト成功から許可を推定しない。
- cloneとインストール済みruntimeは別実体。Merge・ローカルビルドだけで稼働版更新済みとしない。
- 外部マウント横断の拒否、同じ保存先の識別、保存先不在時の停止を維持し、自動mkdir・DB再作成・別保存先へのfailoverを追加しない。
- runtime、設定、監視データの削除判断は独立。データ削除やプロセスの強制終了・自動修復は追加しない。
- スキーマ・保持や情報を失う変更は、合成データで互換性・復旧手段と限界を確認する。不可逆な判断が必要になった時点でユーザーへ相談し、実DBで試さない。
