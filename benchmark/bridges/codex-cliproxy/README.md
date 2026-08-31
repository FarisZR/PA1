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

A request-only field stripper is not sufficient. DeepSeek, Kimi, and GLM return
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

The config lives in two tracked templates:

| File | Contents |
| --- | --- |
| `config.template.yaml` | the whole deployment config, with `"__SENTINEL__"` placeholders for the three credentials and the request-log flag |
| `config.opus.template.yaml` | the Anthropic route, appended by `--include-opus` |

Both are reviewable and diffable. Everything above each file's `---8<---`
delimiter is documentation for a reader of the repository; everything below it
is written out verbatim.

```bash
cd ~/PA1
python3 benchmark/scripts/prepare_configs.py --env-file benchmark/env.local
```

substitutes the sentinels and writes `benchmark/generated/cliproxy-config.yaml`
(mode 0600), plus `benchmark/generated/codex-cliproxy.toml`, the Codex provider
table the job configs point at.

The file has to be generated rather than mounted straight from the template
because **CLIProxyAPI performs no environment interpolation** — there is no
`os.ExpandEnv` or `os.Getenv` anywhere in its config package — so the gateway
key, the bridge key, and the Anthropic key must be literals in the file it
reads. Sentinels are quoted in the template so it stays valid YAML on its own,
and the generator replaces the whole quoted scalar so credentials are escaped
rather than pasted in raw. It refuses to write a config with any placeholder
left unresolved.

Two properties of the generated config are load-bearing:

- **Reasoning levels are checked against the Codex catalog.** CLIProxyAPI snaps
  an incoming `reasoning.effort` to the nearest level it knows about, and its
  default set is low/medium/high. A model declared without `max` in
  `thinking.levels` would therefore turn PA1's `max` requests into `high`
  upstream, silently changing the variable the benchmark measures. Generation
  renders each model block from the same `supported_reasoning_levels` it writes
  into the Codex catalog and **fails** unless the template contains that block
  verbatim, so the two cannot drift apart.
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
runner already trusts the gateway's private PUKI roots, so the compose file
mounts the host bundle read-only at `/etc/ssl/certs` rather than carrying a
second copy of the certificates into the container. That also supplies the
public roots the Anthropic route needs.

This means the bridge depends on the runner trusting the gateway. On a machine
where the private roots are not installed system-wide it fails with
`x509: certificate signed by unknown authority`; install them, or mount
`benchmark/puki-root-ca-2022.pem` into a directory and set
`SSL_CERT_DIR=/etc/ssl/certs:<that directory>`. Do not use `SSL_CERT_FILE` for
this — it *replaces* the public roots instead of adding to them.

The trial containers are unaffected and keep using the tracked bundle through
`PIER_EXTRA_CA_CERTS`.

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

For `deepseek-v4-flash`, `kimi-k3`, and `glm-5p3` this first confirms the direct gateway
*still fails* — otherwise the run cannot show the bridge fixed anything — then
sends the identical request through the bridge, and finally reads the bridge's
own request log to prove the real upstream body is clean.

`request-log` records every prompt verbatim, so leave
`CODEX_CLIPROXY_REQUEST_LOG=false` for benchmark jobs and enable it only for
acceptance runs. Logs land in `benchmark/generated/cliproxy-logs/`.

### Generated config

```bash
python3 benchmark/bridges/codex-cliproxy/tests/test_generated_config.py
```

Runs the real generator into a temporary directory — never touching
`benchmark/generated/` — and checks what it actually emits: no unresolved
placeholders, credentials substituted, `max` still declared for all three models,
the transparency settings present, mode 0600, and the Codex provider TOML
pointing at the bridge. It then boots the pinned image on that exact file and
confirms CLIProxyAPI accepts it, serves exactly the three benchmark models, and
rejects an unknown API key. The `--include-opus` variant is checked too.

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

- **Codex context accounting — investigated, not a defect on this path.**
  CLIProxyAPI carries reasoning in the Responses `summary` field with no
  `encrypted_content`, and Codex's `get_non_last_reasoning_items_tokens` only
  counts reasoning items matching `encrypted_content: Some(_)`, so that term is
  always zero here. That term is a *correction*, applied only when the
  `x-reasoning-included` response header is absent — an OpenAI-backend header the
  bridge does not send — and it exists to compensate for a server that did not
  bill historical reasoning.

  On this path the server does bill it. The bridge replays reasoning as
  `reasoning_content` in the upstream body, so Fireworks charges it as prompt
  tokens, and those land in Codex's base term via the usage mapping. Measured on
  the 83 logged requests of a real Codex trial: the largest body was 578,035
  characters of which 222,644 were replayed reasoning, at 3.61 characters per
  reported prompt token. Excluding the reasoning would imply 2.22 characters per
  token, which no tokenizer produces on this content — the reasoning is counted.

  Skipping the correction is therefore correct rather than lossy; applying it
  would double-count. Across 74 adjacent turn pairs, Codex's base
  (`total_tokens` of the previous response) never exceeded the next request's
  real `prompt_tokens`, and every gap was attributable to locally added items
  that Codex estimates separately.

  This holds *because* the bridge replays reasoning upstream. The three-turn
  contract test asserts exactly that, so it also guards this property.

  Note the practical guard here is the 1,048,576-token context window, not
  auto-compaction: the Sol profile sets `auto_compact_token_limit: null`, which
  leaves `auto_compact_scope_limit` unset so only the full-window cap applies.
  The trial peaked around 160,000 tokens.
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
