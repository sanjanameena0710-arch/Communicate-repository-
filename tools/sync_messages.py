#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent A  <->  Agent B  --  GitHub message bridge (Agent B side)

Ye script `message.txt` ko GitHub repo ki SAARI branches ke saath sync karta hai,
taki dono AI agents isi ek text file ke through baat kar saken:

    AI A  ->  GitHub Repo  ->  AI B  ->  GitHub Repo  ->  AI A

Har sync me:
  1. `git fetch --prune origin`             (saari remote branches laata hai)
  2. `message.txt` padhta hai: local checkout + origin/main + har origin/* branch
  3. naye messages ko local file me APPEND karta hai (purani lines kabhi delete
     nahi hoti, duplicate messages skip ho jaate hain)
  4. kuch naya mila to commit karke Agent B ki branch par push kar deta hai,
     taki Agent A use padh sake.

CLI:
    python3 tools/sync_messages.py --once     # ek baar sync (manual)
    python3 tools/sync_messages.py --loop     # har N second sync karta rahe
                                              # (preview server isi ko background
                                              #  thread me chalata hai)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import threading
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MSG_FILE = "message.txt"
DEFAULT_BRANCH = "arena/cd9c3d1e-communicate-repository"
BRANCH = os.environ.get("AGENT_B_BRANCH", DEFAULT_BRANCH)

# "Agent A: hello" / "A: hello" / "**Agent A** - hello" sab match karega
SENDER_RE = re.compile(
    r"^\s*[*_`>\s]*\s*(?:agent[\s\-_]*)?([abAB])\s*[*_`]*\s*[:>\-]+\s*(.+?)\s*$",
    re.IGNORECASE,
)

# Sabhi branches fetch karne ke liye explicit refspec (clone single-branch ho tab bhi
# ye poora refspec force karta hai).
FETCH_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"

_LOCK = threading.Lock()

STATUS = {
    "at": None,
    "ok": None,
    "error": None,
    "fetched": False,
    "added": 0,
    "committed": False,
    "pushed": False,
    "branches": [],
}


# ---------------------------------------------------------------- git helpers
def git(*args, timeout=90):
    """Run a git command inside the repo. Never raises for non-zero exit."""
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def fetch_all(timeout=90):
    """
    GitHub se SAARI branches laao. Clone ka remote.origin.fetch refspec
    single-branch ho sakta hai, isliye refspec explicitly dete hain.
    """
    p = git("fetch", "--prune", "origin", FETCH_REFSPEC, timeout=timeout)
    if p.returncode == 0:
        return p
    # fallback: purana/limited git
    return git("fetch", "--prune", "origin", timeout=timeout)


def remote_refs():
    """All origin/* refs (origin/HEAD hata ke)."""
    p = git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin")
    refs = []
    for line in (p.stdout or "").splitlines():
        ref = line.strip()
        if not ref or ref.endswith("/HEAD"):
            continue
        refs.append(ref)
    return refs


def show_file(ref, path=MSG_FILE):
    """File ka content us ref par; nahi mila to None."""
    p = git("show", f"{ref}:{path}")
    if p.returncode != 0:
        return None
    return p.stdout


def remote_branch_exists():
    p = git("show-ref", "--verify", "--quiet", f"refs/remotes/origin/{BRANCH}")
    return p.returncode == 0


# -------------------------------------------------------------- file helpers
def local_path(path=MSG_FILE):
    return REPO_ROOT / path


def read_local(path=MSG_FILE):
    try:
        return local_path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def write_local(text, path=MSG_FILE):
    p = local_path(path)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def file_mtime(path=MSG_FILE):
    try:
        return local_path(path).stat().st_mtime
    except FileNotFoundError:
        return None


def normalize_newlines(text):
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def norm_text(text):
    return re.sub(r"\s+", " ", text.strip()).casefold()


def message_key(msg):
    """Sender + text ka normalized key (dedupe ke liye)."""
    letter = "A" if msg["sender"].endswith("A") else "B"
    return f"{letter}:{norm_text(msg['text'])}"


def parse_chat(text):
    """
    message.txt -> [{'sender','text','raw'}].
    '#' se shuru hone wali lines = comments (skip).
    Prefix na ho to line ko Agent A ka message maan lete hain.
    """
    msgs = []
    for raw in normalize_newlines(text).split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = SENDER_RE.match(line)
        if m:
            letter = m.group(1).upper()
            sender = "Agent A" if letter == "A" else "Agent B"
            body = m.group(2).strip()
            guessed = False
        else:
            sender, body, guessed = "Agent A", line, True
        msgs.append(
            {"sender": sender, "text": body, "raw": line, "guessed": guessed}
        )
    return msgs


def merge_texts(local_text, remote_texts):
    """Local file + saari remote versions ka append-only union."""
    merged = [ln.rstrip() for ln in normalize_newlines(local_text).split("\n")]
    # trailing blank lines hata do, taki naye messages seedhe neeche append hon
    while merged and not merged[-1].strip():
        merged.pop()
    seen = {message_key(m) for m in parse_chat(local_text)}
    added = 0
    for rtext in remote_texts:
        for m in parse_chat(rtext):
            k = message_key(m)
            if k in seen:
                continue
            seen.add(k)
            merged.append(m["raw"])
            added += 1
    out = "\n".join(merged).strip("\n") + "\n"
    return out, added


def merge_remote_into_local():
    """Har origin branch ka message.txt padho aur naye messages append karo."""
    local = read_local()
    remote_texts = []
    for ref in remote_refs():
        t = show_file(ref)
        if t is not None:
            remote_texts.append(t)
    merged, added = merge_texts(local, remote_texts)
    if merged != local:
        write_local(merged)
    return merged, added


def message_times():
    """
    Har message kis commit me aaya, us ka timestamp (epoch seconds).
    (git log -p se added lines nikal kar)
    """
    p = git("log", "--reverse", "--format=@@%at", "-p", "--", MSG_FILE, timeout=60)
    times = {}
    ts = None
    for line in (p.stdout or "").split("\n"):
        if line.startswith("@@"):
            try:
                ts = int(line[2:].strip())
            except ValueError:
                ts = None
        elif ts and line.startswith("+") and not line.startswith("+++"):
            body = line[1:].strip()
            if not body or body.startswith("#"):
                continue
            for m in parse_chat(body):
                times[message_key(m)] = ts
    return times


# ------------------------------------------------------------- commit / push
def has_local_changes():
    a = git("diff", "--quiet", "--", MSG_FILE)
    b = git("diff", "--cached", "--quiet", "--", MSG_FILE)
    return a.returncode != 0 or b.returncode != 0


def is_ahead():
    if not remote_branch_exists():
        return True  # branch hi remote par nahi hai -> push karna hai
    p = git("rev-list", "--count", f"origin/{BRANCH}..HEAD")
    return p.returncode == 0 and (p.stdout or "").strip() not in ("", "0")


def commit_and_push(max_attempts=4):
    """message.txt ka sync commit karo aur Agent B ki branch par push karo."""
    out = {"committed": False, "pushed": False, "error": None}
    for _ in range(max_attempts):
        if has_local_changes():
            git("add", MSG_FILE)
            c = git("commit", "-m", "chat: sync message.txt (Agent B)", "--no-verify")
            if c.returncode == 0:
                out["committed"] = True
        if not is_ahead():
            out["pushed"] = True
            break
        p = git("push", "origin", f"HEAD:{BRANCH}")
        if p.returncode == 0:
            out["pushed"] = True
            break
        out["error"] = (p.stderr or p.stdout or "").strip()[-300:]
        # remote aage nikal gaya -> fetch + rebase + dobara merge, phir retry
        fetch_all()
        if remote_branch_exists():
            rb = git("rebase", f"origin/{BRANCH}")
            if rb.returncode != 0:
                git("rebase", "--abort")
        merge_remote_into_local()
        time.sleep(0.4)
    return out


# -------------------------------------------------------------------- driver
def sync_once(push=True, verbose=False):
    """Ek pura sync cycle. STATUS dict return karta hai."""
    with _LOCK:
        st = {
            "at": time.time(),
            "ok": False,
            "error": None,
            "fetched": False,
            "added": 0,
            "committed": False,
            "pushed": False,
            "branches": [],
        }
        try:
            p = fetch_all()
            if p.returncode != 0:
                raise RuntimeError("git fetch failed: " + (p.stderr or "").strip()[-200:])
            st["fetched"] = True
            st["branches"] = remote_refs()
            _, added = merge_remote_into_local()
            st["added"] = added
            if push:
                res = commit_and_push()
                st["committed"] = res["committed"]
                st["pushed"] = res["pushed"]
                if res["error"]:
                    st["error"] = "push: " + res["error"]
            st["ok"] = not st["error"]
        except Exception as exc:  # noqa: BLE001 - status me daal dete hain
            st["error"] = f"{type(exc).__name__}: {exc}"
        STATUS.clear()
        STATUS.update(st)
        if verbose:
            print(json.dumps(st, indent=2, default=str), flush=True)
        return dict(st)


def sync_loop(interval=6.0, verbose=False):
    while True:
        st = sync_once(push=True)
        if verbose:
            stamp = time.strftime("%H:%M:%S")
            print(
                f"[{stamp}] sync ok={st['ok']} added={st['added']} "
                f"committed={st['committed']} pushed={st['pushed']} "
                f"branches={len(st['branches'])}"
                + (f" error={st['error']}" if st["error"] else ""),
                flush=True,
            )
        time.sleep(max(2.0, float(interval)))


def main():
    ap = argparse.ArgumentParser(description="Agent B message.txt <-> GitHub sync")
    ap.add_argument("--once", action="store_true", help="ek baar sync karo")
    ap.add_argument("--loop", action="store_true", help="continuously sync karo")
    ap.add_argument("--interval", type=float, default=6.0, help="loop interval (sec)")
    ap.add_argument("--no-push", action="store_true", help="sirf fetch+merge, push mat karo")
    args = ap.parse_args()

    if args.loop:
        sync_loop(interval=args.interval, verbose=True)
    else:
        st = sync_once(push=not args.no_push, verbose=True)
        raise SystemExit(0 if st["ok"] else 1)


if __name__ == "__main__":
    main()
