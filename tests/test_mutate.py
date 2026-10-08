"""tools/mutate.py: a mutant is judged by the tests, and the file always comes back byte for byte.

The project under test is a temporary directory holding one guarded function and a check that
fails when the guard is broken; the commands are this interpreter, so no toolchain is needed.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "mutate.py")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "hooks"))

import mutate  # noqa: E402

GUARD = "def allowed(age):\n    if age > 5:\n        return False\n    return True\n"
#: Fails when the guard no longer refuses an age of 6.
CHECK = [sys.executable, "-c", "import guard, sys; sys.exit(0 if guard.allowed(6) is False else 1)"]
PASSES = [sys.executable, "-c", "pass"]
SLOW = [sys.executable, "-c", "import time; time.sleep(30)"]


def sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class Project:
    def __init__(self, text=GUARD, newline="\n", config=None):
        self.text, self.newline, self.config = text, newline, config

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="devfactory-mutate-test-")
        self.path = os.path.join(self.dir, "guard.py")

        with open(self.path, "wb") as handle:
            handle.write(self.text.replace("\n", self.newline).encode("utf-8"))

        if self.config is not None:
            with open(os.path.join(self.dir, ".dev-factory.json"), "w", encoding="utf-8") as f:
                json.dump(self.config, f)

        return self

    def __exit__(self, *_):
        shutil.rmtree(self.dir, ignore_errors=True)


def run_tool(project, *pairs, command=CHECK, extra=()):
    argv = [sys.executable, TOOL, "--root", project.dir, "--file", "guard.py", *extra]

    for find, replace in pairs:
        argv += ["--find", find, "--replace", replace]

    if command is not None:
        argv += ["--", *command]

    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


class MutantVerdicts(unittest.TestCase):
    def test_a_mutant_the_tests_catch_is_killed(self):
        with Project() as project:
            before = sha(project.path)
            code, output = run_tool(project, ("if age > 5:", "if age > 6:"))
            self.assertEqual(code, 0, output)
            self.assertIn("KILLED", output)
            self.assertEqual(sha(project.path), before)

    def test_a_mutant_the_tests_miss_survives_and_fails_the_run(self):
        with Project() as project:
            before = sha(project.path)
            code, output = run_tool(project, ("if age > 5:", "if age > 5 and True:"))
            self.assertEqual(code, 1, output)
            self.assertIn("SURVIVED", output)
            self.assertEqual(sha(project.path), before)

    def test_each_mutant_runs_against_the_original(self):
        """The second mutant alone is caught; had the first stayed in, it would hide it."""
        with Project() as project:
            code, output = run_tool(
                project,
                ("if age > 5:", "if age > 5 and True:"),
                ("return False", "return True"),
            )
            self.assertEqual(code, 1, output)
            self.assertIn("1 killed, 1 survived", output)

    def test_a_timeout_counts_as_killed_and_still_restores(self):
        with Project() as project:
            before = sha(project.path)
            code, output = run_tool(project, ("return False", "return None"), command=SLOW,
                                    extra=("--timeout", "1"))
            self.assertEqual(code, 0, output)
            self.assertIn("timed out", output)
            self.assertEqual(sha(project.path), before)


class RefusedMutants(unittest.TestCase):
    def test_text_that_is_not_there_changes_nothing(self):
        with Project() as project:
            before = sha(project.path)
            code, output = run_tool(project, ("if age > 50:", "if True:"))
            self.assertEqual(code, 2, output)
            self.assertIn("not found", output)
            self.assertEqual(sha(project.path), before)

    def test_text_that_occurs_twice_changes_nothing(self):
        with Project(text=GUARD + GUARD) as project:
            before = sha(project.path)
            code, output = run_tool(project, ("if age > 5:", "if age > 6:"))
            self.assertEqual(code, 2, output)
            self.assertIn("2 times", output)
            self.assertEqual(sha(project.path), before)

    def test_a_replacement_equal_to_the_original_is_refused(self):
        with Project() as project:
            code, output = run_tool(project, ("return True", "return True"))
            self.assertEqual(code, 2, output)

    def test_unpaired_find_and_replace_are_refused(self):
        with Project() as project:
            argv = [sys.executable, TOOL, "--root", project.dir, "--file", "guard.py",
                    "--find", "a", "--find", "b", "--replace", "c", "--", *PASSES]
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 2)

    def test_without_a_command_or_a_config_nothing_runs(self):
        with Project() as project:
            before = sha(project.path)
            code, output = run_tool(project, ("return False", "return None"), command=None)
            self.assertEqual(code, 2, output)
            self.assertIn(".dev-factory.json", output)
            self.assertEqual(sha(project.path), before)


class TheProjectsCommand(unittest.TestCase):
    def test_without_a_command_the_declared_fast_test_runs(self):
        config = {"version": 1, "build": PASSES, "test": PASSES, "fastTest": CHECK}

        with Project(config=config) as project:
            code, output = run_tool(project, ("if age > 5:", "if age > 6:"), command=None)
            self.assertEqual(code, 0, output)
            self.assertIn("KILLED", output)

    def test_crlf_files_are_matched_across_lines_and_kept_crlf(self):
        with Project(newline="\r\n") as project:
            before = sha(project.path)
            code, output = run_tool(project, ("if age > 5:\n        return False", "pass"))
            self.assertEqual(code, 0, output)
            self.assertEqual(sha(project.path), before)


class RestoringIsUnconditional(unittest.TestCase):
    """The step the hand-written loop forgot: the original comes back even when the run itself
    blows up, and the copy is kept when it cannot be verified."""

    def test_an_exception_in_the_run_still_restores_the_file(self):
        with Project() as project:
            before = sha(project.path)

            def explode(argv, root, timeout):
                raise KeyboardInterrupt

            with self.assertRaises(KeyboardInterrupt):
                mutate.try_mutant(project.path, "return False", "return None", PASSES,
                                  project.dir, 10, run=explode, report=lambda _: None)
            self.assertEqual(sha(project.path), before)

    def test_the_file_is_mutated_while_the_command_runs(self):
        seen = []

        def look(argv, root, timeout):
            with open(os.path.join(root, "guard.py"), encoding="utf-8") as handle:
                seen.append(handle.read())
            return 1, ""

        with Project() as project:
            mutate.try_mutant(project.path, "return False", "return None", PASSES, project.dir,
                              10, run=look, report=lambda _: None)

        self.assertIn("return None", seen[0])
        self.assertNotIn("return False", seen[0])

    def test_a_restore_that_does_not_verify_keeps_the_copy(self):
        copies = []

        def corrupt(argv, root, timeout):
            return 1, ""

        with Project() as project:
            real_digest = mutate.digest
            calls = []

            def lying_digest(data):
                calls.append(data)
                return "x" * len(calls) if len(calls) > 1 else real_digest(data)

            mutate.digest = lying_digest
            try:
                with self.assertRaises(mutate.MutationError) as caught:
                    mutate.try_mutant(project.path, "return False", "return None", PASSES,
                                      project.dir, 10, run=corrupt, report=copies.append)
            finally:
                mutate.digest = real_digest

            copy = copies[0].split(": ", 1)[1]
            self.assertTrue(os.path.isfile(copy))
            self.assertIn(copy, str(caught.exception))
            shutil.rmtree(os.path.dirname(copy), ignore_errors=True)


class BytecodeCannotLie(unittest.TestCase):
    """A same-length mutant shares the original's size; a .pyc trusted by size and mtime in
    whole seconds would hide it, or keep running it after the restore."""

    def bytecode_env(self):
        env = dict(os.environ)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        return env

    def tool(self, project, *pairs):
        argv = [sys.executable, TOOL, "--root", project.dir, "--file", "guard.py"]
        for find, replace in pairs:
            argv += ["--find", find, "--replace", replace]
        argv += ["--", *CHECK]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                              env=self.bytecode_env())
        return proc.returncode, proc.stdout + proc.stderr

    def test_same_length_mutants_get_their_own_verdicts_with_bytecode_on(self):
        caught, missed = ("if age > 5:", "if age < 5:"), ("if age > 5:", "if age >=5:")

        with Project() as project:
            subprocess.run(CHECK, cwd=project.dir, env=self.bytecode_env(), check=True)
            code, output = self.tool(project, caught, missed, caught)
            self.assertEqual(code, 1, output)
            self.assertIn("2 killed, 1 survived", output)
            after = subprocess.run(CHECK, cwd=project.dir, env=self.bytecode_env())
            self.assertEqual(after.returncode, 0, "the restored source runs as the original")

    def test_the_command_runs_without_writing_bytecode(self):
        probe = [sys.executable, "-c",
                 "import os, sys; sys.exit(1 if os.environ.get('PYTHONDONTWRITEBYTECODE') else 0)"]

        inherited = os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
        try:
            with Project() as project:
                code, _ = mutate.run_command(probe, project.dir, 30)
        finally:
            if inherited is not None:
                os.environ["PYTHONDONTWRITEBYTECODE"] = inherited

        self.assertEqual(code, 1, "the tool sets it itself, whatever the caller's environment")

    def test_the_mutant_and_the_restored_file_are_each_newer_than_what_came_before(self):
        times = []

        def look(argv, root, timeout):
            times.append(os.stat(os.path.join(root, "guard.py")).st_mtime_ns)
            return 1, ""

        with Project() as project:
            before = os.stat(project.path).st_mtime_ns
            started = time.time_ns()
            mutate.try_mutant(project.path, "return False", "return None", PASSES, project.dir,
                              10, run=look, report=lambda _: None)
            after = os.stat(project.path).st_mtime_ns

        self.assertGreaterEqual(times[0] - before, 2_000_000_000)
        self.assertGreaterEqual(times[0], started)
        self.assertGreater(after, times[0])


#: A build that, like make or MSBuild, rebuilds its output only when the source is newer, then
#: tests the output. The output is executed from its text, not imported: an imported copy would
#: bring Python's own bytecode cache into a test about the build's timestamps.
TIMESTAMP_BUILD = [sys.executable, "-c", (
    "import os, shutil, sys\n"
    "src, out = 'guard.py', os.path.join('out', 'guard.py')\n"
    "if not os.path.exists(out) or os.stat(src).st_mtime_ns > os.stat(out).st_mtime_ns:\n"
    "    os.makedirs('out', exist_ok=True)\n"
    "    shutil.copyfile(src, out)\n"
    "built = {}\n"
    "exec(open(out, encoding='utf-8').read(), built)\n"
    "sys.exit(0 if built['allowed'](6) is False else 1)\n"
)]
HOUR_NS = 3_600_000_000_000


class TimestampBuilds(unittest.TestCase):
    """The mutant must reach the build, and must not stay in it after the restore."""

    def build(self, project):
        return subprocess.run(TIMESTAMP_BUILD, cwd=project.dir, capture_output=True).returncode

    def test_a_source_older_than_the_last_build_still_has_its_mutant_built(self):
        with Project() as project:
            hour_ago = time.time_ns() - HOUR_NS
            os.utime(project.path, ns=(hour_ago, hour_ago))
            self.assertEqual(self.build(project), 0)

            code, output = run_tool(project, ("if age > 5:", "if age < 5:"),
                                    command=TIMESTAMP_BUILD)

        self.assertEqual(code, 0, output)
        self.assertIn("KILLED", output)

    def test_after_a_killed_mutant_the_build_is_the_original_again(self):
        with Project() as project:
            self.assertEqual(self.build(project), 0)
            code, output = run_tool(project, ("if age > 5:", "if age < 5:"),
                                    command=TIMESTAMP_BUILD)
            self.assertEqual(code, 0, output)
            self.assertEqual(self.build(project), 0, "the mutant's output outlived the restore")


class FailingFiles(unittest.TestCase):
    def test_a_mutant_that_cannot_be_written_leaves_the_file_and_is_not_a_survivor(self):
        real = mutate.write_whole
        calls = []

        def refuse_first(path, data, times):
            calls.append(data)
            if len(calls) == 1:
                raise PermissionError("read-only")
            real(path, data, times)

        with Project() as project:
            before = sha(project.path)
            mutate.write_whole = refuse_first
            try:
                with self.assertRaises(mutate.MutationError) as caught:
                    mutate.try_mutant(project.path, "return False", "return None", PASSES,
                                      project.dir, 10, run=lambda *a: (1, ""),
                                      report=lambda _: None)
            finally:
                mutate.write_whole = real
            self.assertNotIsInstance(caught.exception, mutate.RestoreError)
            self.assertEqual(sha(project.path), before)

    def test_a_failed_restore_stops_the_run_with_status_2(self):
        real = mutate.write_whole
        calls = []

        def refuse_restore(path, data, times):
            calls.append(data)
            if len(calls) == 2:
                raise PermissionError("locked")
            real(path, data, times)

        printed = io.StringIO()

        with Project() as project:
            mutate.write_whole = refuse_restore
            try:
                with contextlib.redirect_stdout(printed):
                    code = mutate.main(["--root", project.dir, "--file", "guard.py",
                                    "--find", "return False", "--replace", "return None",
                                    "--find", "if age > 5:", "--replace", "if age > 6:",
                                        "--", *CHECK])
            finally:
                mutate.write_whole = real
                # The test put the lock there; it puts the original back itself.
                with open(project.path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(GUARD)

        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 2, "the second mutant must not start from a broken file")
        copy = printed.getvalue().split("copy of the original: ", 1)[1].splitlines()[0]
        self.assertTrue(os.path.isfile(copy), "the way back is kept when the restore fails")
        shutil.rmtree(os.path.dirname(copy), ignore_errors=True)

    def test_a_crlf_match_gets_a_crlf_replacement(self):
        seen = []

        def look(argv, root, timeout):
            with open(os.path.join(root, "guard.py"), "rb") as handle:
                seen.append(handle.read())
            return 1, ""

        with Project(newline="\r\n") as project:
            mutate.try_mutant(project.path, "if age > 5:\n        return False",
                              "if age > 6:\n        return False", PASSES, project.dir, 10,
                              run=look, report=lambda _: None)

        self.assertIn(b"if age > 6:\r\n        return False", seen[0])
        self.assertNotIn(b"6:\n", seen[0])


if __name__ == "__main__":
    unittest.main()
