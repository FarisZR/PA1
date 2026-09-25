# LiteLLM reasoning-history audit (issue #111)

Evidence that AiOrbit's LiteLLM 1.101.0 removed `reasoning_content` from
requests to Fireworks until about 2026-09-21 12:02 UTC. The results chapter
describes the defect in "Reasoning history removed by the gateway".

| File | Content | Produced by |
| --- | --- | --- |
| `replay-request.json` | Upstream Chat Completions body of one DeepSeek V4.1 Flash Codex request (2026-09-21T12:01:40Z, first 12 messages) | Taken from the bridge log named in the file |
| `replay-results.jsonl` | Reasoning fields forwarded by LiteLLM 1.98.0, 1.101.0, and 1.102.0 for that request | `scripts/replay_litellm_fireworks.py` |
| `bridge-requests.csv` | Per-request metadata for the 2,469 DeepSeek requests of the Codex job | `scripts/extract_bridge_reasoning_audit.py` |
| `claude-code-replay-results.jsonl` | Effort and reasoning fields that LiteLLM 1.98.0, 1.101.0, and 1.102.1 forward for a Claude Code-style Anthropic Messages request to a Fireworks model (issues #94, #102) | `scripts/replay_litellm_messages.py` |

## Sanitization

The bridge request logs under `benchmark/generated/cliproxy-logs/` contain
complete prompts and credentials and are not published.

- `replay-request.json` keeps the message roles, tool calls, tool-call ids,
  and `reasoning_content` of the recorded request unchanged. Other message
  contents longer than 400 characters are shortened, and the tool definitions
  are omitted; neither affects the message transform under test.
- `bridge-requests.csv` contains no prompt text. Each row records the request
  time (UTC), the Codex session id and the published trial it belongs to, the
  `X-Litellm-Version` header, the prompt and cached token counts returned by
  Fireworks, the number of earlier assistant messages that carried
  `reasoning_content` upstream, the reasoning characters sent and streamed back,
  the input size LiteLLM reported when it rejected a request as too large
  (`rejected_input_tokens`, from `Max Input Tokens=…, Got=…` in the HTTP 400
  body), and the source log's file name and SHA-256 hash. The hashes let a supervisor
  match every row to the retained private log.

One row (`sess-1`, 2026-09-20T23:52Z) is a single 339-token probe before the
job started and belongs to no trial.

One row has `rejected_input_tokens` set: the first Codex attempt on
`python-statemachine` (2026-09-21T03:01:42Z, 1,049,555 tokens). LiteLLM
counted the reasoning in the request before its Fireworks transform removed
it, so the request was rejected although Fireworks had counted the previous
request as 248,558 prompt tokens. The results chapter shows this and the two
Pi rejections of the same kind.

## Reproduction

```bash
for v in 1.98.0 1.101.0 1.102.0; do
  LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-project --with "litellm==$v" \
    python scripts/replay_litellm_fireworks.py \
    data/litellm-reasoning-audit/replay-request.json
done
# needs the private bridge logs
python3 scripts/extract_bridge_reasoning_audit.py \
  --out data/litellm-reasoning-audit/bridge-requests.csv
```

## Claude Code route at the current gateway version

The bridge logs record the `X-Litellm-Version` response header: all 1,979
requests of the GLM-5.3-Flash rerun on 2026-09-24 went through LiteLLM 1.102.1.
`claude-code-replay-results.jsonl` replays a Claude Code-style request through
that version and the two earlier ones. LiteLLM 1.98.0 and 1.101.0 forward no
earlier thinking and lower `max` effort to `high`, which reproduces issues #94
and #102. LiteLLM 1.102.1 forwards the earlier thinking as `reasoning_content`
but still lowers the effort to `high`, because the model declares no supported
effort levels; AiOrbit's `/model_group/info` still declared none for the three
Fireworks models on 2026-09-25.
