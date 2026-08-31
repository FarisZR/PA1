#!/usr/bin/env python3
"""Acceptance test for the Codex compatibility bridge (PA1 issue #31).

Runs the pinned CLIProxyAPI image against a recording mock upstream, replaying
the Codex 0.151.0 Responses conversation across several tool turns, and asserts
the properties the benchmark depends on:

  * Codex's `client_metadata` never reaches the upstream;
  * `reasoning.{effort,summary}` becomes a *string* `reasoning_effort`;
  * `max` reasoning effort survives instead of being snapped down;
  * each turn's full `reasoning_content` is replayed verbatim on the next
    request, attached to the same assistant message as its tool call;
  * upstream token usage, including reasoning and cached tokens, reaches Codex.

This uses a mock rather than the live gateway on purpose: the reasoning-replay
assertions need a byte-exact expected value. Run `check_live_gateway.py` for the
end-to-end check against the real gateway.

Usage:  python3 benchmark/bridges/codex-cliproxy/tests/test_codex_translation.py
Requires Docker. Exits non-zero on the first failed check.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BRIDGE_DIR = TESTS_DIR.parent
COMPOSE_FILE = BRIDGE_DIR / "compose.yaml"

BRIDGE_KEY = "pa1-acceptance-bridge-key"
UPSTREAM_KEY = "pa1-acceptance-upstream-key"
MODEL = "deepseek-v4-flash"
NETWORK = "pa1-cliproxy-acceptance"
MOCK_NAME = "pa1-cliproxy-acceptance-mock"
BRIDGE_NAME = "pa1-cliproxy-acceptance-bridge"
HOST_PORT = 18999

failures: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    """Record one acceptance assertion without aborting the run."""
    if condition:
        print(f"  PASS  {label}")
        return
    failures.append(label)
    print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def bridge_image() -> str:
    """Read the pinned image reference straight out of compose.yaml."""
    match = re.search(r"^\s*image:\s*(\S+)\s*$", COMPOSE_FILE.read_text(), re.M)
    if not match:
        raise SystemExit(f"No image: line found in {COMPOSE_FILE}")
    return match.group(1)


def run(*args: str, check_rc: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one docker command."""
    result = subprocess.run(args, capture_output=True, text=True)
    if check_rc and result.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{result.stderr}")
    return result


def cleanup() -> None:
    """Remove anything this test created, ignoring what is already gone."""
    run("docker", "rm", "-f", MOCK_NAME, BRIDGE_NAME, check_rc=False)
    run("docker", "network", "rm", NETWORK, check_rc=False)


def codex_request(model: str, effort: str, history: list[dict]) -> dict:
    """Build a request shaped like Codex 0.151.0's Responses payload."""
    return {
        "model": model,
        "instructions": "You are Codex, based on GPT-5.",
        "input": history,
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
        "prompt_cache_key": "pa1-acceptance-session",
        # Attached unconditionally by Codex 0.151.0 and rejected by Fireworks;
        # this is the field the bridge exists to drop.
        "client_metadata": {
            "installation_id": "pa1-acceptance",
            "session_id": "sess-1",
            "thread_id": "thread-1",
            "window_id": "window-1",
        },
    }


def post_responses(payload: dict) -> list[dict]:
    """POST to the bridge's /v1/responses and return the parsed SSE events."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{HOST_PORT}/v1/responses",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {BRIDGE_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"Bridge returned HTTP {error.code}: {error.read().decode()[:600]}"
        ) from error
    return [
        json.loads(match)
        for match in re.findall(r"^data: (\{.*\})$", body, re.M)
    ]


def completed_response(events: list[dict]) -> dict:
    """Return the response object from the terminal response.completed event."""
    for event in events:
        if event.get("type") == "response.completed":
            return event["response"]
    raise SystemExit(
        "No response.completed event. Events seen: "
        + ", ".join(sorted({str(event.get("type")) for event in events}))
    )


def start_stack(record_dir: Path, config_path: Path) -> None:
    """Bring up the mock upstream and the pinned bridge image."""
    run("docker", "network", "create", NETWORK)
    run(
        "docker", "run", "-d", "--name", MOCK_NAME,
        "--network", NETWORK, "--network-alias", "mock-upstream",
        "-v", f"{TESTS_DIR}:/app:ro",
        "-v", f"{record_dir}:/records",
        "-w", "/app",
        "python:3.13-slim", "python", "mock_upstream.py",
    )
    run(
        "docker", "run", "-d", "--name", BRIDGE_NAME,
        "--network", NETWORK,
        "-p", f"127.0.0.1:{HOST_PORT}:8317",
        "-v", f"{config_path}:/CLIProxyAPI/config.yaml:ro",
        bridge_image(),
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            urllib.request.urlopen(
                urllib.request.Request(
                    f"http://127.0.0.1:{HOST_PORT}/v1/models",
                    headers={"Authorization": f"Bearer {BRIDGE_KEY}"},
                ),
                timeout=3,
            )
            return
        except Exception:  # noqa: BLE001 - the bridge is simply not up yet
            time.sleep(1)
    raise SystemExit(
        "Bridge did not become ready:\n"
        + run("docker", "logs", BRIDGE_NAME, check_rc=False).stderr[-3000:]
    )


def main() -> int:
    """Run the acceptance suite and return a process exit code."""
    if shutil.which("docker") is None:
        raise SystemExit("docker is required to run this acceptance test")
    cleanup()
    workdir = Path(tempfile.mkdtemp(prefix="pa1-cliproxy-acceptance-"))
    record_dir = workdir / "records"
    record_dir.mkdir()
    record_dir.chmod(0o777)
    records = record_dir / "requests.jsonl"
    records.touch()
    records.chmod(0o666)

    # Mirrors what prepare_configs.py generates, including the reasoning levels
    # taken from the Codex catalog. Only the upstream URL differs.
    config_path = workdir / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                'host: ""',
                "port: 8317",
                'auth-dir: "/root/.cli-proxy-api"',
                "api-keys:",
                f'  - "{BRIDGE_KEY}"',
                "request-retry: 0",
                "disable-cooling: true",
                "openai-compatibility:",
                '  - name: "litellm"',
                '    base-url: "http://mock-upstream:9111/v1"',
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
    )

    try:
        start_stack(record_dir, config_path)

        print("\nTurn 1: first Codex request")
        history: list[dict] = [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Fix the failing test."}],
            }
        ]
        expected_reasoning: list[str] = []

        turns = 3
        for turn in range(1, turns + 1):
            if turn > 1:
                print(f"\nTurn {turn}: replayed history with prior reasoning")
            events = post_responses(codex_request(MODEL, "max", history))
            response = completed_response(events)
            output = response["output"]

            reasoning_items = [i for i in output if i["type"] == "reasoning"]
            call_items = [i for i in output if i["type"] == "function_call"]
            check(
                len(reasoning_items) == 1,
                f"turn {turn}: bridge emits a reasoning item",
                json.dumps(output)[:400],
            )
            check(
                len(call_items) == 1,
                f"turn {turn}: bridge emits the tool call",
                json.dumps(output)[:400],
            )
            if not reasoning_items or not call_items:
                break

            summary = reasoning_items[0]["summary"][0]["text"]
            expected_reasoning.append(summary)
            check(
                f"REASONING_TURN_{turn}_ALPHA" in summary
                and f"REASONING_TURN_{turn}_OMEGA" in summary,
                f"turn {turn}: reasoning reaches Codex whole, not truncated",
                summary[:200],
            )

            usage = response["usage"]
            check(
                usage["output_tokens_details"]["reasoning_tokens"] == 400 + turn,
                f"turn {turn}: upstream reasoning_tokens reach Codex",
                json.dumps(usage),
            )
            check(
                usage["input_tokens_details"]["cached_tokens"] == 100 + turn,
                f"turn {turn}: upstream cached_tokens reach Codex",
                json.dumps(usage),
            )
            check(
                usage["input_tokens"] == 1000 + turn
                and usage["output_tokens"] == 500 + turn,
                f"turn {turn}: input/output token counts match upstream",
                json.dumps(usage),
            )

            # Feed the model's own output back exactly as Codex would.
            history.extend(
                [
                    reasoning_items[0],
                    call_items[0],
                    {
                        "type": "function_call_output",
                        "call_id": call_items[0]["call_id"],
                        "output": f"turn{turn}\n",
                    },
                ]
            )

        print("\nUpstream requests recorded by the mock gateway")
        upstream = [json.loads(line) for line in records.read_text().splitlines()]
        check(
            len(upstream) == turns,
            f"exactly {turns} upstream requests",
            f"got {len(upstream)}",
        )

        for index, record in enumerate(upstream, start=1):
            body = record["body"]
            check(
                "client_metadata" not in body,
                f"request {index}: client_metadata is absent upstream",
                json.dumps(sorted(body)),
            )
            check(
                "metadata" not in body and "include" not in body,
                f"request {index}: other Codex-only fields are absent upstream",
                json.dumps(sorted(body)),
            )
            check(
                isinstance(body.get("reasoning_effort"), str),
                f"request {index}: reasoning_effort is a JSON string",
                repr(body.get("reasoning_effort")),
            )
            check(
                body.get("reasoning_effort") == "max",
                f"request {index}: max effort is not downgraded",
                repr(body.get("reasoning_effort")),
            )
            check(
                record["path"].endswith("/chat/completions"),
                f"request {index}: routed to Chat Completions",
                record["path"],
            )

        print("\nReasoning replay across turns")
        for index, record in enumerate(upstream[1:], start=2):
            messages = record["body"]["messages"]
            assistants = [m for m in messages if m.get("role") == "assistant"]
            check(
                len(assistants) == index - 1,
                f"request {index}: carries {index - 1} prior assistant turn(s)",
                json.dumps([m.get("role") for m in messages]),
            )
            for prior, assistant in enumerate(assistants):
                check(
                    assistant.get("reasoning_content") == expected_reasoning[prior],
                    f"request {index}: turn {prior + 1} reasoning replayed verbatim",
                    f"want {expected_reasoning[prior]!r}\n"
                    f"        got  {assistant.get('reasoning_content')!r}",
                )
                check(
                    bool(assistant.get("tool_calls")),
                    f"request {index}: turn {prior + 1} reasoning shares the "
                    "assistant message with its tool call",
                    json.dumps(assistant)[:300],
                )
    finally:
        if failures:
            logs = run("docker", "logs", BRIDGE_NAME, check_rc=False)
            print("\nBridge logs (tail):\n" + (logs.stdout + logs.stderr)[-2000:])
        cleanup()
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All acceptance checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
