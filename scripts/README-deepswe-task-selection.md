# DeepSWE task selection for PA1

`select_deepswe_tasks.py` implements the deterministic DeepSWE v1.1 sampling
procedure used for the PA1 harness comparison.

## Selection rule

The script uses public DeepSWE v1.1 `mini-swe-agent` rollouts. Errored trials
are excluded. The reference panel also excludes `claude-opus-5`,
`gpt-5-6-luna`, `deepseek-v4-flash`, and `kimi-k3`, because these were the four
model configurations considered benchmark targets when the sample was frozen on
2026-08-18. Their own DeepSWE results therefore do not contribute to the solve
rates or token medians used to select tasks. PA1's final model matrix changed
later; this exclusion set is part of the frozen sampling procedure and should
not be read as the final evaluated model list.

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
results from the four model configurations excluded when the sample was frozen.
A remaining tie is resolved by task ID.

## Frozen reproduction

The PA1 task set was selected on 2026-08-18. For reproducibility, use:

```bash
python3 scripts/select_deepswe_tasks_frozen.py
```

The wrapper downloads the DeepSWE v1.1 task, trial, and release artifacts and
verifies their SHA-256 hashes against the exact inputs used for PA1 before
running `select_deepswe_tasks.py`. If an upstream artifact changes, it fails
instead of silently selecting a different task set. Verified inputs are cached
under `.cache/deepswe-selection-v1.1` by default.

The selected tasks and the same source hashes are committed in:

```text
data/deepswe_task_selection_v1.1.json
```

These hashes freeze the public sampling artifacts. They do not identify the
DeepSWE Git checkout used later to execute the selected tasks; the runnable
checkout is pinned separately in `benchmark/README.md` and in the run metadata.

The underlying selection script can still be run directly for development or
against explicitly supplied local artifacts, but such a run is not the frozen
PA1 reproduction path.

## Direct/scripted use

To run the selection logic against the current public v1.1 endpoints:

```bash
python3 scripts/select_deepswe_tasks.py
```

To run it against local copies:

```bash
python3 scripts/select_deepswe_tasks.py \
  --tasks-json /path/to/tasks.json \
  --trials-json /path/to/trials.json \
  --release-json /path/to/release.json
```

The script writes the selected tasks, input SHA-256 hashes, selection audit data,
the trajectory hashes used for cache-aware repricing, and the cost projection to
`data/deepswe_task_selection_v1.1.json`.

Use `--harnesses N` to change the projected number of harnesses. PA1 currently
uses four. `--trajectory-cache-dir PATH` controls the local cache for public
trajectory JSON files used by the cache-aware cost calculation.

## Cost estimate

The cost section is a **budget estimate**, not an experimental result. It does
not trust DeepSWE's precomputed `cost_usd` values. Instead, the script reprices
the raw token/cache usage of the four public rollouts for each selected task and
exact target configuration, averages those four costs, and projects one run per
task and PA1 harness.

The frozen pricing table currently uses:

- **Claude Opus 5 medium:** Anthropic API pricing. Per-request DeepSWE
  trajectories expose uncached input, 5-minute and 1-hour cache creation, cache
  reads, and output tokens, so all cache tiers are repriced explicitly.
- **GPT-5.6 Luna max:** current OpenAI reference pricing. Per-request prompt,
  cached-input, and output counts are used so the >272K-token long-context
  multipliers are applied to the correct requests. The historical DeepSWE
  trajectories predate the current `cache_write_tokens` field, so the generated
  budget reports both the observed-data estimate and a conservative upper bound
  that treats every otherwise-unlabelled uncached input token as a cache write.
- **DeepSeek V4 Flash max:** Fireworks pricing, using raw uncached input, cached
  input, and output counts.
- **Kimi K3 max:** Fireworks pricing, using raw uncached input, cached input, and
  output counts.

All price constants and source URLs are explicit in `PRICING` in the script and
are emitted into the generated JSON. This prevents a provider price change from
silently changing a previously documented budget. Update the table deliberately
when PA1's actual provider pricing changes.
