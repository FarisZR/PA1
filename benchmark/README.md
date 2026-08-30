# DeepSWE benchmark runner

This directory contains the runnable PA1 benchmark configuration for the current
batch. Jobs are split **per model**, not per harness, so expensive/high-priority
models can be completed independently.

## Current run order

Run these jobs in this order:

1. `smoke-test.yaml` — Luna low on one pilot task, all three runnable harnesses
2. `kimi-k3.yaml` — **highest-priority primary job**
3. `deepseek-v4-flash.yaml`
4. `luna.yaml`

Claude Opus 5 and OpenCode 2 are deferred and are not part of the commands above.

Each primary model job contains:

- Pi
- Claude Code
- Codex
- 10 selected DeepSWE tasks
- 1 attempt per task
- `n_concurrent_trials: 10`

That is **30 trials per model job** and 90 trials in the current primary batch.
Run only one model job at a time.

## Frozen versions

Do not update these revisions between jobs in the primary batch.

| Component | Frozen revision/version |
| --- | --- |
| FZR Pier fork | `4ca06113149262330a8e3d0a63285bf2ddf0768b` |
| DeepSWE | `0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea` |
| Codex CLI | `0.151.0` |
| Claude Code | `2.1.251` |
| Pi | `0.84.4` |
| Codex model catalog | `rust-v0.151.0` vendored at `benchmark/references/codex-rust-v0.151.0-models.json` |

The DeepSWE revision includes the upstream 10,800-second task timeout. Claude
Code runs with its updater disabled. Pier writes `lock.json` into each job
result directory; keep it with the benchmark results and record the PA1 commit
used for the run.

## Current model policy

| Model | Reasoning | Routing | Context behavior |
| --- | --- | --- | --- |
| Kimi K3 | max | Existing LiteLLM gateway | Native 1,048,576 context |
| DeepSeek V4 Flash 0731 | max | Existing LiteLLM gateway | Native 1,048,576 context; no Claude Code auto-compaction override |
| GPT-5.6 Luna | max | Existing LiteLLM gateway | 272,000-token benchmark window |

The smoke test is intentionally different: it runs Luna at **low** reasoning to
validate the environment cheaply before primary spending.

The repository does not change the LiteLLM deployment. DeepSeek/Kimi vendor
documentation is used only for harness compatibility/model metadata, not for
vendor API endpoints or credentials.

Cost normalization uses `benchmark/pricing.yaml` and official upstream model
prices, not proxy/provider invoice pricing.

## Model isolation

Every job contains one benchmark model and three harness implementations of that
same model.

### Codex

`restrict_model_catalog: true` restricts each Codex trial to its selected test
model. Luna remains Codex's built-in `openai/gpt-5.6-luna` model and only its
base URL is redirected to LiteLLM, preserving Codex's first-party Luna behavior.

Every non-GPT Codex model uses the exact `gpt-5.6-sol` profile from Codex
`rust-v0.151.0` as its compatibility base. The complete upstream `models.json`
is frozen in this repository at
`benchmark/references/codex-rust-v0.151.0-models.json` with SHA-256
`eb0d7b9a5dcaf103895c5f8a14c16b269df46e039b375a55ba97f6238542d2ed`.
Generation reads only this local file.

For DeepSeek, Kimi, and deferred Opus, the Sol profile is preserved except for:

- model identity/display metadata;
- model-specific context, modality, and supported reasoning metadata;
- `use_responses_lite: false` because these third-party LiteLLM routes use the
  normal Responses path.

This preserves the current-release Sol behavior including
`multi_agent_version: "v2"`, `tool_mode: "code_mode_only"`, parallel tool calls,
the Sol system/profile instructions, and `auto_compact_token_limit: null`.
DeepSeek therefore keeps the Codex compaction field; only its Claude Code
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` override is removed.

### Claude Code

The FZR Pier adapter pins the selected model onto Claude Code's main model,
Opus/Sonnet/Haiku aliases, legacy small/fast alias, and
`CLAUDE_CODE_SUBAGENT_MODEL`. The PA1 configs additionally map the Fable alias.
Therefore Claude Code may use its normal internal agent behavior, but every LLM
call in a trial remains on the model being benchmarked.

The third-party `[1m]` aliases are retained for DeepSeek/Kimi compatibility.
Luna also uses `[1m]`, then explicitly lowers its compaction window to 272,000.
Kimi keeps its documented 1,048,576 compaction setting. DeepSeek has **no**
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` override.

### Pi

Pi 0.84.4 uses its native model identities so its bundled compatibility and
pricing metadata remain intact:

```text
moonshotai/kimi-k3
deepseek/deepseek-v4-flash
openai/gpt-5.6-luna
```

Pi has no native subagent system in this benchmark setup. Pier launches the
selected provider/model explicitly in non-interactive print mode.

## Checkout layout

Run Pier commands from the PA1 repository root. The expected sibling layout is:

```text
~/PA1/
~/DeepSWE/
~/pier/
```

The job configs use `../DeepSWE/tasks`. If you use another layout, change the
dataset path consistently in the configs.

## LiteLLM assumptions

The existing gateway must expose both an OpenAI-compatible surface and an
Anthropic-compatible surface.

### OpenAI-compatible aliases

```text
gpt-5.6-luna
deepseek-v4-flash-0731
deepseek-v4-flash
kimi-k3
```

`deepseek-v4-flash` is needed by Pi because that is Pi's native stable DeepSeek
model ID. It must route to the same V4 Flash 0731 checkpoint as
`deepseek-v4-flash-0731`.

### Claude Code aliases

```text
gpt-5.6-luna[1m]
deepseek-v4-flash-0731[1m]
kimi-k3[1m]
```

The `[1m]` suffix is Claude Code compatibility metadata, not a different model.
Each alias must resolve to the same checkpoint as its unsuffixed counterpart.

For Kimi/Codex, the existing LiteLLM route must preserve the compatibility
behavior supplied by Kimi's documented CC Switch path: thinking enabled,
`reasoning_effort` forwarded, and reasoning content preserved across tool-call
turns. PA1 does not reimplement that translation.

## Current environment variables

Create the local environment file:

```bash
cd ~/PA1
cp benchmark/env.example benchmark/env.local
chmod 600 benchmark/env.local
```

Fill these three values:

| Variable | Used by | Meaning |
| --- | --- | --- |
| `LITELLM_API_KEY` | all three harnesses | Credential for the existing LiteLLM gateway |
| `LITELLM_OPENAI_BASE_URL` | Pi, Codex | OpenAI-compatible base URL, normally ending in `/v1` |
| `LITELLM_ANTHROPIC_BASE_URL` | Claude Code | Anthropic-compatible base URL |

No Anthropic/Opus credential is required for the current batch.
`benchmark/env.local` is ignored by Git.

## Step-by-step setup and run

### 0. Install runner prerequisites

The runner needs Git, Docker, `uv`, Python 3.13, and `curl`. The config generator
has no network dependency: its Codex model source is vendored in this repository.
Network access is needed only for normal repository/package setup such as Git
clone/fetch and harness installation.

The Pier task containers must be able to resolve and reach the configured
LiteLLM hosts through the runner's Docker/network/firewall setup.

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

Run the actual jobs from `~/PA1`, not from the Pier checkout.

### 3. Configure LiteLLM

```bash
cd ~/PA1
cp benchmark/env.example benchmark/env.local
chmod 600 benchmark/env.local
$EDITOR benchmark/env.local
```

Before running anything, verify the runner itself can resolve/reach both gateway
surfaces and that the aliases listed above exist.

### 4. Run the cheap Luna smoke test

The smoke job uses the simple pilot task `anko-default-function-arguments` and
contains exactly three trials:

```text
Pi + GPT-5.6 Luna low
Claude Code + GPT-5.6 Luna low
Codex + GPT-5.6 Luna low
```

Run:

```bash
cd ~/PA1
PIER=~/pier/.venv/bin/pier

$PIER job start -c benchmark/configs/smoke-test.yaml \
  --env-file benchmark/env.local
```

The smoke job requires no generated config. Do not start primary spending until
all three trials finish, the verifier runs, and
`benchmark/runs/smoke-luna/` contains the expected result/trajectory data.

### 5. Generate the primary model configs

Pier resolves environment variables in agent `env` maps but does not recursively
interpolate nested Pi provider config or external Codex TOML. Generate the
runnable model jobs once after filling `benchmark/env.local`:

```bash
cd ~/PA1
python3 benchmark/scripts/prepare_configs.py --env-file benchmark/env.local
```

This writes ignored deployment-specific files:

```text
benchmark/generated/kimi-k3.yaml
benchmark/generated/deepseek-v4-flash.yaml
benchmark/generated/luna.yaml
benchmark/generated/codex-litellm.toml
benchmark/generated/codex-thirdparty-models.json
```

The three generated YAML files correspond directly to the three source files in
`benchmark/configs/`; generation only injects deployment-specific endpoint data
needed by Pi. The Codex catalog contains only DeepSeek and Kimi because Luna uses
Codex's bundled first-party model entry. DeepSeek and Kimi are cloned from the
vendored GPT-5.6 Sol entry; no upstream file is fetched while generating these
artifacts.

### 6. Run Kimi K3 first

Kimi is the highest-priority primary model and should finish before Luna or
DeepSeek.

```bash
cd ~/PA1
PIER=~/pier/.venv/bin/pier

$PIER job start -c benchmark/generated/kimi-k3.yaml \
  --env-file benchmark/env.local
```

This runs Pi, Claude Code, and Codex across all 10 selected tasks: 30 trials,
with at most 10 concurrent trials.

### 7. Run DeepSeek V4 Flash 0731

```bash
$PIER job start -c benchmark/generated/deepseek-v4-flash.yaml \
  --env-file benchmark/env.local
```

### 8. Run GPT-5.6 Luna

```bash
$PIER job start -c benchmark/generated/luna.yaml \
  --env-file benchmark/env.local
```

Keep each complete `benchmark/runs/<job-name>/` directory, especially its
`lock.json`.

## Deferred Opus 5

Opus is not part of the current batch. Its model job is
`benchmark/deferred/opus.yaml`, containing Pi + Claude Code + Codex for the same
10 tasks.

When Opus is enabled later:

1. append the variables from `benchmark/deferred/opus-env.example` to
   `benchmark/env.local`;
2. start/expose the dedicated Codex Responses→Anthropic bridge under
   `benchmark/bridges/codex-opus/`;
3. generate the additional Codex Opus artifacts with:

```bash
python3 benchmark/scripts/prepare_configs.py \
  --env-file benchmark/env.local \
  --include-opus
```

4. run the single Opus model job:

```bash
$PIER job start -c benchmark/deferred/opus.yaml \
  --env-file benchmark/env.local
```

The generated Opus catalog is separate from the current DeepSeek/Kimi catalog.

## Deferred OpenCode 2

OpenCode 2 remains blocked on PA1 issue #9 / Pier V2 support. Its staged configs
are under `benchmark/deferred/opencode-v2/` and are also split per model. They
are not referenced by the current smoke or primary commands.

## Upstream compatibility references

Codex third-party model behavior is based on the frozen OpenAI `gpt-5.6-sol`
profile, not the vendor Codex setup scripts. The exact upstream source is:

- Codex 0.151.0 model catalog: https://raw.githubusercontent.com/openai/codex/refs/tags/rust-v0.151.0/codex-rs/models-manager/models.json

The vendor guides below are used only for Claude Code/model transport
compatibility. Their endpoint/API-key instructions are ignored because the
active batch routes through the existing LiteLLM gateway.

- DeepSeek Claude Code: https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/
- Kimi Claude Code: https://platform.kimi.ai/docs/guide/claude-code-kimi
