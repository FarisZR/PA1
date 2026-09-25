# DeepSWE mini-swe-agent reference

DeepSWE's public rollouts of its reference harness `mini-swe-agent` on the ten
PA1 tasks, for the models that were also benchmarked here, at `max` reasoning
effort. The discussion chapter uses them as a minimal-harness reference.

| File | Content | Produced by |
| --- | --- | --- |
| `mini-swe-agent-rollouts.csv` | One row per rollout: task, model, provider, pass/fail, agent steps, tokens, duration, start time | `scripts/extract_deepswe_reference.py` |
| `source.json` | Source URL, Wayback Machine snapshot, SHA-256 hash of `trials.json`, rollout count | `scripts/extract_deepswe_reference.py` |

DeepSWE ran each task four times per model (GLM-5.3-Flash has three rollouts
on `koota-composite-trait-aspects`). The rollouts are not grouped into runs, so
the chapter derives the range of possible single-run scores from the per-task
solve rates instead of a best and a worst run.

DeepSWE's providers differ from the routes used in this study: GPT-5.6 Luna
ran through OpenAI, Kimi K3 through Moonshot, and GLM-5.3-Flash through a
provider DeepSWE labels `anthropic`. DeepSWE has no DeepSeek V4.1 Flash
rollouts; the DeepSeek V4 Flash rows are a different checkpoint and are not
used as a reference.

`trials.json` has changed since the task selection of 2026-08-18
(`data/deepswe_task_selection_v1.1.json` records the earlier hash). The
GLM-5.3-Flash rollouts started on 2026-08-26 and therefore did not contribute
to the task selection.

The extract was generated from the Wayback Machine snapshot
<https://web.archive.org/web/20260925194228/https://deepswe.datacurve.ai/artifacts/v1.1/trials.json>,
whose decompressed content has the SHA-256 hash recorded in `source.json`.
