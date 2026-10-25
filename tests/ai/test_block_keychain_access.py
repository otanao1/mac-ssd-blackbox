#!/usr/bin/env python3
"""
block_keychain_access.py のユニットテスト（BodyMap参考版）。

標準ライブラリのunittestのみ。次のいずれでも実行できる。

  python3 -m unittest discover -s tests/ai -v
"""

import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "ai" / "block_keychain_access.py"

spec = importlib.util.spec_from_file_location("block_keychain_access", MODULE_PATH)
block_keychain_access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(block_keychain_access)

check_command_text = block_keychain_access.check_command_text


class Base(unittest.TestCase):
    def assert_denied(self, command: str):
        reason = check_command_text(command)
        self.assertIsNotNone(reason, f"denied expected but allowed: {command!r}")

    def assert_allowed(self, command: str):
        reason = check_command_text(command)
        self.assertIsNone(reason, f"allowed expected but denied: {command!r} ({reason})")


class DeniedTest(Base):
    def test_find_generic_password(self):
        self.assert_denied("security find-generic-password -s synthetic-test-service -a synthetic-user -w")

    def test_find_internet_password(self):
        self.assert_denied("security find-internet-password -s github.com -w")

    def test_dump_keychain(self):
        self.assert_denied("security dump-keychain -d")

    def test_any_subcommand(self):
        self.assert_denied("security list-keychains")

    def test_absolute_path(self):
        self.assert_denied("/usr/bin/security find-generic-password -s x -w")

    def test_env_assignment_prefix(self):
        self.assert_denied("FOO=1 security find-generic-password -s x -w")

    def test_env_wrapper(self):
        self.assert_denied("env security find-generic-password -s x -w")

    def test_env_wrapper_with_assignment(self):
        self.assert_denied("env FOO=1 security find-generic-password -s x -w")

    def test_command_wrapper(self):
        self.assert_denied("command security find-generic-password -s x -w")

    def test_sudo_wrapper(self):
        self.assert_denied("sudo security find-generic-password -s x -w")

    def test_exec_wrapper(self):
        self.assert_denied("exec security find-generic-password -s x -w")

    def test_after_and(self):
        self.assert_denied("echo hi && security find-generic-password -s x -w")

    def test_after_pipe(self):
        self.assert_denied("echo hi | security find-generic-password -s x -w")

    def test_after_semicolon(self):
        self.assert_denied("echo hi; security find-generic-password -s x -w")

    def test_newline_separated(self):
        self.assert_denied("echo hi\nsecurity find-generic-password -s x -w")

    def test_dollar_paren(self):
        self.assert_denied("echo $(security find-generic-password -s x -w)")

    def test_variable_assignment_dollar_paren(self):
        self.assert_denied("TOKEN=$(security find-generic-password -s x -w)")

    def test_backtick(self):
        self.assert_denied("echo `security find-generic-password -s x -w`")

    def test_bash_dash_c(self):
        self.assert_denied('bash -c "security find-generic-password -s x -w"')

    def test_eval(self):
        self.assert_denied("eval 'security find-generic-password -s x -w'")

    def test_nested(self):
        self.assert_denied('bash -c "echo $(security find-generic-password -s x -w)"')


class AllowedTest(Base):
    def test_grep_security_word(self):
        self.assert_allowed("grep security docs/cloudflare.md")

    def test_git_commit_message(self):
        self.assert_allowed('git commit -m "security fix"')

    def test_git_log_grep(self):
        self.assert_allowed("git log --grep security")

    def test_echo_security(self):
        self.assert_allowed("echo security")

    def test_ls(self):
        self.assert_allowed("ls -la")

    def test_run_script(self):
        # scriptの内部でsecurityが呼ばれても、Bashコマンドの文字列には現れない。
        self.assert_allowed("bash scripts/example-auth-wrapper.sh")

    def test_script_name_contains_security(self):
        self.assert_allowed("bash scripts/security-check.sh")

    def test_path_containing_security_dir(self):
        self.assert_allowed("cat docs/security/notes.md")

    def test_empty(self):
        self.assert_allowed("")


class WrapperOptionTest(Base):
    """BodyMap参考版：ラッパーのオプション付き（値を取るものを含む）も拒否する。"""

    def test_env_unset_option(self):
        self.assert_denied("env --unset=FOO security find-generic-password -s x -w")

    def test_env_short_option_with_value(self):
        self.assert_denied("env -u FOO security find-generic-password -s x -w")

    def test_sudo_user_option(self):
        self.assert_denied("sudo -u someone security find-generic-password -s x -w")

    def test_nice_option(self):
        self.assert_denied("nice -n 10 security find-generic-password -s x -w")

    def test_time_wrapper(self):
        self.assert_denied("time security find-generic-password -s x -w")

    def test_nohup_wrapper(self):
        self.assert_denied("nohup security find-generic-password -s x -w")

    def test_xargs_wrapper(self):
        self.assert_denied("echo x | xargs security find-generic-password -s x -w")

    def test_assignment_then_wrapper(self):
        self.assert_denied("FOO=1 env --unset=BAR security dump-keychain")

    def test_wrapper_absolute_security_path(self):
        self.assert_denied("env --unset=FOO /usr/bin/security list-keychains")

    def test_wrapper_inside_bash_c(self):
        self.assert_denied('bash -c "sudo -u someone security dump-keychain"')

    def test_wrapper_without_security_allowed(self):
        self.assert_allowed("time ls -la")

    def test_env_without_security_allowed(self):
        self.assert_allowed("env --unset=FOO printenv HOME")

    def test_grep_security_without_wrapper_allowed(self):
        self.assert_allowed("grep -r security docs")


if __name__ == "__main__":
    unittest.main()
