# dev-factory

A development process as an installable plugin. The process lives here; the projects it is
used on stay clean of it.

- **Knowledge** in skills — how to write a spec, how to run a build loop, how to review.
- **Law** in hooks — rules the agent cannot talk itself out of, because they are code.
- **Roles** in agents — an adversarial reviewer, a cheap scout, a verifier that reproduces
  claims instead of believing them.

A new application is then: a new repository, this plugin installed, and a spec.

## Status

**0.2.1 — under construction.** Extracted from a first project that ran nine phases under this
process. What exists so far:

| Piece | State |
|---|---|
| `CONTRACT.md` — what a repository must provide | written |
| `STACK.md` — the `.dev-factory.json` schema | written |
| `hooks/` | **written and stack-independent** — every guard proven by mutation |
| `tools/repo_scan.py` — one pass over a repository | **written**; also the project grid's scanner (see `ROADMAP.md`) |
| `tools/spec_doctor.py` — checks a repository against the contract | **written** |
| `skills/` | **moved in** — `spec-writing`, `build-loop`, `review-checklist` |
| `agents/` | **moved in** — `reviewer` (opus), `scout` (haiku), `verifier` (opus) |
| Migration of the first project onto the contract | **done** — `spec-doctor` green, reviewed to APPROVE |

140 tests, and every guard in them has been mutated and watched to fail. See `ROADMAP.md`.

## Requirements

`python3` on `PATH`. The hooks are standard library only — a plugin that enforces
`RULE-INSTALLS` must not ask you to install anything to run it.

## Running it

Check a repository against the contract:

```bash
python3 tools/spec_doctor.py /path/to/project
```

Add `--no-run` to skip actually executing the project's build and test commands.

Test the plugin itself:

```bash
python3 -m unittest discover -s tests
```

## `CONTRACT.md` does not apply to this repository

Running `spec-doctor` on the plugin reports three missing files, and that is correct rather
than embarrassing: the contract describes a **spec-driven application** — one with a domain
specification, numbered phases and gates. The plugin has none of those. It is a tool.

What the plugin does hold itself to is its own `.dev-factory.json`: the build compiles and the
tests are green, checked by the same stop gate it ships.

## The two documents that matter

**[`CONTRACT.md`](CONTRACT.md)** — the shape every repository must have, and therefore the only
thing the plugin is allowed to assume about one. Rules, invariants, phases and limitations are
addressed by **identifier**, never by section number, and the identifiers are ASCII so that a
spec written in any language stays greppable.

**[`STACK.md`](STACK.md)** — how a repository declares its build and test commands, so that no
hook has to know whether it is looking at a .NET desktop application or a Node service.

## Two design choices worth knowing before reading further

**A missing stack declaration blocks the gate; it does not skip it.** A gate that is silently
off is worse than one that is absent, because it is believed.

**Nothing is autodetected.** Guessing which of seven npm scripts is the test suite is right
often enough to be trusted and wrong often enough to matter. The plugin proposes; the owner
confirms; the answer is written down.

## Installing it in a project

The project keeps its own layer: `CLAUDE.md`, the spec, domain-specific skills, concrete
versions, and `.dev-factory.json`.

**Remove the project's local copies of anything the plugin now provides.** A repository that
carries both its own `.claude/agents/reviewer.md` and the plugin's has two sources of truth for
the same role, and they will drift — which is the failure this plugin exists to remove, not to
reproduce.

Project-specific knowledge that used to live in a local agent belongs in the `notes` field of
`.dev-factory.json`: which flag turns a test run into a silent no-op, which build mode skips a
compilation step. The plugin's agents are told to read it.

## Release hygiene: the version is the cache key

Installed plugins are cached per version under `~/.claude/plugins/cache`. A development
install runs straight from this repository, and nothing refreshes the cached copy of the
*same* version when source files change — the law that runs and the copy people read then
silently disagree. This happened: a reviewer cited an encoding bug in the cached
`review_gate.py` that the source had already fixed.

So: **any change under `hooks/`, `tools/`, `skills/` or `agents/` bumps the version in
`.claude-plugin/plugin.json`** — that is what makes the cache refresh on reinstall. The
`cache_parity.py` SessionStart hook is the tripwire for the sessions in between: it compares
the running plugin root with the cache entry of the same version and names every file that
disagrees. It warns; it never blocks.

## Not part of the plugin

The project layer stays in the project repository: `CLAUDE.md`, the spec itself, domain-specific
skills, concrete stack versions, end-to-end configuration.
