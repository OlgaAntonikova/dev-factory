#!/usr/bin/env python3
"""PreToolUse guard for Bash - the law, as code rather than as prompt text.

Enforces three rules from CONTRACT.md:

  RULE-INSTALLS      project dependencies are the agent's; the machine is the owner's
  RULE-DEPENDENCIES  one declared package manager, no global installs
  RULE-GIT           work branches and pull requests are the agent's; the mainline is not,
                     and the integration branch changes only through a pull request

What this guard is: a guardrail that stops ordinary commands from doing what the contract
forbids, by mistake or by habit. What it is not: a sandbox. A process that can run Python can
run anything, and no reading of a command line changes that. The backstop for the mainline is
server-side protection; see "What the guard is, and is not" in CONTRACT.md.

The command is read as commands, not as text. Quoted arguments and heredoc bodies are data
unless a shell executes them; wrappers (`env`, `timeout`, `xargs`, `if ... then`) are looked
through; a branch switch or `cd` earlier on the same line is taken into account. Where the
guard cannot know what will run - a variable in a git argument - it refuses rather than
guesses.

Contract: exit 0 = allow; exit 2 = block, with stderr fed back to Claude as the reason.
Malformed hook input fails open so that a bug here never bricks a session; an internal error
while judging a command that touches git, gh or a package manager fails closed.
"""
import json
import os
import re
import subprocess
import sys

import factory_config as cfg

SHELLS = {"bash", "sh", "zsh", "dash"}
SYSTEM_INSTALLERS = {"brew", "apt", "apt-get", "yum", "dnf", "pacman", "winget", "choco",
                     "scoop", "snap", "sudo", "msiexec"}
PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "bun", "pip", "pip3", "pipx", "uv", "poetry",
                    "conda", "cargo", "gem", "go", "composer", "nuget", "mvn", "gradle",
                    "dotnet"}

#: Names worth recognising even when written with escapes (`gi\t`) or a path.
INTERESTING = {"git", "gh", "eval", "source", "."} | SHELLS | SYSTEM_INSTALLERS | PACKAGE_MANAGERS

#: A quoted argument this plain cannot hide a command, so it is read as the word it is. Anything
#: else - spaces, pipes, regex syntax - is data and is masked, unless a shell executes it.
PLAIN_WORD = re.compile(r"^[\w./:+@~^=,%-]*$")
MASK = "_quoted_"

#: Separators between simple commands. `$(`, `(` and backticks open a command.
SEPARATOR = re.compile(r"\|\||&&|\$\(|[;|&\n()`]")

#: A redirection written as one word (`2>&1`, `>out.txt`, `&>/dev/null`), and one whose target
#: is the next word (`>`, `2>`, `&>`). Splitting on `&` leaves `2>&1` as `2>` and `1`; before
#: these were dropped, a push ending in `2>&1` was read as pushing a branch called "2>".
REDIRECT_WORD = re.compile(r"^(\d*[<>]{1,2}|&>{1,2})&?\S+$")
REDIRECT_OPERATOR = re.compile(r"^(\d*[<>]{1,2}|&>{1,2})$")

HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][\w-]*)\1")
QUOTED = re.compile(r"\$?'([^']*)'|\"((?:[^\"\\]|\\.)*)\"")

#: Words that precede the real command without being it.
PREFIX_WORDS = {"time", "command", "builtin", "nohup", "exec", "then", "do", "else", "elif",
                "if", "while", "until", "!", "{", "}"}

#: Wrappers that run their arguments as a command, and the options of theirs that take a value.
WRAPPERS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {"-I", "-n", "-P", "-d", "-L", "-s", "-E", "-a", "--arg-file", "--delimiter",
              "--max-args", "--max-procs", "--max-lines", "--max-chars", "--eof"},
    "nice": {"-n", "--adjustment"},
    "stdbuf": set(),
    "watch": {"-n", "--interval"},
    "eval": set(),
}

REDIRECT_ALONE = {">", ">>", "<", "1>", "2>", "&>", "1>>", "2>>"}


def block(reason: str) -> None:
    print(reason, file=sys.stderr)
    sys.stderr.flush()
    sys.exit(2)


# --- Reading the command line as commands ---------------------------------------------------


def command_name(word: str) -> str:
    """The program a word names: its last path component, without `.exe` or escapes."""

    def last(text):
        name = re.split(r"[\\/]", text)[-1].lower()
        return name[:-4] if name.endswith(".exe") else name

    unescaped = word.replace("\\", "")
    candidate = unescaped.rsplit("/", 1)[-1].lower()
    candidate = candidate[:-4] if candidate.endswith(".exe") else candidate

    return candidate if candidate in INTERESTING else last(word)


def strip_wrappers(tokens):
    """Drop environment assignments, redirections, keywords and wrappers before the command."""
    while tokens:
        head = tokens[0]
        name = command_name(head)

        if re.match(r"^\w+=", head) or head in PREFIX_WORDS:
            tokens = tokens[1:]
        elif head in REDIRECT_ALONE:
            tokens = tokens[2:]
        elif re.match(r"^\d*[<>]{1,2}&?\S+$", head):
            tokens = tokens[1:]
        elif name in WRAPPERS:
            value_options = WRAPPERS[name]
            tokens = tokens[1:]

            while tokens and (tokens[0].startswith("-") or re.match(r"^\w+=", tokens[0])):
                tokens = tokens[2:] if tokens[0] in value_options else tokens[1:]

            if name == "timeout" and tokens:
                tokens = tokens[1:]  # the duration

            if name == "xargs":
                # xargs appends arguments the guard cannot see; a `$` token makes every
                # argument-dependent check below refuse rather than guess.
                tokens = tokens + ["$xargs"]
        else:
            break

    return tokens


def feeds_shell(line: str, heredoc: re.Match) -> bool:
    """Whether a heredoc on this line is executed: fed to a shell, on either side of the `<<`."""
    for segment in SEPARATOR.split(line[: heredoc.start()] + " " + line[heredoc.end():]):
        tokens = strip_wrappers(segment.split())

        if tokens and command_name(tokens[0]) in SHELLS | {"eval", "source", "."}:
            return True

    return False


def strip_heredocs(command: str) -> str:
    """Drop heredoc bodies that are only data. A body that a shell executes is kept, and so is
    an unquoted-delimiter body containing a command substitution, which the shell expands."""
    lines = command.split("\n")
    kept = []
    index = 0

    while index < len(lines):
        line = lines[index]
        kept.append(line)
        match = HEREDOC.search(line)
        index += 1

        if not match:
            continue

        quoted_tag, tag = match.group(1), match.group(2)
        body = []

        while index < len(lines) and lines[index].strip() != tag:
            body.append(lines[index])
            index += 1

        expands = not quoted_tag and any("$(" in b or "`" in b for b in body)

        if feeds_shell(line, match) or expands:
            kept.extend(body)

        if index < len(lines):
            kept.append(lines[index])  # the terminator
            index += 1

    return "\n".join(kept)


def unquote(command: str, depth: int = 0) -> str:
    """Replace quoted arguments with what a shell would run, or with a mask when it is data.

    Quoted text that a shell executes (`bash -c "..."`, `eval '...'`, `"$(...)"`) is masked in
    place and appended as commands of its own, so the command around it stays whole."""
    executed = []

    def replace(match):
        single = match.group(1) is not None
        content = match.group(1) if single else match.group(2)
        before = strip_wrappers(command[: match.start()].split()[-3:]) or \
            command[: match.start()].split()[-2:]

        shell_c = (len(before) >= 2 and before[-1].startswith("-") and "c" in before[-1]
                   and command_name(before[-2]) in SHELLS)
        evaluated = bool(command[: match.start()].split()) and \
            command_name(command[: match.start()].split()[-1]) == "eval"
        substitutes = not single and ("$(" in content or "`" in content)

        if (shell_c or evaluated or substitutes) and depth < 3:
            executed.append(unquote(content, depth + 1))
            return MASK

        if PLAIN_WORD.match(content):
            return content

        return MASK

    text = QUOTED.sub(replace, command)
    return "\n".join([text] + executed)


#: Redirections that contain an `&`, which would otherwise be split on as a separator and cut
#: the command in two: fd duplication (`2>&1`, `>&2`, `<&0`, `>&-`) has no file target and is
#: dropped; `&>`, `&>>` and `>&` followed by a file redirect both streams to it, which is a `>`.
FD_DUPLICATION = re.compile(r"\d*[<>]&(\d+|-)(?=[\s;|&)]|$)")
BOTH_STREAMS = re.compile(r"&>>?|>&")

#: Separators after which the command they interrupt continues past a substitution the guard
#: cannot see into: `git push origin $(echo x) main` pushes `main`.
OPENS_SUBSTITUTION = {"$(", "(", "`"}

#: Appended to a command whose arguments run on past a substitution, or redirect into one. It
#: contains `$`, so every argument-dependent check refuses rather than guesses.
UNSEEN = "$unseen"


def commands(command: str):
    """Each simple command as a token list, with what precedes the real command removed."""
    text = unquote(strip_heredocs(command))
    text = BOTH_STREAMS.sub(" > ", FD_DUPLICATION.sub(" ", text))
    parts = re.split("(" + SEPARATOR.pattern + ")", text)

    for index in range(0, len(parts), 2):
        following = parts[index + 1] if index + 1 < len(parts) else ""
        tokens = without_redirections(strip_wrappers(parts[index].split()))

        if tokens and following in OPENS_SUBSTITUTION:
            tokens = tokens + [UNSEEN]

        if tokens:
            yield tokens


def without_redirections(tokens):
    """Redirections are not arguments, wherever they stand in the command. An operator whose
    target was cut off by a substitution needs nothing here: the command it belongs to already
    carries `$unseen` from the split."""
    kept = []
    skip = False

    for token in tokens:
        if skip:
            skip = False
        elif REDIRECT_OPERATOR.match(token):
            skip = True
        elif not REDIRECT_WORD.match(token):
            kept.append(token)

    return kept


def options(arguments):
    """Every option named, normalised: `--x=y` is `--x`, `-abc` is `-a -b -c`."""
    found = []

    for token in arguments:
        if token == "--":
            break
        if token.startswith("--"):
            found.append(token.split("=", 1)[0])
        elif re.match(r"^-[A-Za-z]+$", token):
            found.extend("-" + char for char in token[1:])

    return found


def has_option(arguments, long_names=(), short=""):
    """Whether any of the options is present. Git and gh accept any unambiguous prefix of a
    long option, so `--forc` is `--force`; a prefix of three letters or more counts."""
    for option in options(arguments):
        # Below three letters a prefix is ambiguous for every option this guard asks about,
        # and git rejects an ambiguous prefix, so it cannot reach a banned option.
        if option.startswith("--") and len(option) >= 5:
            if any(name.startswith(option) for name in long_names):
                return True
        elif len(option) == 2 and option[1] in short:
            return True

    return False


def option_values(arguments, long_name, short_name=None):
    """Every value given to an option: `--base x`, `--base=x`, `-B x`, `-Bx`."""
    values = []

    for index, token in enumerate(arguments):
        following = arguments[index + 1] if index + 1 < len(arguments) else None

        if token == long_name or (short_name and token == short_name):
            if following is not None:
                values.append(following)
        elif token.startswith(long_name + "="):
            values.append(token.split("=", 1)[1])
        elif short_name and token.startswith(short_name) and len(token) > len(short_name) \
                and not token.startswith("--"):
            values.append(token[len(short_name):])

    return values


def mentions_tooling(text: str) -> bool:
    words = {command_name(word) for word in re.split(r"[\s;&|()`$]+", text) if word}
    return bool(words & ({"git", "gh"} | PACKAGE_MANAGERS))


# --- Reading the hook input ------------------------------------------------------------------

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # fail open

if data.get("tool_name") != "Bash":
    sys.exit(0)

cmd = (data.get("tool_input") or {}).get("command") or ""


def fail_closed(kind, value, traceback):
    """An internal error while judging a command that touches git, gh or a package manager
    blocks; anything else fails open, so a bug here never bricks an unrelated command."""
    if mentions_tooling(cmd):
        print(f"Blocked: the guard could not judge this command ({kind.__name__}: {value}). "
              "Run git, gh and package managers as plain commands.", file=sys.stderr)
        sys.stderr.flush()
        os._exit(2)

    os._exit(0)


sys.excepthook = fail_closed

anchor = cfg.project_root(data)
shell_cwd = data.get("cwd") or anchor
masked = unquote(strip_heredocs(cmd))
parsed = list(commands(cmd))


#: Options of `git` itself that take a value, so the value is not read as the subcommand.
GIT_VALUE_OPTIONS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                     "--config-env"}

def git_invocation(tokens, where):
    """(subcommand, arguments, working directory, git-level -c values) for git, or None."""
    if command_name(tokens[0]) != "git":
        return None

    overrides = []
    index = 1

    while index < len(tokens) and tokens[index].startswith("-"):
        option = tokens[index]
        name = option.split("=", 1)[0]
        value = option.split("=", 1)[1] if "=" in option else None

        if name in GIT_VALUE_OPTIONS and value is None:
            value = tokens[index + 1] if index + 1 < len(tokens) else ""
            index += 1

        if name == "-C" and where is not None:
            where = os.path.join(where, value)
        elif name in {"-c", "--config-env"}:
            overrides.append(value or "")

        index += 1

    if index >= len(tokens):
        return None

    return tokens[index], tokens[index + 1:], where, overrides


def locate(commands_in_order):
    """Each command with the directory it runs in, following every `cd` on the line. `None`
    once a `cd` cannot be followed (a variable, `~`, `-`, a quoted path)."""
    where = shell_cwd
    located = []

    for tokens in commands_in_order:
        if command_name(tokens[0]) == "popd":
            where = None
            continue

        if command_name(tokens[0]) in {"cd", "pushd"}:
            target = tokens[1] if len(tokens) > 1 else "~"
            where = None if re.search(r"[$~`]|^-$", target) or target == MASK or where is None \
                else os.path.join(where, target)
            continue

        located.append((tokens, where))

    return located


def configs_for(directory):
    """Every .dev-factory.json that governs a directory: its own and each ancestor's, up to and
    including the repository root. A nested config (a monorepo package, a fixture) adds rules;
    it never replaces the repository's. `None` for a directory the guard cannot know. A
    directory with no config at all is governed by the defaults, `{}`."""
    if directory is None:
        return None

    found = []
    current = os.path.abspath(directory)

    while True:
        if os.path.exists(os.path.join(current, cfg.CONFIG_NAME)):
            loaded, _ = cfg.load(current)
            found.append(loaded if isinstance(loaded, dict) else {})

        parent = os.path.dirname(current)

        if os.path.exists(os.path.join(current, ".git")) or parent == current:
            break

        current = parent

    return found or [{}]


located = locate(parsed)

#: The configurations this line answers to: every directory a command acts in, and the
#: session anchor. Rules combine toward the strictest - protected branches and declared bans
#: are united, permitted package managers are intersected per command - so stepping into a
#: directory with a laxer (or no) config can only add restrictions, never remove them.
#: Found in review: taking one config from the first `cd` let `cd /tmp/x && cd repo && ...`
#: drop the repository's integration branch, its bans and its package-manager list.
touched = [(git_invocation(tokens, where) or (None, None, where))[2]
           for tokens, where in located]
governing = [configs_for(directory) for directory in touched] + [configs_for(anchor)]
all_configs = [c for group in governing if group for c in group]


# --- RULE-INSTALLS: the machine is the owner's ----------------------------------------------

for tokens in parsed:
    if command_name(tokens[0]) in SYSTEM_INSTALLERS:
        block(
            f"Blocked by RULE-INSTALLS: '{command_name(tokens[0])}' changes the machine, not the "
            "project. Machine-level installs are the owner's: output a checklist - program, "
            "exact version, install command - and wait for the owner to confirm."
        )

if re.search(r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^\n|;&]*\|\s*(ba|z|p?w)?sh\b", masked):
    block(
        "Blocked by RULE-INSTALLS: piping a downloaded script into a shell is never allowed. "
        "Give the owner the link and the command; they review it, install and confirm."
    )

if re.search(r"\bdotnet-install(\.sh|\.ps1)?\b|\brustup\s+(install|toolchain)\b"
             r"|\bnvm\s+install\b|\bpyenv\s+install\b|\bsdk\s+install\b", masked):
    block(
        "Blocked by RULE-INSTALLS: SDKs and toolchains are installed by the owner. Give the "
        "exact command in your reply and wait for confirmation."
    )


# --- RULE-DEPENDENCIES: one package manager, project scope only ------------------------------

GLOBAL_FLAGS = ("--global", "--user", "--break-system-packages")

#: Verbs that install for the machine whatever the flags say.
GLOBAL_VERBS = {
    ("cargo", "install"), ("go", "install"), ("gem", "install"), ("pipx", "install"),
    ("pipx", "upgrade"), ("yarn", "global"), ("composer", "global"), ("uv", "tool"),
}


def allowed_managers(declared) -> set:
    """`packageManager`, plus the first word of the declared build and test commands -
    otherwise ["npm", "test"] would be blocked here while the stop gate ran it happily."""
    if not declared:
        return set()

    names = set()

    if isinstance(declared.get("packageManager"), str):
        names.add(declared["packageManager"])

    for field in ("build", "test", "fastTest"):
        argv = declared.get(field)

        if isinstance(argv, list) and argv and isinstance(argv[0], str):
            names.add(command_name(argv[0]))

    return names


def package_invocation(tokens):
    """(manager, arguments) for a package-manager command, or None. `python -m pip` counts."""
    name = command_name(tokens[0])

    if name in PACKAGE_MANAGERS:
        return name, tokens[1:]

    if re.match(r"^(python[\d.]*|py)$", name) and len(tokens) >= 3 and tokens[1] == "-m" \
            and tokens[2] in {"pip", "pip3"}:
        return "pip", tokens[3:]

    return None


#: Options that point a package manager at another directory than the one it runs in.
ELSEWHERE_OPTIONS = ("--prefix", "--cwd", "--dir", "--target", "--root", "--project",
                     "--directory", "--manifest-path", "--global-dir")


def permitted_in(directory):
    """The package managers every config governing `directory` allows."""
    governing_here = configs_for(directory)

    if not governing_here:
        return set()

    allowed = [allowed_managers(c) for c in governing_here]
    return set.intersection(*allowed) if allowed else set()


for tokens, where in located:
    invocation = package_invocation(tokens)

    if not invocation:
        continue

    manager, arguments = invocation
    permitted = permitted_in(where)
    pip_family = {"pip", "pip3"}

    if has_option(arguments, ELSEWHERE_OPTIONS, "Ct"):
        block(
            f"Blocked by RULE-DEPENDENCIES: '{manager}' pointed at another directory. Run it "
            "from the project it installs into, so that project's rules are the ones applied."
        )

    if not (manager in permitted or (manager in pip_family and permitted & pip_family)):
        block(
            f"Blocked by RULE-DEPENDENCIES: '{manager}' is not this project's package manager. "
            f"Declared: {', '.join(sorted(permitted)) or 'none'}. If the project really needs "
            f"{manager}, that is a conversation with the owner and an entry in "
            f"{cfg.CONFIG_NAME}, not a one-off command."
        )

    verb = arguments[0] if arguments else ""
    second = arguments[1] if len(arguments) > 1 else ""

    if (manager, verb) in GLOBAL_VERBS or has_option(arguments, GLOBAL_FLAGS, "g"):
        block(
            f"Blocked by RULE-INSTALLS: '{manager} {' '.join(arguments)}' installs for the whole "
            "machine. The agent installs into the project only; a global tool goes on the "
            "owner's checklist."
        )

    if manager == "dotnet" and ((verb == "workload" and second in {"install", "update", "restore"})
                                or (verb == "new" and second in {"install", "--install", "-i"})):
        block(
            "Blocked by RULE-INSTALLS: SDK workloads and templates are installed by the owner. "
            "Give the exact command in your reply and wait for confirmation."
        )


# --- RULE-GIT: work branches are the agent's, the mainline is not -----------------------------
#
#   main / master   releases only; the agent never commits, pushes, merges or opens a PR into it
#   dev             integration; changes only through a merged pull request
#   feature/... fix/... chore/... docs/... refactor/... test/... ci/...   the agent's

MAINLINE = {"main", "master"}
WORK_BRANCH = re.compile(r"^(feature|fix|chore|docs|refactor|test|ci)/[a-z0-9][a-z0-9._-]*$")

def integration_of(config):
    """A config's integration branch, or None when the declared value is not acceptable."""
    declared = config.get("integrationBranch", "dev")
    valid = (isinstance(declared, str) and re.match(r"^[A-Za-z0-9._/-]+$", declared)
             and declared not in MAINLINE and not WORK_BRANCH.match(declared))
    return declared if valid else None


invalid = [c.get("integrationBranch") for c in all_configs if integration_of(c) is None]

if invalid and any(command_name(t[0]) in {"git", "gh"} for t in parsed):
    block(
        f"Blocked by RULE-GIT: 'integrationBranch' in {cfg.CONFIG_NAME} is {invalid[0]!r}. It "
        f"must be a branch name that is neither the mainline ({', '.join(sorted(MAINLINE))}) "
        "nor a work branch. Ask the owner to fix it."
    )

#: Every integration branch of every repository this line touches, and `dev` always. A branch
#: protected anywhere on the line is protected everywhere on it.
PROTECTED = MAINLINE | {"dev"} | {integration_of(c) for c in all_configs}
INTEGRATION = "/".join(sorted(PROTECTED - MAINLINE))


def integration_in(directory):
    """The one integration branch of the repository a gh command runs in, or None when it is
    unknown or the governing configs disagree."""
    names = {integration_of(c) for c in configs_for(directory) or []}
    return names.pop() if len(names) == 1 else None

GIT_ALLOWED = {
    "status", "diff", "log", "show", "blame", "rev-parse", "ls-files", "shortlog", "describe",
    "grep", "help", "var", "reflog", "ls-remote", "merge-base", "rev-list", "cat-file",
    "check-ignore",
    "ls-tree", "for-each-ref", "show-ref", "name-rev",
    "add", "commit", "restore", "stash", "rm", "mv", "revert", "cherry-pick", "reset",
    "branch", "checkout", "switch", "worktree",
    "clone", "init", "fetch", "pull", "merge", "push",
    "remote", "config", "tag",
}

#: Subcommands whose meaning depends on arguments the guard must be able to read.
GIT_WRITES = {"commit", "merge", "cherry-pick", "revert", "push", "pull", "reset", "branch",
              "remote", "config", "tag", "switch", "checkout", "worktree"}

PUSH_VALUE_OPTIONS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
PUSH_BANNED_LONG = ("--force", "--force-with-lease", "--force-if-includes", "--all",
                    "--mirror", "--tags", "--delete", "--prune")

#: What this command line has switched to so far: directory -> branch.
switched = {}


def current_branch(where):
    """The branch checked out in `where`, as this command line will have left it."""
    if where is None:
        return None

    key = os.path.normcase(os.path.abspath(where))

    if key in switched:
        return switched[key]

    try:
        proc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=where,
                              capture_output=True, text=True)
    except (OSError, ValueError):
        return None

    return proc.stdout.strip() if proc.returncode == 0 else None


def positional(arguments, value_options=()):
    """Arguments that are not options or option values."""
    found = []
    skip = False

    for token in arguments:
        if skip:
            skip = False
        elif token in value_options:
            skip = True
        elif not token.startswith("-"):
            found.append(token)

    return found


def push_destinations(arguments, where):
    """Every branch a push writes to. `None` in the list means "cannot tell"."""
    refspecs = positional(arguments, PUSH_VALUE_OPTIONS)[1:]

    if not refspecs:
        return [current_branch(where)]

    destinations = []

    for refspec in refspecs:
        destination = refspec.split(":")[-1]
        destination = re.sub(r"^refs/heads/", "", destination)

        if destination == "HEAD" or destination == "":
            destination = current_branch(where) if destination == "HEAD" else refspec
        destinations.append(destination)

    return destinations


for tokens, where in located:
    invocation = git_invocation(tokens, where)

    if not invocation:
        if re.match(r"^\$", tokens[0]) and len(tokens) > 1 and tokens[1] in GIT_WRITES | {"pr"}:
            block(
                f"Blocked by RULE-GIT: '{tokens[0]}' hides which program runs. Name git or gh "
                "directly."
            )
        continue

    subcommand, arguments, git_where, overrides = invocation

    if subcommand not in GIT_ALLOWED:
        block(
            f"Blocked by RULE-GIT: 'git {subcommand}' rewrites or administers the repository and "
            f"is the owner's. Allowed: {', '.join(sorted(GIT_ALLOWED))} - within the branch "
            "rules in CONTRACT.md."
        )

    if subcommand in GIT_WRITES and any("$" in token for token in arguments):
        block(
            f"Blocked by RULE-GIT: 'git {subcommand}' with a variable in its arguments. The guard "
            "cannot see what a variable holds; spell the arguments out."
        )

    if any(re.match(r"^core\.hookspath", value, re.I) for value in overrides):
        block("Blocked by RULE-GIT: overriding core.hooksPath bypasses hooks.")

    if has_option(arguments, ("--no-verify",), "n" if subcommand == "commit" else ""):
        block(
            "Blocked by RULE-GIT: never bypass hooks. If a hook fails, fix what it is "
            "complaining about or report it - do not skip the check."
        )

    if has_option(arguments, ("--amend",)):
        block(
            "Blocked by RULE-GIT: amending rewrites a commit somebody may already have read. "
            "Make a new commit instead."
        )

    if subcommand in {"switch", "checkout"}:
        created = option_values(arguments, "--create", "-c") + option_values(arguments, "-b") \
            + option_values(arguments, "-B") + option_values(arguments, "-C")
        names = positional(arguments)

        if "--" not in arguments and (created or names):
            target = created[0] if created else names[0]
            switched[os.path.normcase(os.path.abspath(git_where or "."))] = target

    if subcommand in {"commit", "merge", "cherry-pick", "revert"}:
        branch = current_branch(git_where)

        if git_where is None:
            block(
                f"Blocked by RULE-GIT: 'git {subcommand}' after a `cd` the guard cannot follow. "
                "Run it as its own command, from the repository."
            )

        if branch in PROTECTED:
            block(
                f"Blocked by RULE-GIT: 'git {subcommand}' on '{branch}'. Work happens on a work "
                f"branch (feature/<n>-<slug>, fix/..., chore/...) created from '{INTEGRATION}', "
                f"and reaches '{INTEGRATION}' only through a merged pull request. "
                f"'{'/'.join(sorted(MAINLINE))}' is the owner's."
            )

    if subcommand == "reset" and has_option(arguments, ("--hard", "--merge", "--keep")):
        block(
            "Blocked by RULE-GIT: this reset discards work. Stash it, or make a revert commit."
        )

    if subcommand == "pull" and has_option(arguments, ("--rebase",), "r"):
        block(
            "Blocked by RULE-GIT: rebasing rewrites history. Pull with a merge "
            "(`git pull --no-rebase`), or fetch and `git merge origin/<branch>`."
        )

    if subcommand == "branch":
        destructive = has_option(arguments, ("--delete", "--move", "--force", "--copy"),
                                 "dDmMfcC")
        named = set(positional(arguments))

        if destructive and named & PROTECTED:
            block(
                f"Blocked by RULE-GIT: deleting, renaming or forcing "
                f"{', '.join(sorted(named & PROTECTED))} is the owner's. The agent may delete "
                "its own merged work branches."
            )

    if subcommand == "remote" and positional(arguments)[:1] not in ([], ["show"], ["get-url"]):
        block(
            "Blocked by RULE-GIT: adding, removing or re-pointing a remote is the owner's. "
            "Reading them (`git remote -v`) is fine."
        )

    if subcommand == "config":
        reading = has_option(arguments, ("--get", "--get-all", "--get-regexp", "--list"), "l")
        plain_read = len(positional(arguments)) == 1 and all(
            option in {"--global", "--local", "--system", "--show-origin", "--show-scope"}
            for option in options(arguments))

        if not (reading or plain_read):
            block(
                "Blocked by RULE-GIT: changing git configuration is the owner's. Reading it "
                "(`git config user.name`, `git config --list`) is fine."
            )

    if subcommand == "tag" and positional(arguments) and not has_option(arguments, ("--list",),
                                                                        "l"):
        block(
            "Blocked by RULE-GIT: tags mark releases, and releases are the owner's - they are "
            f"cut from '{'/'.join(sorted(MAINLINE))}' after the release pull request."
        )

    if subcommand == "push":
        refspecs = positional(arguments, PUSH_VALUE_OPTIONS)[1:]

        if has_option(arguments, PUSH_BANNED_LONG, "fd") \
                or any(r.startswith("+") or r.startswith(":") for r in refspecs):
            block(
                "Blocked by RULE-GIT: this push replaces, deletes or bulk-publishes rather than "
                "adding to one branch. Force (including --force-with-lease), --all, --mirror, "
                "--tags and --delete are the owner's. A merged work branch is deleted by "
                "`gh pr merge --delete-branch`."
            )

        for destination in push_destinations(arguments, git_where):
            if destination is None:
                block(
                    "Blocked by RULE-GIT: cannot tell which branch this push would land on, and "
                    "a push whose destination is unknown is not one the agent may make. Name "
                    "it: `git push -u origin feature/<n>-<slug>`."
                )

            if not WORK_BRANCH.match(destination):
                block(
                    f"Blocked by RULE-GIT: '{destination}' is not a work branch. The agent pushes "
                    "feature/, fix/, chore/, docs/, refactor/, test/ and ci/ branches; "
                    f"'{INTEGRATION}' changes only through a merged pull request, and "
                    f"'{'/'.join(sorted(MAINLINE))}' is the owner's."
                )


# --- RULE-GIT: the GitHub CLI ----------------------------------------------------------------
#
# Pull requests into the integration branch, issues, CI runs and read-only API calls are the
# agent's. Repository settings, secrets, releases, credentials and anything aimed at the
# mainline are not. Merging is allowed only into the integration branch, only with every check
# green - checked here, because branch protection is not available on every plan.

GH_ALLOWED = {
    "auth": {"status"},
    "pr": {"create", "view", "list", "checks", "diff", "comment", "status", "merge", "ready",
           "edit", "close", "reopen", "checkout"},
    "issue": {"create", "view", "list", "comment", "edit", "close", "reopen", "status",
              "develop"},
    "run": {"list", "view", "watch", "rerun"},
    "workflow": {"list", "view"},
    "repo": {"view", "clone", "create", "list"},
    "label": {"list", "create"},
    "search": None,
    "browse": None,
    "api": None,
    "status": None,
}

#: Check conclusions that count as passed.
GREEN = {"SUCCESS", "NEUTRAL", "SKIPPED"}


def gh_argv():
    """The gh executable. Tests point DEV_FACTORY_GH at a stand-in script."""
    override = os.environ.get("DEV_FACTORY_GH")

    if override:
        return [sys.executable, override] if override.endswith(".py") else [override]

    return ["gh"]


def pull_request_state(arguments, where):
    """The pull request a merge would act on, as a validated dict, or None."""
    target = positional(arguments)[:1]

    try:
        proc = subprocess.run(
            gh_argv() + ["pr", "view", *target, "--json", "baseRefName,statusCheckRollup,state"],
            cwd=where, capture_output=True, encoding="utf-8", errors="replace", timeout=20,
        )
        state = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None

    if not isinstance(state, dict) or not isinstance(state.get("statusCheckRollup") or [], list):
        return None

    if not all(isinstance(check, dict) for check in state.get("statusCheckRollup") or []):
        return None

    return state


def check_passed(check):
    return str(check.get("conclusion") or check.get("state") or "").upper() in GREEN


for tokens, where in located:
    if command_name(tokens[0]) != "gh":
        continue

    INTEGRATION = integration_in(where)

    if len(tokens) > 2 and tokens[1] == "pr" and tokens[2] in {"create", "edit", "merge"} \
            and (option_values(tokens[3:], "--repo", "-R")
                 or any(tok.startswith("--repo=") for tok in tokens[3:])):
        block(
            "Blocked by RULE-GIT: '--repo' points the pull request at a repository whose "
            "integration branch the guard has not read. Run gh from that repository's checkout."
        )

    if INTEGRATION is None and len(tokens) > 2 and tokens[1] == "pr" \
            and tokens[2] in {"create", "edit", "merge"}:
        block(
            "Blocked by RULE-GIT: cannot tell which integration branch this pull request belongs "
            "to - the directory is unknown, or its configs disagree. Run gh from the repository."
        )

    group = tokens[1] if len(tokens) > 1 else ""
    verb = tokens[2] if len(tokens) > 2 else ""
    arguments = tokens[3:]

    if group not in GH_ALLOWED or (GH_ALLOWED[group] is not None and verb not in GH_ALLOWED[group]):
        block(
            f"Blocked by RULE-GIT: '{' '.join(tokens[:3])}' is the owner's - repository "
            "settings, secrets, releases, credentials and deletions are not the agent's. "
            "Prepare the exact command for the owner to run."
        )

    if any("$" in token for token in tokens[1:]):
        block(
            "Blocked by RULE-GIT: a gh command with a variable in its arguments. The guard cannot "
            "see what a variable holds; spell the arguments out."
        )

    if group == "api":
        rest = tokens[2:]
        methods = [m.upper() for m in option_values(rest, "--method", "-X")]
        writes = any(re.match(r"^(-f|-F|--fie|--raw|--inp)", token) for token in rest)

        if any(m != "GET" for m in methods) or writes or "graphql" in positional(rest):
            block(
                "Blocked by RULE-GIT: 'gh api' may only read (GET, no fields, no graphql). "
                "Writing through the API reaches settings the CLI allowlist keeps from the agent."
            )

    if group == "repo" and verb == "create" and (
            "--private" not in arguments or has_option(arguments, ("--public", "--internal"))):
        block(
            "Blocked by RULE-GIT: a repository the agent creates is private. Use --private; "
            "making it public is the owner's decision."
        )

    if group == "pr" and verb in {"create", "edit"}:
        bases = option_values(arguments, "--base", "-B")

        if verb == "create" and (not bases or any(base != INTEGRATION for base in bases)):
            block(
                f"Blocked by RULE-GIT: a pull request targets '{INTEGRATION}', named explicitly "
                f"(`--base {INTEGRATION}`); without it GitHub picks the default branch, which is "
                f"the mainline. Release pull requests into '{'/'.join(sorted(MAINLINE))}' are "
                "the owner's."
            )

        if verb == "edit" and any(base != INTEGRATION for base in bases):
            block(f"Blocked by RULE-GIT: re-targeting a pull request away from '{INTEGRATION}' "
                  "is the owner's.")

    if group == "pr" and verb == "merge":
        if has_option(arguments, ("--admin",)):
            block("Blocked by RULE-GIT: '--admin' bypasses the checks the merge depends on.")

        state = pull_request_state(arguments, where)

        if state is None:
            block(
                "Blocked by RULE-GIT: could not read the pull request (gh pr view failed or "
                "answered something unexpected), so its base and checks are unknown. A merge "
                "whose target is unknown is not one the agent may make."
            )

        if state.get("baseRefName") != INTEGRATION:
            block(
                f"Blocked by RULE-GIT: this pull request targets '{state.get('baseRefName')}'. "
                f"The agent merges into '{INTEGRATION}' only; the release into "
                f"'{'/'.join(sorted(MAINLINE))}' is the owner's."
            )

        checks = state.get("statusCheckRollup") or []
        failing = [str(c.get("name") or c.get("context") or "?") for c in checks
                   if not check_passed(c)]

        if not checks or failing:
            block(
                "Blocked by RULE-GIT: a pull request merges only with CI green - "
                + (f"not green: {', '.join(failing)}." if failing else "it has no checks at all.")
                + " Wait for the run (`gh pr checks --watch`) or fix what failed."
            )


# --- Project-specific bans, declared in .dev-factory.json -----------------------------------
for entry in [e for c in all_configs for e in (c.get("blockedCommands") or [])]:
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
if re.search(r"\brm\s+-[a-zA-Z]*[rf][a-zA-Z]*[rf][a-zA-Z]*\s+(/|~)", masked):
    block(
        "Blocked: recursive force delete of an absolute or home path. Delete only inside the "
        "project, with an explicit relative path."
    )

sys.exit(0)
