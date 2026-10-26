#!/usr/bin/env python3
"""
block_main_git.py のユニットテスト（BodyMap参考版）。

標準ライブラリのunittestのみで実装する。

  python3 -m unittest discover -s tests/ai -v

現在のブランチ名はモックする（実際のgit状態に依存しない）。
"""

import importlib.util
import io
import json
import pathlib
import unittest
from contextlib import redirect_stdout
from unittest import mock

MODULE_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ai" / "block_main_git.py"

spec = importlib.util.spec_from_file_location("block_main_git", MODULE_PATH)
block_main_git = importlib.util.module_from_spec(spec)
spec.loader.exec_module(block_main_git)


def run_hook(command: str, branch: str) -> bool:
    """Hookを実行し、拒否されたらTrueを返す。"""
    payload = json.dumps({"tool_input": {"command": command}})
    out = io.StringIO()
    with mock.patch("sys.stdin", io.StringIO(payload)), mock.patch.object(
        block_main_git, "get_current_branch", return_value=branch
    ), redirect_stdout(out):
        try:
            block_main_git.main()
        except SystemExit:
            pass
    return "deny" in out.getvalue()


class MainBranchGitTest(unittest.TestCase):
    def assert_denied_on_main(self, command: str):
        self.assertTrue(run_hook(command, "main"), f"allowed on main: {command!r}")

    def assert_allowed_on_main(self, command: str):
        self.assertFalse(run_hook(command, "main"), f"denied on main: {command!r}")

    def test_plain(self):
        self.assert_denied_on_main("git push")
        self.assert_denied_on_main("git commit -m x")

    def test_absolute_path(self):
        self.assert_denied_on_main("/usr/bin/git push")

    def test_global_options(self):
        self.assert_denied_on_main("git -C . push")
        self.assert_denied_on_main("git -c user.name=x commit -m y")
        self.assert_denied_on_main("git --no-pager push")
        self.assert_denied_on_main("git --git-dir=.git push")

    def test_quoted_and_escaped(self):
        self.assert_denied_on_main('"git" push')
        self.assert_denied_on_main("'git' commit -m x")
        self.assert_denied_on_main("\\git push")

    def test_wrappers(self):
        self.assert_denied_on_main("env FOO=1 git push")
        self.assert_denied_on_main("command git push")
        self.assert_denied_on_main("sudo -u me git push")

    def test_command_substitution(self):
        self.assert_denied_on_main("echo $(git push)")
        self.assert_denied_on_main("echo `git commit -m x`")

    def test_bash_c(self):
        self.assert_denied_on_main('bash -c "git push"')

    def test_after_separator(self):
        self.assert_denied_on_main("git status && git push")

    def test_allowed_on_main(self):
        self.assert_allowed_on_main("git status")
        self.assert_allowed_on_main("git log --grep push")
        self.assert_allowed_on_main("git diff push")
        self.assert_allowed_on_main('echo "git push"')
        self.assert_allowed_on_main('echo "a; git push; b"')
        self.assert_allowed_on_main("git checkout -b feature/x")

    def test_allowed_on_feature_branch(self):
        self.assertFalse(run_hook("git push", "feature/issue-1-x"))
        self.assertFalse(run_hook("/usr/bin/git -C . commit -m x", "feature/issue-1-x"))

    def test_unknown_detached_or_protected_branch(self):
        for branch in ("", "HEAD", "master"):
            self.assertTrue(run_hook("git commit -m x", branch))
            self.assertTrue(run_hook("git push", branch))
            self.assertFalse(run_hook("git status", branch))


class PushToMainTest(unittest.TestCase):
    """BodyMap参考版：mainを更新・削除し得るpushは、現在のブランチを問わず拒否する。"""

    FEATURE = "feature/issue-1-x"

    def assert_denied_everywhere(self, command: str):
        for branch in (self.FEATURE, "main"):
            self.assertTrue(run_hook(command, branch), f"allowed on {branch}: {command!r}")

    def test_refspec_to_main(self):
        self.assert_denied_everywhere("git push origin HEAD:main")
        self.assert_denied_everywhere("git push origin HEAD:refs/heads/main")
        self.assert_denied_everywhere("git push origin feature/x:main")
        self.assert_denied_everywhere("git push origin +HEAD:main")
        self.assert_denied_everywhere("git push -u origin HEAD:main")

    def test_main_by_name(self):
        self.assert_denied_everywhere("git push origin main")
        self.assert_denied_everywhere("git push --force origin main")

    def test_delete_main(self):
        self.assert_denied_everywhere("git push origin --delete main")
        self.assert_denied_everywhere("git push origin :main")

    def test_all_mirror_glob(self):
        self.assert_denied_everywhere("git push --all origin")
        self.assert_denied_everywhere("git push --mirror")
        self.assert_denied_everywhere("git push origin 'refs/heads/*:refs/heads/*'")

    def test_quoted_and_wrapped(self):
        self.assert_denied_everywhere('"git" push origin HEAD:main')
        self.assert_denied_everywhere("git -C . push origin HEAD:main")
        self.assert_denied_everywhere("env FOO=1 git push origin HEAD:main")
        self.assert_denied_everywhere('bash -c "git push origin HEAD:main"')

    def test_feature_branch_push_allowed(self):
        for cmd in (
            "git push",
            "git push origin HEAD",
            "git push -u origin feature/issue-374-x",
            "git push origin feature/x:feature/x",
            "git push origin main-fix",
            "git push origin feature/main",
        ):
            self.assertFalse(run_hook(cmd, self.FEATURE), f"denied: {cmd!r}")


class RemoteBranchDeleteTest(unittest.TestCase):
    """BodyMap参考版：Merge後のリモートbranch削除は、main上でも許可する（main自体の削除は除く）。"""

    def test_delete_allowed_on_main(self):
        for cmd in (
            "git push origin --delete feature/issue-374-x",
            "git push --delete origin feature/issue-374-x",
            "git push origin -d feature/issue-374-x",
            "git push origin :feature/issue-374-x",
            "git push origin --delete feature/a feature/b",
        ):
            self.assertFalse(run_hook(cmd, "main"), f"denied on main: {cmd!r}")

    def test_delete_mixed_with_normal_push_denied_on_main(self):
        self.assertTrue(
            run_hook("git push origin --delete feature/x && git push", "main")
        )
        self.assertTrue(run_hook("git push origin --delete feature/x; git commit -m x", "main"))

    def test_delete_without_target_is_normal_push(self):
        self.assertTrue(run_hook("git push --delete", "main"))


class PrMergeTest(unittest.TestCase):
    """`gh pr merge`は、ブランチを問わず常に拒否する。"""

    def assert_denied(self, command: str):
        self.assertTrue(run_hook(command, "feature/x"), f"allowed: {command!r}")

    def assert_allowed(self, command: str):
        self.assertFalse(run_hook(command, "feature/x"), f"denied: {command!r}")

    def test_plain(self):
        self.assert_denied("gh pr merge 1")

    def test_absolute_path(self):
        self.assert_denied("/opt/homebrew/bin/gh pr merge 1")

    def test_repo_option(self):
        self.assert_denied("gh -R owner/repo pr merge 1")
        self.assert_denied("gh --repo owner/repo pr merge 1 --squash")

    def test_quoted(self):
        self.assert_denied('"gh" pr merge 1')
        self.assert_denied("gh 'pr' \"merge\" 1")
        self.assert_denied("\\gh pr merge 1")

    def test_wrapper_and_substitution(self):
        self.assert_denied("env GH_TOKEN=x gh pr merge 1")
        self.assert_denied("echo $(gh pr merge 1)")
        self.assert_denied('bash -c "gh pr merge 1"')

    def test_after_separator(self):
        self.assert_denied("git status; gh pr merge 3")

    def test_api_pulls_merge(self):
        self.assert_denied("gh api -X PUT repos/o/r/pulls/1/merge")
        self.assert_denied('"gh" api repos/o/r/pulls/1/merge -X PUT')

    def test_api_graphql_merge_mutation(self):
        self.assert_denied(
            'gh api graphql -f query="mutation{mergePullRequest(input:{pullRequestId:\\"x\\"}){clientMutationId}}"'
        )

    def test_allowed(self):
        self.assert_allowed("gh pr view 1")
        self.assert_allowed("gh pr create --title x --body-file b.md")
        self.assert_allowed("gh api repos/o/r/pulls/1")
        self.assert_allowed('git commit -m "gh pr merge の説明"')
        self.assert_allowed('echo "gh pr merge 1"')


if __name__ == "__main__":
    unittest.main()
