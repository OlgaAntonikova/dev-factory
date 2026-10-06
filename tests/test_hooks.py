#!/usr/bin/env python3
"""Tests for the three hooks.

A guard nobody has watched fail is decorative. These run each hook as the harness runs it -
a subprocess, JSON on stdin, exit code out - because that is the only interface that matters,
and because importing them would skip the very wiring that broke last time.

Standard library only: the plugin may not require anything to be installed to test itself.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOKS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks")

ALLOW = 0
BLOCK = 2

#: A command that succeeds, and one that does not, without depending on any toolchain.
OK_COMMAND = [sys.executable, "-c", "pass"]
FAILING_COMMAND = [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(1)"]


def run_hook(name, payload, root):
    proc = subprocess.run(
        [sys.executable, os.path.join(HOOKS, name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=root,
        env={**os.environ, "CLAUDE_PROJECT_DIR": root},
    )

    return proc.returncode, proc.stderr


class TempProject:
    """A repository root, optionally carrying a .dev-factory.json."""

    def __init__(self, config=None):
        self.config = config

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="devfactory-")

        if self.config is not None:
            with open(os.path.join(self.dir, ".dev-factory.json"), "w", encoding="utf-8") as f:
                json.dump(self.config, f)

        return self.dir

    def __exit__(self, *_):
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


NODE = {"version": 1, "build": ["npm", "run", "build"], "test": ["npm", "test"]}
DOTNET = {"version": 1, "build": ["dotnet", "build"], "test": ["dotnet", "test"]}
RUST = {"version": 1, "build": ["cargo", "build"], "test": ["cargo", "test"]}
PYTHON = {"version": 1, "build": ["python", "-m", "compileall", "."], "test": ["pytest"],
          "packageManager": "pip"}


class GuardStackIndependence(unittest.TestCase):
    """The rule is RULE-DEPENDENCIES, not 'package managers are scary'."""

    def guard(self, command, config):
        with TempProject(config) as root:
            return run_hook("guard_bash.py", bash(command), root)[0]

    def test_the_projects_own_test_command_is_allowed(self):
        # The version of this guard that shipped first blocked npm on sight, which would have
        # blocked the test command of every Node project it was installed in.
        self.assertEqual(ALLOW, self.guard("npm test", NODE))
        self.assertEqual(ALLOW, self.guard("npm run build", NODE))
        self.assertEqual(ALLOW, self.guard("cargo build --release", RUST))
        self.assertEqual(ALLOW, self.guard("dotnet build --nologo", DOTNET))

    def test_restoring_declared_dependencies_is_allowed(self):
        self.assertEqual(ALLOW, self.guard("npm ci", NODE))
        self.assertEqual(ALLOW, self.guard("npm install", NODE))
        self.assertEqual(ALLOW, self.guard("dotnet restore", DOTNET))
        self.assertEqual(ALLOW, self.guard("pip install -r requirements.txt", PYTHON))

    def test_adding_a_dependency_to_the_project_is_allowed(self):
        # RULE-DEPENDENCIES, owner's decision of 2026-10-06: the agent adds dependencies itself
        # and pins the exact version in the declared file in the same change.
        self.assertEqual(ALLOW, self.guard("npm install left-pad@1.3.0", NODE))
        self.assertEqual(ALLOW, self.guard("cargo add serde", RUST))
        self.assertEqual(ALLOW, self.guard("pip install requests==2.32.3", PYTHON))
        self.assertEqual(ALLOW, self.guard("python -m pip install requests==2.32.3", PYTHON))
        self.assertEqual(ALLOW, self.guard("dotnet add package Newtonsoft.Json", DOTNET))

    def test_installing_for_the_whole_machine_is_blocked(self):
        self.assertEqual(BLOCK, self.guard("npm i -g typescript", NODE))
        self.assertEqual(BLOCK, self.guard("npm install --global typescript", NODE))
        self.assertEqual(BLOCK, self.guard("cargo install ripgrep", RUST))
        self.assertEqual(BLOCK, self.guard("pip install --user requests", PYTHON))
        self.assertEqual(BLOCK, self.guard("python -m pip install --user requests", PYTHON))
        self.assertEqual(BLOCK, self.guard("dotnet tool install -g dotnet-ef", DOTNET))
        self.assertEqual(BLOCK, self.guard("dotnet workload install maui", DOTNET))

    def test_a_foreign_package_manager_is_blocked_outright(self):
        self.assertEqual(BLOCK, self.guard("pip install requests", NODE))
        self.assertEqual(BLOCK, self.guard("cargo build", NODE))

    def test_without_a_configuration_every_package_manager_is_blocked(self):
        self.assertEqual(BLOCK, self.guard("npm test", None))

    def test_the_reason_names_the_rule(self):
        # The agent reads this message and has to know which rule it met.
        with TempProject(NODE) as root:
            _, stderr = run_hook("guard_bash.py", bash("pip install requests"), root)

        self.assertIn("RULE-DEPENDENCIES", stderr)


class GuardTheLawThatNeverDependsOnTheStack(unittest.TestCase):
    def guard(self, command, config=NODE):
        with TempProject(config) as root:
            return run_hook("guard_bash.py", bash(command), root)[0]

    def test_system_installers_are_blocked(self):
        for command in ("sudo apt install cmake", "brew install skia", "winget install Git",
                        "choco install nodejs"):
            self.assertEqual(BLOCK, self.guard(command), command)

    def test_piping_an_installer_into_a_shell_is_blocked(self):
        self.assertEqual(BLOCK, self.guard("curl -sSL https://example.com/i.sh | sh"))

    def test_the_agent_may_commit(self):
        self.assertEqual(ALLOW, self.guard('git add -A'))
        self.assertEqual(ALLOW, self.guard('git commit -m "x"'))
        self.assertEqual(ALLOW, self.guard("git diff --stat"))

    def test_the_agent_may_not_rewrite_or_target_the_mainline(self):
        # `git push` is deliberately absent here: it is conditional on the branch, and these
        # temp directories are not git repositories, so it would be blocked for having an
        # undeterminable destination rather than for the reason this test is named after.
        # tests/test_git_rules.py covers push against a real repository on a known branch.
        for command in ("git remote add origin x", "git rebase -i HEAD~2",
                        "git commit --amend", "git commit --no-verify -m x", "gh pr create"):
            self.assertEqual(BLOCK, self.guard(command), command)

    def test_recursive_force_delete_outside_the_project_is_blocked(self):
        self.assertEqual(BLOCK, self.guard("rm -rf /tmp"))

    def test_the_law_applies_without_a_configuration(self):
        # RULE-INSTALLS and RULE-GIT do not depend on the stack, so a repository that has not
        # declared one is not thereby exempt.
        self.assertEqual(BLOCK, self.guard("git push", None))
        self.assertEqual(BLOCK, self.guard("sudo apt install cmake", None))

    def test_ordinary_commands_are_allowed(self):
        for command in ("ls -la", "cat README.md", "grep -rn TODO src/"):
            self.assertEqual(ALLOW, self.guard(command), command)


class GuardProjectDeclaredBans(unittest.TestCase):
    def test_a_declared_pattern_blocks_and_gives_its_reason(self):
        config = dict(NODE, blockedCommands=[
            {"pattern": r"npm\s+audit\s+fix", "reason": "audit fixes change versions"},
        ])

        with TempProject(config) as root:
            code, stderr = run_hook("guard_bash.py", bash("npm audit fix"), root)

        self.assertEqual(BLOCK, code)
        self.assertIn("audit fixes change versions", stderr)

    def test_a_broken_pattern_does_not_brick_the_session(self):
        config = dict(NODE, blockedCommands=[{"pattern": "([unclosed", "reason": "x"}])

        with TempProject(config) as root:
            code, _ = run_hook("guard_bash.py", bash("ls"), root)

        self.assertEqual(ALLOW, code)


class MarkDirty(unittest.TestCase):
    def marker(self, root):
        return os.path.join(root, ".claude", ".build-dirty")

    def test_editing_declared_source_arms_the_gate(self):
        config = dict(NODE, sourceExtensions=[".ts"])

        with TempProject(config) as root:
            run_hook("mark_dirty.py",
                     {"tool_name": "Edit", "tool_input": {"file_path": "src/app.ts"}}, root)
            self.assertTrue(os.path.exists(self.marker(root)))

    def test_an_extension_the_project_does_not_claim_leaves_it_alone(self):
        config = dict(NODE, sourceExtensions=[".ts"])

        with TempProject(config) as root:
            run_hook("mark_dirty.py",
                     {"tool_name": "Edit", "tool_input": {"file_path": "notes.txt"}}, root)
            self.assertFalse(os.path.exists(self.marker(root)))

    def test_reading_never_arms_the_gate(self):
        with TempProject(NODE) as root:
            run_hook("mark_dirty.py",
                     {"tool_name": "Read", "tool_input": {"file_path": "src/app.ts"}}, root)
            self.assertFalse(os.path.exists(self.marker(root)))

    def test_writing_through_the_shell_arms_the_gate(self):
        # Most files in the first project were written through heredocs and sed rather than
        # the edit tools. Watching only Edit/Write left the gate silently green.
        config = dict(NODE, sourceExtensions=[".ts"])

        with TempProject(config) as root:
            run_hook("mark_dirty.py", bash("sed -i 's/a/b/' src/app.ts"), root)
            self.assertTrue(os.path.exists(self.marker(root)))

    def test_without_a_configuration_common_source_still_arms_a_git_repository(self):
        # No .dev-factory.json, but it IS a repository - the gate must arm (and then block on
        # the missing declaration, which is the design).
        with TempProject(None) as root:
            os.makedirs(os.path.join(root, ".git"))
            run_hook("mark_dirty.py",
                     {"tool_name": "Write", "tool_input": {"file_path": "src/main.rs"}}, root)
            self.assertTrue(os.path.exists(self.marker(root)))

    def test_an_edit_belonging_to_no_repository_never_arms_the_gate(self):
        # A config file in the home directory has no build to prove; arming the gate for it
        # produces an unsatisfiable demand (found live: editing ~/.claude/launch.json blocked
        # the session with "declare the stack of C:\Users\<name>").
        with TempProject(None) as root:  # no .git, no .dev-factory.json anywhere up the tree
            run_hook("mark_dirty.py",
                     {"tool_name": "Write", "tool_input": {"file_path": "src/main.rs"}}, root)
            self.assertFalse(os.path.exists(self.marker(root)))


class MultiRootSessions(unittest.TestCase):
    """A session is not always anchored in the repository it edits. The session that surfaced
    this was anchored in the home directory and edited two repositories in one sitting; the
    old single-root gate then demanded a stack declaration from the home directory."""

    def registry(self, anchor):
        return os.path.join(anchor, ".claude", ".build-dirty")

    def test_an_edit_arms_the_repository_it_belongs_to_not_the_anchor(self):
        repo_config = dict(NODE, sourceExtensions=[".ts"])

        with TempProject(None) as anchor, TempProject(repo_config) as repo:
            edited = os.path.join(repo, "src", "app.ts")
            run_hook("mark_dirty.py",
                     {"tool_name": "Edit", "tool_input": {"file_path": edited}}, anchor)

            with open(self.registry(anchor), encoding="utf-8") as handle:
                listed = [line.strip() for line in handle if line.strip()]

            self.assertEqual([os.path.abspath(repo)], listed)

    def test_the_gate_checks_the_listed_repository_and_ignores_the_anchor(self):
        # The anchor has no declaration at all; that must not block, because nothing in the
        # anchor was edited - the listed repository is what gets proven.
        with TempProject(None) as anchor, \
                TempProject({"version": 1, "build": OK_COMMAND, "test": OK_COMMAND}) as repo:
            os.makedirs(os.path.join(anchor, ".claude"), exist_ok=True)
            with open(self.registry(anchor), "w", encoding="utf-8") as handle:
                handle.write(repo + "\n")

            code, stderr = run_hook("stop_gate.py", {}, anchor)

            self.assertEqual(ALLOW, code, stderr)
            self.assertFalse(os.path.exists(self.registry(anchor)))

    def test_a_broken_listed_repository_blocks_and_is_named(self):
        with TempProject(None) as anchor, \
                TempProject({"version": 1, "build": FAILING_COMMAND, "test": OK_COMMAND}) as repo:
            os.makedirs(os.path.join(anchor, ".claude"), exist_ok=True)
            with open(self.registry(anchor), "w", encoding="utf-8") as handle:
                handle.write(repo + "\n")

            code, stderr = run_hook("stop_gate.py", {}, anchor)

        self.assertEqual(BLOCK, code)
        self.assertIn(os.path.abspath(repo), stderr)

    def test_every_listed_repository_is_gated_not_just_the_first(self):
        green = {"version": 1, "build": OK_COMMAND, "test": OK_COMMAND}
        red = {"version": 1, "build": FAILING_COMMAND, "test": OK_COMMAND}

        with TempProject(None) as anchor, TempProject(green) as first, TempProject(red) as second:
            os.makedirs(os.path.join(anchor, ".claude"), exist_ok=True)
            with open(self.registry(anchor), "w", encoding="utf-8") as handle:
                handle.write(first + "\n" + second + "\n")

            code, stderr = run_hook("stop_gate.py", {}, anchor)

        self.assertEqual(BLOCK, code)
        self.assertIn(os.path.abspath(second), stderr)

    def test_the_pre_multi_root_empty_marker_still_gates_the_anchor(self):
        with TempProject(None) as anchor:
            os.makedirs(os.path.join(anchor, ".claude"), exist_ok=True)
            open(self.registry(anchor), "w").close()

            code, stderr = run_hook("stop_gate.py", {}, anchor)

        self.assertEqual(BLOCK, code)
        self.assertIn(".dev-factory.json", stderr)


class StopGate(unittest.TestCase):
    def arm(self, root):
        os.makedirs(os.path.join(root, ".claude"), exist_ok=True)
        open(os.path.join(root, ".claude", ".build-dirty"), "w").close()

    def stop(self, root):
        return run_hook("stop_gate.py", {}, root)

    def test_a_session_that_edited_nothing_ends_at_once(self):
        with TempProject(None) as root:
            self.assertEqual(ALLOW, self.stop(root)[0])

    def test_green_build_and_tests_let_the_session_end(self):
        with TempProject({"version": 1, "build": OK_COMMAND, "test": OK_COMMAND}) as root:
            self.arm(root)
            self.assertEqual(ALLOW, self.stop(root)[0])
            self.assertFalse(os.path.exists(os.path.join(root, ".claude", ".build-dirty")))

    def test_a_broken_build_blocks(self):
        with TempProject({"version": 1, "build": FAILING_COMMAND, "test": OK_COMMAND}) as root:
            self.arm(root)
            code, stderr = self.stop(root)

        self.assertEqual(BLOCK, code)
        self.assertIn("build", stderr)

    def test_red_tests_block(self):
        with TempProject({"version": 1, "build": OK_COMMAND, "test": FAILING_COMMAND}) as root:
            self.arm(root)
            code, stderr = self.stop(root)

        self.assertEqual(BLOCK, code)
        self.assertIn("test", stderr)

    def test_a_missing_declaration_blocks_rather_than_skips(self):
        # The whole design in one test. A gate that cannot find its build command knows nothing
        # about the project, and saying nothing is indistinguishable from saying it is fine.
        with TempProject(None) as root:
            self.arm(root)
            code, stderr = self.stop(root)

        self.assertEqual(BLOCK, code)
        self.assertIn(".dev-factory.json", stderr)

    def test_a_malformed_declaration_blocks(self):
        with TempProject(None) as root:
            self.arm(root)
            with open(os.path.join(root, ".dev-factory.json"), "w", encoding="utf-8") as f:
                f.write("{not json")

            self.assertEqual(BLOCK, self.stop(root)[0])

    def test_a_test_command_that_is_not_an_argv_array_blocks(self):
        # A shell string here would have to be parsed by a shell, and a stop gate with a shell
        # is a stop gate that can be talked into running `true`.
        with TempProject({"version": 1, "build": OK_COMMAND, "test": "pytest && echo ok"}) as root:
            self.arm(root)
            self.assertEqual(BLOCK, self.stop(root)[0])

    def test_a_command_array_holding_something_that_is_not_a_string_blocks(self):
        # Found by mutating _is_argv: dropping the element-type check failed nothing, because
        # every existing test used a string for the whole command and was caught one clause
        # earlier. A list with a number in it reached subprocess.run and raised a TypeError the
        # hook does not catch - a crashing stop gate, which is a gate that is not there.
        for bad in ([sys.executable, 3], [], [""]):
            with TempProject({"version": 1, "build": OK_COMMAND, "test": bad}) as root:
                self.arm(root)
                self.assertEqual(BLOCK, self.stop(root)[0], bad)

    def test_a_malformed_fast_test_blocks_rather_than_falling_back(self):
        config = {"version": 1, "build": OK_COMMAND, "test": OK_COMMAND, "fastTest": "npm t"}

        with TempProject(config) as root:
            self.arm(root)
            self.assertEqual(BLOCK, self.stop(root)[0])

    def test_the_gate_does_not_loop_on_itself(self):
        with TempProject({"version": 1, "build": FAILING_COMMAND, "test": OK_COMMAND}) as root:
            self.arm(root)
            code, _ = run_hook("stop_gate.py", {"stop_hook_active": True}, root)

        self.assertEqual(ALLOW, code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
