# Codex Opus bridge

Codex 0.150.1 only emits OpenAI Responses requests. Claude Opus 5 is intentionally
not available through the shared benchmark LiteLLM gateway, so this service does
one protocol-translation job:

```text
Codex -> /v1/responses -> dedicated bridge -> Anthropic Messages -> api.anthropic.com
```

The bridge has exactly one model route, `claude-opus-5`, backed by
`anthropic/claude-opus-5`. It uses `ANTHROPIC_API_KEY` for the upstream request.
`CODEX_OPUS_RESPONSES_API_KEY` is only the credential Codex uses to authenticate
to this local bridge.

The translator is pinned to LiteLLM 1.83.0. It is a separate deployment and does
not reference `LITELLM_API_KEY`, `LITELLM_OPENAI_BASE_URL`, or the shared model
proxy.

## Run

From this directory, with both keys available in the environment:

```bash
docker compose up -d --build
```

The safe default binds `127.0.0.1:4000`. The benchmark's filtered Docker egress
only permits HTTP(S) ports 80/443, so for actual Pier runs expose this service on
a stable HTTPS endpoint (normally through the runner's reverse proxy) and set:

```dotenv
CODEX_OPUS_RESPONSES_BASE_URL=https://codex-opus-bridge.example/v1
```

Do not point `CODEX_OPUS_RESPONSES_BASE_URL` at the shared LiteLLM gateway.
