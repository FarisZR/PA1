# DeepSWE benchmark runner

This directory contains the PA1 benchmark templates, frozen compatibility data,
and runbook for the current batch. Primary jobs are split **per model**, not per
harness, so expensive/high-priority models can be completed independently.

## Current run order

The Pi / Claude Code / Codex Kimi K3 runs were completed on 2026-08-31 and are
preserved as historical benchmark data. Do not rerun them with the current
configuration. All other model runs use the current setup below. Kimi K3 is the
only continuity exception: its harness/tool configuration remains aligned with
the 2026-08-31 run where required ([PA1 #54](https://github.com/FarisZR/PA1/issues/54)).

There are no deferred model jobs. Claude Opus 5 and all OpenCode V2 primary
profiles are active and ready to run.

Run the current setup in this order:

1. generate deployment-specific files with `prepare_configs.py --include-opus`
2. start the Codex compatibility bridge and authenticate the ChatGPT account used for Luna
3. `benchmark/generated/smoke-test.yaml` — low-effort pilot across Luna subscription, direct Z.AI, and DeepSeek/LiteLLM routes
4. run any route-specific acceptance checks still needed
5. `benchmark/generated/glm-5.3-sub.yaml`
6. `benchmark/generated/deepseek-v4p1-flash.yaml`
7. `benchmark/generated/luna.yaml`
   - `benchmark/generated/deepseek-claude-code-cliproxy-api.yaml` — Claude Code × DeepSeek rerun at true `max` (see below)
   - `benchmark/generated/deepseek-codex-rerun.yaml` — rerun of the five Codex × DeepSeek trials affected by issue #111 (see below)
   - `benchmark/generated/deepseek-pi-rerun.yaml` — rerun of all six Pi × DeepSeek trials affected by issue #111 (see below)
8. `benchmark/configs/opus.yaml`
9. run the matching `benchmark/configs/opencode-v2/<model>.yaml` job for the
   OpenCode V2 result of each model. Kimi's OpenCode V2 profile preserves the
   continuity policy from the 2026-08-31 configuration.

The source templates for the gateway-backed Pi / Claude Code / Codex jobs remain
under `benchmark/configs/`; use the generated files for GLM, DeepSeek, Luna,
and Kimi because generation resolves deployment-specific endpoints and Codex
bridge files. Opus is a direct-Anthropic source job and is launched from
`benchmark/configs/opus.yaml`.

## OpenCode V2 verification, smoke tests, and primary profiles

Pier's `opencode-v2` adapter is exercised separately from the current Pi /
Claude Code / Codex primary jobs. There are two small jobs under
`benchmark/configs/opencode-v2/`: `smoke.yaml` covers the three active
provider surfaces (direct Z.AI GLM, CLIProxyAPI-backed Luna, and DeepSeek
through LiteLLM), while `delegation-smoke.yaml` retains the historical
Fireworks GLM delegation check. Both use `low`, have zero automatic
whole-trial retries, and leave primary reasoning settings unchanged.

Run the deterministic verifier before any gateway call:

```bash
python3 benchmark/scripts/verify_opencode_v2.py \
  --pier-root ~/pier \
  --mode offline \
  --output-dir /absolute/path/to/evidence/offline
python3 benchmark/scripts/verify_opencode_v2.py \
  --pier-root ~/pier \
  --mode live \
  --env-file benchmark/env.local \
  --model glm-5p3-flash --variant low --max-cost-usd 2 \
  --output-dir /absolute/path/to/evidence/live
```

The verifier reads the release version and archive checksum from the smoke YAML
and checks them against the reference provenance and other OpenCode job
configs. It uses a disposable loopback fake provider and the pinned
`@opencode/cli-linux-x64` 2.0.8 bytes. It never reads `benchmark/env.local` in
offline mode. Live mode accepts credentials only from an explicitly supplied
env file and must retain Fireworks route provenance; missing provenance is
reported as blocked, never as a provider pass. Do not launch a full DeepSWE
job for this gate.

For a cheap real-task check of every provider surface used by the current
OpenCode V2 runs, generate and run the dedicated three-trial smoke job. It
uses GLM-5.3-Flash Low through the native Z.AI Coding Plan endpoint,
GPT-5.6 Luna Low through CLIProxyAPI backed by the ChatGPT subscription, and
DeepSeek V4.1 Flash Low through the LiteLLM proxy. Each edits one file in
`benchmark/tasks/opencode-v2-smoke`, with 8192-token request caps and no
whole-trial retries:

```bash
python3 benchmark/scripts/prepare_configs.py --env-file benchmark/env.local --include-opus
PIER=~/pier/.venv/bin/pier

$PIER run \
  -c benchmark/generated/opencode-v2/smoke.yaml \
  --env-file benchmark/env.local --yes
```

This low-effort smoke does not change the primary profiles' reasoning
levels or task selection.

The per-model primary profiles are under
[`benchmark/configs/opencode-v2/`](configs/opencode-v2/). They are launched as separate primary jobs and use the same scoring and
pricing policy as the other harnesses. Use `benchmark/references/opencode-v2-glm-5.3-flash.json` to verify the
frozen binary/profile provenance and `benchmark/pricing.yaml` for PA1's existing
normalized cost policy.

Tracked decisions and regressions for this gate:
[PA1 #41](https://github.com/FarisZR/PA1/issues/41) (OpenCode V2 adapter
implementation; web search is disabled except for Kimi K3 to preserve the 2026-08-31 configuration),
[#40](https://github.com/FarisZR/PA1/issues/40) (V2 does not set max output
tokens; the smoke wire control is `limit.output` metadata plus an explicit
Chat Completions `max_tokens` model body),
[#37](https://github.com/FarisZR/PA1/issues/37) (per-harness transport-retry
policy; the smoke jobs run with retries disabled so adapter faults stay
visible), and [#9](https://github.com/FarisZR/PA1/issues/9) (V1 vs V2
decision: V2, npm scope `@opencode`, pinned `2.0.8`).

Each current generated primary job contains Pi, Claude Code, and Codex over the
same 10 selected DeepSWE tasks. OpenCode V2 is represented by a separate
per-model profile under `benchmark/configs/opencode-v2/`. The split is only
about launch/config organization; it does not make the three-harness jobs
historical.

Kimi K3 alone keeps the web-tool behavior of the completed 2026-08-31 run for
continuity. GLM-5.3-Flash, DeepSeek V4.1 Flash, and GPT-5.6 Luna use the updated
configuration with web-search tooling disabled. The OpenCode V2 profiles follow
the same rule: web search is disabled except for Kimi K3
([PA1 #48](https://github.com/FarisZR/PA1/issues/48),
[PA1 #54](https://github.com/FarisZR/PA1/issues/54)).

For a model evaluated on all four harnesses, the complete wave is therefore
**40 planned trials**: 30 from the current Pi / Claude Code / Codex job plus 10
from the matching OpenCode V2 profile. Each profile uses 1 attempt per task,
at most 1 automatic whole-trial retry, and
a per-model `n_concurrent_trials` sized against the model's Fireworks
token-rate headroom (see "Sizing concurrency against the limit").

Successful trials run once. The retry is meant for transport/gateway faults: a
trial that fails with one is discarded and run again once; if the retry also
fails, the second failure is final.

Agent timeouts and verifier/reward faults are **not** retried
(`exclude_exceptions` keeps Pier's default non-retryable set). A trial that
exhausts the 10,800-second budget is a genuine efficiency result on this task
set, not an infrastructure fault, and re-running it would both double the spend
on the most expensive tasks and erase that result. Verifier and reward-file
faults are grading faults; re-running a whole trial does not fix them.

Pier retries every exception type that is *not* excluded, however, and a
non-zero harness exit is always `NonZeroAgentExitCodeError`, whatever caused it.
The recorded runs therefore also contain retries after failures of the model or
harness ([PA1 #95](https://github.com/FarisZR/PA1/issues/95)). For those, the
first attempt is the observation. `scripts/build_corrected_results.py` publishes
the canonical attempt as the normal trial directory under
`data/benchmark-results/` and keeps every other attempt under `.retry-attempts/`
(see `data/benchmark-results/README.md`). Raw runs under `benchmark/runs/` keep
Pier's layout; analyze the published copy.

Comparative cost uses the canonical attempt only. Discarded attempts still
consume budget: record their token usage and report it separately as
experimental overhead. Run only one model job at a time.

## Frozen versions

### Current benchmark setup (2026-09-21)

Use these revisions for new runs. The Pier pin is the merge commit that includes
the reviewed OpenCode V2 live-evidence and timeout-diagnostic fixes plus the
provider-URL compatibility hotfix required for CLIProxyAPI on the Docker bridge
endpoint.

| Component | Frozen revision/version |
| --- | --- |
| FZR Pier fork | [`ac868ad54893db98143f6e9a2d8c783faeb64a61`](https://github.com/FZR-forks/pier/commit/ac868ad54893db98143f6e9a2d8c783faeb64a61) |
| DeepSWE | [`0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea`](https://github.com/datacurve-ai/deep-swe/commit/0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea) |
| Codex CLI | `0.151.0` |
| Claude Code | `2.1.251` |
| Pi | `0.84.4` |
| OpenCode V2 | [`2.0.8`](https://www.npmjs.com/package/@opencode/cli/v/2.0.8) (`@opencode/cli-<target>@2.0.8`; checksums and frozen catalog provenance under `benchmark/references/`) |
| Codex model catalog | `rust-v0.151.0` vendored at `benchmark/references/codex-rust-v0.151.0-models.json` |
| Codex compatibility bridge | CLIProxyAPI `v7.2.146` + [upstream #5659](https://github.com/router-for-me/CLIProxyAPI/issues/5659) backport; GHCR digest `sha256:26de0755cf37765291e149590e13ee354010c8caa7b25ec3981827f2d606d6dc` |

Pier PR [FZR-forks/pier#12](https://github.com/FZR-forks/pier/pull/12) added the
independent `opencode-v2` adapter, PR
[#14](https://github.com/FZR-forks/pier/pull/14) made its execution evidence
durable during failures and timeouts, and PR
[#15](https://github.com/FZR-forks/pier/pull/15) removed an OpenCode V2-only
provider URL restriction that rejected the benchmark CLIProxyAPI endpoint
`http://172.17.0.1/v1`. PR #15 changes only the OpenCode V2 provider-URL
validation and its tests; Pi, Claude Code, Codex, task definitions, retry
semantics, and accounting are unchanged. The PA1-side OpenCode configuration,
verification evidence, frozen binary checksums, model isolation rules, output
caps, and transport choices are documented in
`benchmark/references/opencode-v2-verification.md`,
`benchmark/references/opencode-v2-glm-5.3-flash.json`, and
`benchmark/configs/opencode-v2/`.

The DeepSWE revision includes the upstream 10,800-second task timeout. Claude
Code runs with its updater disabled. Pier writes `lock.json` into each job
result directory; keep it with the benchmark results and record the PA1 commit
used for the run.

### DeepSeek V4.1 pre-hotfix checkpoint — preserve completed runs

DeepSeek V4.1 Flash was already completed on Pi, Claude Code, and Codex using
Pier [`7636cbee99ed947c64e0b350be031ec31e47dfee`](https://github.com/FZR-forks/pier/commit/7636cbee99ed947c64e0b350be031ec31e47dfee)
before the OpenCode V2 CLIProxyAPI incompatibility was discovered. Preserve
those completed results on that checkpoint.

The OpenCode V2 adapter at that revision enforced HTTPS for provider URLs except
explicit loopback HTTP addresses. Trial containers reach CLIProxyAPI through
the Docker bridge at `http://172.17.0.1/v1`, so OpenCode V2 rejected the route
before the harness could run. Pier PR
[#15](https://github.com/FZR-forks/pier/pull/15) removes that adapter-specific
restriction and makes OpenCode V2 follow the same provider-address policy as the
other Pier adapters.

The hotfix merge checkpoint is
[`ac868ad54893db98143f6e9a2d8c783faeb64a61`](https://github.com/FZR-forks/pier/commit/ac868ad54893db98143f6e9a2d8c783faeb64a61).
Use it for the DeepSeek OpenCode V2 trials and for every newly started benchmark
run after the hotfix. Do **not** rerun the completed DeepSeek Pi, Claude Code,
or Codex trials merely to align the Pier SHA: PR #15 does not alter any code
path used by those harnesses. Their exact runner revision remains recorded by
the retained job `lock.json`.

The resulting retained Pier provenance is therefore:

| Primary data | Pier revision |
| --- | --- |
| Kimi K3 — Pi, Claude Code, Codex | `ff65bae55c9a8ff15ddd3c2967c81a936713dd4d` |
| DeepSeek V4.1 Flash — Pi, Claude Code, Codex | `7636cbee99ed947c64e0b350be031ec31e47dfee` |
| DeepSeek V4.1 Flash — OpenCode V2 | `ac868ad54893db98143f6e9a2d8c783faeb64a61` |
| All newly started runs after the hotfix | `ac868ad54893db98143f6e9a2d8c783faeb64a61` |

### Historical Kimi K3 2026-08-31 run configuration — preserve exactly

The completed Kimi K3 runs at the end of August used the following frozen setup.
This is historical provenance for Kimi only and must not be generalized to the
other model configurations. The later model runs use the current setup above;
Kimi keeps its historical web-tool behavior for continuity
([PA1 #54](https://github.com/FarisZR/PA1/issues/54)).

| Component | Frozen revision/version used on 2026-08-31 |
| --- | --- |
| FZR Pier fork | [`ff65bae55c9a8ff15ddd3c2967c81a936713dd4d`](https://github.com/FZR-forks/pier/commit/ff65bae55c9a8ff15ddd3c2967c81a936713dd4d) |
| DeepSWE | [`0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea`](https://github.com/datacurve-ai/deep-swe/commit/0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea) |
| Codex CLI | `0.151.0` |
| Claude Code | `2.1.251` |
| Pi | `0.84.4` |
| OpenCode V2 | not available in this Pier revision |
| Codex model catalog | `rust-v0.151.0` vendored at `benchmark/references/codex-rust-v0.151.0-models.json` |
| Codex compatibility bridge | completed Kimi K3 run used unmodified CLIProxyAPI `v7.2.146`, digest `sha256:238691ac26ce55e4d1c5219d72e3ad74838f81eda26359912eeb415e2820d163` |

The completed Kimi K3 run predates the [CLIProxyAPI #5659](https://github.com/router-for-me/CLIProxyAPI/issues/5659) backport. Its request logs
were checked and contained zero streamed deltas with both non-empty `content`
and `reasoning_content`, so the triggering condition for that bug was absent.
Later third-party Codex runs use the `v7.2.146` base with only upstream fix
[`c8ecb4f3`](https://github.com/router-for-me/CLIProxyAPI/commit/c8ecb4f3c972664aae802e21c6a6743d5f6bd80a) backported. Full scope and verification are recorded in
[`benchmark/bridges/codex-cliproxy/BACKPORT-5659.md`](bridges/codex-cliproxy/BACKPORT-5659.md).

## Current model policy

| Model | Reasoning | Routing | Context behavior |
| --- | --- | --- | --- |
| Kimi K3 | max | Existing LiteLLM gateway | Native 1,048,576 context |
| GLM-5.3-Flash | max | Direct Z.AI Coding Plan API (Codex via CLIProxyAPI translation) | 1,000,000-token Z.AI model window |
| DeepSeek V4.1 Flash | max | Existing LiteLLM gateway | Normalized to exactly 1,000,000 across Pi, Claude Code, Codex, and OpenCode V2 |
| GPT-5.6 Luna | max | CLIProxyAPI -> ChatGPT subscription | 272,000-token benchmark window |

DeepSeek is deliberately normalized to exactly **1,000,000 tokens** across all
harnesses. DeepSeek documents the model as having a 1M context window, and the
upstream Pi and OpenCode/models.dev profiles both encode that as 1,000,000.
Fireworks advertises a larger 1,048,576-token route limit, but PA1 does not use
that provider-specific ceiling because doing so would create harness-specific
context differences for the same benchmark model.

The general smoke test is intentionally low-effort. It runs one pilot task
through all nine Pi / Claude Code / Codex combinations for Luna, GLM, and
DeepSeek so every API surface used by the remaining primary runs is exercised
before primary spending.

The repository does not change the LiteLLM deployment. DeepSeek/Kimi/GLM vendor
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
base URL/authentication path is redirected to the shared CLIProxyAPI instance,
preserving Codex's first-party Luna profile.

Every non-GPT Codex model uses the exact `gpt-5.6-sol` profile from Codex
`rust-v0.151.0` as its compatibility base. The complete upstream `models.json`
is frozen in this repository at
`benchmark/references/codex-rust-v0.151.0-models.json` with SHA-256
`eb0d7b9a5dcaf103895c5f8a14c16b269df46e039b375a55ba97f6238542d2ed`.
Generation reads only this local file.

For Claude Opus 5, DeepSeek, Kimi, and GLM, the Sol profile is preserved except for:

- model identity/display metadata;
- model-specific context, modality, and supported reasoning metadata;
- `multi_agent_version: "v1"` because non-GPT models use Codex Multi-Agent V1;
- `use_responses_lite: false` because these third-party routes use the normal
  Responses path.

This preserves the rest of the current-release Sol behavior, including
`tool_mode: "code_mode_only"`, parallel tool calls,
the Sol system/profile instructions, and `auto_compact_token_limit: null`.
DeepSeek keeps the Codex compaction field and explicitly sets Claude Code
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=1000000`.

#### Codex compatibility bridge

Codex's Responses requests are **not** sent to the LiteLLM gateway for the
third-party models. Codex 0.151.0 unconditionally attaches `client_metadata`,
and the gateway forwards the Responses `reasoning` object into `reasoning_effort`
as an object, so the Fireworks-backed routes reject every request (PA1 issue
[#31](https://github.com/FarisZR/PA1/issues/31)). DeepSeek, Kimi, and GLM therefore route through a pinned CLIProxyAPI instance
that translates Responses to Chat Completions in front of the same gateway:

```text
Codex -> Responses -> CLIProxyAPI -> Chat Completions -> LiteLLM -> Fireworks
```

Claude Opus 5 uses the same CLIProxyAPI deployment for Codex, but that route
translates Responses to Anthropic Messages and calls `api.anthropic.com`
directly with `ANTHROPIC_API_KEY`. Luna instead uses the bridge's ChatGPT
OAuth credential for all four harnesses; Codex and OpenCode retain their native
Responses profiles, while Pi uses the OpenAI-compatible surface and Claude Code
uses CLIProxyAPI's Anthropic-compatible translation.

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

The third-party `[1m]` aliases are retained for DeepSeek/Kimi/GLM compatibility.
Luna also uses `[1m]`, then explicitly lowers its compaction window to 272,000.
Kimi keeps its 1,048,576 declared context. DeepSeek instead explicitly sets a
1,000,000-token Claude Code auto-compaction window so it matches Pi, Codex, and
the OpenCode V2 profile under the DeepSeek normalization policy above.
For unrecognized `[1m]` aliases Claude Code itself assumes a 1,000,000-token
window, so DeepSeek's declared and effective limits now match.

Every Claude Code cell also sets two timeout variables that only matter because
the requests are routed through a gateway rather than directly to Anthropic:

| Variable | Value | Why |
| --- | --- | --- |
| `API_FORCE_IDLE_TIMEOUT` | `0` | Turns off the 5-minute body idle timeout, which is active by default on any provider other than the direct Anthropic API. At `max` reasoning a silent thinking pause can exceed it. |
| `CLAUDE_STREAM_IDLE_TIMEOUT_MS` | `1800000` | Raises both the event- and byte-level streaming idle watchdogs to 30 minutes, the byte-level cap. Claude Code counts gateway-relayed bytes including SSE pings and aborts a silent stream; a gateway that strips or buffers pings during a long thinking pause would otherwise abort the trial. |

These are documented in [Claude Code environment variables](https://code.claude.com/docs/en/env-vars)
and [Model configuration](https://code.claude.com/docs/en/model-config#correct-the-window-for-a-gateway-or-custom-model-id).
The Opus job sets none of them: `claude-opus-5` is a model ID Claude
Code recognizes, and it connects to the Anthropic API directly.

#### Claude Code output-token policy

PA1 deliberately leaves `CLAUDE_CODE_MAX_OUTPUT_TOKENS` unset. Claude Code
documents a 32,000-token default for model IDs it does not recognize, including
gateway-specific names. The earlier benchmark configuration overrode this with
64,000 tokens. That override was removed because it was a PA1-specific policy,
not a requirement of the models or their official Claude Code integrations.

The model-level limits are substantially larger: [Kimi K3's API defaults
`max_completion_tokens` to 131,072](https://www.kimi.ai/help/kimi-api/api-troubleshooting),
while [DeepSeek V4.1 Flash documents a 384K maximum output](https://api-docs.deepseek.com/quick_start/pricing/).
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
deepseek/deepseek-v4p1-flash
zai/glm-5p3-flash
openai/gpt-5.6-luna
```

The gateway exposes GLM-5.3-Flash as `glm-5p3-flash`. Pi therefore registers that
transport alias as a custom `zai` model while copying Pi 0.84.4's built-in
`zai/glm-5.3-flash` metadata: text-and-image input, `low`/`high`/`max`
reasoning, a 1,000,000-token context window, a 131,072-token output ceiling,
and the built-in cost metadata. The Fireworks route advertises 1,048,576
context, but like `glm-5.3-sub` and DeepSeek, PA1 normalizes every GLM harness
to 1,000,000. That covers Codex's catalog, the bridge entry, Claude Code's
compaction window, and OpenCode V2's catalog limit.

Two Z.AI transport settings are overridden for the Fireworks-backed gateway.
`thinkingFormat` is set to `"openai"` so Pi sends only `reasoning_effort`;
the bundled `"zai"` format sends `thinking` as well and the gateway rejects
that pair. `zaiToolStream` is set to `false` because Pi otherwise adds the
Z.AI-only `tool_stream: true` field whenever tools are present, which Fireworks
also rejects with HTTP 400. Generation now validates both invariants before any
benchmark file is written.

Pi has no native subagent system in this benchmark setup. Pier launches the
selected provider/model explicitly in non-interactive print mode.

OpenCode V2 keeps the same canonical `zai/glm-5.3-flash` model metadata, but
uses its native Fireworks transport for the PA1 gateway route. This avoids the
Z.AI transport emitting both `thinking` and `reasoning_effort` (PA1 #53) while
preserving the upstream context window and normalized pricing.

DeepSeek V4.1 Flash is newer than the frozen Pi 0.84.4 catalog, so
`benchmark/configs/deepseek-v4p1-flash.yaml` declares its model explicitly. The
entry preserves DeepSeek's required reasoning-content echo, adds native image
input, uses the normalized 1,000,000-token context window, and uses the same
`thinkingFormat: openai` compatibility path as Pi's bundled Kimi K3 entry: a
single `reasoning_effort`, mapped from `--thinking` by the declared
`thinkingLevelMap` (`max` -> `"max"`). The gateway's Fireworks route rejects the
alternative `thinking: {type: "enabled"}` plus `reasoning_effort` pair with HTTP
400 before the first turn.

The underlying rule is a property of the gateway's Fireworks route, not of one
model: it rejects `thinking` and `reasoning_effort` together, and accepts either
one alone. All three Fireworks-backed aliases were probed directly against the
gateway, and all behave identically — `deepseek-v4p1-flash`, `kimi-k3`, and
`glm-5p3-flash` each return HTTP 200 for `reasoning_effort` alone and HTTP 400
for the pair. Any further Fireworks model added to Pi must therefore be checked
for a bundled `thinkingFormat` that emits two controls.

Kimi K3 needs no override: its bundled entry already declares
`thinkingFormat: "openai"` with `supportsReasoningEffort: true`, and a captured
request confirms it sends `reasoning_effort: "max"` alone. Luna needs none
either; it is an OpenAI model that the gateway forwards to OpenAI, so the
Fireworks restriction does not apply to it.

Reasoning is also confirmed to be genuinely on at `max` for both Fireworks
models: sampled against the gateway, `reasoning_effort: "max"` returns non-empty
`reasoning_content` and non-zero `reasoning_tokens` for `kimi-k3` and
`deepseek-v4p1-flash` alike, while `"none"` returns none. `--thinking max` is
therefore a real setting on this route rather than a silently ignored one.

Pi's own provider entries are kept and patched per model rather than replaced by
a single custom gateway provider. Declaring the gateway as a new provider makes
Pi lose its vendor-specific compatibility detection: a captured request for such
a provider switches the system message to the `developer` role, adds
`store: false`, sends `max_completion_tokens` instead of `max_tokens`, and drops
Kimi's bundled `deferredToolsMode: "kimi"`. Those are harness behaviors under
test, so per-model `modelOverrides` is the smaller deviation.

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
deepseek-v4p1-flash
kimi-k3
glm-5p3-flash
```

`deepseek-v4p1-flash` is the stable DeepSeek model ID used by all three
harnesses. The gateway maps it to the DeepSeek V4.1 Flash checkpoint.

### Fireworks rate limits

Fireworks rate-limits **per account and per model**, on token throughput rather
than request count, and the effective limit is *adaptive* — it grows and shrinks
with recent usage inside a ceiling set by model size. A cold burst is therefore
throttled at a lower limit than the same load is once the account has warmed up,
which is why the 2026-09-20 GLM run saw 429s clustered in its first minutes and
none later. Fireworks sends no `Retry-After`; it documents exponential backoff.

The gateway forwards Fireworks' limit headers as `Llm_provider-X-Ratelimit-*`,
so the effective ceilings can be read at any time from a one-token request.
Measured 2026-09-20 (tokens/min):

| | `glm-5p3-flash` | `kimi-k3` | `deepseek-v4p1-flash` |
|---|---|---|---|
| Total prompt | 26,367,187 | 7,200,000 | 7,200,000 |
| Uncached prompt | 2,250,000 | 1,800,000 | 1,800,000 |
| Cache-adjusted prompt | 3,515,625 | 1,800,000 | 1,800,000 |
| Generated | 175,781 | 72,000 | 72,000 |

These are account-wide and shared with anything else using the same gateway
credential, so they are an upper bound on what a job can assume, not a budget
reserved for it.

Read the table as a snapshot, not as fixed capacity. **All concurrency sizing
here rests on the observed idle effective limit — 7.2M total-prompt TPM — and
not on any documented figure.** That number is what an untouched route reports,
and it is what the `check_rate_headroom.py` output and every config comment
divide by.

The interpretation below is secondary and weaker. An [archived 2026-05-07
revision](https://web.archive.org/web/20260507222857/https://docs.fireworks.ai/serverless/rate-limits)
of the Fireworks page published starting limits of 3.6M / 900k / 36k TPM; the
current revision no longer states them, so treat the figures as historical
rather than current documentation. Against that old anchor, `kimi-k3` and
`deepseek-v4p1-flash` both sit at exactly twice it while `glm-5p3-flash` sits
near 7x prompt and 5x generated, measured two hours after a 55-minute GLM job.
That is consistent with elevated limits persisting for hours after the traffic
that earned them, but it is inference from three readings with no pre-run
baseline, and the decay is undocumented. Nothing operational depends on it.

The practical consequence is that a model's headroom at the *start* of a job is
near the floor, not the number measured after a previous run. DeepSeek V4.1
Flash begins cold, so a concurrency tuned against warmed GLM is not
automatically safe on it.

This is also why the cheap per-model acceptance check in steps 6, 8, and 10 is
worth running immediately before its primary job rather than hours earlier: it
is three trials of real traffic that the run order already budgets for, so it
warms the route at no extra cost. Pier has no stagger or ramp control — the only
load knob is `n_concurrent_trials` — so a back-to-back acceptance run is the
only free protection against the cold-burst 429s the Fireworks docs warn about
("if your traffic ramps up too quickly, you will get 429s").

### Sizing concurrency against the limit

`benchmark/scripts/check_rate_headroom.py` reads the current effective limits
and converts them into a trial count:

```bash
python3 benchmark/scripts/check_rate_headroom.py --env-file benchmark/env.local
python3 benchmark/scripts/check_rate_headroom.py --model glm-5p3-flash --watch 60
```

It sends a nonce in every probe on purpose. A repeated payload is served from
the gateway's cache without an upstream call, and the reply then carries no
`Llm_provider-X-Ratelimit-*` headers at all — which looks like "this route
reports no limits" rather than like a cache hit.

The arithmetic is `max_trials = limit_TPM / per_trial_TPM`. Configs are set at
roughly 82-85% of the resulting cap; the script prints a flatter 80% as its
conservative default, so it will sometimes suggest one trial fewer than a
config uses. Every such margin is against **median** per-trial demand, so it is
a sizing convention rather than guaranteed headroom — the p90 row below is what
a run of uniformly heavy trials would draw. Per-trial demand measured over the
20 real trials of the 2026-09-20 GLM run:

| | total prompt | uncached | generated |
|---|---|---|---|
| median per trial | 736,678 | 43,148 | 2,262 |
| p90 per trial | 1,393,319 | 69,870 | 4,501 |

**Total prompt binds, always, and by a wide margin** — an agentic loop re-sends
a roughly 94%-cached context every turn, so it consumes total-prompt allowance
an order of magnitude faster than uncached or generated allowance. Sizing
against output tokens, the intuitive choice, would be wrong by 3-8x.

Against a cold 7.2M total-prompt limit that caps a GLM job at 9.8 concurrent
trials on median demand, or 5.2 on p90. The 2026-09-20 run at 30 demanded 307%
of the cold limit.

Only GLM and Kimi have been measured on this harness set. For a model that has
not, scale a measured model's rate by the ratio between the two in upstream
DeepSWE v1.1 trial data (`https://deepswe.datacurve.ai/artifacts/v1.1/trials.json`,
31,617 rollouts with per-trial tokens and durations), **restricted to PA1's ten
tasks** — the full 113-task set understates it, because our selection is
heavier than average:

| prompt TPM, mini-swe-agent | all 113 tasks | PA1's 10 tasks |
|---|---|---|
| `glm-5-3-flash` | 410,747 | 503,080 |
| `deepseek-v4-flash` | 761,419 | 1,032,494 |
| `kimi-k3` | 116,396 | 173,443 |
| **DeepSeek / GLM ratio** | 1.85x | **2.05x** |

Upstream runs a different harness, so its absolute rates sit roughly 1.5-2x
below ours and are not usable directly. **The ratio is used as an estimate, not
as a transferable constant.** The one case where both sources measure the same
pair disagrees by about 40%: upstream puts GLM at 2.90x Kimi on our tasks where
our own runs measured 2.07x. That agrees on direction and rough magnitude, which
is enough to reject the earlier "DeepSeek behaves like GLM" assumption, but it
is not precision. A separate consistency check is better behaved — upstream's
94% cache rate for GLM matches the 94.4% measured on 2026-09-20.

Size for that uncertainty rather than through it. At the central 2.05x estimate
DeepSeek at four trials sits at 84% of the idle limit; the ratio would have to
reach 2.44x before four trials exceeded it, and the 40% disagreement above
spans that. Four is therefore the right setting on the central estimate but is
not immune to the estimate being wrong; drop to three if a run is too expensive
to risk. Replace the estimate with a direct measurement from the first
DeepSeek job's `result.json` files and this caveat goes away.

Treat the result as an upper bound rather than a target, for two reasons. The
per-trial figures are averages over a whole trial, but demand grows with context
length, so late-trial demand exceeds them. And the limit adapts to the *rate* of
increase as well as the level: the 2026-08-31 Kimi run sustained 30 trials at
about 148% of its own cold cap without a single 429, because Kimi's slower turns
let the adaptive limit keep pace, whereas GLM's six-times-denser ramp outran it.
A number under the cap is safe; a number over it is not automatically fatal.

Sampling the `remaining-tokens-*` headers while no PA1 job was running showed
0% of prompt and generated quota consumed on all three models across three
samples, so other consumers of the gateway credential were not measurably
eating the budget in that window. That was a single Sunday-afternoon
observation and says nothing about weekday load; re-sample before assuming
headroom.

### Claude Code aliases

```text
gpt-5.6-luna[1m]
deepseek-v4p1-flash[1m]
kimi-k3[1m]
glm-5p3-flash[1m]
```

The `[1m]` suffix is Claude Code compatibility metadata, not a different model.
Each alias must resolve to the same checkpoint as its unsuffixed counterpart.

The gateway needs to serve Kimi, DeepSeek, and GLM correctly on **Chat Completions**
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
| `LITELLM_OPENAI_BASE_URL` | Pi, bridge | OpenAI-compatible base URL ending in `/v1`; generation rejects dotless hosts, localhost, and non-80/443 ports because Pier's egress proxy would block them. |
| `LITELLM_ANTHROPIC_BASE_URL` | Claude Code | Anthropic-compatible base URL; generation applies the same host and 80/443 egress checks. |
| `PIER_EXTRA_CA_CERTS` | all three harnesses | Absolute path to the tracked `benchmark/puki-root-ca-2022.pem` bundle containing both public PUKI Root CA 2022 RSA and EC certificates. Required on this runner: the gateway serves an internal IONOS PUKI certificate that containers do not trust by default, and without it every trial fails its first model call. |
| `CODEX_CLIPROXY_BASE_URL` | Codex, Pi, OpenCode V2 | `/v1` endpoint of the compatibility bridge as seen from a trial container. Luna uses this route for the ChatGPT subscription. Must be on port 80 or 443 and must not be a dotless bare hostname; `prepare_configs.py` rejects both. |
| `CODEX_CLIPROXY_ANTHROPIC_BASE_URL` | Claude Code Luna | Anthropic-compatible base URL of the same CLIProxyAPI instance, without the `/v1` suffix. |
| `CODEX_CLIPROXY_API_KEY` | Codex; all Luna harnesses | Local token presented to the bridge. Chosen locally; it is not the ChatGPT OAuth credential or a vendor credential. |
| `CODEX_CLIPROXY_BIND`, `CODEX_CLIPROXY_PORT` | bridge | Host address and port the bridge publishes on. Must match `CODEX_CLIPROXY_BASE_URL`. |
| `CODEX_CLIPROXY_REQUEST_LOG` | bridge | Keep `true` for the current benchmark debugging run. It records every request body, prompt, and authorization header verbatim in the owner-only `benchmark/generated/cliproxy-logs/` directory; do not share those logs. Set `false` only when intentionally disabling capture. |
| `ANTHROPIC_API_KEY` | Pi, Claude Code, OpenCode V2, bridge | Direct Anthropic API credential for Claude Opus 5. Codex itself receives only the bridge-local `CODEX_CLIPROXY_API_KEY`; CLIProxyAPI holds `ANTHROPIC_API_KEY` for the outbound Anthropic Messages request. |
| `ZAI_API_KEY` | all GLM subscription harnesses, bridge | Z.AI Coding Plan key. Pi uses the native Z.AI provider, Claude Code uses Z.AI's Anthropic-compatible endpoint, OpenCode V2 maps it to its built-in `ZHIPU_API_KEY`, and CLIProxyAPI holds it for Codex's direct Z.AI Chat Completions route. |

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

### 2. Pin and build Pier

Pier declares Python `>=3.12` in its `pyproject.toml`. The PA1 runner uses
Python 3.13, but do not assume that a distro-managed interpreter exists at
`/usr/bin/python3.13`. Let `uv` install and manage the interpreter instead.

Install Python 3.13 once, then create/synchronize Pier's project environment:

```bash
cd ~
git clone https://github.com/FZR-forks/pier.git pier   # skip if present
cd ~/pier
git fetch origin
git checkout ac868ad54893db98143f6e9a2d8c783faeb64a61

uv python install 3.13
uv python find 3.13
uv sync --python 3.13

~/pier/.venv/bin/pier job start --help
```

`uv python install 3.13` installs a uv-managed interpreter if Python 3.13 is
not already available. Passing `--python 3.13` asks uv to resolve that
interpreter by version instead of requiring a particular filesystem path.
Therefore an error such as

```text
error: No interpreter found at path `/usr/bin/python3.13`
```

means only that the hard-coded system path does not exist; it does not mean Pier
requires a distro package at that path.

If a compatible Python is already installed, Pier also supports Python 3.12 or
newer. For exact PA1 runner reproduction, keep using 3.13.

Run the actual jobs from `~/PA1`, not from the Pier checkout.

To reproduce the completed DeepSeek V4.1 Pi / Claude Code / Codex runs,
deliberately check out the pre-hotfix checkpoint and resync the environment:

```bash
cd ~/pier
git checkout 7636cbee99ed947c64e0b350be031ec31e47dfee
uv sync --python 3.13
```

To reproduce the end-of-August runs instead of starting a new run, deliberately
check out the historical Pier revision:

```bash
cd ~/pier
git checkout ff65bae55c9a8ff15ddd3c2967c81a936713dd4d
uv sync --python 3.13
```

Do not use that historical checkout for OpenCode V2; it predates the adapter.

### 3. Configure LiteLLM

```bash
cd ~/PA1
cp benchmark/env.example benchmark/env.local
chmod 600 benchmark/env.local
$EDITOR benchmark/env.local
```

Before running anything, verify the runner itself can resolve/reach both gateway
surfaces and that the aliases listed above exist.

### 4. Run the cheap route smoke test

The smoke job uses the synthetic one-line file-edit task under
`benchmark/tasks/opencode-v2-smoke`. The agent only has to replace
`/app/answer.txt` with the expected marker and finish. No DeepSWE task is used.

The general smoke contains nine serial trials: Pi, Claude Code, and Codex on
GPT-5.6 Luna, GLM-5.3-Flash, and DeepSeek V4.1 Flash, all at low reasoning.

Run:

```bash
cd ~/PA1
PIER=~/pier/.venv/bin/pier

$PIER job start -c benchmark/generated/smoke-test.yaml \
  --env-file benchmark/env.local
```

Run the generated smoke job after `prepare_configs.py`; its DeepSeek Pi leg
needs the resolved LiteLLM endpoint. Do not start primary spending until all
nine trials finish and `benchmark/runs/smoke-current-routes/` contains the
expected result/trajectory data.

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
benchmark/generated/deepseek-v4p1-flash.yaml
benchmark/generated/glm-5.3-flash.yaml
benchmark/generated/glm-5.3-sub.yaml
benchmark/generated/luna.yaml
benchmark/generated/codex-cliproxy.toml        # Codex -> compatibility bridge
benchmark/generated/cliproxy-config.yaml       # bridge deployment config (0600)
benchmark/generated/codex-litellm.toml         # direct route; control only
benchmark/generated/codex-thirdparty-models.json
benchmark/generated/codex-glm-zai-models.json       # direct Z.AI GLM Codex catalog
benchmark/generated/codex-opus-models.json          # restricted Codex Opus catalog
```

The generated YAML files include the historical gateway-backed Kimi, DeepSeek,
GLM, and Luna templates, the separate direct-Z.AI `glm-5.3-sub` template, and
the deployment-resolved `smoke-test.yaml` used to exercise all current API
surfaces.
Claude Opus 5 is launched
from `benchmark/configs/opus.yaml` and uses the generated restricted Codex Opus
catalog plus the same CLIProxyAPI deployment. Generation resolves the nested Pi endpoint placeholder,
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

The generated historical third-party Codex catalog contains DeepSeek, Kimi, and
the `glm-5p3-flash` gateway alias because Luna uses Codex's bundled first-party
model entry. `codex-glm-zai-models.json` contains only the direct upstream
`glm-5.3-flash` entry used by `glm-5.3-sub`. A separate generated catalog
contains Claude Opus 5. These non-native entries are derived from the vendored
GPT-5.6 Sol entry; no upstream file is fetched while generating these artifacts.

### 5b. Start the Codex compatibility bridge

Required for Codex on Kimi, DeepSeek, GLM, and Claude Opus 5, and for every
GPT-5.6 Luna harness. Luna's ChatGPT OAuth credential is held by this instance.

```bash
cd ~/PA1/benchmark/bridges/codex-cliproxy
docker compose --env-file ../../env.local up -d
docker compose ps          # expect: healthy
```

For Luna, authenticate the ChatGPT account once if the persistent auth volume
does not already contain the benchmark credential:

```bash
docker compose run --rm codex-cliproxy ./CLIProxyAPI \
  -config /CLIProxyAPI/config.yaml -codex-device-login
docker compose --env-file ../../env.local up -d --force-recreate
```

Confirm that `gpt-5.6-luna` appears in the bridge model list before running
the Luna smoke test. The OAuth credential stays in CLIProxyAPI's auth volume and
is never copied into Pier.

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

The Luna smoke test proves the task environment and the shared subscription route, but it
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
full Kimi batch: check the bridge is healthy and inspect the translated upstream body in
`benchmark/generated/cliproxy-logs/` before touching the gateway route. The current
benchmark debugging run keeps `CODEX_CLIPROXY_REQUEST_LOG=true`; these logs contain
verbatim prompts and authorization headers, so keep the directory owner-only and do not
share it outside the runner.

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
all 30 concurrent. Kimi is the one model that needs no reduction: it draws
356,251 prompt TPM per trial, the lowest of the three Fireworks models, and the
2026-08-31 run completed at that setting without a single 429.

### 8. Configure and test GLM-5.3-Flash through Z.AI

The new subscription configuration is deliberately separate from the earlier
Fireworks route:

```text
benchmark/configs/glm-5.3-sub.yaml
benchmark/configs/opencode-v2/glm-5.3-sub.yaml
```

Set `ZAI_API_KEY` in `benchmark/env.local`, then regenerate and restart
CLIProxyAPI:

```bash
python3 benchmark/scripts/prepare_configs.py --env-file benchmark/env.local

cd benchmark/bridges/codex-cliproxy
docker compose --env-file ../../env.local up -d --force-recreate
cd ~/PA1
```

For this job the routes are intentionally provider-direct:

```text
Pi          -> Z.AI native provider
Claude Code -> https://api.z.ai/api/anthropic
OpenCode V2 -> https://api.z.ai/api/coding/paas/v4
Codex       -> CLIProxyAPI -> https://api.z.ai/api/coding/paas/v4
```

The Codex bridge is only the Responses-to-Chat-Completions compatibility layer.
The `glm-5.3-flash` bridge alias uses `ZAI_API_KEY` and the Z.AI Coding Plan
endpoint directly; it does **not** route through AiOrbit, the corporate LiteLLM
gateway, or Fireworks. The older `glm-5p3-flash` alias remains in the bridge
only so previously collected Fireworks runs stay reproducible.

Run a single pilot task before the primary batch:

```bash
$PIER job start -c benchmark/generated/glm-5.3-sub.yaml \
  --env-file benchmark/env.local \
  --path ../DeepSWE/tasks \
  --include-task-name anko-default-function-arguments \
  --job-name acceptance-glm-5.3-sub
```

If Pi, Claude Code, and Codex complete, run the primary three-harness job:

```bash
$PIER job start -c benchmark/generated/glm-5.3-sub.yaml \
  --env-file benchmark/env.local
```

Run OpenCode V2 separately:

```bash
$PIER job start -c benchmark/configs/opencode-v2/glm-5.3-sub.yaml \
  --env-file benchmark/env.local
```

Both jobs are capped at `n_concurrent_trials: 3`.

### Historical Fireworks GLM route

These steps cover the Fireworks-backed GLM route: the planned rerun on AiOrbit's
LiteLLM 1.102.1, then the earlier 2026-09-20 run. They are not used by
`glm-5.3-sub`.

#### Fireworks GLM rerun on AiOrbit (LiteLLM 1.102.1)

`glm-5.3-flash.yaml` and `opencode-v2/glm-5.3-flash.yaml` now describe the
rerun (jobs `glm-5.3-flash-rerun` and `opencode-v2-glm-5.3-flash-rerun`), not
the excluded 2026-09-20 batch. The historical configuration is in git history.
The rerun isolates whether that batch's timeouts, output-token volume, and
repeated tool calls came from the model or from the lost reasoning history.

Compared with the 2026-09-20 batch:

- **#111:** AiOrbit now runs LiteLLM 1.102.1, whose Fireworks adapter forwards
  `reasoning_content` again. The offline replay
  (`scripts/replay_litellm_fireworks.py`) forwards 2 of 2 reasoning blocks on
  1.102.1 and 0 of 2 on 1.101.0.
- **#94:** Claude Code goes through CLIProxyAPI (`is-compat: true`) instead of
  LiteLLM's `/v1/messages` adapter, like the DeepSeek fixed-thinking rerun.
- **Context:** normalized to 1,000,000 in every harness, as in `glm-5.3-sub`.
- **Concurrency:** 10 for the main job plus 6 for OpenCode V2, started as two
  waves (see the run plan below), instead of the throttled 30.
- Pi and OpenCode V2 keep the Fireworks transport workarounds (#53).

LiteLLM response caching stays as deployed, as for every earlier run.

Point `LITELLM_OPENAI_BASE_URL` and `LITELLM_API_KEY` at AiOrbit and
regenerate. Then run the live route check against the exact gateway before
starting. Pass `--bridge` once the bridge is running with
`CODEX_CLIPROXY_REQUEST_LOG=true`:

```bash
python3 benchmark/scripts/check_litellm_route.py --env-file benchmark/env.local \
  --model glm-5p3-flash --expect-version 1.102.1 --bridge
```

It fails if any of the following holds:

- the LiteLLM version is wrong or the key budget is insufficient;
- `reasoning_effort` or `max_tokens` does not reach Fireworks;
- reasoning replay is dropped or streaming `cached_tokens` is missing;
- the router retried or fell back;
- a bridge body lacks `reasoning_effort: "max"` or the replayed reasoning.

It also reports whether response caching is on, whether `tool_choice` is
dropped, and whether cached tokens are billed at the cache-read rate.

**Fastest run plan.** Skip the separate pilot task. Its job was to warm the
route, and GLM still has its warmed limits: 32.96M total prompt, 4.39M
cache-adjusted, 1.8M uncached, and 72k generated tokens per minute, measured on
2026-09-24. The route check above replaces it as the correctness gate.

1. Start wave 1: the main job with 30 trials at 10 concurrent.

   ```bash
   $PIER job start -c benchmark/generated/glm-5.3-flash.yaml --env-file benchmark/env.local
   ```

2. While wave 1 runs, confirm reasoning replay in the first Codex and Claude
   Code bridge logs. After ~30 minutes, read the limits:

   ```bash
   python3 benchmark/scripts/check_rate_headroom.py --env-file benchmark/env.local \
     --model glm-5p3-flash --watch 60
   ```

   Ignore its printed trial count, which uses an outdated per-trial rate. Read
   the limits themselves.

3. Once the cache-adjusted limit reads 5.49M or more (one 1.25x step), start
   wave 2: OpenCode V2 with 10 trials at 6 concurrent.

   ```bash
   $PIER job start -c benchmark/configs/opencode-v2/glm-5.3-flash.yaml \
     --env-file benchmark/env.local
   ```

Expected wall time is about 3.5-4 h if GLM behaves as on the direct Z.AI route,
which kept its reasoning (no timeouts, median 65 minutes per trial). It is about
4.5-5.5 h if the 2026-09-21 profile repeats (49.6 trial-hours for 30 trials,
ten 3 h timeouts). The 3 h agent limit sets a floor of about 3 h 10 min. Pier
cannot change concurrency mid-job, so two staggered jobs are the simplest way
to follow the limit as it grows. Going beyond ~16 concurrent trials would need
further limit steps and risks 429 slowdowns that inflate trial durations.

**Sizing evidence.** This comes from the AiOrbit bridge logs and trajectories,
analyzed in #129.

- The 2026-09-21 run at 8 drew a median 10.9M total prompt per minute (p99
  20.7M). That is 2.13M per running trial (p90 2.85M), about 3x the earlier
  737k-per-trial figure.
- Cache-adjusted load is about 0.30M per trial (p90 0.40M). This counts cached
  tokens at 1/7.5, the total/cache-adjusted limit ratio observed on both
  gateways; Fireworks does not document the weighting.
- Limits step up 1.25x after roughly 16 minutes of near-limit load. Two ladders
  were observed:
  - total prompt: 13.5M, 16.9M, 21.1M, 26.4M, 33.0M, 41.2M;
  - cache-adjusted: 1.8M, 2.25M, 2.81M, 3.52M, 4.39M, 5.49M.
- The only 429s in the 2026-09-20 30-trial run came in its first 1.5 minutes,
  plus two isolated ones later. At the start, empty prompt caches pushed
  uncached load to 2.2M per minute against the 1.8M uncached limit. No trial
  failed because of them.

After the run, confirm retention with `scripts/analyze_reasoning_retention.py`
on each run directory. Kept reasoning gives about 100% or more; dropped
reasoning gives a few percent.

#### Historical gateway acceptance check

GLM uses its own gateway alias. Run the pilot-task override before the primary
laptop batch:

```bash
$PIER job start -c benchmark/generated/glm-5.3-flash.yaml \
  --env-file benchmark/env.local \
  --path ../DeepSWE/tasks \
  --include-task-name anko-default-function-arguments \
  --job-name acceptance-glm-5.3-flash
```

If all three trials complete, start the primary GLM job.

#### Historical primary GLM run

```bash
$PIER job start -c benchmark/generated/glm-5.3-flash.yaml \
  --env-file benchmark/env.local
```

The GLM job is pinned to eight concurrent trials. A GLM trial draws a median
736,678 total-prompt TPM, measured over the 20 real trials of the 2026-09-20
run, and the 7.2M observed idle limit therefore caps a cold start at 9.8
trials; eight sits at 82% of that. That margin is against *median* demand, not
a guarantee — at p90 demand the same limit allows only 5.2 trials. That earlier attempt at thirty demanded 307% of the
cold limit, was throttled by the Fireworks route, and Codex did not survive it.
Expect roughly **2-3 hours** for the 30 trials.

### 9. Run the DeepSeek gateway acceptance check

DeepSeek uses a different gateway alias from Kimi. Before its 30-trial batch,
run the same pilot-task override:

```bash
$PIER job start -c benchmark/generated/deepseek-v4p1-flash.yaml \
  --env-file benchmark/env.local \
  --path ../DeepSWE/tasks \
  --include-task-name anko-default-function-arguments \
  --job-name acceptance-deepseek
```

If all three trials complete, start the primary DeepSeek job.

### 10. Run DeepSeek V4.1 Flash

```bash
$PIER job start -c benchmark/generated/deepseek-v4p1-flash.yaml \
  --env-file benchmark/env.local
```

The DeepSeek job is pinned to four concurrent trials. Do not assume it behaves
like GLM because both are flash-class: on PA1's ten tasks, upstream DeepSWE data
puts DeepSeek Flash at **2.05x** GLM-5.3-Flash's prompt TPM, because it takes
roughly a third more steps per trial (median 148 vs 110) in a comparable
wall-clock time. Scaling our measured GLM rate by that ratio gives about
1,510,190 TPM per trial, so the 7.2M cold limit caps a cold start at 4.8 trials.
Four sits at 84% of it; six would have been 126%.

Expect roughly **2.5-4 hours** for the 30 trials. DeepSeek trials are short —
upstream median 27 minutes on these tasks against Kimi's 93 — so the lower
concurrency costs much less wall clock than it would for a slow model. Replaying
our real Kimi durations scaled by that ratio through a four-worker scheduler
gives 2.5h; scaling from our (censored, therefore optimistic) GLM durations
instead gives about 4h.

Two caveats on the 2.05x. Upstream measures `deepseek-v4-flash` and this job
runs v4.1, and upstream's harness is `mini-swe-agent` rather than ours, so only
the ratio transfers, not the absolute rate. Re-measure from the run's own
`result.json` files afterwards and replace the estimate in
`benchmark/scripts/check_rate_headroom.py`.

### 11. Run GPT-5.6 Luna

Luna uses bounded concurrency (`n_concurrent_trials: 6`) because all harnesses
share one ChatGPT subscription through CLIProxyAPI. Six concurrent trials limit
the subscription burst while avoiding the unnecessary wall-clock cost of fully
serial execution.

```bash
$PIER job start -c benchmark/generated/luna.yaml \
  --env-file benchmark/env.local
```

Run the OpenCode V2 Luna profile separately; it uses the same subscription route
but keeps the concurrency configured in its own profile.

Keep each complete `benchmark/runs/<job-name>/` directory, especially its
`lock.json`.

## Claude Opus 5

Claude Opus 5 is an active primary model. The Pi / Claude Code / Codex job is
`benchmark/configs/opus.yaml`; OpenCode V2 uses
`benchmark/configs/opencode-v2/opus.yaml`. All use the same ten selected tasks
and medium reasoning.

Pi, Claude Code, and OpenCode V2 call the official Anthropic API directly with
`ANTHROPIC_API_KEY`. Codex reaches Opus through the same
`benchmark/bridges/codex-cliproxy/` deployment used for the third-party
compatibility path. CLIProxyAPI routes `claude-opus-5` directly to
`api.anthropic.com/v1/messages`; it does not send Opus through the shared
LiteLLM gateway.

Generate the current bridge and Codex catalogs with Opus enabled:

```bash
python3 benchmark/scripts/prepare_configs.py \
  --env-file benchmark/env.local \
  --include-opus

cd benchmark/bridges/codex-cliproxy
docker compose --env-file ../../env.local up -d --force-recreate
cd ~/PA1
```

Then run the primary job:

```bash
$PIER job start -c benchmark/configs/opus.yaml \
  --env-file benchmark/env.local
```

The Codex-to-CLIProxyAPI-to-Anthropic path has been validated end to end against
live Anthropic traffic and is part of the active benchmark setup. The route
still has documented measurement characteristics: CLIProxyAPI estimates
Anthropic reasoning-token counts, maps Codex effort to Anthropic adaptive
thinking, and does not implement `/v1/responses/compact` for the Claude
upstream. These are recorded properties of the working route, not blockers.

## OpenCode V2 primary profiles

OpenCode V2 is supported by the current pinned Pier revision. Its per-model primary profiles are under `benchmark/configs/opencode-v2/` and
are launched as separate per-model jobs from the Pi / Claude Code / Codex jobs.

Before a primary OpenCode V2 run, execute the offline verifier and the relevant
cheap live/provider gate described above, then launch the matching per-model
profile. [PA1 #9](https://github.com/FarisZR/PA1/issues/9) records the original
V1/V2 decision; Pier support itself is no longer blocked.

## Upstream compatibility references

Codex third-party model behavior is based on the frozen OpenAI `gpt-5.6-sol`
profile, not the vendor Codex setup scripts. The exact upstream source is:

- Codex 0.151.0 model catalog: https://raw.githubusercontent.com/openai/codex/refs/tags/rust-v0.151.0/codex-rs/models-manager/models.json

The vendor guides below are used only for Claude Code/model transport
compatibility. Their endpoint/API-key instructions are ignored because the
active batch routes through the existing LiteLLM gateway.

- DeepSeek Claude Code: https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/
- Kimi Claude Code: https://platform.kimi.ai/docs/guide/claude-code-kimi

## Claude Code × DeepSeek via CLIProxyAPI (issue #94)

AiOrbit's LiteLLM `/v1/messages` adapter lowers Claude Code's
`output_config.effort: "max"` to `reasoning_effort: "high"` for the Fireworks
deployments, because they do not declare max support. Deterministic
temperature-0 probes on the live gateway returned byte-identical output for
`high`, `xhigh`, and `max` on DeepSeek and GLM. Routing the same Anthropic
requests through the pinned CLIProxyAPI bridge sends `reasoning_effort: "max"`
to AiOrbit's Chat Completions surface, which forwards it unchanged. The bridge
request logs confirm this. The Pi, Codex, and OpenCode V2 legs are unaffected.

The same bridge route also fixes the separate reasoning-replay defect tracked in
issue #102. Claude Code sends its prior thinking blocks back on later tool turns,
but CLIProxyAPI drops them for an OpenAI-compatible model unless that model is
marked `is-compat: true`. The three LiteLLM-backed aliases in
`bridges/codex-cliproxy/config.template.yaml` now set that flag. The smoke below
must therefore check both that the task passes and that a later upstream request
body contains `reasoning_content` on an assistant tool-call message; a successful
HTTP response alone would not detect this silent loss.

`configs/deepseek-claude-code-cliproxy-api.yaml` contains only the Claude Code
leg and replaces the Claude Code result from `deepseek-v4p1-flash.yaml`. It uses
the same bridge environment as Claude Code × Luna (`CODEX_CLIPROXY_API_KEY`,
`CODEX_CLIPROXY_ANTHROPIC_BASE_URL`). The bridge must be running and must
declare `max` for `deepseek-v4p1-flash` (see `bridges/codex-cliproxy/config.template.yaml`).
The smoke command overrides the configured DeepSWE dataset with the synthetic
OpenCode V2 placeholder task, so it checks the route without paying for an
actual benchmark task; the full rerun still uses the ten configured tasks.

```bash
python3 benchmark/scripts/prepare_configs.py --env-file benchmark/env.local --include-opus
PIER=~/pier/.venv/bin/pier

# one-task placeholder smoke first
$PIER run \
  -c benchmark/generated/deepseek-claude-code-cliproxy-api.yaml \
  --env-file benchmark/env.local --yes \
  --job-name deepseek-claude-code-cliproxy-api-smoke \
  --path benchmark/tasks/opencode-v2-smoke
# verify every upstream DeepSeek request carried max
grep -l '"model":"deepseek-v4p1-flash"' benchmark/generated/cliproxy-logs/v1-messages-* \
  | xargs grep -ho '"reasoning_effort":"[a-z]*"' | sort | uniq -c
# full rerun
$PIER run \
  -c benchmark/generated/deepseek-claude-code-cliproxy-api.yaml \
  --env-file benchmark/env.local --yes
```

## Codex × DeepSeek rerun of the issue #111 trials

AiOrbit ran LiteLLM 1.101.0 until about 2026-09-21 12:02 UTC. Its Fireworks
provider removed `reasoning_content` from every message before forwarding, so
the earlier reasoning that Codex, Pi, and Claude Code sent back never reached
the model (issue #111; LiteLLM `32bf1aba`, fixed by LiteLLM PR #40682 in
1.102.0). Five of the ten Codex trials in `deepseek-v4p1-flash.yaml` ran before
the upgrade. `configs/deepseek-codex-rerun.yaml` reruns exactly those five
tasks with the Codex leg copied unchanged, including the one trial that passed,
so the selection follows the run time rather than the outcome. The other five
Codex trials are kept. The six affected Pi trials are not rerun by this job;
the DeepSeek Pi condition is excluded from the comparative data instead.

The bridge's `is-compat: true` setting, added after the original run, only
changes Claude-format translation. With `optimize-multi-agent-v2: false`, the
Codex Responses → Chat Completions route is the same as in the original run.

```bash
python3 benchmark/scripts/prepare_configs.py --env-file benchmark/env.local --include-opus
PIER=~/pier/.venv/bin/pier
$PIER run \
  -c benchmark/generated/deepseek-codex-rerun.yaml \
  --env-file benchmark/env.local --yes
# after the first requests: AiOrbit must report LiteLLM >= 1.102.0
# (needs CODEX_CLIPROXY_REQUEST_LOG=true when the configs were generated)
ls -t benchmark/generated/cliproxy-logs/v1-responses-* | head -20 \
  | xargs grep -ah -m1 -o 'X-Litellm-Version: [0-9.]*' | sort | uniq -c
# after the job: every trial must show retention above 100% (a few percent means removed)
python3 scripts/analyze_reasoning_retention.py benchmark/runs/deepseek-codex-rerun
# publish the rerun in place of the superseded trials (checks retention again)
python3 scripts/build_corrected_results.py --pier-python ~/pier/.venv/bin/python
```

## Pi × DeepSeek rerun of the issue #111 trials

Six of the ten Pi trials in `deepseek-v4p1-flash.yaml` ran before AiOrbit's
LiteLLM upgrade and never gave the model its earlier reasoning (issue #111).
The previous three-task rerun was constrained by the then-remaining budget and
ended when the gateway budget was exhausted; those failed attempts remain
preserved as audit evidence under
`data/benchmark-results/.excluded/deepseek-v4p1-flash/pi-rerun/`.

With additional budget available, `configs/deepseek-pi-rerun.yaml` now reruns
all six affected tasks, including affected trials that passed. Selection
therefore follows the gateway-version/time boundary rather than the outcome.
The four clean Pi trials are retained.

The Pi configuration remains unchanged from `deepseek-v4p1-flash.yaml`.
Concurrency is set to **4**, the maximum safe value under the existing DeepSeek
cold-start calibration; six simultaneous cold starts were estimated at about
126% of the 7.2M total-prompt TPM limit.

Use a fresh `benchmark/runs/deepseek-pi-rerun` run directory when launching
the replacement batch; the previous failed attempts are already preserved in
the published audit data.

```bash
python3 benchmark/scripts/prepare_configs.py --env-file benchmark/env.local --include-opus
PIER=~/pier/.venv/bin/pier
$PIER run \
  -c benchmark/generated/deepseek-pi-rerun.yaml \
  --env-file benchmark/env.local --yes
# after the job: every trial must show retention above 100% (a few percent means removed)
python3 scripts/analyze_reasoning_retention.py benchmark/runs/deepseek-pi-rerun
```
