#!/usr/bin/env python3
"""Recording stand-in for the LiteLLM gateway's Chat Completions surface.

Every request body the bridge sends upstream is appended to RECORD_FILE, which
is what lets the acceptance test assert on the translated payload instead of
guessing. Responses are streamed in the shape a Fireworks-backed reasoning model
produces: `reasoning_content` deltas, then either a tool call or final text, then
a usage frame carrying `completion_tokens_details.reasoning_tokens`.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RECORD_FILE = os.environ.get("RECORD_FILE", "/records/requests.jsonl")
PORT = int(os.environ.get("PORT", "9111"))
# Turns 1..TOOL_TURNS answer with a tool call; the turn after that finishes.
TOOL_TURNS = int(os.environ.get("TOOL_TURNS", "3"))

_lock = threading.Lock()
_turn = 0


def reasoning_text(turn: int) -> str:
    """Return a distinctive reasoning string so replay can be matched exactly."""
    return (
        f"REASONING_TURN_{turn}_ALPHA think about step {turn} "
        f"REASONING_TURN_{turn}_OMEGA"
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # noqa: D102 - silence stderr
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = json.dumps({"object": "list", "data": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        global _turn
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        with _lock:
            _turn += 1
            turn = _turn
            with open(RECORD_FILE, "a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "turn": turn,
                            "path": self.path,
                            "headers": {
                                key.lower(): value
                                for key, value in self.headers.items()
                            },
                            "body": json.loads(raw or b"{}"),
                        }
                    )
                    + "\n"
                )

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        base = {
            "id": f"chatcmpl-{turn}",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "mock-model",
        }

        def emit(payload: dict[str, object]) -> None:
            data = f"data: {json.dumps(payload)}\n\n".encode()
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()

        for word in reasoning_text(turn).split(" "):
            emit(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"reasoning_content": word + " "},
                            "finish_reason": None,
                        }
                    ],
                }
            )

        if turn <= TOOL_TURNS:
            emit(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": f"call_{turn}",
                                        "type": "function",
                                        "function": {
                                            "name": "shell",
                                            "arguments": json.dumps(
                                                {"command": ["echo", f"turn{turn}"]}
                                            ),
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
            finish = "tool_calls"
        else:
            emit(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "FINAL_ANSWER"},
                            "finish_reason": None,
                        }
                    ],
                }
            )
            finish = "stop"

        emit({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        emit(
            {
                **base,
                "choices": [],
                "usage": {
                    "prompt_tokens": 1000 + turn,
                    "completion_tokens": 500 + turn,
                    "total_tokens": 1500 + 2 * turn,
                    "prompt_tokens_details": {"cached_tokens": 100 + turn},
                    "completion_tokens_details": {"reasoning_tokens": 400 + turn},
                },
            }
        )
        done = b"data: [DONE]\n\n"
        self.wfile.write(b"%x\r\n" % len(done) + done + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
