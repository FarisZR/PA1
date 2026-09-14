#!/usr/bin/env python3
"""Regression test for CLIProxyAPI issue #5659.

The mock upstream emits the exact problematic shape: one streamed Chat
Completions delta contains both non-empty ``reasoning_content`` and ``content``.
The test runs the original PA1 CLIProxyAPI v7.2.146 image and the patched image
against the same stream. It passes only when the baseline reproduces the bad
Responses item ordering and the patched image emits reasoning before message
content while preserving both complete texts.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
COMPOSE_FILE = TESTS_DIR.parent / "compose.yaml"

BASELINE_IMAGE = (
    "eceasy/cli-proxy-api:v7.2.146@"
    "sha256:238691ac26ce55e4d1c5219d72e3ad74838f81eda26359912eeb415e2820d163"
)
BRIDGE_KEY = "pa1-5659-bridge-key"
UPSTREAM_KEY = "pa1-5659-upstream-key"
MODEL = "deepseek-v4p1-flash"
MOCK_PORT = 19111
BRIDGE_PORT = 19112
CONTAINER_NAME = "pa1-cliproxy-5659-regression"


def patched_image() -> str:
    match = re.search(r"^\s*image:\s*(\S+)\s*$", COMPOSE_FILE.read_text(), re.M)
    if not match:
        raise SystemExit(f"No image line found in {COMPOSE_FILE}")
    return match.group(1)


class MixedChunkHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        base = {
            "id": "chatcmpl-5659",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": MODEL,
        }

        def emit(payload: dict[str, object] | str) -> None:
            if isinstance(payload, str):
                body = f"data: {payload}\n\n".encode()
            else:
                body = f"data: {json.dumps(payload)}\n\n".encode()
            self.wfile.write(b"%x\r\n" % len(body) + body + b"\r\n")
            self.wfile.flush()

        emit(
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": "Thinking part 1,"},
                        "finish_reason": None,
                    }
                ],
            }
        )
        emit(
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "content": "Bien",
                            "reasoning_content": " Just professional.",
                        },
                        "finish_reason": None,
                    }
                ],
            }
        )
        emit(
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": " continues here."},
                        "finish_reason": None,
                    }
                ],
            }
        )
        emit(
            {
                **base,
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
            }
        )
        emit("[DONE]")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(
            f"{' '.join(args)} failed ({result.returncode}):\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result


def cleanup() -> None:
    run("docker", "rm", "-f", CONTAINER_NAME, check=False)


def config_text() -> str:
    return "\n".join(
        [
            'host: ""',
            "port: 8317",
            'auth-dir: "/root/.cli-proxy-api"',
            "api-keys:",
            f'  - "{BRIDGE_KEY}"',
            "request-retry: 0",
            "disable-cooling: true",
            "openai-compatibility:",
            '  - name: "mock"',
            f'    base-url: "http://host.docker.internal:{MOCK_PORT}/v1"',
            "    api-key-entries:",
            f'      - api-key: "{UPSTREAM_KEY}"',
            "    models:",
            f'      - name: "{MODEL}"',
            f'        alias: "{MODEL}"',
            "        input-modalities: [text]",
            "        thinking:",
            '          levels: ["low", "high", "max"]',
            "",
        ]
    )


def start_bridge(image: str, config_path: Path) -> None:
    cleanup()
    run(
        "docker",
        "run",
        "-d",
        "--name",
        CONTAINER_NAME,
        "--add-host",
        "host.docker.internal:host-gateway",
        "-p",
        f"127.0.0.1:{BRIDGE_PORT}:8317",
        "-v",
        f"{config_path}:/CLIProxyAPI/config.yaml:ro",
        image,
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            urllib.request.urlopen(
                urllib.request.Request(
                    f"http://127.0.0.1:{BRIDGE_PORT}/v1/models",
                    headers={"Authorization": f"Bearer {BRIDGE_KEY}"},
                ),
                timeout=2,
            )
            return
        except Exception:  # noqa: BLE001 - readiness polling
            time.sleep(0.25)
    logs = run("docker", "logs", CONTAINER_NAME, check=False)
    raise SystemExit("Bridge did not become ready:\n" + logs.stdout + logs.stderr)


def request_events() -> list[dict[str, object]]:
    payload = {
        "model": MODEL,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Test mixed chunk."}],
            }
        ],
        "reasoning": {"effort": "max", "summary": "auto"},
        "stream": True,
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{BRIDGE_PORT}/v1/responses",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {BRIDGE_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"Bridge returned HTTP {error.code}: {error.read().decode()}"
        ) from error
    return [json.loads(raw) for raw in re.findall(r"^data: (\{.*\})$", body, re.M)]


def summarize(events: list[dict[str, object]]) -> tuple[list[str], dict[str, object]]:
    order: list[str] = []
    completed: dict[str, object] | None = None
    for event in events:
        event_type = event.get("type")
        if event_type in {"response.output_item.added", "response.output_item.done"}:
            item = event.get("item")
            assert isinstance(item, dict)
            verb = "added" if event_type == "response.output_item.added" else "done"
            order.append(f"{verb}:{item.get('type')}:{event.get('output_index')}")
        elif event_type == "response.completed":
            response = event.get("response")
            assert isinstance(response, dict)
            completed = response
    if completed is None:
        raise AssertionError("No response.completed event")
    return order, completed


def output_texts(completed: dict[str, object]) -> tuple[list[str], list[str]]:
    reasoning: list[str] = []
    messages: list[str] = []
    output = completed.get("output")
    assert isinstance(output, list)
    for item in output:
        assert isinstance(item, dict)
        if item.get("type") == "reasoning":
            summary = item.get("summary")
            assert isinstance(summary, list)
            reasoning.extend(
                part.get("text", "")
                for part in summary
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        elif item.get("type") == "message":
            content = item.get("content")
            assert isinstance(content, list)
            messages.extend(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
    return reasoning, messages


def main() -> int:
    if shutil.which("docker") is None:
        raise SystemExit("docker is required")

    workdir = Path(tempfile.mkdtemp(prefix="pa1-cliproxy-5659-"))
    config_path = workdir / "config.yaml"
    config_path.write_text(config_text())
    mock = ThreadingHTTPServer(("0.0.0.0", MOCK_PORT), MixedChunkHandler)
    thread = threading.Thread(target=mock.serve_forever, daemon=True)
    thread.start()

    try:
        print("Baseline v7.2.146")
        start_bridge(BASELINE_IMAGE, config_path)
        baseline_order, baseline_completed = summarize(request_events())
        baseline_reasoning, baseline_messages = output_texts(baseline_completed)
        print("  event order:", baseline_order)
        print("  completed reasoning:", baseline_reasoning)
        print("  completed messages:", baseline_messages)

        if baseline_order == [
            "added:reasoning:0",
            "done:reasoning:0",
            "added:message:1",
            "done:message:1",
        ]:
            raise AssertionError("Baseline unexpectedly has correct item ordering")

        print("\nPatched v7.2.146 + #5659 backport")
        start_bridge(patched_image(), config_path)
        patched_order, patched_completed = summarize(request_events())
        patched_reasoning, patched_messages = output_texts(patched_completed)
        print("  event order:", patched_order)
        print("  completed reasoning:", patched_reasoning)
        print("  completed messages:", patched_messages)

        expected_order = [
            "added:reasoning:0",
            "done:reasoning:0",
            "added:message:1",
            "done:message:1",
        ]
        assert patched_order == expected_order, (patched_order, expected_order)
        assert patched_reasoning == ["Thinking part 1, Just professional."], patched_reasoning
        assert patched_messages == ["Bien continues here."], patched_messages

        print("\nPASS: baseline reproduces #5659; patched image repairs the edge case.")
        return 0
    finally:
        cleanup()
        mock.shutdown()
        mock.server_close()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
