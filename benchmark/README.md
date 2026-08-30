# DeepSWE benchmark runner

This directory is the runnable configuration for the primary PA1 DeepSWE matrix:
four harnesses, four models, and ten selected DeepSWE v1.1 tasks. Each harness
is one Pier job containing all four models. With one attempt per task, each job
contains 40 trials and the full matrix contains 160 trials.

Every job uses `n_concurrent_trials: 10`. Run one harness job at a time; the
limit is global to the job, not per model.

## Frozen versions

The following revisions were checked on **2026-08-30** immediately before the
benchmark freeze. Do not update any of them between primary runs.

| Component | Frozen revision/version |
| --- | --- |
| FZR Pier fork | `4ca06113149262330a8e3d0a63285bf2ddf0768b` |
| DeepSWE | `0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea` |
| Codex CLI | `0.151.0` |
| Claude Code | `2.1.251` |
| Pi | `0.84.4` |
| OpenCode 2 beta | `0.0.0-beta-18684` |

The DeepSWE revision above is the current v1.1 task tree. Relative to the
previous PA1 pin, its only change is the upstream increase of task timeouts to
10,800 seconds. Claude Code is run with `DISABLE_AUTOUPDATER=1`; OpenCode 2 has
`autoupdate: false` in its staged configuration.

Pier writes `lock.json` in every job directory. Keep it with the results. Also
record the PA1 commit used for each primary run because the generator and
versioned harness configuration are part of this repository.

## Benchmark policy

| Model | Reasoning | Routing | Context policy |
| --- | --- | --- | --- |
| Claude Opus 5 | medium | Direct Anthropic; Codex uses the dedicated local Responses→Anthropic bridge | Native 1M |
| GPT-5.6 Luna | max | Existing LiteLLM gateway | 272,000 in every harness |
| DeepSeek V4 Flash 0731 | max | Existing LiteLLM gateway | Native 1,048,576 |
| Kimi K3 | max | Existing LiteLLM gateway | Native 1,048,576 |

The LiteLLM deployment is not changed by this repository. Vendor integration
documentation is used only for model/harness compatibility settings. The
benchmark does **not** switch DeepSeek or Kimi to their vendor endpoints.

Benchmark cost is normalized with `benchmark/pricing.yaml`, which contains the
official upstream model prices. Gateway invoices and Fireworks-specific prices
are not the ranking source of truth.

### Harness compatibility settings

For Claude Code, the FZR Pier adapter pins the main model, Opus/Sonnet/Haiku
aliases, the deprecated small/fast alias, and `CLAUDE_CODE_SUBAGENT_MODEL` to the
same benchmark model. The PA1 config additionally pins the Fable alias. This is
intentional: a benchmark cell evaluates one model, so Claude Code may decide to
spawn subagents, but it may not silently switch those calls to another model.

DeepSeek's current Claude Code guide uses a `[1m]` model name, max effort and
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=786432`. The DeepSeek benchmark cell keeps
those behavior settings but pins every Claude Code tier/subagent to the selected
Flash 0731 alias rather than reproducing DeepSeek's normal Pro-main/Flash-helper
product routing.

Kimi's current Claude Code guide maps the main model, Opus/Sonnet/Haiku/Fable
aliases and subagents to `kimi-k3[1m]`, uses max effort, and sets
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=1048576`. The benchmark mirrors that behavior
while keeping the base URL and credential on LiteLLM.

For Codex, DeepSeek publishes a complete `models.json` profile. The generator
copies the official V4 Flash entry (1,048,576 context, text input, parallel tool
calls, Multi-Agent V2, low/high/max reasoning and its documented Codex tool
profile) and changes only the slug/display name to the benchmark's explicit
`deepseek-v4-flash-0731` LiteLLM alias.

Kimi's official Codex guide configures K3 through CC Switch because Kimi's
upstream format is Chat Completions. We do **not** run CC Switch. The generator
copies the Kimi settings used by that documented integration — K3, 1,048,576
context, thinking enabled, reasoning-effort support with low/high/max and max as
the benchmark effort — while the existing LiteLLM gateway remains responsible
for the Codex Responses-facing route/protocol translation.

Luna is the one Codex model that deliberately stays on Codex's built-in
`openai` provider. Pier only changes `OPENAI_BASE_URL` to LiteLLM. This keeps the
first-party Codex model metadata and OpenAI-specific behavior, including the
bundled 272k context policy and the supported multi-agent/compaction path of the
frozen Codex release.

## Assumed checkout layout

Run all Pier commands from the PA1 repository root. The dataset path in the YAML
files is deliberately relative because Pier does not expand `~` in
`datasets.path`.

```text
~/PA1/
~/DeepSWE/
~/pier/          # clone of FZR-forks/pier
```

If you use different directory names, either preserve the sibling relationship
or adjust `datasets[].path` consistently in all four configs.

## Proxy assumptions

The existing LiteLLM deployment must already provide both its OpenAI-compatible
Responses surface and its Anthropic-compatible surface. No vendor endpoint is
configured by this benchmark.

The OpenAI-compatible surface must accept these model names:

```text
gpt-5.6-luna
deepseek-v4-flash-0731
deepseek-v4-flash
kimi-k3
```

`deepseek-v4-flash` is needed only by Pi, whose native DeepSeek catalog uses the
stable upstream model ID. It must resolve to the same V4 Flash 0731 checkpoint
as `deepseek-v4-flash-0731`.

The Anthropic-compatible surface used by Claude Code must accept these exact
aliases:

```text
gpt-5.6-luna[1m]
deepseek-v4-flash-0731[1m]
kimi-k3[1m]
```

The `[1m]` suffix is intentional Claude Code model metadata, not a different
checkpoint. The aliases must route to the same models as their unsuffixed
counterparts.

Claude Opus 5 is deliberately **not** expected on the shared LiteLLM gateway.
Pi and Claude Code call Anthropic directly. Codex cannot speak Anthropic Messages
itself, so its Opus cell uses the dedicated bridge in
`benchmark/bridges/codex-opus/`; that bridge has exactly one route and connects
directly to Anthropic.

For Kimi in Codex, LiteLLM must preserve the compatibility behavior that the
documented CC Switch path provides at its Chat Completions boundary: thinking
enabled, `reasoning_effort` forwarded, and reasoning content preserved across
tool-call turns. The PA1 repository does not change the proxy to achieve this;
the smoke test below is the acceptance check for the already-deployed route.

## Required environment variables

Copy the template first:

```bash
cd ~/PA1
cp benchmark/env.example benchmark/env.local
chmod 600 benchmark/env.local
```

Fill these values in `benchmark/env.local`:

| Variable | Required for | Meaning |
| --- | --- | --- |
| `LITELLM_API_KEY` | Pi, Claude Code, Codex, later OpenCode | Credential for the existing shared LiteLLM gateway |
| `LITELLM_OPENAI_BASE_URL` | Pi, Codex, later OpenCode | OpenAI-compatible gateway base URL, normally ending in `/v1` |
| `LITELLM_ANTHROPIC_BASE_URL` | Claude Code | Anthropic-compatible gateway base URL |
| `ANTHROPIC_API_KEY` | Opus in Pi/Claude Code and the Codex Opus bridge | Direct upstream Anthropic API key |
| `CODEX_OPUS_RESPONSES_API_KEY` | Codex Opus | A private key chosen for Codex→bridge authentication; it is not an Anthropic key |
| `CODEX_OPUS_RESPONSES_BASE_URL` | Codex Opus | Responses-compatible URL of the dedicated Opus bridge, ending in `/v1` |

A suitable bridge-local key can be generated with:

```bash
openssl rand -hex 32
```

Do not commit `benchmark/env.local`; it is ignored by Git.

## Step-by-step setup

### 0. Runner prerequisites

The benchmark runner needs Git, Docker with Compose, `uv`, Python 3.13, `curl`,
and `openssl`. The host also needs outbound HTTPS during preparation because the
single config generator fetches SHA-256-pinned Codex/DeepSeek/CC Switch reference
files. No API key is sent to those reference hosts.

The Pier task containers must be able to reach the configured LiteLLM endpoints
and the Codex Opus bridge. Pier derives its egress allowlist from the agent
configuration, but DNS/routing/firewall access to those hosts must already work
on the runner.

### 1. Pin DeepSWE

Clone it as a sibling of PA1 if it is not present yet, then freeze the exact
revision:

```bash
cd ~
git clone https://github.com/DataCurveAI/deep-swe.git DeepSWE
cd ~/DeepSWE
git fetch origin
git checkout 0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea
```

If the directory already exists, only `fetch` and `checkout` are needed.

### 2. Pin the FZR Pier fork

```bash
cd ~
git clone https://github.com/FZR-forks/pier.git pier
cd ~/pier
git fetch origin
git checkout 4ca06113149262330a8e3d0a63285bf2ddf0768b
```

Use Python 3.13 for this benchmark. Pier supports newer Python versions in its
metadata, but PyIceberg 0.11.1 currently publishes wheels through CPython 3.13;
3.13 avoids an unnecessary native build on the benchmark runner.

```bash
cd ~/pier
uv sync --python /usr/bin/python3.13
~/pier/.venv/bin/pier job start --help
```

The final command is only a CLI sanity check; do not launch a job from the Pier
repository. Actual benchmark commands below are run from `~/PA1` so relative
paths resolve correctly.

### 3. Fill `benchmark/env.local`

Use the environment-variable table above. Before continuing, verify the runner
itself can reach both LiteLLM surfaces and that the three `[1m]` Claude aliases
plus the four OpenAI-compatible aliases exist on the gateway. This repository
assumes those routes already exist and does not modify them.

### 4. Start the Codex Opus bridge

This is required only before a Codex job. Pi and Claude Code do not use it.

```bash
cd ~/PA1/benchmark/bridges/codex-opus
docker compose --env-file ../../env.local up -d --build
docker compose --env-file ../../env.local ps
curl -fsS http://127.0.0.1:4000/health/liveliness
```

Compose binds the bridge to localhost by default. Expose it through a stable
HTTPS endpoint reachable by Pier's Docker containers and put that URL in
`CODEX_OPUS_RESPONSES_BASE_URL`. The bridge's outbound request uses
`ANTHROPIC_API_KEY` and goes directly to Anthropic; it has no shared LiteLLM
route or credential.

### 5. Generate the deployment-specific files

Pier resolves `${VAR}` in an agent's `env` mapping, but it does not recursively
interpolate nested Pi config or external Codex TOML. One preparation script is
therefore kept for the pieces that genuinely need the local endpoint URLs:

```bash
cd ~/PA1
python3 benchmark/scripts/prepare_configs.py --env-file benchmark/env.local
```

It writes ignored files under `benchmark/generated/`:

```text
pi.yaml
codex-litellm.toml
codex-opus.toml
codex-thirdparty-models.json
```

The generator never changes LiteLLM. It only inserts your existing base URLs,
keeps Pi on its native provider/model IDs, and creates Codex model metadata from
the documented DeepSeek/Kimi integration profiles. Its vendor reference inputs
and the Codex fallback prompt are version/hash pinned so a later documentation or
helper change cannot silently alter a frozen run.

### 6. Smoke-test one DeepSWE task

Use the same job configs that will be used for the primary run. Pier's CLI
`--path`/`--include-task-name` override only the dataset selection, so each
command below runs the four configured model cells on one task.

From `~/PA1`:

```bash
PIER=~/pier/.venv/bin/pier
TASK=expr-try-catch-errors

$PIER job start -c benchmark/generated/pi.yaml \
  --env-file benchmark/env.local \
  --path ../DeepSWE/tasks --include-task-name "$TASK" \
  --job-name smoke-pi

$PIER job start -c benchmark/configs/claude-code.yaml \
  --env-file benchmark/env.local \
  --path ../DeepSWE/tasks --include-task-name "$TASK" \
  --job-name smoke-claude-code

$PIER job start -c benchmark/configs/codex.yaml \
  --env-file benchmark/env.local \
  --path ../DeepSWE/tasks --include-task-name "$TASK" \
  --job-name smoke-codex
```

Before spending the primary budget, inspect those smoke jobs and confirm:

1. the installed harness version equals the frozen version above;
2. Luna, DeepSeek and Kimi requests reach the existing LiteLLM gateway, while
   Opus reaches Anthropic directly (or the dedicated bridge for Codex);
3. Claude Code reports the expected `[1m]` model aliases and compaction windows;
4. Codex Luna remains the built-in OpenAI provider, DeepSeek has the vendor
   Codex capability profile, and Kimi uses the K3 compatibility profile;
5. Pi reports native `openai`, `deepseek`, `moonshotai` and `anthropic` model
   identities rather than custom proxy model definitions;
6. result/trajectory output contains token/cache, call, wall-clock and verifier
   data needed for analysis.

For Kimi/Codex specifically, use a smoke task that performs at least one tool
call and a subsequent model turn. A one-shot text reply is not enough to verify
that reasoning/tool state survives the Responses→provider translation.

### 7. Run the primary jobs

Run only one harness job at a time. From `~/PA1`:

```bash
PIER=~/pier/.venv/bin/pier

$PIER job start -c benchmark/generated/pi.yaml \
  --env-file benchmark/env.local

$PIER job start -c benchmark/configs/claude-code.yaml \
  --env-file benchmark/env.local

$PIER job start -c benchmark/configs/codex.yaml \
  --env-file benchmark/env.local
```

Each job uses the 10-task selection, one attempt, and concurrency 10 from its
YAML file. Results are written below `benchmark/runs/<job-name>/`. Keep the
entire job directory, especially `lock.json`, with the benchmark artifacts.

## OpenCode 2 status

The intended OpenCode 2 job is staged in `benchmark/configs/opencode-v2.yaml`,
frozen to `@opencode-ai/cli@0.0.0-beta-18684`. Do **not** run it yet. At the
frozen Pier revision, Pier's `opencode` adapter still installs the OpenCode 1
`opencode-ai` package and executes `opencode`; doing so would benchmark the
wrong harness. PA1 issue #9 remains the blocker. Once the V2 adapter is merged,
run the same one-task smoke pattern and then the primary job with this config.

## Upstream configuration references

These references are used for harness/model behavior only; their endpoint/API-key
instructions are deliberately ignored because PA1 routes DeepSeek and Kimi
through its existing LiteLLM gateway.

- DeepSeek Codex: https://api-docs.deepseek.com/quick_start/agent_integrations/codex/
- DeepSeek Claude Code: https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/
- Kimi Codex: https://platform.kimi.ai/docs/guide/codex-kimi
- Kimi Claude Code: https://platform.kimi.ai/docs/guide/claude-code-kimi
