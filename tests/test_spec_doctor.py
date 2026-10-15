#!/usr/bin/env python3
"""Tests for spec-doctor and the scanner underneath it.

Each test builds a repository that satisfies the contract, breaks exactly one thing, and checks
that the doctor names it. A checker nobody has watched fail is decorative - and this one exists
precisely to catch things that look fine.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, TOOLS)

import repo_scan  # noqa: E402
import spec_doctor  # noqa: E402

OK_COMMAND = [sys.executable, "-c", "pass"]
FAILING_COMMAND = [sys.executable, "-c", "import sys; sys.exit(1)"]


def spec_text(rules=None, phases=("PHASE-0",), gate=True, deliverables=True):
    rules = spec_doctor.CORE_RULES if rules is None else rules

    lines = ["# Specification", "", "## Rules", "", "| ID | Meaning |", "|---|---|"]
    lines += [f"| {rule} | what it means |" for rule in rules]
    lines += ["", "## Invariants", "", "| # | Invariant |", "|---|---|",
              "| **INV-1** | something a test can check |", ""]

    for phase in phases:
        lines += [f"### {phase} - a title in any language", "", "- What: prose"]

        if deliverables:
            lines.append("- Deliverables: a thing")

        if gate:
            lines.append("- Gate: a command that runs")

        lines.append("")

    return "\n".join(lines)


class Repo:
    """A repository that satisfies CONTRACT.md, until a test breaks one thing in it."""

    def __init__(self, **overrides):
        self.overrides = overrides

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="specdoctor-")
        os.makedirs(os.path.join(self.dir, "docs"))

        files = {
            "docs/SPEC.md": spec_text(),
            "DECISIONS.md": "# Decisions\n\n### D-001 - the first one\n\nBecause.\n",
            "NEEDS_REVIEW.md": "# Needs review\n\n### NR-1 - an unchecked claim\n",
            "README.md": "# A project\n",
            ".dev-factory.json": json.dumps(
                {"version": 1, "build": OK_COMMAND, "test": OK_COMMAND}),
        }
        files.update(self.overrides)

        for relative, content in files.items():
            if content is None:
                continue  # a test removing a required file

            path = os.path.join(self.dir, relative)
            os.makedirs(os.path.dirname(path), exist_ok=True)

            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)

        return self.dir

    def __exit__(self, *_):
        shutil.rmtree(self.dir, ignore_errors=True)


def doctor(root, *extra):
    proc = subprocess.run(
        [sys.executable, os.path.join(TOOLS, "spec_doctor.py"), root, *extra],
        capture_output=True, text=True,
    )

    return proc.returncode, proc.stdout + proc.stderr


class AConformingRepository(unittest.TestCase):
    def test_passes(self):
        with Repo() as root:
            code, output = doctor(root)

        self.assertEqual(0, code, output)
        self.assertIn("satisfies CONTRACT.md", output)

    def test_running_the_declared_commands_is_part_of_it(self):
        with Repo() as root:
            _, output = doctor(root)

        self.assertIn("build command", output)
        self.assertIn("test command", output)

    def test_python_commands_run_inside_the_projects_virtual_environment(self):
        inside = ["python", "-c", "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)"]
        config = json.dumps({"version": 1, "build": OK_COMMAND, "test": inside})

        with Repo(**{".dev-factory.json": config}) as root:
            subprocess.run([sys.executable, "-m", "venv", "--without-pip",
                            os.path.join(root, ".venv")], check=True, capture_output=True)
            code, output = doctor(root)

        self.assertEqual(0, code, output)


class MissingPieces(unittest.TestCase):
    def test_each_required_file_is_named_when_absent(self):
        for missing in ("docs/SPEC.md", "DECISIONS.md", "NEEDS_REVIEW.md",
                        "README.md", ".dev-factory.json"):
            with Repo(**{missing: None}) as root:
                code, output = doctor(root, "--no-run")

            self.assertEqual(1, code, missing)
            self.assertIn(missing, output)

    def test_a_missing_core_rule_is_named(self):
        without_git = [r for r in spec_doctor.CORE_RULES if r != "RULE-GIT"]

        with Repo(**{"docs/SPEC.md": spec_text(rules=without_git)}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("RULE-GIT", output)

    def test_a_phase_without_a_gate_is_named(self):
        with Repo(**{"docs/SPEC.md": spec_text(gate=False)}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("PHASE-0", output)
        self.assertIn("Gate:", output)

    def test_a_phase_without_deliverables_is_named(self):
        with Repo(**{"docs/SPEC.md": spec_text(deliverables=False)}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("Deliverables:", output)

    def test_a_spec_with_no_phases_at_all_fails(self):
        with Repo(**{"docs/SPEC.md": spec_text(phases=())}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("PHASE", output)


class Logging(unittest.TestCase):
    def test_a_missing_logging_rule_is_named(self):
        without = [r for r in spec_doctor.CORE_RULES if r != "RULE-LOGGING"]

        with Repo(**{"docs/SPEC.md": spec_text(rules=without)}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("RULE-LOGGING", output)


class EnglishOnly(unittest.TestCase):
    def test_a_missing_english_only_rule_is_named(self):
        without = [r for r in spec_doctor.CORE_RULES if r != "RULE-ENGLISH-ONLY"]

        with Repo(**{"docs/SPEC.md": spec_text(rules=without)}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("RULE-ENGLISH-ONLY", output)

    def test_non_english_text_in_the_spec_is_named_with_its_line(self):
        spec = spec_text() + "\nЭто предложение нарушает правило.\n"

        with Repo(**{"docs/SPEC.md": spec}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("docs/SPEC.md:", output)
        self.assertIn("RULE-ENGLISH-ONLY", output)

    def test_non_english_text_in_a_decision_record_is_caught(self):
        decisions = "# Decisions\n\n### D-001 - the first one\n\nПотому что так решили.\n"

        with Repo(**{"DECISIONS.md": decisions}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("DECISIONS.md:", output)

    def test_an_all_english_repository_passes(self):
        with Repo() as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(0, code, output)
        self.assertIn("english only", output)


class TheStackDeclaration(unittest.TestCase):
    def test_a_failing_build_is_reported(self):
        config = json.dumps({"version": 1, "build": FAILING_COMMAND, "test": OK_COMMAND})

        with Repo(**{".dev-factory.json": config}) as root:
            code, output = doctor(root)

        self.assertEqual(1, code)
        self.assertIn("build command", output)

    def test_a_command_that_is_not_argv_is_reported(self):
        config = json.dumps({"version": 1, "build": "make && ./run", "test": OK_COMMAND})

        with Repo(**{".dev-factory.json": config}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("array of strings", output)

    def test_unparseable_json_is_reported(self):
        with Repo(**{".dev-factory.json": "{not json"}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn(".dev-factory.json", output)


class Identifiers(unittest.TestCase):
    def test_a_citation_of_something_that_does_not_exist_is_caught(self):
        # The check that pays for the rest: a component confidently pointing at a document or a
        # decision that is not there, and saying nothing while it does.
        source = "// See D-999 for why this is the way it is.\n"

        with Repo(**{"src/thing.cs": source}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("D-999", output)
        self.assertIn("src/thing.cs", output)

    def test_a_citation_that_does_exist_is_fine(self):
        with Repo(**{"src/thing.cs": "// See D-001.\n"}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(0, code, output)

    def test_the_same_identifier_defined_twice_is_caught(self):
        twice = "# Decisions\n\n### D-001 - one\n\n### D-001 - and again\n"

        with Repo(**{"DECISIONS.md": twice}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(1, code)
        self.assertIn("D-001", output)
        self.assertIn("twice" if "twice" in output else "2 times", output)

    def test_repeating_a_limitation_in_the_readme_is_not_a_duplicate(self):
        # The spec owns the limitations table and the README carries a copy of it. Counting the
        # copy as a second definition would make every conforming repository fail.
        spec = spec_text() + "\n## Limitations\n\n| # | Limitation |\n|---|---|\n| L-1 | a real one |\n"
        readme = "# A project\n\n| # | Limitation |\n|---|---|\n| L-1 | a real one |\n"

        with Repo(**{"docs/SPEC.md": spec, "README.md": readme}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(0, code, output)

    def test_a_closed_question_still_counts_as_defined(self):
        # Closed items are struck through and bolded in place rather than deleted, so the
        # decoration must not hide the identifier.
        spec = spec_text() + "\n## Questions\n\n| # | Q |\n|---|---|\n| ~~**Q-1**~~ | closed |\n"

        with Repo(**{"docs/SPEC.md": spec, "README.md": "# p\n\nsee Q-1\n"}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(0, code, output)


class TheScanner(unittest.TestCase):
    def test_build_output_is_not_scanned(self):
        # This test used to assert that a stale document under bin/ defined nothing, and
        # mutating the ignore list failed nothing at all: a file outside the owning path is not
        # a definition site anyway, so it was observing the owner rule under another name.
        #
        # What the ignore list actually buys is this - build directories are full of stale
        # copies of source, and every decision they cite that has since been renumbered would
        # be reported as a dangling reference against a file nobody edits.
        with Repo(**{"obj/Debug/stale.cs": "// See D-777 for why.\n"}) as root:
            code, output = doctor(root, "--no-run")

        self.assertEqual(0, code, output)

        with Repo(**{"src/live.cs": "// See D-777 for why.\n"}) as root:
            self.assertEqual(1, doctor(root, "--no-run")[0],
                             "the same citation in real source must still be caught")

    def test_every_file_carries_a_hash_for_the_grid_to_check(self):
        with Repo() as root:
            records = repo_scan.walk(root)

        self.assertTrue(records)

        for record in records:
            self.assertEqual(64, len(record.sha256), record.path)

    def test_the_hash_changes_when_the_file_does(self):
        with Repo() as root:
            before = {r.path: r.sha256 for r in repo_scan.walk(root)}

            with open(os.path.join(root, "README.md"), "a", encoding="utf-8") as handle:
                handle.write("\nsomething new\n")

            after = {r.path: r.sha256 for r in repo_scan.walk(root)}

        self.assertNotEqual(before["README.md"], after["README.md"])
        self.assertEqual(before["DECISIONS.md"], after["DECISIONS.md"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
