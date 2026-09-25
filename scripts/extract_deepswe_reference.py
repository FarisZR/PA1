#!/usr/bin/env python3
"""Extract DeepSWE's public mini-swe-agent rollouts for the PA1 tasks and models.

DeepSWE publishes every rollout of its reference harness `mini-swe-agent` in
``trials.json``. The discussion chapter uses the rollouts of the models that
were also benchmarked here, at the same `max` reasoning effort, as a
minimal-harness reference. DeepSWE has no DeepSeek V4.1 Flash rollouts; its
DeepSeek V4 Flash rollouts are kept for completeness but are a different
checkpoint and are not used as a reference.

The file changes as DeepSWE adds rollouts, so its download time and SHA-256
hash are recorded next to the extract.

Usage:
    python3 scripts/extract_deepswe_reference.py            # download trials.json
    python3 scripts/extract_deepswe_reference.py --trials-json trials.json --archive-url URL

The Wayback Machine serves the snapshot gzip-compressed; decompress it before
passing it with --trials-json.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import urllib.request
from pathlib import Path

TRIALS_URL = "https://deepswe.datacurve.ai/artifacts/v1.1/trials.json"
TASKS_FILE = Path("data/deepswe_task_selection_v1.1.json")
OUTPUT = Path("data/deepswe-reference")
MODELS = {
    "gpt-5-6-luna": "gpt-5.6-luna",
    "kimi-k3": "kimi-k3",
    "glm-5-3-flash": "glm-5p3-flash",
    "deepseek-v4-flash": "deepseek-v4-flash (not V4.1)",
}
FIELDS = ["task_name", "model", "pa1_model", "reasoning_effort", "provider", "passed", "n_agent_steps",
          "n_input_tokens", "n_cache_tokens", "n_output_tokens", "agent_duration_seconds", "started_at"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trials-json", type=Path, help="Use a downloaded trials.json instead of fetching it")
    parser.add_argument("--archive-url", help="Wayback Machine snapshot of the same trials.json, recorded as provenance")
    args = parser.parse_args()
    if args.trials_json:
        raw = args.trials_json.read_bytes()
    else:
        request = urllib.request.Request(TRIALS_URL, headers={"User-Agent": "PA1-DeepSWE-reference/1"})
        with urllib.request.urlopen(request, timeout=300) as response:
            raw = response.read()
    tasks = {task["task_id"] for task in json.loads(TASKS_FILE.read_text(encoding="utf-8"))["selected_tasks"]}
    rows = [
        {**{field: trial.get(field) for field in FIELDS}, "pa1_model": MODELS[trial["model"]]}
        for trial in json.loads(raw)["rows"]
        if trial.get("harness") == "mini-swe-agent" and trial.get("model") in MODELS
        and trial.get("reasoning_effort") == "max" and trial.get("task_name") in tasks
        and trial.get("included_in_score")
    ]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / "mini-swe-agent-rollouts.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda r: (r["model"], r["task_name"], r["started_at"] or "")))
    (OUTPUT / "source.json").write_text(json.dumps({
        "url": TRIALS_URL,
        "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "trials_json_sha256": hashlib.sha256(raw).hexdigest(),
        "archive_url": args.archive_url,
        "rollouts": len(rows),
    }, indent=2) + "\n", encoding="utf-8")
    print(f"{len(rows)} rollouts")


if __name__ == "__main__":
    main()
