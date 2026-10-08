#!/usr/bin/env python3
"""
AI A <-> AI B live chat preview server.

Communication channel: message.txt in this GitHub repo.
  AI A -> message.txt -> commit & push -> GitHub -> AI B
  AI B -> message.txt -> commit & push -> GitHub -> AI A (this server pulls it live)

Endpoints:
  GET  /               -> chat preview UI (index.html)
  GET  /api/messages   -> JSON list of parsed messages from message.txt
  POST /api/send       -> send a message as AI A (appends to message.txt, commits & pushes)

A background thread syncs with GitHub every few seconds so Agent B's
replies show up in the preview automatically.
"""

import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
MESSAGE_FILE = os.path.join(REPO_DIR, "message.txt")
INDEX_FILE = os.path.join(REPO_DIR, "index.html")
PORT = int(os.environ.get("PORT", "8000"))
SYNC_SECONDS = float(os.environ.get("SYNC_SECONDS", "5"))

LINE_RE = re.compile(r"^\s*\[(AI A|AI B|Agent A|Agent B)\]\s*:?\s*(.*)$", re.IGNORECASE)

git_lock = threading.Lock()


def run_git(*args):
    return subprocess.run(
        ["git", "-C", REPO_DIR, *args],
        capture_output=True,
        text=True,
    )


def current_branch():
    r = run_git("branch", "--show-current")
    return r.stdout.strip() or "main"


def sync_with_remote():
    """Pull Agent B's latest messages, then push any local commits (as AI A)."""
    with git_lock:
        branch = current_branch()
        run_git("pull", "--rebase", "origin", branch)
        r = run_git("rev-list", f"origin/{branch}..{branch}", "--count")
        if r.returncode == 0 and r.stdout.strip() not in ("", "0"):
            run_git("push", "origin", branch)


def background_sync_loop():
    while True:
        try:
            sync_with_remote()
        except Exception as e:
            print(f"[sync] error: {e}", flush=True)
        time.sleep(SYNC_SECONDS)


def read_messages():
    messages = []
    try:
        with open(MESSAGE_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                if not line.strip():
                    continue
                m = LINE_RE.match(line)
                if m:
                    agent = m.group(1).upper().replace("AGENT", "AI")
                    messages.append({"agent": agent, "text": m.group(2).strip()})
                else:
                    messages.append({"agent": "RAW", "text": line.strip()})
    except FileNotFoundError:
        pass
    return messages


def send_message(agent, text):
    agent = (agent or "AI A").strip().upper()
    if agent not in ("AI A", "AI B"):
        return False, "agent must be 'AI A' or 'AI B'"
    text = (text or "").strip()
    if not text:
        return False, "empty message"
    with git_lock:
        branch = current_branch()
        run_git("pull", "--rebase", "origin", branch)
        with open(MESSAGE_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{agent}] {text}\n")
        run_git("add", "message.txt")
        run_git("commit", "-m", f"{agent}: {text[:60]}")
        run_git("push", "origin", branch)
    return True, "sent"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, content_type="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/messages":
            self._send(200, json.dumps({"messages": read_messages()}), "application/json")
        elif path in ("/", "/index.html"):
            try:
                with open(INDEX_FILE, "r", encoding="utf-8") as f:
                    self._send(200, f.read())
            except FileNotFoundError:
                self._send(404, "index.html not found", "text/plain")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/send":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            agent = "AI A"
            try:
                payload = json.loads(body)
                text = payload.get("text", "")
                agent = payload.get("agent", "AI A")
            except (json.JSONDecodeError, AttributeError):
                text = parse_qs(body).get("text", [""])[0]
            ok, msg = send_message(agent, text)
            self._send(200 if ok else 400,
                       json.dumps({"ok": ok, "message": msg}),
                       "application/json")
        else:
            self._send(404, "not found", "text/plain")

    def log_message(self, fmt, *args):
        print("[http]", fmt % args, flush=True)


def main():
    try:
        sync_with_remote()
    except Exception as e:
        print(f"[sync] startup error: {e}", flush=True)
    threading.Thread(target=background_sync_loop, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[server] Live preview running on port {PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
