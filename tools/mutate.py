#!/usr/bin/env python3
"""Mutation proof for one guard (RULE-MUTATION-PROOF): break the line, watch a test fail, and
always put the line back.

    python3 tools/mutate.py --root <repo> --file <path> --find <text> --replace <text>
        [--find <text> --replace <text> ...] [--timeout <seconds>] [-- <test command>]

Each --find/--replace pair is one mutant, tried on its own against the original file:

1. The text to find must occur exactly once in the file, or nothing is changed.
2. The file is rewritten with that one occurrence replaced, and the test command runs. Without
   a command after `--`, the project's `fastTest` (else `test`) from .dev-factory.json runs, the
   way the stop gate runs it (`python` resolves to the project's virtual environment).
3. The original bytes are written back whatever happened - a failing command, a timeout, an
   interrupt - each write replacing the file in one step, and the result is compared with the
   original by SHA-256. A copy waits in a temporary directory until that
   comparison passes, and its path is printed first, so a killed process still leaves the way
   back. A restore that fails stops the run.

Build tools and bytecode caches judge a file by its modification time. The mutant is stamped
newer than the original (by two seconds at least) and than anything built before the run; the
restored original newer than the mutant; and Python writes no bytecode during the run. So the
tests always see the mutant, and nothing built from it outlives the restore. The original's
modification time is deliberately not put back: its bytes are.

A mutant is KILLED when the command fails, SURVIVED when it passes. Exit status: 0 when every
mutant was killed, 1 when any survived, 2 when the tool could not run a mutant (bad text, no
command, a file it could not restore).

Built from the hand-written loop the first projects used (back up, `sed`, run, copy back,
`cmp`): the step people forget is the copy back, and the tool cannot.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))

import factory_config as cfg  # noqa: E402

DEFAULT_TIMEOUT_SECONDS = 900
#: How much of a command's output is shown for a surviving or failing mutant.
OUTPUT_TAIL_CHARS = 1200

#: How far each stamp lies past the newer of the clock and the previous stamp: beyond the
#: whole-second granularity of bytecode caches and of coarse file systems, which round a
#: smaller step away. The cost is a modification time up to a few seconds ahead of the clock.
MUTANT_MTIME_SHIFT_NS = 2_000_000_000

KILLED, SURVIVED, ERROR = "KILLED", "SURVIVED", "ERROR"


class MutationError(Exception):
    """A mutant that cannot be tried."""


class RestoreError(MutationError):
    """The original could not be put back. The run stops: the next mutant would start from it."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def locate(original: bytes, find: str) -> bytes:
    """The bytes to replace: `find` as given, or with CRLF line ends when the file uses them."""
    candidates = [find.encode("utf-8")]

    if "\n" in find and b"\r\n" in original:
        candidates.append(find.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))

    for needle in candidates:
        count = original.count(needle)

        if count == 1:
            return needle

        if count > 1:
            raise MutationError(f"the text occurs {count} times; make it unique")

    raise MutationError("the text was not found")


def test_command(root: str, given: list):
    if given:
        return given

    config, problem = cfg.load(root)

    if config is None:
        raise MutationError(f"no command after '--', and {problem}")

    return cfg.command(config, "fastTest", fallback="test")


def run_command(argv: list, root: str, timeout: float):
    """(exit code, output tail); a timeout counts as a failed run.

    Python writes no bytecode during the run: a cached `.pyc` is trusted by the source's mtime
    in whole seconds and its size, so a same-length mutant compiled and restored within one
    second would otherwise go on running from the cache after its source was put back.
    """
    resolved, env = cfg.project_command(root, argv)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    try:
        proc = subprocess.run(resolved, cwd=root, env=env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout} s"
    except OSError as error:
        raise MutationError(f"could not start the command: {error}") from None

    return proc.returncode, (proc.stdout + proc.stderr)[-OUTPUT_TAIL_CHARS:]


def write_whole(path: str, data: bytes, times_ns: tuple) -> None:
    """Replaces the file in one step (never truncated and half-written), keeping its mode, and
    sets its access and modification times."""
    descriptor, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".mutate-")

    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)

        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise

    os.utime(path, ns=times_ns)


def restore(path: str, original: bytes, times_ns: tuple, backup: str) -> None:
    """The original bytes back with the given times, verified by hash; RestoreError otherwise."""
    try:
        write_whole(path, original, times_ns)

        with open(path, "rb") as handle:
            restored = digest(handle.read()) == digest(original)
    except OSError as error:
        raise RestoreError(f"could not restore {path} ({error}); restore it from {backup}") from None

    if not restored:
        raise RestoreError(f"{path} differs from the original; restore it from {backup}")


def restored_times(mutant_time: int) -> tuple:
    """Access and modification time for the restored original: past both the clock and the
    mutant, whose own time lies ahead of the clock."""
    restored = max(time.time_ns(), mutant_time) + MUTANT_MTIME_SHIFT_NS
    return restored, restored


def try_mutant(path: str, find: str, replace: str, argv: list, root: str, timeout: float,
               run=run_command, report=print):
    """Runs one mutant and restores the file. Returns (outcome, detail)."""
    try:
        with open(path, "rb") as handle:
            original = handle.read()
        stat = os.stat(path)
    except OSError as error:
        raise MutationError(f"could not read {path}: {error}") from None

    needle = locate(original, find)
    replacement = replace.encode("utf-8")

    if b"\r\n" in needle:
        replacement = replace.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")

    mutated = original.replace(needle, replacement, 1)

    if mutated == original:
        raise MutationError("the replacement equals the original text")

    # Build tools rebuild what is older than its source (make, MSBuild, cargo); bytecode caches
    # trust a source whose time matches theirs to the second. So the mutant is stamped newer
    # than both the original and anything built before the run, and the restored original newer
    # than anything built from the mutant: each is rebuilt, and neither is mistaken for the
    # other. The original modification time is not put back - that would make the mutant's
    # outputs look newer than the restored source.
    mutant_time = max(time.time_ns(), stat.st_mtime_ns) + MUTANT_MTIME_SHIFT_NS

    try:
        backup_dir = tempfile.mkdtemp(prefix="devfactory-mutate-")
        backup = os.path.join(backup_dir, os.path.basename(path))
        shutil.copyfile(path, backup)
    except OSError as error:
        raise MutationError(f"could not keep a copy of {path}: {error}") from None

    report(f"  copy of the original: {backup}")

    try:
        write_whole(path, mutated, (mutant_time, mutant_time))
    except OSError as error:
        restore(path, original, restored_times(mutant_time), backup)
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise MutationError(f"could not write the mutant: {error}") from None

    try:
        code, tail = run(argv, root, timeout)
    finally:
        restore(path, original, restored_times(mutant_time), backup)
        shutil.rmtree(backup_dir, ignore_errors=True)

    if code == 0:
        return SURVIVED, tail

    return KILLED, f"exit {code}" if code is not None else tail


def main(arguments=None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    command = []

    if "--" in arguments:
        split = arguments.index("--")
        arguments, command = arguments[:split], arguments[split + 1:]

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=".", help="the repository the tests run in")
    parser.add_argument("--file", required=True, help="the file to mutate, relative to --root")
    parser.add_argument("--find", action="append", required=True, help="text to break")
    parser.add_argument("--replace", action="append", required=True, help="what it becomes")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    options = parser.parse_args(arguments)

    if len(options.find) != len(options.replace):
        parser.error("every --find needs its own --replace")

    root = os.path.abspath(options.root)
    path = os.path.join(root, options.file)

    try:
        argv = test_command(root, command)
    except MutationError as error:
        print(f"mutate: {error}", file=sys.stderr)
        return 2

    if not os.path.isfile(path):
        print(f"mutate: no file {options.file} under {root}", file=sys.stderr)
        return 2

    try:
        with open(path, "rb") as handle:
            before = digest(handle.read())
    except OSError as error:
        print(f"mutate: could not read {options.file}: {error}", file=sys.stderr)
        return 2

    outcomes = []

    for number, (find, replace) in enumerate(zip(options.find, options.replace), 1):
        print(f"mutant {number}: {find!r} -> {replace!r}")

        try:
            outcome, detail = try_mutant(path, find, replace, argv, root, options.timeout)
        except RestoreError as error:
            outcomes.append(ERROR)
            print(f"  {ERROR}: {error}\n  stopped: the file is not the original any more")
            break
        except MutationError as error:
            outcome, detail = ERROR, str(error)

        outcomes.append(outcome)
        print(f"  {outcome}: {detail.strip() if outcome != KILLED else detail}")

    try:
        with open(path, "rb") as handle:
            unchanged = digest(handle.read()) == before
    except OSError:
        unchanged = False

    print(f"{outcomes.count(KILLED)} killed, {outcomes.count(SURVIVED)} survived, "
          f"{outcomes.count(ERROR)} not run; {options.file} is "
          + ("unchanged" if unchanged else "CHANGED - restore it from the copy printed above"))

    if ERROR in outcomes or not unchanged:
        return 2

    return 1 if SURVIVED in outcomes else 0


if __name__ == "__main__":
    sys.exit(main())
