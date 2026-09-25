# Billed LiteLLM spend (AiOrbit)

Billed spend of every request the researcher's AiOrbit gateway user made from
2026-08-01 to 2026-09-25, attributed to benchmark trial attempts. The
discussion chapter uses it for the cost of the gateway defects.

| File | Content | Produced by |
| --- | --- | --- |
| `summary.json` | Total spend, spend per group and per attempt status, unassigned benchmark spend, and non-benchmark spend per model | `scripts/attribute_litellm_spend.py` |
| `attempt-spend.csv` | Requests, token-matched requests, and billed spend per trial attempt | `scripts/attribute_litellm_spend.py` |

## Source

`scripts/fetch_litellm_spend.py` downloads the per-request log from LiteLLM's
`/spend/logs/v2` endpoint and the per-day totals from `/user/daily/activity`.
The endpoint returns at most 10,000 requests per query, so days that reach the
cap are fetched hour by hour. The fetched log has 78,943 requests and USD
977.05, against USD 976.68 in the daily totals; the fetch time and the
SHA-256 hash of the log are recorded in `summary.json`.

## Attribution

A request belongs to a trial attempt when it used the attempt's model, started
during the attempt's agent execution (plus two minutes on each side), and has
the same prompt and completion token counts as one of the attempt's trajectory
steps. Requests in a window without such a match are attributed by time when all
overlapping attempts have the same status; the rest remain "unassigned
benchmark". Requests outside every trial window are not benchmark traffic.
`summary.json` lists how much spend each method attributed.

The status of an attempt comes from `data/benchmark-results/` (canonical,
excluded, superseded, or retry). Attempts that exist only in the raw run
workspace are "not published". The group "lost to gateway defects" contains the
statuses listed in `defect_statuses`: the runs excluded or superseded because
of the LiteLLM defects (#94, #102, #111) and the aborted first start of the
first GLM-5.3-Flash run (raw job `glm-5.3-flash-2026-09-20`, before the gateway
upgrade).

## Privacy

The repository is public, and the gateway user was also used for work outside
the benchmark. The request log therefore stays in the git-ignored
`benchmark/generated/litellm-spend/`, together with the per-request
attribution. Only per-attempt and per-model totals are published; they contain
no timestamps of non-benchmark requests, no API keys, key hashes, or key
aliases, and no request contents. The attribution also needs the raw run
workspace (`benchmark/runs/`), which is not in git.
