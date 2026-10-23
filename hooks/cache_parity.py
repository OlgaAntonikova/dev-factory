#!/usr/bin/env python3
"""SessionStart parity check - warns when the plugin cache is stale.

Installed plugins are cached per version under ~/.claude/plugins/cache. A
development install runs straight from the source repository, and nothing
refreshes the cached copy of the SAME version when source files change - so
the law that runs and the copy people read can silently disagree. This was
found in the field: a reviewer cited an encoding bug in the cached
review_gate.py that the source had already fixed.

The durable habit is bumping the version in .claude-plugin/plugin.json on
every change to anything this hook compares - COMPARED_DIRS and
COMPARED_FILES below, deliberately not listed here, so that this sentence
cannot fall behind the set it describes; this hook is the
tripwire for the sessions in between - it compares the running plugin root
with the cache entry of the same version and names every file that disagrees.

Always exits 0. This hook observes; it never blocks.
"""
import hashlib
import json
import os
import sys

#: What readers actually consult: the law, the tools, the knowledge, the
#: roles, the contract. Tests and the roadmap can lag without misleading.
COMPARED_DIRS = ("hooks", "tools", "skills", "agents", "commands")
COMPARED_FILES = ("CONTRACT.md", "STACK.md", os.path.join(".claude-plugin", "plugin.json"))
IGNORED_DIRS = {"__pycache__"}
MAX_LISTED = 10


def digest(path):
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def tree_files(root):
    """Relative paths (posix separators) of every compared file under root."""
    found = []
    for rel_dir in COMPARED_DIRS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, rel_dir)):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
            for name in filenames:
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                found.append(rel.replace(os.sep, "/"))
    for rel in COMPARED_FILES:
        if os.path.isfile(os.path.join(root, rel)):
            found.append(rel.replace(os.sep, "/"))
    return found


def stale_entries(source_root, cache_root):
    """(relpath, reason) for every compared file whose cached copy disagrees."""
    entries = []
    for rel in sorted(set(tree_files(source_root)) | set(tree_files(cache_root))):
        source_path = os.path.join(source_root, rel)
        cached_path = os.path.join(cache_root, rel)
        if not os.path.isfile(cached_path):
            entries.append((rel, "missing from the cache"))
        elif not os.path.isfile(source_path):
            entries.append((rel, "only in the cache"))
        elif digest(source_path) != digest(cached_path):
            entries.append((rel, "content differs"))
    return entries


def cache_root_for(plugin_root):
    """The cache directory holding this plugin name at this version, or None."""
    try:
        with open(os.path.join(plugin_root, ".claude-plugin", "plugin.json"),
                  encoding="utf-8") as handle:
            manifest = json.load(handle)
        name = manifest["name"]
        version = manifest["version"]
    except (OSError, ValueError, KeyError):
        return None
    return os.path.join(
        os.path.expanduser("~"), ".claude", "plugins", "cache", name, name, version)


def report(plugin_root, cache_root, entries):
    listed = "\n".join(f"  {rel} - {reason}" for rel, reason in entries[:MAX_LISTED])
    remaining = len(entries) - MAX_LISTED
    if remaining > 0:
        listed += f"\n  ... and {remaining} more"
    print(
        "dev-factory cache parity: the cached plugin copy disagrees with the "
        f"running source of the same version.\n"
        f"  running: {plugin_root}\n"
        f"  cached:  {cache_root}\n"
        f"{listed}\n"
        "Readers of the cache see stale law. Bump the version in "
        ".claude-plugin/plugin.json (so the cache refreshes on reinstall) or "
        "reinstall the plugin."
    )


def main():
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or ""
    if not os.path.isdir(plugin_root):
        return

    cache_root = cache_root_for(plugin_root)
    if cache_root is None or not os.path.isdir(cache_root):
        return  # no trustworthy manifest, or nothing cached: nothing is stale

    # When the plugin runs from the cache itself (the normal installed case)
    # the two roots are one tree, every file compares equal, and the hook is
    # silent without a special case.
    entries = stale_entries(plugin_root, cache_root)
    if entries:
        report(plugin_root, cache_root, entries)


if __name__ == "__main__":
    main()
    sys.exit(0)
