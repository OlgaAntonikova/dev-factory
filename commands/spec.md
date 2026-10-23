---
description: Write or restructure the project specification (docs/SPEC.md) with the spec-writing skill
argument-hint: "[the idea, or a path to an existing draft]"
---

Write the specification for this project using the `spec-writing` skill. Read the skill fully
first and follow its section order; do not work from memory of it.

Input: $ARGUMENTS

- **An idea, not a spec yet:** before writing, ask the owner concrete questions **one at a
  time** (`RULE-ASK`) until you know the worst failure mode, the scope of v1, the stack and its
  prohibitions, and what the first phase must demonstrate. Do not fill a gap with a default
  silently.
- **An existing draft:** restructure it into the shape the skill and `CONTRACT.md` require.
  Keep every identifier that already exists; identifiers are never renumbered or reused.
- **Nothing given:** ask what is being built.

The specification is written in English (`RULE-ENGLISH-ONLY`), whatever language the
conversation is in.

Finish by running `python3 "${CLAUDE_PLUGIN_ROOT}/tools/spec_doctor.py" .` and fixing what it
names. Then show the owner the worst failure mode, the phase list with each `Gate:`, and the
open `Q-n`, and stop: the spec is approved by the owner before `/dev-factory:phase` starts.
