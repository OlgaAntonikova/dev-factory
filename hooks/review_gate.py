#!/usr/bin/env python3
"""Review gate - RULE-REVIEW.

Refuses a commit unless a review verdict exists that was stamped against *exactly this diff*.
Approve one change and commit another, and the hashes disagree.

Three modes:

    review_gate.py                 hook mode: JSON on stdin, exit 0 to allow, 2 to block
    review_gate.py --approve       stamp the staged diff as reviewed
    review_gate.py --status        print the staged hash and what the verdict says

What this proves, and what it does not
--------------------------------------
It proves that a verdict exists and that the diff has not moved since it was written. It cannot
prove *who* wrote it: the stamping command is available to whoever runs it, including the author
of the code. Independence is enforced by the review skill and the reviewer agent, not here.
Claiming otherwise would be exactly the sort of overclaim this process exists to catch.

Failure policy: malformed hook input fails open, because a bug here must not brick a session.
A malformed or missing *verdict* fails closed, because that is the artifact being checked and a
review gate that waves through what it cannot read is not a gate.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

import factory_config as cfg

VERDICT_FILE = os.path.join(".claude", ".review-verdict")
APPROVE = "APPROVE"

#: `git commit` at the start of a command or after a separator, so that a commit message
#: mentioning the words does not trip the gate.
COMMIT = re.compile(r"(?:^|[;&|]\s*)git(?:\.exe)?\s+(?:-\S+\s+)*commit\b")

#: -a / -am / --all stage everything at commit time, which means the diff the gate hashed is
#: not the diff that gets committed.
STAGES_EVERYTHING = re.compile(r"(?:^|\s)(--all(?=\s|$)|-[a-zA-Z]*a[a-zA-Z]*(?=\s|$))")


def staged_diff(root):
    """The staged diff, or None when this is not a git repository."""
    # The encoding is pinned to UTF-8: git emits UTF-8 bytes, while text=True decodes with
    # the platform default - cp1252 on Windows - which raised UnicodeDecodeError on the first
    # real diff containing an arrow character, failing the stamp while the hook failed open.
    # errors="replace" keeps the hash deterministic even for invalid bytes.
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--no-color"],
            cwd=root, capture_output=True, encoding="utf-8", errors="replace",
        )
    except (OSError, ValueError):
        return None

    if proc.returncode != 0:
        return None

    return proc.stdout


def diff_hash(root):
    diff = staged_diff(root)

    return None if diff is None else hashlib.sha256(diff.encode("utf-8")).hexdigest()


def read_verdict(root):
    """Returns (verdict, problem). Exactly one is None."""
    path = os.path.join(root, VERDICT_FILE)

    if not os.path.exists(path):
        return None, "no review verdict exists"

    try:
        with open(path, encoding="utf-8") as handle:
            verdict = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        return None, f"the review verdict could not be read: {error}"

    if not isinstance(verdict, dict) or not isinstance(verdict.get("diffSha256"), str):
        return None, "the review verdict is not in the expected form"

    return verdict, None


def stamp(root, reviewer, verdict_word, findings):
    current = diff_hash(root)

    if current is None:
        print("Not a git repository, or git is unavailable - nothing to stamp.", file=sys.stderr)
        return 1

    if not staged_diff(root).strip():
        print("Nothing is staged. Stage the change under review with `git add` first, so that "
              "the verdict names a diff.", file=sys.stderr)
        return 1

    os.makedirs(os.path.join(root, ".claude"), exist_ok=True)

    with open(os.path.join(root, VERDICT_FILE), "w", encoding="utf-8") as handle:
        json.dump({
            "verdict": verdict_word,
            "diffSha256": current,
            "reviewer": reviewer,
            "findings": findings,
        }, handle, indent=2)

    print(f"{verdict_word} recorded for staged diff {current[:12]} (reviewer: {reviewer})")

    return 0


def hook(data):
    command = (data.get("tool_input") or {}).get("command") or ""

    if data.get("tool_name") != "Bash" or not COMMIT.search(command):
        return 0

    root = cfg.project_root(data)

    if STAGES_EVERYTHING.search(command[COMMIT.search(command).end():]):
        print(
            "Blocked by RULE-REVIEW: `git commit -a` stages files while committing, so what "
            "gets committed is not the diff that was reviewed. Stage explicitly with `git add`, "
            "have the change reviewed, then commit without -a.",
            file=sys.stderr,
        )
        return 2

    current = diff_hash(root)

    if current is None:
        return 0  # not a git repository; the commit will fail on its own terms

    verdict, problem = read_verdict(root)

    if problem:
        print(
            f"Blocked by RULE-REVIEW: {problem}.\n"
            "Every commit is preceded by a review the implementer did not perform. Have the "
            "staged change reviewed against the spec and the diff, then record the verdict:\n"
            "  python3 <plugin>/hooks/review_gate.py --approve --reviewer <name>",
            file=sys.stderr,
        )
        return 2

    if verdict.get("verdict") != APPROVE:
        findings = verdict.get("findings") or "see the review"
        print(
            f"Blocked by RULE-REVIEW: the review verdict is "
            f"{verdict.get('verdict', 'not APPROVE')}, not {APPROVE}.\n{findings}\n"
            "A BLOCK stops the work until it is answered - fix the findings and have the "
            "change reviewed again.",
            file=sys.stderr,
        )
        return 2

    if verdict["diffSha256"] != current:
        print(
            "Blocked by RULE-REVIEW: the staged diff is not the one that was reviewed.\n"
            f"  reviewed: {verdict['diffSha256'][:12]}\n"
            f"  staged:   {current[:12]}\n"
            "Something changed after the review. Have the current diff reviewed and stamped, "
            "rather than committing a change nobody looked at.",
            file=sys.stderr,
        )
        return 2

    return 0


def main(argv):
    if "--status" in argv:
        root = os.path.abspath(_option(argv, "--root") or ".")
        current = diff_hash(root)
        verdict, problem = read_verdict(root)

        print(f"staged diff: {current[:12] if current else 'not a git repository'}")
        print(f"verdict:     {problem or verdict.get('verdict')}")

        if verdict and current:
            print(f"matches:     {verdict['diffSha256'] == current}")

        return 0

    if "--approve" in argv or "--block" in argv:
        return stamp(
            root=os.path.abspath(_option(argv, "--root") or "."),
            reviewer=_option(argv, "--reviewer") or "unnamed",
            verdict_word=APPROVE if "--approve" in argv else "BLOCK",
            findings=_option(argv, "--findings") or "",
        )

    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0  # fail open: a bug here must not brick the session

    return hook(data)


def _option(argv, name):
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]

    return None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
