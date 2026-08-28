# Benchmark runner configuration

This directory contains the reproducible Pier configuration for the primary
DeepSWE matrix: four harnesses, four models, and the ten selected DeepSWE v1.1
tasks. Each harness is one Pier job containing all four models. With one attempt
per task, each harness job creates 40 trials and the full matrix creates 160.

`n_concurrent_trials` is set to **10** in every job. Run one harness job at a
time. The limit is global to that job, so Pier can spread those ten concurrent
trials over the four model providers without allowing ten trials per model.

## Frozen inputs

The benchmark configuration is written against these exact versions. Do not
update them between primary runs.

| Component | Frozen revision/version |
| --- | --- |
| FZR Pier fork | `6307395aad1679f8044d3274abe65c6fe600070d` |
| DeepSWE | `435ee89ec2f2e2289f33b0da4f992f0b7b7266b9` |
| Codex CLI | `0.150.1` |
| Claude Code | `2.1.247` |
| Pi | `0.84.3` |
| OpenCode 2 beta | `0.0.0-beta-18387` |

Claude Code is additionally launched with `DISABLE_AUTOUPDATER=1`. The staged
OpenCode 2 configuration sets `autoupdate: false`, so the pinned harness cannot
replace itself during a benchmark job.

Pier also writes a `lock.json` into every job directory. Keep that file with the
results; it records the resolved trial configuration, task hashes, and Pier
revision.

## Checkout layout

Run the commands below from the PA1 repository root. The configs intentionally
use `../DeepSWE/tasks`, so the portable layout is:

```text
~/PA1/
~/DeepSWE/
```

Pier does not expand `~` in `datasets.path`, so putting `~/DeepSWE/tasks`
directly in a YAML config does not work. A relative sibling path avoids a
machine-specific home-directory prefix.

Before the benchmark, pin DeepSWE explicitly:

```bash
git -C ../DeepSWE checkout 435ee89ec2f2e2289f33b0da4f992f0b7b7266b9
```

Use the FZR Pier fork at the commit in the table above rather than an unpinned
PyPI install.

## Provider routing

Copy `benchmark/env.example` to `benchmark/env.local` and fill in the local
values. `env.local` is ignored by Git.

- **Claude Opus 5, medium:** Pi, Claude Code, and OpenCode use the upstream
  Anthropic API directly through `ANTHROPIC_API_KEY`.
- **GPT-5.6 Luna, max; DeepSeek V4 Flash 0731, max; Kimi K3, max:** use the
  custom LiteLLM gateway through `LITELLM_API_KEY` and the protocol-specific
  gateway base URLs.
- **Pi:** keeps each model under its native built-in provider so Pi 0.84.3 keeps
  its own compatibility metadata and official model pricing. Luna stays
  `openai/gpt-5.6-luna`; DeepSeek uses `deepseek/deepseek-v4-flash` (the stable
  API ID for the selected 0731 checkpoint); Kimi uses `moonshotai/kimi-k3`.
  The generated Pi job changes only the DeepSeek/Moonshot base URL to LiteLLM.
  The gateway therefore needs a `deepseek-v4-flash` alias pinned to the same
  0731 checkpoint in addition to the `deepseek-v4-flash-0731` alias used by the
  other harnesses.
- **Codex + GPT-5.6 Luna:** remains Codex's built-in `openai` provider. Pier
  only redirects `openai_base_url` to LiteLLM. This preserves Codex's
  OpenAI-specific behavior, including its model metadata, multi-agent selection,
  and remote compaction path.
- **Codex + DeepSeek/Kimi:** use an explicit third-party `litellm` Responses
  provider and generated fallback-style model metadata with the models' native
  context limits.
- **Codex + Opus:** is the one exception that cannot use the shared LiteLLM
  gateway. Codex 0.150.1 only speaks the Responses wire protocol, so
  `CODEX_OPUS_RESPONSES_*` must identify a dedicated Responses-to-Anthropic
  bridge. That bridge connects directly to `api.anthropic.com` using the
  benchmark's Anthropic account/key; it must not route Opus through the shared
  model proxy.

Generate the endpoint-specific Pi job plus Codex TOML/model catalog before
starting the benchmark:

```bash
python3 benchmark/scripts/prepare_codex_configs.py --env-file benchmark/env.local
uv run benchmark/scripts/preflight.py --env-file benchmark/env.local
```

The generated files contain endpoint URLs and provider metadata, but never API
keys. They are written under `benchmark/generated/` and ignored by Git. The Pi
source job needs generation because Pier does not interpolate environment
variables inside nested `kwargs.pi_config`; the generator replaces only the
non-secret LiteLLM base URL while keeping the native Pi provider/model IDs.
Codex reads the actual key from the environment named by `env_key`.

For the Codex+Opus cell, deploy the dedicated bridge in
`benchmark/bridges/codex-opus/` on the benchmark runner (or another endpoint the
runner can reach). It is pinned to LiteLLM 1.83.0 solely for Responses-to-Anthropic
protocol translation and has one route: `claude-opus-5` ->
`anthropic/claude-opus-5`. Its outbound credential is `ANTHROPIC_API_KEY`; it has
no shared-LiteLLM route or credential. The bridge README documents the HTTPS
exposure requirement for Pier's filtered egress.

For third-party model entries Codex also requires base harness instructions.
The generator downloads the generic fallback prompt from the exact
`rust-v0.150.1` Codex source tag and verifies SHA-256
`ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807`
before embedding it in the generated catalog. This avoids copying Luna/GPT
model-specific instructions onto Opus, DeepSeek, or Kimi.

## Context-window policy

The primary benchmark keeps each model's normal provider context window except
for GPT-5.6 Luna. Luna is capped to Codex's reduced **272,000-token** window in
every harness so the benchmark stays below OpenAI's long-context pricing tier.

- Codex derives Luna's 272k metadata from the frozen Codex binary's bundled
  catalog; no custom Luna catalog is supplied.
- Pi uses its built-in `openai/gpt-5.6-luna` entry, which is already 272k in
  Pi 0.84.3. OpenCode declares 272k explicitly.
- Claude Code uses the same `[1m]` model-name convention used by third-party
  integrations such as Z.AI, then sets `CLAUDE_CODE_AUTO_COMPACT_WINDOW=272000`
  for Luna. This makes Claude Code compact at the benchmark limit locally rather
  than relying on the gateway to reject oversized requests.
- DeepSeek and Kimi also use `[1m]` aliases in Claude Code so unknown third-party
  names are not assigned a small default context. Opus keeps its native 1M
  window; DeepSeek keeps 1M; Kimi's native window is 1,048,576 tokens (Claude
  Code's `[1m]` marker represents the corresponding 1M class).

The context limits are harness metadata/compaction limits, not a request to pad
prompts to those sizes.

## Cost normalization

`benchmark/pricing.yaml` freezes the official model launch/reference prices used
for the benchmark cost metric. Provider invoices and gateway-specific prices are
not comparable across harnesses and are therefore not the source of truth for
ranking. Final analysis reprices the recorded token/cache usage with this table.

Pi is configured so its own per-call cost metadata already follows the same
upstream model identities: `anthropic`, `openai`, `deepseek`, and `moonshotai`.
In particular, no Fireworks price block is injected into Pi. Raw harness/provider
`cost_usd` fields remain useful diagnostics, but the normalized PA1 cost is
computed from token usage plus the frozen table.

## Running jobs

If Codex+Opus is part of the next run, start the dedicated bridge first. From
`benchmark/bridges/codex-opus/`:

```bash
docker compose --env-file ../../env.local up -d --build
```

Expose it through the stable HTTPS URL configured in
`CODEX_OPUS_RESPONSES_BASE_URL`; the default compose binding is localhost-only.

Run the benchmark jobs from the PA1 repository root:

```bash
pier job start -c benchmark/generated/pi.yaml --env-file benchmark/env.local
pier job start -c benchmark/configs/claude-code.yaml --env-file benchmark/env.local
pier job start -c benchmark/configs/codex.yaml --env-file benchmark/env.local
```

Each job writes to `benchmark/runs/<harness>/` and can be resumed by Pier using
that directory.

### OpenCode 2 blocker

The selected harness is OpenCode **2**, not OpenCode 1. The frozen V2 package is
`@opencode-ai/cli@0.0.0-beta-18387` and its executable is `opencode2`. The
current FZR Pier `opencode` adapter still installs `opencode-ai` and executes
`opencode`, so running that adapter would benchmark the wrong harness.

`configs/opencode-v2.yaml` is therefore committed as the intended four-model
job definition but is marked blocked until PA1 issue #9 adds a Pier OpenCode 2
adapter. Do not substitute the OpenCode 1 adapter for the primary benchmark.
That V2 adapter must also translate the existing `kwargs.variant` value to V2's
`provider/model#variant` model selector and resolve `{env:...}` base URLs before
building Pier's no-network egress allowlist.

## Preflight

Before paid runs, do one task with each harness/model routing mode and verify:

1. the reported harness version matches the frozen version;
2. the expected provider endpoint receives the request;
3. Luna reports/compacts against 272k in every harness; for Claude Code,
   verify the `[1m]` alias plus `CLAUDE_CODE_AUTO_COMPACT_WINDOW=272000`;
4. Pi reports the native providers (`openai`, `deepseek`, `moonshotai`) and its
   built-in launch-price metadata rather than proxy-provider prices;
5. Codex Luna still uses its first-party OpenAI provider behavior;
6. Codex Opus reaches the dedicated Responses-to-Anthropic bridge and that
   bridge's upstream connection is the direct Anthropic API;
7. token, cache, cost, wall-clock, and verifier fields are present in Pier's
   result/trajectory output.
