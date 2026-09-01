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

anchor = cfg.project_root(data)

# The repository the EDIT belongs to, not the one the session happens to be anchored in - a
# session anchored outside the repository it edits (home directory, a workspace of several
# repos) would otherwise arm a gate that checks the wrong root, or none. Bash commands carry
# no file path, so they fall back to the anchor.
file_path = (data.get("tool_input") or {}).get("file_path") or ""
root = cfg.file_root(file_path, anchor) if data.get("tool_name") in EDIT_TOOLS else anchor

config, _ = cfg.load(root)  # a missing or broken config is the stop gate's business, not ours

# Only repositories are gated (a .git or a .dev-factory.json up the tree). An edit that
# belongs to no repository - a config file in the home directory - has no build to prove, and
# arming the gate for it produces an unsatisfiable block.
if cfg.is_repository(root) and marks_source(data, cfg.source_extensions(config)):
    cfg.add_dirty_root(anchor, root)

sys.exit(0)
