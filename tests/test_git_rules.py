#!/usr/bin/env python3
"""Tests for RULE-GIT: work branches and pull requests are the agent's, the mainline is not.

A real git repository in each test, because what the guard asks git - "which branch is checked
out" - cannot be faked without testing the fake. `gh` is replaced by a stand-in script that
answers `gh pr view` with whatever pull request the test describes.
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

FAKE_GH = """\
import json, os, sys
state = os.environ.get("FAKE_PR")
if state is None:
    sys.exit(1)
print(state)
"""


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def guard(command, root, pull_request=None, cwd=None, anchor=None):
    """Run the guard as the harness does. `anchor` is the session's project directory and
    `cwd` the shell's, both defaulting to the test repository."""
    env = {**os.environ, "CLAUDE_PROJECT_DIR": anchor or root,
           "DEV_FACTORY_GH": os.path.join(root, "fake_gh.py")}
    env.pop("FAKE_PR", None)

    if pull_request is not None:
        env["FAKE_PR"] = json.dumps(pull_request)

    payload = {"tool_name": "Bash", "tool_input": {"command": command}}

    if cwd is not None:
        payload["cwd"] = cwd

    proc = subprocess.run(
        [sys.executable, os.path.join(HOOKS, "guard_bash.py")],
        input=json.dumps(payload), capture_output=True, text=True, cwd=root, env=env,
    )

    return proc.returncode, proc.stderr


def pr(base="dev", checks=(("ci", "SUCCESS"),)):
    return {"baseRefName": base, "state": "OPEN",
            "statusCheckRollup": [{"name": n, "conclusion": c} for n, c in checks]}


class Repo:
    """A repository with one commit on main and a dev branch, checked out on the named branch."""

    def __init__(self, branch="feature/3-orders", init=True):
        self.branch = branch
        self.init = init

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="gitrules-")

        with open(os.path.join(self.dir, ".dev-factory.json"), "w", encoding="utf-8") as f:
            json.dump(CONFIG, f)

        with open(os.path.join(self.dir, "fake_gh.py"), "w", encoding="utf-8") as f:
            f.write(FAKE_GH)

        if not self.init:
            return self.dir

        git(self.dir, "init", "-q", "-b", "main")
        git(self.dir, "config", "user.email", "t@example.com")
        git(self.dir, "config", "user.name", "t")

        with open(os.path.join(self.dir, "a.txt"), "w", encoding="utf-8") as f:
            f.write("x\n")

        git(self.dir, "add", "a.txt")
        git(self.dir, "commit", "-q", "-m", "first")
        git(self.dir, "branch", "dev")

        if self.branch != "main":
            git(self.dir, "checkout", "-q", "-B", self.branch)

        return self.dir

    def __exit__(self, *_):
        shutil.rmtree(self.dir, ignore_errors=True)


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class WorkBranchesAreTheAgents(unittest.TestCase):
    def test_committing_and_pushing_a_work_branch_is_allowed(self):
        for branch in ("feature/3-orders", "fix/rate-cache", "chore/ci", "docs/readme",
                       "refactor/x", "test/y", "ci/z"):
            with Repo(branch) as root:
                for command in ('git commit -m "feat: x"', "git push",
                                f"git push -u origin {branch}", f"git push origin HEAD:{branch}"):
                    code, stderr = guard(command, root)
                    self.assertEqual(ALLOW, code, f"{branch}: {command}: {stderr}")

    def test_creating_branches_and_moving_around_is_allowed(self):
        with Repo("dev") as root:
            for command in ("git switch dev", "git pull", "git fetch origin",
                            "git switch -c feature/4-auth", "git checkout -b fix/x",
                            "git branch scratch"):
                self.assertEqual(ALLOW, guard(command, root)[0], command)

    def test_bringing_dev_into_a_work_branch_is_allowed(self):
        with Repo("feature/3-orders") as root:
            self.assertEqual(ALLOW, guard("git merge origin/dev", root)[0])

    def test_deleting_an_own_work_branch_is_allowed(self):
        with Repo("dev") as root:
            self.assertEqual(ALLOW, guard("git branch -d feature/3-orders", root)[0])

    def test_clone_and_init_are_allowed(self):
        with Repo("dev") as root:
            for command in ("git clone https://github.com/o/r.git", "git init"):
                self.assertEqual(ALLOW, guard(command, root)[0], command)


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class ProtectedBranches(unittest.TestCase):
    def test_committing_on_a_protected_branch_is_blocked(self):
        for branch in ("main", "master", "dev"):
            with Repo(branch) as root:
                code, stderr = guard('git commit -m "x"', root)

                self.assertEqual(BLOCK, code, branch)
                self.assertIn(branch, stderr)

    def test_merging_into_a_protected_branch_locally_is_blocked(self):
        with Repo("dev") as root:
            code, stderr = guard("git merge feature/3-orders", root)

        self.assertEqual(BLOCK, code)
        self.assertIn("pull request", stderr)

    def test_pushing_a_protected_branch_is_blocked_however_it_is_named(self):
        with Repo("feature/3-orders") as root:
            for command in ("git push origin main", "git push origin master",
                            "git push origin dev", "git push origin HEAD:dev",
                            "git push origin feature/3-orders:main"):
                code, stderr = guard(command, root)

                self.assertEqual(BLOCK, code, command)
                self.assertIn("not a work branch", stderr)

    def test_pushing_from_a_protected_branch_is_blocked(self):
        for branch in ("main", "dev"):
            with Repo(branch) as root:
                self.assertEqual(BLOCK, guard("git push", root)[0], branch)

    def test_a_branch_outside_the_naming_scheme_is_not_pushed(self):
        with Repo("feature/3-orders") as root:
            for command in ("git push origin experiment", "git push origin phase/3",
                            "git push origin feature/"):
                self.assertEqual(BLOCK, guard(command, root)[0], command)

    def test_deleting_or_renaming_a_protected_branch_is_blocked(self):
        with Repo("feature/3-orders") as root:
            for command in ("git branch -D dev", "git branch -d main", "git branch -m dev x"):
                self.assertEqual(BLOCK, guard(command, root)[0], command)

    def test_a_quoted_protected_branch_is_still_a_protected_branch(self):
        # Quoted arguments are masked when they look like data. A plain word is not masked, so
        # quoting the destination cannot launder a push to the mainline.
        with Repo("feature/3-orders") as root:
            self.assertEqual(BLOCK, guard('git push origin "main"', root)[0])
            self.assertEqual(BLOCK, guard("git push origin 'dev'", root)[0])
            # ...and the same unquoting is what lets a quoted work branch through. Without this
            # line the two above pass even when quoted words are masked, for the wrong reason.
            self.assertEqual(ALLOW, guard('git push origin "feature/3-orders"', root)[0])


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class WhatIsNeverAllowed(unittest.TestCase):
    def test_force_is_blocked_even_on_a_work_branch(self):
        with Repo("feature/3-orders") as root:
            for command in ("git push --force origin feature/3-orders",
                            "git push -f", "git push --force-with-lease",
                            "git push origin +feature/3-orders"):
                self.assertEqual(BLOCK, guard(command, root)[0], command)

    def test_bulk_publishing_and_remote_deletion_are_blocked(self):
        with Repo("feature/3-orders") as root:
            for command in ("git push --all", "git push --mirror", "git push --tags",
                            "git push origin --delete feature/2-x"):
                self.assertEqual(BLOCK, guard(command, root)[0], command)

    def test_history_remotes_tags_and_config_are_the_owners(self):
        with Repo("feature/3-orders") as root:
            for command in ("git remote add origin x", "git remote set-url origin y",
                            "git rebase dev", "git reset --hard HEAD~1",
                            'git commit --amend -m "x"', "git commit --no-verify -m x",
                            "git tag v1.0.0", "git config user.name x",
                            "git filter-branch", "git update-ref refs/heads/dev HEAD"):
                self.assertEqual(BLOCK, guard(command, root)[0], command)

    def test_reading_remotes_tags_and_config_is_allowed(self):
        with Repo("feature/3-orders") as root:
            for command in ("git remote -v", "git remote get-url origin", "git tag",
                            "git tag -l", "git config --get user.name",
                            "git check-ignore -v .env", "git merge-base dev HEAD"):
                self.assertEqual(ALLOW, guard(command, root)[0], command)

    def test_a_push_whose_destination_cannot_be_determined_is_blocked(self):
        with Repo(init=False) as root:
            code, stderr = guard("git push", root)

        self.assertEqual(BLOCK, code)
        self.assertIn("cannot tell which branch", stderr)

    def test_a_command_hidden_in_a_shell_string_is_still_read(self):
        with Repo("feature/3-orders") as root:
            self.assertEqual(BLOCK, guard('bash -c "git push origin main"', root)[0])
            self.assertEqual(BLOCK, guard("cd x && git push origin dev", root)[0])
            self.assertEqual(BLOCK, guard("bash <<'EOF'\ngit push origin main\nEOF", root)[0])


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class BypassesFoundInReview(unittest.TestCase):
    """Every command here got exit 0 from the first version of the new guard. Each is a review
    finding of 2026-10-06; none may come back."""

    def assertBlocked(self, root, *command_list, pull_request=None):
        for command in command_list:
            code, stderr = guard(command, root, pull_request)
            self.assertEqual(BLOCK, code, f"{command}: {stderr}")

    def test_every_refspec_of_a_push_is_judged(self):
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "git push origin dev feature/1-x",
                               "git push origin :dev feature/1-x",
                               "git push origin feature/1-x:main feature/1-x",
                               "git push origin feature/1-x:refs/heads/main")

    def test_redirections_are_not_refspecs(self):
        # Found in the first live use: `git push -u origin <work branch> 2>&1` was blocked for
        # pushing to a branch called "2>". Redirections are dropped wherever they stand - and a
        # protected destination stays protected however the output is redirected.
        with Repo("feature/1-x") as root:
            for command in ("git push -u origin feature/1-x 2>&1",
                            "git push origin feature/1-x > push.log",
                            "git push origin feature/1-x &>/dev/null",
                            "git push origin feature/1-x 2> err.txt | tail -3",
                            "git push origin feature/1-x >push.log 2>err.txt",
                            "git push -u origin feature/1-x 2>&1; echo done",
                            "git push -u origin feature/1-x 2>&1| tail -3"):
                code, stderr = guard(command, root)
                self.assertEqual(ALLOW, code, f"{command}: {stderr}")

            self.assertBlocked(root, "git push origin dev 2>&1",
                               "git push > push.log origin main",
                               "git push origin main &")

    def test_arguments_after_a_redirection_are_still_read(self):
        # Review of the first fix: splitting on the `&` of `2>&1` cut the command, and dropping
        # the leftover `2>` made everything after it invisible.
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "git push origin feature/1-x 2>&1 main",
                               "git push origin feature/1-x 2>&1 dev",
                               "git push origin feature/1-x >&2 main",
                               "git push -u origin 2>&1 feature/1-x:main",
                               "git push origin 2>&1 --force feature/1-x",
                               "git push origin feature/1-x &> push.log main")

    def test_a_substitution_inside_a_command_hides_nothing(self):
        # The words after a substitution belong to the command it interrupted; the guard cannot
        # see what the substitution yields, so a write is refused.
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "git push origin feature/1-x > $(echo x) main",
                               "git push origin > `echo x` main",
                               "git push origin >(cat) main", "git push origin <(cat) main",
                               "git push origin $(echo x) main")

            for command in ("git log --format=%H $(git merge-base dev HEAD)",
                            "echo $(git rev-parse HEAD)"):
                code, stderr = guard(command, root)
                self.assertEqual(ALLOW, code, f"{command}: {stderr}")

    def test_a_deletion_refspec_is_blocked_even_for_a_work_branch(self):
        # `:branch` deletes on the remote, like --delete. Work branches are deleted by
        # `gh pr merge --delete-branch`, after review, not by a bare push.
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "git push origin :feature/2-old")

    def test_a_fully_qualified_work_branch_is_still_a_work_branch(self):
        with Repo("feature/1-x") as root:
            code, stderr = guard("git push origin HEAD:refs/heads/feature/1-x", root)

        self.assertEqual(ALLOW, code, stderr)

    def test_a_variable_in_git_arguments_is_refused(self):
        # $FLAGS could hold -n (skip hooks); the guard cannot see it, so it refuses.
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "git commit $FLAGS -m x")
            # An ANSI-C quoted flag is unquoted to the flag itself; were it not, the `$` left
            # behind would still trip the variable check. Two guards, proven as a pair.
            self.assertBlocked(root, "git commit $'-n' -m x")

    def test_flag_spellings_do_not_launder_force_or_hook_bypass(self):
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "git push --force-with-lease=feature/1-x",
                               "git push -uf origin feature/1-x",
                               "git push --forc origin feature/1-x",
                               "git push $'-f' origin feature/1-x",
                               "F=-f; git push $F origin feature/1-x",
                               "git commit -nm x", "git commit --no-verif -m x",
                               "git commit --amen -m x",
                               "git -c core.hooksPath=/dev/null commit -m x",
                               "git pull --rebase", "git pull -r")

    def test_a_branch_switch_earlier_on_the_line_counts(self):
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "git switch dev && git merge feature/1-x && git push origin",
                               "git checkout dev; git commit --allow-empty -m x",
                               "git switch main && git push")

    def test_a_cd_earlier_on_the_line_counts(self):
        with Repo("feature/1-x") as root:
            other = tempfile.mkdtemp(prefix="gitrules-other-")
            try:
                git(other, "init", "-q", "-b", "dev")
                git(other, "config", "user.email", "t@example.com")
                git(other, "config", "user.name", "t")
                git(other, "commit", "-q", "--allow-empty", "-m", "first")
                self.assertBlocked(root, f"cd {other} && git push",
                                   f"cd {other}; git commit --allow-empty -m x",
                                   'cd "a path with spaces" && git commit -m x')
            finally:
                shutil.rmtree(other, ignore_errors=True)

    def test_popd_is_not_ignored(self):
        # `popd` returns to the directory before `pushd`. Read as an ordinary command, the
        # guard would judge the commit in the work-branch repository it had pushed into.
        with Repo("dev") as root, Repo("feature/1-x") as other:
            self.assertBlocked(root, f"pushd {other} && popd && git commit -m x")

    def test_wrappers_and_shell_syntax_are_looked_through(self):
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "env git push -f origin dev", "eval git push -f origin dev",
                               "timeout 60 git push -f origin dev",
                               "echo dev | xargs git push origin",
                               "{ git push -f origin dev; }",
                               "if true; then git push -f origin dev; fi",
                               "for x in a; do git push -f origin dev; done",
                               "! git push -f origin dev", ">/dev/null git push -f origin dev",
                               "echo `git push -f origin dev`",
                               'echo "$(git push -f origin dev)"',
                               "gi\\t push -f origin dev",
                               "cat <<EOF | bash\ngit push -f origin dev\nEOF",
                               "env gh pr merge 5 --admin")

    def test_gh_option_spellings_do_not_reach_writes_or_the_mainline(self):
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "gh api -XPOST repos/o/r/pulls/5/merge",
                               "gh api graphql --raw-field=query=mutation{x}",
                               "gh api repos/o/r --field=private=false",
                               "gh api repos/o/r -fname=x", "gh api repos/o/r --input=body.json",
                               "gh pr create --base dev --base main",
                               "gh pr create -B dev -Bmain", "gh pr edit 5 -Bmain")

    def test_an_unexpected_pull_request_answer_fails_closed(self):
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "gh pr merge 5", pull_request=[1, 2])
            self.assertBlocked(root, "gh pr merge 5",
                               pull_request={"baseRefName": "dev", "statusCheckRollup": ["x"]})

    def test_a_pull_request_body_from_a_heredoc_keeps_its_base(self):
        # The way a long body is usually written. The command substitution inside the quotes is
        # read as a command of its own, without cutting --base off the gh command around it.
        command = ('gh pr create --title "feat: x" --body "$(cat <<\'EOF\'\n'
                   "Mentions git push origin main.\nEOF\n)\" --base dev")

        with Repo("feature/1-x") as root:
            code, stderr = guard(command, root)

        self.assertEqual(ALLOW, code, stderr)

    def test_head_names_the_checked_out_branch(self):
        with Repo("dev") as root:
            self.assertBlocked(root, "git push origin HEAD")

        with Repo("feature/1-x") as root:
            self.assertEqual(ALLOW, guard("git push origin HEAD", root)[0])

    def test_environment_assignments_do_not_hide_the_command(self):
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "GIT_TRACE=1 git push origin main")

    def test_eval_with_a_quoted_string_is_read(self):
        with Repo("feature/1-x") as root:
            self.assertBlocked(root, "eval 'git push origin main'")

    def test_reading_config_and_push_options_are_not_mistaken_for_writes(self):
        with Repo("feature/1-x") as root:
            for command in ("git config user.name", "git push origin feature/1-x -o ci.skip",
                            "gh api repos/o/r --include", "git pull --no-rebase"):
                code, stderr = guard(command, root)
                self.assertEqual(ALLOW, code, f"{command}: {stderr}")


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class TheIntegrationBranchSetting(unittest.TestCase):
    def write_config(self, root, value):
        with open(os.path.join(root, ".dev-factory.json"), "w", encoding="utf-8") as f:
            json.dump(dict(CONFIG, integrationBranch=value), f)

    def test_a_custom_integration_branch_is_honoured(self):
        with Repo("develop") as root:
            self.write_config(root, "develop")
            self.assertEqual(BLOCK, guard('git commit -m "x"', root)[0])
            self.assertEqual(ALLOW, guard("gh pr create --base develop --fill", root)[0])
            self.assertEqual(BLOCK, guard("gh pr create --base dev --fill", root)[0])

    def test_the_integration_branch_survives_a_detour(self):
        # Review: with the config taken from the first `cd` or from an unrelated shell
        # directory, `develop` stopped being protected.
        with Repo("develop") as root:
            self.write_config(root, "develop")
            empty = tempfile.mkdtemp(prefix="gitrules-empty-")
            try:
                # The session is anchored elsewhere too, so only the repository the command
                # acts in can supply `develop`.
                # `-C` after another git option that takes a value: a second parser of git's
                # options once stopped at `user.name=a` and never saw the `-C`.
                for command in (f"cd {empty} && cd {root} && git commit -m x",
                                f"git -C {root} commit -m x",
                                f"git -c user.name=a -C {root} commit -m x",
                                f"git --work-tree {root} -C {root} commit -m x"):
                    code, stderr = guard(command, root, cwd=empty, anchor=empty)
                    self.assertEqual(BLOCK, code, f"{command}: {stderr}")

                code, _ = guard(f"cd {empty} && cd {root} && gh pr create --base dev --fill",
                                root, cwd=empty, anchor=empty)
                self.assertEqual(BLOCK, code)

                # --repo names a repository whose config the guard has not read.
                code, stderr = guard("gh pr create --repo o/r --base develop --fill", root)
                self.assertEqual(BLOCK, code)
                self.assertIn("--repo", stderr)

                # A directory the guard cannot follow has no known integration branch.
                code, stderr = guard("cd $HOME && gh pr create --base dev --fill", root)
                self.assertEqual(BLOCK, code)
                self.assertIn("cannot tell which integration branch", stderr)
            finally:
                shutil.rmtree(empty, ignore_errors=True)

    def test_a_nested_config_cannot_unprotect_the_integration_branch(self):
        with Repo("develop") as root:
            self.write_config(root, "develop")
            sub = os.path.join(root, "fixtures")
            os.makedirs(sub)

            with open(os.path.join(sub, ".dev-factory.json"), "w", encoding="utf-8") as f:
                json.dump(CONFIG, f)

            empty = tempfile.mkdtemp(prefix="gitrules-empty-")
            try:
                code, stderr = guard('git commit -m "x"', root, cwd=sub, anchor=empty)
            finally:
                shutil.rmtree(empty, ignore_errors=True)

        self.assertEqual(BLOCK, code, stderr)

    def test_the_mainline_or_a_malformed_value_is_refused(self):
        for value in ("main", "master", ["dev"], "feature/1-x"):
            with Repo("feature/1-x") as root:
                self.write_config(root, value)
                code, stderr = guard("gh pr create --base main --fill", root)

                self.assertEqual(BLOCK, code, value)
                self.assertIn("integrationBranch", stderr)


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class TextIsNotACommand(unittest.TestCase):
    """Each of these was blocked by the first guard, which matched words anywhere."""

    def test_a_note_written_through_a_heredoc_may_mention_anything(self):
        with Repo("feature/3-orders") as root:
            command = "cat > notes.md <<'EOF'\ngit clone x\ngh repo delete y\nEOF"
            self.assertEqual(ALLOW, guard(command, root)[0])

    def test_a_search_pattern_may_mention_anything(self):
        with Repo("feature/3-orders") as root:
            # Unmasked, the alternation splits into `gh secret set` and `git push origin main`.
            command = "grep -nE 'gh\\b|gh secret set|git push origin main' hooks/x.py"
            self.assertEqual(ALLOW, guard(command, root)[0])

    def test_a_path_or_argument_named_git_is_not_git(self):
        # Only the first word of a command is the command.
        with Repo("feature/3-orders") as root:
            command = "find . -path ./.git -prune -o -name git -print"
            self.assertEqual(ALLOW, guard(command, root)[0])

    def test_a_commit_message_may_mention_anything(self):
        with Repo("feature/3-orders") as root:
            command = 'git commit -m "docs: explain why git push origin main is blocked"'
            self.assertEqual(ALLOW, guard(command, root)[0])


@unittest.skipIf(shutil.which("git") is None, "git is not available")
class TheGitHubCli(unittest.TestCase):
    def test_pull_requests_into_dev_issues_and_runs_are_allowed(self):
        with Repo("feature/3-orders") as root:
            for command in ('gh pr create --base dev --title "feat: x" --body y',
                            "gh pr create -B dev --fill", "gh pr view 3", "gh pr checks 3",
                            "gh pr list", "gh issue create --title x --body y",
                            "gh run view 1 --log-failed", "gh auth status",
                            "gh repo create o/r --private", "gh api repos/o/r/pulls"):
                code, stderr = guard(command, root)
                self.assertEqual(ALLOW, code, f"{command}: {stderr}")

    def test_a_pull_request_must_name_dev_as_its_base(self):
        # Without --base, GitHub targets the default branch - the mainline.
        with Repo("feature/3-orders") as root:
            for command in ("gh pr create --fill", "gh pr create --base main --fill",
                            "gh pr create --base master --fill", "gh pr edit 3 --base main"):
                code, stderr = guard(command, root)
                self.assertEqual(BLOCK, code, command)

    def test_settings_secrets_releases_and_credentials_are_the_owners(self):
        with Repo("feature/3-orders") as root:
            for command in ("gh repo delete o/r --yes", "gh repo edit --visibility public",
                            "gh secret set X", "gh release create v1", "gh auth token",
                            "gh auth logout", "gh ruleset list", "gh repo create o/r --public",
                            "gh repo create o/r", "gh api -X PUT repos/o/r/branches/dev/protection",
                            "gh api repos/o/r/labels -f name=x"):
                self.assertEqual(BLOCK, guard(command, root)[0], command)

    def test_merging_into_dev_with_green_checks_is_allowed(self):
        with Repo("feature/3-orders") as root:
            code, stderr = guard("gh pr merge 3 --squash --delete-branch", root, pr())

        self.assertEqual(ALLOW, code, stderr)

    def test_merging_into_the_mainline_is_blocked(self):
        with Repo("feature/3-orders") as root:
            code, stderr = guard("gh pr merge 3 --squash", root, pr(base="main"))

        self.assertEqual(BLOCK, code)
        self.assertIn("main", stderr)

    def test_merging_with_red_or_missing_checks_is_blocked(self):
        with Repo("feature/3-orders") as root:
            red = guard("gh pr merge 3", root, pr(checks=(("ci", "SUCCESS"), ("lint", "FAILURE"))))
            pending = guard("gh pr merge 3", root, pr(checks=(("ci", None),)))
            none = guard("gh pr merge 3", root, pr(checks=()))

        self.assertEqual(BLOCK, red[0])
        self.assertIn("lint", red[1])
        self.assertEqual(BLOCK, pending[0])
        self.assertEqual(BLOCK, none[0])
        self.assertIn("no checks", none[1])

    def test_a_merge_whose_pull_request_cannot_be_read_is_blocked(self):
        with Repo("feature/3-orders") as root:
            code, stderr = guard("gh pr merge 3", root, None)

        self.assertEqual(BLOCK, code)
        self.assertIn("could not read", stderr)

    def test_admin_merge_is_blocked(self):
        with Repo("feature/3-orders") as root:
            self.assertEqual(BLOCK, guard("gh pr merge 3 --admin", root, pr())[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
