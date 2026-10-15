---
name: spec-writing
description: How to write a complete project specification before any code is written — a three-document set (domain spec, technical plan, execution protocol) ready to hand to Claude Code. Use this skill whenever the user starts a new application or a large feature, asks to "write the spec", "draft the plan", "prepare the init prompt", drafts requirements, or is about to start building something that has no written spec yet. Also use it when reviewing or restructuring an existing spec.
---

# Spec Writing

A specification is not one document. It is three, with a strict separation of concerns:

| Document | Contains | Changes when |
|---|---|---|
| `DOMAIN.md` | What we build: domain model, content, invariants, scope. Stack-independent. | The product idea changes |
| `PLAN.md` | How we build it: stack with pinned versions, architecture, data schema, key algorithms, roadmap | Technical decisions change |
| `INIT_PROMPT.md` | Execution protocol for the agent: contract, rules, phases, gates, working protocol | The process changes |

The execution prompt references the other two ("read both fully before writing any code; they are the source of truth") and never duplicates their content.

**File count is a means, not the requirement.** For a small or mid-sized project all three parts may live in one file — what is mandatory is the sections themselves and their separation of concerns, with the contract and rules opening the execution part. Split into separate files when the domain part grows big enough to change independently of the stack (a curriculum, a content model, a rulebook).

## Required sections and the order to write them

### 1. Worst failure mode — write this first

One sentence naming the single worst thing this project could ship. It becomes **non-negotiable rule #1** with a concrete mitigation mechanism.

> Example: "Silently shipped wrong grammar is the worst failure mode." → Rule: any domain claim not present in the spec or an imported dataset must be marked `// TODO(expert): verify` and listed in `NEEDS_REVIEW.md`.

Every other rule in the spec is negotiable; this one is not. If you cannot name the worst failure mode, you do not understand the project yet — stop and figure it out.

### 2. Contract: stack, versions, prohibitions

- Stack listed with **exact pinned versions**, marked "fixed, do not substitute".
- **Known version conflicts documented in advance**, with the reasoning spelled out. If two plausible packages cannot coexist, say which one wins and why, and forbid the loser explicitly ("do not add it, do not suggest it, do not pull it transitively").
- **Forbidden dependencies with named substitutes** (licence-driven or policy-driven): "MediatR → hand-rolled dispatcher; FluentAssertions → Shouldly". A ban without a substitute invites workarounds.

### 3. Non-negotiable rules (**named**, never numbered)

Short, testable, and each carrying an identifier that appears literally in the text:
`RULE-INSTALLS`, not "rule 2".

**Numbering is the one mistake this section used to make.** The first project under this
process cited its rules by position; when the rules were later renamed, seventy-odd citations
across the repository — most of them in code comments, in two languages — silently pointed at
the wrong thing, and nothing failed. A name survives insertion, renumbering and translation.

Every spec carries the **core rules listed in `CONTRACT.md` §3**, each by identifier. Their
wording is yours; their meaning is fixed there. Reproduce them all — `spec-doctor` checks that
each one appears, and a spec missing `RULE-REVIEW` describes a project where nothing is
reviewed.

Add project-declared rules with new identifiers where the project needs them
(`CONTRACT.md` §4 has three common ones: `RULE-ONE-CONVERSION` for anything with units or
coordinate spaces, `RULE-COMMAND-STACK` for anything with undo, `RULE-TEST-FIRST` for a project
that names its hard classes). Never redefine a core identifier to mean something else.

Two things a rule section must not do:

- **Do not state how many rules there are.** "Eight rules" ages into a lie the first time one
  splits, and nothing checks a sentence.
- **Do not let one rule do two jobs.** Four of the first project's eight turned out to be two
  rules each, visible only once each needed a name of its own. If a rule's title needs an "and",
  it is two rules.

Beyond the core set, adapt:

1. The worst-failure-mode rule (from §1) — this one usually earns its own identifier.
2. **Vertical slices** — every phase ends with something runnable and demonstrable, not a half-built layer.
3. **Stop at each phase gate** — summary + `git diff --stat` + what is demonstrable + open questions, then WAIT for confirmation. Never chain phases.
4. **Test-first for named hard classes** — name the 1–3 genuinely hard classes explicitly and require the highest coverage bar there specifically. Do not demand 100% everywhere; demand it where it matters.
5. **Ask, don't assume** — a missing spec detail produces one concrete question, not a silently picked default.
6. Showcase requirements, if the project is a portfolio piece — encode them ("every non-trivial SQL query gets its `EXPLAIN (ANALYZE, BUFFERS)` plan pasted in a comment above it").

### 3a. The shapes everything else depends on

`CONTRACT.md` is the full statement; these are the parts the spec itself must carry, because
every other component greps for them:

| Family | Written as | Defined by |
|---|---|---|
| Rules | `RULE-NAME` | a heading, or the first cell of a table row |
| Invariants | `INV-n` | same |
| Phases | `PHASE-n` | a heading |
| Limitations | `L-n` | a table row, echoed in the README |
| Open questions | `Q-n` | a table row, closed in place |

All prose is English — `RULE-ENGLISH-ONLY` is core and covers every repository artifact: the
spec, decision and review records, README, code comments and docstrings, commit messages,
config comments. The sole exception is user-facing localized content shipped as data (l10n
files, seeded content). Identifiers are ASCII and each phase block carries the anchors
**`Deliverables:`** and **`Gate:`**, because the plugin greps for anchors and identifiers,
never headings.

Identifiers are never renumbered and never reused. A retired rule is struck through where it
stands, with the reason and the date.

### 3b. `.dev-factory.json`

Phase 0 produces it, before any hook needs it: the build command, the test command, and what
counts as source. `STACK.md` has the schema. Without it the stop gate blocks rather than
skipping, which is deliberate — a gate that cannot find its build command knows nothing about
the project, and saying nothing is indistinguishable from saying it is fine.

### 3c. Logging — where failures become visible

`RULE-LOGGING` is core, and the rule alone does not say enough to build from. The spec answers,
concretely:

- **Library and setup** — the one logger (pinned like any dependency), configured once at
  startup. Bare `print`/`console.log` is not logging.
- **Destination and format** — where logs go in development and in production (stdout for a
  container platform, a rotating file, a hosted collector), structured (JSON) or plain, and the
  retention.
- **Levels** — what is `INFO` (startup, each request or job with its outcome and duration),
  `WARNING` (rejected input, retries) and `ERROR` (every caught failure, with operation, context
  and stack).
- **Correlation** — a request or job id carried through every line, so one failure can be
  followed end to end.
- **Redaction list** — the named secret fields and patterns (tokens, keys, passwords, signed
  payloads, payment and personal data) the logger strips itself.
- **Alerting** — how an `ERROR` reaches a human, and how fast. "Someone reads the log" is not an
  answer; a chat message, an e-mail or a monitored dashboard is.

Phase 0 delivers this, with its tests: the logger exists before any feature that could fail.

### 4. Machine-checkable invariants

Turn methodology claims into CI checks. For every "principle" in the domain doc, ask: *can a validator reject a violation?* If yes, specify the check and require a test proving the validator rejects a deliberately broken input.

> Example: "spiral curriculum" became: *a sentence in unit N may only use lemmas with `unit_index ≤ N`* — enforced by `ContentSpiralTests` in CI.

An invariant that cannot be checked is a wish, not an invariant.

### 5. Phases with gates

Each phase: **Deliverables** (concrete files/artifacts) + **Gate** (an observable behaviour, not a feeling). Gate criteria must be executable by a human in minutes:

> "On a physical iPhone, added to the home screen: complete a full session, toggle script mid-session, see everything re-render."

Order phases so that something real is demonstrable early (a key milestone by roughly phase 3, not at the end). Mark the key milestone explicitly.

### 6. Human-gate for generated content

Anything generated (content, config variations, migrations) lands in a `proposed/` area and enters the mainline only via an explicit accept step (`accept --file … --confirm`). **Never auto-merge generated artifacts.** State this in the spec.

### 7. Honest limitations

A table of known limitations with severity and workaround, destined for the README. A named limitation reads as professionalism; a masked one reads as a bug.

### 8. Decisions left open

A closing section "Decide before starting": each open question with options and a recommendation. The spec is not done while an open question has no recommendation attached.

### 9. Effort estimates with known-bias corrections

Estimate per phase, and flag the categories that are systematically underestimated (content/data work: multiply your gut estimate by 3; state that in the doc).

## Working-protocol block (goes into INIT_PROMPT.md)

- Conventional Commits, one logical change per commit.
- A running `DECISIONS.md` for anything the agent decided that the spec did not specify.
- On an environment blocker: stop and report; **never change the architecture to route around a broken environment**.
- Before each phase: post a short plan (files to create/modify) and wait for go-ahead.

## Before handing it over

Run `spec-doctor` on the repository. It checks the shapes above mechanically and names what is
missing. It does **not** check prose: a sentence claiming eight rules, or forty-nine citations,
is invisible to it. That class is caught by review, which is why `RULE-REVIEW` is core.

## Quality bar

A spec is ready when a competent engineer (human or agent) who has never spoken to you can start phase 0 without asking a single question that the spec should have answered — and when every question they *do* ask is genuinely new information for you too.
