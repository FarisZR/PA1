#!/usr/bin/env python3
"""Download the researcher's per-request spend log from the AiOrbit LiteLLM gateway.

The log is private: it covers every request made with the researcher's gateway
user, including work outside the benchmark, and it names the API key aliases.
It is therefore written to the git-ignored ``benchmark/generated/`` directory.
``scripts/attribute_litellm_spend.py`` derives the published benchmark subset
from it.

LiteLLM's ``/spend/logs/v2`` endpoint returns at most 10,000 requests per
query window, so a day that reaches the cap is fetched again hour by hour. The
per-day totals of ``/user/daily/activity`` are stored alongside the log so that
its completeness can be checked.

Credentials are read from ``LITELLM_BASE_URL`` and ``LLM_PROXY_KEY`` and are
never printed or written.

Usage:
    python3 scripts/fetch_litellm_spend.py --start 2026-08-01 --end 2026-09-25
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT = Path("benchmark/generated/litellm-spend")
PAGE_SIZE = 1000
QUERY_CAP = 10_000
FIELDS = ["request_id", "startTime", "endTime", "model", "model_group", "custom_llm_provider",
          "prompt_tokens", "completion_tokens", "total_tokens", "spend"]


def get(base: str, key: str, path: str) -> Any:
    request = urllib.request.Request(f"{base}/{path}", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def fetch_window(base: str, key: str, start: dt.datetime, end: dt.datetime) -> list[dict[str, Any]]:
    """All requests that started in [start, end), following the pagination."""
    rows, page = [], 1
    while True:
        body = get(base, key, f"spend/logs/v2?start_date={start:%Y-%m-%d%%20%H:%M:%S}"
                              f"&end_date={end:%Y-%m-%d%%20%H:%M:%S}&page={page}&page_size={PAGE_SIZE}")
        rows.extend(body.get("data", []))
        if page >= (body.get("total_pages") or 1):
            return rows
        page += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=dt.date.fromisoformat, required=True, help="First day (UTC)")
    parser.add_argument("--end", type=dt.date.fromisoformat, required=True, help="Last day (UTC), inclusive")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    base = os.environ["LITELLM_BASE_URL"].rstrip("/")
    key = os.environ["LLM_PROXY_KEY"]

    requests: dict[str, dict[str, Any]] = {}
    day = args.start
    while day <= args.end:
        start = dt.datetime.combine(day, dt.time())
        windows = [(start, start + dt.timedelta(days=1))]
        if len(fetch_window(base, key, *windows[0])) >= QUERY_CAP:
            windows = [(start + dt.timedelta(hours=h), start + dt.timedelta(hours=h + 1)) for h in range(24)]
        for window in windows:
            rows = fetch_window(base, key, *window)
            if len(windows) > 1 and len(rows) >= QUERY_CAP:
                raise SystemExit(f"{window[0]:%Y-%m-%d %H}:00 reached the {QUERY_CAP}-request cap; the log would be incomplete")
            for row in rows:
                record = {field: row.get(field) for field in FIELDS}
                record["key_alias"] = (row.get("metadata") or {}).get("user_api_key_alias")
                requests[row["request_id"]] = record
        day += dt.timedelta(days=1)

    daily = get(base, key, f"user/daily/activity?start_date={args.start}&end_date={args.end}&page_size=1000")
    args.output.mkdir(parents=True, exist_ok=True)
    log_path = args.output / "requests.jsonl"
    with log_path.open("w") as handle:
        for record in sorted(requests.values(), key=lambda r: r["startTime"]):
            handle.write(json.dumps(record) + "\n")
    daily_totals = {r["date"]: {"requests": r["metrics"]["api_requests"], "spend": r["metrics"]["spend"]}
                    for r in daily["results"]}
    meta = {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "endpoint": "/spend/logs/v2 (per request), /user/daily/activity (per day)",
        "start": str(args.start), "end": str(args.end),
        "requests": len(requests),
        "spend_usd": round(sum(r["spend"] or 0 for r in requests.values()), 6),
        "daily_activity_spend_usd": round(sum(d["spend"] for d in daily_totals.values()), 6),
        "daily_activity": daily_totals,
        "requests_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
    }
    (args.output / "fetch-meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(f"{meta['requests']} requests, USD {meta['spend_usd']:.2f} "
          f"(daily activity: USD {meta['daily_activity_spend_usd']:.2f})")


if __name__ == "__main__":
    main()
