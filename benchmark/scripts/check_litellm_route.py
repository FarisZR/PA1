#!/usr/bin/env python3
"""Live acceptance check for one LiteLLM -> Fireworks model route.

Run it against the exact gateway and model before starting a benchmark job.
Every assertion uses ground truth that LiteLLM cannot rewrite: the Fireworks
response headers LiteLLM passes through (``llm_provider-fireworks-*``), an
upstream validation error, or the CLIProxyAPI request log.

Direct gateway checks (always run):

  version     ``x-litellm-version`` matches ``--expect-version``.
  budget      the virtual key's max budget leaves room for ``--planned-spend-usd``.
  cache       reports whether LiteLLM response caching is on. PA1 keeps the
              gateway's caching as deployed, as in every earlier run; the
              probes themselves bypass it so they always measure Fireworks.
  effort      ``reasoning_effort`` reaches Fireworks (a bogus value is rejected
              upstream) and ``max`` returns real reasoning.
  tools       whether ``tool_choice`` reaches Fireworks. LiteLLM drops it
              silently when the deployment lacks ``supports_tool_choice``.
  max_tokens  the harness ``max_tokens`` reaches Fireworks (sampling header).
  replay      prior ``reasoning_content`` reaches the model (PA1 #111). The
              Fireworks prompt-token count must grow by the reasoning's size.
  streaming   the final streaming usage chunk keeps ``cached_tokens`` on every
              request (LiteLLM #36882).
  router      no hidden router retries or fallbacks on any call.
  billing     whether LiteLLM debits cached tokens at the cache-read rate.

Bridge checks (``--bridge``; CLIProxyAPI must be running with request logging):

  claude-code a Claude Code-shaped Anthropic Messages exchange with effort
              ``max`` through CLIProxyAPI. The logged upstream body must carry
              ``reasoning_effort: "max"`` and the replayed ``reasoning_content``
              (PA1 #94).
  codex       the same for a Codex-shaped Responses exchange.

Usage:
  python3 benchmark/scripts/check_litellm_route.py --env-file benchmark/env.local \\
      --model glm-5p3-flash --expect-version 1.93.0 [--bridge] \\
      [--report "$BB_THREAD_STORAGE/litellm-route.json"]

Every direct request except the cache probe sends LiteLLM's per-request cache
bypass, so each probe reaches Fireworks and carries its headers. The harnesses
do not send it. Expected cost is well under USD 0.10.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = BENCHMARK_DIR / "generated" / "cliproxy-logs"

sys.path.insert(0, str(BENCHMARK_DIR / "scripts"))
from prepare_configs import load_env_file, require  # noqa: E402

# LiteLLM consumes this body field itself and never forwards it. "no-cache"
# skips reading a stored response, "no-store" skips writing one. Only the
# probes send it; the harnesses use the gateway's caching as deployed.
CACHE_BYPASS = {"no-cache": True, "no-store": True}

results: list[dict] = []
responses: list[dict] = []


def record(status: str, name: str, detail: str = "") -> None:
    """Print and keep one check result. status is PASS, FAIL, WARN, or SKIP."""
    results.append({"status": status, "check": name, "detail": detail})
    print(f"  {status:4s}  {name}" + (f"\n        {detail}" if detail else ""))


def check(condition: bool, name: str, detail: str = "", soft: bool = False) -> bool:
    record("PASS" if condition else ("WARN" if soft else "FAIL"), name, detail)
    return condition


class Gateway:
    """Minimal Chat Completions client that keeps response headers."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def post(self, body: dict, bypass: bool = True, path: str = "/chat/completions") -> dict:
        body = {"model": self.model, **body}
        if bypass:
            body["cache"] = CACHE_BYPASS
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                status, headers, raw = response.status, response.headers, response.read().decode()
        except urllib.error.HTTPError as error:
            status, headers, raw = error.code, error.headers, error.read().decode()
        result = {
            "status": status,
            "headers": {k.lower(): v for k, v in headers.items()},
            "raw": raw,
            "json": None,
            "usage": None,
        }
        if body.get("stream"):
            for line in raw.splitlines():
                if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                    try:
                        chunk = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        result["usage"] = chunk["usage"]
        else:
            try:
                result["json"] = json.loads(raw)
                result["usage"] = result["json"].get("usage")
            except json.JSONDecodeError:
                pass
        responses.append(result)
        return result


def header_int(result: dict, name: str) -> int | None:
    value = result["headers"].get(name)
    try:
        return int(float(value)) if value is not None else None
    except ValueError:
        return None


def fw_prompt_tokens(result: dict) -> int | None:
    return header_int(result, "llm_provider-fireworks-prompt-tokens")


def message(result: dict) -> dict:
    try:
        return result["json"]["choices"][0]["message"]
    except (TypeError, KeyError, IndexError):
        return {}


def cached_tokens(usage: dict | None) -> int | None:
    return ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens")


def unique_prompt(label: str) -> str:
    return f"PA1 route probe {label} {uuid.uuid4().hex}."


PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_record",
        "description": "Fetch one record by numeric id.",
        "parameters": {
            "type": "object",
            "properties": {"record_id": {"type": "integer"}},
            "required": ["record_id"],
        },
    },
}

# A moderately hard question so reasoning length visibly depends on effort.
EFFORT_PROMPT = (
    "Count the ordered pairs (a, b) of integers with 1 <= a, b <= 60 such that "
    "a*b + a + b is divisible by 7. Give only the final number."
)


def reasoning_text(label: str, sentences: int) -> str:
    """Distinctive filler reasoning; distinct per call so no prefix is cached."""
    return " ".join(
        f"Step {i} of plan {label}: record {i * 13 % 97} must be checked against "
        f"invariant {i * 7 % 31} before the lookup tool is called again."
        for i in range(sentences)
    )


def check_direct(gw: Gateway, args: argparse.Namespace) -> None:
    print(f"\n=== direct gateway: {gw.base_url} model={gw.model} ===")

    # version -----------------------------------------------------------
    first = gw.post({"messages": [{"role": "user", "content": "Reply with OK."}], "reasoning_effort": "low", "max_tokens": 200})
    if first["status"] != 200:
        record("FAIL", "gateway answers a minimal request", f"HTTP {first['status']}: {first['raw'][:400]}")
        return
    version = first["headers"].get("x-litellm-version")
    check(version == args.expect_version, f"x-litellm-version is {args.expect_version}", f"got {version!r}")
    api_base = first["headers"].get("x-litellm-model-api-base")
    check(api_base is not None and "fireworks.ai" in api_base, "deployment points at Fireworks", repr(api_base))

    # budget ------------------------------------------------------------
    max_budget = first["headers"].get("x-litellm-key-max-budget")
    spend = first["headers"].get("x-litellm-key-spend")
    if max_budget in (None, "", "None"):
        record("PASS", "virtual key has no max budget")
    else:
        remaining = float(max_budget) - float(spend or 0)
        check(
            remaining >= args.planned_spend_usd,
            f"key budget covers planned spend (USD {args.planned_spend_usd:.0f})",
            f"max_budget={float(max_budget):.2f} spend={float(spend or 0):.2f} remaining={remaining:.2f}. "
            "LiteLLM bills every call at its own price map, including response-cache hits; "
            "exhausting the budget mid-job turns the remaining trials into HTTP 400 failures.",
        )

    # response cache ----------------------------------------------------
    probe = {"messages": [{"role": "user", "content": unique_prompt("cache") + " Pick a random integer between 1 and 1000000; reply with only the number."}],
             "reasoning_effort": "low", "max_tokens": 300}
    a = gw.post(probe, bypass=False)
    b = gw.post(probe, bypass=False)
    hit = b["headers"].get("x-litellm-cache-key") is not None or (
        a["json"] and b["json"] and a["json"].get("id") == b["json"].get("id")
    )
    record(
        "PASS",
        "(info) gateway response cache is " + ("ON: identical requests are replayed" if hit else "off"),
        "second identical request returned the stored completion "
        f"(id {b['json'].get('id') if b['json'] else '?'}, {b['headers'].get('x-litellm-response-duration-ms')} ms, "
        "no Fireworks headers). Only byte-identical requests hit it, such as a relaunched trial's first turn."
        if hit else "",
    )
    probe_b = {**probe, "messages": [{"role": "user", "content": unique_prompt("bypass") + " Pick a random integer between 1 and 1000000; reply with only the number."}]}
    c = gw.post(probe_b, bypass=True)
    d = gw.post(probe_b, bypass=True)
    e = gw.post(probe_b, bypass=False)
    fresh = all(r["headers"].get("x-litellm-cache-key") is None and fw_prompt_tokens(r) is not None for r in (c, d, e))
    check(
        c["status"] == 200 and fresh,
        "probe cache bypass reaches Fireworks every time and stores nothing",
        f"statuses={[r['status'] for r in (c, d, e)]} cache_keys={[r['headers'].get('x-litellm-cache-key') for r in (c, d, e)]} "
        f"(HTTP 403 means the key is not allowed to set cache controls)",
    )

    # reasoning effort --------------------------------------------------
    bogus = gw.post({"messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "pa1-bogus", "max_tokens": 50})
    check(
        bogus["status"] == 400 and "reasoning_effort" in bogus["raw"] and "Fireworks" in bogus["raw"],
        "reasoning_effort reaches Fireworks (bogus value rejected upstream)",
        f"HTTP {bogus['status']}: {bogus['raw'][:300]}",
    )
    efforts: dict[str, dict] = {}
    for effort in ("low", "max"):
        efforts[effort] = gw.post({"messages": [{"role": "user", "content": unique_prompt(effort) + " " + EFFORT_PROMPT}],
                                   "reasoning_effort": effort, "max_tokens": args.max_tokens})
    mx = efforts["max"]
    check(mx["status"] == 200 and bool(message(mx).get("reasoning_content")),
          "reasoning_effort=max returns HTTP 200 with reasoning_content", f"HTTP {mx['status']}: {mx['raw'][:300]}")
    rt = {k: ((v["usage"] or {}).get("completion_tokens_details") or {}).get("reasoning_tokens") for k, v in efforts.items()}
    check((rt["max"] or 0) > (rt["low"] or 0), "max reasons longer than low on the same problem",
          f"reasoning_tokens low={rt['low']} max={rt['max']}", soft=True)

    # max_tokens --------------------------------------------------------
    try:
        sampling = json.loads(mx["headers"].get("llm_provider-fireworks-sampling-options") or "{}")
    except json.JSONDecodeError:
        sampling = {}
    check(sampling.get("max_tokens") == args.max_tokens, f"max_tokens={args.max_tokens} reaches Fireworks",
          f"Fireworks sampling options: max_tokens={sampling.get('max_tokens')!r}")

    # tool_choice -------------------------------------------------------
    tc = gw.post({"messages": [{"role": "user", "content": "What is 2+2?"}], "tools": [PROBE_TOOL],
                  "tool_choice": "pa1-bogus", "reasoning_effort": "low", "max_tokens": 500})
    if tc["status"] == 400:
        record("PASS", "tool_choice reaches Fireworks")
    else:
        record("WARN", "tool_choice is silently dropped by LiteLLM",
               "the deployment does not declare supports_tool_choice and the proxy runs with drop_params. "
               "Harmless for tool_choice=auto (the default); any other value is ignored.")
    auto = gw.post({"messages": [{"role": "user", "content": "Fetch record 7 with the tool."}], "tools": [PROBE_TOOL],
                    "tool_choice": "auto", "reasoning_effort": "low", "max_tokens": 2000})
    check(auto["status"] == 200 and bool(message(auto).get("tool_calls")), "tools + tool_choice=auto produce a tool call",
          f"HTTP {auto['status']}: {auto['raw'][:300]}")

    # reasoning replay (#111) -------------------------------------------
    label = uuid.uuid4().hex[:8]
    reasoning = reasoning_text(label, args.replay_sentences)
    # GLM-5.3 is thinking-only and rejects reasoning_effort="none".
    size_probe = gw.post({"messages": [{"role": "user", "content": reasoning}], "max_tokens": 16, "reasoning_effort": "low"})
    base_probe = gw.post({"messages": [{"role": "user", "content": "x"}], "max_tokens": 16, "reasoning_effort": "low"})
    reasoning_tokens = (fw_prompt_tokens(size_probe) or 0) - (fw_prompt_tokens(base_probe) or 0)
    if reasoning_tokens <= 0:
        record("FAIL", "measure the probe reasoning size", f"size probe HTTP {size_probe['status']}: {size_probe['raw'][:300]}")
        return

    def tool_loop(with_reasoning: bool) -> dict:
        assistant = {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_pa1_1", "type": "function", "function": {"name": "lookup_record", "arguments": '{"record_id": 7}'}}]}
        if with_reasoning:
            assistant["reasoning_content"] = reasoning
        return gw.post({"messages": [
            {"role": "system", "content": f"You are a careful assistant. Session {label}."},
            {"role": "user", "content": "Look up record 7 with the tool, then state its colour in one word."},
            assistant,
            {"role": "tool", "tool_call_id": "call_pa1_1", "content": "record 7: colour=teal"},
        ], "tools": [PROBE_TOOL], "reasoning_effort": "low", "max_tokens": 300})

    with_r, without_r = tool_loop(True), tool_loop(False)
    delta = (fw_prompt_tokens(with_r) or 0) - (fw_prompt_tokens(without_r) or 0)
    retention = delta / reasoning_tokens if reasoning_tokens > 0 else 0.0
    check(with_r["status"] == 200 and retention >= 0.8,
          "prior reasoning_content in a tool loop reaches the model (PA1 #111)",
          f"Fireworks prompt tokens with={fw_prompt_tokens(with_r)} without={fw_prompt_tokens(without_r)} "
          f"delta={delta} reasoning~{reasoning_tokens} tokens retention={retention:.2f} (dropped ~0, kept ~1)")

    # Informational: whether the chat template keeps reasoning from turns that
    # precede the latest user message. A low value here is template behavior,
    # not a gateway defect, and applies to every harness equally.
    def earlier_turn(with_reasoning: bool) -> dict:
        assistant = {"role": "assistant", "content": "Noted."}
        if with_reasoning:
            assistant["reasoning_content"] = reasoning
        return gw.post({"messages": [
            {"role": "user", "content": f"Remember session {label}."}, assistant,
            {"role": "user", "content": "Reply with OK."}], "reasoning_effort": "low", "max_tokens": 200})

    early_delta = (fw_prompt_tokens(earlier_turn(True)) or 0) - (fw_prompt_tokens(earlier_turn(False)) or 0)
    early = early_delta / reasoning_tokens if reasoning_tokens > 0 else 0.0
    record("PASS", f"(info) reasoning before the latest user turn: template retention={early:.2f}",
           "~0 means GLM's template drops it there for every harness; use tool-loop retention for #111 checks")

    # streaming usage (#36882) ------------------------------------------
    prefix = unique_prompt("stream") + " " + " ".join(
        f"Reference line {i}: the harness reads file_{i}.py and keeps its outline." for i in range(args.stream_prefix_lines))
    stream_body = {"messages": [{"role": "system", "content": prefix}, {"role": "user", "content": "Reply with OK."}],
                   "reasoning_effort": "low", "max_tokens": 200}
    warm = gw.post(stream_body)
    time.sleep(1)
    missing, zero, observed = 0, 0, []
    for _ in range(args.stream_repeats):
        s = gw.post({**stream_body, "stream": True, "stream_options": {"include_usage": True}})
        value = cached_tokens(s["usage"])
        observed.append(value)
        if s["usage"] is None or value is None:
            missing += 1
        elif value == 0:
            zero += 1
    check(missing == 0, f"final streaming usage keeps cached_tokens on {args.stream_repeats}/{args.stream_repeats} requests (LiteLLM #36882)",
          f"prompt={fw_prompt_tokens(warm)} cached per stream={observed}")
    check(zero == 0, "warm prefix is reported as cached on every stream",
          f"{zero} stream(s) reported cached_tokens=0 after warm-up; Fireworks cache routing may be sticky-less", soft=True)
    non_stream = gw.post(stream_body)
    check(cached_tokens(non_stream["usage"]) == header_int(non_stream, "llm_provider-fireworks-cached-prompt-tokens"),
          "non-streaming usage.cached_tokens equals the Fireworks cached-prompt header",
          f"usage={cached_tokens(non_stream['usage'])} header={header_int(non_stream, 'llm_provider-fireworks-cached-prompt-tokens')}")

    # budget accounting -------------------------------------------------
    # LiteLLM debits the key budget with its own price map, not the Fireworks
    # invoice. Without a cache-read price on the deployment, a ~95%-cached
    # agent loop burns the budget about four times faster than PA1 pricing.
    usage = non_stream["usage"] or {}
    p, c, o = usage.get("prompt_tokens", 0), cached_tokens(usage) or 0, usage.get("completion_tokens", 0)
    billed = float(non_stream["headers"].get("x-litellm-response-cost") or 0)
    at_cache = ((p - c) * args.input_price + c * args.cached_price + o * args.output_price) / 1e6
    at_full = (p * args.input_price + o * args.output_price) / 1e6
    if c == 0:
        record("SKIP", "LiteLLM bills cached tokens at the cache-read rate", "no cached tokens on the probe")
    else:
        check(abs(billed - at_cache) < abs(billed - at_full), "LiteLLM bills cached tokens at the cache-read rate",
              f"billed={billed:.3e} expected cache-rate={at_cache:.3e} full-rate={at_full:.3e}. At the full rate the key "
              "budget must cover roughly PA1's uncached cost (about USD 93 per clean GLM harness on the 2026-09-20 data).",
              soft=True)

    # router ------------------------------------------------------------
    retried = [r for r in responses if (header_int(r, "x-litellm-attempted-retries") or 0) or (header_int(r, "x-litellm-attempted-fallbacks") or 0)]
    model_ids = {r["headers"].get("x-litellm-model-id") for r in responses if r["headers"].get("x-litellm-model-id")}
    check(not retried, "no hidden router retries or fallbacks", f"{len(retried)} call(s) reported retries/fallbacks")
    check(len(model_ids) == 1, "every call hit the same single deployment", f"model ids: {sorted(model_ids)}", soft=True)

    limits = {k[len('llm_provider-x-ratelimit-'):]: v for k, v in first["headers"].items() if k.startswith("llm_provider-x-ratelimit-limit")}
    record("PASS", "(info) Fireworks rate limits on this account", json.dumps(limits))


# --- bridge ---------------------------------------------------------------


def http_post(url: str, key: str, body: dict, headers: dict | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {key}", "x-api-key": key, "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
    except urllib.error.URLError as error:
        return 0, str(error)


def upstream_bodies(since: float, marker: str) -> list[dict]:
    """Translated upstream bodies from CLIProxyAPI request logs carrying marker."""
    found: list[dict] = []
    if not LOG_DIR.is_dir():
        return found
    for path in sorted(LOG_DIR.rglob("*.log"), key=lambda p: p.stat().st_mtime):
        if path.stat().st_mtime < since:
            continue
        for match in re.finditer(r"^Body:\s*(\{.*)$", path.read_text(errors="replace"), re.M):
            try:
                body = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            # Only chat-completions bodies (the translated upstream request).
            if "messages" in body and marker in json.dumps(body):
                found.append(body)
    return found


# Top-level request keys LiteLLM 1.93.0's Fireworks adapter forwards
# (FireworksAIConfig.get_supported_openai_params) plus the ones it consumes
# itself. With drop_params on, anything else is removed without an error.
FIREWORKS_FORWARDED = {
    "model", "messages", "stream", "max_completion_tokens", "max_tokens", "temperature", "top_p", "top_k",
    "frequency_penalty", "presence_penalty", "n", "stop", "response_format", "user", "logprobs",
    "prompt_truncate_len", "context_length_exceeded_behavior", "seed", "top_logprobs", "min_p", "typical_p",
    "repetition_penalty", "mirostat_target", "mirostat_lr", "logit_bias", "echo", "echo_last", "ignore_eos",
    "prompt_cache_key", "prompt_cache_isolation_key", "raw_output", "perf_metrics_in_response", "return_token_ids",
    "safe_tokenization", "service_tier", "speculation", "prediction", "stream_options", "sampling_mask",
    "tools", "parallel_tool_calls", "reasoning_effort", "reasoning_history", "thinking",
}


def check_upstream(label: str, bodies: list[dict], effort: str, reasoning: str | None) -> None:
    if not bodies:
        record("SKIP", f"{label}: upstream body inspection",
               "no logged upstream body carries this run's marker; set CODEX_CLIPROXY_REQUEST_LOG=true, regenerate, restart the bridge")
        return
    last = bodies[-1]
    check(last.get("reasoning_effort") == effort, f"{label}: upstream reasoning_effort is {effort!r} (PA1 #94)",
          repr(last.get("reasoning_effort")))
    check(last.get("tool_choice") in (None, "auto"), f"{label}: tool_choice is absent or auto (LiteLLM drops it)",
          repr(last.get("tool_choice")))
    dropped = sorted({k for b in bodies for k in b} - FIREWORKS_FORWARDED - {"tool_choice"})
    check(not dropped, f"{label}: every other upstream key survives LiteLLM's drop_params",
          f"silently dropped by LiteLLM 1.93.0 for Fireworks: {dropped}", soft=True)
    if reasoning is not None:
        replayed = [m.get("reasoning_content") for m in last["messages"] if m.get("role") == "assistant"]
        check(any(r and reasoning[:200] in r for r in replayed), f"{label}: prior reasoning is replayed as reasoning_content",
              f"assistant reasoning_content lengths: {[len(r or '') for r in replayed]}")


def check_bridge(model: str, effort: str) -> None:
    anthropic_url = require("CODEX_CLIPROXY_ANTHROPIC_BASE_URL").rstrip("/") + "/v1/messages"
    openai_url = require("CODEX_CLIPROXY_BASE_URL").rstrip("/")
    key = require("CODEX_CLIPROXY_API_KEY")

    print(f"\n=== bridge: Claude Code shape via {anthropic_url} ===")
    marker = f"pa1-route-{uuid.uuid4().hex[:12]}"
    since = time.time() - 1
    tool = {"name": "Bash", "description": "Run a shell command.",
            "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}
    system = [{"type": "text", "text": f"You are Claude Code. [{marker}]"}]
    first_user = {"role": "user", "content": "Run `echo pa1` with the Bash tool, then report the output."}
    body = {"model": model, "max_tokens": 32000, "thinking": {"type": "adaptive"}, "output_config": {"effort": effort},
            "system": system, "tools": [tool], "messages": [first_user]}
    status, raw = http_post(anthropic_url, key, body, {"anthropic-version": "2023-06-01"})
    if not check(status == 200, "Claude Code turn 1 succeeds through the bridge", f"HTTP {status}: {raw[:300]}"):
        return
    reply = json.loads(raw)
    thinking = next((b.get("thinking") for b in reply.get("content", []) if b.get("type") == "thinking"), None)
    tool_use = next((b for b in reply.get("content", []) if b.get("type") == "tool_use"), None)
    check(bool(thinking), "Claude Code turn 1 returns a thinking block", json.dumps(reply.get("content"))[:300])
    if tool_use is None:
        record("SKIP", "Claude Code turn 2 replay", "model did not call the tool; rerun")
        check_upstream("claude-code", upstream_bodies(since, marker), effort, None)
        return
    body["messages"] = [first_user, {"role": "assistant", "content": reply["content"]},
                        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use["id"], "content": "pa1"}]}]
    status, raw = http_post(anthropic_url, key, body, {"anthropic-version": "2023-06-01"})
    check(status == 200, "Claude Code turn 2 succeeds through the bridge", f"HTTP {status}: {raw[:300]}")
    time.sleep(1)
    check_upstream("claude-code", upstream_bodies(since, marker), effort, thinking)

    print(f"\n=== bridge: Codex shape via {openai_url}/responses ===")
    marker = f"pa1-route-{uuid.uuid4().hex[:12]}"
    since = time.time() - 1
    shell = {"type": "function", "name": "shell", "description": "Run a shell command", "strict": False,
             "parameters": {"type": "object", "properties": {"command": {"type": "array", "items": {"type": "string"}}}, "required": ["command"]}}
    inputs = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Run `echo pa1` with the shell tool, then report the output."}]}]
    base = {"model": model, "instructions": f"You are Codex, based on GPT-5. [{marker}]", "tools": [shell],
            "tool_choice": "auto", "parallel_tool_calls": True, "reasoning": {"effort": effort, "summary": "auto"},
            "store": False, "stream": True, "include": ["reasoning.encrypted_content"]}
    status, raw = http_post(openai_url + "/responses", key, {**base, "input": inputs})
    if not check(status == 200, "Codex turn 1 succeeds through the bridge", f"HTTP {status}: {raw[:300]}"):
        return
    events = [json.loads(m) for m in re.findall(r"^data: (\{.*\})$", raw, re.M)]
    done = next((e["response"] for e in events if e.get("type") == "response.completed"), None)
    output = (done or {}).get("output", [])
    summary = next((i["summary"][0]["text"] for i in output if i.get("type") == "reasoning" and i.get("summary")), None)
    call = next((i for i in output if i.get("type") == "function_call"), None)
    check(bool(summary), "Codex turn 1 returns a reasoning item", json.dumps(output)[:300])
    if call is None:
        record("SKIP", "Codex turn 2 replay", "model did not call the tool; rerun")
        check_upstream("codex", upstream_bodies(since, marker), effort, None)
        return
    inputs += [i for i in output if i.get("type") in ("reasoning", "function_call")]
    inputs.append({"type": "function_call_output", "call_id": call["call_id"], "output": "pa1"})
    status, raw = http_post(openai_url + "/responses", key, {**base, "input": inputs})
    check(status == 200, "Codex turn 2 succeeds through the bridge", f"HTTP {status}: {raw[:300]}")
    time.sleep(1)
    check_upstream("codex", upstream_bodies(since, marker), effort, summary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", default="glm-5p3-flash")
    parser.add_argument("--expect-version", default="1.93.0")
    parser.add_argument("--planned-spend-usd", type=float, default=100.0,
                        help="budget the job needs; the 2026-09-20 GLM Fireworks batch cost ~USD 21 per clean harness at PA1 prices")
    parser.add_argument("--max-tokens", type=int, default=131072, help="max_tokens the harness configs send")
    parser.add_argument("--input-price", type=float, default=0.15, help="USD per 1M uncached input tokens")
    parser.add_argument("--cached-price", type=float, default=0.03, help="USD per 1M cached input tokens")
    parser.add_argument("--output-price", type=float, default=0.5, help="USD per 1M output tokens")
    parser.add_argument("--stream-repeats", type=int, default=10)
    parser.add_argument("--stream-prefix-lines", type=int, default=1200, help="~14 tokens per line")
    parser.add_argument("--replay-sentences", type=int, default=60, help="~30 tokens per sentence")
    parser.add_argument("--effort", default="max", help="effort the bridge checks request")
    parser.add_argument("--bridge", action="store_true", help="also check Claude Code and Codex through CLIProxyAPI")
    parser.add_argument("--skip-direct", action="store_true")
    parser.add_argument("--report", type=Path, help="write all results as JSON")
    args = parser.parse_args()
    if args.env_file:
        load_env_file(args.env_file)

    if not args.skip_direct:
        check_direct(Gateway(require("LITELLM_OPENAI_BASE_URL"), require("LITELLM_API_KEY"), args.model), args)
    if args.bridge:
        check_bridge(args.model, args.effort)

    if args.report:
        args.report.write_text(json.dumps({"model": args.model, "results": results}, indent=2) + "\n")
    counts = {s: sum(r["status"] == s for r in results) for s in ("PASS", "WARN", "FAIL", "SKIP")}
    print("\n" + " ".join(f"{k}={v}" for k, v in counts.items()))
    for r in results:
        if r["status"] in ("FAIL", "WARN"):
            print(f"  {r['status']}: {r['check']}")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
