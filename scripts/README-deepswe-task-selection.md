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

It writes the selected tasks, input SHA-256 hashes, selection audit data, the
trajectory hashes used for cache-aware repricing, and the cost projection to:

```text
data/deepswe_task_selection_v1.1.json
```

To reproduce the calculation from local copies instead of downloading them:

```bash
python3 scripts/select_deepswe_tasks.py \
  --tasks-json /path/to/tasks.json \
  --trials-json /path/to/trials.json \
  --release-json /path/to/release.json
```

Use `--harnesses N` to change the projected number of harnesses. PA1 currently
uses four. `--trajectory-cache-dir PATH` controls the local cache for immutable
public trajectory JSON files used by the cache-aware cost calculation.

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
