#!/usr/bin/env python3
"""Tests for RULE-GIT variant B: the phase branch is the agent's, the mainline is not.

A real git repository in each test, because what the guard asks git - "which branch is checked
out" - cannot be faked without testing the fake.
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

CONFIG = {"version": 1, "build": ["true"], "test": ["true"]}


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def guard(command, root):
    proc = subprocess.run(
        [sys.executable, os.path.join(HOOKS, "guard_bash.py")],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": command}}),
        capture_output=True, text=True, cwd=root,
        env={**os.environ, "CLAUDE_PROJECT_DIR": root},
    )

    return proc.returncode, proc.stderr


class Repo:
    """A repository with one commit, checked out on the branch the test names."""

    def __init__(self, branch="phase/3", init=True):
        self.branch = branch
        self.init = init

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="gitrules-")

        with open(os.path.join(self.dir, ".dev-factory.json"), "w", encoding="utf-8") as f:
            json.dump(CONFIG, f)

        if not self.init:
            return self.dir

        git(self.dir, "init", "-q", "-b", "main")
        git(self.dir, "config", "user.email", "t@example.com")
        git(self.dir, "config", "user.name", "t")

        with open(os.path.join(self.dir, "a.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")

        git(self.dir, "add", "a.txt")
        git(self.dir, "commit", "-q", "-m", "first")

        if self.branch != "main":
            git(self.dir, "checkout", "-q", "-b", self.branch)

        return self.dir

    def __exit__(self, *_):
        shutil.rmtree(self.dir, ignore_errors=True)


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class ThePhaseBranchIsTheAgents(unittest.TestCase):
    def test_pushing_the_checked_out_phase_branch_is_allowed(self):
        with Repo("phase/3") as root:
            self.assertEqual(ALLOW, guard("git push", root)[0])
            self.assertEqual(ALLOW, guard("git push origin phase/3", root)[0])
            self.assertEqual(ALLOW, guard("git push -u origin phase/3", root)[0])

    def test_an_explicit_refspec_onto_the_phase_branch_is_allowed(self):
        # Found by mutation: dropping the split of "source:destination" failed nothing, because
        # every refspec test named a destination that was blocked either way. What the split
        # actually buys is this - reading the destination out of a legitimate push, rather than
        # rejecting it for containing a colon.
        with Repo("phase/3") as root:
            for command in ("git push origin HEAD:phase/3",
                            "git push origin phase/3:phase/3"):
                code, stderr = guard(command, root)

                self.assertEqual(ALLOW, code, f"{command}: {stderr}")

    def test_a_readable_suffix_is_allowed_but_the_number_is_not_optional(self):
        # The number is what ties the branch to a PHASE-n block in the spec. Without it a
        # branch is just a branch, and the rule stops meaning anything.
        with Repo("phase/8-polish") as root:
            self.assertEqual(ALLOW, guard("git push", root)[0])
            self.assertEqual(ALLOW, guard("git push origin phase/8-polish", root)[0])

        with Repo("phase/finish") as root:
            code, stderr = guard("git push", root)

        self.assertEqual(BLOCK, code)
        self.assertIn("not a phase branch", stderr)

    def test_creating_any_branch_is_allowed_on_purpose(self):
        # Not an oversight. A scratch branch cannot be pushed and a commit on it does not touch
        # the mainline, so restricting creation would be a rule that prevents nothing.
        with Repo("phase/3") as root:
            for command in ("git checkout -b feature/experiment", "git switch -c scratch",
                            "git branch anything"):
                self.assertEqual(ALLOW, guard(command, root)[0], command)

    def test_committing_on_a_phase_branch_is_allowed(self):
        with Repo("phase/12") as root:
            self.assertEqual(ALLOW, guard('git commit -m "work"', root)[0])

    def test_creating_a_phase_branch_is_allowed(self):
        with Repo("main") as root:
            for command in ("git checkout -b phase/4", "git switch -c phase/4",
                            "git branch phase/4", "git switch phase/4"):
                self.assertEqual(ALLOW, guard(command, root)[0], command)


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class TheMainlineIsNot(unittest.TestCase):
    def test_committing_on_the_mainline_is_blocked(self):
        # The one thing worth preventing, and it has exactly one way in. Guarding checkout
        # instead would be a rule against moving around: easy to trip over and easy to evade.
        for branch in ("main", "master"):
            with Repo("main") as root:
                if branch == "master":
                    git(root, "branch", "-m", "master")

                code, stderr = guard('git commit -m "x"', root)

                self.assertEqual(BLOCK, code, branch)
                self.assertIn(branch, stderr)

    def test_pushing_the_mainline_is_blocked_however_it_is_named(self):
        with Repo("phase/3") as root:
            for command in ("git push origin main", "git push origin HEAD:main",
                            "git push origin phase/3:main"):
                code, stderr = guard(command, root)

                self.assertEqual(BLOCK, code, command)
                self.assertIn("not a phase branch", stderr)

    def test_pushing_from_the_mainline_is_blocked(self):
        # No destination named, so the destination is the checked-out branch.
        with Repo("main") as root:
            code, stderr = guard("git push", root)

        self.assertEqual(BLOCK, code)
        self.assertIn("not a phase branch", stderr)

    def test_pushing_some_other_branch_is_blocked(self):
        with Repo("phase/3") as root:
            self.assertEqual(BLOCK, guard("git push origin feature/experiment", root)[0])
            self.assertEqual(BLOCK, guard("git push origin phase3", root)[0])
            self.assertEqual(BLOCK, guard("git push origin phase/", root)[0])


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class WhatIsNeverAllowed(unittest.TestCase):
    def test_force_is_blocked_even_on_a_phase_branch(self):
        with Repo("phase/3") as root:
            for command in ("git push --force origin phase/3",
                            "git push -f origin phase/3",
                            "git push --force-with-lease origin phase/3",
                            "git push origin +phase/3"):
                code, stderr = guard(command, root)

                self.assertEqual(BLOCK, code, command)

    def test_force_with_lease_is_blocked_deliberately(self):
        # It is the safer of the two and it still replaces a commit somebody may have read.
        with Repo("phase/3") as root:
            _, stderr = guard("git push --force-with-lease", root)

        self.assertIn("force-with-lease", stderr)

    def test_bulk_publishing_is_blocked(self):
        with Repo("phase/3") as root:
            for command in ("git push --all", "git push --mirror", "git push --tags",
                            "git push origin --delete phase/2"):
                self.assertEqual(BLOCK, guard(command, root)[0], command)

    def test_deleting_or_renaming_a_branch_is_blocked(self):
        with Repo("phase/3") as root:
            for command in ("git branch -D phase/2", "git branch -d phase/2",
                            "git branch -m phase/2 phase/9"):
                self.assertEqual(BLOCK, guard(command, root)[0], command)

    def test_remotes_and_history_are_still_the_owners(self):
        with Repo("phase/3") as root:
            for command in ("git remote add origin x", "git rebase -i HEAD~2",
                            "git reset --hard HEAD~1", 'git commit --amend -m "x"',
                            "gh pr create"):
                self.assertEqual(BLOCK, guard(command, root)[0], command)

    def test_a_push_whose_destination_cannot_be_determined_is_blocked(self):
        # Not a git repository, so git cannot say what is checked out. An unknown destination
        # is not one the agent may push to - the safe answer is no, not "probably fine".
        with Repo(init=False) as root:
            code, stderr = guard("git push", root)

        self.assertEqual(BLOCK, code)
        self.assertIn("cannot tell which branch", stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
