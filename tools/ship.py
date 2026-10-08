#!/usr/bin/env python3
"""Ship a reviewed, committed work branch: push, pull request, CI, merge, back to the
integration branch - the build-loop's "Record" steps 3 to 6 (skills/build-loop).

    python3 tools/ship.py --root <repo> --title <title> --body-file <path>
        [--base dev] [--anchor <dir>] [--no-merge] [--checks-appear-seconds 90]
        [--watch-timeout 1800]

The commit itself stays a plain `git commit`: that is where the review gate checks the stamp,
and a tool that committed for you would step around it.

**The guard still decides.** A tool runs its commands as subprocesses, which the PreToolUse
guard never sees - it sees only `python3 tools/ship.py`. So before running anything, this tool
shows each command to the plugin's own `hooks/guard_bash.py`, the way the harness does (the
command on stdin, `--root` as the working directory), and runs it only when the guard allows it.
A blocked command stops the run with the guard's reason; a guard that is missing or answers
anything but allow or block stops it too. The rules - work branch only, `--base` the
integration branch, merge only into it and only with every check green - therefore live in one
place.

One input the tool cannot see for itself: the session's anchor directory, whose
`.dev-factory.json` the guard adds to the repository's ("strictest rules win"). It is taken from
`--anchor`, else from `CLAUDE_PROJECT_DIR` when the environment carries it, else it is `--root`.
When the session is anchored elsewhere, pass `--anchor` so the guard judges with the same rules.
Commands run as argument lists, never through a shell, so the guard judges what runs.

Steps:
1. The working tree is clean and the branch is not the integration branch.
2. `git push -u origin <branch>`.
3. The branch's open pull request, or `gh pr create --base <base> --head <branch>`.
4. Wait until the pull request's head is the commit just pushed and CI reports on it. GitHub
   sometimes never starts the run for a new pull request; then the pull request is closed and
   reopened once, which starts it.
5. `gh pr checks <n> --watch`, then every check of that commit must be green. Red: stop and say
   how to read the failure. Nothing is merged.
6. Unless `--no-merge`: `gh pr merge <n> --squash --delete-branch --match-head-commit <sha>` -
   GitHub refuses it if anything else was pushed meanwhile - then switch to the base branch and
   pull.

Run it again after pushing a fix: the open pull request is reused. Exit status 0 when every
step ran, 1 when CI was red or never reported, 2 when a step was refused or failed, or an answer
from git or gh could not be read.

Built from the sequence the first projects ran by hand for every pull request.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(PLUGIN_ROOT, "hooks", "guard_bash.py")

GUARD_ALLOWS, GUARD_BLOCKS = 0, 2
DEFAULT_BASE = "dev"
CHECKS_APPEAR_SECONDS = 90
CHECKS_POLL_SECONDS = 5
WATCH_TIMEOUT_SECONDS = 1800
COMMAND_TIMEOUT_SECONDS = 120
#: Check conclusions that count as passed - the guard's own list.
GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}
PULL_URL = re.compile(r"/pull/(\d+)\s*$")


class ShipError(Exception):
    """A step was refused or failed; the message says which and why."""

    def __init__(self, message: str, status: int = 2) -> None:
        super().__init__(message)
        self.status = status


def session_anchor(root: str, given=None) -> str:
    """The directory the guard treats as the session's anchor (see the module docstring)."""
    return given or os.environ.get("CLAUDE_PROJECT_DIR") or root


def guard_verdict(command: str, root: str, anchor=None):
    """(allowed, reason): the plugin's guard judging `command` as if Claude ran it in `root`,
    in a session anchored at `anchor`."""
    if not os.path.isfile(GUARD):
        return False, f"the guard is missing ({GUARD}); nothing runs without it"

    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": root}

    try:
        proc = subprocess.run(
            [sys.executable, GUARD], input=json.dumps(payload), capture_output=True,
            encoding="utf-8", errors="replace", cwd=root, timeout=COMMAND_TIMEOUT_SECONDS,
            env={**os.environ, "CLAUDE_PROJECT_DIR": session_anchor(root, anchor)},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, f"the guard could not run: {error}"

    if proc.returncode == GUARD_ALLOWS:
        return True, ""

    if proc.returncode == GUARD_BLOCKS:
        return False, proc.stderr.strip() or "blocked"

    return False, f"the guard answered {proc.returncode}, which is neither allow nor block"


def run_command(argv: list, root: str, timeout: float = COMMAND_TIMEOUT_SECONDS):
    """(exit code, stdout, stderr)."""
    try:
        proc = subprocess.run(argv, cwd=root, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "", f"timed out after {timeout} s"
    except OSError as error:
        return None, "", str(error)

    return proc.returncode, proc.stdout, proc.stderr


def parse_json(text: str, what: str):
    try:
        return json.loads(text or "null")
    except ValueError:
        raise ShipError(f"could not read {what}: {text.strip()[:300]}") from None


class Shipper:
    def __init__(self, root, *, guard=guard_verdict, run=run_command, sleep=time.sleep,
                 say=print, checks_appear_seconds=CHECKS_APPEAR_SECONDS,
                 watch_timeout=WATCH_TIMEOUT_SECONDS):
        self.root = root
        self.guard, self.run, self.sleep, self.say = guard, run, sleep, say
        self.checks_appear_seconds = checks_appear_seconds
        self.watch_timeout = watch_timeout

    # --- one guarded command ------------------------------------------------------------------

    def step(self, argv, *, timeout=COMMAND_TIMEOUT_SECONDS, allow_failure=False):
        command = shlex.join(argv)
        allowed, reason = self.guard(command, self.root)

        if not allowed:
            raise ShipError(f"the guard refused `{command}`:\n{reason}")

        self.say(f"$ {command}")
        code, out, err = self.run(argv, self.root, timeout)

        if code != 0 and not allow_failure:
            raise ShipError(f"`{command}` failed ({code}): {(err or out).strip()[-800:]}")

        return code, out

    def read(self, argv):
        """A read-only command (still shown to the guard): its stdout, or ShipError."""
        return self.step(argv)[1]

    # --- the steps ------------------------------------------------------------------------------

    def ship(self, title, body_file, base=DEFAULT_BASE, merge=True):
        branch = self.read(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()

        if branch in {base, "HEAD"}:
            raise ShipError(f"on '{branch}': ship a work branch, not the integration branch")

        if self.read(["git", "status", "--porcelain"]).strip():
            raise ShipError("the working tree has uncommitted changes; review and commit first")

        if not os.path.isfile(os.path.join(self.root, body_file)):
            raise ShipError(f"no pull request body at {body_file}")

        self.step(["git", "push", "-u", "origin", branch])
        head = self.read(["git", "rev-parse", "HEAD"]).strip()
        number = self.open_pull_request(branch, base, title, body_file)
        self.wait_for_checks(number, head)
        self.watch_checks(number, head)

        if not merge:
            self.say(f"pull request #{number} is green; not merged (--no-merge)")
            return number

        self.step(["gh", "pr", "merge", str(number), "--squash", "--delete-branch",
                   "--match-head-commit", head])
        self.step(["git", "switch", base])
        self.step(["git", "pull"])
        self.say(f"pull request #{number} merged into {base}")
        return number

    def open_pull_request(self, branch, base, title, body_file):
        listed = self.read(["gh", "pr", "list", "--head", branch, "--state", "open",
                            "--json", "number,baseRefName"])
        existing = parse_json(listed, "the open pull requests") or []

        if not isinstance(existing, list) or not all(isinstance(e, dict) for e in existing):
            raise ShipError(f"could not read the open pull requests: {listed.strip()[:300]}")

        if existing:
            number, existing_base = existing[0].get("number"), existing[0].get("baseRefName")

            if not isinstance(number, int):
                raise ShipError(f"could not read the open pull requests: {listed.strip()[:300]}")

            if existing_base != base:
                raise ShipError(f"pull request #{number} targets '{existing_base}', not '{base}'")

            self.say(f"reusing open pull request #{number}")
            return number

        out = self.read(["gh", "pr", "create", "--base", base, "--head", branch,
                         "--title", title, "--body-file", body_file])
        found = PULL_URL.search(out.strip())

        if not found:
            raise ShipError(f"could not read the pull request number from: {out.strip()}")

        return int(found.group(1))

    def checks(self, number, head):
        """The pull request's checks, or [] while its head is not yet `head`."""
        out = self.read(["gh", "pr", "view", str(number), "--json", "headRefOid,statusCheckRollup"])
        state = parse_json(out, f"pull request #{number}")

        if not isinstance(state, dict):
            raise ShipError(f"could not read pull request #{number}: {out.strip()[:300]}")

        if state.get("headRefOid") != head:
            return []

        rollup = state.get("statusCheckRollup") or []

        if not isinstance(rollup, list) or not all(isinstance(c, dict) for c in rollup):
            raise ShipError(f"could not read the checks of pull request #{number}")

        return rollup

    def wait_for_checks(self, number, head):
        for reopened in (False, True):
            waited = 0

            while waited <= self.checks_appear_seconds:
                if self.checks(number, head):
                    return

                self.sleep(CHECKS_POLL_SECONDS)
                waited += CHECKS_POLL_SECONDS

            if reopened:
                break

            self.say("no CI run reported; closing and reopening the pull request to start it")
            self.step(["gh", "pr", "close", str(number)])
            self.step(["gh", "pr", "reopen", str(number)])

        raise ShipError(f"pull request #{number}: CI never reported, even after a reopen", 1)

    def watch_checks(self, number, head):
        self.say(f"watching CI on pull request #{number} (up to {self.watch_timeout} s)")
        self.step(["gh", "pr", "checks", str(number), "--watch"], timeout=self.watch_timeout,
                  allow_failure=True)
        checks = self.checks(number, head)
        red = [str(c.get("name") or c.get("context") or "?") for c in checks
               if str(c.get("conclusion") or c.get("state") or "").upper() not in GREEN]

        if not checks:
            raise ShipError(f"pull request #{number}: no checks reported on {head[:12]}", 1)

        if red:
            raise ShipError(
                f"pull request #{number}: not green - {', '.join(red)}. Read the failure with "
                "`gh run view --log-failed`, fix it on the same branch, commit, and run this "
                "again; the pull request is reused.",
                1,
            )


def main(arguments=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=".", help="the repository checkout")
    parser.add_argument("--title", required=True, help="the pull request title")
    parser.add_argument("--body-file", required=True, help="the pull request body, a file")
    parser.add_argument("--base", default=DEFAULT_BASE, help="the integration branch")
    parser.add_argument("--anchor", help="the session's anchor directory, if not --root")
    parser.add_argument("--no-merge", action="store_true", help="stop once CI is green")
    parser.add_argument("--checks-appear-seconds", type=int, default=CHECKS_APPEAR_SECONDS)
    parser.add_argument("--watch-timeout", type=int, default=WATCH_TIMEOUT_SECONDS)
    options = parser.parse_args(arguments)

    root = os.path.abspath(options.root)
    anchor = os.path.abspath(options.anchor) if options.anchor else None
    shipper = Shipper(
        root,
        guard=lambda command, where: guard_verdict(command, where, anchor),
        checks_appear_seconds=options.checks_appear_seconds,
        watch_timeout=options.watch_timeout,
    )

    try:
        shipper.ship(options.title, options.body_file, options.base, merge=not options.no_merge)
    except ShipError as error:
        print(f"ship: {error}", file=sys.stderr)
        return error.status
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"ship: stopped on an unexpected answer ({type(error).__name__}: {error})",
              file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
