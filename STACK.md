# `.dev-factory.json` — the stack declaration

One file in the repository root. It is the only place the plugin learns anything about the
project's technology, and the reason the same hooks serve a .NET desktop application and a
Node service without knowing what either is.

```json
{
  "version": 1,

  "build": ["dotnet", "build", "--nologo"],
  "test":  ["dotnet", "test", "--no-build"],
  "fastTest": ["dotnet", "test", "--no-build", "--filter-not-trait", "slow"],

  "packageManager": "dotnet",
  "dependencyFiles": ["Directory.Packages.props"],

  "sourceExtensions": [".cs", ".csproj", ".props", ".targets", ".sln", ".axaml"],

  "blockedCommands": [
    { "pattern": "dotnet\\s+list\\s+package\\s+--outdated", "reason": "version drift is the owner's call" }
  ]
}
```

## Which file applies

The Bash guard reads the file of the repository a command **acts in** - the shell's directory,
followed through every `cd` and `git -C` on the line - not the session's anchor. A
subdirectory's own `.dev-factory.json` (a monorepo package, a fixture) adds to the repository's
file, never replaces it. When a line touches several directories, the rules combine toward the
strictest: protected branches and `blockedCommands` are united across all of them and the
anchor, and a package manager must be permitted by every file governing the directory it runs
in. Stepping through a directory with a laxer file, or with none, can only add restrictions.

What this cannot see: a package manager aimed at another project by a path argument rather
than by the directory it runs in (`dotnet add <other>/App.csproj package X`, `mvn -f`,
`gradle -p`, `composer -d`, `uv pip --python <other venv>`), or pip installing into whichever
environment happens to be active. The common pointing options (`--prefix`, `--cwd`, `--dir`,
`--target`, `--project`, ...) are refused; the rest is the reviewer's to catch - the guard is a
guardrail, not a sandbox (CONTRACT.md, "What the guard is, and is not").

## Fields

| Field | Required | Used by | Meaning |
|---|---|---|---|
| `version` | yes | all | Schema version. `1` |
| `build` | yes | `stop_gate` | Argv array. Exit 0 means the project compiles |
| `test` | yes | `stop_gate`, gates | Argv array. Exit 0 means the suite is green |
| `fastTest` | no | `stop_gate` | Argv array for the end-of-session check when the full suite is slow. Falls back to `test` |
| `packageManager` | no | `guard_bash` | The one package manager this project is allowed to invoke |
| `dependencyFiles` | no | `review-checklist` | Files whose change means a dependency changed, and therefore must be named in the pull request |
| `sourceExtensions` | no | `mark_dirty` | What counts as source. Editing one of these arms the stop gate |
| `blockedCommands` | no | `guard_bash` | Extra project-specific bans. `pattern` is a Python regex, `reason` is shown to the agent |
| `integrationBranch` | no | `guard_bash` | The branch pull requests target and the agent merges into. Default `dev`. It may not be `main`/`master` or a work-branch name; an invalid value blocks every git and gh command until the owner fixes it |
| `notes` | no | nobody | Free text, ignored by every hook. See below |

**Argv arrays, not strings.** A command string has to be parsed by a shell, and the shell is
different on every machine this might run on. An argv array is passed to the process directly:
no quoting rules, no `&&`, no way for a path with a space to change the meaning.

## `notes`, because JSON has no comments

Hard-won knowledge about a command belongs beside the command. JSON offers nowhere to put it,
so there is a `notes` field that nothing reads.

The first project needed it on day one: `dotnet test --nologo` reports zero tests and exits 5
under Microsoft.Testing.Platform, so a gate that added `--nologo` for tidiness would block every
session with a false red. That sentence is worth more in this file than in anyone's memory.

## Commands are argv, so they cannot chain

If a project needs two commands to build, that is a script in the project, named in `build`.
The plugin does not get a shell, on purpose: a stop gate that can run arbitrary shell is a stop
gate that can be talked into running `true`.

## Missing file: the gate blocks, it does not skip

If source files were edited this session and `.dev-factory.json` is absent or unparseable, the
stop gate **blocks** and prints a starter template.

This is the deliberate choice, and it is the opposite of convenient. A missing config that
quietly disables the gate produces exactly the failure this whole process exists to prevent:
green that checked nothing. The first project spent three phases discovering tests that were
green because they observed nothing at all; a gate that is silently off is the same bug with a
larger blast radius.

The cost is one setup step in a new repository, and `spec-writing` emits the file as part of
the first phase, so in practice the block is only ever seen by a repository that skipped it.

## No autodetection

It is tempting to see a `.csproj` and infer `dotnet build`, or a `package.json` and infer
`npm test`. The plugin does not.

A guess is right often enough to be trusted and wrong often enough to matter: which of the
seven scripts in `package.json` is the test suite, which project in a monorepo, whether `test`
means unit tests or the ten-minute integration run. A gate that guesses wrong reports green
without having checked, which is worse than a gate that is absent, because it is believed.

Autodetection appears in exactly one place: as a **suggestion inside the error message** when
the file is missing. The agent proposes, the owner confirms, the file is written down.

## `packageManager` and what the guard actually blocks

The rules being enforced are `RULE-DEPENDENCIES` - one package manager, versions pinned in one
declared file - and `RULE-INSTALLS` - the project is the agent's, the machine is the owner's.
Neither is "package managers are scary".

| | Example | Verdict |
|---|---|---|
| Running the project's own tooling | `npm test`, `npm run build`, `dotnet build` | **allowed** |
| Restoring already-declared dependencies | `npm ci`, `dotnet restore`, `pip install -r requirements.txt` | **allowed** |
| Adding, removing or upgrading a dependency of the project | `npm install left-pad@1.3.0`, `dotnet add package X`, `pip install requests==2.32.3` | **allowed** - the exact version goes into the declared file in the same change |
| Installing for the whole machine | `npm i -g`, `pip install --user`, `cargo install`, `go install`, `pipx install`, `dotnet tool install -g`, `dotnet workload install` | **blocked** - the owner's checklist |

Any package manager that is **not** the declared `packageManager` is blocked outright. A .NET
project has no business running `pip`, and a Node project has none running `cargo`; if it turns
out it does, that is a conversation and a second entry, not a silent allowance.

The first version of this guard blocked `npm|pnpm|yarn|pip|cargo|gem` on sight. That was
correct for a repository that used none of them and would have made the plugin unusable in any
repository that used one — it would have blocked the project's own test command.

## What does *not* go in this file

Anything that is knowledge rather than configuration. The spec, the domain rules, the phase
plan, the versions themselves — those live in `docs/SPEC.md` and the project's dependency file.
This file answers one question: *what commands prove this project still works?*
