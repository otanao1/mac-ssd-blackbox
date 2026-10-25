"""BodyMapのコマンド解析からGit/Keychainガードが使う部分のみを抽出。
Cloudflare/D1/Previewの検査・例外は含まない。完全なshellパーサではない。
出典・制約はdocs/development-ai.mdを参照。
"""
import os
import re
import shlex

MAX_RECURSION_DEPTH = 6

WRAPPER_WORDS = frozenset(
    {
        "env",
        "command",
        "builtin",
        "sudo",
        "exec",
        "time",
        "nohup",
        "nice",
        "xargs",
        "stdbuf",
        "caffeinate",
        # BodyMap参考版：パッケージ実行・起動系のラッパーを追加
        "npx",
        "pnpx",
        "bunx",
        "pnpm",
        "yarn",
        "npm",
        "node",
        "timeout",
        "setsid",
        "arch",
        "doas",
        "ionice",
        "watch",
        "parallel",
        "find",
    }
)

LEADING_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

SEGMENT_SPLIT_RE = re.compile(r"[;&|\n]+")

SHELL_DASH_C_DQ_RE = re.compile(
    r"(?:^|[;&|]|\s)(?:\S*/)?(?:bash|sh|zsh|dash|ksh)\b[^;&|\n\"']*-c\s+\"((?:\\.|[^\"\\])*)\""
)

SHELL_DASH_C_SQ_RE = re.compile(
    r"(?:^|[;&|]|\s)(?:\S*/)?(?:bash|sh|zsh|dash|ksh)\b[^;&|\n\"']*-c\s+'([^']*)'"
)

EVAL_DQ_RE = re.compile(r"(?:^|[;&|]|\s)eval\s+\"((?:\\.|[^\"\\])*)\"")

EVAL_SQ_RE = re.compile(r"(?:^|[;&|]|\s)eval\s+'([^']*)'")

BACKTICK_RE = re.compile(r"`((?:\\.|[^`\\])*)`")

DOUBLE_QUOTE_ESCAPE_RE = re.compile(r'\\([$`"\\\n])')

def unescape_double_quoted(s: str) -> str:
    return DOUBLE_QUOTE_ESCAPE_RE.sub(r"\1", s)

def find_matching_paren(text: str, open_index: int) -> int:
    """text[open_index] == '(' に対応する ')' のインデックスを返す。

    見つからない場合は-1を返す。シングル/ダブルクォート内の
    `(` `)` はカウントに含めない（クォート内の文字列に括弧が
    含まれていても誤って閉じ位置と判定しないため）。
    """
    depth = 0
    i = open_index
    n = len(text)
    in_single = False
    in_double = False
    while i < n:
        c = text[i]
        if in_single:
            if c == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                in_double = False
            i += 1
            continue
        if c == "'":
            in_single = True
        elif c == '"':
            in_double = True
        elif c == "\\" and i + 1 < n:
            i += 2
            continue
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1

def extract_dollar_paren_strings(text: str) -> list:
    """`$(...)` コマンド置換の中身を抽出する（ネスト対応）。"""
    extracted = []
    i = 0
    n = len(text)
    while i < n:
        idx = text.find("$(", i)
        if idx == -1:
            break
        open_index = idx + 1
        close_index = find_matching_paren(text, open_index)
        if close_index == -1:
            break
        extracted.append(text[open_index + 1:close_index])
        i = close_index + 1
    return extracted

def extract_code_strings(text: str) -> list:
    extracted = []
    for pattern in (EVAL_DQ_RE, SHELL_DASH_C_DQ_RE):
        for m in pattern.finditer(text):
            extracted.append(unescape_double_quoted(m.group(1)))
    for pattern in (EVAL_SQ_RE, SHELL_DASH_C_SQ_RE):
        for m in pattern.finditer(text):
            extracted.append(m.group(1))
    extracted.extend(extract_dollar_paren_strings(text))
    extracted.extend(m.group(1) for m in BACKTICK_RE.finditer(text))
    return extracted

def split_tokens(segment: str) -> list:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()

def normalize_word(token: str) -> str:
    """コマンド名の比較用に、パス・`@version`・`.js`系の拡張子を取り除く。

    `/path/to/wrangler`・`wrangler@3`・`.../wrangler.js`を`wrangler`として扱う。
    """
    name = os.path.basename(token)
    if name.startswith("@"):
        return name
    name = name.split("@", 1)[0]
    for ext in (".js", ".mjs", ".cjs"):
        if name.endswith(ext):
            name = name[: -len(ext)]
    return name
