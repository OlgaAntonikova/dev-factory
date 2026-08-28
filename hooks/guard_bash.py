#!/usr/bin/env python3
"""PreToolUse guard for Bash - the law, as code rather than as prompt text.

Enforces three rules from CONTRACT.md:

  RULE-INSTALLS      the agent installs nothing; it hands the owner a checklist and waits
  RULE-GIT           the agent commits; publishing and history rewriting stay with the owner
  RULE-DEPENDENCIES  dependency versions live in one declared file, changed only on approval

The first two never depend on the stack, so they apply with or without a configuration. The
third does, and reads .dev-factory.json - see the note on package managers below.

Contract: exit 0 = allow; exit 2 = block, with stderr fed back to Claude as the reason. Fails
open on malformed hook input so that a bug here never bricks a session.
"""
import json
import re
import subprocess
import sys

import factory_config as cfg

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # fail open

if data.get("tool_name") != "Bash":
    sys.exit(0)

cmd = (data.get("tool_input") or {}).get("command") or ""


def block(reason: str) -> None:
    print(reason, file=sys.stderr)
    sys.exit(2)


# --- RULE-INSTALLS: system-level installers are the owner's ---------------------------------
if re.search(r"\b(brew|apt|apt-get|yum|dnf|pacman|winget|choco|scoop|snap|sudo)\b", cmd):
    block(
        "Blocked by RULE-INSTALLS: installs are the owner's. Do not install anything yourself. "
        "Output a checklist instead - program or package, exact version, install command - "
        "and wait for the owner to confirm it is done."
    )

if re.search(r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^\n|;&]*\|\s*(ba|z|p?w)?sh\b", cmd):
    block(
        "Blocked by RULE-INSTALLS: piping an installer script into a shell is not allowed. "
        "Give the owner the link and the command; they install and confirm."
    )

if re.search(r"\bdotnet-install(\.sh|\.ps1)?\b|\brustup\s+(install|toolchain)\b"
             r"|\bnvm\s+install\b|\bpyenv\s+install\b|\bsdk\s+install\b", cmd):
    block(
        "Blocked by RULE-INSTALLS: SDKs and toolchains are installed by the owner. "
        "Give the exact command in your reply and wait for confirmation."
    )


# --- RULE-DEPENDENCIES: adding a dependency is not the same as running the project ----------
#
# The rule is that dependency versions live in one declared file and change only with the
# owner's approval. It is not "package managers are dangerous". An earlier version of this
# guard blocked npm, pip, cargo and gem on sight, which was correct in a repository that used
# none of them and would have blocked the project's own test command in any repository that
# did.
#
# So three cases are distinguished:
#   running the project's tooling    npm test, cargo build          allowed
#   restoring declared dependencies  npm ci, dotnet restore         allowed
#   adding / removing / upgrading    npm install left-pad           blocked
#
# A package manager that is not this project's is blocked outright.

INSTALL_SUBCOMMANDS = {
    "npm": {"install", "i", "add", "remove", "rm", "uninstall", "un", "update", "up"},
    "pnpm": {"install", "i", "add", "remove", "rm", "uninstall", "un", "update", "up"},
    "yarn": {"install", "add", "remove", "up", "upgrade"},
    "bun": {"install", "i", "add", "remove", "update"},
    "pip": {"install", "uninstall"},
    "pip3": {"install", "uninstall"},
    "pipx": {"install", "uninstall", "upgrade"},
    "uv": {"add", "remove", "pip"},
    "poetry": {"add", "remove", "update"},
    "conda": {"install", "remove", "update"},
    "cargo": {"install", "add", "remove", "update"},
    "gem": {"install", "uninstall", "update"},
    "go": {"get", "install"},
    "composer": {"require", "remove", "update"},
    "nuget": {"install", "add", "update", "restore"},
    "mvn": set(),
    "gradle": set(),
    "dotnet": set(),  # dotnet's dependency verbs are two words; handled separately below
}

#: Flags that swallow the token after them, so it is not mistaken for a package name.
VALUE_FLAGS = {"-r", "--requirement", "-c", "--constraint", "-f", "--file", "--prefix"}


def allowed_managers(config) -> set:
    """
    The package managers this project may invoke.

    Taken from `packageManager` and, just as importantly, from the first word of the build and
    test commands the owner already declared. Otherwise a repository that declares
    ["npm", "test"] as its test command would have that very command blocked here, while the
    stop gate ran it happily - the two would disagree about the same repository.
    """
    if not config:
        return set()

    names = set()

    if isinstance(config.get("packageManager"), str):
        names.add(config["packageManager"])

    for field in ("build", "test", "fastTest"):
        argv = config.get(field)

        if isinstance(argv, list) and argv and isinstance(argv[0], str):
            names.add(argv[0])

    return names


def names_a_package(rest: str) -> bool:
    """Whether an install-ish subcommand actually names something to install."""
    tokens = rest.split()
    skip_next = False

    for token in tokens:
        if skip_next:
            skip_next = False
            continue

        if token in VALUE_FLAGS:
            skip_next = True
            continue

        if token.startswith("-"):
            continue

        if token in {"&&", "||", ";", "|"}:
            break

        return True

    return False


config, _ = cfg.load(cfg.project_root(data))
permitted = allowed_managers(config)

for manager, subcommands in INSTALL_SUBCOMMANDS.items():
    for match in re.finditer(
        rf"(?:^|[;&|]\s*)({re.escape(manager)})(?:\.exe|\.cmd)?\s+([a-z][\w-]*)?([^\n;&|]*)", cmd
    ):
        subcommand = match.group(2) or ""
        rest = match.group(3) or ""

        if manager not in permitted:
            block(
                f"Blocked by RULE-DEPENDENCIES: '{manager}' is not this project's package "
                f"manager. Declared: {', '.join(sorted(permitted)) or 'none'}. "
                f"If the project really needs {manager}, that is a conversation with the owner "
                f"and an entry in {cfg.CONFIG_NAME}, not a one-off command."
            )

        if subcommand in subcommands and names_a_package(rest):
            block(
                f"Blocked by RULE-DEPENDENCIES: '{manager} {subcommand}' changes the project's "
                "dependencies. Versions live in one declared file and change only with the "
                "owner's approval. Propose a 'package + version' list and wait. "
                f"Restoring what is already declared ({manager} with no package named) is fine."
            )

# dotnet's dependency verbs take two words, so they do not fit the table above.
if re.search(r"\bdotnet\s+(add|remove)\b[^\n;&|]*\bpackage\b", cmd):
    block(
        "Blocked by RULE-DEPENDENCIES: do not add or remove packages from the command line. "
        "Propose a 'package + version' list for the project's central version file and wait "
        "for the owner's approval."
    )

if re.search(r"\bdotnet\s+(workload\s+(install|update)|new\s+(install|--install|-i)\b"
             r"|tool\s+(install|update))", cmd):
    block(
        "Blocked by RULE-INSTALLS: SDKs, workloads, templates and tools are installed by the "
        "owner. Give the exact command in your reply and wait for confirmation."
    )


# --- RULE-GIT: the phase branch is the agent's, the mainline is not -------------------------
#
# The agent works on `phase/<n>` and may push it. Everything else - the mainline, other
# branches, force, remotes, history - stays with the owner.
#
# The teeth are on commit and push, not on navigation. A rule against `checkout` would be a
# rule against moving around: easy to trip over, easy to work around, and it does not prevent
# the thing worth preventing. A commit on the mainline has exactly one way in, and this is it.

GIT_READONLY = {
    "status", "diff", "log", "show", "blame", "rev-parse",
    "ls-files", "shortlog", "describe", "grep", "help", "var",
}
GIT_WRITE_ALLOWED = {"add", "commit", "restore", "stash"}
GIT_NAVIGATION = {"branch", "checkout", "switch"}
GIT_CONDITIONAL = {"push"}
GIT_ALLOWED = GIT_READONLY | GIT_WRITE_ALLOWED | GIT_NAVIGATION | GIT_CONDITIONAL

#: `phase/8` and `phase/8-polish`, but not `phase/finish`. The number is required because
#: it is what ties the branch to a `PHASE-n` block in the spec; the suffix is for readers.
PHASE_BRANCH = re.compile(r"^phase/\d+(-[a-z0-9-]+)?$")
MAINLINE = {"main", "master"}

#: Flags that push more than one branch, or replace what is already published.
PUSH_BANNED_FLAGS = re.compile(
    r"(?:^|\s)(--force\b|--force-with-lease\b|-f\b|--all\b|--mirror\b|--tags\b"
    r"|--delete\b|-d\b|--prune\b)"
)


def current_branch(root):
    """The checked-out branch, or None when git cannot say."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root, capture_output=True, text=True,
        )
    except (OSError, ValueError):
        return None

    return proc.stdout.strip() if proc.returncode == 0 else None


def push_destination(arguments):
    """
    The branch a push would land on, as far as the command line says it.

    Returns the destination name, or None when the command names none - in which case the
    destination is whatever branch is checked out, and the caller asks git.
    """
    tokens = [token for token in arguments.split() if not token.startswith("-")]

    # `git push <remote> <refspec>`: the refspec is the last token, and a colon means
    # "source:destination", where only the destination matters.
    if len(tokens) >= 2:
        refspec = tokens[-1]

        # No lstrip("+") here: a '+' token is a force push and was blocked above, so it can
        # never reach this line. Stripping it would only hide it.
        return refspec.split(":")[-1]

    return None


root = cfg.project_root(data)

for match in re.finditer(r"\bgit\s+(?:-[^\s]+\s+)*([a-z][a-z-]*)([^\n;&|]*)", cmd):
    subcommand = match.group(1)
    arguments = match.group(2) or ""

    if subcommand not in GIT_ALLOWED:
        block(
            f"Blocked by RULE-GIT: 'git {subcommand}' is the owner's. "
            f"The agent may run: {', '.join(sorted(GIT_ALLOWED))}, and push only to a phase "
            "branch. Remotes, history and the mainline are not the agent's."
        )

    if subcommand == "commit":
        branch = current_branch(root)

        if branch in MAINLINE:
            block(
                f"Blocked by RULE-GIT: this is a commit on '{branch}'. Work happens on a phase "
                "branch - create `phase/<n>` and commit there. Merging into the mainline is the "
                "owner's."
            )

    if subcommand == "branch" and re.search(r"(?:^|\s)-(?:[dDm]\b|-delete\b|-move\b)", arguments):
        block(
            "Blocked by RULE-GIT: deleting or renaming a branch is the owner's. Leave the "
            "branch and say what should happen to it."
        )

    if subcommand == "push":
        # A refspec beginning with '+' is a force push with no flag to notice. The first
        # version of this guard checked the flags and then stripped the '+' while working out
        # the destination, so `git push origin +phase/3` read as an ordinary push to a phase
        # branch and was allowed.
        forced_refspec = any(
            token.startswith("+") for token in arguments.split() if not token.startswith("-")
        )

        if forced_refspec or PUSH_BANNED_FLAGS.search(arguments):
            block(
                "Blocked by RULE-GIT: this push replaces or bulk-publishes rather than adding "
                "to one branch. Force, --all, --mirror, --tags and --delete are the owner's - "
                "including --force-with-lease, which is safer and still rewrites what somebody "
                "may already have read."
            )

        destination = push_destination(arguments) or current_branch(root)

        if destination is None:
            block(
                "Blocked by RULE-GIT: cannot tell which branch this push would land on, and a "
                "push whose destination is unknown is not one the agent may make. Name the "
                "branch: `git push origin phase/<n>`."
            )

        if not PHASE_BRANCH.match(destination):
            block(
                f"Blocked by RULE-GIT: '{destination}' is not a phase branch. The agent pushes "
                "`phase/<n>` and nothing else; the mainline and every other branch are the "
                "owner's."
            )

# Flag-level bans the subcommand allowlist cannot express.
if re.search(r"\bgit\b[^\n;&|]*--amend\b", cmd):
    block(
        "Blocked by RULE-GIT: rewriting history is the owner's. Make a new commit instead of "
        "amending."
    )

if re.search(r"\bgit\b[^\n;&|]*--no-verify\b", cmd):
    block(
        "Blocked by RULE-GIT: never bypass hooks. If a hook fails, fix what it is complaining "
        "about or report it - do not skip the check."
    )

if re.search(r"(?:^|[;&|]\s*)gh(?:\.exe)?\b", cmd):
    block(
        "Blocked by RULE-GIT: the GitHub CLI is the owner's. Prepare the exact commands for "
        "the owner to run."
    )


# --- Project-specific bans, declared in .dev-factory.json -----------------------------------
for entry in (config or {}).get("blockedCommands") or []:
    if not isinstance(entry, dict) or not isinstance(entry.get("pattern"), str):
        continue

    try:
        matched = re.search(entry["pattern"], cmd)
    except re.error:
        continue  # a broken pattern is the owner's to fix; it must not brick the session

    if matched:
        reason = entry.get("reason") or "this project does not allow that command"
        block(f"Blocked by {cfg.CONFIG_NAME}: {reason}")


# --- Safety net: recursive force-delete outside the workspace -------------------------------
if re.search(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*[rf][a-zA-Z]*\s+(/|~)", cmd):
    block(
        "Blocked: recursive force delete of an absolute or home path. Delete only inside the "
        "project, with an explicit relative path."
    )

sys.exit(0)
