#!/usr/bin/env python3
"""Attribute the billed LiteLLM spend to benchmark trial attempts.

Input is the private request log written by ``scripts/fetch_litellm_spend.py``
together with the raw run workspace ``benchmark/runs/`` (trial timing and
trajectories) and the publication layout of ``data/benchmark-results/`` (which
attempt is canonical, excluded, superseded, or a retry).

A request belongs to an attempt when it used the attempt's model, started within
the attempt's agent execution (plus two minutes on each side), and has the same
``(prompt_tokens, completion_tokens)`` as one of the attempt's trajectory steps.
Requests inside a window without such a match are attributed by time when every
overlapping attempt has the same status. The rest of the in-window requests stay
"unassigned benchmark"; requests outside every window are not benchmark traffic.

Only aggregates are published, because the repository is public and the log
also records the researcher's work outside the benchmark:

- ``data/litellm-spend/attempt-spend.csv``: requests and spend per trial attempt;
- ``data/litellm-spend/summary.json``: totals per group and status, and the
  non-benchmark spend per model without timestamps.

The per-request attribution is written next to the private log.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any

PRIVATE = Path("benchmark/generated/litellm-spend")
RUNS = Path("benchmark/runs")
RESULTS = Path("data/benchmark-results")
OUTPUT = Path("data/litellm-spend")
WINDOW_MARGIN_S = 120
MODEL_KEYS = ("kimi-k3", "glm-5p3-flash", "deepseek-v4p1-flash", "deepseek-v4-flash", "gpt-5.6-luna", "gpt-5.6-sol")

# Attempts that became unusable because of the AiOrbit LiteLLM defects (#94, #102, #111).
# The raw job `glm-5.3-flash-2026-09-20` is the aborted first start of the first GLM run;
# it ran before the gateway upgrade and was never published.
DEFECT_STATUSES = {
    "excluded: glm-5.3-flash/first-run",
    "excluded: glm-5.3-flash/opencode-v2-first-run",
    "excluded: kimi-k3/claude-code",
    "excluded: deepseek-v4p1-flash/claude-code-litellm",
    "superseded: deepseek-v4p1-flash/pi",
    "superseded: deepseek-v4p1-flash/codex",
    "not published: glm-5.3-flash-2026-09-20",
}


def timestamp(value: str) -> float:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def model_key(name: str | None) -> str | None:
    name = (name or "").lower().replace("5.3", "5p3").replace("v4.1", "v4p1")
    return next((key for key in MODEL_KEYS if key in name), None)


def result_files(root: Path):
    """Every result.json below root, including hidden directories such as `.excluded/`."""
    for directory, _, files in os.walk(root):
        if "result.json" in files:
            yield Path(directory) / "result.json"


def publication_status() -> dict[tuple[str, bool], str]:
    status = {}
    for path in result_files(RESULTS):
        result = json.loads(path.read_text())
        if "trial_name" not in result:
            continue
        parts = path.relative_to(RESULTS).parts
        retry = ".retry-attempts" in parts
        if parts[0] == ".excluded":
            label = "excluded: " + "/".join(parts[1:3])
        elif parts[0] == ".superseded":
            label = "superseded: " + "/".join(parts[2:4])
        elif retry:
            label = "retry (non-canonical)"
        else:
            label = "canonical"
        status[(result["trial_name"], retry)] = label
    return status


def load_attempts() -> list[dict[str, Any]]:
    status = publication_status()
    attempts = []
    for path in result_files(RUNS):
        result = json.loads(path.read_text())
        if "trial_name" not in result:
            continue
        execution = result.get("agent_execution") or {}
        start = execution.get("started_at") or result.get("started_at")
        end = execution.get("finished_at") or result.get("finished_at")
        if not start or not end:
            continue
        steps = collections.Counter()
        trajectory = path.parent / "agent" / "trajectory.json"
        if trajectory.exists():
            for step in json.loads(trajectory.read_text()).get("steps", []):
                metrics = step.get("metrics") or {}
                if metrics.get("prompt_tokens"):
                    steps[(metrics["prompt_tokens"], metrics.get("completion_tokens") or 0)] += 1
        job = path.relative_to(RUNS).parts[0]
        retry = ".retry-attempts" in path.parts
        attempts.append({
            "job": job, "trial": result["trial_name"], "harness": result["config"]["agent"]["name"],
            "model": model_key(result["config"]["agent"].get("model_name")), "retry": retry,
            "start": timestamp(start) - WINDOW_MARGIN_S, "end": timestamp(end) + WINDOW_MARGIN_S,
            "steps": steps, "status": status.get((result["trial_name"], retry), f"not published: {job}"),
            "requests": 0, "spend": 0.0, "exact": 0,
        })
    return attempts


def group_of(status: str) -> str:
    if status == "canonical":
        return "canonical"
    if status in DEFECT_STATUSES:
        return "lost to gateway defects"
    return "other benchmark overhead"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--private", type=Path, default=PRIVATE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    log_path = args.private / "requests.jsonl"
    requests = sorted((json.loads(line) for line in log_path.open()), key=lambda r: r["startTime"])
    attempts = load_attempts()
    by_model = collections.defaultdict(list)
    for attempt in attempts:
        by_model[attempt["model"]].append(attempt)

    groups: dict[str, float] = collections.Counter()
    statuses: dict[str, float] = collections.Counter()
    methods: dict[str, float] = collections.Counter()
    unassigned: dict[str, float] = collections.Counter()
    outside: dict[str, float] = collections.Counter()
    rows = []
    for request in requests:
        spend = request["spend"] or 0.0
        moment = timestamp(request["startTime"])
        model = model_key(request["model"]) or model_key(request.get("model_group"))
        overlapping = [a for a in by_model.get(model, []) if a["start"] <= moment <= a["end"]]
        tokens = (request.get("prompt_tokens") or 0, request.get("completion_tokens") or 0)
        exact = [a for a in overlapping if a["steps"][tokens] > 0]
        candidates = {a["status"] for a in overlapping}
        if exact:
            attempt, method = exact[0], "token match"
            attempt["steps"][tokens] -= 1
            attempt["exact"] += 1
        elif len(candidates) == 1:
            attempt, method = overlapping[0], "time window"
        else:
            attempt, method = None, "unassigned" if overlapping else "outside benchmark"
        if attempt:
            attempt["requests"] += 1
            attempt["spend"] += spend
            groups[group_of(attempt["status"])] += spend
            statuses[attempt["status"]] += spend
        elif overlapping and candidates <= DEFECT_STATUSES:
            groups["lost to gateway defects"] += spend
            statuses["overlapping defect-affected attempts"] += spend
            method = "time window"
        elif overlapping:
            groups["unassigned benchmark"] += spend
            unassigned[" | ".join(sorted(candidates))] += spend
        else:
            groups["outside benchmark"] += spend
            outside[(request["model"] or "").split("/")[-1]] += spend
        methods[method] += spend
        rows.append((request["request_id"], request["startTime"], request["model"], method,
                     attempt["job"] if attempt else "", attempt["trial"] if attempt else ""))

    with (args.private / "attribution.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["request_id", "start_time", "model", "method", "job", "trial"])
        writer.writerows(rows)

    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "attempt-spend.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["job", "trial", "harness", "model", "retry_attempt", "status", "group",
                         "requests", "token_matched_requests", "spend_usd"])
        for a in sorted(attempts, key=lambda a: (a["job"], a["trial"], a["retry"])):
            if a["requests"]:
                writer.writerow([a["job"], a["trial"], a["harness"], a["model"], a["retry"], a["status"],
                                 group_of(a["status"]), a["requests"], a["exact"], f"{a['spend']:.6f}"])

    meta = json.loads((args.private / "fetch-meta.json").read_text())
    rounded = lambda counter: {k: round(v, 2) for k, v in sorted(counter.items(), key=lambda kv: -kv[1])}
    summary = {
        "source": {
            "gateway": "AiOrbit (LiteLLM), researcher's user, all API keys",
            "endpoint": meta["endpoint"], "fetched_at": meta["fetched_at"],
            "period": [meta["start"], meta["end"]], "requests": meta["requests"],
            "private_log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
            "daily_activity_spend_usd": round(meta["daily_activity_spend_usd"], 2),
        },
        "total_spend_usd": round(sum(r["spend"] or 0 for r in requests), 2),
        "by_group": rounded(groups),
        "by_status": rounded(statuses),
        "unassigned_benchmark": rounded(unassigned),
        "outside_benchmark_by_model": rounded(outside),
        "by_method": rounded(methods),
        "defect_statuses": sorted(DEFECT_STATUSES),
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in ("total_spend_usd", "by_group", "by_method")}, indent=2))


if __name__ == "__main__":
    main()
