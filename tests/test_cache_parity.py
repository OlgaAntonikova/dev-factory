"""cache_parity - the stale-cache tripwire.

Two layers, matching the house standard (see test_hooks.py): the comparison
logic is tested as pure functions over temp trees, and main() is tested the
way the harness runs it - a subprocess with CLAUDE_PLUGIN_ROOT and a faked
home directory, exit code and stdout out - because that is the only interface
that matters. Every compared directory and file is made to actually differ in
some test, so the compared set cannot silently narrow.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

import cache_parity

HOOK_PATH = os.path.join(os.path.dirname(__file__), "..", "hooks", "cache_parity.py")


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


class StaleEntriesTests(unittest.TestCase):
    """The comparison logic, as pure functions over temp trees."""

    def setUp(self):
        self._source = tempfile.TemporaryDirectory()
        self._cache = tempfile.TemporaryDirectory()
        self.source = self._source.name
        self.cache = self._cache.name
        self.addCleanup(self._source.cleanup)
        self.addCleanup(self._cache.cleanup)

    def populate_both(self):
        for root in (self.source, self.cache):
            write(root, os.path.join("hooks", "law.py"), "print('law')\n")
            write(root, os.path.join("skills", "how", "SKILL.md"), "# how\n")
            write(root, "CONTRACT.md", "# contract\n")

    def test_identical_trees_report_nothing(self):
        self.populate_both()
        self.assertEqual([], cache_parity.stale_entries(self.source, self.cache))

    def test_content_difference_is_named(self):
        # Same file names on both sides: only a content comparison can see it.
        self.populate_both()
        write(self.cache, os.path.join("hooks", "law.py"), "print('old law')\n")

        entries = cache_parity.stale_entries(self.source, self.cache)

        self.assertEqual([("hooks/law.py", "content differs")], entries)

    def test_file_missing_from_cache_is_named(self):
        self.populate_both()
        write(self.source, os.path.join("hooks", "new_guard.py"), "pass\n")

        entries = cache_parity.stale_entries(self.source, self.cache)

        self.assertEqual([("hooks/new_guard.py", "missing from the cache")], entries)

    def test_file_only_in_cache_is_named(self):
        self.populate_both()
        write(self.cache, os.path.join("hooks", "removed_guard.py"), "pass\n")

        entries = cache_parity.stale_entries(self.source, self.cache)

        self.assertEqual([("hooks/removed_guard.py", "only in the cache")], entries)

    def test_every_compared_directory_is_actually_compared(self):
        # Each directory in the compared set gets a real divergence; a set
        # silently narrowed to hooks/ fails here on the first missing entry.
        self.populate_both()
        for rel_dir in ("hooks", "tools", "skills", "agents"):
            write(self.source, os.path.join(rel_dir, "drifted.txt"), "new\n")
            write(self.cache, os.path.join(rel_dir, "drifted.txt"), "old\n")

        entries = cache_parity.stale_entries(self.source, self.cache)

        reported = {rel for rel, _ in entries}
        for rel_dir in ("hooks", "tools", "skills", "agents"):
            self.assertIn(f"{rel_dir}/drifted.txt", reported)

    def test_every_compared_top_level_file_is_actually_compared(self):
        self.populate_both()
        for rel in ("CONTRACT.md", "STACK.md",
                    os.path.join(".claude-plugin", "plugin.json")):
            write(self.source, rel, "new\n")
            write(self.cache, rel, "old\n")

        entries = cache_parity.stale_entries(self.source, self.cache)

        reported = {rel for rel, _ in entries}
        self.assertIn("CONTRACT.md", reported)
        self.assertIn("STACK.md", reported)
        self.assertIn(".claude-plugin/plugin.json", reported)

    def test_pycache_is_ignored(self):
        self.populate_both()
        write(self.source, os.path.join("hooks", "__pycache__", "law.pyc"), "bytecode")

        self.assertEqual([], cache_parity.stale_entries(self.source, self.cache))

    def test_files_outside_compared_set_are_ignored(self):
        # The roadmap and tests may lag the cache without misleading anyone.
        self.populate_both()
        write(self.source, "ROADMAP.md", "# plans change\n")

        self.assertEqual([], cache_parity.stale_entries(self.source, self.cache))

    def test_cache_root_resolves_from_manifest(self):
        write(self.source, os.path.join(".claude-plugin", "plugin.json"),
              '{"name": "dev-factory", "version": "9.9.9"}')

        cache_root = cache_parity.cache_root_for(self.source)

        expected_tail = os.path.join(
            ".claude", "plugins", "cache", "dev-factory", "dev-factory", "9.9.9")
        self.assertTrue(cache_root.endswith(expected_tail), cache_root)

    def test_broken_manifest_resolves_to_none(self):
        write(self.source, os.path.join(".claude-plugin", "plugin.json"), "not json")

        self.assertIsNone(cache_parity.cache_root_for(self.source))


class MainSubprocessTests(unittest.TestCase):
    """main() as the harness runs it: subprocess, env in, exit code and
    stdout out. Guards the documented promises: always exit 0, silent in
    every healthy configuration, a named warning on divergence."""

    PLUGIN_NAME = "parity-probe"
    VERSION = "1.2.3"

    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        self._plugin = tempfile.TemporaryDirectory()
        self.home = self._home.name
        self.plugin = self._plugin.name
        self.addCleanup(self._home.cleanup)
        self.addCleanup(self._plugin.cleanup)
        self.make_plugin_tree(self.plugin)

    def make_plugin_tree(self, root):
        write(root, os.path.join(".claude-plugin", "plugin.json"), json.dumps(
            {"name": self.PLUGIN_NAME, "version": self.VERSION}))
        write(root, os.path.join("hooks", "law.py"), "print('law')\n")

    def cache_dir(self):
        return os.path.join(self.home, ".claude", "plugins", "cache",
                            self.PLUGIN_NAME, self.PLUGIN_NAME, self.VERSION)

    def make_cache_copy(self):
        shutil.copytree(self.plugin, self.cache_dir())

    def run_hook(self, plugin_root, cwd=None):
        env = dict(os.environ)
        env["USERPROFILE"] = self.home   # expanduser on Windows
        env["HOME"] = self.home          # expanduser everywhere else
        if plugin_root is None:
            env.pop("CLAUDE_PLUGIN_ROOT", None)
        else:
            env["CLAUDE_PLUGIN_ROOT"] = plugin_root
        return subprocess.run(
            [sys.executable, HOOK_PATH],
            env=env, cwd=cwd or self.home, capture_output=True,
            encoding="utf-8", errors="replace", timeout=60)

    def test_no_plugin_root_is_silent_even_beside_a_divergent_cache(self):
        # cwd is a plugin tree and its cache diverges; without the env var the
        # hook must still stay out of it (a relative-path manifest read here
        # would produce a warning and fail this test).
        self.make_cache_copy()
        write(self.cache_dir(), os.path.join("hooks", "law.py"), "old\n")

        result = self.run_hook(plugin_root=None, cwd=self.plugin)

        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout.strip())

    def test_no_cache_entry_is_silent(self):
        result = self.run_hook(plugin_root=self.plugin)

        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout.strip())

    def test_running_from_the_cache_itself_is_silent(self):
        self.make_cache_copy()

        result = self.run_hook(plugin_root=self.cache_dir())

        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout.strip())

    def test_identical_cache_is_silent(self):
        self.make_cache_copy()

        result = self.run_hook(plugin_root=self.plugin)

        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout.strip())

    def test_divergent_cache_warns_by_name_and_still_exits_zero(self):
        self.make_cache_copy()
        write(self.cache_dir(), os.path.join("hooks", "law.py"), "print('old')\n")

        result = self.run_hook(plugin_root=self.plugin)

        self.assertEqual(0, result.returncode)
        self.assertIn("hooks/law.py - content differs", result.stdout)
        self.assertIn("Bump the version", result.stdout)

    def test_long_listings_are_truncated(self):
        # Literal sizes on purpose: deriving them from MAX_LISTED would make
        # this test green for every value of the bound. 13 divergent files
        # must yield exactly 10 listed lines plus the 3-more tail.
        self.make_cache_copy()
        for index in range(13):
            write(self.plugin, os.path.join("hooks", f"guard_{index:02}.py"), "new\n")

        result = self.run_hook(plugin_root=self.plugin)

        self.assertEqual(0, result.returncode)
        listed = [line for line in result.stdout.splitlines()
                  if " - missing from the cache" in line
                  or " - content differs" in line
                  or " - only in the cache" in line]
        self.assertEqual(10, len(listed))
        self.assertIn("... and 3 more", result.stdout)

class HooksWiringTests(unittest.TestCase):
    """hooks.json parses and every command it names points at a real file."""

    def test_every_wired_hook_script_exists(self):
        repo = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(repo, "hooks", "hooks.json"),
                  encoding="utf-8") as handle:
            config = json.load(handle)

        commands = [
            hook["command"]
            for entries in config["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
        ]
        self.assertTrue(commands)
        for command in commands:
            marker = "${CLAUDE_PLUGIN_ROOT}/"
            self.assertIn(marker, command)
            rel = command.split(marker, 1)[1].split('"', 1)[0]
            self.assertTrue(
                os.path.isfile(os.path.join(repo, rel)),
                f"hooks.json wires a missing script: {rel}")


if __name__ == "__main__":
    unittest.main()
