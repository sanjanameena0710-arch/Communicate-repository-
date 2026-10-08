#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Live preview server for the AI A <-> AI B chat.

  GET /            -> chat UI (tools/chat.html)
  GET /api/chat    -> JSON: parsed messages + timestamps + sync status
  GET /message.txt -> raw file (no cache)
  GET /healthz     -> "ok"

Background thread har SYNC_SECONDS second me message.txt ko GitHub ki saari
branches ke saath sync karta hai (tools/sync_messages.py), taki Agent A ke
messages live preview me dikhein aur Agent B ka reply push ho jaye.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sync_messages as sync  # noqa: E402  (path setup ke baad import)

UI_FILE = HERE / "chat.html"
PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")
SYNC_SECONDS = float(os.environ.get("SYNC_SECONDS", "6"))
POLL_MS = int(os.environ.get("POLL_MS", "2500"))


def chat_payload():
    text = sync.read_local()
    msgs = sync.parse_chat(text)
    times = sync.message_times()
    for m in msgs:
        m["ts"] = times.get(sync.message_key(m))
    st = dict(sync.STATUS)
    return {
        "messages": msgs,
        "count": len(msgs),
        "raw": text,
        "file_mtime": sync.file_mtime(),
        "branch": sync.BRANCH,
        "branches": st.get("branches") or [],
        "last_sync": st,
        "now": time.time(),
        "poll_ms": POLL_MS,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentBChat/1.0"
    protocol_version = "HTTP/1.1"

    # --- helpers ---------------------------------------------------------
    def _send(self, code, body: bytes, ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    # --- routing ---------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path in ("/", "/index.html", "/preview"):
            try:
                body = UI_FILE.read_bytes()
            except FileNotFoundError:
                self._send(500, b"chat.html missing", "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return

        if path == "/api/chat":
            try:
                self._json(chat_payload())
            except Exception as exc:  # noqa: BLE001
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            return

        if path == "/message.txt":
            self._send(200, sync.read_local().encode("utf-8"), "text/plain; charset=utf-8")
            return

        if path == "/healthz":
            self._send(200, b"ok")
            return

        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return

        self._send(404, b"not found")

    def log_message(self, fmt, *args):  # keep logs small
        if "/api/chat" in (self.path or "") or "/healthz" in (self.path or ""):
            return
        sys.stderr.write("[http] %s %s\n" % (self.address_string(), fmt % args))


def start_sync_thread():
    def worker():
        while True:
            st = sync.sync_once(push=True)
            stamp = time.strftime("%H:%M:%S")
            print(
                f"[{stamp}] github sync: ok={st['ok']} added={st['added']} "
                f"committed={st['committed']} pushed={st['pushed']} "
                f"branches={len(st['branches'])}"
                + (f" error={st['error']}" if st["error"] else ""),
                flush=True,
            )
            time.sleep(max(2.0, SYNC_SECONDS))

    t = threading.Thread(target=worker, name="sync", daemon=True)
    t.start()
    return t


def main():
    st = sync.sync_once(push=True)  # pehla sync turant
    print(
        f"[boot] first sync: ok={st['ok']} added={st['added']} "
        f"pushed={st['pushed']} error={st['error']}",
        flush=True,
    )
    start_sync_thread()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    print(f"[boot] chat preview live on http://{HOST}:{PORT}  (sync every {SYNC_SECONDS}s)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
