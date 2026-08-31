#!/usr/bin/env python3
"""Live acceptance check for the Codex bridge against the real gateway.

For each model it does three things:

  1. Sends a Codex 0.151.0-shaped Responses request straight to the LiteLLM
     gateway and confirms it still fails. Without this the run cannot show the
     bridge fixed anything.
  2. Sends the identical request through the running bridge and requires a
     usable answer.
  3. If CODEX_CLIPROXY_REQUEST_LOG was enabled when the config was generated,
     reads the bridge's own request log and proves the *real* upstream body has
     no `client_metadata` and a string `reasoning_effort`.

The bridge must already be running (see the bridge README).

Usage:
  python3 benchmark/bridges/codex-cliproxy/tests/check_live_gateway.py \
      --env-file benchmark/env.local [--model deepseek-v4-flash ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parents[3]
LOG_DIR = BENCHMARK_DIR / "generated" / "cliproxy-logs"

sys.path.insert(0, str(BENCHMARK_DIR / "scripts"))
from prepare_configs import load_env_file, require  # noqa: E402

failures: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    """Record one acceptance assertion without aborting the run."""
    if condition:
        print(f"  PASS  {label}")
        return
    failures.append(label)
    print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def skip(label: str, detail: str = "") -> None:
    """Record an inconclusive assertion: neither pass nor failure."""
    print(f"  SKIP  {label}" + (f"\n        {detail}" if detail else ""))


# Unique per run so logged upstream bodies can be correlated with *this*
# request instead of whatever entry happens to be newest in the log files.
MARKER_PREFIX = "pa1-live-acceptance"


def codex_request(model: str, effort: str, marker: str | None = None) -> dict:
    """Build a request shaped like Codex 0.151.0's Responses payload."""
    text = "List the files in the current directory."
    if marker:
        text += f" [{MARKER_PREFIX}:{marker}]"
    return {
        "model": model,
        "instructions": "You are Codex, based on GPT-5.",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": text,
                    }
                ],
            }
        ],
        "tools": [
            {
                "type": "function",
                "name": "shell",
                "description": "Run a shell command",
                "strict": False,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["command"],
                },
            }
        ],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
        "reasoning": {"effort": effort, "summary": "auto"},
        "store": False,
        "stream": True,
        "include": ["reasoning.encrypted_content"],
        "prompt_cache_key": "pa1-live-acceptance",
        "client_metadata": {
            "installation_id": "pa1-live",
            "session_id": "sess-1",
            "thread_id": "thread-1",
            "window_id": "window-1",
        },
    }


def post(url: str, api_key: str, payload: dict) -> tuple[int, str]:
    """POST a Responses request and return (status, body)."""
    request = urllib.request.Request(
        url.rstrip("/") + "/responses",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except urllib.error.URLError as error:
        return 0, str(error)


def sse_events(body: str) -> list[dict]:
    """Parse the data frames out of an SSE body."""
    return [json.loads(match) for match in re.findall(r"^data: (\{.*\})$", body, re.M)]


def is_payload_rejection(status: int, body: str) -> bool:
    """True only when the gateway rejected the Codex payload itself.

    The issue #31 control requires the gateway's own validation to reject this
    request shape (HTTP 400 naming the offending fields). Transport failures
    (status 0), authentication problems, DNS/TLS errors, and 5xx responses
    prove nothing about how the gateway treats the payload.
    """
    if status != 400:
        return False
    lowered = body.lower()
    return any(
        hint in lowered
        for hint in (
            "client_metadata",
            "extra inputs are not permitted",
            "reasoning_effort",
        )
    )


def newest_upstream_bodies(since: float) -> list[dict]:
    """Return upstream request bodies from bridge logs written after `since`."""
    bodies: list[dict] = []
    if not LOG_DIR.is_dir():
        return bodies
    for path in LOG_DIR.rglob("*.log"):
        if path.stat().st_mtime < since:
            continue
        text = path.read_text(errors="replace")
        # RecordAPIRequest writes "Body:" followed by the translated payload.
        for match in re.finditer(r"^Body:\s*(\{.*)$", text, re.M):
            try:
                bodies.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                continue
    return bodies


def main() -> int:
    """Run the live acceptance checks and return a process exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--model",
        action="append",
        help="repeatable; defaults to the two Fireworks-backed benchmark models",
    )
    parser.add_argument("--effort", default="max")
    args = parser.parse_args()
    if args.env_file:
        load_env_file(args.env_file)

    models = args.model or ["deepseek-v4-flash", "kimi-k3"]
    gateway_url = require("LITELLM_OPENAI_BASE_URL")
    gateway_key = require("LITELLM_API_KEY")
    bridge_url = require("CODEX_CLIPROXY_BASE_URL")
    bridge_key = require("CODEX_CLIPROXY_API_KEY")

    import time

    for model in models:
        print(f"\n=== {model} (reasoning effort: {args.effort}) ===")

        status, body = post(gateway_url, gateway_key, codex_request(model, args.effort))
        if is_payload_rejection(status, body):
            check(
                True,
                f"{model}: direct gateway still rejects the Codex request",
            )
            print(f"        (control) direct gateway HTTP {status}: {body[:200]}")
        elif status == 200:
            check(
                False,
                f"{model}: direct gateway still rejects the Codex request",
                "the gateway accepted the Codex payload; the issue #31 defect "
                "appears to be fixed upstream, so re-evaluate whether the "
                "bridge is still required",
            )
        else:
            skip(
                f"{model}: direct gateway rejection inconclusive (HTTP {status})",
                "expected a 400 validation error naming the Codex payload "
                "fields; transport, DNS, TLS, authentication, and server "
                f"failures prove nothing about the payload. Body: {body[:200]}",
            )

        started = time.time() - 1
        marker = f"{model}-{uuid.uuid4().hex[:12]}"
        status, body = post(
            bridge_url, bridge_key, codex_request(model, args.effort, marker)
        )
        check(
            status == 200,
            f"{model}: same request succeeds through the bridge",
            f"HTTP {status}: {body[:400]}",
        )
        if status != 200:
            continue

        events = sse_events(body)
        completed = [e for e in events if e.get("type") == "response.completed"]
        check(
            bool(completed),
            f"{model}: bridge streams a terminal response.completed",
            ", ".join(sorted({str(e.get("type")) for e in events}))[:300],
        )
        if not completed:
            continue
        response = completed[0]["response"]
        output = response.get("output", [])
        check(
            any(item.get("type") in {"function_call", "message"} for item in output),
            f"{model}: model produced a tool call or a message",
            json.dumps(output)[:300],
        )
        usage = response.get("usage", {})
        check(
            usage.get("input_tokens", 0) > 0 and usage.get("total_tokens", 0) > 0,
            f"{model}: usage accounting reaches Codex",
            json.dumps(usage),
        )
        print(f"        usage: {json.dumps(usage)}")

        bodies = newest_upstream_bodies(started)
        # Only bodies carrying this request's unique marker belong to it; log
        # files may also hold older or concurrent requests.
        latest = next(
            (
                body
                for body in bodies
                if f"[{MARKER_PREFIX}:{marker}]" in json.dumps(body)
            ),
            None,
        )
        if latest is None:
            hint = (
                "no bridge request logs found. Set "
                "CODEX_CLIPROXY_REQUEST_LOG=true, regenerate, and restart the "
                "bridge to enable it."
                if not bodies
                else "no logged upstream body contains this request's marker."
            )
            skip(f"{model}: upstream body inspection", hint)
            continue
        check(
            "client_metadata" not in latest,
            f"{model}: real upstream request has no client_metadata",
            json.dumps(sorted(latest)),
        )
        check(
            isinstance(latest.get("reasoning_effort"), str),
            f"{model}: real upstream reasoning_effort is a string",
            repr(latest.get("reasoning_effort")),
        )
        check(
            latest.get("reasoning_effort") == args.effort,
            f"{model}: real upstream keeps effort {args.effort!r}",
            repr(latest.get("reasoning_effort")),
        )

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All live acceptance checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
