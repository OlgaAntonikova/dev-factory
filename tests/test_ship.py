"""tools/ship.py: every command it runs is first allowed by the real guard, and nothing merges
unless CI is green.

The flow runs against a scripted fake of git and gh; the guard consultation runs the plugin's
real hooks/guard_bash.py in a temporary repository.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import ship  # noqa: E402

BRANCH = "feature/7-thing"
HEAD = "a" * 40
OLD_HEAD = "b" * 40
CONFIG = {"version": 1, "build": [sys.executable, "-c", "pass"],
          "test": [sys.executable, "-c", "pass"]}
GREEN_RUN = [{"name": "build-and-test", "conclusion": "SUCCESS"}]
RED_RUN = [{"name": "build-and-test", "conclusion": "FAILURE"}]


class FakeGitHub:
    """Answers git and gh commands from a script; records what ran."""

    def __init__(self, *, branch=BRANCH, dirty="", open_prs=None, rollups=None,
                 created="https://github.com/o/r/pull/31\n", heads=None, listed=None,
                 log=None):
        self.branch, self.dirty, self.created = branch, dirty, created
        self.open_prs, self.listed = open_prs or [], listed
        self.rollups = list(rollups if rollups is not None else [GREEN_RUN])
        self.heads = list(heads or [HEAD])
        self.ran, self.log = [], log

    def __call__(self, argv, root, timeout):
        self.ran.append(argv)
        if self.log is not None:
            self.log.append(("run", ship.shlex.join(argv)))
        words = argv[:3]

        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return 0, HEAD + "\n", ""
        if argv[:2] == ["git", "rev-parse"]:
            return 0, self.branch + "\n", ""
        if argv[:2] == ["git", "status"]:
            return 0, self.dirty, ""
        if words == ["gh", "pr", "list"]:
            return 0, self.listed if self.listed is not None else json.dumps(self.open_prs), ""
        if words == ["gh", "pr", "create"]:
            return 0, self.created, ""
        if words == ["gh", "pr", "view"]:
            rollup = self.rollups.pop(0) if len(self.rollups) > 1 else self.rollups[0]
            head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
            return 0, json.dumps({"headRefOid": head, "statusCheckRollup": rollup}), ""
        if words == ["gh", "pr", "checks"]:
            return (0 if self.rollups[0] == GREEN_RUN else 1), "", ""

        return 0, "", ""

    def commands(self):
        return [" ".join(argv[:3]) for argv in self.ran]


class Body:
    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="devfactory-ship-test-")
        with open(os.path.join(self.dir, "pr.md"), "w", encoding="utf-8") as handle:
            handle.write("body\n")
        return self.dir

    def __exit__(self, *_):
        shutil.rmtree(self.dir, ignore_errors=True)


def shipper(root, fake, guard=None, judged=None):
    def allow_all(command, where):
        if judged is not None:
            judged.append(command)
        return True, ""

    return ship.Shipper(root, guard=guard or allow_all, run=fake, sleep=lambda _: None,
                        say=lambda _: None, checks_appear_seconds=10)


class TheFlow(unittest.TestCase):
    def test_a_green_branch_is_pushed_opened_watched_merged_and_left(self):
        fake = FakeGitHub()

        with Body() as root:
            number = shipper(root, fake).ship("title", "pr.md")

        self.assertEqual(number, 31)
        ran = fake.commands()
        self.assertIn("git push -u", ran)
        self.assertIn("gh pr create", ran)
        self.assertLess(ran.index("gh pr checks"), ran.index("gh pr merge"))
        self.assertEqual(ran[-2:], ["git switch dev", "git pull"])
        create = next(argv for argv in fake.ran if argv[:3] == ["gh", "pr", "create"])
        self.assertEqual(create[create.index("--base") + 1], "dev")

    def test_every_command_is_judged_immediately_before_it_runs(self):
        log = []
        fake = FakeGitHub(log=log)

        def judge(command, where):
            log.append(("judge", command))
            return True, ""

        with Body() as root:
            shipper(root, fake, guard=judge).ship("title", "pr.md")

        self.assertEqual(len(log), 2 * len(fake.ran))
        for judged, ran in zip(log[::2], log[1::2]):
            self.assertEqual((judged[0], ran[0]), ("judge", "run"))
            self.assertEqual(judged[1], ran[1])

    def test_the_merge_is_tied_to_the_pushed_commit(self):
        fake = FakeGitHub()

        with Body() as root:
            shipper(root, fake).ship("title", "pr.md")

        merge = next(argv for argv in fake.ran if argv[:3] == ["gh", "pr", "merge"])
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], HEAD)

    def test_an_earlier_commits_green_checks_do_not_count(self):
        """Right after a push the pull request may still show the last commit and its green run."""
        fake = FakeGitHub(heads=[OLD_HEAD, OLD_HEAD, HEAD], rollups=[GREEN_RUN])

        with Body() as root:
            shipper(root, fake).ship("title", "pr.md")

        views = [i for i, argv in enumerate(fake.ran) if argv[:3] == ["gh", "pr", "view"]]
        watch = next(i for i, argv in enumerate(fake.ran) if argv[:3] == ["gh", "pr", "checks"])
        self.assertGreaterEqual(len([i for i in views if i < watch]), 3)

    def test_a_push_during_the_watch_is_not_merged(self):
        """The pull request moved on while CI was watched: its checks are not this commit's."""
        fake = FakeGitHub(heads=[HEAD, OLD_HEAD])

        with Body() as root:
            with self.assertRaises(ship.ShipError) as caught:
                shipper(root, fake).ship("title", "pr.md")

        self.assertEqual(caught.exception.status, 1)
        self.assertNotIn("gh pr merge", fake.commands())

    def test_a_head_that_never_arrives_is_not_merged(self):
        fake = FakeGitHub(heads=[OLD_HEAD])

        with Body() as root:
            with self.assertRaises(ship.ShipError) as caught:
                shipper(root, fake).ship("title", "pr.md")

        self.assertEqual(caught.exception.status, 1)
        self.assertNotIn("gh pr merge", fake.commands())

    def test_an_unreadable_answer_from_gh_is_a_refusal_not_red_ci(self):
        fake = FakeGitHub(listed="<html>rate limited</html>")

        with Body() as root:
            with self.assertRaises(ship.ShipError) as caught:
                shipper(root, fake).ship("title", "pr.md")

        self.assertEqual(caught.exception.status, 2)
        self.assertNotIn("gh pr create", fake.commands())

    def test_a_refused_command_never_runs_and_stops_the_flow(self):
        fake = FakeGitHub()

        def refuse_merges(command, where):
            return ("gh pr merge" not in command), "Blocked by RULE-GIT: not green"

        with Body() as root:
            with self.assertRaises(ship.ShipError) as caught:
                shipper(root, fake, guard=refuse_merges).ship("title", "pr.md")

        self.assertEqual(caught.exception.status, 2)
        self.assertIn("Blocked by RULE-GIT", str(caught.exception))
        self.assertNotIn("gh pr merge", fake.commands())
        self.assertNotIn("git switch dev", fake.commands())

    def test_red_ci_is_never_merged(self):
        fake = FakeGitHub(rollups=[RED_RUN])

        with Body() as root:
            with self.assertRaises(ship.ShipError) as caught:
                shipper(root, fake).ship("title", "pr.md")

        self.assertEqual(caught.exception.status, 1)
        self.assertIn("build-and-test", str(caught.exception))
        self.assertNotIn("gh pr merge", fake.commands())

    def test_a_pending_check_is_not_green(self):
        fake = FakeGitHub(rollups=[[{"name": "build-and-test", "status": "IN_PROGRESS"}]])

        with Body() as root:
            with self.assertRaises(ship.ShipError):
                shipper(root, fake).ship("title", "pr.md")

        self.assertNotIn("gh pr merge", fake.commands())

    def test_no_merge_stops_once_green(self):
        fake = FakeGitHub()

        with Body() as root:
            shipper(root, fake).ship("title", "pr.md", merge=False)

        self.assertIn("gh pr checks", fake.commands())
        self.assertNotIn("gh pr merge", fake.commands())


class CIThatNeverStarts(unittest.TestCase):
    def test_a_silent_pull_request_is_reopened_once(self):
        fake = FakeGitHub(rollups=[[], [], [], [], GREEN_RUN])

        with Body() as root:
            shipper(root, fake).ship("title", "pr.md")

        ran = fake.commands()
        self.assertEqual((ran.count("gh pr close"), ran.count("gh pr reopen")), (1, 1))
        self.assertIn("gh pr merge", ran)

    def test_still_silent_after_the_reopen_stops_without_merging(self):
        fake = FakeGitHub(rollups=[[]])

        with Body() as root:
            with self.assertRaises(ship.ShipError) as caught:
                shipper(root, fake).ship("title", "pr.md")

        self.assertEqual(caught.exception.status, 1)
        self.assertEqual(fake.commands().count("gh pr reopen"), 1)
        self.assertNotIn("gh pr merge", fake.commands())


class Preconditions(unittest.TestCase):
    def test_uncommitted_changes_stop_before_the_push(self):
        fake = FakeGitHub(dirty=" M app.py\n")

        with Body() as root:
            with self.assertRaises(ship.ShipError):
                shipper(root, fake).ship("title", "pr.md")

        self.assertNotIn("git push -u", fake.commands())

    def test_the_integration_branch_is_not_shipped(self):
        fake = FakeGitHub(branch="dev")

        with Body() as root:
            with self.assertRaises(ship.ShipError):
                shipper(root, fake).ship("title", "pr.md")

        self.assertNotIn("git push -u", fake.commands())

    def test_a_missing_body_stops_before_the_push(self):
        fake = FakeGitHub()

        with Body() as root:
            with self.assertRaises(ship.ShipError):
                shipper(root, fake).ship("title", "absent.md")

        self.assertNotIn("git push -u", fake.commands())

    def test_an_open_pull_request_is_reused(self):
        fake = FakeGitHub(open_prs=[{"number": 12, "baseRefName": "dev"}])

        with Body() as root:
            number = shipper(root, fake).ship("title", "pr.md")

        self.assertEqual(number, 12)
        self.assertNotIn("gh pr create", fake.commands())

    def test_an_open_pull_request_into_another_base_is_refused(self):
        fake = FakeGitHub(open_prs=[{"number": 12, "baseRefName": "main"}])

        with Body() as root:
            with self.assertRaises(ship.ShipError):
                shipper(root, fake).ship("title", "pr.md")

        self.assertNotIn("gh pr merge", fake.commands())


class ExitStatus(unittest.TestCase):
    def status_when(self, error):
        real = ship.Shipper.ship

        def fail(self, *args, **kwargs):
            raise error

        ship.Shipper.ship = fail
        try:
            with Body() as root:
                return ship.main(["--root", root, "--title", "t", "--body-file", "pr.md"])
        finally:
            ship.Shipper.ship = real

    def test_red_ci_is_1_and_a_refusal_is_2(self):
        self.assertEqual(self.status_when(ship.ShipError("red", 1)), 1)
        self.assertEqual(self.status_when(ship.ShipError("refused")), 2)

    def test_an_unexpected_answer_is_2_not_red_ci(self):
        for error in (OSError("gone"), ValueError("bad json"), KeyError("number")):
            self.assertEqual(self.status_when(error), 2, error)


class NoShell(unittest.TestCase):
    def test_arguments_reach_the_command_verbatim(self):
        tricky = "a; echo pwned & $(whoami) `id` | x > y"
        echo = [sys.executable, "-c", "import json, sys; print(json.dumps(sys.argv[1:]))", tricky]

        with Body() as root:
            code, out, _ = ship.run_command(echo, root)

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), [tricky])


class TheRealGuardDecides(unittest.TestCase):
    """guard_verdict asks hooks/guard_bash.py itself, so the tool cannot be looser than it."""

    def setUp(self):
        # The repository is the anchor unless a test says otherwise.
        self.inherited = os.environ.pop("CLAUDE_PROJECT_DIR", None)
        self.repo = tempfile.mkdtemp(prefix="devfactory-ship-guard-")
        with open(os.path.join(self.repo, ".dev-factory.json"), "w", encoding="utf-8") as f:
            json.dump(CONFIG, f)
        git = ["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(git + ["init", "-q", "-b", BRANCH], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "start"], check=True)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)
        if self.inherited is not None:
            os.environ["CLAUDE_PROJECT_DIR"] = self.inherited

    def verdict(self, argv):
        return ship.guard_verdict(ship.shlex.join(argv), self.repo)

    def test_a_pull_request_into_the_integration_branch_is_allowed(self):
        allowed, _ = self.verdict(["gh", "pr", "create", "--base", "dev", "--head", BRANCH,
                                   "--title", "t", "--body-file", "pr.md"])
        self.assertTrue(allowed)

    def test_a_pull_request_into_the_mainline_is_refused(self):
        allowed, reason = self.verdict(["gh", "pr", "create", "--base", "main", "--head", BRANCH,
                                        "--title", "t", "--body-file", "pr.md"])
        self.assertFalse(allowed)
        self.assertIn("RULE-GIT", reason)

    def test_pushing_the_work_branch_is_allowed_and_a_protected_one_is_not(self):
        self.assertTrue(self.verdict(["git", "push", "-u", "origin", BRANCH])[0])
        self.assertFalse(self.verdict(["git", "push", "-u", "origin", "dev"])[0])
        self.assertFalse(self.verdict(["git", "push", "-u", "origin", "main"])[0])

    def test_a_title_cannot_smuggle_in_a_second_command(self):
        """Arguments are quoted for the guard and passed to git and gh without a shell, so the
        guard judges exactly what runs: one pull request, not a push to the mainline."""
        title = "fix; git push origin main"
        allowed, _ = self.verdict(["gh", "pr", "create", "--base", "dev", "--head", BRANCH,
                                   "--title", title, "--body-file", "pr.md"])
        self.assertTrue(allowed)
        self.assertFalse(self.verdict(["git", "push", "origin", "main"])[0])

    def test_the_session_anchors_rules_are_applied(self):
        anchor = tempfile.mkdtemp(prefix="devfactory-ship-anchor-")
        strict = dict(CONFIG, blockedCommands=[
            {"pattern": "gh pr (create|checks)", "reason": "the anchor forbids it"}])
        with open(os.path.join(anchor, ".dev-factory.json"), "w", encoding="utf-8") as f:
            json.dump(strict, f)
        command = ship.shlex.join(["gh", "pr", "checks", "5", "--watch"])
        try:
            self.assertTrue(ship.guard_verdict(command, self.repo)[0])
            allowed, reason = ship.guard_verdict(command, self.repo, anchor)
        finally:
            shutil.rmtree(anchor, ignore_errors=True)
        self.assertFalse(allowed)
        self.assertIn("the anchor forbids it", reason)

    def test_an_inherited_anchor_is_kept(self):
        self.assertEqual(ship.session_anchor("/repo", "/given"), "/given")
        real = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = "/session"
        try:
            self.assertEqual(ship.session_anchor("/repo"), "/session")
        finally:
            if real is None:
                del os.environ["CLAUDE_PROJECT_DIR"]
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = real
        os.environ.pop("CLAUDE_PROJECT_DIR", None)
        try:
            self.assertEqual(ship.session_anchor("/repo"), "/repo")
        finally:
            if real is not None:
                os.environ["CLAUDE_PROJECT_DIR"] = real

    def test_without_the_guard_nothing_is_allowed(self):
        real = ship.GUARD
        ship.GUARD = os.path.join(self.repo, "absent_guard.py")
        try:
            allowed, reason = self.verdict(["git", "status"])
        finally:
            ship.GUARD = real
        self.assertFalse(allowed)
        self.assertIn("missing", reason)

    def test_a_guard_answering_neither_allow_nor_block_refuses(self):
        odd = os.path.join(self.repo, "odd_guard.py")
        with open(odd, "w", encoding="utf-8") as handle:
            handle.write("import sys\nsys.exit(1)\n")
        real = ship.GUARD
        ship.GUARD = odd
        try:
            allowed, reason = self.verdict(["git", "status"])
        finally:
            ship.GUARD = real
        self.assertFalse(allowed)
        self.assertIn("neither allow nor block", reason)


if __name__ == "__main__":
    unittest.main()
