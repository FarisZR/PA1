# Filtered benchmark results

This directory contains the committed, filtered evidence used for PA1 analysis
of the primary 30-task jobs for Kimi K3, GLM 5.3 Flash, and DeepSeek V4.1 Flash.
The directory layout mirrors the corresponding job directories under the local
`benchmark/runs/` directory, but it is a publication copy rather than a run
workspace.

Each model directory contains:

- the job-level `result.json`, `config.json`, and `lock.json`;
- each final task's `result.json` and `config.json`; and
- each final task's structured `agent/trajectory.json`.

Pier names both the job-level and task-level result files `result.json`; there
are no `results.json` or `task.json` files in these outputs. Retry attempts,
request/proxy logs, verifier logs, agent console logs, session JSONL, sandbox
contents, and generated binaries are intentionally excluded. The raw run
workspace remains under `benchmark/runs/` and is ignored by Git.

The analysis scripts can use a model directory directly, for example:

```bash
python3 scripts/analyze_benchmark_costs.py \
  data/benchmark-results/kimi-k3 --exclude-retries
```
