#!/usr/bin/env python3
"""Tests for the review gate.

Each one builds a real git repository, stages something, and checks what the gate does with a
`git commit`. Real git rather than a stub, because what is being hashed is git's own diff
output and a stub would be testing the stub.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(os.path.dirname(HERE), "hooks")

ALLOW = 0
BLOCK = 2

GATE = os.path.join(HOOKS, "review_gate.py")


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def gate(payload, root):
    proc = subprocess.run(
        [sys.executable, GATE],
        input=json.dumps(payload), capture_output=True, text=True, cwd=root,
        env={**os.environ, "CLAUDE_PROJECT_DIR": root},
    )

    return proc.returncode, proc.stderr


def stamp(root, *extra):
    proc = subprocess.run(
        [sys.executable, GATE, "--root", root, *extra],
        capture_output=True, text=True,
    )

    return proc.returncode, proc.stdout + proc.stderr


def commit(command='git commit -m "x"'):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


class GitRepo:
    """A repository with one staged change, unless a test says otherwise."""

    def __init__(self, stage=True):
        self.stage = stage

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="reviewgate-")
        git(self.dir, "init", "-q")
        git(self.dir, "config", "user.email", "t@example.com")
        git(self.dir, "config", "user.name", "t")

        self.write("src.txt", "first version\n")

        if self.stage:
            git(self.dir, "add", "src.txt")

        return self.dir

    def write(self, name, content):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
            handle.write(content)

    def __exit__(self, *_):
        shutil.rmtree(self.dir, ignore_errors=True)


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class TheGate(unittest.TestCase):
    def test_a_commit_without_any_review_is_blocked(self):
        with GitRepo() as root:
            code, stderr = gate(commit(), root)

        self.assertEqual(BLOCK, code)
        self.assertIn("RULE-REVIEW", stderr)

    def test_a_commit_after_an_approval_of_that_diff_is_allowed(self):
        with GitRepo() as root:
            self.assertEqual(0, stamp(root, "--approve", "--reviewer", "reviewer")[0])

            code, stderr = gate(commit(), root)

        self.assertEqual(ALLOW, code, stderr)

    def test_changing_the_diff_after_the_review_blocks_the_commit(self):
        # The whole point. Approving one change and committing another is the failure this
        # hook exists to make impossible.
        with GitRepo() as root:
            stamp(root, "--approve", "--reviewer", "reviewer")

            with open(os.path.join(root, "src.txt"), "w", encoding="utf-8") as handle:
                handle.write("something else entirely\n")

            git(root, "add", "src.txt")

            code, stderr = gate(commit(), root)

        self.assertEqual(BLOCK, code)
        self.assertIn("not the one that was reviewed", stderr)

    def test_a_block_verdict_stops_the_commit_and_shows_the_findings(self):
        with GitRepo() as root:
            stamp(root, "--block", "--reviewer", "reviewer",
                  "--findings", "the dimension test observes nothing")

            code, stderr = gate(commit(), root)

        self.assertEqual(BLOCK, code)
        self.assertIn("the dimension test observes nothing", stderr)

    def test_commit_dash_a_is_blocked_because_it_stages_while_committing(self):
        # `git commit -am` stages files as it commits, so the diff the gate hashed is not the
        # diff that lands. Allowing it would make every other check here decorative.
        with GitRepo() as root:
            stamp(root, "--approve", "--reviewer", "reviewer")

            for command in ('git commit -am "x"', 'git commit -a -m "x"',
                            'git commit --all -m "x"'):
                code, stderr = gate(commit(command), root)

                self.assertEqual(BLOCK, code, command)
                self.assertIn("stages files", stderr)

    def test_an_ordinary_commit_flag_is_not_mistaken_for_all(self):
        with GitRepo() as root:
            stamp(root, "--approve", "--reviewer", "reviewer")

            for command in ('git commit -m "x"', 'git commit -q -m "x"',
                            'git commit --author "a <a@b.c>" -m "x"'):
                code, stderr = gate(commit(command), root)

                self.assertEqual(ALLOW, code, f"{command}: {stderr}")

    def test_commands_that_are_not_commits_pass_through(self):
        with GitRepo() as root:
            for command in ("git status", "git add -A", "ls", 'echo "git commit"'):
                self.assertEqual(ALLOW, gate(commit(command), root)[0], command)

    def test_a_malformed_verdict_blocks_rather_than_waving_through(self):
        with GitRepo() as root:
            os.makedirs(os.path.join(root, ".claude"), exist_ok=True)

            with open(os.path.join(root, ".claude", ".review-verdict"), "w") as handle:
                handle.write("{not json")

            code, stderr = gate(commit(), root)

        self.assertEqual(BLOCK, code)
        self.assertIn("could not be read", stderr)

    def test_a_verdict_missing_its_hash_blocks(self):
        with GitRepo() as root:
            os.makedirs(os.path.join(root, ".claude"), exist_ok=True)

            with open(os.path.join(root, ".claude", ".review-verdict"), "w") as handle:
                json.dump({"verdict": "APPROVE"}, handle)

            code, stderr = gate(commit(), root)

        self.assertEqual(BLOCK, code)

    def test_malformed_hook_input_fails_open(self):
        # A bug in this hook must not brick a session. The verdict failing closed and the hook
        # input failing open are different decisions about different things.
        with GitRepo() as root:
            proc = subprocess.run(
                [sys.executable, GATE], input="not json",
                capture_output=True, text=True, cwd=root,
            )

        self.assertEqual(ALLOW, proc.returncode)

    def test_stamping_with_nothing_staged_is_refused(self):
        # A verdict over an empty diff approves nothing while looking like an approval.
        with GitRepo(stage=False) as root:
            code, output = stamp(root, "--approve")

        self.assertNotEqual(0, code)
        self.assertIn("Nothing is staged", output)

    def test_status_reports_whether_the_verdict_matches(self):
        with GitRepo() as root:
            stamp(root, "--approve", "--reviewer", "reviewer")
            _, output = stamp(root, "--status")

        self.assertIn("APPROVE", output)
        self.assertIn("matches:     True", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
