---
description: Clean-context adversarial review of the staged change by the reviewer agent, then stamp the verdict for the review gate
argument-hint: "[PHASE-n or pull request number]"
---

Get an independent review of the current change (`RULE-REVIEW`). Context: $ARGUMENTS

1. **Stage exactly what will be committed** - named files with `git add <path>`, never
   `git add -A` or `git add .`, and the commit afterwards is a plain `git commit` (the gate
   refuses `-a`/`--all`, which would commit a diff other than the one reviewed). The review gate
   hashes the staged diff; anything staged after the verdict voids it. If nothing is staged and
   no pull request number was given, stop and say so.
2. **Start the `reviewer` agent.** Give it only what to review (the staged diff, or the pull
   request number) and which `PHASE-n` or `INV-n` the change serves. **Do not give it your
   summary of the change** - the reviewer reads the diff and the spec, not the author's
   account of them.
3. **Report its verdict verbatim.** When the review was of the staged diff, record it for the
   review gate (when it was of a pull request, stamp nothing: the stamp attests to the staged
   diff, and that is not what was reviewed):
   - `APPROVE`: `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/review_gate.py" --approve --reviewer reviewer`
   - `BLOCK`: `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/review_gate.py" --block --reviewer reviewer --findings "<numbered findings>"`

On `BLOCK`, stop. Fix nothing in this command: the owner sees the findings first, and every fix
is reviewed again.
