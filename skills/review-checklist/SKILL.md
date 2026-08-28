---
name: review-checklist
description: Adversarial code-review procedure for changes produced against a written spec - what to check, in what order, and the verdict format. Use this skill whenever reviewing a diff, a pull request, a completed task or phase, when the user says "do a code review", "check this code", "review this", or before any merge to the mainline. The reviewer does not trust the author's claims; every claim is re-verified against the spec and the actual diff.
---

# Review Checklist

Stance: **do not trust the author's summary.** Review the diff against the spec, not against the author's description of the diff. Work top-down; stop early and BLOCK if a level-1 item fails.

Applicability rule: skip items whose subject does not exist in the project (no database → skip the query-plan item). Never invent findings to satisfy a checklist row — an inapplicable item is silently skipped, not reported.

## Level 1 — Contract violations (any hit = BLOCK)

1. **Forbidden dependencies** — none of the banned packages present, *including transitively* (check the lock/props files, not just direct references). Versions match the central version file; no version drifted. Explicit references to packages that also arrive transitively pin **exactly** the transitive version — no forced major bump over a transitive dependency.
2. **Architectural bans respected** — no banned patterns (e.g. generic repository, ORM where forbidden, a mutation bypassing a spec-mandated single pathway such as a command stack or an append-only log). If the spec has an ADR for it, the diff conforms to the ADR.
3. **Domain-fact hygiene** — every domain claim (grammar fact, formula, business rule, magic constant) either traces to the spec / an imported dataset, or is marked `TODO(expert)` and listed in `NEEDS_REVIEW.md`. An unverified domain fact silently shipped is the project's named worst failure mode.
4. **Generated content gate** — nothing generated (content, config variations, migrations produced by tooling) enters the mainline without an explicit accept artifact. No auto-merge.
5. **Reserved actions untouched** — if the project protocol reserves actions to the user (installs, git, deploys), the change was produced without the agent performing them; checklists and ready-to-run commands were handed over instead.
6. **Build hygiene intact** — warnings-as-errors and nullable settings untouched; no suppressions added without an ADR-grade justification in the diff.

## Level 2 — Spec conformance

7. The change implements the referenced spec section **as written**. Deviations are either (a) recorded in `DECISIONS.md`, or (b) a question that should have been asked. Silent defaults on ambiguous spec points are findings.
8. **Tests match the spec's matrices** — every row of the relevant input/verdict table is present as a test case; the standard edge rows (empty, whitespace, oversized, mixed-script) exist.
9. **Negative tests exist for validators** — the diff proves each new check can fail on a deliberately broken input.
10. **Named hard classes** meet their coverage bar; performance-critical paths keep their benchmark gates green (the numbers, not just "it ran").

## Level 3 — Data and correctness properties

11. **Non-trivial queries carry their plan** (projects with a database) — `EXPLAIN (ANALYZE, BUFFERS)` output in a comment above the query; the plan actually uses the intended index.
12. **Idempotency** where the spec requires it (client-generated IDs, retried imports): replaying the same operation is a no-op, and a test shows it.
13. **Transactionality** — multi-step imports/migrations are atomic; a mid-failure leaves no partial state.
14. **Determinism of tooling** — pinned versions, sorted output; re-running produces identical bytes.
15. **Unit and coordinate discipline** — the internal model uses the spec's canonical units (mm, cents, seconds); conversion to display/output units happens in exactly one designated place; no magic constants scattered through the code.

## Level 4 — Process and presentation

16. Commits: Conventional Commits, one logical change each; no drive-by refactors buried in a feature commit.
17. `DECISIONS.md` updated for every choice the spec did not dictate; `NEEDS_REVIEW.md` current.
18. If UI is touched — verify against the spec's UI constraint list. Mobile-first projects: tap targets ≥ 44 px, safe-area insets respected, keyboard does not cover inputs. Desktop canvas apps: zoom anchored to the cursor, correct DPI scaling, state changes re-render without recreating the document. Both: theming via variables, not hardcoded colors.
19. Docs that the spec designates as deliverables (ADRs, README sections, honest-limitations table) exist and are updated by the same diff, not "later".

## Verdict format

End every review with exactly one of:

- **APPROVE** — optionally with non-blocking nits, each tagged `nit:`.
- **BLOCK** — a numbered list of findings; each finding cites the checklist item number, the file/line, and the rule or invariant identifier it violates. No vague findings: "step 4 query has no EXPLAIN comment (item 11, `WordFormRepository.cs:41`, PLAN §7)" — not "SQL could be better documented".

A review that ends in prose without a verdict is not a review.
