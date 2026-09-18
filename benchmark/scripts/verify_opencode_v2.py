#!/usr/bin/env python3
"""Acceptance verifier for the PA1 OpenCode V2 integration.

Offline mode drives the real Pier ``opencode-v2`` adapter inside a disposable
task workspace against a fake OpenAI-compatible provider, so every machine
assertion (model pinning, reasoning control, max_tokens on the wire, usage
extraction, compaction, isolation, fault behavior) runs against the actual
harness without spending money.

Live mode runs the same adapter against the existing LiteLLM gateway through a
transparent recorder, under a persisted atomic spend ledger and hard caps.

Usage:
    python3 benchmark/scripts/verify_opencode_v2.py --pier-root ~/pier \
        --mode offline --output-dir /absolute/path/to/evidence/offline
    python3 benchmark/scripts/verify_opencode_v2.py --pier-root ~/pier \
        --mode live --env-file benchmark/env.local \
        --output-dir /absolute/path/to/evidence/live \
        --model glm-5p3-flash --variant low --max-cost-usd 2.00
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import os
import signal
import socket
import ssl
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

BENCHMARK_DIR = Path(__file__).resolve().parents[1]


def load_cli_pin(benchmark_dir: Path = BENCHMARK_DIR) -> dict[str, str]:
    """Read release selection from committed jobs and verify recorded provenance."""
    import hashlib

    import yaml

    smoke = benchmark_dir / "configs/opencode-v2/smoke.yaml"
    job = yaml.safe_load(smoke.read_text())
    kwargs = job["agents"][0]["kwargs"]
    version = str(kwargs["version"])
    checksums = kwargs["opencode_v2_checksums"]
    reference = json.loads(
        (benchmark_dir / "references/opencode-v2-glm-5.3-flash.json").read_text()
    )
    if reference["version"] != version or reference["sha256"] != checksums["linux-x64"]:
        raise ValueError("OpenCode release provenance does not match the smoke config")
    catalog_path = benchmark_dir / "references/opencode-v2-model-catalog-2.0.8.json"
    catalog_sha256 = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
    if catalog_sha256 != reference["catalog"]["sha256"]:
        raise ValueError("OpenCode model catalog provenance does not match its bytes")
    for path in (
        *sorted((benchmark_dir / "configs/opencode-v2").glob("*.yaml")),
        *sorted((benchmark_dir / "configs/opencode-v2").glob("*.yaml")),
    ):
        for agent in (yaml.safe_load(path.read_text()) or {}).get("agents", []):
            if agent.get("name") != "opencode-v2":
                continue
            selected = agent.get("kwargs") or {}
            if (
                str(selected.get("version")) != version
                or selected.get("opencode_v2_checksums") != checksums
                or selected.get("model_catalog_file")
                != "benchmark/references/opencode-v2-model-catalog-2.0.8.json"
            ):
                raise ValueError(f"{path}: release pin differs from the smoke config")
    return {
        "version": version,
        "tarball_sha256": checksums["linux-x64"],
        "binary_sha256": reference["binary_sha256"],
    }


CLI_PIN = load_cli_pin()
OFFLINE_CLI_VERSION = CLI_PIN["version"]
OFFLINE_CLI_TARBALL_SHA256 = CLI_PIN["tarball_sha256"]
OFFLINE_CLI_BINARY_SHA256 = CLI_PIN["binary_sha256"]
OFFLINE_CLI_TARBALL_NAME = f"opencode-cli-linux-x64-{OFFLINE_CLI_VERSION}.tgz"
LIVE_ENV_KEYS = ("LITELLM_API_KEY", "LITELLM_OPENAI_BASE_URL", "PIER_EXTRA_CA_CERTS")

failures: list[str] = []
passes: list[str] = []
blocked: list[str] = []
evidence: dict[str, Any] = {}


def check(condition: bool, label: str, detail: str = "") -> bool:
    """Record one machine assertion."""
    if condition:
        passes.append(label)
        print(f"  PASS  {label}")
        return True
    failures.append(label)
    print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))
    return False


def block(label: str, detail: str) -> None:
    """Record blocked evidence: something could not be verified, not skipped."""
    blocked.append(label)
    print(f"  BLOCKED  {label}\n        {detail}")


# ---------------------------------------------------------------------------
# Fake OpenAI-compatible provider
# ---------------------------------------------------------------------------

FAKE_PROVIDER_SOURCE = r'''
"""Minimal OpenAI-compatible Chat Completions endpoint for offline V2 probes.

Serves every scenario the verifier needs, records every request body it sees,
and never talks to the network.
"""
import fcntl
import json
import os
import signal
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RECORD_DIR = Path(os.environ["PA1_FAKE_RECORD_DIR"])
SCENARIO_FILE = Path(os.environ["PA1_FAKE_SCENARIO_FILE"])
STATE_FILE = Path(os.environ["PA1_FAKE_STATE_FILE"])
STATE_LOCK = STATE_FILE.with_suffix(".lock")
IDLE_FILE = Path(os.environ["PA1_FAKE_IDLE_FILE"])


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _record(self, body: dict, response_usage: dict | None = None) -> None:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        request_id = uuid.uuid4().hex
        path = RECORD_DIR / f"req-{request_id}.json"
        path.write_text(json.dumps({
            "request_id": request_id,
            "t": time.time(),
            "scenario": SCENARIO_FILE.read_text().strip(),
            "path": self.path,
            "body": body,
            "response_usage": response_usage,
        }, indent=1))

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {"unparseable": raw.decode(errors="replace")}
        scenario = SCENARIO_FILE.read_text().strip()
        STATE_LOCK.touch(exist_ok=True)
        with STATE_LOCK.open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
            count = state.get(scenario, 0) + 1
            state[scenario] = count
            STATE_FILE.write_text(json.dumps(state))

        failed_attempt = (
            scenario in {"http-401", "server-death"}
            or scenario in {"http-429", "http-503"} and count < 3
            or scenario == "http-429-retry-after" and count < 2
            or scenario == "partial-stream" and count < 2
        )
        response_usage = None
        if not failed_attempt and scenario != "usage-missing":
            if self.path.rstrip("/").endswith("/responses"):
                response_usage = {
                    "input_tokens": 12,
                    "output_tokens": 34,
                    "input_tokens_details": {"cached_tokens": 2},
                    "output_tokens_details": {"reasoning_tokens": 5},
                }
            else:
                response_usage = {
                    "prompt_tokens": 12,
                    "completion_tokens": 34,
                    "prompt_tokens_details": {"cached_tokens": 2},
                    "completion_tokens_details": {"reasoning_tokens": 5},
                }
        self._record(body, response_usage=response_usage)

        # Fireworks Chat Completions rejects the native DeepSeek combination
        # of reasoning_effort and thinking. Keep this fixture strict so the
        # offline acceptance cannot hide a transport/configuration regression.
        if (
            not self.path.rstrip("/").endswith("/responses")
            and "reasoning_effort" in body
            and "thinking" in body
        ):
            self._plain(
                400,
                {
                    "error": {
                        "message": "reasoning_effort and thinking are mutually exclusive",
                        "type": "invalid_request",
                    }
                },
            )
            return

        if scenario == "http-401":
            self._plain(401, {"error": {"message": "bad key", "type": "auth"}})
            return
        if scenario == "http-429" and count < 3:
            self._plain(429, {"error": {"message": "rate limited", "type": "rate"}})
            return
        if scenario == "http-429-retry-after" and count < 2:
            self._plain(
                429,
                {"error": {"message": "rate limited", "type": "rate"}},
                {"Retry-After": "1"},
            )
            return
        if scenario == "http-503" and count < 3:
            self._plain(503, {"error": {"message": "upstream", "type": "server"}})
            return
        if scenario == "server-death":
            self.close_connection = True
            return

        model = body.get("model", "unknown")
        if self.path.rstrip("/").endswith("/responses"):
            self._responses(model)
            return
        if scenario == "partial-stream" and count < 2:
            # One content frame, then cut the connection mid-stream.
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: " + json.dumps({"choices":[{"delta":{"role":"assistant","content":"par"}}]}).encode() + b"\n\n")
            self.wfile.flush()
            self.close_connection = True
            return

        stream_started = False
        idle = float(IDLE_FILE.read_text() or "0")
        if scenario == "slow-silence" and idle:
            # Open the stream, send one frame, then go silent past every
            # reasonable client watchdog.
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            frame = {"choices":[{"delta":{"role":"assistant","content":""}}]}
            self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
            self.wfile.flush()
            time.sleep(idle)
            stream_started = True

        usage_missing = scenario == "usage-missing"
        message_text = json.dumps(body.get("messages") or [])
        if "You MUST summarize the conversation" in message_text or "required summary template" in message_text:
            content = "## Objective\n- Verify native compaction.\n\n## Work State\n### Completed\n- Captured the checkpoint.\n\n## Next Move\n1. Continue the session."
        elif scenario == "cap-8192":
            content = "x" * 9000
        elif scenario == "truncated":
            content = "T" * 400
        else:
            content = "Hello from the PA1 fake provider."

        if not stream_started:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
        role = {"id": "c1", "object": "chat.completion.chunk", "created": 1,
                "model": model, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]}
        self.wfile.write(b"data: " + json.dumps(role).encode() + b"\n\n")
        for word in content.split(" ") if False else [content]:
            frame = {"id": "c1", "object": "chat.completion.chunk", "created": 1,
                     "model": model, "choices": [{"index": 0, "delta": {"content": word}}]}
            self.wfile.write(b"data: " + json.dumps(frame).encode() + b"\n\n")
        self.wfile.flush()
        finish = "length" if scenario == "truncated" else "stop"
        final = {"id": "c1", "object": "chat.completion.chunk", "created": 1,
                 "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
        if not usage_missing:
            final["usage"] = {"prompt_tokens": 12, "completion_tokens": 34,
                              "total_tokens": 46,
                              "prompt_tokens_details": {"cached_tokens": 2},
                              "completion_tokens_details": {"reasoning_tokens": 5}}
        self.wfile.write(b"data: " + json.dumps(final).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _responses(self, model: str) -> None:
        """Return one complete OpenAI Responses SSE turn."""
        response_id = "resp_pa1_offline"
        item_id = "msg_pa1_offline"
        text = "Hello from the PA1 fake Responses provider."
        events = [
            {
                "type": "response.created",
                "sequence_number": 0,
                "response": {
                    "id": response_id,
                    "object": "response",
                    "status": "in_progress",
                    "model": model,
                    "output": [],
                },
            },
            {
                "type": "response.output_item.added",
                "sequence_number": 1,
                "output_index": 0,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                },
            },
            {
                "type": "response.content_part.added",
                "sequence_number": 2,
                "output_index": 0,
                "content_index": 0,
                "item_id": item_id,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
            {
                "type": "response.output_text.delta",
                "sequence_number": 3,
                "output_index": 0,
                "content_index": 0,
                "item_id": item_id,
                "delta": text,
            },
            {
                "type": "response.output_text.done",
                "sequence_number": 4,
                "output_index": 0,
                "content_index": 0,
                "item_id": item_id,
                "text": text,
            },
            {
                "type": "response.output_item.done",
                "sequence_number": 5,
                "output_index": 0,
                "item": {
                    "id": item_id,
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": text, "annotations": []}
                    ],
                },
            },
            {
                "type": "response.completed",
                "sequence_number": 6,
                "response": {
                    "id": response_id,
                    "object": "response",
                    "status": "completed",
                    "model": model,
                    "output": [
                        {
                            "id": item_id,
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": text,
                                    "annotations": [],
                                }
                            ],
                        }
                    ],
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 34,
                        "total_tokens": 46,
                        "input_tokens_details": {"cached_tokens": 2},
                        "output_tokens_details": {"reasoning_tokens": 5},
                    },
                },
            },
        ]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in events:
            payload = json.dumps(event).encode()
            self.wfile.write(b"event: " + event["type"].encode() + b"\n")
            self.wfile.write(b"data: " + payload + b"\n\n")
        self.wfile.flush()

    def _plain(self, code: int, payload: dict, headers: dict | None = None):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


port = int(os.environ.get("PA1_FAKE_PORT", "40987"))
ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


class FakeProvider:
    """The fake provider process plus the scenario switch the verifier drives."""

    def __init__(self, workdir: Path):
        self.workdir = workdir
        self.record_dir = workdir / "requests"
        self.scenario_file = workdir / "scenario"
        self.state_file = workdir / "state.json"
        self.idle_file = workdir / "idle-seconds"
        self.idle_file.write_text("0")
        self.scenario_file.write_text("ok")
        self.source = workdir / "fake_provider.py"
        self.source.write_text(FAKE_PROVIDER_SOURCE)
        # Ask the kernel for an unused loopback port before launching the
        # recorder. The short close/launch gap is harmless for this local
        # fixture and avoids collisions between simultaneous offline checks.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        env = dict(os.environ)
        env.update(
            PA1_FAKE_RECORD_DIR=str(self.record_dir),
            PA1_FAKE_SCENARIO_FILE=str(self.scenario_file),
            PA1_FAKE_STATE_FILE=str(self.state_file),
            PA1_FAKE_IDLE_FILE=str(self.idle_file),
            PA1_FAKE_PORT=str(self.port),
        )
        self._stderr = open(workdir / "fake-provider.err", "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(self.source)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), 0.5):
                    return
            except OSError:
                if self.proc.poll() is not None:
                    raise SystemExit(
                        "fake provider died: "
                        + (workdir / "fake-provider.err").read_text()[-800:]
                    )
                time.sleep(0.2)
        raise SystemExit("fake provider did not become ready")

    def scenario(self, name: str, idle_seconds: float = 0.0) -> None:
        self.scenario_file.write_text(name)
        self.idle_file.write_text(str(idle_seconds))

    def requests(self) -> list[dict]:
        out = []
        for path in sorted(self.record_dir.glob("*.json")):
            out.append(json.loads(path.read_text()))
        return sorted(
            out, key=lambda item: (item.get("t", 0), item.get("request_id", ""))
        )

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self._stderr.close()


# ---------------------------------------------------------------------------
# Pier adapter invocation
# ---------------------------------------------------------------------------


def _pier_python(pier_root: Path) -> Path:
    venv = pier_root / ".venv" / "bin" / "python"
    if not venv.exists():
        raise SystemExit(
            f"Pier virtualenv not found at {venv}. Run `uv sync` in {pier_root} first."
        )
    return venv


def stage_binary(installed_agent_dir: Path) -> None:
    """Extract the pinned binary from the reference tarball into the sandbox."""
    import hashlib
    import tarfile

    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    binary = installed_agent_dir / "opencode-v2-bin"
    lock_path = installed_agent_dir / "opencode-v2-bin.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if binary.exists() and file_sha256(binary) == OFFLINE_CLI_BINARY_SHA256:
            return

        tarball = find_pinned_tarball()
        if tarball is None:
            raise SystemExit(
                "Pinned OpenCode binary tarball missing. Place the verified bytes at "
                f"benchmark/references/{OFFLINE_CLI_TARBALL_NAME} or set "
                "OPENCODE_V2_BINARY_CACHE to a local cache file."
            )
        digest = file_sha256(tarball)
        if digest != OFFLINE_CLI_TARBALL_SHA256:
            raise SystemExit(
                f"Pinned OpenCode tarball changed: expected "
                f"{OFFLINE_CLI_TARBALL_SHA256}, got {digest}"
            )

        temporary = installed_agent_dir / f".opencode-v2-bin.{uuid.uuid4().hex}"
        try:
            with tarfile.open(tarball) as archive:
                member = archive.extractfile("package/bin/opencode")
                if member is None:
                    raise SystemExit("pinned tarball has no package/bin/opencode")
                with temporary.open("wb") as stream:
                    for chunk in iter(lambda: member.read(1024 * 1024), b""):
                        stream.write(chunk)
            if file_sha256(temporary) != OFFLINE_CLI_BINARY_SHA256:
                raise SystemExit("extracted OpenCode binary checksum does not match")
            temporary.chmod(0o755)
            os.replace(temporary, binary)
        finally:
            temporary.unlink(missing_ok=True)


def write_restricted_catalog(
    output_dir: Path,
    model_name: str,
    variant: str | None,
    opencode_config: dict,
) -> Path:
    """Create the frozen one-model source used by disposable verifier runs."""
    source = BENCHMARK_DIR / "references/opencode-v2-model-catalog-2.0.8.json"
    catalog = json.loads(source.read_text())
    provider_id, model_id = model_name.split("/", 1)
    provider = catalog.get(provider_id)
    if not isinstance(provider, dict) or model_id not in (provider.get("models") or {}):
        configured_provider = (opencode_config.get("providers") or {}).get(
            provider_id, {}
        )
        configured_model = (configured_provider.get("models") or {}).get(model_id, {})
        package = str(configured_provider.get("package") or "@ai-sdk/openai-compatible")
        package = package.removeprefix("aisdk:")
        capabilities = configured_model.get("capabilities") or {}
        compatibility = configured_model.get("compatibility") or {}
        variants = [
            str(item.get("id"))
            for item in configured_model.get("variants") or []
            if isinstance(item, dict) and item.get("id")
        ]
        if variant and variant not in variants:
            variants.append(variant)
        limits = configured_model.get("limit") or {}
        raw_model: dict[str, Any] = {
            "id": model_id,
            "name": str(configured_model.get("name") or model_id),
            "family": str(configured_model.get("family") or model_id),
            "attachment": any(
                item != "text" for item in capabilities.get("input") or []
            ),
            "reasoning": bool(variants),
            "reasoning_options": [{"type": "effort", "values": variants}],
            "tool_call": bool(capabilities.get("tools", True)),
            "release_date": "2026-01-01",
            "modalities": {
                "input": list(capabilities.get("input") or ["text"]),
                "output": list(capabilities.get("output") or ["text"]),
            },
            "limit": {
                "context": int(limits.get("context") or 1048576),
                "output": int(limits.get("output") or 131072),
            },
        }
        if field := compatibility.get("reasoningField"):
            raw_model["interleaved"] = {"field": field}
        catalog[provider_id] = {
            "id": provider_id,
            "name": str(configured_provider.get("name") or provider_id),
            "env": list(configured_provider.get("env") or []),
            "npm": package,
            "models": {model_id: raw_model},
        }
    selected_provider = copy.deepcopy(catalog[provider_id])
    selected_model = copy.deepcopy(selected_provider["models"][model_id])
    selected_model.pop("experimental", None)
    if variant:
        selected_model["reasoning_options"] = [
            {"type": "effort", "values": [variant]}
        ]
    selected_provider["models"] = {model_id: selected_model}
    catalog = {provider_id: selected_provider}
    path = output_dir / "models.json"
    path.write_text(json.dumps(catalog, separators=(",", ":")))
    return path

def run_pier_agent(
    pier_root: Path,
    *,
    workdir: Path,
    output_dir: Path,
    model_name: str,
    variant: str | None,
    opencode_config: dict,
    base_url: str,
    api_key: str,
    instruction: str,
    env_extra: dict[str, str] | None = None,
    seed_files: dict[str, str] | None = None,
    timeout: int = 300,
) -> dict:
    """Run the real Pier OpenCodeV2 adapter in a disposable workspace.

    The adapter is driven in-process from the Pier checkout with a local exec
    shim that maps the adapter's sandbox paths (``/logs/agent`` etc.) into a
    disposable workspace directory. Install/setup commands run for real so the
    config emission and runner hand-off are exercised; only the actual binary
    download is stubbed, and it is verified separately (provenance check).

    Returns the parsed stdout events plus the driver diagnostics.
    """
    python = _pier_python(pier_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_catalog_file = write_restricted_catalog(
        output_dir, model_name, variant, opencode_config
    )
    sandbox = output_dir / "sandbox"
    pier_logs = output_dir / "pier-logs"
    # The adapter hardcodes container paths (/logs/agent, /installed-agent);
    # the exec shim binds them into this disposable sandbox directory.
    for sub in ("logs/agent", "installed-agent", "work"):
        (sandbox / sub).mkdir(parents=True, exist_ok=True)
    (sandbox / "logs").chmod(0o777)
    (sandbox / "logs" / "agent").chmod(0o777)
    (sandbox / "installed-agent").chmod(0o777)
    (sandbox / "work").chmod(0o777)
    for relative, content in (seed_files or {}).items():
        target = Path(relative)
        if target.is_absolute() or ".." in target.parts:
            raise ValueError(f"unsafe seed path: {relative}")
        destination = sandbox / "work" / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    # Extract the pinned binary once per acceptance output directory. Each
    # disposable trial gets a hard link to the same verified bytes so the
    # fault matrix does not consume hundreds of megabytes per probe.
    stage_binary(workdir)
    cached_binary = workdir / "opencode-v2-bin"
    trial_binary = sandbox / "installed-agent" / "opencode-v2-bin"
    if not trial_binary.exists():
        try:
            os.link(cached_binary, trial_binary)
        except OSError:
            import shutil

            shutil.copy2(cached_binary, trial_binary)
    driver = output_dir / "driver.py"
    driver.write_text(
        textwrap.dedent(
            f"""
            import json, os, shutil, subprocess, sys
            from pathlib import Path
            sys.path.insert(0, {str(pier_root / "src")!r})
            from pier.agents.installed.opencode_v2 import OpenCodeV2
            from pier.models.agent.context import AgentContext

            SANDBOX = Path({str(sandbox.resolve())!r})
            PIER_LOGS = Path({str(pier_logs)!r})
            TASK_WORKDIR = SANDBOX / "work"
            BINDS = {{
                "/logs": SANDBOX / "logs",
                "/installed-agent": SANDBOX / "installed-agent",
                "/tmp/opencode-v2-home": SANDBOX / "home",
                "/tmp/opencode-v2-work": SANDBOX / "work",
            }}

            def rewrite(command: str) -> str:
                for remote, local in list(BINDS.items())[::-1]:
                    command = command.replace(remote, str(local))
                return command

            class LocalEnvironment:
                session_id = "verify"
                persistent_env = {{}}
                # agent_install_spec=None marks "not preinstalled" and would
                # trigger the real download; the offline run skips install and
                # verifies the pinned binary separately.
                agent_install_spec = None
                def agent_process_env(self, env):
                    return {{
                        key: rewrite(value) if isinstance(value, str) else value
                        for key, value in env.items()
                    }}
                async def exec(self, *, command, user=None, env=None, **kwargs):
                    import getpass
                    run_as = []
                    if user == "root":
                        run_as = ["sudo", "-n"]
                    completed = subprocess.run(
                        run_as + ["bash", "-c", rewrite(command)],
                        env={{
                            **os.environ,
                            **{{
                                key: rewrite(value) if isinstance(value, str) else value
                                for key, value in (env or {{}}).items()
                            }},
                        }},
                        capture_output=True, text=True,
                        cwd=rewrite(str(kwargs.get("cwd") or TASK_WORKDIR)),
                        timeout={timeout},
                    )
                    from pier.environments.base import ExecResult
                    return ExecResult(
                        return_code=completed.returncode,
                        stdout=completed.stdout,
                        stderr=completed.stderr,
                    )
                async def upload_file(self, source, target):
                    target = str(target)
                    for remote, local in BINDS.items():
                        if target.startswith(remote):
                            dest = Path(f"{{local}}{{target[len(remote):]}}")
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(Path(source).read_bytes())
                            return
                    raise FileNotFoundError(target)
                async def download_file(self, source, target):
                    source = str(source)
                    for remote, local in BINDS.items():
                        if source.startswith(remote):
                            rel = source[len(remote):].lstrip("/")
                            src = local / rel
                            if src.exists():
                                Path(target).parent.mkdir(parents=True, exist_ok=True)
                                Path(target).write_bytes(src.read_bytes())
                            return
                    raise FileNotFoundError(source)
                async def download_dir(self, source, target):
                    source = str(source)
                    for remote, local in BINDS.items():
                        if source.startswith(remote):
                            rel = source[len(remote):].lstrip("/")
                            src = local / rel
                            shutil.copytree(src, Path(target), dirs_exist_ok=True)
                            return
                    raise FileNotFoundError(source)

            agent = OpenCodeV2(
                logs_dir=PIER_LOGS,
                model_name={model_name!r},
                version={OFFLINE_CLI_VERSION!r},
                restrict_model=True,
                model_catalog_file={str(model_catalog_file)!r},
                variant={variant!r},
                opencode_v2_config={opencode_config!r},
            )
            agent._extra_env.update(
                {{
                    "LITELLM_API_KEY": {api_key!r},
                    "LITELLM_OPENAI_BASE_URL": {base_url!r},
                }}
            )
            import asyncio
            async def setup_shim():
                # Skip the real npm install: bind the locally verified pinned
                # binary into the sandbox and upload the runner next to it.
                # The pinned tarball's SHA-256 has its own provenance check.
                (SANDBOX / "installed-agent").mkdir(parents=True, exist_ok=True)
                local_bin = SANDBOX / "installed-agent" / "opencode-v2-bin"
                if not local_bin.exists():
                    raise RuntimeError(
                        "pinned OpenCode binary not staged at "
                        f"{{local_bin}}; stage it from the verified tarball"
                    )
                await LocalEnvironment().upload_file(
                    Path({str(pier_root / "src/pier/agents/installed/opencode_v2_runner.py")!r}),
                    "/installed-agent/opencode_v2_runner.py",
                )

            try:
                asyncio.run(setup_shim())
            except Exception as error:
                print(f"setup failed: {{type(error).__name__}}: {{error}}", file=sys.stderr)
                sys.exit(3)
            context = AgentContext()
            run_error = None
            try:
                asyncio.run(agent.run({instruction!r}, LocalEnvironment(), context))
            except Exception as error:
                run_error = error
                print(f"run failed: {{type(error).__name__}}: {{error}}", file=sys.stderr)
            try:
                agent.populate_context_post_run(context)
                (PIER_LOGS / "context.json").write_text(
                    json.dumps(context.model_dump(mode="json"), indent=2)
                )
            except Exception as error:
                print(
                    f"post-run conversion failed: {{type(error).__name__}}: {{error}}",
                    file=sys.stderr,
                )
                if run_error is None:
                    run_error = error
            if run_error is not None:
                sys.exit(1)
            """
        ).strip()
    )
    env = dict(os.environ)
    env.update(
        LITELLM_OPENAI_BASE_URL=base_url,
        LITELLM_API_KEY=api_key,
        HOME=str(output_dir / "home"),
    )
    if env_extra:
        env.update(env_extra)
    completed = subprocess.run(
        [str(python), str(driver)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout + 60,
    )
    (output_dir / "driver.stderr").write_text(completed.stderr)
    (output_dir / "driver.stdout").write_text(completed.stdout)
    events = []
    stdout_candidates = (
        pier_logs / "opencode-v2" / "opencode-v2-cli-events.jsonl",
        sandbox / "logs" / "agent" / "opencode-v2-cli-events.jsonl",
        sandbox / "logs" / "agent" / "opencode-v2" / "opencode-v2-cli-events.jsonl",
    )
    stdout_file = next(
        (path for path in stdout_candidates if path.exists()), stdout_candidates[0]
    )
    if stdout_file.exists():
        for line in stdout_file.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    sessions = []
    session_candidates = (
        pier_logs / "opencode-v2" / "opencode-v2-sessions.jsonl",
        sandbox / "logs" / "agent" / "opencode-v2-sessions.jsonl",
        sandbox / "logs" / "agent" / "opencode-v2" / "opencode-v2-sessions.jsonl",
    )
    sessions_file = next(
        (path for path in session_candidates if path.exists()), session_candidates[0]
    )
    if sessions_file.exists():
        for line in sessions_file.read_text().splitlines():
            try:
                sessions.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    trajectory_path = pier_logs / "trajectory.json"
    context_path = pier_logs / "context.json"
    return {
        "returncode": completed.returncode,
        "events": events,
        "sessions": sessions,
        "stderr": completed.stderr,
        "trajectory": json.loads(trajectory_path.read_text())
        if trajectory_path.exists()
        else None,
        "context": json.loads(context_path.read_text())
        if context_path.exists()
        else None,
    }


def wire_requests(requests: list[dict]) -> list[dict]:
    """Return just the Chat Completions bodies the fake provider saw."""
    return [entry["body"] for entry in requests]


def run_limit_only_control(
    pier_root: Path, provider: FakeProvider, root: Path
) -> dict[str, Any]:
    """Run the frozen executable with limit.output and deliberately no body cap."""
    output_dir = root / f"limit-only-{uuid.uuid4().hex[:8]}"
    installed = output_dir / "installed-agent"
    logs = output_dir / "logs"
    task = output_dir / "task"
    home = output_dir / "home"
    for path in (installed, logs, task, home):
        path.mkdir(parents=True, exist_ok=True)
    stage_binary(installed)
    instruction = output_dir / "instruction.txt"
    instruction.write_text("Say hello for the output-limit control.")
    selection = "litellm/glm-5p3-flash"
    config = {
        "model": selection,
        "providers": {
            "litellm": {
                "canonical": "openai",
                "package": "aisdk:@ai-sdk/openai-compatible",
                "settings": {
                    "baseURL": f"http://127.0.0.1:{provider.port}/v1",
                    "apiKey": "offline-test-key",
                },
                "models": {
                    "glm-5p3-flash": {
                        "modelID": "glm-5p3-flash",
                        "limit": {"context": 1048576, "output": 54321},
                        "compatibility": {
                            "maxTokensField": "max_tokens",
                            "reasoningField": "reasoning_content",
                        },
                        "variants": [
                            {
                                "id": "low",
                                "settings": {"reasoningEffort": "low"},
                            }
                        ],
                    }
                },
            }
        },
        "agents": {
            name: {"model": selection}
            for name in ("build", "general", "explore", "compaction")
        },
    }
    config["agents"]["title"] = {"model": selection, "disabled": True}
    config["agents"]["summary"] = {"model": selection, "disabled": True}
    config_path = output_dir / "opencode.json"
    config_path.write_text(json.dumps(config, indent=2))
    env = dict(os.environ)
    password = uuid.uuid4().hex
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_STATE_HOME=str(home / "state"),
        OPENCODE_CONFIG=str(config_path),
        OPENCODE_PASSWORD=password,
        OPENCODE_CONFIG_PROJECT_DISABLE="1",
        OPENCODE_DISABLE_MODELS_FETCH="1",
        PWD=str(task),
    )
    request_offset = len(provider.requests())
    completed = subprocess.run(
        [
            str(_pier_python(pier_root)),
            str(pier_root / "src/pier/agents/installed/opencode_v2_runner.py"),
            "--instruction-file",
            str(instruction),
            "--logs-dir",
            str(logs),
            "--work-dir",
            str(task),
            "--binary",
            str(installed / "opencode-v2-bin"),
            "--config-file",
            str(config_path),
            "--model",
            "litellm/glm-5p3-flash",
            "--variant",
            "low",
            "--title",
            "pier-benchmark",
            "--settle-timeout",
            "60",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    (output_dir / "stderr.txt").write_text(completed.stderr)
    return {
        "returncode": completed.returncode,
        "wire": wire_requests(provider.requests()[request_offset:]),
        "output_dir": output_dir,
    }


def run_native_compaction(
    pier_root: Path, provider: FakeProvider, root: Path
) -> dict[str, Any]:
    """Compact and continue one live server, then use Pier's collector/converter."""
    output_dir = root.resolve() / f"native-compaction-{uuid.uuid4().hex[:8]}"
    installed = output_dir / "installed-agent"
    logs = output_dir / "logs" / "opencode-v2"
    task = output_dir / "task"
    home = output_dir / "home"
    host_logs = output_dir / "pier-logs"
    for path in (installed, logs, task, home, host_logs / "opencode-v2"):
        path.mkdir(parents=True, exist_ok=True)
    stage_binary(installed)
    # Native OpenCode config uses the ``model#variant`` spelling.  The Pier
    # adapter below still receives the base model and variant separately.
    selection = "litellm/glm-5p3-flash#low"
    pier_model = "litellm/glm-5p3-flash"
    config = _cap_config(1048576, 8192)
    config["model"] = selection
    config["agents"] = {
        name: {"model": selection}
        for name in ("build", "general", "explore", "compaction")
    }
    config["agents"]["title"] = {"model": selection, "disabled": True}
    config["agents"]["summary"] = {"model": selection, "disabled": True}
    config["providers"]["litellm"]["settings"]["baseURL"] = (
        f"http://127.0.0.1:{provider.port}/v1"
    )
    config["providers"]["litellm"]["settings"]["apiKey"] = "offline-test-key"
    config_path = output_dir / "opencode.json"
    config_path.write_text(json.dumps(config, indent=2))
    model_catalog_file = write_restricted_catalog(
        output_dir, pier_model, "low", config
    )
    controller = output_dir / "controller.py"
    controller.write_text(
        textwrap.dedent(
            f"""
            import json, os, subprocess, sys, time
            from pathlib import Path
            sys.path.insert(0, {str(pier_root / "src")!r})
            from pier.agents.installed import opencode_v2_runner as runner
            from pier.agents.installed.opencode_v2 import OpenCodeV2
            from pier.models.agent.context import AgentContext

            binary = {str(installed / "opencode-v2-bin")!r}
            task = {str(task)!r}
            logs = Path({str(logs)!r})
            host_logs = Path({str(host_logs)!r})
            config_path = {str(config_path)!r}
            selection = {selection!r}
            env = dict(os.environ)
            server = runner.OpenCodeV2Server(binary, task, env["OPENCODE_PASSWORD"], env)
            inspections = []
            root_id = None
            compact_status = 0
            continued = False
            settled = False
            errors = []

            def run_cli(prompt, session=None):
                command = [binary, "run", "--server", server.url, "--format", "json",
                           "--thinking", "--auto", "-m", selection]
                if session:
                    command += ["--session", session]
                else:
                    command += ["--title", "pier-benchmark"]
                command.append(prompt)
                completed = subprocess.run(command, cwd=task, env=env,
                                           capture_output=True, text=True, timeout=90)
                return completed, runner._events(completed.stdout.splitlines())

            try:
                server.start()
                preflight = runner.preflight_runtime(
                    server,
                    model_spec=selection,
                    config_file=config_path,
                    restrict_model=True,
                )
                first, first_events = run_cli("Create a short checkpoint source message.")
                sessions = server.collect_sessions()
                root_id = runner._root_id(first_events, sessions, set())
                if first.returncode != 0 or not root_id:
                    raise RuntimeError(f"initial run failed: {{first.returncode}} {{first.stderr[-400:]}}")
                before_ids = [m.get("id") for m in server.collect_messages(root_id)]
                compact_status = runner.http_post(
                    server._api_url(f"api/session/{{root_id}}/compact"),
                    server.password,
                    {{}},
                    timeout=60,
                )
                if compact_status not in {{200, 202}}:
                    raise RuntimeError(f"compact returned HTTP {{compact_status}}")
                if not server.wait_session(root_id, timeout=90):
                    raise RuntimeError("native compact wait failed")
                after_compact = server.collect_messages(root_id)
                second, _ = run_cli("Continue after compaction and say continued.", root_id)
                continued = second.returncode == 0
                inspections, settled = runner._collect_tree(
                    server, root_id, deadline=time.monotonic() + 90, errors=errors
                )
            finally:
                server.stop()

            runner.dump_jsonl(logs / "opencode-v2-sessions.jsonl", inspections)
            runner.dump_jsonl(logs / "opencode-v2-raw-pages.jsonl", server.raw_pages)
            result = {{
                "root_id": root_id,
                "collection_complete": settled and not errors,
                "collection_errors": errors,
                "discovered_session_ids": [
                    str(item.get("session", {{}}).get("id")) for item in inspections
                ],
            }}
            (logs / "runner-result.json").write_text(json.dumps(result, indent=2))
            for source in logs.iterdir():
                if source.is_file():
                    (host_logs / "opencode-v2" / source.name).write_bytes(source.read_bytes())
            agent = OpenCodeV2(
                logs_dir=host_logs,
                model_name={pier_model!r},
                version={OFFLINE_CLI_VERSION!r},
                restrict_model=True,
                model_catalog_file={str(model_catalog_file)!r},
                variant="low",
                opencode_v2_config={config!r},
            )
            context = AgentContext()
            agent.populate_context_post_run(context)
            trajectory = json.loads((host_logs / "trajectory.json").read_text())
            compactions = [
                message
                for item in inspections
                for message in item.get("messages", [])
                if message.get("type") == "compaction"
            ]
            output = {{
                "compact_status": compact_status,
                "continued": continued,
                "settled": settled,
                "before_ids": before_ids,
                "prior_ids_retained": all(
                    item in {{m.get("id") for m in after_compact}} for item in before_ids
                ),
                "compaction_records": compactions,
                "trajectory": trajectory,
                "preflight": preflight,
                "errors": errors,
            }}
            Path({str(output_dir / "controller-result.json")!r}).write_text(
                json.dumps(output, indent=2)
            )
            """
        ).strip()
    )
    env = dict(os.environ)
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_STATE_HOME=str(home / "state"),
        OPENCODE_CONFIG=str(config_path),
        OPENCODE_MODELS_PATH=str(model_catalog_file),
        OPENCODE_PASSWORD=uuid.uuid4().hex,
        OPENCODE_CONFIG_PROJECT_DISABLE="1",
        OPENCODE_DISABLE_MODELS_FETCH="1",
        PWD=str(task),
    )
    request_offset = len(provider.requests())
    completed = subprocess.run(
        [str(_pier_python(pier_root)), str(controller)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    (output_dir / "controller.stderr").write_text(completed.stderr)
    result_path = output_dir / "controller-result.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    result.update(
        returncode=completed.returncode,
        stderr=completed.stderr,
        wire=wire_requests(provider.requests()[request_offset:]),
        output_dir=str(output_dir),
    )
    return result


# ---------------------------------------------------------------------------
# Offline assertions
# ---------------------------------------------------------------------------


def find_finish(events: list[dict]) -> dict | None:
    for event in events:
        if event.get("type") == "step_finish":
            return event
    return None


def find_result_finish(result: dict) -> dict | None:
    """Use CLI finish events when present, then persisted assistant records.

    V2's JSON CLI stream is a status view and may omit ``step_finish`` even
    though the server has durably recorded an assistant message with
    authoritative usage. The adapter's collector intentionally treats the
    persisted record as the source of truth, so the verifier does the same.
    """
    finish = find_finish(result.get("events") or [])
    if finish is not None:
        return finish
    for inspection in result.get("sessions") or []:
        for message in reversed(inspection.get("messages") or []):
            if message.get("type") != "assistant":
                continue
            if not message.get("finish") and not message.get("tokens"):
                continue
            return {
                "finish": message.get("finish"),
                "part": {"tokens": message.get("tokens") or {}},
            }
    return None


def find_errors(events: list[dict]) -> list[str]:
    out = []
    for event in events:
        if event.get("type") == "error":
            error = event.get("error") or {}
            out.append(str(error.get("message") or error.get("type") or error))
    return out


def config_written_to(output_dir: Path) -> dict:
    """Reconstruct the opencode.json the adapter wrote (from the driver capture)."""
    path = output_dir / "captured-opencode.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def offline_probe(
    pier_root: Path,
    provider: FakeProvider,
    root: Path,
    *,
    model_name: str = "litellm/glm-5p3-flash",
    variant: str | None = "low",
    opencode_config: dict | None = None,
    instruction: str = "Say hello.",
    max_output_tokens: int = 131072,
    metadata_output_tokens: int | None = None,
) -> dict:
    """One adapter run against one scenario; returns driver + wire capture."""
    selected_model = model_name
    output_dir = root / f"probe-{uuid.uuid4().hex[:8]}"
    output_dir.mkdir(parents=True)
    config = opencode_config or {
        "providers": {
            "litellm": {
                "name": "LiteLLM",
                "canonical": "openai",
                "env": ["LITELLM_API_KEY"],
                "package": "aisdk:@ai-sdk/openai-compatible",
                "settings": {"baseURL": "__BASE_URL__"},
                "models": {
                    "glm-5p3-flash": {
                        "modelID": "glm-5p3-flash",
                        "name": "GLM-5.3-Flash",
                        "limit": {
                            "context": 1048576,
                            "output": metadata_output_tokens or max_output_tokens,
                        },
                        # V2 does not reliably turn limit.output into a
                        # Chat Completions output field. Keep metadata and the
                        # transport override explicit (PA1 #40).
                        "body": {"max_tokens": max_output_tokens},
                        "compatibility": {
                            "maxTokensField": "max_tokens",
                            "reasoningField": "reasoning_content",
                        },
                        "variants": [
                            {"id": "low", "settings": {"reasoningEffort": "low"}}
                        ],
                    }
                },
            }
        }
    }
    for provider_config in config.get("providers", {}).values():
        settings = provider_config.get("settings") or {}
        if settings.get("baseURL") == "__BASE_URL__":
            settings["baseURL"] = f"http://127.0.0.1:{provider.port}/v1"
    request_offset = len(provider.requests())
    result = run_pier_agent(
        pier_root,
        workdir=root,
        output_dir=output_dir,
        model_name=selected_model,
        variant=variant,
        opencode_config=config,
        base_url=f"http://127.0.0.1:{provider.port}/v1",
        api_key="offline-test-key",
        instruction=instruction,
    )
    request_records = provider.requests()[request_offset:]
    result["output_dir"] = output_dir
    result["request_records"] = request_records
    result["wire"] = wire_requests(request_records)
    return result


def offline_mode(pier_root: Path, output_dir: Path) -> int:
    """Run every offline assertion. Returns a process exit code."""
    print("\n=== offline: pinned binary provenance ===")
    if not verify_offline_provenance(output_dir):
        print("\nPinned binary unavailable; offline suite is blocked.")
        return 1

    focused = subprocess.run(
        [
            str(_pier_python(pier_root)),
            "-m",
            "pytest",
            "-q",
            str(pier_root / "tests/test_opencode_v2.py"),
        ],
        cwd=pier_root,
        capture_output=True,
        text=True,
        timeout=180,
    )
    evidence["pier_fault_matrix"] = {
        "returncode": focused.returncode,
        "summary": (focused.stdout + focused.stderr).strip().splitlines()[-3:],
    }
    check(
        focused.returncode == 0,
        "offline: focused Pier collector/converter fault matrix passes",
        "\n".join(evidence["pier_fault_matrix"]["summary"]),
    )

    print("\n=== offline: fake provider + real adapter ===")
    provider = FakeProvider(output_dir)
    try:
        offline_contract(pier_root, provider, output_dir)
        offline_primary_responses_profiles(pier_root, provider, output_dir)
        offline_compaction(pier_root, provider, output_dir)
        offline_faults(pier_root, provider, output_dir)
        offline_isolation(pier_root, provider, output_dir)
    finally:
        evidence["offline_request_count"] = len(provider.requests())
        if provider.state_file.exists():
            evidence["offline_attempts_by_scenario"] = json.loads(
                provider.state_file.read_text()
            )
        provider.stop()

    print()
    print(f"{len(passes)} passed, {len(failures)} failed, {len(blocked)} blocked")
    if blocked:
        print("Blocked (preserved, not skipped):")
        for item in blocked:
            print(f"  - {item}")
    if failures:
        print("Failed:")
        for item in failures:
            print(f"  - {item}")
    return 1 if failures else 0


def verify_offline_provenance(output_dir: Path) -> bool:
    """Verify the config-pinned @opencode/cli binary without network fetches.

    The verifier accepts the linux-x64 package tarball at
    ``benchmark/references/`` or an explicit local cache path with a known
    SHA-256. If it is absent the check is BLOCKED (evidence preserved), not
    skipped green; offline mode never fetches bytes from the network.
    """
    tarball = find_pinned_tarball()
    expected = OFFLINE_CLI_TARBALL_SHA256
    if tarball is None:
        block(
            f"offline: pinned @opencode/cli {OFFLINE_CLI_VERSION} tarball present",
            "provide the verified local reference file or set "
            "OPENCODE_V2_BINARY_CACHE; no network fetch is attempted by offline mode.",
        )
        return False
    import hashlib

    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    return check(
        digest == expected,
        f"offline: pinned @opencode/cli {OFFLINE_CLI_VERSION} tarball SHA-256 matches",
        f"expected {expected}, got {digest}",
    )


def find_pinned_tarball() -> Path | None:
    """Find the pinned bytes in the checkout or an explicit local cache."""
    candidates = [BENCHMARK_DIR / "references" / OFFLINE_CLI_TARBALL_NAME]
    cache = os.environ.get("OPENCODE_V2_BINARY_CACHE", "").strip()
    if cache:
        candidates.append(Path(cache).expanduser())
    else:
        candidates.append(
            Path.home() / ".cache" / "pa1" / "opencode-v2" / OFFLINE_CLI_TARBALL_NAME
        )
    return next((path for path in candidates if path.is_file()), None)


def offline_contract(pier_root: Path, provider: FakeProvider, root: Path) -> None:
    """Machine assertions on the happy path: identity, control, max_tokens."""
    provider.scenario("ok")
    control = run_limit_only_control(pier_root, provider, root)
    control_wire = [body for body in control["wire"] if body.get("model")]
    control_caps = [
        {
            key: body[key]
            for key in ("max_tokens", "max_completion_tokens", "max_output_tokens")
            if key in body
        }
        for body in control_wire
    ]
    evidence["limit_only_control"] = {
        "request_count": len(control_wire),
        "observed_output_cap_fields": control_caps,
        "returncode": control["returncode"],
    }
    check(
        control["returncode"] == 0 and bool(control_wire),
        "offline: actual-binary limit-only control result is recorded",
        json.dumps(evidence["limit_only_control"]),
    )

    # The production profile is 131072, but the deterministic acceptance
    # control deliberately uses the small explicit body cap from PA1 #40 so a
    # recorder can prove the exact field/value without a long response.
    result = offline_probe(
        pier_root,
        provider,
        root,
        max_output_tokens=8192,
        metadata_output_tokens=54321,
    )
    wire = [body for body in result["wire"] if body.get("model")]
    evidence["body_override"] = {
        "request_count": len(wire),
        "max_tokens": sorted(
            {body.get("max_tokens") for body in wire if "max_tokens" in body}
        ),
        "reasoning_effort": sorted(
            {
                body.get("reasoning_effort")
                for body in wire
                if "reasoning_effort" in body
            }
        ),
    }

    check(
        find_result_finish(result) is not None,
        "offline: adapter run reaches a step_finish on the happy path",
        result["stderr"][-600:],
    )
    check(bool(wire), "offline: fake provider received at least one request")
    if not wire:
        return

    bodies = [
        body for body in wire if "max_tokens" in body or "reasoning_effort" in body
    ]
    check(
        all(body.get("model") == "glm-5p3-flash" for body in wire),
        "offline: every request carries the pinned transport model id",
        str({body.get("model") for body in wire}),
    )
    check(
        all(body.get("reasoning_effort") == "low" for body in bodies),
        "offline: GLM low requests carry reasoning_effort=low",
        str({body.get("reasoning_effort") for body in bodies}),
    )
    check(
        all("thinking" not in body for body in bodies),
        "offline: GLM low requests carry no conflicting thinking control",
        str([body.get("thinking") for body in bodies if "thinking" in body][:2]),
    )
    check(
        all(body.get("max_tokens") == 8192 for body in bodies),
        "offline: Chat Completions bodies carry exactly max_tokens=8192",
        str({body.get("max_tokens") for body in bodies}),
    )
    check(
        all("max_completion_tokens" not in body for body in bodies),
        "offline: no competing max_completion_tokens field is sent",
        str([body for body in bodies if "max_completion_tokens" in body][:1]),
    )
    config_candidates = sorted(
        (result["output_dir"] / "sandbox").glob("home*/config/opencode/opencode.json")
    )
    if not config_candidates:
        check(
            False,
            "offline: generated control config is inspectable",
            "config file missing",
        )
        return
    try:
        runtime_config_path = config_candidates[0]
        runtime_config = json.loads(runtime_config_path.read_text())
        model_config = runtime_config["providers"]["litellm"]["models"]["glm-5p3-flash"]
        check(
            model_config["limit"]["output"] == 54321,
            "offline: limit.output=54321 remains metadata in the control",
            json.dumps(model_config),
        )
        check(
            model_config["body"] == {"max_tokens": 8192},
            "offline: model body override is exactly max_tokens=8192",
            json.dumps(model_config),
        )
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        check(False, "offline: generated control config is inspectable", str(error))

    finish = find_result_finish(result)
    if check(finish is not None, "offline: trajectory contains a step_finish"):
        tokens = (finish or {}).get("part", {}).get("tokens", {})
        check(
            tokens.get("input", 0) or tokens.get("output", 0),
            "offline: usage metadata present on the happy path",
            json.dumps(tokens),
        )
        agent_steps = [
            step
            for step in (result.get("trajectory") or {}).get("steps", [])
            if step.get("source") == "agent"
        ]
        atif_completion = (
            (agent_steps[0].get("metrics") or {}).get("completion_tokens")
            if agent_steps
            else None
        )
        provider_record = next(
            (
                record
                for record in result.get("request_records", [])
                if record.get("response_usage")
            ),
            {},
        )
        provider_usage = provider_record.get("response_usage") or {}
        provider_completion_tokens = provider_usage.get("completion_tokens")
        if provider_completion_tokens is None:
            provider_completion_tokens = provider_usage.get("output_tokens")
        completion_details = provider_usage.get("completion_tokens_details") or {}
        if not completion_details:
            completion_details = provider_usage.get("output_tokens_details") or {}
        provider_reasoning_tokens = completion_details.get("reasoning_tokens")
        evidence["normalized_usage_contract"] = {
            "provider_usage": provider_usage,
            "provider_completion_tokens": provider_completion_tokens,
            "provider_reasoning_tokens": provider_reasoning_tokens,
            "persisted_visible_output_tokens": tokens.get("output"),
            "persisted_reasoning_tokens": tokens.get("reasoning"),
            "atif_completion_tokens": atif_completion,
        }
        check(
            provider_completion_tokens is not None
            and provider_reasoning_tokens is not None
            and tokens.get("output")
            == provider_completion_tokens - provider_reasoning_tokens
            and tokens.get("reasoning") == provider_reasoning_tokens
            and atif_completion == provider_completion_tokens,
            "offline: provider completion is split into visible output plus reasoning and reconstructed once",
            json.dumps(evidence["normalized_usage_contract"], sort_keys=True),
        )


def offline_primary_responses_profiles(
    pier_root: Path, provider: FakeProvider, root: Path
) -> None:
    """Execute the committed primary profiles on the pinned executable."""
    try:
        import yaml
    except ImportError as error:  # pragma: no cover - environment prerequisite
        raise SystemExit(
            "offline primary-profile verification requires PyYAML to read committed YAML"
        ) from error

    primary_dir = BENCHMARK_DIR / "configs" / "opencode-v2"
    cases = (
        (
            "kimi-k3.yaml",
            "/v1/chat/completions",
            "max_tokens",
            131072,
            {},
            "@opencode/ai/providers/fireworks",
        ),
        (
            "deepseek-v4p1-flash.yaml",
            "/v1/chat/completions",
            "max_tokens",
            384000,
            {},
            "@opencode/ai/providers/fireworks",
        ),
        (
            "luna.yaml",
            "/v1/responses",
            "max_output_tokens",
            128000,
            # PA1 #17 holds Luna to 272,000 tokens in every harness; the
            # catalogue's inherited 1,050,000 window crosses the 2x input
            # price tier that pricing.yaml does not model.
            {"context": 272000, "input": 144000, "output": 128000},
            None,
        ),
    )
    evidence["offline_primary_responses"] = {}
    for (
        filename,
        expected_path,
        cap_field,
        cap_value,
        expected_limits,
        expected_package,
    ) in cases:
        document = yaml.safe_load((primary_dir / filename).read_text()) or {}
        agent = (document.get("agents") or [{}])[0]
        kwargs = agent.get("kwargs") or {}
        model_name = agent.get("model_name")
        variant = kwargs.get("variant")
        config = copy.deepcopy(kwargs.get("opencode_v2_config") or {})
        model_id = model_name.split("/", 1)[1] if "/" in model_name else model_name
        provider_name = model_name.split("/", 1)[0]
        provider_config = (config.get("providers") or {}).get(provider_name) or {}
        configured_model = config.get("model")
        model_config = (provider_config.get("models") or {}).get(model_id) or {}
        expected_websearch = (
            {"provider": "random"} if filename == "kimi-k3.yaml" else False
        )
        check(
            configured_model is None,
            f"offline: primary {filename} leaves model pinning to adapter kwargs",
            json.dumps({"model_name": model_name, "config_model": configured_model}),
        )
        check(
            config.get("websearch") == expected_websearch,
            f"offline: primary {filename} has the expected web-search policy",
            json.dumps(config.get("websearch")),
        )
        check(
            (model_config.get("body") or {}).get(cap_field) == cap_value,
            f"offline: primary {filename} declares its transport output cap",
            json.dumps(model_config),
        )
        if filename == "luna.yaml":
            policy_limit = yaml.safe_load(
                (BENCHMARK_DIR / "pricing.yaml").read_text()
            )["models"]["gpt-5.6-luna"]["context_limit"]
            check(
                (model_config.get("limit") or {}).get("context") == policy_limit,
                "offline: primary luna.yaml matches the pricing.yaml context policy",
                json.dumps(
                    {
                        "configured": (model_config.get("limit") or {}).get("context"),
                        "pricing_yaml": policy_limit,
                    }
                ),
            )
        check(
            (model_config.get("limit") or {}) == expected_limits,
            f"offline: primary {filename} carries the committed limit overlay",
            json.dumps(model_config.get("limit") or {}),
        )
        check(
            provider_config.get("package") == expected_package,
            f"offline: primary {filename} uses the expected provider package",
            json.dumps(provider_config),
        )
        provider.scenario("ok")
        result = offline_probe(
            pier_root,
            provider,
            root,
            model_name=model_name,
            variant=variant,
            opencode_config=config,
        )
        request = result["request_records"][0] if result["request_records"] else {}
        body = request.get("body") or {}
        details = {
            "returncode": result["returncode"],
            "request_count": len(result["request_records"]),
            "path": request.get("path"),
            "model": body.get("model"),
            "configured_model": configured_model,
            "variant": variant,
            "package": provider_config.get("package"),
            "limits": model_config.get("limit") or {},
            "output_cap": body.get(cap_field),
            "reasoning_effort": body.get("reasoning_effort"),
            "reasoning": body.get("reasoning"),
            "thinking_present": "thinking" in body,
            "errors": find_errors(result["events"]),
        }
        evidence["offline_primary_responses"][filename] = details
        check(
            result["returncode"] == 0
            and details["request_count"] == 1
            and details["path"] == expected_path
            and details["model"] == model_id
            and details["output_cap"] == cap_value
            and not (
                "reasoning_effort" in body and "thinking" in body
            )
            and (
                details["reasoning_effort"] == "max"
                if expected_path == "/v1/chat/completions"
                else (details["reasoning"] or {}).get("effort") == "max"
                and set(details["reasoning"] or {}) <= {"effort", "summary"}
            )
            and not details["errors"],
            f"offline: primary {filename} executes",
            json.dumps(details),
        )


def offline_compaction(pier_root: Path, provider: FakeProvider, root: Path) -> None:
    """Native compact + continuation on one session identity."""
    provider.scenario("ok")
    result = run_native_compaction(pier_root, provider, root)
    wire = [body for body in result["wire"] if body.get("model")]
    models = {body.get("model") for body in wire}
    check(
        result["returncode"] == 0
        and result.get("compact_status") in {200, 202}
        and result.get("continued")
        and result.get("settled"),
        "offline: native compact operation completes and the same session continues",
        result.get("stderr", "")[-600:],
    )
    check(
        result.get("prior_ids_retained") is True,
        "offline: pre-compaction messages remain collectible",
        json.dumps(result.get("before_ids")),
    )
    check(
        len(result.get("compaction_records") or []) == 1,
        "offline: one authoritative compaction record is persisted",
        json.dumps(result.get("compaction_records") or [])[:800],
    )
    trajectory = result.get("trajectory") or {}
    compact_steps = [
        step for step in trajectory.get("steps") or [] if step.get("source") == "system"
    ]
    check(
        len(compact_steps) == 1
        and (trajectory.get("final_metrics", {}).get("extra") or {}).get(
            "summarization_count"
        )
        == 1,
        "offline: converter includes compaction usage exactly once",
        json.dumps(trajectory.get("final_metrics") or {}),
    )
    check(
        models == {"glm-5p3-flash"},
        "offline: root, compaction, and continuation keep one model identity",
        str(models),
    )
    check(
        all(
            body.get("reasoning_effort") == "low"
            and body.get("max_tokens") == 8192
            and "thinking" not in body
            for body in wire
        ),
        "offline: compaction request preserves low effort and output-body policy",
        json.dumps(wire[:3]),
    )


def offline_faults(pier_root: Path, provider: FakeProvider, root: Path) -> None:
    """Fault matrix: 429/503 retry, 401 permanent, partial stream, truncation,
    missing usage, timeout, server death, slow silent stream, caps."""
    cases = [
        ("http-429", "offline: 429s are retried and then recover"),
        ("http-503", "offline: 503s are retried and then recover"),
        (
            "partial-stream",
            "offline: a partial stream fails or recovers instead of hanging",
        ),
        (
            "usage-missing",
            "offline: capture-only missing usage is classified explicitly",
        ),
        ("truncated", "offline: truncated output is visible in the trajectory"),
        ("server-death", "offline: server death fails or recovers instead of hanging"),
    ]
    for scenario, label in cases:
        provider.scenario(scenario)
        result = offline_probe(pier_root, provider, root)
        finished = find_result_finish(result)
        errors = find_errors(result["events"])
        if scenario in {"http-429", "http-503"}:
            timestamps = [
                float(record["t"]) for record in result.get("request_records") or []
            ]
            delays = [
                round(later - earlier, 3)
                for earlier, later in zip(timestamps, timestamps[1:])
            ]
            evidence[f"{scenario}_retry"] = {
                "attempts": len(timestamps),
                "delays_seconds": delays,
            }
            check(
                finished is not None
                and len(timestamps) == 3
                and all(d > 0 for d in delays),
                label,
                json.dumps(evidence[f"{scenario}_retry"]),
            )
        elif scenario == "usage-missing":
            tokens = (finished or {}).get("part", {}).get("tokens", {})
            evidence["capture_only_usage_gap"] = (
                "The frozen SDK persists an all-zero normalized usage object when "
                "the provider omits usage. Production HTTP collection cannot "
                "distinguish that from a provider-reported zero; recorder-only "
                "usage is never added to ATIF."
            )
            check(
                finished is not None
                and set(tokens) >= {"input", "output", "reasoning", "cache"}
                and not tokens.get("input")
                and not tokens.get("output")
                and not tokens.get("reasoning")
                and not (tokens.get("cache") or {}).get("read")
                and not (tokens.get("cache") or {}).get("write"),
                label,
                json.dumps(tokens),
            )
        else:
            check(
                finished is not None or bool(errors) or result["returncode"] != 0,
                label,
                f"returncode={result['returncode']} events={result['events'][-1:]}",
            )

    provider.scenario("http-429-retry-after")
    retry_after = offline_probe(pier_root, provider, root)
    timestamps = [
        float(record["t"]) for record in retry_after.get("request_records") or []
    ]
    delays = [later - earlier for earlier, later in zip(timestamps, timestamps[1:])]
    evidence["retry_after"] = {
        "attempts": len(timestamps),
        "delays_seconds": [round(value, 3) for value in delays],
    }
    check(
        find_result_finish(retry_after) is not None
        and len(timestamps) == 2
        and bool(delays)
        and delays[0] >= 0.9,
        "offline: native retry honors Retry-After before recovery",
        json.dumps(evidence["retry_after"]),
    )
    # PA1 #37 target (~3 minutes of transport tolerance) is not configurable
    # in the pinned executable; recorded as a gap with the observed
    # native schedule rather than silently claimed as met.
    evidence["retry_policy_gap"] = (
        f"Frozen OpenCode {OFFLINE_CLI_VERSION} exposes no retry-schedule configuration; its "
        "native short exponential allowance was observed above. PA1 #37's "
        "approximately three-minute goal is NOT established by this run and "
        "needs either an upstream configuration surface or a tracked harness "
        "exception."
    )

    # Permanent 401: the adapter must surface the provider error, not retry forever.
    provider.scenario("http-401")
    started = time.time()
    result = offline_probe(pier_root, provider, root)
    elapsed = time.time() - started
    check(
        find_errors(result["events"]) or result["returncode"] != 0,
        "offline: a permanent 401 surfaces as an error",
        result["events"][-1:],
    )
    check(
        elapsed < 120,
        "offline: a permanent 401 is not retried for minutes",
        f"{elapsed:.0f}s",
    )

    # Slow silent stream: the wrapper must not add a short idle timeout.
    provider.scenario("slow-silence", idle_seconds=2.0)
    started = time.time()
    result = offline_probe(pier_root, provider, root, instruction="wait")
    elapsed = time.time() - started
    check(
        elapsed >= 2.0 and find_result_finish(result) is not None,
        "offline: a silent stream survives the deliberate quiet interval",
        f"{elapsed:.0f}s",
    )

    # Context-cap probes: 256 and 8192 with a >2048-token response.
    for cap, label in ((256, "offline: tiny cap 256 probe"),):
        provider.scenario("cap-256")
        result = offline_probe(
            pier_root,
            provider,
            root,
            opencode_config=_cap_config(256, 256),
            instruction="probe",
        )
        check(
            find_result_finish(result) or find_errors(result["events"]),
            label + " completes or fails explicitly",
        )
    provider.scenario("cap-8192")
    result = offline_probe(
        pier_root,
        provider,
        root,
        opencode_config=_cap_config(8192, 8192),
        instruction="probe",
    )
    # The 9000-char fake response exceeds 2048 tokens; assert the run either
    # completed or failed with an explicit error (never a silent hang), and
    # that a large individual response was produced and recorded.
    finished = find_result_finish(result)
    errors = find_errors(result["events"])
    large = any(
        len(str(part.get("text") or "")) > 2048
        for session in result["sessions"]
        for message in session.get("messages", [])
        for part in (message.get("content") or [])
        if isinstance(part, dict)
    )
    check(
        (finished is not None or bool(errors)) and (large or errors),
        "offline: large cap 8192 with a >2048-token response completes or fails explicitly",
        f"large={large} errors={errors[:1]}",
    )


def _cap_config(context_cap: int, output_cap: int = 131072) -> dict:
    return {
        "providers": {
            "litellm": {
                "name": "LiteLLM",
                "canonical": "openai",
                "env": ["LITELLM_API_KEY"],
                "package": "aisdk:@ai-sdk/openai-compatible",
                "settings": {"baseURL": "__BASE_URL__"},
                "models": {
                    "glm-5p3-flash": {
                        "modelID": "glm-5p3-flash",
                        "name": "GLM-5.3-Flash",
                        "limit": {"context": context_cap, "output": output_cap},
                        "body": {"max_tokens": output_cap},
                        "cost": {
                            "input": 0.15,
                            "output": 0.5,
                            "cache": {"read": 0.03, "write": 0.0},
                        },
                        "compatibility": {
                            "maxTokensField": "max_tokens",
                            "reasoningField": "reasoning_content",
                        },
                        "variants": [
                            {"id": "low", "settings": {"reasoningEffort": "low"}}
                        ],
                    }
                },
            }
        }
    }


def offline_isolation(pier_root: Path, provider: FakeProvider, root: Path) -> None:
    """Isolation trials: wrong child model and subagent identity."""
    provider.scenario("ok")

    # A caller-supplied conflicting subagent variant is overwritten after the
    # config merge. The caller's permissions survive, while every request and
    # the saved runtime config resolve to the selected low identity.
    conflicting = _cap_config(1048576, 8192)
    conflicting["agents"] = {
        "general": {
            "model": "litellm/glm-5p3-flash#max",
            "permission": {"read": "allow"},
        }
    }
    result = offline_probe(
        pier_root,
        provider,
        root,
        opencode_config=conflicting,
    )
    configs = sorted(
        (result["output_dir"] / "sandbox").glob("home*/config/opencode/opencode.json")
    )
    runtime = json.loads(configs[0].read_text()) if configs else {}
    general = (runtime.get("agents") or {}).get("general") or {}
    check(
        result["returncode"] == 0
        and general.get("model") == "litellm/glm-5p3-flash#low"
        and general.get("permission") == {"read": "allow"}
        and all(
            body.get("reasoning_effort") == "low"
            for body in result["wire"]
            if body.get("model")
        ),
        "offline: conflicting custom subagent resolves to the restricted selection",
        json.dumps(general),
    )

    # Simultaneous trials must both see fresh state and owned servers.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            offline_probe,
            pier_root,
            provider,
            root,
            instruction="isolation trial one",
        )
        second_future = pool.submit(
            offline_probe,
            pier_root,
            provider,
            root,
            instruction="isolation trial two",
        )
        first = first_future.result()
        second = second_future.result()
    sessions = []
    for result in (first, second):
        ids = {
            str(item.get("session", {}).get("id"))
            for item in result["sessions"]
            if item.get("session", {}).get("id")
        }
        sessions.append(ids)
    check(
        bool(sessions[0]) and bool(sessions[1]) and sessions[0].isdisjoint(sessions[1]),
        "offline: two simultaneous trials use disjoint session identities",
        str(sessions),
    )


# ---------------------------------------------------------------------------
# Live mode
# ---------------------------------------------------------------------------


def load_live_env(env_file: Path) -> dict[str, str]:
    """Read exactly the three allowed keys from the supplied env file."""
    values: dict[str, str] = {}
    for raw in env_file.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in LIVE_ENV_KEYS:
            values[key] = value.strip().strip('"').strip("'")
    missing = [key for key in LIVE_ENV_KEYS if not values.get(key)]
    if missing:
        raise SystemExit(
            f"Live mode env file is missing {', '.join(missing)}; refusing to run."
        )
    return values


LIVE_REQUEST_CAP = 24
LIVE_WALL_CLOCK_CAP_SECONDS = 15 * 60
LIVE_FIX_RERUN_CYCLES_CAP = 3


class SpendLedger:
    """Initialize the persistent ledger shared by at most three live cycles."""

    def __init__(self, path: Path, max_cost_usd: float):
        self.path = path
        self.max_cost_usd = max_cost_usd
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        lock_path.touch(exist_ok=True)
        with lock_path.open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if path.exists():
                state = json.loads(path.read_text())
            else:
                state = {
                    "started_at": time.time(),
                    "cycles": 0,
                    "spent_usd": 0.0,
                    "reservations": [],
                    "forwarded": 0,
                }
            if (
                time.time() - float(state.get("started_at", 0))
                > LIVE_WALL_CLOCK_CAP_SECONDS
            ):
                raise SystemExit(
                    "live: persisted 15-minute budget window has expired; retain "
                    "the ledger as evidence and obtain approval before starting "
                    "a new window"
                )
            state["cycles"] = int(state.get("cycles", 0)) + 1
            if state["cycles"] > LIVE_FIX_RERUN_CYCLES_CAP:
                raise SystemExit(
                    "live: three shared fix-and-rerun cycles are already recorded"
                )
            if float(state.get("max_cost_usd", max_cost_usd)) != max_cost_usd:
                raise SystemExit(
                    "live: persisted ledger has a different approved cost cap"
                )
            state["max_cost_usd"] = max_cost_usd
            self._atomic_write(state)
        self.state = state

    def _atomic_write(self, state: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n")
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.path)

    def snapshot(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())


class TransparentRecorder:
    """A recording reverse proxy placed before the existing gateway.

    Live requests go CLI -> recorder -> gateway, so the capture is transparent
    to the harness. Recordings are sanitized before storage: Authorization
    headers and api-key fields are dropped, and request bodies are reduced to
    the machine-checked fields (model, reasoning_effort, max_tokens,
    thinking).
    """

    def __init__(
        self,
        *,
        upstream: str,
        api_key: str,
        ca_file: str,
        record_dir: Path,
        ledger: SpendLedger,
        context_limit: int,
        route_output_limit: int,
        input_rate: float,
        output_rate: float,
        cache_read_rate: float,
        cache_creation_rate: float,
        port: int = 0,
    ):
        # The client addresses the recorder as .../v1. Strip that suffix from
        # the real gateway once so forwarding never creates /v1/v1.
        upstream = upstream.rstrip("/")
        self.upstream = (
            upstream[:-3].rstrip("/") if upstream.endswith("/v1") else upstream
        )
        self.record_dir = record_dir
        record_dir.mkdir(parents=True, exist_ok=True)
        self.script = record_dir / "recorder.py"
        self.script.write_text(
            textwrap.dedent(
                """
                import fcntl, json, os, ssl, time, urllib.error, urllib.request, uuid
                from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
                from pathlib import Path

                UPSTREAM = os.environ["RECORDER_UPSTREAM"].rstrip("/")
                RECORD_DIR = Path(os.environ["RECORDER_RECORD_DIR"])
                LEDGER = Path(os.environ["RECORDER_LEDGER"])
                LOCK = LEDGER.with_suffix(".lock")
                CONTEXT_LIMIT = int(os.environ["RECORDER_CONTEXT_LIMIT"])
                ROUTE_OUTPUT_LIMIT = int(os.environ["RECORDER_OUTPUT_LIMIT"])
                INPUT_RATE = float(os.environ["RECORDER_INPUT_RATE"])
                OUTPUT_RATE = float(os.environ["RECORDER_OUTPUT_RATE"])
                CACHE_READ_RATE = float(os.environ["RECORDER_CACHE_READ_RATE"])
                CACHE_CREATION_RATE = float(os.environ["RECORDER_CACHE_CREATION_RATE"])
                MAX_COST = float(os.environ["RECORDER_MAX_COST"])
                CA_FILE = os.environ.get("RECORDER_CA_FILE", "")

                def atomic_write(path, value):
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(value, indent=2) + "\\n")
                    fd = os.open(tmp, os.O_RDONLY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    os.replace(tmp, path)

                def update_ledger(callback):
                    LOCK.touch(exist_ok=True)
                    with LOCK.open("r+") as handle:
                        fcntl.flock(handle, fcntl.LOCK_EX)
                        state = json.loads(LEDGER.read_text())
                        result = callback(state)
                        atomic_write(LEDGER, state)
                        return result

                def reserve(body, path):
                    cap = body.get("max_tokens")
                    if isinstance(cap, bool) or not isinstance(cap, int) or not 0 < cap <= ROUTE_OUTPUT_LIMIT:
                        raise ValueError("an explicit supported max_tokens cap is required")
                    reservation = CONTEXT_LIMIT * INPUT_RATE + cap * OUTPUT_RATE
                    request_id = uuid.uuid4().hex
                    def apply(state):
                        if time.time() - float(state["started_at"]) > 15 * 60:
                            raise ValueError("15-minute live window expired")
                        in_flight = sum(float(item["usd"]) for item in state["reservations"])
                        if int(state["forwarded"]) + len(state["reservations"]) >= 24:
                            raise ValueError("24-request live backstop reached")
                        if float(state["spent_usd"]) + in_flight + reservation > MAX_COST:
                            raise ValueError("operational spend cap would be exceeded")
                        state["reservations"].append({
                            "id": request_id,
                            "usd": reservation,
                            "output_cap": cap,
                            "path": path,
                            "t": time.time(),
                        })
                    update_ledger(apply)
                    return request_id, reservation

                def reconcile(request_id, usage):
                    usage_complete = (
                        isinstance(usage, dict)
                        and all(
                            isinstance(usage.get(key), int)
                            and not isinstance(usage.get(key), bool)
                            and usage[key] >= 0
                            for key in ("prompt_tokens", "completion_tokens")
                        )
                    )
                    def apply(state):
                        found = next(
                            (item for item in state["reservations"] if item["id"] == request_id),
                            None,
                        )
                        if found is None:
                            return
                        state["reservations"].remove(found)
                        if not usage_complete:
                            actual = float(found["usd"])
                        else:
                            prompt = int(usage["prompt_tokens"])
                            details = usage.get("prompt_tokens_details") or {}
                            cached = details.get("cached_tokens")
                            if (
                                not isinstance(cached, int)
                                or isinstance(cached, bool)
                                or cached < 0
                                or cached > prompt
                            ):
                                cached = 0
                            uncached_rate = max(INPUT_RATE, CACHE_CREATION_RATE)
                            actual = (
                                (prompt - cached) * uncached_rate
                                + cached * CACHE_READ_RATE
                                + int(usage["completion_tokens"]) * OUTPUT_RATE
                            )
                        state["spent_usd"] = float(state["spent_usd"]) + actual
                        state["forwarded"] = int(state["forwarded"]) + 1
                    update_ledger(apply)

                def response_evidence(payload):
                    usage = None
                    finishes = []
                    for raw in payload.decode("utf-8", "replace").splitlines():
                        if not raw.startswith("data:"):
                            continue
                        value = raw[5:].strip()
                        if not value or value == "[DONE]":
                            continue
                        try:
                            event = json.loads(value)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                        for choice in event.get("choices") or []:
                            if choice.get("finish_reason"):
                                finishes.append(choice["finish_reason"])
                    return usage, sorted(set(finishes))

                class Handler(BaseHTTPRequestHandler):
                    protocol_version = "HTTP/1.1"

                    def _sanitize(self, body: bytes) -> dict:
                        try:
                            parsed = json.loads(body or b"{}")
                        except json.JSONDecodeError:
                            return {"unparseable": True}
                        return {
                            "model": parsed.get("model"),
                            "reasoning_effort": parsed.get("reasoning_effort"),
                            "max_tokens": parsed.get("max_tokens"),
                            "max_completion_tokens": parsed.get("max_completion_tokens"),
                            "thinking": parsed.get("thinking"),
                            "stream": parsed.get("stream"),
                        }

                    def _send_local_error(self, status, message):
                        payload = json.dumps({"error": {"message": message}}).encode()
                        self.send_response(status)
                        self.send_header("content-type", "application/json")
                        self.send_header("content-length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)

                    def _proxy(self):
                        started_at = time.time()
                        n = int(self.headers.get("content-length", 0))
                        body = self.rfile.read(n) if n else b""
                        try:
                            parsed = json.loads(body or b"{}")
                            request_id, reserved = reserve(parsed, self.path)
                        except (json.JSONDecodeError, ValueError) as error:
                            self._send_local_error(429, str(error))
                            return
                        out = urllib.request.Request(
                            UPSTREAM + self.path, data=body, method=self.command
                        )
                        for header in ("content-type", "accept"):
                            value = self.headers.get(header)
                            if value:
                                out.add_header(header, value)
                        out.add_header("Authorization", os.environ["RECORDER_UPSTREAM_AUTH"])
                        context = ssl.create_default_context()
                        if CA_FILE:
                            context.load_verify_locations(cafile=CA_FILE)
                        payload = bytearray()
                        response_headers = {}
                        try:
                            with urllib.request.urlopen(out, timeout=600, context=context) as response:
                                status = response.status
                                ctype = response.headers.get("content-type", "application/json")
                                response_headers = {
                                    key.lower(): value[:300]
                                    for key, value in response.headers.items()
                                    if key.lower()
                                    in {
                                        "x-litellm-model-id",
                                        "x-litellm-call-id",
                                        "x-litellm-response-cost",
                                        "x-litellm-version",
                                        "x-request-id",
                                        "request-id",
                                    }
                                }
                                self.send_response(status)
                                self.send_header("content-type", ctype)
                                self.send_header("connection", "close")
                                self.end_headers()
                                self.close_connection = True
                                while True:
                                    chunk = response.read(65536)
                                    if not chunk:
                                        break
                                    payload.extend(chunk)
                                    self.wfile.write(chunk)
                                    self.wfile.flush()
                        except urllib.error.HTTPError as error:
                            payload.extend(error.read())
                            status = error.code
                            ctype = error.headers.get("content-type", "application/json")
                            self.send_response(status)
                            self.send_header("content-type", ctype)
                            self.send_header("content-length", str(len(payload)))
                            self.end_headers()
                            self.wfile.write(payload)
                        except Exception as error:
                            status = 502
                            payload.extend(json.dumps({"error": {"message": type(error).__name__}}).encode())
                            self._send_local_error(status, type(error).__name__)
                        usage, finishes = response_evidence(bytes(payload))
                        reconcile(request_id, usage)
                        record = {
                            "t": time.time(),
                            "started_at": started_at,
                            "finished_at": time.time(),
                            "request_id": request_id,
                            "path": self.path,
                            "status": status,
                            "request": self._sanitize(body),
                            "reservation_usd": reserved,
                            "response_bytes": len(payload),
                            "response_headers": response_headers,
                            "usage": usage,
                            "usage_complete": (
                                isinstance(usage, dict)
                                and all(
                                    isinstance(usage.get(key), int)
                                    and not isinstance(usage.get(key), bool)
                                    and usage[key] >= 0
                                    for key in ("prompt_tokens", "completion_tokens")
                                )
                            ),
                            "finish_reasons": finishes,
                        }
                        (RECORD_DIR / f"req-{request_id}.json").write_text(
                            json.dumps(record, indent=1) + "\\n"
                        )

                    do_POST = _proxy

                    def log_message(self, *a):
                        pass

                ThreadingHTTPServer(("127.0.0.1", int(os.environ["RECORDER_PORT"])), Handler).serve_forever()
                """
            )
        )
        # Bind port 0 to let the OS pick a free one.
        import socket as _socket

        with _socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
            self.port = probe.getsockname()[1]
        env = dict(os.environ)
        env.update(
            RECORDER_PORT=str(self.port),
            RECORDER_UPSTREAM=self.upstream,
            RECORDER_UPSTREAM_AUTH=f"Bearer {api_key}",
            RECORDER_RECORD_DIR=str(record_dir),
            RECORDER_LEDGER=str(ledger.path),
            RECORDER_CONTEXT_LIMIT=str(context_limit),
            RECORDER_OUTPUT_LIMIT=str(route_output_limit),
            RECORDER_INPUT_RATE=str(input_rate),
            RECORDER_OUTPUT_RATE=str(output_rate),
            RECORDER_CACHE_READ_RATE=str(cache_read_rate),
            RECORDER_CACHE_CREATION_RATE=str(cache_creation_rate),
            RECORDER_MAX_COST=str(ledger.max_cost_usd),
            RECORDER_CA_FILE=ca_file,
        )
        self._stderr = open(record_dir / "recorder.err", "wb")
        self.proc = subprocess.Popen(
            [sys.executable, str(self.script)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=self._stderr,
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), 0.5):
                    return
            except OSError:
                time.sleep(0.2)
        raise SystemExit("live recorder did not become ready")

    def records(self) -> list[dict]:
        return [
            json.loads(path.read_text())
            for path in sorted(self.record_dir.glob("req-*.json"))
        ]

    def stop(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self._stderr.close()


def gateway_model_metadata(env_values: dict[str, str], model: str) -> dict[str, Any]:
    """Return a sanitized exact route record from authenticated LiteLLM metadata."""
    base = env_values["LITELLM_OPENAI_BASE_URL"].rstrip("/")
    roots = [base]
    if base.endswith("/v1"):
        roots.append(base[:-3].rstrip("/"))
    context = ssl.create_default_context()
    ca_file = Path(env_values["PIER_EXTRA_CA_CERTS"])
    if not ca_file.is_file():
        raise SystemExit("live: PIER_EXTRA_CA_CERTS does not name a readable file")
    context.load_verify_locations(cafile=ca_file)
    errors: list[str] = []
    for root in dict.fromkeys(roots):
        request = urllib.request.Request(
            root + "/model/info",
            headers={
                "Authorization": f"Bearer {env_values['LITELLM_API_KEY']}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, context=context, timeout=30
            ) as response:
                payload = json.load(response)
        except (OSError, ValueError, urllib.error.HTTPError) as error:
            errors.append(type(error).__name__)
            continue
        matches = [
            item
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("model_name") == model
        ]
        if len(matches) != 1:
            errors.append(f"exact matches={len(matches)}")
            continue
        item = matches[0]
        params = item.get("litellm_params") or {}
        info = item.get("model_info") or {}
        return {
            "model_name": item.get("model_name"),
            "provider": params.get("custom_llm_provider")
            or info.get("litellm_provider"),
            "upstream_model": params.get("model"),
            "route_id": info.get("id"),
            "max_input_tokens": info.get("max_input_tokens"),
            "max_output_tokens": info.get("max_output_tokens"),
            "input_cost_per_token": info.get("input_cost_per_token"),
            "output_cost_per_token": info.get("output_cost_per_token"),
            "cache_read_input_token_cost": info.get("cache_read_input_token_cost"),
            "cache_creation_input_token_cost": info.get(
                "cache_creation_input_token_cost"
            ),
        }
    block(
        "live: authenticated gateway model metadata resolves the Fireworks route",
        "GET /model/info on the authorized gateway did not return usable "
        "metadata (" + ", ".join(errors) + "). Verify the gateway is reachable "
        "with the supplied credentials and that PIER_EXTRA_CA_CERTS names the "
        "correct bundle, then re-run. Recorded as blocked rather than failed: "
        "no money was spent and nothing was skipped green.",
    )
    return {}


def _live_config(*, output_cap: int, disable_tools: bool) -> dict[str, Any]:
    config = _cap_config(1048576, 131072)
    model = config["providers"]["litellm"]["models"]["glm-5p3-flash"]
    model["body"] = {"max_tokens": output_cap}
    if disable_tools:
        # The probe is deliberately text-only. This is not used by delegation
        # or benchmark jobs and does not broaden any native permissions.
        config["tools"] = {"*": False}
        config["agents"] = {"build": {"permission": "deny"}}
    return config


def _live_case(
    pier_root: Path,
    output_dir: Path,
    recorder: TransparentRecorder,
    ca_file: str,
    *,
    name: str,
    output_cap: int,
    instruction: str,
    disable_tools: bool,
    seed_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    before = {item.get("request_id") for item in recorder.records()}
    case_dir = output_dir / name
    recorder_base_url = f"http://127.0.0.1:{recorder.port}/v1"
    config = _live_config(output_cap=output_cap, disable_tools=disable_tools)
    config["providers"]["litellm"]["settings"]["baseURL"] = recorder_base_url
    result = run_pier_agent(
        pier_root,
        workdir=output_dir,
        output_dir=case_dir,
        model_name="litellm/glm-5p3-flash",
        variant="low",
        opencode_config=config,
        base_url=recorder_base_url,
        api_key="local-recorder-key",
        instruction=instruction,
        env_extra={"PIER_EXTRA_CA_CERTS": ca_file},
        seed_files=seed_files,
        timeout=600,
    )
    result["records"] = [
        item for item in recorder.records() if item.get("request_id") not in before
    ]
    result["output_dir"] = case_dir
    return result


def _assistant_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        message
        for inspection in result.get("sessions") or []
        for message in inspection.get("messages") or []
        if message.get("type") == "assistant"
    ]


def _all_identity_locked(result: dict[str, Any]) -> bool:
    wire_locked = all(
        record.get("request", {}).get("model") == "glm-5p3-flash"
        and record.get("request", {}).get("reasoning_effort") == "low"
        and record.get("request", {}).get("thinking") is None
        for record in result.get("records") or []
    )
    persisted_locked = all(
        message.get("model")
        == {"id": "glm-5p3-flash", "providerID": "litellm", "variant": "low"}
        for message in _assistant_records(result)
    )
    return wire_locked and persisted_locked


def _provider_completion(record: dict[str, Any]) -> int | None:
    usage = record.get("usage")
    if not isinstance(usage, dict):
        return None
    value = usage.get("completion_tokens")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _reconcile_records(
    result: dict[str, Any],
) -> tuple[bool, list[dict[str, str]]]:
    """Match every provider request to one persisted record by time and usage.

    Token equality is only a consistency check. The persisted session/message
    identity and a unique request time window are required; ambiguity fails.
    """

    def nonnegative_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    records = result.get("records") or []
    pending: dict[str, dict[str, Any]] = {}
    for record in records:
        request_id = record.get("request_id")
        usage = record.get("usage")
        if (
            not request_id
            or not isinstance(usage, dict)
            or not all(
                nonnegative_int(usage.get(key))
                for key in ("prompt_tokens", "completion_tokens")
            )
        ):
            return False, []
        pending[str(request_id)] = record
    if len(pending) != len(records):
        return False, []
    persisted: list[tuple[str, str, dict[str, Any]]] = []
    for inspection in result.get("sessions") or []:
        session_id = str((inspection.get("session") or {}).get("id") or "")
        for message in inspection.get("messages") or []:
            if message.get("type") not in {"assistant", "compaction"}:
                continue
            tokens = message.get("tokens")
            cache = tokens.get("cache") if isinstance(tokens, dict) else None
            if (
                not isinstance(tokens, dict)
                or not isinstance(cache, dict)
                or not all(
                    nonnegative_int(tokens.get(key))
                    for key in ("input", "output", "reasoning")
                )
                or not all(nonnegative_int(cache.get(key)) for key in ("read", "write"))
            ):
                return False, []
            persisted.append((session_id, str(message.get("id") or ""), message))
    persisted.sort(
        key=lambda item: float((item[2].get("time") or {}).get("created") or 0)
    )
    mappings: list[dict[str, str]] = []
    for session_id, message_id, message in persisted:
        tokens = message["tokens"]
        cache = tokens["cache"]
        prompt = tokens["input"] + cache["read"] + cache["write"]
        completion = tokens["output"] + tokens["reasoning"]
        created = float((message.get("time") or {}).get("created") or 0) / 1000
        completed = float((message.get("time") or {}).get("completed") or 0) / 1000
        candidates = []
        for request_id, record in pending.items():
            usage = record["usage"]
            started = float(record.get("started_at") or 0)
            finished = float(record.get("finished_at") or record.get("t") or 0)
            if (
                usage["prompt_tokens"] == prompt
                and usage["completion_tokens"] == completion
                and started <= completed + 1
                and finished >= created - 1
            ):
                candidates.append(request_id)
        if len(candidates) != 1:
            return False, mappings
        request_id = candidates[0]
        pending.pop(request_id)
        mappings.append(
            {
                "session_id": session_id,
                "message_id": message_id,
                "request_id": request_id,
            }
        )
    return not pending and len(mappings) == len(persisted), mappings


def live_mode(
    pier_root: Path,
    output_dir: Path,
    env_file: Path,
    model: str,
    variant: str,
    max_cost_usd: float,
) -> int:
    """Live gateway acceptance under a persisted ledger and hard caps."""
    if not math.isfinite(max_cost_usd) or max_cost_usd <= 0 or max_cost_usd > 2.0:
        raise SystemExit("live: --max-cost-usd must be finite, positive, and <= 2")
    env_values = load_live_env(env_file)
    print("\n=== live: preflight ===")
    offline_report_path = output_dir.parent / "offline" / "verification.json"
    offline_report = (
        json.loads(offline_report_path.read_text())
        if offline_report_path.is_file()
        else {}
    )
    check(
        offline_report.get("mode") == "offline"
        and offline_report.get("ok") is True
        and not offline_report.get("failed"),
        "live: the sibling offline acceptance report is green",
        str(offline_report_path),
    )
    check(
        model == "glm-5p3-flash",
        "live: acceptance runs the GLM alias (glm-5p3-flash) only",
        model,
    )
    check(
        variant == "low",
        "live: GLM 5.3 Flash acceptance is low-effort only",
        variant,
    )

    metadata = gateway_model_metadata(env_values, model)
    evidence["gateway_route"] = metadata or {"unavailable": True}
    if not metadata:
        print()
        print(f"{len(passes)} passed, {len(failures)} failed, {len(blocked)} blocked")
        if failures:
            for item in failures:
                print(f"  - {item}")
        return 1
    route_ok = check(
        metadata.get("provider") == "fireworks_ai"
        and metadata.get("upstream_model") == "accounts/fireworks/models/glm-5p3-flash"
        and metadata.get("max_input_tokens") == 1048576,
        "live: authenticated gateway metadata resolves the exact Fireworks route",
        json.dumps(metadata, sort_keys=True),
    )
    input_rate = metadata.get("input_cost_per_token")
    output_rate = metadata.get("output_cost_per_token")
    rates_ok = check(
        isinstance(input_rate, (int, float))
        and input_rate > 0
        and isinstance(output_rate, (int, float))
        and output_rate > 0,
        "live: route exposes positive input/output rates before forwarding",
        json.dumps(metadata, sort_keys=True),
    )
    if not route_ok or not rates_ok or failures:
        return 1

    # The gateway does not currently publish an output ceiling for this route.
    # Acceptance therefore supports no cap above the committed and live-proven
    # 8192-token cap. This is not evidence that the primary profile's 131072
    # ceiling is provider-ready.
    acceptance_output_limit = 8192
    cache_read_rate = metadata.get("cache_read_input_token_cost")
    cache_creation_rate = metadata.get("cache_creation_input_token_cost")
    cache_rates_known = (
        isinstance(cache_read_rate, (int, float))
        and not isinstance(cache_read_rate, bool)
        and cache_read_rate >= 0
        and isinstance(cache_creation_rate, (int, float))
        and not isinstance(cache_creation_rate, bool)
        and cache_creation_rate >= 0
    )
    if not cache_rates_known:
        evidence["budget_assumptions"] = {
            "context_limit": 1048576,
            "acceptance_output_limit": acceptance_output_limit,
            "cache_read_rate": cache_read_rate,
            "cache_creation_rate": cache_creation_rate,
            "budget_enforcement": "blocked before ledger creation or forwarding",
        }
        block(
            "live: route exposes cache-read and cache-creation rates before forwarding",
            "The authenticated route metadata does not identify every applicable "
            "cache charge, so the operational budget cannot be enforced without "
            "assuming a missing surcharge.",
        )
        return 1
    reservation_input_rate = max(
        float(input_rate),
        float(cache_creation_rate),
    )
    evidence["budget_assumptions"] = {
        "context_limit": 1048576,
        "acceptance_output_limit": acceptance_output_limit,
        "acceptance_output_limit_source": (
            "committed frozen acceptance profile; retained live evidence proves "
            "max_tokens=8192 on the Fireworks route"
        ),
        "gateway_reported_output_limit": metadata.get("max_output_tokens"),
        "input_reservation_rate": reservation_input_rate,
        "cache_creation_rate": cache_creation_rate,
        "cache_policy": (
            "reserve the full context at the greater of ordinary-input and "
            "published cache-creation rates; never apply the cache-read discount"
        ),
        "primary_131072_provider_ready": False,
        "gateway_hidden_retry_fallback_policy_verified": False,
    }

    # Keep the cross-cycle ledger in this checkout's ignored generated area;
    # the supplied env file may intentionally live in another checkout.
    ledger_path = BENCHMARK_DIR / "generated" / "opencode-v2-live-budget.json"
    ledger = SpendLedger(ledger_path, max_cost_usd)
    recorder = TransparentRecorder(
        upstream=env_values["LITELLM_OPENAI_BASE_URL"],
        api_key=env_values["LITELLM_API_KEY"],
        ca_file=env_values["PIER_EXTRA_CA_CERTS"],
        record_dir=output_dir / "recorder",
        ledger=ledger,
        context_limit=1048576,
        route_output_limit=acceptance_output_limit,
        input_rate=reservation_input_rate,
        output_rate=float(output_rate),
        cache_read_rate=float(cache_read_rate),
        cache_creation_rate=float(cache_creation_rate),
    )
    live_results: list[dict[str, Any]] = []
    try:
        print("\n=== live: small output cap ===")
        small = _live_case(
            pier_root,
            output_dir,
            recorder,
            env_values["PIER_EXTRA_CA_CERTS"],
            name="small-cap",
            output_cap=256,
            disable_tools=True,
            instruction=(
                "This is a deterministic output-length test. Without using "
                "tools, emit integers from 0001 through 4000, each on its own "
                "line, with no prose. Completing all 4000 lines is mandatory."
            ),
        )
        live_results.append(small)
        small_statuses = [item.get("status") for item in small["records"]]
        if small["records"] and not any(status == 200 for status in small_statuses):
            supported_cap = None
            attempted_caps = [256]
            for candidate in (512, 1024, 2048, 4096, 8192):
                attempted_caps.append(candidate)
                fallback = _live_case(
                    pier_root,
                    output_dir,
                    recorder,
                    env_values["PIER_EXTRA_CA_CERTS"],
                    name=f"small-cap-fallback-{candidate}",
                    output_cap=candidate,
                    disable_tools=True,
                    instruction=(
                        "Without using tools, emit integers from 0001 through "
                        "4000, each on its own line, with no prose."
                    ),
                )
                live_results.append(fallback)
                if any(item.get("status") == 200 for item in fallback["records"]):
                    supported_cap = candidate
                    break
            evidence["small_cap_rejection"] = {
                "statuses": small_statuses,
                "attempted_caps": attempted_caps,
                "smallest_observed_supported_cap": supported_cap,
            }
            block(
                "live: max_tokens=256 is supported by the route",
                "The route rejected 256; the rejection and smallest observed "
                f"supported probe cap ({supported_cap!r}) were retained.",
            )
            return 1
        small_completions = [
            value
            for value in (_provider_completion(item) for item in small["records"])
            if value is not None
        ]
        small_finishes = {
            finish
            for item in small["records"]
            for finish in item.get("finish_reasons") or []
        }
        check(
            small["returncode"] == 0
            and bool(small["records"])
            and all(
                item["request"].get("max_tokens") == 256 for item in small["records"]
            )
            and bool(small_completions)
            and max(small_completions) <= 256,
            "live: small-cap request forwards max_tokens=256 and usage stays within cap",
            json.dumps(
                {"usage": small_completions, "finishes": sorted(small_finishes)}
            ),
        )
        check(
            "length" in small_finishes,
            "live: small-cap terminal state is normalized as truncation",
            json.dumps(sorted(small_finishes)),
        )
        check(
            _all_identity_locked(small),
            "live: small-cap request and persisted record retain GLM low identity",
        )

        print("\n=== live: large output cap ===")
        large = _live_case(
            pier_root,
            output_dir,
            recorder,
            env_values["PIER_EXTRA_CA_CERTS"],
            name="large-cap-1",
            output_cap=8192,
            disable_tools=True,
            instruction=(
                "Without using tools, output exactly 3000 repetitions of the "
                "single token `X`, separated by one space. No introduction, "
                "summary, ellipsis, or early stop."
            ),
        )
        live_results.append(large)
        large_completions = [
            value
            for value in (_provider_completion(item) for item in large["records"])
            if value is not None
        ]
        if not any(2048 < value <= 8192 for value in large_completions):
            large = _live_case(
                pier_root,
                output_dir,
                recorder,
                env_values["PIER_EXTRA_CA_CERTS"],
                name="large-cap-2",
                output_cap=8192,
                disable_tools=True,
                instruction=(
                    "This is a deterministic output-length test. Emit integers "
                    "from 0001 through 4000, each on its own line, with no prose. "
                    "Completing all 4000 lines is mandatory."
                ),
            )
            live_results.append(large)
            large_completions.extend(
                value
                for value in (_provider_completion(item) for item in large["records"])
                if value is not None
            )
        check(
            any(2048 < value <= 8192 for value in large_completions),
            "live: one max_tokens=8192 provider response exceeds 2048 completion tokens",
            json.dumps(large_completions),
        )
        check(
            all(
                item["request"].get("max_tokens") == 8192
                for result in live_results[1:]
                for item in result["records"]
            )
            and all(_all_identity_locked(result) for result in live_results[1:]),
            "live: large-cap requests retain the exact body and GLM low identity",
        )

        print("\n=== live: delegation, resume, and repair ===")
        nonce = uuid.uuid4().hex[:16]
        delegation = _live_case(
            pier_root,
            output_dir,
            recorder,
            env_values["PIER_EXTRA_CA_CERTS"],
            name="delegation-repair",
            output_cap=8192,
            disable_tools=False,
            seed_files={
                "solution.py": "def add(a, b):\n    return a - b\n",
                "review_fixture.txt": f"EXPECTED_NONCE={nonce}\n",
                "verify.py": (
                    "from solution import add\n"
                    "from pathlib import Path\n"
                    f"assert add(2, 3) == 5\nassert 'EXPECTED_NONCE={nonce}' in "
                    "Path('review_fixture.txt').read_text()\n"
                    f"print('reward=1 nonce={nonce}')\n"
                ),
            },
            instruction=(
                "Perform this acceptance task exactly. First invoke the general "
                "subagent to inspect review_fixture.txt. Then resume that same "
                "child by passing its sessionID and ask it to confirm the nonce. "
                "Fix solution.py so add(2, 3) returns 5, run `python3 verify.py`, "
                f"and finish with the exact nonce {nonce}. Do not create a second child."
            ),
        )
        live_results.append(delegation)
        sandbox_work = delegation["output_dir"] / "sandbox" / "work"
        verifier = subprocess.run(
            [sys.executable, "verify.py"],
            cwd=sandbox_work,
            capture_output=True,
            text=True,
            timeout=30,
        )
        child_ids = {
            str(item.get("session", {}).get("id"))
            for item in delegation.get("sessions") or []
            if item.get("session", {}).get("parentID")
        }
        subagent_calls = [
            part
            for message in _assistant_records(delegation)
            for part in message.get("content") or []
            if isinstance(part, dict)
            and part.get("type") == "tool"
            and part.get("name") == "subagent"
        ]
        resumed_ids = {
            str((part.get("state") or {}).get("input", {}).get("sessionID"))
            for part in subagent_calls
            if (part.get("state") or {}).get("input", {}).get("sessionID")
        }
        final_text = "\n".join(
            str(part.get("text") or "")
            for message in _assistant_records(delegation)
            for part in message.get("content") or []
            if isinstance(part, dict) and part.get("type") == "text"
        )
        check(
            verifier.returncode == 0
            and "reward=1" in verifier.stdout
            and nonce in final_text,
            "live: root repairs the function, deterministic verifier rewards 1, and nonce returns",
            (verifier.stderr or final_text[-400:]),
        )
        check(
            len(child_ids) == 1
            and len(subagent_calls) >= 2
            and resumed_ids == child_ids,
            "live: one real child is invoked twice and resumed by the same sessionID",
            json.dumps(
                {
                    "children": sorted(child_ids),
                    "invocations": len(subagent_calls),
                    "resumed": sorted(resumed_ids),
                }
            ),
        )
        trajectory = delegation.get("trajectory") or {}
        final_extra = (trajectory.get("final_metrics") or {}).get("extra") or {}
        check(
            _all_identity_locked(delegation)
            and final_extra.get("subagent_count") == 1
            and final_extra.get("tree_metrics_complete") is True,
            "live: delegation tree is complete and locked to GLM low",
            json.dumps(final_extra, sort_keys=True),
        )
        final_metrics = trajectory.get("final_metrics") or {}
        provider_prompt = sum(
            int((item.get("usage") or {}).get("prompt_tokens") or 0)
            for item in delegation["records"]
        )
        provider_completion = sum(
            int((item.get("usage") or {}).get("completion_tokens") or 0)
            for item in delegation["records"]
        )
        persisted_cost = sum(
            float(message.get("cost"))
            for inspection in delegation.get("sessions") or []
            for message in inspection.get("messages") or []
            if message.get("type") in {"assistant", "compaction"}
            and isinstance(message.get("cost"), (int, float))
            and not isinstance(message.get("cost"), bool)
        )
        records_reconciled, request_mappings = _reconcile_records(delegation)
        evidence["delegation_request_mappings"] = request_mappings
        check(
            final_metrics.get("total_prompt_tokens") == provider_prompt
            and final_metrics.get("total_completion_tokens") == provider_completion
            and abs(float(final_metrics.get("total_cost_usd") or 0) - persisted_cost)
            < 1e-9
            and (delegation.get("context") or {}).get("cost_usd")
            == final_metrics.get("total_cost_usd"),
            "live: provider usage, persisted records, ATIF, AgentContext, and normalized cost reconcile",
            json.dumps(
                {
                    "provider_prompt": provider_prompt,
                    "provider_completion": provider_completion,
                    "atif_prompt": final_metrics.get("total_prompt_tokens"),
                    "atif_completion": final_metrics.get("total_completion_tokens"),
                    "persisted_cost": persisted_cost,
                    "atif_cost": final_metrics.get("total_cost_usd"),
                },
                sort_keys=True,
            ),
        )
        check(
            records_reconciled,
            "live: each provider request maps unambiguously to one session/message record",
            json.dumps(request_mappings, sort_keys=True),
        )

        all_records = [item for result in live_results for item in result["records"]]
        route_id = metadata.get("route_id")
        per_request_route = (
            bool(route_id)
            and bool(all_records)
            and all(
                item.get("status") == 200
                and item.get("response_headers", {}).get("x-litellm-model-id")
                == route_id
                for item in all_records
            )
        )
        if not route_id:
            block(
                "live: every captured request has actual Fireworks route provenance",
                "The gateway metadata omitted its route ID, so HTTP 200 responses "
                "cannot establish per-request upstream provenance.",
            )
        elif not per_request_route:
            block(
                "live: every captured request has actual Fireworks route provenance",
                "Authenticated model metadata identified Fireworks, but response "
                "headers did not identify that route for every forwarded request.",
            )
        else:
            check(
                per_request_route,
                "live: every captured request reconciles to the authorized gateway route",
            )
    finally:
        recorder.stop()
        ledger_state = ledger.snapshot()
        evidence["live_budget"] = {
            "cycles": ledger_state.get("cycles"),
            "forwarded": ledger_state.get("forwarded"),
            "spent_usd": ledger_state.get("spent_usd"),
            "outstanding_reservations": len(ledger_state.get("reservations") or []),
            "max_cost_usd": max_cost_usd,
        }
        evidence["live_requests"] = recorder.records()
    print()
    print(f"{len(passes)} passed, {len(failures)} failed, {len(blocked)} blocked")
    if failures or blocked:
        for item in failures:
            print(f"  - {item}")
        return 1
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def approved_max_cost(value: str) -> float:
    """Argparse validator for the immutable live operational ceiling."""
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not math.isfinite(parsed) or parsed <= 0 or parsed > 2.0:
        raise argparse.ArgumentTypeError("must be finite, positive, and <= 2")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pier-root", type=Path, required=True)
    parser.add_argument("--mode", choices=["offline", "live"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", default="glm-5p3-flash")
    parser.add_argument("--variant", default="low")
    parser.add_argument("--max-cost-usd", type=approved_max_cost, default=2.00)
    args = parser.parse_args()

    if not args.pier_root.exists():
        raise SystemExit(f"--pier-root does not exist: {args.pier_root}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "offline":
        code = offline_mode(args.pier_root, args.output_dir)
    else:
        if not args.env_file:
            raise SystemExit("live mode requires --env-file")
        code = live_mode(
            args.pier_root,
            args.output_dir,
            args.env_file,
            args.model,
            args.variant,
            args.max_cost_usd,
        )

    report = {
        "mode": args.mode,
        "pier_root": str(args.pier_root),
        "passed": passes,
        "failed": failures,
        "blocked": blocked,
        "evidence": evidence,
        "ok": code == 0,
    }
    (args.output_dir / "verification.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(f"\nverification.json written to {args.output_dir / 'verification.json'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
