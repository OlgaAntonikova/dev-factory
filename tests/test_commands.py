#!/usr/bin/env python3
"""Tests for the slash commands in commands/.

A command is an instruction file, so nothing executes it until a user types it. These tests are
what fails instead: a command without a description is invisible in the command list, and a
command that names a plugin file that is not there sends the agent to run something that does
not exist - the same class of error spec-doctor's check 7 exists for.
"""
import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS = os.path.join(REPO, "commands")

#: Every path a command names under the plugin root.
PLUGIN_PATH = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")
#: Every other command a command file points the user at.
COMMAND_REF = re.compile(r"/dev-factory:([a-z][a-z0-9-]*)")
#: Agents a command starts by name.
AGENT_REF = re.compile(r"`([a-z][a-z0-9-]*)` agent")
#: A command-line flag, as a quoted literal in the script that must accept it.
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
#: Skills a command tells the agent to read.
SKILL_REF = re.compile(r"`([a-z][a-z0-9-]*)` skill")


def command_files():
    return sorted(
        name for name in os.listdir(COMMANDS) if name.endswith(".md")
    )


def read(name):
    with open(os.path.join(COMMANDS, name), encoding="utf-8") as handle:
        return handle.read()


def script_text(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as handle:
        return handle.read()


def accepts(rel, flag):
    """The script names the flag as a whole quoted literal - "--no" does not pass for --no-run.

    Known limit: a quoted flag anywhere in the script counts, a help string included. This
    catches a renamed or removed flag, not a parser that stopped accepting one it still mentions.
    """
    return re.search(r"[\"']" + re.escape(flag) + r"[\"']", script_text(rel)) is not None


def frontmatter(text):
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not match:
        return None
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


class CommandFiles(unittest.TestCase):

    def test_there_are_commands(self):
        # The loops below pass vacuously over an empty directory.
        self.assertTrue(command_files())

    def test_every_command_has_a_description(self):
        for name in command_files():
            fields = frontmatter(read(name))
            self.assertIsNotNone(fields, f"{name}: no frontmatter")
            self.assertTrue(fields.get("description"), f"{name}: no description")

    def test_every_plugin_path_a_command_names_exists(self):
        seen = 0
        for name in command_files():
            for rel in PLUGIN_PATH.findall(read(name)):
                seen += 1
                self.assertTrue(
                    os.path.exists(os.path.join(REPO, rel)),
                    f"{name} names ${{CLAUDE_PLUGIN_ROOT}}/{rel}, which does not exist",
                )
        self.assertTrue(seen, "the pattern matched no path in any command")

    def test_every_command_a_command_points_to_exists(self):
        existing = {name[:-3] for name in command_files()}
        seen = 0
        for name in command_files():
            for ref in COMMAND_REF.findall(read(name)):
                seen += 1
                self.assertIn(ref, existing, f"{name} points to /dev-factory:{ref}")
        self.assertTrue(seen, "the pattern matched no command reference")

    def test_every_agent_a_command_starts_exists(self):
        seen = 0
        for name in command_files():
            for agent in AGENT_REF.findall(read(name)):
                seen += 1
                self.assertTrue(
                    os.path.isfile(os.path.join(REPO, "agents", agent + ".md")),
                    f"{name} starts the {agent} agent, which does not exist",
                )
        self.assertTrue(seen, "the pattern matched no agent reference")

    def test_every_skill_a_command_reads_exists(self):
        seen = 0
        for name in command_files():
            for skill in SKILL_REF.findall(read(name)):
                seen += 1
                self.assertTrue(
                    os.path.isfile(os.path.join(REPO, "skills", skill, "SKILL.md")),
                    f"{name} reads the {skill} skill, which does not exist",
                )
        self.assertTrue(seen, "the pattern matched no skill reference")

    def test_every_flag_a_command_passes_to_a_plugin_script_exists(self):
        # A renamed flag leaves every path in place and the command broken. A flag is checked
        # on the line that invokes the script, and in the argument hint, which is passed
        # through as $ARGUMENTS to the scripts the command invokes.
        seen = 0
        for name in command_files():
            text = read(name)
            scripts = set()
            for line in text.splitlines():
                rels = [rel for rel in PLUGIN_PATH.findall(line) if rel.endswith(".py")]
                for rel in rels:
                    scripts.add(rel)
                    rest = line.split(rel, 1)[1]
                    for flag in FLAG.findall(rest):
                        seen += 1
                        self.assertTrue(
                            accepts(rel, flag),
                            f"{name} passes {flag} to {rel}, which does not accept it",
                        )
            hint = (frontmatter(text) or {}).get("argument-hint", "")
            for flag in FLAG.findall(hint):
                seen += 1
                self.assertTrue(
                    any(accepts(rel, flag) for rel in scripts),
                    f"{name} hints {flag}, which none of its scripts accepts",
                )
        self.assertTrue(seen, "the pattern matched no flag in any command")

    def test_commands_are_english_only(self):
        # RULE-ENGLISH-ONLY: every repository artifact. ASCII is stricter than English, and
        # that is the point - it is checkable.
        for name in command_files():
            for number, line in enumerate(read(name).splitlines(), 1):
                self.assertTrue(line.isascii(), f"{name}:{number} is not ASCII")


if __name__ == "__main__":
    unittest.main()
