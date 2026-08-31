# Codex compatibility bridge (CLIProxyAPI)

Codex 0.151.0 speaks only the OpenAI Responses protocol, and it always attaches
two things the Fireworks-backed gateway routes reject. This service is the
protocol adapter that makes Codex usable with the benchmark's third-party
models. It replaces the earlier `benchmark/bridges/codex-opus/` LiteLLM bridge,
which only handled Anthropic.

```text
Codex 0.151.0
  -> OpenAI Responses            (http://<bridge>/v1/responses)
CLIProxyAPI v7.2.146
  -> OpenAI Chat Completions     -> existing LiteLLM gateway -> Fireworks
  -> Anthropic Messages          -> api.anthropic.com        (deferred Opus)
```

Pi and Claude Code do not use the bridge. Codex's Luna route does not use it
either: Luna is OpenAI-backed, its native Responses path works through the
gateway unchanged, and translating it through Chat Completions would needlessly
change first-party behavior.

## The problem this solves

Two independent defects, both reproduced against the live gateway:

| Codex field | What happens | Why |
| --- | --- | --- |
| `client_metadata` | Fireworks: `Extra inputs are not permitted, field: 'client_metadata'` | Codex 0.151.0 attaches it unconditionally in `ResponsesApiRequest` (`codex-rs/core/src/client.rs`, tag `rust-v0.151.0`). There is no config key or CLI flag to disable it. |
| `reasoning: {effort, summary}` | Fireworks: `Request body field 'reasoning_effort' is of type 'object', expected 'string'` | The gateway forwards the Responses `reasoning` object into `reasoning_effort` instead of mapping `reasoning.effort` to a string. |

A request-only field stripper is not sufficient. DeepSeek and Kimi return
plaintext `reasoning_content` on Chat Completions rather than OpenAI's opaque
`reasoning.encrypted_content`, so the adapter must also carry each turn's
reasoning back through Codex and reconstruct it upstream, or the model loses its
own chain of thought on the next tool turn. CLIProxyAPI does both.

## Pinned version

| Component | Pin |
| --- | --- |
| Image | `eceasy/cli-proxy-api:v7.2.146` |
| Digest | `sha256:238691ac26ce55e4d1c5219d72e3ad74838f81eda26359912eeb415e2820d163` |

`compose.yaml` pins the digest, so the tag is only a human-readable label and
cannot silently move between jobs. The Responses translator in `v7.2.146` is
byte-identical to the reviewed upstream tree (`git diff v7.2.146 81e1b537 --
internal/translator/openai/openai/responses/` is empty), and earlier releases
had different reasoning-replay behavior. Do not downgrade.

## Configuration

The runtime config is **generated**, never committed: it carries the gateway
key and, for Opus, the Anthropic key.

```bash
cd ~/PA1
python3 benchmark/scripts/prepare_configs.py --env-file benchmark/env.local
```

writes `benchmark/generated/cliproxy-config.yaml` (mode 0600) and
`benchmark/generated/codex-cliproxy.toml`, the Codex provider table the job
configs point at.

Two properties of the generated config are load-bearing:

- **Reasoning levels come from the Codex catalog.** CLIProxyAPI snaps an
  incoming `reasoning.effort` to the nearest level it knows about, and its
  default set is low/medium/high. A model declared without `thinking.levels`
  would therefore turn PA1's `max` requests into `high` upstream, silently
  changing the variable the benchmark measures. The generator derives each
  model's levels from the same `supported_reasoning_levels` it writes into the
  Codex catalog, so the two cannot drift apart.
- **Everything that could make the layer non-transparent is off.** `request-retry`,
  credential cooldown, retry intervals, and the quota fallbacks are disabled.
  Left at their defaults they would retry failed calls without attribution, take
  the single credential out of service after one transient error, and — in the
  case of `quota-exceeded.switch-preview-model` — answer with a different model.

## Running it

```bash
cd ~/PA1/benchmark/bridges/codex-cliproxy
docker compose --env-file ../../env.local up -d
docker compose ps          # expect: healthy
```

Restart it after any `prepare_configs.py` run that changes the model set (for
example enabling Opus):

```bash
docker compose --env-file ../../env.local up -d --force-recreate
```

### Why port 80

Pier's egress Squid is generated with `acl Safe_ports port 80 443` and
`http_access deny CONNECT !SSL_ports`, so a trial container can reach **only**
port 80 over plain HTTP and port 443 over CONNECT. CLIProxyAPI's own 8317 is
unreachable no matter what the domain allowlist contains, which is why
`compose.yaml` publishes `80:8317`.

The default bind is the Docker bridge gateway `172.17.0.1`. Every trial's egress
proxy container can route to it, and it is not published on the LAN.
`prepare_configs.py` rejects a `CODEX_CLIPROXY_BASE_URL` on any other port, and
also rejects a dotless bare hostname, because Pier discards those when building
the Squid allowlist and every Codex request would then be denied.

The Codex-to-bridge hop is plain HTTP on the runner host. What crosses it is
`CODEX_CLIPROXY_API_KEY`, a token chosen locally for this bridge — the gateway
and Anthropic credentials stay in the bridge's own config and never leave the
host. To use TLS instead, set `tls.enable`/`cert`/`key` in the generated config,
publish on 443, and append the issuing CA to the `PIER_EXTRA_CA_CERTS` bundle,
which accepts multiple PEM blocks.

### Private CA trust

CLIProxyAPI has no CA configuration key; Go uses the system trust store. The
compose file mounts PA1's tracked PUKI bundle at `/pa1-ca` and sets
`SSL_CERT_DIR=/etc/ssl/certs:/pa1-ca`, which **adds** the private roots while
keeping the image's public roots. Using `SSL_CERT_FILE` instead would replace
the public roots and break the Anthropic route.

## Acceptance tests

### Translation contract (offline, deterministic)

```bash
python3 benchmark/bridges/codex-cliproxy/tests/test_codex_translation.py
```

Runs the pinned image against a recording mock gateway and replays a three-turn
Codex tool loop. It asserts that `client_metadata` never reaches the upstream,
that `reasoning_effort` arrives as a string and keeps `max`, that each turn's
full `reasoning_content` is replayed verbatim on the *same assistant message as
its tool call*, and that reasoning/cached/input/output token counts survive into
Codex's usage. It uses a mock rather than the live gateway because the replay
assertions need a byte-exact expected value.

### Live gateway

```bash
# needs CODEX_CLIPROXY_REQUEST_LOG=true at generation time for the last checks
python3 benchmark/bridges/codex-cliproxy/tests/check_live_gateway.py \
    --env-file benchmark/env.local --effort max
```

For `deepseek-v4-flash` and `kimi-k3` this first confirms the direct gateway
*still fails* — otherwise the run cannot show the bridge fixed anything — then
sends the identical request through the bridge, and finally reads the bridge's
own request log to prove the real upstream body is clean.

`request-log` records every prompt verbatim, so leave
`CODEX_CLIPROXY_REQUEST_LOG=false` for benchmark jobs and enable it only for
acceptance runs. Logs land in `benchmark/generated/cliproxy-logs/`.

### Codex driving a real task

Neither test above exercises Codex itself. `tests/acceptance-codex-only.yaml`
runs Codex alone on the pilot task at **low** reasoning:

```bash
cd ~/PA1
~/pier/.venv/bin/pier job start \
  -c benchmark/bridges/codex-cliproxy/tests/acceptance-codex-only.yaml \
  --env-file benchmark/env.local
```

Low effort is deliberate. This checks transport, not model quality, and the
primary jobs' `max` effort turns a few-minute check into a very long and
expensive one. Effort handling itself is covered by the two tests above, which
assert `max` survives translation.

Before a primary batch, also run the full pilot-task acceptance job described in
`benchmark/README.md`, which covers all three harnesses.

## Known behavior to watch

- **Codex context accounting.** CLIProxyAPI carries reasoning in the Responses
  `summary` field with an empty `encrypted_content`. Codex's local context
  estimator treats reasoning items with `encrypted_content` specially, so its
  *estimate* of older reasoning can read as zero. This does not change what the
  model receives: the bridge still reconstructs the exact `reasoning_content`
  upstream. It is a local token-accounting concern for very long tasks only.
- **Unpinned metadata refresh.** CLIProxyAPI fetches a model-metadata catalog
  and its management-panel asset from GitHub at startup and every three hours.
  Those entries are keyed by OAuth provider and do not govern the
  `openai-compatibility` routes PA1 uses, but the Anthropic route's model
  metadata does come from that catalog.
- **Opus route is not yet validated end to end** — see below.

## Deferred Opus route

`prepare_configs.py --include-opus` adds a `claude-api-key` entry for
`claude-opus-5` with no `base-url`, so CLIProxyAPI targets
`api.anthropic.com/v1/messages` directly and never the shared gateway. Do not
add a `cloak` block or `fingerprint-profile` to that entry: those rewrite
Codex's system prompt.

This route replaces the deleted LiteLLM bridge but has **not** been validated
against live Anthropic traffic. Before enabling Opus, check specifically:

- `/v1/responses/compact` returns HTTP 501 for Claude upstreams. Confirm Codex
  0.151.0 does not depend on it for this workload.
- Reasoning-token counts on the Anthropic path are **estimated** by CLIProxyAPI
  (reasoning text length / 4) because the Anthropic API does not report them.
  Do not compare them with the exact counts on the Fireworks path.
- Effort is mapped to Anthropic adaptive thinking rather than
  `thinking.budget_tokens`, and `max_tokens` defaults to 32000 when Codex omits
  `max_output_tokens`. Both differ from the old LiteLLM bridge, so Opus Codex
  numbers are not comparable to any collected with it.
