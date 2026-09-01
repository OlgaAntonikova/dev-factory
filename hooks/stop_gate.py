#!/usr/bin/env python3
"""Stop gate - RULE-GREEN.

Refuses to let a session end while the build is broken or the tests are red. Runs only when
source files were touched this session (marker set by mark_dirty.py), so a session spent
reading and answering ends instantly.

The build and test commands come from the project's .dev-factory.json. Nothing about any
particular stack appears here.

Contract: exit 0 = allow stopping; exit 2 = block, with stderr telling Claude what to fix.
stop_hook_active guards against looping on a gate that keeps blocking.
"""
import json
import os
import subprocess
import sys

import factory_config as cfg

TAIL_LINES = 40

try:
    data = json.load(sys.stdin)
except Exception:
    data = {}

# Claude is already continuing because this hook blocked once - do not loop.
if data.get("stop_hook_active"):
    sys.exit(0)

anchor = cfg.project_root(data)
roots = cfg.read_dirty_roots(anchor)

if not roots:
    sys.exit(0)  # nothing was edited, so there is nothing to prove


def block(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def run(argv, root):
    try:
        proc = subprocess.run(argv, cwd=root, capture_output=True, text=True)
    except (OSError, ValueError) as error:
        return None, f"{argv[0]} could not be started: {error}"

    return proc.returncode, (proc.stdout + "\n" + proc.stderr).strip()


def gate(argv, what, root):
    code, output = run(argv, root)
    printed = " ".join(argv)

    if code is None:
        block(
            f"STOP GATE: the {what} command could not run in {root}.\n{output}\n"
            f"Check the \"{what}\" entry in {cfg.CONFIG_NAME}."
        )

    if code != 0:
        tail = "\n".join(output.splitlines()[-TAIL_LINES:])

        block(
            f"STOP GATE: `{printed}` failed in {root} - the session cannot end with a "
            f"broken {what}.\n{tail}\n\nFix it, then finish again."
        )


# Every repository this session dirtied is gated; one session may edit several (the session
# that surfaced this edited the plugin and an application in the same sitting).
for root in roots:
    config, problem = cfg.load(root)

    if problem:
        # Deliberately a block and not a skip. A gate that cannot find its build command knows
        # nothing about the state of the project, and reporting nothing is indistinguishable
        # from reporting success. Source files were edited this session; something must prove
        # they still compile.
        hint = cfg.suggestion(root)

        block(
            f"STOP GATE: this session edited source files in {root} and that repository's "
            "stack is not declared.\n"
            f"{problem}\n\n"
            f"Write {cfg.CONFIG_NAME} in that repository's root:\n{cfg.STARTER}\n"
            + (f"\n{hint}\n" if hint else "")
            + "\nCommands are argv arrays, not shell strings. Ask the owner to confirm them, "
            "then finish again."
        )

    gate(cfg.command(config, "build"), "build", root)

    # fastTest exists for suites too slow to run on every stop. It is a narrower check by
    # definition, so the phase gate still runs the full suite - see RULE-PHASE-GATE.
    gate(cfg.command(config, "fastTest", fallback="test"), "test", root)

os.remove(cfg.dirty_registry(anchor))
sys.exit(0)
