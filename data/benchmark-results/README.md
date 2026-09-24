# Filtered benchmark results

This directory contains the committed, filtered evidence used for PA1 analysis
of the primary jobs for Kimi K3, GLM-5.3-Flash, DeepSeek V4.1 Flash, and
GPT-5.6 Luna. Only the eligible Kimi K3 Pi and Codex trials are in the normal
Kimi directory; its Claude Code trials are archived for audit (see below).
The directory layout mirrors the corresponding job directories under the local
`benchmark/runs/` directory, but it is a publication copy rather than a run
workspace: every trial directory holds the canonical attempt (see "Canonical
attempts" below).

Each model directory normally contains:

- the job-level `result.json`, `config.json`, and `lock.json` (except Kimi K3, whose unfiltered Pier summary is archived);
- each trial's canonical `result.json` and `config.json`; and
- each trial's structured `agent/trajectory.json`; and
- every other attempt under `.retry-attempts/`, containing its
  `result.json`, `config.json`, and `agent/trajectory.json` files.

Pier names both the job-level and task-level result files `result.json`; there
are no `results.json` or `task.json` files in these outputs. Retry attempts are
included in the same filtered form as the trials. Request/proxy logs, verifier
logs, agent console logs, session JSONL, sandbox contents, and generated
binaries remain intentionally excluded. The raw run workspace remains under
`benchmark/runs/` and is ignored by Git.

The DeepSeek V4.1 Flash OpenCode V2 job is published separately at
`opencode-v2-deepseek-v4p1-flash/`; it is a separate run from the primary
`deepseek-v4p1-flash/` job. It contains the 10 trials and 4 retry
attempts, including each OpenCode adapter `runner-result.json` alongside the
result, config, and trajectory files.

The GPT-5.6 Luna job is published at `luna/`. It contains all 30 trials
across the Pi, Claude Code, and Codex harnesses. This job recorded no retry
attempts.

The completed OpenCode V2 GPT-5.6 Luna run is published separately at
`opencode-v2-luna/`. It contains the 10 trials and 4 retry attempts,
including each OpenCode adapter `runner-result.json` alongside the filtered
result, config, and trajectory files.

The DeepSeek V4.1 Flash directory `deepseek-v4p1-flash/` holds the canonical
Codex, Pi, and Claude Code trials, which come from four Pier jobs:

| Harness | Trials | Pier job |
| --- | --- | --- |
| Codex | 5 (katex, scriggo, fastapi, csstree, koota) | `deepseek-v4p1-flash` (the original job) |
| Codex | 5 (boa, effect-sse, expr, oxvg, statemachine) | `deepseek-codex-rerun` (#111) |
| Pi | 10 | `deepseek-pi-rerun` (complete rerun, #111) |
| Claude Code | 10 | `deepseek-claude-code-cliproxy-api-fixed-thinking` (CLIProxyAPI with `is-compat`, #102, #105) |

Each trial's `config.json` names its Pier job in `trials_dir`. The job-level
`result.json`, `config.json`, and `lock.json` in the directory belong to the
original job; those of the three rerun jobs are kept unchanged under
`.rerun-jobs/<pier job>/`. The original Pi trials, the budget-stopped Pi rerun,
and the two faulty Claude Code runs are not in this directory (see below).

The GLM-5.3-Flash directories `glm-5.3-flash/` (Pi, Claude Code, Codex) and
`opencode-v2-glm-5.3-flash/` hold the second Fireworks run on the fixed gateway,
Pier jobs `glm-5.3-flash-rerun` and `opencode-v2-glm-5.3-flash-rerun`
(2026-09-24, LiteLLM 1.102.1). They replace the first run completely, so their
job-level `result.json`, `config.json`, and `lock.json` are those of the rerun
jobs, and each trial's `config.json` names the rerun job in `trials_dir`. The
OpenCode V2 trials also include the adapter's `runner-result.json`. Neither job
recorded a retry attempt. The first run and the direct Z.AI snapshot are not in
these directories (see below).

## Observations outside the comparative data

`scripts/build_corrected_results.py` moves observations that must not enter
comparative results out of the job directories, so the normal
`<job>/*/result.json` glob never returns them:

- `.excluded/kimi-k3/claude-code/`: the Kimi K3 Claude Code run (effort lowered
  to `high`, #108).
- `.excluded/deepseek-v4p1-flash/pi-rerun-budget-crash/`: the first attempted
  rerun of three affected Pi trials. Every attempt ended when the gateway
  rejected further requests because the benchmark budget was exhausted
  (HTTP 429), so no task was finished. It ran under the Pier job name
  `deepseek-pi-rerun`, so its files name that directory; the raw run directory
  was later renamed to `deepseek-pi-rerun-budget-crash` so that the complete
  rerun could use the job name.
- `.excluded/deepseek-v4p1-flash/claude-code-litellm/`: the original DeepSeek
  Claude Code trials, routed through LiteLLM. LiteLLM lowered the requested
  `max` effort to `high` (#94), and the six trials before the gateway upgrade
  never gave the model its earlier reasoning (#111).
- `.excluded/deepseek-v4p1-flash/claude-code-cliproxy-default/`: the first
  CLIProxyAPI Claude Code run (Pier job `deepseek-claude-code-cliproxy-api`, with
  its job-level files). It kept `max` effort, but without `is-compat`
  CLIProxyAPI dropped Claude Code's earlier thinking blocks, so no trial gave
  the model its earlier reasoning (#102).
- `.superseded/issue-111/deepseek-v4p1-flash/codex/`: the five DeepSeek Codex
  trials affected by #111. Their reruns (Pier job `deepseek-codex-rerun`) are
  the canonical trials in `deepseek-v4p1-flash/`, published only after the
  same retention check showed that the reasoning reached the model.
- `.superseded/issue-111/deepseek-v4p1-flash/pi/`: all ten original DeepSeek Pi
  trials. Six of them ran before AiOrbit's LiteLLM upgrade and never gave the
  model its earlier reasoning (#111). The whole Pi run was repeated (Pier job
  `deepseek-pi-rerun`), including the four unaffected trials, so the canonical
  Pi condition comes from one run. The repetitions were published only after
  the same retention check.

- `.excluded/glm-5.3-flash/first-run/`: the first GLM-5.3-Flash run of Pi,
  Claude Code, and Codex (Pier job `glm-5.3-flash`, 2026-09-21, with its
  job-level files and two retry attempts). It ran entirely before AiOrbit's
  LiteLLM upgrade, and no measurable trial kept its earlier reasoning (#111).
- `.excluded/glm-5.3-flash/opencode-v2-first-run/`: the first GLM-5.3-Flash
  OpenCode V2 run (Pier job `opencode-v2-glm-5.3-flash`, 2026-09-18), with the
  same defect. It is published in Pier's layout and is not corrected: three
  timed-out trials left no session record from which to recover their totals.
- `.excluded/glm-5.3-flash/zai-direct/`: the incomplete direct Z.AI Coding Plan
  batch (Pier job `glm-5.3-sub`). It is an **unfiltered snapshot**: its 814
  files are copied from the run directory as-is, including retry attempts,
  logs, sessions, verifier output, and other run artifacts, and are not
  corrected.

The three DeepSeek Claude Code runs are compared with each other in the
results chapter to show what the route defects changed (#105), and the three
GLM-5.3-Flash runs likewise (#129). These two comparisons are the only analyses
that read `.excluded/`; no comparative result does. The
results chapter also compares the superseded Codex and Pi trials with their
repetitions to show what the missing reasoning changed; `.superseded/` is read
only there and in the gateway-defect evidence.

The moved files are unchanged, and each job's `corrections.json` lists every
moved trial with its location and reason. The evidence for #111 is in
`../litellm-reasoning-audit/`.

The analysis scripts can use a model directory directly. The Kimi K3 directory
contains twenty eligible Pi and Codex trials by default. They read one
canonical attempt per trial by default; add `--include-retries` to also count
the experimental overhead under `.retry-attempts/`:

```bash
python3 scripts/analyze_benchmark_costs.py \
  data/benchmark-results/kimi-k3
```

## Excluded Kimi K3 Claude Code run

The completed Kimi K3 Claude Code run is ineligible for the comparative
benchmark: LiteLLM lowered the requested `max` reasoning effort to `high` and
dropped earlier reasoning from subsequent model requests ([PA1 #108](https://github.com/FarisZR/PA1/issues/108)).
The ten trial directories are retained unchanged under
`data/benchmark-results/.excluded/kimi-k3/claude-code/`. Pier's original
30-trial job summary, which also includes these observations, is retained as
`.excluded/kimi-k3/pier-job-summary.json`. These locations are audit evidence,
not inputs for benchmark figures, success rates, token/cache totals, or costs.
The normal `kimi-k3/*/result.json` glob now contains exactly ten Pi and ten
Codex trials. `scripts/build_corrected_results.py` reapplies and validates this
layout during regeneration; its `corrections.json` manifest records the excluded
trial names, destination paths, and reason. To inspect the ineligible trials,
an analyst must deliberately read `.excluded/kimi-k3/claude-code/*/result.json`.
Do not modify their rewards or token counts to signal exclusion.

## Corrected files

Some recorded values are known to be wrong or missing (see "Measurement
corrections" in `chapters/_04-results.qmd`). `scripts/build_corrected_results.py`
writes the corrected values to the normal Pier file names and keeps Pier's
unmodified output beside them with an `.original.json` suffix:

```text
<trial>/result.json                       corrected where needed; use this
<trial>/result.original.json              unmodified Pier output (only where corrected)
<trial>/agent/trajectory.json             corrected where needed; use this
<trial>/agent/trajectory.original.json    unmodified Pier output (only where rebuilt)
<job>/corrections.json                    every change: field, original, corrected, source, reason
```

**Always read `result.json` and `agent/trajectory.json`.** Do not read
`*.original.json` for analysis or figures; those files only exist as recorded
evidence. Glob patterns such as `*/result.json` pick up the corrected files
automatically. The corrected files have exactly the same format as Pier's
output; only the corrected values differ.

Take token, cost, and context metrics from `result.json`. The Codex
trajectories are not corrected, so their `final_metrics` still contain Pier's
original context values; use trajectories only for step-level analysis.

| Correction | Files |
| --- | --- |
| Codex peak context and compaction count recomputed from per-call input tokens (upstream Pier counted output tokens) | `result.json` for all 58 Codex attempts |
| OpenCode V2 token and cost totals withheld by the adapter, taken from OpenCode's session records | `result.json` for 12 OpenCode V2 attempts |
| OpenCode V2 trajectories lost to the adapter's `splitlines()` reader, rebuilt offline | `agent/trajectory.json` for both Luna `effect-sse-httpapi-streaming` attempts |

The script validates every correction before writing it. The Codex counts must
equal the explicit compaction events in the raw rollouts. The OpenCode session
totals must equal Pier's totals on every attempt where Pier reported them.
Regeneration needs the raw run workspace (`benchmark/runs/`) and, for the
trajectory rebuild, the pinned Pier checkout:

```bash
python3 scripts/build_corrected_results.py --pier-python ~/pier/.venv/bin/python
```

## Canonical attempts

Every job allows Pier to repeat a trial once. The retry was meant for
transport/gateway faults, but Pier retries every exception type that is not
excluded, and a non-zero harness exit is always `NonZeroAgentExitCodeError`.
Eight OpenCode V2 trials were therefore retried after a failure of the model or harness
([PA1 #95](https://github.com/FarisZR/PA1/issues/95)). For those, the first
attempt is the observation, not Pier's final one. `scripts/build_corrected_results.py`
publishes it in the normal trial directory and moves Pier's retry to
`attempt-2`:

```text
<trial>/                                canonical attempt; use this
.retry-attempts/<trial>/attempt-1/      Pier discarded it after a transport fault (Pier's layout)
.retry-attempts/<trial>/attempt-2/      Pier's final trial after a model/harness failure (moved)
<job>/corrections.json                  attempt_selection: both attempts' paths, ids, rewards, and the reason
```

**Glob `*/result.json` for comparative results.** It returns exactly one
canonical attempt per trial, so success, token, cache, and cost figures describe
the same attempt. Everything under `.retry-attempts/` is experimental overhead:
it consumed budget but is not a model–harness observation. The moved files are
byte-identical to Pier's output; only their location changed.

| Job | Trials | Reason |
| --- | --- | --- |
| `opencode-v2-deepseek-v4p1-flash` | fastapi, katex, koota, scriggo | Session finished and passed, but OpenCode 2.0.8 kept exit status 1 after a recovered stream error |
| `opencode-v2-luna` | effect-sse, expr, katex, oxvg | Model asked for a Git identity through OpenCode's interactive `question` tool |

The Kimi K3 Pi, first-run GLM-5.3-Flash Codex, and DeepSeek V4.1 Flash Pi/Codex retries
followed transport or gateway faults (the DeepSeek ones are gateway
context-window rejections of trials now under `.superseded/`), so Pier's final
trial stays canonical there.

The job-level `<job>/result.json` is Pier's run summary for jobs other than Kimi.
Its `stats` block (rewards, errors, token totals) reflects Pier's final trials
and uncorrected values; do not use it for analysis. The Kimi summary is kept
under `.excluded/kimi-k3/pier-job-summary.json` because it contains the
ineligible Claude Code trials.

`.excluded/glm-5.3-flash/zai-direct/` and `.excluded/glm-5.3-flash/opencode-v2-first-run/` are not corrected.
