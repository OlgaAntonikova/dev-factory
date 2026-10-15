# dev-factory

[![ci](https://github.com/OlgaAntonikova/dev-factory/actions/workflows/ci.yml/badge.svg?branch=dev)](https://github.com/OlgaAntonikova/dev-factory/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A [Claude Code](https://claude.com/claude-code) plugin: a development process as an installable plugin. The process lives here; the projects it is
used on stay clean of it.

- **Knowledge** in skills — how to write a spec, how to run a build loop, how to review.
- **Law** in hooks — rules the agent cannot talk itself out of, because they are code.
- **Roles** in agents — an adversarial reviewer, a cheap scout, a verifier that reproduces
  claims instead of believing them.

A new application is then: a new repository, this plugin installed, and a spec.

## Status

**0.3.0.** Extracted from a real project that ran nine phases under this process, and used on
the projects since. What exists:

| Piece | State |
|---|---|
| `CONTRACT.md` — what a repository must provide | written |
| `STACK.md` — the `.dev-factory.json` schema | written |
| `hooks/` | **written and stack-independent** — every guard proven by mutation |
| `tools/repo_scan.py` — one pass over a repository | **written**; also the project grid's scanner (see `ROADMAP.md`) |
| `tools/spec_doctor.py` — checks a repository against the contract | **written** |
| `tools/mutate.py` — mutation proof of one guard, file always restored | **written** |
| `tools/ship.py` — push, pull request, CI, merge, each command judged by the guard | **written** |
| `skills/` | **moved in** — `spec-writing`, `build-loop`, `review-checklist` |
| `agents/` | **moved in** — `reviewer` (opus), `scout` (haiku), `verifier` (opus) |
| Migration of the first project onto the contract | **done** — `spec-doctor` green, reviewed to APPROVE |

207 tests. The guards of the hooks have each been mutated and watched to fail, and so have those of the two newest tools, using `tools/mutate.py` on itself. See `ROADMAP.md`.

## Installing

In Claude Code:

```text
/plugin marketplace add OlgaAntonikova/dev-factory
/plugin install dev-factory@dev-factory
```

Then give each project a `.dev-factory.json` with its build and test commands (see
[`STACK.md`](STACK.md)). Until it exists, the stop gate blocks instead of guessing.

## Requirements

`python3` on `PATH`. The hooks are standard library only — a plugin that enforces
`RULE-INSTALLS` must not ask you to install anything to run it.

## Running it

Check a repository against the contract:

```bash
python3 tools/spec_doctor.py /path/to/project
```

Add `--no-run` to skip actually executing the project's build and test commands.

Prove a guard test (`RULE-MUTATION-PROOF`): each `--find`/`--replace` pair is one mutant, tried
alone against the original. Its bytes are restored and verified by hash; the mutant is stamped
newer than everything built before it and the restored file newer than the mutant, so make,
MSBuild and bytecode caches rebuild each, and Python writes no bytecode during the run. A restore
that fails stops the run and names the kept copy. Without
a command after `--`, the project's `fastTest` (else `test`) runs.

```bash
python3 tools/mutate.py --root /path/to/project --file src/app/limits.py \
    --find "if amount > limit:" --replace "if False:" \
    -- python -m pytest -q tests/test_limits.py
```

Ship a reviewed, committed work branch (`RULE-GIT`): push, open or reuse the pull request into
`dev`, wait for CI (reopening the pull request once if GitHub never starts the run), watch it,
merge when green, switch back and pull. The commit stays a plain `git commit`, where the review
gate checks the stamp. A tool's subprocesses are invisible to the guard hook, so `ship.py` shows
every command to `hooks/guard_bash.py` itself and runs only what it allows. The guard also
reads the session's anchor directory, which a tool cannot see: it is `--anchor`, else
`CLAUDE_PROJECT_DIR`, else `--root`. The merge names the commit that CI ran on
(`--match-head-commit`).

```bash
python3 tools/ship.py --root /path/to/project --title "feat: ..." --body-file pr.md
```

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

## License

[MIT](LICENSE)
