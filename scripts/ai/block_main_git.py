#!/usr/bin/env python3
"""
PreToolUse hook (matcher: Bash)

mac-ssd-blackboxの開発フロー（Issue #27）を技術的に強制する防御層。

- mainブランチ上での `git commit` / `git push` を拒否する
- `gh pr merge`（および `gh api .../pulls/<番号>/merge`）を、
  ブランチを問わず常に拒否する

単純な文字列検索ではなく、コマンド文字列のうち「実際に実行される
部分」と「クォート・heredoc内のデータ（commitメッセージ、説明文、
ドキュメント本文など）」を区別してから判定する。

- シングルクォート `'...'`、ダブルクォート `"..."`、heredoc本文は
  原則としてデータとして扱い、危険パターンの判定対象から除外する
- ただし `eval "..."` や `bash -c "..."` 等、文字列をコード
  として実行する構文については、その中身を再帰的に同じ規則で
  解析する（`bash -c` によるラップでの回避を防ぐため）

BodyMap参考版：上記の構造判定（正規表現）に加え、単純コマンドをshlexでトークン化
（クォート・エスケープを解除）して、`git`/`gh`の呼び出しを判定する。
絶対パス・グローバルオプション（`git -C . push`・`gh -R o/r pr merge`）・
クォート付き・ラッパー（env・command等）・`$(...)`・バッククォート経由、
`gh api graphql`のmergePullRequestを検出する。
限界（最善努力の柵）：別名（alias）・シェル関数・スクリプト経由は検出できない。

pushは、対象（refspec）でも判定する（`analyze_push`）。
- mainを更新・削除し得るもの（`git push origin HEAD:main`・`origin main`・`:main`・
  `--all`・`--mirror`・glob）は、現在のブランチを問わず拒否する
- main以外のリモートbranchの削除だけ（`--delete`・`-d`・`:branch`）は、Merge後の
  片付けのため、main上でも許可する
- それ以外のpushは、従来どおりmain上でのみ拒否する
"""

import json
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shell_syntax import (  # noqa: E402
    LEADING_ASSIGN_RE,
    WRAPPER_WORDS,
    extract_code_strings as extract_all_code_strings,
    normalize_word,
)

MAX_RECURSION_DEPTH = 6

GIT_COMMIT_PUSH_RE = re.compile(r"(^|[;&|]|\s)git\s+(commit|push)(\s|$)")
GH_PR_MERGE_RE = re.compile(r"(^|[;&|]|\s)gh\s+pr\s+merge(\s|$)")
GH_API_RE = re.compile(r"(^|[;&|]|\s)gh\s+api\b")
PULLS_MERGE_RE = re.compile(r"pulls/\d+/merge")

# bash/sh/zsh/dash/ksh -c "..." 形式（絶対パス・env経由も許容）。
# grep -c "..." 等、無関係なコマンドの -c フラグと誤認しないよう、
# シェル名が -c の手前に明示的に現れることを要求する。
SHELL_DASH_C_DQ_RE = re.compile(
    r"(?:^|[;&|]|\s)(?:\S*/)?(?:bash|sh|zsh|dash|ksh)\b[^;&|\n\"']*-c\s+\"((?:\\.|[^\"\\])*)\""
)
SHELL_DASH_C_SQ_RE = re.compile(
    r"(?:^|[;&|]|\s)(?:\S*/)?(?:bash|sh|zsh|dash|ksh)\b[^;&|\n\"']*-c\s+'([^']*)'"
)
EVAL_DQ_RE = re.compile(r"(?:^|[;&|]|\s)eval\s+\"((?:\\.|[^\"\\])*)\"")
EVAL_SQ_RE = re.compile(r"(?:^|[;&|]|\s)eval\s+'([^']*)'")

DOUBLE_QUOTE_RE = re.compile(r"\"(?:\\.|[^\"\\])*\"")
SINGLE_QUOTE_RE = re.compile(r"'[^']*'")

HEREDOC_START_RE = re.compile(r"<<-?\s*([\"']?)(\w+)\1")


def strip_heredocs(text: str) -> str:
    """heredoc開始マーカーごと本文をコマンド文字列から取り除く（データとして扱うため）。"""
    result = text
    while True:
        m = HEREDOC_START_RE.search(result)
        if not m:
            break
        delimiter = m.group(2)
        term_re = re.compile(r"\n[ \t]*" + re.escape(delimiter) + r"[ \t]*(?:\n|$)")
        term_m = term_re.search(result, m.end())
        end = term_m.end() if term_m else len(result)
        # マーカー自体（<<'EOF' 等）も含めて除去する。マーカーを残すと
        # 次のループでも同じ箇所に再マッチし、進行しなくなる（無限ループ）。
        result = result[: m.start()] + result[end:]
    return result


DOUBLE_QUOTE_ESCAPE_RE = re.compile(r'\\([$`"\\\n])')


def unescape_double_quoted(s: str) -> str:
    """ダブルクォート内のバックスラッシュエスケープを、bashの規則に沿って解除する。

    bashのダブルクォート内でバックスラッシュが特別な意味を持つのは
    `$` `` ` `` `"` `\\` および改行の直前だけで、それ以外のバックスラッシュは
    そのまま残る。これを解除しないと、`bash -c "eval \\"...\\""` のように
    二重にラップされたコードの中身が、再帰解析時に元の文字列と一致しなくなる。
    """
    return DOUBLE_QUOTE_ESCAPE_RE.sub(r"\1", s)


def extract_code_strings(text: str) -> list:
    """eval / <shell> -c で「コードとして実行される」文字列を抽出する。"""
    extracted = []
    for pattern in (EVAL_DQ_RE, SHELL_DASH_C_DQ_RE):
        for m in pattern.finditer(text):
            extracted.append(unescape_double_quoted(m.group(1)))
    for pattern in (EVAL_SQ_RE, SHELL_DASH_C_SQ_RE):
        for m in pattern.finditer(text):
            extracted.append(m.group(1))
    return extracted


def strip_data_quotes(text: str) -> str:
    """データとしてのクォート内容（commitメッセージ等）を空にする。"""
    text = DOUBLE_QUOTE_RE.sub('""', text)
    text = SINGLE_QUOTE_RE.sub("''", text)
    return text


def collect_structural_text(command: str, depth: int = 0) -> str:
    """
    実際に実行され得るコマンド部分だけを抽出して連結する。
    eval / bash -c 等でラップされたコードは再帰的に同じ処理を適用する。
    再帰が異常に深い場合は、解析を諦めて元の文字列をそのまま返す
    （安全側 = 危険パターンとして検出されやすくする）。
    """
    if depth > MAX_RECURSION_DEPTH:
        return command

    no_heredoc = strip_heredocs(command)
    nested_code = extract_code_strings(no_heredoc)
    structural = strip_data_quotes(no_heredoc)

    pieces = [structural]
    for inner in nested_code:
        pieces.append(collect_structural_text(inner, depth + 1))

    return "\n".join(pieces)


# BodyMap参考版：`git`に値を渡すグローバルオプション（これらの値はサブコマンドではない）
GIT_OPTIONS_WITH_VALUE = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
     "--super-prefix", "--config-env"}
)
# `gh`に値を渡すグローバルオプション
GH_OPTIONS_WITH_VALUE = frozenset({"-R", "--repo", "--hostname"})
MERGE_MUTATION_RE = re.compile(r"mergePullRequest|enablePullRequestAutoMerge", re.IGNORECASE)


def split_segments(text: str) -> list:
    """クォート・heredocの外にある区切り（`;` `&` `|` 改行 `(` `)` バッククォート）で分割する。

    クォート内の区切りでは分割しない（`echo "a; git push"`を誤検知しない）。
    """
    segments, buf = [], []
    quote = None
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                buf.append(text[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c == "\\" and i + 1 < n:
            buf.append(c)
            buf.append(text[i + 1])
            i += 1
        elif c in "\"'":
            quote = c
            buf.append(c)
        elif c in ";&|\n()`":
            segments.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    segments.append("".join(buf))
    return segments


def invocation_tails(tokens: list, word: str) -> list:
    """`word`を実行する位置から後ろのトークン列を返す（ラッパー経由は、以降の全出現位置）。"""
    i = 0
    while i < len(tokens) and LEADING_ASSIGN_RE.match(tokens[i]):
        i += 1
    if i >= len(tokens):
        return []
    first = normalize_word(tokens[i])
    if first == word:
        return [tokens[i + 1:]]
    if first in WRAPPER_WORDS:
        return [
            tokens[j + 1:] for j in range(i + 1, len(tokens)) if normalize_word(tokens[j]) == word
        ]
    return []


def first_positional(args: list, options_with_value) -> tuple:
    """先頭のオプション（値付きを含む）を読み飛ばして、(位置引数のindex, 残り)を返す。"""
    j = 0
    while j < len(args):
        a = args[j]
        if not a.startswith("-"):
            return j, args[j:]
        if a in options_with_value:
            j += 2
        else:
            j += 1
    return len(args), []


# `git push`に値を渡すオプション（値は、リモート名・refspecではない）
GIT_PUSH_OPTIONS_WITH_VALUE = frozenset(
    {"--repo", "--receive-pack", "--exec", "-o", "--push-option"}
)
PROTECTED_BRANCHES = frozenset({"main", "master"})


def analyze_push(args: list) -> str:
    """`git push`の引数を、pushの対象で分類する（BodyMap参考版）。

    - "push_main"：mainを更新・削除し得る（refspecの宛先がmain、`--all`・`--mirror`、
      glob）。現在のブランチを問わず拒否する
    - "push_delete_only"：main以外のリモートbranchの削除だけ（`--delete`・`-d`。
      Merge後のbranch削除のため、main上でも許可する）
    - "push"：それ以外（通常のpush。main上でのみ拒否する）
    """
    delete = False
    refspecs = []
    positionals = 0
    j = 0
    while j < len(args):
        a = args[j]
        if a in ("--all", "--mirror"):
            return "push_main"
        if a == "--delete" or (a.startswith("-") and not a.startswith("--") and "d" in a[1:]):
            delete = True
        if a in GIT_PUSH_OPTIONS_WITH_VALUE:
            j += 2
            continue
        if not a.startswith("-"):
            positionals += 1
            if positionals > 1:  # 1つ目はリモート名
                refspecs.append(a)
        j += 1

    for spec in refspecs:
        dst = spec.lstrip("+")
        if ":" in dst:
            dst = dst.split(":", 1)[1]
        if dst.startswith("refs/heads/"):
            dst = dst[len("refs/heads/"):]
        if dst in PROTECTED_BRANCHES or "*" in dst:
            return "push_main"
    # `--delete`付き、または全refspecが`:branch`形式（空の送信元＝削除）なら、削除だけのpush
    if refspecs and (delete or all(r.startswith(":") for r in refspecs)):
        return "push_delete_only"
    return "push"


def classify_segment(segment: str) -> set:
    """単純コマンドを、トークン化（クォート・エスケープ解除）して分類する。

    "commit" / "push" / "push_main" / "push_delete_only" / "pr_merge" / "api_merge" の集合を返す。
    """
    kinds = set()
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()

    for tail in invocation_tails(tokens, "git"):
        _, rest = first_positional(tail, GIT_OPTIONS_WITH_VALUE)
        if rest and rest[0] == "commit":
            kinds.add("commit")
        elif rest and rest[0] == "push":
            kinds.add(analyze_push(rest[1:]))

    for tail in invocation_tails(tokens, "gh"):
        _, rest = first_positional(tail, GH_OPTIONS_WITH_VALUE)
        if len(rest) >= 2 and rest[0] == "pr" and rest[1] == "merge":
            kinds.add("pr_merge")
        if rest and rest[0] == "api":
            joined = " ".join(rest[1:])
            if PULLS_MERGE_RE.search(joined) or MERGE_MUTATION_RE.search(joined):
                kinds.add("api_merge")
    return kinds


def classify_command(command: str, depth: int = 0) -> set:
    """コマンド文字列全体（eval / bash -c / `$(...)`・バッククォートの中身を含む）を分類する。"""
    kinds = set()
    if depth > MAX_RECURSION_DEPTH:
        return kinds
    no_heredoc = strip_heredocs(command)
    for segment in split_segments(no_heredoc):
        kinds |= classify_segment(segment)
    for inner in extract_all_code_strings(no_heredoc):
        kinds |= classify_command(inner, depth + 1)
    return kinds


def get_current_branch() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return ""


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

    structural = collect_structural_text(command)
    kinds = classify_command(command)

    # -----------------------------
    # gh pr merge / gh api ...pulls/N/merge
    # ブランチに関わらず常に拒否する（PRのMergeはユーザーのみが行う）。
    # -----------------------------
    if "pr_merge" in kinds or GH_PR_MERGE_RE.search(structural):
        deny(
            "Pull RequestのMergeは開発AIが実行しません。"
            "mac-ssd-blackboxの開発フロー（Issue #27）により、Mergeはユーザーのみが行います。"
            "ユーザーにMergeを依頼してください。"
        )

    if "api_merge" in kinds or (
        GH_API_RE.search(structural) and PULLS_MERGE_RE.search(structural)
    ):
        deny(
            "Pull RequestのMergeは開発AIが実行しません（gh api経由のmerge呼び出しを検知）。"
            "mac-ssd-blackboxの開発フロー（Issue #27）により、Mergeはユーザーのみが行います。"
        )

    # -----------------------------
    # mainを更新・削除し得るgit push（`git push origin HEAD:main`・`--all`等）
    # ブランチに関わらず常に拒否する（開発ルールのmain保護）。
    # -----------------------------
    if "push_main" in kinds:
        deny(
            "mainブランチへの直接push（refspecの宛先がmain、--all・--mirror、globを含む）は"
            "禁止されています。mac-ssd-blackboxの開発フロー（Issue #27）により、変更は必ず"
            "feature branch経由でPull Requestを作り、ユーザーがMergeしてmainへ反映します。"
        )

    # -----------------------------
    # git commit / git push（mainブランチ上のみ拒否）
    # main以外のリモートbranchの削除だけ（`git push origin --delete feature/x`）は、
    # Merge後の片付けのため、main上でも許可する（BodyMap参考版）。
    # -----------------------------
    m = GIT_COMMIT_PUSH_RE.search(structural)
    action = next((a for a in ("commit", "push") if a in kinds), None)
    if action is None and m and not (m.group(2) == "push" and "push_delete_only" in kinds):
        action = m.group(2)
    if action:
        branch = get_current_branch()
        if branch in PROTECTED_BRANCHES or not branch or branch == "HEAD":
            deny(
                f"保護ブランチ上またはブランチ不明時の git {action} は禁止されています。"
                "mac-ssd-blackboxの開発フロー（Issue #27）により、通常の変更は必ずfeature branchを"
                "作成してから行ってください（例: git checkout -b feature/issue-<番号>-<内容>）。"
                "Pull Request経由でユーザーがMergeするまで、mainへの直接反映はできません。"
            )

    sys.exit(0)


if __name__ == "__main__":
    main()
