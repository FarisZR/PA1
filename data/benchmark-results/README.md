# Filtered benchmark results

This directory contains the committed, filtered evidence used for PA1 analysis
of the primary 30-task jobs for Kimi K3, GLM 5.3 Flash, DeepSeek V4.1 Flash, and
GPT-5.6 Luna.
The directory layout mirrors the corresponding job directories under the local
`benchmark/runs/` directory, but it is a publication copy rather than a run
workspace.

Each model directory contains:

- the job-level `result.json`, `config.json`, and `lock.json`;
- each final task's `result.json` and `config.json`; and
- each final task's structured `agent/trajectory.json`; and
- filtered retry attempts under `.retry-attempts/`, containing the retry
  `result.json`, `config.json`, and `agent/trajectory.json` files.

Pier names both the job-level and task-level result files `result.json`; there
are no `results.json` or `task.json` files in these outputs. Retry attempts are
included in the same filtered form as final tasks. Request/proxy logs, verifier
logs, agent console logs, session JSONL, sandbox contents, and generated
binaries remain intentionally excluded. The raw run workspace remains under
`benchmark/runs/` and is ignored by Git.

The DeepSeek V4.1 Flash OpenCode V2 job is published separately at
`opencode-v2-deepseek-v4p1-flash/`; it is a separate run from the primary
`deepseek-v4p1-flash/` job. It contains the 10 final tasks and 4 retry
attempts, including each OpenCode adapter `runner-result.json` alongside the
result, config, and trajectory files.

The GPT-5.6 Luna job is published at `luna/`. It contains all 30 final tasks
across the Pi, Claude Code, and Codex harnesses. This job recorded no retry
attempts.

The completed OpenCode V2 GPT-5.6 Luna run is published separately at
`opencode-v2-luna/`. It contains the 10 final tasks and 4 retry attempts,
including each OpenCode adapter `runner-result.json` alongside the filtered
result, config, and trajectory files.

The current GLM 5.3 Flash sub-run is published as an **unfiltered snapshot**
at `glm-5.3-sub/`. Its 814 files are copied from the run directory as-is,
including retry attempts, logs, sessions, verifier output, and other run
artifacts. Unlike the other data directories, no files were selected,
renamed, redacted, or removed from this snapshot.

## Primary-attempt selection

The published directory structure preserves what Pier recorded: normal trial
directories contain Pier's final attempts, while discarded attempts remain
under `.retry-attempts/`. Directory location alone therefore does not define
the canonical observation used by the comparative analysis.

`primary-attempt-overrides.json` is a sparse override manifest. Pier's final
trial is the default. Only observations for which Pier repeated a trial for a
non-transport cause are listed; those entries select the earlier recorded
attempt. Jobs and observations without an override are parsed exactly as Pier
published them.

Paper analysis must use `scripts/benchmark_results.py` as the selection layer:

```python
from pathlib import Path
from scripts.benchmark_results import iter_primary_trials

for trial_dir, result in iter_primary_trials(
    Path("data/benchmark-results/opencode-v2-luna")
):
    ...
```

The loader validates that every override points to an existing attempt with the
same job, harness, and task, rejects duplicate overrides, and reads the normal
`result.json`, which already contains any measurement corrections described
below. Validate the complete manifest with:

```bash
python3 scripts/benchmark_results.py
```

Do not move retry files into normal trial directories. Their location is part
of the preserved execution evidence; selection is expressed only through the
sparse manifest.

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

`glm-5.3-sub/` is not corrected.
