---
description: Close a phase - reproduce every gate criterion with the verifier, post the phase report, and stop for approval
argument-hint: "PHASE-n"
---

Close `$ARGUMENTS` (`RULE-PHASE-GATE`) using the `build-loop` skill ("Phase closing"). Read the
skill fully first.

1. Confirm every task of the phase is merged into `dev` and `dev` is pulled locally. An open
   pull request means the phase is not done; say which.
2. Run the `Gate:` criteria **literally**, including any real-device or real-environment step.
   For each claim the gate makes, start the `verifier` agent with the claim alone and record
   its verdict and evidence. Only `PASS` counts as passed: `INCONCLUSIVE` is not a pass, it is
   a check that did not run. A step you cannot run (a physical device, the owner's account)
   goes to the owner as a ready-to-run instruction - it is not reported as passed.
3. If any criterion is not `PASS`, stop and report: the phase stays open.
4. If all pass: mark the `PHASE-n` block closed **in place** in `docs/SPEC.md` with the date and
   the evidence (`CONTRACT.md`, "Phase shape"). That edit is a `docs/` work branch, reviewed and
   shipped like any other change.
5. Post the phase report: summary, the merged pull requests, `git diff --stat` of the phase
   against where `dev` started, what is now demonstrable, the `DECISIONS.md` delta, open
   questions.

Then **stop and wait for approval.** Never start the next phase from here. A release from `dev`
into `main`/`master` is the owner's.
