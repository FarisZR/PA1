# Filtered benchmark results

This directory contains the committed, filtered evidence used for PA1 analysis
of the primary jobs for Kimi K3, GLM 5.3 Flash, DeepSeek V4.1 Flash, and
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

The completed DeepSeek Claude Code CLIProxy API run is published separately at
`deepseek-claude-code-cliproxy-api/`. It contains the 10 trials with the job
metadata, task results/configs, and structured trajectories. The completed
fixed-thinking rerun is published separately at
`deepseek-claude-code-cliproxy-api-fixed-thinking/` with the same filtered
artifact set.

The current GLM 5.3 Flash sub-run is published as an **unfiltered snapshot**
at `glm-5.3-sub/`. Its 814 files are copied from the run directory as-is,
including retry attempts, logs, sessions, verifier output, and other run
artifacts. Unlike the other data directories, no files were selected,
renamed, redacted, or removed from this snapshot.

## Observations outside the comparative data

`scripts/build_corrected_results.py` moves observations that must not enter
comparative results out of the job directories, so the normal
`<job>/*/result.json` glob never returns them:

- `.excluded/kimi-k3/claude-code/`: the Kimi K3 Claude Code run (effort lowered
  to `high`, #108).
- `.excluded/deepseek-v4p1-flash/pi/`: all ten DeepSeek V4.1 Flash Pi trials.
  Six of them ran before AiOrbit's LiteLLM upgrade and never gave the model its
  earlier reasoning (#111), and they are not rerun. A condition with four valid
  trials cannot be compared with complete ten-task conditions.
- `.superseded/issue-111/deepseek-v4p1-flash/codex/`: the five DeepSeek Codex
  trials affected by #111. The rerun job `deepseek-codex-rerun` replaces them;
  once it has run, the same script publishes its trials in
  `deepseek-v4p1-flash/` after checking that the reasoning reached the model.

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
| Codex peak context and compaction count recomputed from per-call input tokens (upstream Pier counted output tokens) | `result.json` for all 43 Codex attempts |
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

The Kimi K3 Pi, GLM-5.3-Flash Codex, and DeepSeek V4.1 Flash Pi/Codex retries
followed transport or gateway faults (the DeepSeek ones are gateway
context-window rejections), so Pier's final trial stays canonical there.

The job-level `<job>/result.json` is Pier's run summary for jobs other than Kimi.
Its `stats` block (rewards, errors, token totals) reflects Pier's final trials
and uncorrected values; do not use it for analysis. The Kimi summary is kept
under `.excluded/kimi-k3/pier-job-summary.json` because it contains the
ineligible Claude Code trials.

`glm-5.3-sub/` is not corrected.
