#!/usr/bin/env python3
"""spec-doctor - checks a repository against CONTRACT.md.

Mechanical, all of it. Every finding names the file and the missing identifier, because a
report that says "the spec is incomplete" is a report nobody can act on.

Run it when the plugin is installed, and at the start of every phase.

    python3 tools/spec_doctor.py [path] [--no-run]

Exit 0 when the repository satisfies the contract, 1 when it does not.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks"))

import repo_scan  # noqa: E402

REQUIRED_FILES = [
    ("docs/SPEC.md", "the specification"),
    ("DECISIONS.md", "choices the spec did not dictate"),
    ("NEEDS_REVIEW.md", "claims awaiting expert verification"),
    ("README.md", "how to build it, and the honest limitations"),
    (".dev-factory.json", "the build and test commands"),
]

#: CONTRACT.md section 3. Every dev-factory spec carries all of these.
CORE_RULES = [
    "RULE-INSTALLS",
    "RULE-GIT",
    "RULE-DEPENDENCIES",
    "RULE-GREEN",
    "RULE-PHASE-GATE",
    "RULE-REVIEW",
    "RULE-ASK",
    "RULE-NEGATIVE-TEST",
    "RULE-MUTATION-PROOF",
    "RULE-NO-MAGIC-NUMBERS",
    "RULE-BUILD-HYGIENE",
    "RULE-BRIEF",
    "RULE-ENGLISH-ONLY",
]

#: RULE-ENGLISH-ONLY, the mechanical part. The doctor checks the required documents; text in
#: code comments, commit messages and everything else is the reviewer's to catch — a script
#: that tried to judge "is this English?" in arbitrary source would be guessing. Cyrillic is
#: what the rule has actually been broken with, so Cyrillic is what is detected; precise
#: beats broad here (a Latin-alphabet non-English sentence is invisible to this check, and
#: the contract says so rather than pretending otherwise).
NON_ENGLISH = re.compile(r"[Ѐ-ӿ]")

ENGLISH_ONLY_DOCS = ["docs/SPEC.md", "DECISIONS.md", "NEEDS_REVIEW.md", "README.md"]

#: CONTRACT.md section 5. English anchors so a spec in any language stays greppable.
PHASE_ANCHORS = ["Deliverables:", "Gate:"]

HEADING_LEVEL = re.compile(r"^\s{0,3}(#{1,6})\s")


class Report:
    def __init__(self):
        self.failures = []

    def ok(self, check, detail=""):
        print(f"  ok   {check}" + (f" - {detail}" if detail else ""))

    def fail(self, check, detail):
        self.failures.append((check, detail))
        print(f"  FAIL {check} - {detail}")

    def finish(self):
        print()

        if not self.failures:
            print("spec-doctor: the repository satisfies CONTRACT.md")
            return 0

        print(f"spec-doctor: {len(self.failures)} problem(s). "
              "Each names what is missing and where it belongs.")
        return 1


def check_files(root, report):
    missing = [path for path, _ in REQUIRED_FILES if not os.path.exists(os.path.join(root, path))]

    for path, purpose in REQUIRED_FILES:
        if path in missing:
            report.fail("required files", f"{path} is missing ({purpose})")

    if not missing:
        report.ok("required files", f"all {len(REQUIRED_FILES)} present")

    return not missing


def check_stack(root, report, run_commands):
    path = os.path.join(root, ".dev-factory.json")

    if not os.path.exists(path):
        return  # already reported by check_files

    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        report.fail("stack declaration", f".dev-factory.json could not be read: {error}")
        return

    for field in ("build", "test"):
        value = config.get(field)

        if not (isinstance(value, list) and value and all(isinstance(x, str) and x for x in value)):
            report.fail("stack declaration",
                        f'"{field}" must be a non-empty array of strings, not {value!r}')
            return

    report.ok("stack declaration", f'build={" ".join(config["build"])}')

    if not run_commands:
        return

    for field in ("build", "test"):
        argv = config[field]

        try:
            proc = subprocess.run(argv, cwd=root, capture_output=True, text=True)
        except (OSError, ValueError) as error:
            report.fail(f"{field} command", f'`{" ".join(argv)}` could not start: {error}')
            continue

        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-5:]
            report.fail(f"{field} command",
                        f'`{" ".join(argv)}` exited {proc.returncode}: ' + " / ".join(tail))
        else:
            report.ok(f"{field} command", " ".join(argv))


def check_core_rules(root, report):
    text = repo_scan.read_text(root, "docs/SPEC.md")
    absent = [rule for rule in CORE_RULES if rule not in text]

    if absent:
        report.fail("core rules",
                    f"docs/SPEC.md does not mention {', '.join(absent)} "
                    "(CONTRACT.md section 3 requires every core rule by identifier)")
    else:
        report.ok("core rules", f"all {len(CORE_RULES)} present in docs/SPEC.md")


def check_english_only(root, report):
    """RULE-ENGLISH-ONLY over the required documents. Every hit names the file and the line."""
    hits = []

    for relative in ENGLISH_ONLY_DOCS:
        text = repo_scan.read_text(root, relative)

        for number, line in enumerate(text.splitlines(), start=1):
            if NON_ENGLISH.search(line):
                hits.append(f"{relative}:{number}")

    if hits:
        shown = ", ".join(hits[:5]) + (f" (+{len(hits) - 5} more)" if len(hits) > 5 else "")
        report.fail("english only",
                    f"non-English text at {shown} (RULE-ENGLISH-ONLY: repository "
                    "artifacts are written in English)")
    else:
        report.ok("english only", f"no non-English text in {len(ENGLISH_ONLY_DOCS)} documents")


def check_families_present(root, report, definitions):
    for family in ("INV", "PHASE"):
        found = [i for i in definitions if i.startswith(family + "-")]

        if found:
            report.ok(f"{family} identifiers", f"{len(found)} defined")
        else:
            report.fail(f"{family} identifiers",
                        f"docs/SPEC.md defines no {family}-n. A spec with no {family} "
                        "identifiers is missing a section the contract requires")


def phase_blocks(text):
    """Each PHASE-n heading and the lines belonging to it, up to the next heading of its level."""
    lines = text.splitlines()
    starts = []

    for number, line in enumerate(lines):
        heading = HEADING_LEVEL.match(line)

        if heading and re.search(r"\bPHASE-\d+\b", line):
            starts.append((number, len(heading.group(1)), re.search(r"\bPHASE-\d+\b", line).group(0)))

    for index, (start, level, identifier) in enumerate(starts):
        end = len(lines)

        for number in range(start + 1, len(lines)):
            heading = HEADING_LEVEL.match(lines[number])

            if heading and len(heading.group(1)) <= level:
                end = number
                break

        yield identifier, "\n".join(lines[start:end])


def check_phase_anchors(root, report):
    text = repo_scan.read_text(root, "docs/SPEC.md")
    blocks = list(phase_blocks(text))

    if not blocks:
        return  # already reported by check_families_present

    broken = []

    for identifier, body in blocks:
        missing = [anchor for anchor in PHASE_ANCHORS if anchor not in body]

        if missing:
            broken.append(f"{identifier} has no {' or '.join(missing)}")

    if broken:
        for problem in broken:
            report.fail("phase shape", problem)
    else:
        report.ok("phase shape", f"{len(blocks)} phases carry Deliverables: and Gate:")


def check_no_duplicate_definitions(report, definitions):
    duplicates = {i: sites for i, sites in definitions.items() if len(sites) > 1}

    if duplicates:
        for identifier, sites in sorted(duplicates.items()):
            report.fail("duplicate identifier",
                        f"{identifier} is defined {len(sites)} times: {', '.join(sites)}")
    else:
        report.ok("duplicate identifiers", f"none among {len(definitions)} definitions")


def check_every_reference_resolves(report, definitions, references):
    """
    The check that pays for the rest.

    Everything above confirms a shape. This one catches the class of error where a component
    confidently cites something that is not there - a review skill that pointed for six phases
    at a document which had been superseded, and said nothing while doing it.
    """
    dangling = {i: sites for i, sites in references.items() if i not in definitions}

    if not dangling:
        report.ok("references", f"all {len(references)} identifiers resolve")
        return

    for identifier, sites in sorted(dangling.items()):
        shown = ", ".join(sites[:3]) + (f" (+{len(sites) - 3} more)" if len(sites) > 3 else "")
        report.fail("dangling reference",
                    f"{identifier} is cited at {shown} but defined nowhere")


def check_prerequisites(report):
    if shutil.which("python3"):
        report.ok("prerequisites", "python3 is on PATH")
    else:
        report.fail("prerequisites",
                    "python3 is not on PATH. The hooks are invoked by that name, so every "
                    "hook would silently fail to run")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check a repository against CONTRACT.md.")
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("--no-run", action="store_true",
                        help="skip actually running the build and test commands")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.root)
    print(f"spec-doctor: {root}\n")

    report = Report()

    check_prerequisites(report)
    has_files = check_files(root, report)
    check_stack(root, report, run_commands=not args.no_run)

    if has_files or os.path.exists(os.path.join(root, "docs/SPEC.md")):
        definitions, references = repo_scan.identifier_sites(root)

        check_core_rules(root, report)
        check_english_only(root, report)
        check_families_present(root, report, definitions)
        check_phase_anchors(root, report)
        check_no_duplicate_definitions(report, definitions)
        check_every_reference_resolves(report, definitions, references)

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
