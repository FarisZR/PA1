# Deferred Claude Opus 5 runs

Opus 5 is intentionally excluded from the current Luna/DeepSeek/Kimi primary
jobs. Keep these files untouched until the Opus budget/run is enabled.

There is one config per harness so the original benchmark rule still applies:
run only one harness job at a time.

## Opus policy

- model: Claude Opus 5
- reasoning effort: medium
- context: native 1M
- Pi: direct Anthropic API
- Claude Code: direct Anthropic API
- Codex: dedicated Responses-to-Anthropic bridge because Codex 0.151.0 does not
  speak Anthropic Messages directly

Claude Code's Pier adapter remaps all model-selection channels to the selected
Opus model; the YAML additionally maps the Fable tier to Opus.

## Additional environment variables

Append the values from `benchmark/deferred/opus/env.example` to
`benchmark/env.local`:

```text
ANTHROPIC_API_KEY
CODEX_OPUS_RESPONSES_API_KEY
CODEX_OPUS_RESPONSES_BASE_URL
```

Generate the bridge-local key with, for example:

```bash
openssl rand -hex 32
```

`CODEX_OPUS_RESPONSES_BASE_URL` must be the HTTPS `/v1` URL at which Pier task
containers can reach the dedicated bridge. It must not be the shared LiteLLM
model gateway.

## Start the Codex Opus bridge

```bash
cd ~/PA1/benchmark/bridges/codex-opus
docker compose --env-file ../../env.local up -d --build
docker compose --env-file ../../env.local ps
curl -fsS http://127.0.0.1:4000/health/liveliness
```

The bridge has exactly one route, `claude-opus-5 -> anthropic/claude-opus-5`,
and uses `ANTHROPIC_API_KEY` for the outbound request to Anthropic.

## Generate the deferred Codex files

From `~/PA1`:

```bash
python3 benchmark/scripts/prepare_configs.py \
  --env-file benchmark/env.local \
  --include-opus
```

In addition to the normal current-run artifacts, this creates:

```text
benchmark/generated/codex-opus.toml
benchmark/generated/codex-opus-models.json
```

## Run Opus later

Run one harness at a time:

```bash
PIER=~/pier/.venv/bin/pier

$PIER job start -c benchmark/deferred/opus/pi.yaml \
  --env-file benchmark/env.local

$PIER job start -c benchmark/deferred/opus/claude-code.yaml \
  --env-file benchmark/env.local

$PIER job start -c benchmark/deferred/opus/codex.yaml \
  --env-file benchmark/env.local
```
