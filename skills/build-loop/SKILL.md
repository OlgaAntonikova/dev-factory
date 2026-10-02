---
name: build-loop
description: The test-first implementation cycle for executing a phase of a specced project - how to turn spec sections and gate criteria into tests, implement against them, and close a phase. Use this skill whenever implementing features from a written spec or plan, executing a project phase, when the user says "start the phase", "implement from the spec", "begin implementation", or whenever writing production code in a repo that has PLAN.md / spec documents. Also use it to decide what kinds of tests a given piece of code needs.
---

# Build Loop

The unit of work is a **phase** (from the spec). The unit of iteration is a **task** within a phase. Both follow the same shape: red → green → verify → record.

## Phase opening

1. Read the phase's **Gate** criteria first — the gate defines done; work backwards from it.
2. Read the referenced spec sections fully. If the spec says "implement §6 verbatim", the spec text is the test oracle.
3. Post a short plan: files to create/modify, order, open questions. Wait for go-ahead if the protocol requires it.
4. **Environment gate — before any code.** If the phase introduces tools, SDKs, templates, or packages: the first output is a checklist for the user (program/package + exact version + install command) and a package+version list for approval. Never install anything yourself — the user installs and confirms. Before adding an explicit reference to a package that already arrives transitively, discover the transitive version (`dotnet list package --include-transitive` or the ecosystem's equivalent) and pin exactly that version — never force a newer major over a transitive dependency.

## Per-task cycle

### 1. Red — derive tests from the spec, before implementation

- **Spec matrices become `[Theory]`/parameterized tests, row by row.** If the spec contains a table of input → expected verdict, every row is a test case with inline data. Add the standard edge rows the spec authors always forget: empty input, whitespace-only, oversized input, mixed encodings/scripts.
- **Test-first is mandatory only for the named hard classes** (the spec names them). For those, target branch coverage explicitly. For everything else, tests accompany the code but need not precede it.
- **Every validator gets a negative test**: prove the check *rejects* a deliberately broken input. A validate step that has never failed is not known to work.

### 2. Green — implement the minimum that passes

- Respect the spec's architectural bans (no forbidden packages — including transitively; no banned patterns). If tempted to add a dependency, re-read the contract first.
- **Log as you write, not afterwards** (`RULE-LOGGING`). Every new failure path logs where it is caught, at `ERROR`, with the operation and its context; a catch that neither logs nor re-raises is never written. Every external call (database, HTTP, bot API, file) logs its failure with enough context to reproduce it, and never its credentials. The logger and its redaction exist from Phase 0 - a feature is never built on an application that cannot yet report its own failures.
- Any decision the spec does not cover: make it, record it in `DECISIONS.md`, move on. Any *domain fact* the spec does not state: do not invent it — mark `TODO(expert)` and add to `NEEDS_REVIEW.md`.

### 3. Verify — the test taxonomy

Choose by layer; a class typically needs one or two of these, not all:

| Layer | Test kind | Bar |
|---|---|---|
| Value objects / parsers | Exhaustive unit tests | All enum branches; the type everything depends on is tested first |
| Pure algorithms | Property-based + golden-file | Invariants as properties ("stability never decreases on Good"); one pinned snapshot sequence for regression |
| Converters / round-trips | Property-based round-trip over the *full* dataset | `decode(encode(x)) == x` for every record, not a sample |
| Persistence / external systems | Integration on the **real** system (Testcontainers or equivalent), migrations applied, fixture seeded | **No mocks of the real system anywhere** |
| UI components (only if the spec includes UI in the automated-test boundary) | Component tests (bUnit, Avalonia.Headless, or equivalent) for the 1–2 interaction-critical components | Re-render correctness, not pixel perfection |
| Performance-critical paths | Benchmarks **as gates** | Explicit numbers from the spec: "zero allocations on the happy path", "< 1 µs" |

**Logging has tests of its own, whatever the stack** (`RULE-LOGGING`):

- **Redaction** — feed the logger a record carrying each secret on the spec's redaction list, and assert none of them comes out. This is a negative test in the `RULE-NEGATIVE-TEST` sense, and it is mutation-proven like any guard: disable the filter and watch the test fail.
- **Failure paths** — for each kind of handled failure (invalid input, external call down, unhandled exception reaching the top-level handler), one test triggers it and asserts an `ERROR` record with the operation name and the stack trace. A failure path whose log line nobody has seen is not known to log.
- **Startup** — one test asserts the startup record carries the version and the effective configuration, and contains no secret.

**The spec's testing boundary overrides this table.** If the spec excludes a layer from automated testing ("UI is not auto-tested"), do not invent tests for it, and skip rows whose subject does not exist in the project (no database → no persistence row). Concentrate effort where the spec names the hard classes.

Determinism rule for any tooling you build along the way: pinned model/tool versions, sorted output, idempotent re-runs, transactional imports. A pipeline that produces different bytes on re-run is broken even if each run is individually correct.

### 4. Record

- **A phase gets a branch: `phase/<n>`, created before the first commit of the phase.** A
  readable suffix is allowed - `phase/8-polish` - but the number is not optional. The mainline is never committed to directly; merging is the owner's. The agent may push that branch and nothing else (`RULE-GIT`).
- One logical change per commit, Conventional Commits. Who executes git is set by the project protocol: if the spec reserves git to the user, prepare the exact commands and the commit message instead of running them.
- Local CI parity before every commit: build → test → format/lint verify. If CI runs it, you run it locally first.

## Phase closing (gate protocol)

1. Run the gate criteria literally — including the physical-device / real-environment steps if the gate names them.
2. Post: summary + `git diff --stat` + **what is now demonstrable** + open questions + `DECISIONS.md` delta.
3. STOP and wait for confirmation. Never chain into the next phase.
4. On an environment blocker at any point: stop and report. **Never change the architecture to route around a broken environment.**

## Smells that mean the loop is broken

- A test written after the implementation for one of the named hard classes.
- A validator with no failing-input test.
- An integration test mocking the database.
- A benchmark that prints numbers but gates nothing.
- A `catch`/`except` that neither logs nor re-raises, or a failure that is only visible to the user.
- A `print` or `console.log` where the logger belongs.
- `DECISIONS.md` empty after a phase that involved any ambiguity.
