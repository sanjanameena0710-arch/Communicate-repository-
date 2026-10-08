#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""message.txt parser/merge tests.  Run:  python3 tests/test_parse.py"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import sync_messages as s  # noqa: E402

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}\n   got : {got!r}\n   want: {want!r}")
    else:
        print(f"  ok  {name}")


def snd(text):
    return [(m["sender"], m["text"]) for m in s.parse_chat(text)]


print("parse_chat:")
check("Agent A prefix", snd("Agent A: hello"), [("Agent A", "hello")])
check("Agent B prefix", snd("Agent B: hello"), [("Agent B", "hello")])
check("lowercase", snd("agent b: hi there"), [("Agent B", "hi there")])
check("short prefix", snd("A: hi"), [("Agent A", "hi")])
check("short prefix lower", snd("b: hi"), [("Agent B", "hi")])
check("dash separator", snd("Agent A - hi"), [("Agent A", "hi")])
check("bold markdown", snd("**Agent B**: kya haal"), [("Agent B", "kya haal")])
check("backticks", snd("`Agent A`: yo"), [("Agent A", "yo")])
check("quoted blockquote", snd("> Agent B: yo"), [("Agent B", "yo")])
check(
    "colons inside text",
    snd("Agent A: time 10:30, note: ok"),
    [("Agent A", "time 10:30, note: ok")],
)
check("emoji", snd("Agent B: bho bho 🐶"), [("Agent B", "bho bho 🐶")])
check("no prefix -> Agent A (guessed)", snd("hello agent b"), [("Agent A", "hello agent b")])
check("comments skipped", snd("# comment\nAgent A: real"), [("Agent A", "real")])
check("blank lines skipped", snd("\n\nAgent A: x\n\n"), [("Agent A", "x")])
check(
    "prose starting with a word is not a prefix",
    snd("about the plan we talked"),
    [("Agent A", "about the plan we talked")],
)
check("guessed flag", s["guessed"] if False else [m["guessed"] for m in s.parse_chat("plain")], [True])
check("crlf handled", snd("Agent A: x\r\nAgent B: y"), [("Agent A", "x"), ("Agent B", "y")])

print("\nmerge_texts:")
local = "Agent B: hello A\n"
remote_new = "Agent A: hello B\n"
merged, added = s.merge_texts(local, [remote_new])
check("append remote", merged, "Agent B: hello A\nAgent A: hello B\n")
check("added count", added, 1)

merged, added = s.merge_texts(local, [local])
check("no duplicates", (merged, added), ("Agent B: hello A\n", 0))

merged, added = s.merge_texts("", ["Agent A: dup\n", "Agent A: dup\n"])
check("dedupe within merge", (merged, added), ("Agent A: dup\n", 1))

merged, added = s.merge_texts("Agent A: a\n", ["Agent A: a\nAgent A: b\n"])
check("new tail appended", merged, "Agent A: a\nAgent A: b\n")

merged, added = s.merge_texts(
    "Agent B: b1\nAgent B: b2\n", ["Agent A: a1\n", "Agent A: a2\n"]
)
check("multi-branch union", merged, "Agent B: b1\nAgent B: b2\nAgent A: a1\nAgent A: a2\n")

print("\nreal file:")
real = s.read_local()
print(f"  ok  parsed {len(s.parse_chat(real))} messages from message.txt")
for m in s.parse_chat(real)[:2]:
    print(f"      {m['sender']}: {m['text'][:50]}")

print()
if FAILURES:
    print(f"❌ {len(FAILURES)} FAILED\n")
    for f in FAILURES:
        print(" -", f)
    raise SystemExit(1)
print("✅ all tests passed")
