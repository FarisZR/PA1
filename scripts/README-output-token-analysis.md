# Benchmark output-token analysis

`analyze_output_tokens_before_tool.py` scans the normalized benchmark
trajectories under `benchmark/runs/` and finds the largest model response that
is followed by one or more tool calls.

The metric is taken directly from each ATIF trajectory entry:

```text
steps[*].source == "agent"
steps[*].tool_calls is non-empty
steps[*].metrics.completion_tokens
```

This is provider-reported completion usage. It includes reasoning tokens when
the provider includes them in its completion count, and it does not estimate
tokens from message text. Steps without a recorded `completion_tokens` value are
reported separately and excluded from maxima.

Run from the repository root:

```bash
python3 scripts/analyze_output_tokens_before_tool.py
```

Useful options:

```bash
# Show more of the largest responses.
python3 scripts/analyze_output_tokens_before_tool.py --top 50

# Exclude the retry attempts stored below .retry-attempts/.
python3 scripts/analyze_output_tokens_before_tool.py --exclude-retries

# Emit a machine-readable report and save it for later comparison.
python3 scripts/analyze_output_tokens_before_tool.py \
  --format json --output /tmp/output-token-report.json

# Analyze another run directory or one trajectory directly.
python3 scripts/analyze_output_tokens_before_tool.py benchmark/runs/kimi-k3
python3 scripts/analyze_output_tokens_before_tool.py \
  benchmark/runs/smoke-luna/anko-default-function-arguments__XUpEuFJ/agent/trajectory.json
```

The default scan includes retries because the question is about the highest
observed generation anywhere in the recorded benchmark data. Use
`--exclude-retries` when comparing one primary attempt per trial.
