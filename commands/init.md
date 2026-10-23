---
description: Prepare a repository for dev-factory - declare its stack, create the required files, and check it against the contract
argument-hint: "[repository path, default: the current project]"
---

Prepare the repository at `$ARGUMENTS` (the current project if empty) for dev-factory.
`CONTRACT.md` in the plugin (`${CLAUDE_PLUGIN_ROOT}/CONTRACT.md`) lists what a repository must
provide; `${CLAUDE_PLUGIN_ROOT}/STACK.md` specifies `.dev-factory.json`. Read both before
writing anything.

1. **Look, do not guess.** List what the repository already has: which required files exist,
   which build files, test runners and package manager are present.
2. **Propose `.dev-factory.json`, then wait.** Nothing is autodetected: show the proposed
   `build`, `test`, `fastTest`, `packageManager`, `dependencyFiles` and `sourceExtensions`
   with the evidence for each, and ask the owner to confirm or correct it. Write the file only
   after confirmation. Anything you learnt that is easy to get wrong about a command goes into
   `notes`.
3. **Create the missing required files** as empty skeletons with their headings only -
   `DECISIONS.md`, `NEEDS_REVIEW.md`, and a `README.md` limitations table if the README has
   none. Never overwrite an existing file. Do not write `docs/SPEC.md` here: that is
   `/dev-factory:spec`.
4. **Check.** Run `python3 "${CLAUDE_PLUGIN_ROOT}/tools/spec_doctor.py" <repository>` and report
   what it names as missing. A missing spec is expected at this point; say that the next step is
   `/dev-factory:spec`.

Machine-level installs are the owner's (`RULE-INSTALLS`): if a declared command cannot run
because a program is missing, put it on a checklist (program + exact version + install command)
instead of installing it.
