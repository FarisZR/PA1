# DeepSWE benchmark runner

This directory contains the PA1 benchmark templates, frozen compatibility data,
and runbook for the current batch. Primary jobs are split **per model**, not per
harness, so expensive/high-priority models can be completed independently.

## Current run order

Run these steps in this order:

1. `benchmark/configs/smoke-test.yaml` — Luna low on one pilot task, all three runnable harnesses
2. generate the deployment-specific primary files with `prepare_configs.py`
3. start the Codex compatibility bridge under `benchmark/bridges/codex-cliproxy/`
4. one-task Kimi gateway acceptance run using `benchmark/generated/kimi-k3.yaml`
5. `benchmark/generated/kimi-k3.yaml` — **highest-priority primary job**
6. one-task DeepSeek gateway acceptance run using `benchmark/generated/deepseek-v4-flash.yaml`
7. `benchmark/generated/deepseek-v4-flash.yaml`
8. `benchmark/generated/luna.yaml`

The primary files under `benchmark/configs/` are source templates. Do not launch
the Kimi or DeepSeek templates directly: their Pi endpoint placeholder is
resolved only in `benchmark/generated/`. Use generated files for all three
primary jobs for one consistent workflow.

Claude Opus 5 and OpenCode 2 are deferred and are not part of the commands above.

Each primary model job contains:

- Pi
- Claude Code
- Codex
- 10 selected DeepSWE tasks
- 1 attempt per task
- 1 automatic retry, for transport/gateway faults only
- `n_concurrent_trials: 14`

That is **30 planned trials per model job** and 90 planned trials in the current
primary batch. Successful trials run once. A trial that fails with a
transport/gateway fault is discarded and run again once; if the retry also fails,
the second failure is final.

Agent timeouts and verifier/reward faults are **not** retried
(`exclude_exceptions` keeps Pier's default non-retryable set). A trial that
exhausts the 10,800-second budget is a genuine efficiency result on this task
set, not an infrastructure fault, and re-running it would both double the spend
on the most expensive tasks and erase that result. Verifier and reward-file
faults are grading faults; re-running a whole trial does not fix them.

Discarded attempts still consume budget. Record their token usage and report
cost inclusive of them, otherwise the harness that fails more often has its
wasted spend deleted from the cost metric. Run only one model job at a time.

## Frozen versions

Re-verified on **2026-08-31** immediately before the benchmark launch. Do not
update these revisions between jobs in the primary batch.

| Component | Frozen revision/version |
| --- | --- |
| FZR Pier fork | `4349d63938804c25fea7166ede9f65df545331a2` |
| DeepSWE | `0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea` |
| Codex CLI | `0.151.0` |
| Claude Code | `2.1.251` |
| Pi | `0.84.4` |
| Codex model catalog | `rust-v0.151.0` vendored at `benchmark/references/codex-rust-v0.151.0-models.json` |
| Codex compatibility bridge | CLIProxyAPI `v7.2.146`, digest `sha256:238691ac26ce55e4d1c5219d72e3ad74838f81eda26359912eeb415e2820d163` |

The DeepSWE revision includes the upstream 10,800-second task timeout. Claude
Code runs with its updater disabled. Pier writes `lock.json` into each job
result directory; keep it with the benchmark results and record the PA1 commit
used for the run.

## Current model policy

| Model | Reasoning | Routing | Context behavior |
| --- | --- | --- | --- |
| Kimi K3 | max | Existing LiteLLM gateway | Native 1,048,576 context |
| DeepSeek V4 Flash 0731 | max | Existing LiteLLM gateway | Native 1,048,576 context; Claude Code compacts at 1,048,576 |
| GPT-5.6 Luna | max | Existing LiteLLM gateway | 272,000-token benchmark window |

The smoke test is intentionally different: it runs Luna at **low** reasoning to
validate the environment cheaply before primary spending.

The repository does not change the LiteLLM deployment. DeepSeek/Kimi vendor
documentation is used for Claude Code compatibility and model-specific facts
such as context/modality, not for vendor API endpoints or credentials. Codex
behavior comes from the frozen GPT-5.6 Sol profile described below.

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
- `multi_agent_version: "v1"` because non-GPT models use Codex Multi-Agent V1;
- `use_responses_lite: false` because these third-party routes use the normal
  Responses path.

This preserves the rest of the current-release Sol behavior, including
`tool_mode: "code_mode_only"`, parallel tool calls,
the Sol system/profile instructions, and `auto_compact_token_limit: null`.
DeepSeek keeps the Codex compaction field and explicitly sets Claude Code
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=1048576`.

#### Codex compatibility bridge

Codex's Responses requests are **not** sent to the LiteLLM gateway for the
third-party models. Codex 0.151.0 unconditionally attaches `client_metadata`,
and the gateway forwards the Responses `reasoning` object into `reasoning_effort`
as an object, so the Fireworks-backed routes reject every request (PA1 issue
[#31](https://github.com/FarisZR/PA1/issues/31)). DeepSeek, Kimi, and the
deferred Opus therefore route through a pinned CLIProxyAPI instance that
translates Responses to Chat Completions in front of the same gateway:

```text
Codex -> Responses -> CLIProxyAPI -> Chat Completions -> LiteLLM -> Fireworks
```

Luna stays on the direct native Responses path, because it is OpenAI-backed and
works unchanged.

This is a transport fix, not a harness change: the Codex model catalog, prompt,
reasoning effort, and reasoning-summary settings are all unchanged, and the
bridge is configured so it cannot retry, cool down a credential, or fall back to
another model. One consequence is load-bearing and worth restating here: the
bridge snaps an unknown `reasoning.effort` down to the nearest level it knows,
so `prepare_configs.py` derives its declared reasoning levels from the same
Codex catalog entries, and PA1's `max` requests reach Fireworks as `max`.

Setup, the pinned digest, the acceptance tests, and the known caveats are in
[`benchmark/bridges/codex-cliproxy/README.md`](bridges/codex-cliproxy/README.md).

### Claude Code

The FZR Pier adapter pins the selected model onto Claude Code's main model,
Opus/Sonnet/Haiku aliases, legacy small/fast alias, and
`CLAUDE_CODE_SUBAGENT_MODEL`. The PA1 configs additionally map the Fable alias.
Therefore Claude Code may use its normal internal agent behavior, but every LLM
call in a trial remains on the model being benchmarked.

The third-party `[1m]` aliases are retained for DeepSeek/Kimi compatibility.
Luna also uses `[1m]`, then explicitly lowers its compaction window to 272,000.
Kimi and DeepSeek both explicitly use a 1,048,576 Claude Code auto-compaction
window, stated as each model's literal native context so the value matches
`pricing.yaml` and the generated Codex catalog. Claude Code caps the window at
the one it assumes for the model ID, which is 1,000,000 for an unrecognized
`[1m]` alias, so the effective threshold is 1,000,000 and the declared value is
the model's context rather than the reachable ceiling.

Every Claude Code cell also sets two timeout variables that only matter because
the requests are routed through a gateway rather than directly to Anthropic:

| Variable | Value | Why |
| --- | --- | --- |
| `API_FORCE_IDLE_TIMEOUT` | `0` | Turns off the 5-minute body idle timeout, which is active by default on any provider other than the direct Anthropic API. At `max` reasoning a silent thinking pause can exceed it. |
| `CLAUDE_STREAM_IDLE_TIMEOUT_MS` | `1800000` | Raises both the event- and byte-level streaming idle watchdogs to 30 minutes, the byte-level cap. Claude Code counts gateway-relayed bytes including SSE pings and aborts a silent stream; a gateway that strips or buffers pings during a long thinking pause would otherwise abort the trial. |

These are documented in [Claude Code environment variables](https://code.claude.com/docs/en/env-vars)
and [Model configuration](https://code.claude.com/docs/en/model-config#correct-the-window-for-a-gateway-or-custom-model-id).
The deferred Opus job sets none of them: `claude-opus-5` is a model ID Claude
Code recognizes, and it connects to the Anthropic API directly.

#### Claude Code output-token policy

PA1 deliberately leaves `CLAUDE_CODE_MAX_OUTPUT_TOKENS` unset. Claude Code
documents a 32,000-token default for model IDs it does not recognize, including
gateway-specific names. The earlier benchmark configuration overrode this with
64,000 tokens. That override was removed because it was a PA1-specific policy,
not a requirement of the models or their official Claude Code integrations.

The model-level limits are substantially larger: [Kimi K3's API defaults
`max_completion_tokens` to 131,072](https://www.kimi.ai/help/kimi-api/api-troubleshooting),
while [DeepSeek V4 Flash 0731 documents a 384K maximum output](https://api-docs.deepseek.com/quick_start/pricing/).
Their official Claude Code setup instructions do not set
`CLAUDE_CODE_MAX_OUTPUT_TOKENS`; they configure model routing, reasoning and
context behavior and leave Claude Code's output policy intact. See
[Claude Code environment variables](https://code.claude.com/docs/en/env-vars),
[Kimi's Claude Code integration](https://www.kimi.com/code/docs/en/third-party-tools/claude-code.html),
and [DeepSeek's Claude Code integration](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/).

An acceptance run also showed that Claude Code 2.1.251 treats an exhausted
output budget as an intermediate generation boundary rather than necessarily a
terminal agent failure. A DeepSeek V4 Flash turn reached 63,999 completion
tokens with `stop_reason: max_tokens`, emitted neither visible text nor a tool
call, and Claude Code immediately issued a follow-up request in the same
session. The model continued the task, resumed tool execution, and the run later
ended normally with `stop_reason: end_turn`. The smaller native default can
therefore add another model request and its associated input/cache cost, but
that behavior belongs to the Claude Code harness being measured.

The other harnesses are intentionally unchanged. Pi uses its model metadata and
request-specific context clamping (for example 131,072 for Kimi K3 and 384,000
for DeepSeek V4 Flash). The PA1 Codex configuration does not set an output-token
limit; its generated third-party model catalog only carries identity, context,
modality and reasoning metadata on top of the frozen Codex profile. This keeps
completion-limit policy harness-native instead of normalizing it in PA1.

The config generator rejects any future reintroduction of
`CLAUDE_CODE_MAX_OUTPUT_TOKENS` under `benchmark/configs/` so generated trial
files cannot silently restore the removed override.

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
deepseek-v4-flash
kimi-k3
```

`deepseek-v4-flash` is the stable DeepSeek model ID used by all three harnesses.
The gateway maps it to the V4 Flash 0731 checkpoint.

### Claude Code aliases

```text
gpt-5.6-luna[1m]
deepseek-v4-flash[1m]
kimi-k3[1m]
```

The `[1m]` suffix is Claude Code compatibility metadata, not a different model.
Each alias must resolve to the same checkpoint as its unsuffixed counterpart.

The gateway needs to serve Kimi and DeepSeek correctly on **Chat Completions**
for Codex, not on Responses: thinking enabled, a string `reasoning_effort`
forwarded, and `reasoning_content` accepted on historical assistant messages.
The Responses/Chat translation itself is done by the pinned Codex compatibility
bridge, which is why PA1 no longer depends on the gateway's own Responses
translation for these models.

## Current environment variables

Create the local environment file:

```bash
cd ~/PA1
cp benchmark/env.example benchmark/env.local
chmod 600 benchmark/env.local
```

Fill these values:

| Variable | Used by | Meaning |
| --- | --- | --- |
| `LITELLM_API_KEY` | all three harnesses | Credential for the existing LiteLLM gateway. Codex reaches it indirectly, through the bridge. |
| `LITELLM_OPENAI_BASE_URL` | Pi, bridge | OpenAI-compatible base URL, normally ending in `/v1` |
| `LITELLM_ANTHROPIC_BASE_URL` | Claude Code | Anthropic-compatible base URL |
| `PIER_EXTRA_CA_CERTS` | all three harnesses | Absolute path to the tracked `benchmark/puki-root-ca-2022.pem` bundle containing both public PUKI Root CA 2022 RSA and EC certificates. Required on this runner: the gateway serves an internal IONOS PUKI certificate that containers do not trust by default, and without it every trial fails its first model call. |
| `CODEX_CLIPROXY_BASE_URL` | Codex | `/v1` endpoint of the compatibility bridge as seen from a trial container. Must be on port 80 or 443 and must not be a dotless bare hostname; `prepare_configs.py` rejects both. |
| `CODEX_CLIPROXY_API_KEY` | Codex | Token Codex presents to the bridge. Chosen locally; not a gateway or vendor credential. |
| `CODEX_CLIPROXY_BIND`, `CODEX_CLIPROXY_PORT` | bridge | Host address and port the bridge publishes on. Must match `CODEX_CLIPROXY_BASE_URL`. |
| `CODEX_CLIPROXY_REQUEST_LOG` | bridge | Log full request bodies for the live acceptance check. Keep `false` for benchmark jobs; it records every prompt verbatim. |

No Anthropic/Opus credential is required for the current batch.
`benchmark/env.local` is ignored by Git.

## Step-by-step setup and run

### 0. Install runner prerequisites

The runner needs Git, Docker, `uv`, Python 3.13, and `curl`. The config generator
has no network dependency: its Codex model source is vendored in this repository.
Network access is needed only for normal repository/package setup such as Git
clone/fetch and harness installation.

The Pier task containers must be able to resolve and reach the configured
LiteLLM hosts through the runner's Docker/network/firewall setup, and — for
Codex — the Codex compatibility bridge on the runner host.

Pier gives each trial a filtered egress proxy that permits only plain HTTP to
port 80 and HTTPS to port 443, on hostnames it derives from the configured base
URLs. That is why the bridge is published on port 80 of the Docker bridge
gateway rather than on CLIProxyAPI's own port.

### 1. Pin DeepSWE

```bash
cd ~
git clone https://github.com/datacurve-ai/deep-swe.git DeepSWE   # skip if present
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
git checkout 4349d63938804c25fea7166ede9f65df545331a2
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
benchmark/generated/codex-cliproxy.toml        # Codex -> compatibility bridge
benchmark/generated/cliproxy-config.yaml       # bridge deployment config (0600)
benchmark/generated/codex-litellm.toml         # direct route; control only
benchmark/generated/codex-thirdparty-models.json
```

The three generated YAML files correspond directly to the three source templates
in `benchmark/configs/`. Generation resolves the nested Pi endpoint placeholder,
writes the Codex provider TOML and the bridge's deployment config, and builds
the restricted third-party Codex catalog. It verifies the expected placeholder
count and the SHA-256 of the vendored Codex catalog before writing the run set,
so a stale template/reference fails generation instead of leaving a partially
updated configuration.

`codex-litellm.toml` is the direct corporate-gateway Codex route. No job uses
it; it is kept so the issue #31 failure can be reproduced as a control.

`cliproxy-config.yaml` contains live credentials and is written mode 0600. It is
generated from the tracked templates in `benchmark/bridges/codex-cliproxy/`
rather than mounted directly, because CLIProxyAPI does no environment
interpolation and needs the credentials as literals.

The Codex catalog contains only DeepSeek and Kimi because Luna uses Codex's
bundled first-party model entry. DeepSeek and Kimi are cloned from the vendored
GPT-5.6 Sol entry; no upstream file is fetched while generating these artifacts.

### 5b. Start the Codex compatibility bridge

Required before any Kimi or DeepSeek job, and before the acceptance runs below.
Luna does not need it.

```bash
cd ~/PA1/benchmark/bridges/codex-cliproxy
docker compose --env-file ../../env.local up -d
docker compose ps          # expect: healthy
```

Verify the translation contract and the live gateway before spending:

```bash
cd ~/PA1
python3 benchmark/bridges/codex-cliproxy/tests/test_codex_translation.py
python3 benchmark/bridges/codex-cliproxy/tests/test_generated_config.py
python3 benchmark/bridges/codex-cliproxy/tests/check_live_gateway.py \
  --env-file benchmark/env.local --effort max
```

Re-run `docker compose ... up -d --force-recreate` after any regeneration that
changes the bridge's model set. See
[`bridges/codex-cliproxy/README.md`](bridges/codex-cliproxy/README.md).

### 6. Run the Kimi gateway acceptance check

The Luna smoke test proves the task environment and both LiteLLM surfaces, but it
does not exercise the third-party Codex profile. Codex 0.151.0 sends normal
Responses HTTP requests for Kimi/DeepSeek and, under the frozen Sol profile, may
include Codex tool definitions such as `custom` exec and `web_search`. The
bridge tests in step 5b cover the protocol contract, but not Codex itself
driving a full task, so this run stays.

Before starting Kimi's 30 primary trials, run the same generated Kimi job on the
cheap pilot task. This is an acceptance run, not benchmark data:

```bash
cd ~/PA1
PIER=~/pier/.venv/bin/pier

$PIER job start -c benchmark/generated/kimi-k3.yaml \
  --env-file benchmark/env.local \
  --path ../DeepSWE/tasks \
  --include-task-name anko-default-function-arguments \
  --job-name acceptance-kimi
```

Confirm all three harness trials complete and, for Codex specifically, that a
tool call followed by another model turn succeeds. If the Codex trial fails on a
tool schema, reasoning-state, or Responses translation error, stop before the
full Kimi batch: check the bridge is healthy and, with
`CODEX_CLIPROXY_REQUEST_LOG=true`, inspect the translated upstream body in
`benchmark/generated/cliproxy-logs/` before touching the gateway route.

### 7. Run Kimi K3 first

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

### 8. Run the DeepSeek gateway acceptance check

DeepSeek uses a different gateway alias from Kimi. Before its 30-trial batch,
run the same pilot-task override:

```bash
$PIER job start -c benchmark/generated/deepseek-v4-flash.yaml \
  --env-file benchmark/env.local \
  --path ../DeepSWE/tasks \
  --include-task-name anko-default-function-arguments \
  --job-name acceptance-deepseek
```

If all three trials complete, start the primary DeepSeek job.

### 9. Run DeepSeek V4 Flash 0731

```bash
$PIER job start -c benchmark/generated/deepseek-v4-flash.yaml \
  --env-file benchmark/env.local
```

### 10. Run GPT-5.6 Luna

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

Codex reaches Opus through the same `benchmark/bridges/codex-cliproxy/`
deployment as DeepSeek and Kimi. There is no separate Opus bridge: CLIProxyAPI
routes `claude-opus-5` straight to `api.anthropic.com/v1/messages`, never
through the shared LiteLLM gateway. The previous dedicated LiteLLM bridge has
been removed.

When Opus is enabled later:

1. append the variables from `benchmark/deferred/opus-env.example` to
   `benchmark/env.local` — only `ANTHROPIC_API_KEY` is new;
2. regenerate so the bridge learns the Opus route, and restart it:

```bash
python3 benchmark/scripts/prepare_configs.py \
  --env-file benchmark/env.local \
  --include-opus

cd benchmark/bridges/codex-cliproxy
docker compose --env-file ../../env.local up -d --force-recreate
cd ~/PA1
```

3. run the single Opus model job:

```bash
$PIER job start -c benchmark/deferred/opus.yaml \
  --env-file benchmark/env.local
```

The generated Opus catalog is separate from the current DeepSeek/Kimi catalog.

The Anthropic route through the bridge has **not** been validated against live
Anthropic traffic, and it differs from the removed LiteLLM bridge in ways that
matter for measurement: reasoning tokens are estimated rather than reported,
reasoning effort maps to Anthropic adaptive thinking instead of a token budget,
and `/v1/responses/compact` is unsupported. Run the Opus acceptance checks in
[`bridges/codex-cliproxy/README.md`](bridges/codex-cliproxy/README.md) before
treating Opus Codex numbers as comparable to anything.

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
