#!/usr/bin/env python3
"""Replay a Claude Code-style request through LiteLLM's Anthropic Messages translation (issues #94, #102).

Claude Code sends Anthropic Messages requests with `thinking: {"type": "adaptive"}`,
`output_config: {"effort": "max"}`, and its earlier thinking blocks in the history.
The script sends such a request through ``litellm.anthropic.messages`` with a
``fireworks_ai/`` model to a local capture server that stands in for Fireworks,
and reports the forwarded ``reasoning_effort`` and how many earlier assistant
messages still carry ``reasoning_content``. It repeats the request with the
thinking block's signature missing, empty, and non-empty. The model is declared
as a reasoning model without effort levels, as AiOrbit declares it. No model is
called and no network access is needed.

Run it once per LiteLLM version, each in a throwaway environment:

    for v in 1.98.0 1.101.0 1.102.1; do
      LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-project --with "litellm==$v" \\
        python scripts/replay_litellm_messages.py
    done
"""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import litellm
# AiOrbit declares the model as a reasoning model without effort levels (`supports_reasoning: true`).
litellm.register_model({"fireworks_ai/accounts/fireworks/models/deepseek-v4p1-flash": {
    "litellm_provider": "fireworks_ai", "mode": "chat", "supports_reasoning": True, "supports_function_calling": True,
    "max_input_tokens": 1000000, "max_output_tokens": 64000, "input_cost_per_token": 0, "output_cost_per_token": 0}})

captured = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        captured.append(body)
        reply = json.dumps({"id": "r", "object": "chat.completion", "created": int(time.time()), "model": body.get("model"),
                            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply))); self.end_headers(); self.wfile.write(reply)

server = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}/v1"

def assistant(signature):
    block = {"type": "thinking", "thinking": "I should list the files first to find the parser."}
    if signature is not None:
        block["signature"] = signature
    return {"role": "assistant", "content": [block, {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}]}

results = {}
for label, signature in (("no signature", None), ("empty signature", ""), ("non-empty signature", "abc123")):
    captured.clear()
    messages = [{"role": "user", "content": "Fix the parser."}, assistant(signature),
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "src/ tests/"}]}]
    try:
        litellm.anthropic.messages.create(model="fireworks_ai/accounts/fireworks/models/deepseek-v4p1-flash",
                                          messages=messages, max_tokens=32000, api_base=base, api_key="x",
                                          thinking={"type": "adaptive"}, output_config={"effort": "max"})
    except Exception as exc:
        results[label] = f"error: {type(exc).__name__}: {str(exc)[:120]}"
        continue
    body = captured[-1] if captured else {}
    prior = [m for m in body.get("messages", []) if m.get("role") == "assistant"]
    results[label] = {"reasoning_effort": body.get("reasoning_effort"),
                      "assistant_messages": len(prior),
                      "with_reasoning_content": sum(bool(m.get("reasoning_content")) for m in prior)}
print(json.dumps({"litellm": version("litellm"), **results}))
server.shutdown()
