---
description: Open a phase of the spec - read its gate, post the plan, and wait for the go-ahead
argument-hint: "PHASE-n"
---

Open `$ARGUMENTS` from `docs/SPEC.md` using the `build-loop` skill ("Phase opening"). Read the
skill fully first.

- If no phase is given, find the first `PHASE-n` block not marked closed and propose it.
- If the previous phase is not closed, stop and say so: phases are never chained
  (`RULE-PHASE-GATE`).
- Read the phase's `Gate:` first, then every spec section it references.
- Post the plan: the tasks (one work branch each), the files each one touches, the tests that
  will prove the gate, and the open questions. Include the environment checklist if anything
  machine-level is needed.

Then **stop and wait for the owner's go-ahead.** Do not create a branch or write code in this
command. After approval, work task by task through the build loop; each task ends with
`/dev-factory:review` and `/dev-factory:ship`, and the phase ends with `/dev-factory:gate`.
