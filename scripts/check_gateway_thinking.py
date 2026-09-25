#!/usr/bin/env python3
"""Check on the live gateway whether Claude Code's earlier thinking reaches a Fireworks model (issue #102).

The script repeats one step of Claude Code's loop through the gateway's Anthropic
Messages endpoint. The first request asks the model to call a tool with
`thinking: {"type": "adaptive"}` and `output_config: {"effort": "max"}`, as Claude
Code does. The assistant turn of the answer, with its thinking blocks exactly as
the gateway returned them, is then sent back with the tool result, once complete
and once with the thinking blocks removed. If the gateway forwards the earlier
thinking, the complete request has more input tokens than the one without it; if
it drops the thinking, both have the same number.

Reads LITELLM_BASE_URL and LLM_PROXY_KEY and never prints them. Each model costs
three short requests.

    python scripts/check_gateway_thinking.py deepseek-v4p1-flash glm-5p3-flash kimi-k3
"""
import json
import os
import sys
import urllib.request

URL = os.environ["LITELLM_BASE_URL"].rstrip("/") + "/v1/messages"
TOOLS = [{"name": "Bash", "description": "Run a shell command and return its output.",
          "input_schema": {"type": "object", "properties": {"command": {"type": "string"}},
                           "required": ["command"]}}]
TASK = ("The tests in /app fail with an import error. Decide which single shell command to run first to "
        "find the cause, then call the Bash tool with it. Do not answer in text.")


def post(model: str, messages: list) -> tuple[dict, str]:
    body = {"model": model, "max_tokens": 4000, "messages": messages, "tools": TOOLS,
            "thinking": {"type": "adaptive"}, "output_config": {"effort": "max"}}
    request = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {os.environ['LLM_PROXY_KEY']}", "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response), response.headers.get("x-litellm-version", "")


def prompt_tokens(response: dict) -> int:
    """All input tokens of a request, including those the provider read from its cache."""
    usage = response["usage"]
    return sum(usage.get(key) or 0 for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))


def check(model: str) -> dict:
    first, version = post(model, [{"role": "user", "content": TASK}])
    content = first["content"]
    thinking = [b for b in content if b["type"] in ("thinking", "redacted_thinking")]
    tool_use = next((b for b in content if b["type"] == "tool_use"), None)
    if tool_use is None:
        return {"model": model, "litellm": version, "error": "no tool call", "blocks": [b["type"] for b in content]}
    result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use["id"],
                                           "content": "ModuleNotFoundError: No module named 'parser'"}]}
    history = [{"role": "user", "content": TASK}]
    with_thinking, _ = post(model, history + [{"role": "assistant", "content": content}, result])
    without_thinking, _ = post(model, history + [
        {"role": "assistant", "content": [b for b in content if b not in thinking]}, result])
    return {
        "model": model,
        "litellm": version,
        "thinking_blocks": len(thinking),
        "thinking_chars": sum(len(b.get("thinking", "")) for b in thinking),
        "signatures": sorted({"missing" if "signature" not in b else "empty" if not b["signature"] else "present"
                              for b in thinking}),
        "input_tokens_with_thinking": prompt_tokens(with_thinking),
        "input_tokens_without_thinking": prompt_tokens(without_thinking),
    }


if __name__ == "__main__":
    for name in sys.argv[1:]:
        print(json.dumps(check(name)), flush=True)
