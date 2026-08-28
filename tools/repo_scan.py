#!/usr/bin/env python3
"""One pass over a repository: files, hashes, and where every identifier is defined and used.

Written to serve two callers. `spec-doctor` needs the identifier map to check that nothing
cites a rule or a decision that does not exist. The project grid (ROADMAP, v1.1) needs the same
walk plus the hashes, and a scanner written only for the doctor would be rewritten within the
month.

The hashes are the grid's whole safety story, which is why they are collected here and not
bolted on later: an index entry is trustworthy exactly as long as the file it describes has not
changed, and a consumer that cannot check that is consuming a cache with no expiry.

Standard library only.
"""
import hashlib
import os
import re

#: Directories that hold build output, dependencies or history rather than the project.
IGNORED_DIRECTORIES = {
    ".git", ".hg", ".svn", ".idea", ".vs", ".vscode",
    "bin", "obj", "build", "dist", "out", "target",
    "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache",
    "artifacts", "TestResults", "coverage",
}

#: What the identifier scan reads. Prose carries the definitions, code carries the citations -
#: and a code comment citing a decision that no longer exists is exactly the error worth
#: catching.
TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".adoc",
    ".cs", ".fs", ".vb", ".java", ".kt", ".swift", ".go", ".rs", ".rb", ".php",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".m", ".mm",
    ".axaml", ".xaml", ".razor", ".html", ".css", ".scss",
    ".json", ".yaml", ".yml", ".toml", ".props", ".targets", ".ps1", ".sh",
}

#: Every identifier family the contract defines, and the document that owns its definitions.
#: A family's IDs may be *cited* anywhere; they are *defined* only in the owning file, which is
#: what lets the same limitation appear in both the spec and the README without counting twice.
FAMILIES = {
    "RULE": ("docs/SPEC.md", r"RULE-[A-Z][A-Z0-9-]*"),
    "INV": ("docs/SPEC.md", r"INV-\d+"),
    "PHASE": ("docs/SPEC.md", r"PHASE-\d+"),
    "L": ("docs/SPEC.md", r"L-\d+"),
    "Q": ("docs/SPEC.md", r"Q-\d+"),
    "D": ("DECISIONS.md", r"D-\d+"),
    "NR": ("NEEDS_REVIEW.md", r"NR-\d+"),
}

ANY_ID = re.compile("|".join(rf"\b{pattern}\b" for _, pattern in FAMILIES.values()))

#: A definition site is a heading, or the first cell of a table row, whose text begins with the
#: identifier. Both are how the first project already wrote them; neither depends on the
#: language of the prose around them.
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<cell>.*)$")
TABLE_ROW = re.compile(r"^\s*\|(?P<cell>[^|]*)\|")

#: Markdown decoration to strip before asking whether a cell starts with an identifier:
#: emphasis, strike-through for a closed item, and code ticks.
DECORATION = re.compile(r"^[\s*~`_]+|[\s*~`_]+$")


class FileRecord:
    """One file, as both the doctor and the grid need it."""

    __slots__ = ("path", "sha256", "size", "lines")

    def __init__(self, path, sha256, size, lines):
        self.path = path
        self.sha256 = sha256
        self.size = size
        self.lines = lines

    def __repr__(self):
        return f"FileRecord({self.path!r}, {self.sha256[:8]}, {self.size} bytes)"


def walk(root, extensions=None):
    """Every file under `root`, hashed, with build output and dependencies skipped."""
    wanted = set(extensions) if extensions else None
    records = []

    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = [d for d in subdirectories if d not in IGNORED_DIRECTORIES]

        for name in names:
            absolute = os.path.join(directory, name)
            relative = os.path.relpath(absolute, root).replace(os.sep, "/")

            if wanted is not None and os.path.splitext(name)[1] not in wanted:
                continue

            try:
                with open(absolute, "rb") as handle:
                    content = handle.read()
            except OSError:
                continue  # unreadable is not the scanner's problem to solve, only to skip

            records.append(FileRecord(
                path=relative,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                lines=content.count(b"\n") + 1,
            ))

    records.sort(key=lambda record: record.path)

    return records


def read_text(root, relative):
    try:
        with open(os.path.join(root, relative), encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def identifier_sites(root, records=None):
    """
    Where every identifier is defined, and everywhere it is cited.

    Returns (definitions, references), each mapping an identifier to a list of "path:line".
    An identifier defined nowhere but cited somewhere is the finding this exists for.
    """
    if records is None:
        records = walk(root, TEXT_EXTENSIONS)

    owners = {family: owner for family, (owner, _) in FAMILIES.items()}
    definitions = {}
    references = {}

    for record in records:
        if os.path.splitext(record.path)[1] not in TEXT_EXTENSIONS:
            continue

        text = read_text(root, record.path)

        for number, line in enumerate(text.splitlines(), start=1):
            where = f"{record.path}:{number}"

            for match in ANY_ID.finditer(line):
                references.setdefault(match.group(0), []).append(where)

            identifier = _defined_here(line)

            if identifier and owners.get(_family_of(identifier)) == record.path:
                definitions.setdefault(identifier, []).append(where)

    return definitions, references


def _family_of(identifier):
    return identifier.split("-")[0]


def _defined_here(line):
    """The identifier this line defines, if it is a definition site at all."""
    for pattern in (HEADING, TABLE_ROW):
        match = pattern.match(line)

        if not match:
            continue

        cell = DECORATION.sub("", match.group("cell"))
        found = ANY_ID.match(cell)

        if found:
            return found.group(0)

    return None
