# DeepSWE benchmark runner

This directory contains the **currently runnable** PA1 benchmark configuration.
The active matrix is:

- 3 harnesses: Pi, Claude Code, Codex
- 3 models: GPT-5.6 Luna, DeepSeek V4 Flash 0731, Kimi K3
- 10 selected DeepSWE v1.1 tasks
- 1 attempt per task

That is 30 trials per harness and **90 primary trials** total. Run one primary
harness job at a time. Each primary job uses `n_concurrent_trials: 10`.

Claude Opus 5 is intentionally deferred for now under
`benchmark/deferred/opus/`. OpenCode 2 is also deferred separately because its
Pier V2 adapter is not ready. Neither appears in the commands for the current
primary run.

## Frozen versions

These revisions were checked on **2026-08-30** immediately before the benchmark
freeze. Do not update them between primary jobs.

| Component | Frozen revision/version |
| --- | --- |
| FZR Pier fork | `4ca06113149262330a8e3d0a63285bf2ddf0768b` |
| DeepSWE | `0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea` |
| Codex CLI | `0.151.0` |
| Claude Code | `2.1.251` |
| Pi | `0.84.4` |

The pinned DeepSWE revision includes the upstream 10,800-second task timeouts.
Claude Code is run with `DISABLE_AUTOUPDATER=1`. Pier writes `lock.json` into
each job directory; keep that file with the results and record the PA1 commit
used for the run.

## Current benchmark policy

| Model | Reasoning | Routing | Context behavior |
| --- | --- | --- | --- |
| GPT-5.6 Luna | max | Existing LiteLLM gateway | 272,000-token benchmark window |
| DeepSeek V4 Flash 0731 | max | Existing LiteLLM gateway | 1,048,576 model context; Claude Code compacts at the vendor-documented 786,432 threshold |
| Kimi K3 | max | Existing LiteLLM gateway | 1,048,576 model context |

The smoke test is the one exception to the reasoning column: it runs Luna at
`low` purely to verify the environment cheaply before primary spending.

The repository does **not** change LiteLLM routing. DeepSeek/Kimi vendor
documentation is used only for harness/model compatibility settings, not for
vendor API URLs or credentials.

Cost normalization uses `benchmark/pricing.yaml` and the official upstream model
prices. Proxy/provider invoice pricing is not the ranking source of truth.

## Model isolation and harness-specific behavior

Every benchmark cell evaluates exactly one model.

For **Codex**, `restrict_model_catalog: true` narrows the available catalog to
the selected test model. Luna deliberately stays on Codex's built-in `openai`
provider while only `OPENAI_BASE_URL` is redirected to LiteLLM, preserving the
frozen Codex OpenAI-specific behavior and 272k metadata. DeepSeek and Kimi use
custom LiteLLM Responses routing plus generated vendor-derived model metadata.

For **Claude Code**, the FZR Pier adapter pins the selected model onto the main
model, Opus/Sonnet/Haiku aliases, the legacy small/fast alias, and
`CLAUDE_CODE_SUBAGENT_MODEL`. The PA1 YAML also maps the Fable alias. Claude Code
can still decide to use subagents, but every LLM call remains on the benchmark
model. The `[1m]` aliases and compaction settings follow the documented
DeepSeek/Kimi integration behavior while keeping LiteLLM as the transport.

For **Pi**, the native Pi 0.84.4 model IDs are kept so Pi retains its bundled
capability/compatibility/pricing metadata:

```text
openai/gpt-5.6-luna
deepseek/deepseek-v4-flash
moonshotai/kimi-k3
```

Pi has no native subagents in this setup. Pier launches the selected model
explicitly with `--provider`/`--model` in non-interactive print mode.

## Assumed checkout layout

Run Pier commands from the PA1 repository root:

```text
~/PA1/
~/DeepSWE/
~/pier/          # FZR-forks/pier
```

The dataset path is relative (`../DeepSWE/tasks`) because Pier does not expand
`~` in `datasets.path`. If you use another layout, update that path consistently
in the configs.

## Existing LiteLLM assumptions

The existing gateway must expose both an OpenAI-compatible surface and an
Anthropic-compatible surface. The benchmark does not create or modify these
routes.

OpenAI-compatible aliases:

```text
gpt-5.6-luna
deepseek-v4-flash-0731
deepseek-v4-flash
kimi-k3
```

`deepseek-v4-flash` is required by Pi because that is Pi's native stable
DeepSeek model ID; it must resolve to the same V4 Flash 0731 checkpoint as
`deepseek-v4-flash-0731`.

Claude Code's Anthropic-compatible surface must accept:

```text
gpt-5.6-luna[1m]
deepseek-v4-flash-0731[1m]
kimi-k3[1m]
```

The `[1m]` suffix is Claude Code model metadata, not a different checkpoint.
Those aliases must resolve to the same models as their unsuffixed counterparts.

For Kimi/Codex, the existing route must preserve the Kimi compatibility behavior
used by the documented CC Switch path: thinking enabled, `reasoning_effort`
forwarded, and reasoning content preserved across tool-call turns. PA1 does not
reimplement that translation.

## Required environment variables for the current run

Create the local env file:

```bash
cd ~/PA1
cp benchmark/env.example benchmark/env.local
chmod 600 benchmark/env.local
```

Fill exactly these values for the current Luna/DeepSeek/Kimi run:

| Variable | Used by | Meaning |
| --- | --- | --- |
| `LITELLM_API_KEY` | all three harnesses | Credential for the existing LiteLLM gateway |
| `LITELLM_OPENAI_BASE_URL` | Pi, Codex | OpenAI-compatible base URL, normally ending in `/v1` |
| `LITELLM_ANTHROPIC_BASE_URL` | Claude Code | Anthropic-compatible base URL |

No Anthropic key or Opus bridge variable is required for the current run.
`benchmark/env.local` is ignored by Git.

## Step-by-step setup

### 0. Install runner prerequisites

The runner needs Git, Docker, `uv`, Python 3.13, and `curl`. Preparation also
needs outbound HTTPS because the single generator fetches SHA-256-pinned Codex,
DeepSeek, and CC Switch reference files. No model API credentials are sent to
those reference hosts.

The task containers must be able to resolve and reach both configured LiteLLM
hosts through the runner's Docker networking/firewall setup.

### 1. Pin DeepSWE

```bash
cd ~
git clone https://github.com/DataCurveAI/deep-swe.git DeepSWE   # skip if present
cd ~/DeepSWE
git fetch origin
git checkout 0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea
```

### 2. Pin Pier

```bash
cd ~
git clone https://github.com/FZR-forks/pier.git pier   # skip if present
cd ~/pier
git fetch origin
git checkout 4ca06113149262330a8e3d0a63285bf2ddf0768b
uv sync --python /usr/bin/python3.13
~/pier/.venv/bin/pier job start --help
```

Run actual jobs from `~/PA1`, not from the Pier repository.

### 3. Configure LiteLLM credentials/endpoints

```bash
cd ~/PA1
cp benchmark/env.example benchmark/env.local
chmod 600 benchmark/env.local
$EDITOR benchmark/env.local
```

Before spending anything, verify the runner can reach both configured gateway
surfaces and that the aliases listed above are deployed.

### 4. Run the Luna end-to-end smoke test

The dedicated smoke job uses only GPT-5.6 Luna at **low** reasoning on the
simple pilot task `anko-default-function-arguments`.

It contains exactly three trials:

```text
Pi + Luna low
Claude Code + Luna low
Codex + Luna low
```

It exercises the DeepSWE task environment, Docker execution, harness
installation, both LiteLLM protocol surfaces, Luna's 272k policy, Codex's
restricted catalog, verifier execution, and Pier result/trajectory capture.
It requires no generated files and no Opus/Anthropic configuration.

Run from `~/PA1`:

```bash
PIER=~/pier/.venv/bin/pier

$PIER job start -c benchmark/configs/smoke-test.yaml \
  --env-file benchmark/env.local
```

Do not proceed to the primary run until all three trials finish and the verifier
runs. Inspect `benchmark/runs/smoke-luna/` and confirm the recorded harness/model
pairs are Pi/Luna, Claude Code/Luna, and Codex/Luna.

### 5. Generate deployment-specific primary configs

The generator is needed because Pier resolves `${VAR}` in agent `env` mappings
but does not recursively interpolate nested Pi config or external Codex TOML.

```bash
cd ~/PA1
python3 benchmark/scripts/prepare_configs.py --env-file benchmark/env.local
```

For the current run it writes only:

```text
benchmark/generated/pi.yaml
benchmark/generated/codex-litellm.toml
benchmark/generated/codex-thirdparty-models.json
```

It does not require or generate any Opus bridge configuration unless explicitly
invoked later with `--include-opus`.

### 6. Run the three primary harness jobs

Run **one harness job at a time**:

```bash
cd ~/PA1
PIER=~/pier/.venv/bin/pier

$PIER job start -c benchmark/generated/pi.yaml \
  --env-file benchmark/env.local

$PIER job start -c benchmark/configs/claude-code.yaml \
  --env-file benchmark/env.local

$PIER job start -c benchmark/configs/codex.yaml \
  --env-file benchmark/env.local
```

Each job is 3 models × 10 tasks = 30 trials, with concurrency 10. Keep the whole
`benchmark/runs/<job-name>/` directory, especially `lock.json`.

## Deferred Opus 5

Opus is **not part of the current run**. Its three harness-specific configs and
later setup instructions live under `benchmark/deferred/opus/`. They remain
separate per harness so Opus can also be run one harness at a time when enabled.
The direct Anthropic key and Codex Opus bridge are not needed until then.

## Deferred OpenCode 2

OpenCode 2 remains outside the runnable matrix. Its staged config is
`benchmark/deferred/opencode-v2.yaml` until PA1 issue #9 / Pier V2 support is
ready.

## Upstream configuration references

These references are used for behavior/compatibility settings only. Their vendor
endpoint/API-key instructions are deliberately ignored for the current proxy
routing.

- DeepSeek Codex: https://api-docs.deepseek.com/quick_start/agent_integrations/codex/
- DeepSeek Claude Code: https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/
- Kimi Codex: https://platform.kimi.ai/docs/guide/codex-kimi
- Kimi Claude Code: https://platform.kimi.ai/docs/guide/claude-code-kimi
