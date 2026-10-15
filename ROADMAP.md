# Roadmap

## v1.0 — the process, portable

| # | Piece | State |
|---|---|---|
| 1 | `CONTRACT.md` + rule identifiers | done |
| 2 | `.dev-factory.json` + stack-independent hooks | done — every guard proven by mutation |
| 3 | `spec-doctor` | done |
| 4 | Migrate the original project onto the contract | done — spec-doctor green, reviewed to APPROVE after one BLOCK |
| 5 | Move the skills and agents in | done — three skills, three agents, all stack-independent |
| 6 | `mutate.py` and `ship.py` | done — the plugin's own guards mutation-proven with `mutate.py` |

207 tests in total. Step 4 was not optional, and it earned its cost: the review of that
migration found five defects, three of which are recorded below because they change what the
plugin should do.

**Where the next mistakes get found.** On new applications: the plugin is exercised on each new
project, and what they break is what gets fixed.

---

## Lessons learned from the first migration

Three things the migration taught, at the cost of one BLOCK verdict. They are here rather than
in a commit message because each one changes what the plugin should do.

**A scan that reports completeness must say what it could not see.** The migration's own scan
matched `\brule \d\b` — case-sensitively, and in English. It therefore never saw `Rule 5` at
the start of a sentence, nor twenty-four rule citations in a part of the specification written
in another language, ten of which were live normative text. The decision record then stated a count and the
word "all". Both were wrong. Search case-insensitively, in every language the document uses, and
report the pattern alongside the number.

**`spec-doctor` cannot check prose, and should not pretend to.** It resolves dangling
*identifiers*: `D-999` cited and defined nowhere. It is blind to "the eight rules" after a split,
and to "forty-nine citations" after a miscount. That is a structural limit, not a gap to fill —
a checker that tried to validate claims in prose would be guessing. The answer is that
`RULE-REVIEW` is core: this class is caught by a reviewer or not at all.

**An instruction file that summarises the spec goes stale silently.** The reviewer agent opened
with "§2 (the eight rules)" and stayed true for nine phases. The migration made it false, and
nothing failed — a reviewer would simply have read the spec through a wrong map. The plugin's
agents now state no counts and quote no rules: they point at the spec and tell the reader to
count for themselves.

---

## v1.1 — the project grid

A persistent index of the repository, so that answering "where does X live" costs a lookup
instead of a sweep, and a review reads the three files that matter rather than the forty that
might.

### What it is

| Layer | Built by | Cost |
|---|---|---|
| Files, symbols, imports, **content hashes** | script, deterministic | ~free |
| One line of "what this file is for" | the model, only for files with no entry or a changed hash | amortised |
| Where every `RULE-*`, `INV-n`, `L-n`, `D-nnn` is defined and referenced | the same scan `spec-doctor` already needs | free |

If the model read every file to build the index, the first build would cost exactly what the
index is meant to save. The split above is what makes it economical.

### The two rules it cannot break

**An index is a cache of the code, and a stale cache is a lie about the code.** A reviewer
working from a lie approves broken things confidently. So every entry carries the hash of the
file it describes, and a consumer verifies the hash before trusting the entry. Staleness
becomes impossible to consume silently rather than merely unlikely.

**The grid is for navigation, never for judgment.** Finding *where* to look: the grid. Deciding
*whether the code is right*: the actual lines.

Not one real defect in the original project was visible at the level of "class X has method
Y" — not the test whose input never reached the branch it was named for, not a value drawn from
a constant while every caller used a different one, not the keyboard shortcut sitting
unreachable behind an earlier switch case. A review conducted over summaries finds only the defects a summary can express.

### When

After step 4, not before. The grid changes *how* review works, and changing the review at the
same time as migrating the project the review is being tested on puts two unknowns in one
equation.

`spec-doctor` (step 3) needs a full-repository scan anyway. That scanner is written to be the
grid's scanner too, so that it is not rewritten in two months.

### The condition it ships under

The entire justification is cost. So it is **measured, not assumed**: the token cost of one
review of the same diff, before the grid and after. Adding machinery on faith is the same
mistake as shipping a guard nobody has watched fail.

If the measurement does not show a saving, the grid does not ship.
