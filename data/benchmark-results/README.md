# Filtered benchmark results

This directory contains the committed, filtered evidence used for PA1 analysis
of the primary 30-task jobs for Kimi K3, GLM 5.3 Flash, DeepSeek V4.1 Flash, and
GPT-5.6 Luna.
The directory layout mirrors the corresponding job directories under the local
`benchmark/runs/` directory, but it is a publication copy rather than a run
workspace: every trial directory holds the canonical attempt (see "Canonical
attempts" below).

Each model directory contains:

- the job-level `result.json`, `config.json`, and `lock.json`;
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

The analysis scripts can use a model directory directly. They read one
canonical attempt per trial by default; add `--include-retries` to also count
the experimental overhead under `.retry-attempts/`:

```bash
python3 scripts/analyze_benchmark_costs.py \
  data/benchmark-results/kimi-k3
```

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

The job-level `<job>/result.json` is Pier's run summary. Its `stats` block
(rewards, errors, token totals) reflects Pier's final trials and uncorrected
values; do not use it for analysis.

`glm-5.3-sub/` is not corrected.
