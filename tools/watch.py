#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ek "chat round": apna message bhejo aur phir N second tak Agent A ke naye
messages live dekho.

Usage:
    python3 tools/watch.py --seconds 25 --say "Hello Agent A!"
    python3 tools/watch.py --seconds 25                 # sirf sunna
    python3 tools/watch.py --seconds 25 --say "..." --quiet
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sync_messages as s  # noqa: E402


def keys(msgs):
    return {s.message_key(m) for m in msgs}


def stamp():
    return time.strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=25, help="kitne second sunna hai")
    ap.add_argument("--say", default=None, help="pehle apna message bhejo (Agent B ke roop me)")
    ap.add_argument("--as", dest="as_", default="B", choices=["A", "B"])
    ap.add_argument("--poll", type=float, default=4.0, help="sync har kitne second")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    s.sync_once(push=True)
    seen = keys(s.parse_chat(s.read_local()))

    if args.say:
        res = s.append_message(args.say, sender=args.as_)
        body = s.format_message(args.say, args.as_)
        seen.add(s.message_key({"sender": "Agent B" if args.as_ == "B" else "Agent A", "text": args.say}))
        if not args.quiet:
            print(f"[{stamp()}] >> {body}   (pushed={res['pushed']})", flush=True)

    deadline = time.time() + max(1.0, args.seconds)
    fresh = []
    while time.time() < deadline:
        time.sleep(min(args.poll, max(0.2, deadline - time.time())))
        st = s.sync_once(push=True)
        for m in s.parse_chat(s.read_local()):
            k = s.message_key(m)
            if k in seen:
                continue
            seen.add(k)
            fresh.append(m)
            who = "<<" if m["sender"] == "Agent A" else ">>"
            print(f"[{stamp()}] {who} {m['sender']}: {m['text']}", flush=True)

    if not fresh:
        print(f"[{stamp()}] (koi naya message nahi aaya)", flush=True)

    # tip sync rakhne ke liye aakhri push
    s.sync_once(push=True)
    print(json.dumps({"new_messages": len(fresh), "total": len(s.parse_chat(s.read_local()))}), flush=True)


if __name__ == "__main__":
    main()
