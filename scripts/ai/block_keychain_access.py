#!/usr/bin/env python3
"""BodyMapのKeychain直接操作ガードを流用したPreToolUse Hook。
既存の認証済み接続経路は維持し、開発AIによるsecurityコマンドの直接実行を拒否する。
検査は入力コマンド文字列のみ。script内部・alias・未知のwrapperは見えない。
承認管理や認証経路を新設するものではない。制約はdocs/development-ai.mdを参照。
"""

import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shell_syntax import (  # noqa: E402
    LEADING_ASSIGN_RE,
    SEGMENT_SPLIT_RE,
    WRAPPER_WORDS,
    extract_code_strings,
    split_tokens,
)

MAX_RECURSION_DEPTH = 6

DENY_REASON = (
    "`security`コマンド（macOSのKeychain）は開発AIが直接実行しません。"
    "既存の認証済み接続経路を使用し、トークンの直接取得・表示は行いません。"
    "必要な場合はユーザー自身が実行してください。"
)


def invokes_security(segment: str) -> bool:
    """単純コマンドが、`security`を実行するものか。

    - コマンド位置（環境変数の代入の後）の語が`security`
    - 先頭がラッパー（env・sudo・command等）で、以降のどこかのトークンが`security`
      （ラッパーのオプションの解析はしない。`sudo -u user security`・`env --unset=X security`
      も対象になる。副作用：`time grep security file`のように、ラッパーの後ろに
      `security`という語が引数として現れるだけでも拒否される）
    """
    tokens = split_tokens(segment)
    i = 0
    while i < len(tokens) and LEADING_ASSIGN_RE.match(tokens[i]):
        i += 1
    if i >= len(tokens):
        return False
    first = os.path.basename(tokens[i])
    if first == "security":
        return True
    if first in WRAPPER_WORDS:
        return any(os.path.basename(t) == "security" for t in tokens[i + 1:])
    return False


def check_command_text(text: str, depth: int = 0):
    if depth > MAX_RECURSION_DEPTH:
        return None

    for segment in SEGMENT_SPLIT_RE.split(text):
        if invokes_security(segment):
            return DENY_REASON

    for inner in extract_code_strings(text):
        reason = check_command_text(inner, depth + 1)
        if reason:
            return reason

    return None


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        sys.exit(0)

    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        sys.exit(0)

    reason = check_command_text(command)
    if reason:
        deny(reason)

    sys.exit(0)


if __name__ == "__main__":
    main()
