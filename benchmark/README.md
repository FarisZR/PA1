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
- **Codex + GPT-5.6 Luna:** remains Codex's built-in `openai` provider. Pier
  only redirects `openai_base_url` to LiteLLM. This preserves Codex's
  OpenAI-specific behavior, including its model metadata, multi-agent selection,
  and remote compaction path.
- **Codex + DeepSeek/Kimi:** use an explicit third-party `litellm` Responses
  provider and generated fallback-style model metadata with the models' native
  context limits.
- **Codex + Opus:** Codex does not speak Anthropic Messages directly; current
  Codex only supports the Responses wire protocol. The separate
  `CODEX_OPUS_RESPONSES_*` variables therefore point to a Responses-compatible
  bridge whose upstream route is Anthropic. This is intentionally separate from
  the normal LiteLLM route. If direct Anthropic Messages is a hard requirement
  for this cell, Codex + Opus cannot be run with the current Codex CLI.

Generate the endpoint-specific Codex TOML files and third-party model catalog
before starting Codex:

```bash
python benchmark/scripts/prepare_codex_configs.py --env-file benchmark/env.local
```

The generated files contain endpoint URLs and provider metadata, but never API
keys. They are written under `benchmark/generated/` and ignored by Git. Codex
reads the actual key from the environment named by `env_key`.

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
- Pi and OpenCode declare 272k explicitly for the Luna alias.
- The LiteLLM `gpt-5.6-luna` alias used by Claude Code must enforce the same
  272k maximum. Claude Code has no equivalent local model-catalog context
  override.
- Opus 5 keeps its upstream 1M window. Fireworks DeepSeek V4 Flash 0731 and Kimi
  K3 keep their 1040k provider windows.

The context limits are harness metadata/compaction limits, not a request to pad
prompts to those sizes.

## Running jobs

Run from the PA1 repository root:

```bash
pier job start -c benchmark/configs/pi.yaml --env-file benchmark/env.local
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
3. Luna reports/compacts against 272k in every harness;
4. Codex Luna still uses its first-party OpenAI provider behavior;
5. token, cache, cost, wall-clock, and verifier fields are present in Pier's
   result/trajectory output.
