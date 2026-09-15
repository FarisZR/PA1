# DeepSWE task selection for PA1

`select_deepswe_tasks.py` implements the deterministic DeepSWE v1.1 sampling
procedure used for the PA1 harness comparison.

## Selection rule

The script uses public DeepSWE v1.1 `mini-swe-agent` rollouts. Errored trials
are excluded. The reference panel also excludes `claude-opus-5`,
`gpt-5-6-luna`, `deepseek-v4-flash`, and `kimi-k3`, because these were the four
model configurations considered benchmark targets when the sample was selected
on 2026-08-18. Their own DeepSWE results therefore do not contribute to the solve
rates or token medians used to select tasks. PA1's final model matrix changed
later; this exclusion set is part of the historical sampling procedure and
should not be read as the final evaluated model list.

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
results from the four model configurations excluded when the sample was
selected. A remaining tie is resolved by task ID.

## Recorded selection

The PA1 task set was selected on 2026-08-18 by running
`select_deepswe_tasks.py` against the then-current public DeepSWE v1.1 API
artifacts.

The resulting fixed task set is committed in:

```text
data/deepswe_task_selection_v1.1.json
```

That file also records SHA-256 hashes of the exact `tasks.json`, `trials.json`,
and `release.json` responses used at selection time. These hashes are retained
as provenance for the historical sampling inputs. Reproducing the PA1 benchmark
should use the committed task IDs rather than rerun selection against mutable
public API data.

The API-response hashes are independent of the DeepSWE Git revision used later
to execute the selected tasks. The runnable checkout is pinned separately in
`benchmark/README.md` and in the run metadata.

## Scripted use

To run the same selection logic against the current public v1.1 endpoints:

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

Because the public API data can change, a new run is not expected to reproduce
the historical 2026-08-18 task set unless the same source artifacts are supplied.
The committed JSON remains the authoritative task selection for PA1.

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
