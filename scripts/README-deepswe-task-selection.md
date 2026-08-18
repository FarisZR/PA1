# DeepSWE task selection for PA1

`select_deepswe_tasks.py` implements the deterministic DeepSWE v1.1 sampling
procedure used for the PA1 harness comparison.

## Selection rule

The script uses public DeepSWE v1.1 `mini-swe-agent` rollouts. Errored trials
and the four models evaluated by PA1 (`claude-opus-5`, `gpt-5-6-luna`,
`deepseek-v4-flash`, and `kimi-k3`) are excluded from the reference panel used
for task selection.

For each programming language independently, tasks are sorted from lowest to
highest solve rate. The hardest task receives difficulty percentile 100 and the
easiest percentile 0. Solve-rate ties are ordered by DeepSWE task ID.

- **Hard:** difficulty percentile 75 through 100, inclusive.
- **Medium:** difficulty percentile 50 through 75, with 75 excluded so the
  strata cannot overlap.

Within each language and stratum, the task with the highest **median historical
total token count** (`n_input_tokens + n_output_tokens`) is selected. This
secondary criterion deliberately selects token-intensive tasks because PA1 is
testing how different harnesses handle demanding workloads. It does not use
results from any of the four evaluated models. A remaining tie is resolved by
task ID.

## Run it

From the repository root:

```bash
python3 scripts/select_deepswe_tasks.py
```

The script downloads these frozen-version public artifacts directly:

```text
https://deepswe.datacurve.ai/artifacts/v1.1/tasks.json
https://deepswe.datacurve.ai/artifacts/v1.1/trials.json
```

It writes the selected tasks, input SHA-256 hashes, selection audit data, and
cost projection to:

```text
data/deepswe_task_selection_v1.1.json
```

To reproduce the calculation from local copies instead of downloading them:

```bash
python3 scripts/select_deepswe_tasks.py \
  --tasks-json /path/to/tasks.json \
  --trials-json /path/to/trials.json
```

Use `--harnesses N` to change the projected number of harnesses. PA1 currently
uses four.

## Cost estimate

The cost section is a **budget estimate**, not an experimental result. For each
selected task it averages the public DeepSWE rollout cost of the exact target
configuration, then projects one run per task and PA1 harness.

- Claude Opus 5: `medium`, using the public DeepSWE `cost_usd` field.
- GPT-5.6 Luna: `max`, using the public cost with DeepSWE's v1.1 Luna `0.2`
  display adjustment.
- DeepSeek V4 Flash: `max`, recalculated from public token counts using the
  Fireworks serverless prices configured in the script.
- Kimi K3: `max`, using the public DeepSWE `cost_usd` field.

The Fireworks pricing constants are intentionally explicit in the script so a
provider price change cannot silently change a previously documented budget.
Update them deliberately if PA1's actual provider pricing changes.
