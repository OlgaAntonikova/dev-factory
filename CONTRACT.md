# The dev-factory contract

What every repository using this plugin must provide, and what the plugin's skills, hooks and
agents are therefore allowed to assume.

`spec-writing` **produces** this shape. `build-loop`, `review-checklist`, the hooks and the
agents **depend** on it. `spec-doctor` **checks** it. Nothing else in the plugin may assume
anything about a repository that is not written down here.

---

## Why identifiers and not section numbers

The first version of this process cited rules by position — "SPEC §2 rule 2" appeared ten
times across the hooks. Position is not identity. Insert a rule in the middle and every
citation silently points at the wrong thing, with nothing failing.

So: **every referenceable thing has an ID that appears literally in the text.** Sections may
be numbered, renumbered or unnumbered; the plugin never looks at a number. It greps for an ID.

The same reasoning makes the contract **language-neutral**. The prose of a spec may be in any
language — the first project's is in Russian — so the contract keys on ASCII identifiers and a
handful of English keyword anchors, never on headings.

---

## 1. Required files

| Path | Holds |
|---|---|
| `docs/SPEC.md` | The specification. The single source of truth for the project |
| `DECISIONS.md` | Choices the spec did not dictate, with the reasoning. `D-nnn` |
| `NEEDS_REVIEW.md` | Domain claims awaiting expert verification. `NR-n` |
| `.dev-factory.json` | The stack: how to build, how to test, what counts as source |
| `README.md` | How to build and run it, and the honest limitations table |

A repository missing any of these is not a dev-factory repository, and `spec-doctor` says so
by name rather than guessing.

---

## 2. Identifier families

Each of these appears **literally, in ASCII**, in the document that owns it.

| Family | Example | Lives in | Means |
|---|---|---|---|
| `RULE-*` | `RULE-INSTALLS` | `docs/SPEC.md` | A non-negotiable rule. Priority over everything |
| `INV-n` | `INV-3` | `docs/SPEC.md` | A machine-checkable invariant. Each has a test |
| `PHASE-n` | `PHASE-4` | `docs/SPEC.md` | One phase of work, ending at a gate |
| `L-n` | `L-12` | `docs/SPEC.md` + `README.md` | An honest limitation of v1 |
| `Q-n` | `Q-6` | `docs/SPEC.md` | An open question, closed in place when answered |
| `D-nnn` | `D-054` | `DECISIONS.md` | A decision and why |
| `NR-n` | `NR-1` | `NEEDS_REVIEW.md` | A claim nobody qualified has checked yet |

IDs are **never renumbered and never reused.** A rule that stops applying is struck through
where it stands, with the reason and the date. A closed question keeps its number.

---

## 3. Core rules

Every dev-factory spec carries all of these, each with its ID present literally. The wording
is the project's; the meaning is fixed here. Rules the hooks enforce are marked.

| ID | Meaning | Enforced |
|---|---|---|
| `RULE-INSTALLS` | The agent installs nothing — no SDK, tool, template, workload or package. It emits a checklist (program + exact version + install command) and waits. The first move in a project is that checklist, not code | hook |
| `RULE-GIT` | Each phase gets its own branch, `phase/<n>`. The agent works there: `add`, `commit`, `restore`, `stash`, and `push` **to that branch only**. It never commits on `main`/`master`, never pushes anywhere else, never force-pushes, never creates or edits a remote, never rewrites history, never bypasses hooks. Merging into the mainline is the owner's | hook |
| `RULE-DEPENDENCIES` | Dependency versions live in exactly one declared file and are added only after the owner approves a "package + version" list. Never through a package manager's install command | hook |
| `RULE-GREEN` | A session never ends with a broken build or red tests | hook |
| `RULE-PHASE-GATE` | Each phase leaves the project buildable and runnable, and ends with: summary, `git diff --stat`, what is now demonstrable, the `DECISIONS.md` delta, open questions — then **waits for approval**. Phases are never chained. A broken environment is reported, never designed around | skill |
| `RULE-REVIEW` | Every phase gate and every commit is preceded by a review the implementer did not perform. The reviewer starts from a clean context and reads the diff and the spec rather than the author's summary. The verdict is `APPROVE` or `BLOCK` with findings; a `BLOCK` stops the work until it is answered | hook + skill |
| `RULE-ASK` | A missing detail becomes one concrete question, not a silently chosen default. A choice the spec did not dictate is made, recorded in `DECISIONS.md`, and work continues. A domain fact that is not in the spec is never invented: it is marked in the code and raised in `NEEDS_REVIEW.md` | skill |
| `RULE-NEGATIVE-TEST` | Every validator has a test proving it rejects a bad input. A check that has never failed is not known to work | skill |
| `RULE-MUTATION-PROOF` | A guard test is not finished until the **specific line it is named for** has been mutated and the test observed to fail, with the failure quoted. A test named for a property it cannot observe is a defect, not coverage | skill |
| `RULE-NO-MAGIC-NUMBERS` | Every constant is named, and carries its unit in its name where it has one | skill |
| `RULE-BUILD-HYGIENE` | Warnings are errors and the formatter is authoritative. Neither is ever weakened to get green; a suppression carries a recorded reason | hook + skill |
| `RULE-BRIEF` | Reports are the changed files and a short summary, not a wall of text | — |

### Why `RULE-MUTATION-PROOF` is core

It is the most expensive lesson of the first project and the only one that would not survive
being left in a `DECISIONS.md`. Across three phases, eleven tests were found that were named
for behaviour they could not observe — an arc test on a straight wall, a print-mode test whose
palette painted the grid invisible either way, an assertion comparing a call with itself. Every
one of them was green. Every one of them was found only because somebody went looking.

Mutating the line a test is named for is the cheapest way to ask "would this notice?", and the
answer is written down rather than assumed.

### The phase branch

`RULE-GIT` opened as "the agent never pushes". That was the safe reading while there was one
branch and one person, and it stops being workable the moment a phase should arrive as a pull
request: a pull request needs a branch on the remote, and a rule that forbids all pushing
forbids that.

So the permission is scoped rather than removed. `phase/<n>` is the agent's; everything else is
not:

| | |
|---|---|
| `git push` while on `phase/<n>`, or naming it | allowed |
| Creating or switching to any branch | allowed - see below |
| `git push` naming any other branch, or from one | blocked |
| `git push --force`, `--force-with-lease`, `+refspec`, `--all`, `--mirror`, `--tags`, `--delete` | blocked |
| `git commit` while on `main` or `master` | blocked |
| `git branch -d/-D/-m`, `git remote`, `rebase`, `reset --hard`, `--amend` | blocked |

**The branch is `phase/<n>`, optionally with a readable suffix:** `phase/8` and
`phase/8-polish` both qualify, `phase/finish` does not. The number is required, because it is
what ties the branch to a `PHASE-n` block in the spec; the suffix exists so that a list of
branches can be read.

**Creating branches is not restricted.** Any branch may be created and switched to. That is
deliberate rather than an omission: a scratch branch is harmless, since it cannot be pushed and
a commit on it does not touch the mainline. Restricting creation would add a rule that prevents
nothing.

The teeth are on **commit** and **push**, not on navigation. Blocking `checkout` would be a rule
against moving around, easy to trip over and easy to work around; blocking a commit on the
mainline is the thing actually worth preventing, and there is only one way to do it.

`--force-with-lease` is blocked along with `--force`. It is the safer of the two, and it still
replaces a commit somebody may have already read - which is the owner's call, not the agent's.

### Why `RULE-REVIEW` is core, and what the hook can actually prove

The cycle that produced the first project was *gate, review, fix or approve, commit*. Until this
rule existed, none of that was in the contract: `RULE-PHASE-GATE` required a summary and a wait
for approval, and a repository could be built, gated and committed without anything ever being
reviewed while `spec-doctor` reported that the contract was satisfied.

Four things are load-bearing and none of them survives being left implicit:

- **A clean context.** A reviewer who has read the implementation session inherits its blind
  spots along with its confidence. In the first project, adversarial review with a fresh
  context arrived at Phase 6 and immediately found eleven tests that were named for behaviour
  they could not observe. Same-context review, over five earlier phases, had found none of them.
- **The summary is not evidence.** Claims are re-derived from the diff. The author of that
  project wrote four confidently false statements about their own tests, each of which survived
  until somebody re-ran the thing being claimed.
- **Two words, not a paragraph.** `APPROVE` or `BLOCK`. "Good overall, a few notes" is how
  findings get lost.
- **A `BLOCK` stops the work.** Otherwise the review is decoration.

**What `review_gate.py` proves, and what it does not.** The hook proves that a review verdict
exists and that it was stamped against *exactly the diff being committed* — approve one change
and commit another, and the hashes disagree. It cannot prove who produced the verdict: the
stamping command is available to whoever runs it. Independence is enforced by the skill and the
agent definition, not by the hook, and saying otherwise would be the same overclaim this
process exists to catch.

**Independence has degrees, too.** A subagent with a fresh context is independent of the
conversation but not of the model: it shares the same habits and priors. It catches "the author
has already convinced themselves". It does not catch a systematic blind spot in the model,
which is what the `verifier` role is for — reproducing a claim from the artifact rather than
reasoning about it.

---

## 4. Project-declared rules

A project adds these when they apply, with the same ID, and the plugin's skills pick them up if
present. They are not universal, so nothing fails when they are absent.

| ID | When it applies |
|---|---|
| `RULE-ONE-CONVERSION` | The project has units or coordinate spaces: one internal unit of truth, and each conversion between spaces exists in exactly one place |
| `RULE-COMMAND-STACK` | The project has undo, or needs an audit trail: every mutation of the model goes through one pathway, from the first one |
| `RULE-TEST-FIRST` | The spec names the hard classes whose tests are written before their implementation, and the test-first claim is backed by a quoted red run |

A project may add rules of its own with new IDs. It may not redefine a core ID to mean
something else.

---

## 5. Phase shape

Each `PHASE-n` block carries three parts. Two of them are anchored by **English keywords**, so
that a spec written in any language stays greppable:

```
### PHASE-4 — <title, any language>

- <what: prose, any language>
- Deliverables: <list>
- Gate: <the observable criterion that closes this phase>
```

`Gate:` is the load-bearing one. A gate must name something **observable** — a command that
runs, a file that exists, a measurement that can be reproduced. "The feature works" is not a
gate. The `verifier` agent exists to reproduce gate claims rather than believe them.

When a phase closes, its block is marked closed **in place**, with the date and the evidence.
The record of how a phase was actually closed is worth more than a tidy plan.

---

## 6. `.dev-factory.json`

The stack, declared by the project so that no hook has to know it. Specified in full in
`STACK.md`; the contract requires only that the file exists, parses, and names a build command
and a test command.

---

## 7. What `spec-doctor` checks

Mechanical, all of it. Every check names the file and the missing identifier.

1. The five required files exist at their paths.
2. `.dev-factory.json` parses, and its `build` and `test` commands actually run.
3. Every core `RULE-*` ID appears literally in `docs/SPEC.md`.
4. At least one `INV-n` and one `PHASE-n` appear in `docs/SPEC.md`.
5. Every `PHASE-n` block contains `Deliverables:` and `Gate:`.
6. No ID is defined twice, in any family.
7. Every ID **referenced** anywhere in the repository is **defined** somewhere in it — the
   check that would have caught a review skill pointing at a document that no longer existed.

Check 7 is the one that pays for the rest. Everything else confirms a shape; this one catches
the whole class of error where a component confidently cites something that is not there.
