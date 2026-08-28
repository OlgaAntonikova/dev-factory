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
    """The repository being worked on, however the hook was invoked."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or "."


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


def _is_argv(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(isinstance(item, str) and item for item in value)
    )
