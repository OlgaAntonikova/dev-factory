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

The same reasoning keeps the contract keyed on ASCII identifiers and a handful of English
keyword anchors, never on headings. Identifier-keying is robust against any prose — and the
prose itself is governed by `RULE-ENGLISH-ONLY`: every repository artifact is written in
English. (The first project predates that rule and its spec is in Russian; it is the reason
the rule exists as a named core rule rather than an assumption.)

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
| `RULE-INSTALLS` | The project is the agent's, the machine is the owner's. The agent installs the project's dependencies into the project (its virtual environment, `node_modules`, package references). Anything that changes the machine for every project on it - a system package manager, an SDK or toolchain, a workload, a template, a global tool, a script piped into a shell - goes to the owner as a checklist (program + exact version + install command), and the agent waits | hook |
| `RULE-GIT` | The branch model in "The branch model" below. `main`/`master` holds releases and is the owner's: the agent never commits, pushes, merges or opens a pull request into it. `dev` is the integration branch and changes only through a merged pull request. All work happens on short-lived work branches created from `dev` (`feature/<n>-<slug>`, `fix/`, `chore/`, `docs/`, `refactor/`, `test/`, `ci/`), which the agent commits, pushes, opens as a pull request into `dev`, and merges once CI is green and the review is `APPROVE`. Never force-push, rewrite history, bypass hooks, or change remotes, tags, repository settings, secrets or releases | hook |
| `RULE-DEPENDENCIES` | Dependency versions live in exactly one declared file, pinned exactly. The agent may add or upgrade a dependency, through the project's one declared package manager only, provided the exact version lands in that file in the same change and the pull request lists it ("package + version + why"). A different package manager, or an install that escapes the project, is blocked | hook |
| `RULE-GREEN` | A session never ends with a broken build or red tests | hook |
| `RULE-PHASE-GATE` | A phase is a set of pull requests merged into `dev`. Each phase leaves `dev` buildable and runnable, and ends with: summary, the merged pull requests, `git diff --stat` of the phase against where `dev` started, what is now demonstrable, the `DECISIONS.md` delta, open questions — then **waits for approval**. Phases are never chained. A release (`dev` into `main`/`master`, tagged) is the owner's. A broken environment is reported, never designed around | skill |
| `RULE-REVIEW` | Every phase gate and every commit is preceded by a review the implementer did not perform. The reviewer starts from a clean context and reads the diff and the spec rather than the author's summary. The verdict is `APPROVE` or `BLOCK` with findings; a `BLOCK` stops the work until it is answered | hook + skill |
| `RULE-ASK` | A missing detail becomes one concrete question, not a silently chosen default. A choice the spec did not dictate is made, recorded in `DECISIONS.md`, and work continues. A domain fact that is not in the spec is never invented: it is marked in the code and raised in `NEEDS_REVIEW.md` | skill |
| `RULE-NEGATIVE-TEST` | Every validator has a test proving it rejects a bad input. A check that has never failed is not known to work | skill |
| `RULE-MUTATION-PROOF` | A guard test is not finished until the **specific line it is named for** has been mutated and the test observed to fail, with the failure quoted. A test named for a property it cannot observe is a defect, not coverage | skill |
| `RULE-NO-MAGIC-NUMBERS` | Every constant is named, and carries its unit in its name where it has one | skill |
| `RULE-BUILD-HYGIENE` | Warnings are errors and the formatter is authoritative. Neither is ever weakened to get green; a suppression carries a recorded reason | hook + skill |
| `RULE-BRIEF` | Reports are the changed files and a short summary, not a wall of text | — |
| `RULE-ENGLISH-ONLY` | Every repository artifact is written in English: the spec and all docs, code comments and docstrings, commit messages, branch names, configuration comments, decision and review records. The sole exception is user-facing localized content shipped as **data** (l10n resource files, seeded content). Conversation with the owner may be in any language; the repository may not | doctor + skill |
| `RULE-LOGGING` | Every application logs, from Phase 0, through one configured logger - never bare `print`/`console.log`. Every failure is logged where it is caught, at `ERROR` or above, with the operation, its context and the stack trace; an unhandled error is caught and logged by one top-level handler before the process or request dies. A catch that neither logs nor re-raises is a defect. Startup logs the version and the effective configuration with secrets redacted. **Secrets and personal data never reach a log** - tokens, keys, passwords, signed payloads and payment details are redacted by the logger itself, not by each caller. The spec names where logs go, their format and retention, and how an `ERROR` reaches a human. A library with no process of its own satisfies the rule by never swallowing an error and never logging a secret; the application that uses it owns the rest | skill |

### Why `RULE-MUTATION-PROOF` is core

It is the most expensive lesson of the first project and the only one that would not survive
being left in a `DECISIONS.md`. Across three phases, eleven tests were found that were named
for behaviour they could not observe — an arc test on a straight wall, a print-mode test whose
palette painted the grid invisible either way, an assertion comparing a call with itself. Every
one of them was green. Every one of them was found only because somebody went looking.

Mutating the line a test is named for is the cheapest way to ask "would this notice?", and the
answer is written down rather than assumed.

### Why `RULE-ENGLISH-ONLY` is core

A repository is shown to people. The first project shipped commits, docs and code comments in
Russian, and the owner's verdict was that none of it could be shown to anyone. The rule was
then stated once per project and forgotten once per project — which is exactly the kind of
lesson this contract exists to hold. So it is core: `spec-doctor` checks the required
documents for non-English text mechanically, and the reviewer blocks a diff that introduces it
anywhere else (comments, commit messages, config). The one thing the rule does **not** cover
is localized user-facing content shipped as data — a translation file is content, not an
artifact of the process.

### Why `RULE-LOGGING` is core

When something breaks in production, the log is the only witness. A failure that was caught
and swallowed leaves no trace at all, and the first person to learn of it is a user - days
later, with nothing to reproduce from. The owner's requirement is that the log says what broke
*at the moment it broke*, so logging is not something a project gets round to in its last
phase: it is set up in Phase 0, alongside the build, and every later phase is reviewed for it.

Four parts are load-bearing:

- **One logger, configured once.** Scattered `print` calls have no level, no timestamp and no
  destination, and cannot be redacted centrally.
- **No silent catch.** The swallowed exception is the one failure logging cannot recover after
  the fact. A handler that catches must log or re-raise; the reviewer blocks one that does
  neither.
- **A top-level handler.** Whatever slips past every local handler is still logged once, with
  its stack, before the request or process ends.
- **Redaction in the logger, not at the call site.** A rule that relies on every caller
  remembering to mask a token fails the first time one forgets, and a leaked token in a log
  file is a leaked token. The logger filters known secret fields and patterns itself, and a
  test feeds it a secret and proves it does not come out (`RULE-NEGATIVE-TEST`).

How an `ERROR` reaches a human - a chat alert, an e-mail, a dashboard - depends on the project,
so the contract requires only that the spec **names** the path; the mechanism is the project's.

`spec-doctor` checks that the rule is declared. Whether the code obeys it is the reviewer's and
the build loop's to establish: deciding mechanically, in an arbitrary language, whether a catch
block is silent would be guessing.

### The branch model

The process now runs the way a team repository does. It replaces `phase/<n>` branches, which
gave the agent one pushable branch per phase and left every merge to the owner - workable for a
single person, and a bottleneck the moment work should arrive as reviewed pull requests.

```
main/master  ----------------*-------------*-----   releases; the owner's
                             ^ release PR  ^
dev          --*---*---*-----+---*---*-----+-----   integration; pull requests only
                \   \   \
   feature/12-auth  fix/rate-cache  chore/ci           the agent's; one task each
```

| | |
|---|---|
| Creating or switching to any branch, `fetch`, `pull`, `clone`, `init` | allowed |
| `git commit`, `merge`, `cherry-pick`, `revert` on a work branch | allowed |
| The same on `main`, `master` or `dev` | blocked |
| `git push` of a work branch (`feature/`, `fix/`, `chore/`, `docs/`, `refactor/`, `test/`, `ci/` + a lowercase slug) | allowed |
| `git push` naming or standing on any other branch | blocked |
| `git push` with several refspecs | every destination is judged; one protected destination blocks the push |
| `git push --force`, `--force-with-lease`, `+refspec`, `:refspec` (remote delete), `--all`, `--mirror`, `--tags`, `--delete` - in any spelling git accepts (`-uf`, `--forc`) | blocked |
| `git branch -d/-D/-m` of a work branch | allowed; of a protected branch, blocked |
| `rebase`, `pull --rebase`, `reset --hard/--merge/--keep`, `--amend`, `--no-verify`, `-c core.hooksPath`, `filter-branch`, `update-ref` | blocked |
| `reset --soft` / `--mixed` | allowed: it rewrites only local commits, and publishing the result would need a force push, which is blocked |
| `git remote`, `git config`, `git tag` - reading (`git config user.name`, `git remote -v`, `git tag -l`) | allowed; writing, blocked |
| A variable in the arguments of a git write or any gh command (`git push $F`) | blocked: the guard cannot see what it holds |
| `gh pr create` / `edit` with `--base dev` | allowed; any other base, or none, blocked |
| `gh pr merge` into `dev` with every check green and at least one check | allowed |
| `gh pr merge` into anything else, with a red, pending or missing check, or with `--admin` | blocked |
| `gh pr` view/list/checks/diff/comment, `gh issue`, `gh run` view/watch/rerun, `gh auth status` | allowed |
| `gh repo create --private`, `gh repo view/clone/list`, `gh api` reads | allowed |
| `gh` repo settings, secrets, variables, releases, rulesets, keys, `auth token/login/logout`, `api` writes, public repositories | blocked |

**Why the merge is checked by the guard and not left to branch protection.** Branch protection
is a repository setting, and it is not available on every plan for private repositories. A rule
that holds only when somebody remembered to switch a setting on is not a rule. So the guard asks
GitHub for the pull request's base and checks before every `gh pr merge`, and refuses when
either is wrong - or when it cannot find out. Branch protection, where available, is a second
line, not the first.

**Why `--base` must be named.** Without it, `gh pr create` targets the repository's default
branch, which is the mainline. The one release path into the mainline is the owner's, so the
agent's pull requests name `dev` explicitly and the guard refuses those that do not.

**Why `rebase` stays blocked on work branches too.** A work branch is pushed as soon as it has a
pull request, and rebasing a pushed branch needs a force push. Bringing `dev` into a work branch
is `git merge origin/dev`; the squash merge into `dev` keeps its history linear anyway.

**Commands are read as commands.** The guard splits a command line into simple commands and
looks at each one's first word, after looking through what precedes it: environment
assignments, redirections, `if/then/do/!/{`, and wrappers that run their arguments (`env`,
`eval`, `timeout`, `xargs`, `nice`, `watch`). Quoted arguments and heredoc bodies are data - a
commit message or a grep pattern may mention `git push origin main` - unless a shell executes
them (`bash -c "..."`, `eval '...'`, `"$(...)"`, a heredoc fed to a shell on either side of the
`<<`), in which case they are read as commands of their own. A quoted plain word is read as the
word, so quoting a branch name does not hide it. A `git switch` or `cd` earlier on the same line
is followed, so `git switch dev && git commit` is judged on `dev`; a `cd` the guard cannot
follow makes a following commit or merge refuse.

### What the guard is, and is not

The guard is a **guardrail**: it stops ordinary commands from doing what the contract forbids,
whether by mistake, by habit or by a plausible-looking shortcut, and every rule in it is proven
by mutation. It is **not a sandbox**. A process that can run Python or write a script can run
anything, and no reading of a command line changes that; the first review of this guard found
many ways round it in an afternoon - each now a test in `BypassesFoundInReview` - and there
will be another.

So the guard is one layer of three, and none of them is optional:

1. **The guard** - catches the ordinary command, immediately, with a reason.
2. **Review** (`RULE-REVIEW`) - a clean-context reviewer reads the diff and the pull request,
   including anything that looks like a way round the rules.
3. **Server-side protection** - the only layer that holds against everything on the machine.
   Protect `main`/`master` and the integration branch on GitHub (require a pull request and
   green checks, block force pushes and deletion). On a private repository this needs a paid
   plan; until it is in place, the owner should know the mainline is protected by layers 1
   and 2 only, and say so in the project's README.

When the guard cannot judge a command that touches git, gh or a package manager - an internal
error, an answer from GitHub it does not recognise - it **blocks**. Anything else fails open, so
a bug in the guard never bricks an unrelated command.

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

Each `PHASE-n` block carries three parts, two of them anchored by the **`Deliverables:`** and
**`Gate:`** keywords (anchors are keyed on, never headings — and prose is English,
`RULE-ENGLISH-ONLY`):

```
### PHASE-4 — <title>

- <what: prose>
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
