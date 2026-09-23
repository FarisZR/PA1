#!/usr/bin/env python3
"""Replay a recorded request through LiteLLM's Fireworks provider (issue #111).

The script sends the committed fixture through ``litellm.completion`` with a
``fireworks_ai/`` model to a local capture server that stands in for Fireworks,
then reports how many earlier assistant messages still carry
``reasoning_content`` in the body LiteLLM forwarded. No model is called and no
network access is needed.

Run it once per LiteLLM version, each in a throwaway environment:

    for v in 1.98.0 1.101.0 1.102.0; do
      LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-project --with "litellm==$v" \\
        python scripts/replay_litellm_fireworks.py \\
        data/litellm-reasoning-audit/replay-request.json
    done
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402  (must follow the environment setting)

FIREWORKS_MODEL = "fireworks_ai/accounts/fireworks/models/deepseek-v4p1-flash"
captured: list[dict] = []


class CaptureHandler(BaseHTTPRequestHandler):
    """Record the forwarded body and answer with a minimal completion."""

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        captured.append(body)
        reply = json.dumps({
            "id": "replay",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


def reasoning_messages(messages: list[dict]) -> tuple[int, int, int]:
    assistant = [m for m in messages if m.get("role") == "assistant"]
    with_reasoning = [m for m in assistant if (m.get("reasoning_content") or "").strip()]
    return len(assistant), len(with_reasoning), sum(len(m["reasoning_content"]) for m in with_reasoning)


def main() -> None:
    fixture = json.loads(Path(sys.argv[1]).read_text())
    server = ThreadingHTTPServer(("127.0.0.1", 0), CaptureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        litellm.completion(
            model=FIREWORKS_MODEL,
            api_base=f"http://127.0.0.1:{server.server_port}/v1",
            api_key="replay",
            messages=fixture["messages"],
        )
    finally:
        server.shutdown()
    if len(captured) != 1:
        raise SystemExit(f"expected one forwarded request, captured {len(captured)}")
    sent = reasoning_messages(fixture["messages"])
    forwarded = reasoning_messages(captured[0]["messages"])
    print(json.dumps({
        "litellm": version("litellm"),
        "assistant_messages": sent[0],
        "sent_with_reasoning_content": sent[1],
        "sent_reasoning_chars": sent[2],
        "forwarded_with_reasoning_content": forwarded[1],
        "forwarded_reasoning_chars": forwarded[2],
    }))


if __name__ == "__main__":
    main()
