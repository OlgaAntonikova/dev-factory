#!/usr/bin/env python3
"""PostToolUse marker - arms the stop gate.

Whenever Claude edits or creates a file the project calls source, drop a marker so the stop
gate knows it must prove the build and the tests before the session may end. A session that
only reads code never arms it.

Covers Bash as well as the edit tools. Plenty of files get written through heredocs, sed and
redirects rather than Edit/Write, and watching only the edit tools leaves the gate silently
green - which is the failure this whole plugin exists to prevent.

What counts as source comes from the project's .dev-factory.json. Without one, a deliberately
broad default list is used: arming the gate when nothing important changed costs one build,
missing it costs a session that ended on red.

Always exits 0. This hook observes; it never blocks.
"""
import json
import os
import re
import sys

import factory_config as cfg

# Shell constructs that write to disk. Redirects require trailing whitespace so that stream
# merges such as `2>&1` are not mistaken for a write.
BASH_WRITE = re.compile(
    r">>?\s|\btee\b|\bsed\s+-i\b|\bcp\b|\bmv\b|<<\s*['\"]?\w+|\brm\b|\bpython3?\s"
)

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def marks_source(data: dict, extensions) -> bool:
    tool = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}

    if tool == "Bash":
        command = tool_input.get("command") or ""

        # The heredoc body is part of the command string, so file names written by an inline
        # script show up here too.
        return bool(BASH_WRITE.search(command)) and any(
            ext in command for ext in extensions
        )

    # Named explicitly rather than "anything carrying a file_path": Read carries one too, and
    # this hook must be correct on its own rather than relying on the settings matcher.
    if tool in EDIT_TOOLS:
        return (tool_input.get("file_path") or "").endswith(tuple(extensions))

    return False


try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

root = cfg.project_root(data)
config, _ = cfg.load(root)  # a missing or broken config is the stop gate's business, not ours

if marks_source(data, cfg.source_extensions(config)):
    marker_dir = os.path.join(root, ".claude")
    os.makedirs(marker_dir, exist_ok=True)
    open(os.path.join(marker_dir, ".build-dirty"), "w").close()

sys.exit(0)
