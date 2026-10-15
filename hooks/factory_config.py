#!/usr/bin/env python3
"""Reads .dev-factory.json - the one place the hooks learn about the project's stack.

Every hook in this plugin is stack-independent. What a project is built with lives in its own
root, declared once, so that the same three hooks serve a .NET desktop application and a Node
service without either being named here. See STACK.md.

Failure policy differs by hook, on purpose:

  guard_bash  - the generic law (installs, git, publishing) never depends on the stack, so it
                applies with or without a config. Without one, every package manager is
                blocked, which is the strict reading and the safe one.
  mark_dirty  - falls back to a broad list of source extensions. Arming the gate too often
                costs a few seconds; arming it too rarely means a session ends on red.
  stop_gate   - blocks. A gate that cannot find its build command must not conclude that all
                is well.
"""
import json
import os

CONFIG_NAME = ".dev-factory.json"

#: Project-local Python virtual environments, in the order they are looked for.
VENV_DIRS = (".venv", "venv")

#: First words that name the Python interpreter - the only ones ever redirected into a venv.
PYTHON_COMMANDS = ("python", "python3")

#: Used when no configuration names any. Deliberately broad: a false arm of the stop gate
#: costs one build, a missed one costs a session that ended with red tests.
DEFAULT_SOURCE_EXTENSIONS = (
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".csproj", ".fs", ".fsproj",
    ".go", ".java", ".kt", ".rs", ".swift", ".rb", ".php", ".py",
    ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
    ".sql", ".proto", ".graphql",
    ".axaml", ".xaml", ".razor", ".html", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml", ".props", ".targets", ".sln", ".gradle",
)

STARTER = """{
  "version": 1,
  "build": ["<command>", "<args>"],
  "test":  ["<command>", "<args>"],
  "sourceExtensions": [".<ext>"]
}"""


def project_root(data: dict) -> str:
    """The session's anchor directory, however the hook was invoked."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or "."


def file_root(path: str, fallback: str) -> str:
    """The repository an edited file belongs to: the nearest ancestor directory holding a
    .dev-factory.json or a .git. A session is not always anchored in the repository it edits
    (the session that surfaced this was anchored in the home directory and edited two
    repositories), so the file's own ancestry outranks the session anchor. Falls back to the
    anchor when no marker is found up the tree."""
    directory = os.path.dirname(os.path.abspath(os.path.join(fallback, path)))

    while True:
        if (os.path.exists(os.path.join(directory, CONFIG_NAME))
                or os.path.exists(os.path.join(directory, ".git"))):
            return directory

        parent = os.path.dirname(directory)
        if parent == directory:
            return fallback
        directory = parent


def is_repository(root: str) -> bool:
    """A directory the stop gate can meaningfully gate: it declares a stack, or it is at least
    a git repository that OUGHT to declare one. An edit belonging to neither (a config file in
    the home directory, say) has no build to prove - arming the gate for it produces a demand
    nobody can satisfy, which teaches people to silence gates."""
    return (os.path.exists(os.path.join(root, CONFIG_NAME))
            or os.path.exists(os.path.join(root, ".git")))


def dirty_registry(anchor: str) -> str:
    """The marker file. It lists the dirty repository roots, one absolute path per line; an
    empty file (the pre-multi-root format) means the anchor itself is dirty."""
    return os.path.join(anchor, ".claude", ".build-dirty")


def read_dirty_roots(anchor: str):
    path = dirty_registry(anchor)

    if not os.path.exists(path):
        return []

    try:
        with open(path, encoding="utf-8") as handle:
            listed = [line.strip() for line in handle if line.strip()]
    except OSError:
        listed = []

    return listed or [anchor]


def add_dirty_root(anchor: str, root: str) -> None:
    root = os.path.abspath(root)

    if root in read_dirty_roots(anchor):
        return

    os.makedirs(os.path.dirname(dirty_registry(anchor)), exist_ok=True)

    with open(dirty_registry(anchor), "a", encoding="utf-8") as handle:
        handle.write(root + "\n")


def load(root: str):
    """Returns (config, problem). Exactly one of them is None."""
    path = os.path.join(root, CONFIG_NAME)

    if not os.path.exists(path):
        return None, f"{CONFIG_NAME} is missing from the repository root."

    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        return None, f"{CONFIG_NAME} could not be read: {error}"

    if not isinstance(config, dict):
        return None, f"{CONFIG_NAME} must contain a JSON object."

    for field in ("build", "test"):
        if not _is_argv(config.get(field)):
            return None, (
                f"{CONFIG_NAME} needs a \"{field}\" command as an array of strings, "
                f"for example [\"npm\", \"run\", \"{field}\"]."
            )

    # Optional, but a malformed one must not fall back to "test" unnoticed: the gate would
    # then run the full suite while the file claims a fast one, and nobody would know which
    # was checked.
    if "fastTest" in config and not _is_argv(config["fastTest"]):
        return None, f"{CONFIG_NAME} has a \"fastTest\" entry that is not an array of strings."

    return config, None


def command(config: dict, field: str, fallback: str = ""):
    """An argv array from the config, or the fallback field's, or None."""
    if _is_argv(config.get(field)):
        return list(config[field])

    if fallback and _is_argv(config.get(fallback)):
        return list(config[fallback])

    return None


def source_extensions(config):
    declared = (config or {}).get("sourceExtensions")

    if isinstance(declared, list) and all(isinstance(x, str) for x in declared):
        return tuple(x for x in declared if x.startswith("."))

    return DEFAULT_SOURCE_EXTENSIONS


def suggestion(root: str) -> str:
    """
    A guess at the stack, for the error message only.

    Never used to configure anything. A gate that guesses which of seven npm scripts is the
    test suite is right often enough to be trusted and wrong often enough to matter; the whole
    point of the declaration is that somebody decided and wrote it down. This exists so that
    the person reading the block has something to paste rather than a blank page.
    """
    marks = {
        "package.json": '{"build": ["npm", "run", "build"], "test": ["npm", "test"]}',
        "Cargo.toml": '{"build": ["cargo", "build"], "test": ["cargo", "test"]}',
        "go.mod": '{"build": ["go", "build", "./..."], "test": ["go", "test", "./..."]}',
        "pyproject.toml": '{"build": ["python", "-m", "compileall", "."], "test": ["pytest"]}',
        "pom.xml": '{"build": ["mvn", "-q", "compile"], "test": ["mvn", "-q", "test"]}',
    }

    for mark, example in marks.items():
        if os.path.exists(os.path.join(root, mark)):
            return f"This looks like a {mark} project. A starting point, to be confirmed:\n{example}"

    for name in os.listdir(root) if os.path.isdir(root) else []:
        if name.endswith((".sln", ".csproj", ".fsproj")):
            return (
                "This looks like a .NET project. A starting point, to be confirmed:\n"
                '{"build": ["dotnet", "build", "--nologo"], "test": ["dotnet", "test", "--no-build"]}'
            )

    return ""


def project_command(root: str, argv: list):
    """The argv and environment that run a declared command the way the project runs it.

    Only one thing is ever changed: a first word naming the Python interpreter (`python`,
    `python3`) is replaced by the interpreter inside the project's virtual environment - a
    `.venv` or `venv` holding `pyvenv.cfg` and that interpreter - and the environment's scripts
    directory goes first on PATH, as if it were activated. Without that, Windows starts the
    interpreter the hook runs on (CreateProcess searches the parent's own directory before
    PATH), which has none of the project's packages, and the gate reports a red that is not
    there.

    Nothing is searched: no PATH lookup, no current directory, no PATHEXT. Every other command,
    and every project without such an environment, runs exactly as declared. Trusting the
    project's interpreter is a deliberate trade-off; see STACK.md.
    """
    argv, env = list(argv), dict(os.environ)

    if argv[0] not in PYTHON_COMMANDS:
        return argv, env

    for name in VENV_DIRS:
        venv = os.path.join(root, name)
        scripts = os.path.join(venv, "Scripts" if os.name == "nt" else "bin")
        interpreter = os.path.join(scripts, argv[0] + (".exe" if os.name == "nt" else ""))

        if os.path.isfile(os.path.join(venv, "pyvenv.cfg")) and os.path.isfile(interpreter):
            env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = venv
            env.pop("PYTHONHOME", None)
            return [interpreter] + argv[1:], env

    return argv, env


def _is_argv(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(item, str) and item for item in value)
    )
