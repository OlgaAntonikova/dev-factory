---
description: Ship a reviewed, committed work branch - push, pull request into dev, wait for CI, merge when green
argument-hint: "[--no-merge]"
---

Ship the current work branch with `tools/ship.py` (`RULE-GIT`). Options: $ARGUMENTS

Preconditions - check each, and stop with the reason if one fails:

- The branch is a work branch (`feature/`, `fix/`, `chore/`, `docs/`, `refactor/`, `test/`,
  `ci/`), never `dev`, `main` or `master`.
- The working tree is clean and the change is committed - the commit passed the review gate.
- Local CI parity: the project's `build` and `test` from `.dev-factory.json` are green now.

Write the pull request body to a temporary file, as the `build-loop` skill ("Record", step 4)
requires: what changed, how it was verified (commands and their results), the `INV-n` /
`PHASE-n` it serves, any dependency added (package + version + why), and the reviewer's verdict.
Use a Conventional Commits title.

Run:

    python3 "${CLAUDE_PLUGIN_ROOT}/tools/ship.py" --root . --title "<title>" --body-file <file> $ARGUMENTS

Report the pull request URL and the CI result. On red, read the failing run
(`gh run view --log-failed`), and report it - the fix is a new reviewed commit on the same
branch, then `/dev-factory:ship` again, which reuses the open pull request.
