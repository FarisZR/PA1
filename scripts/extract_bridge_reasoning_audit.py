#!/usr/bin/env python3
"""Derive a publishable audit of DeepSeek Codex requests from the bridge logs (issue #111).

CLIProxyAPI's request logs (``benchmark/generated/cliproxy-logs/``) contain
complete prompts and credentials and are not published. This script keeps only
per-request metadata: time, LiteLLM version reported by AiOrbit, the prompt
and cached token counts returned by Fireworks, how many earlier assistant
messages carried ``reasoning_content`` in the body sent upstream, the reasoning
characters sent and streamed back, and the SHA-256 of the source log file.

    python3 scripts/extract_bridge_reasoning_audit.py \
        --out data/litellm-reasoning-audit/bridge-requests.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "benchmark" / "generated" / "cliproxy-logs"
PUBLISHED = ROOT / "data" / "benchmark-results"
MODEL = "deepseek-v4p1-flash"
# Log names use the bridge's UTC+8 clock; this covers the whole DeepSeek Codex job.
NAME_WINDOW = ("2026-09-20T000000", "2026-09-22T000000")
FIELDS = [
    "request_time_utc", "codex_session", "trial", "litellm_version", "status",
    "prompt_tokens", "cached_tokens", "assistant_messages",
    "assistant_messages_with_reasoning", "reasoning_chars_sent",
    "reasoning_chars_streamed", "log_file", "log_sha256",
]


def utc(stamp: str) -> str:
    """Convert the log's nanosecond, UTC+8 timestamp to ISO UTC."""
    head, _, rest = stamp.partition(".")
    fraction, offset = rest[:-6], rest[-6:]
    parsed = datetime.fromisoformat(f"{head}.{fraction[:6]}{offset}")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def audit(path: Path) -> dict | None:
    section = None
    request = upstream = usage = None
    request_time = version = status = None
    streamed = 0
    expect_body = False
    with path.open(errors="replace") as handle:
        for line in handle:
            if line.startswith("=== "):
                section = line.strip("= \n")
                expect_body = False
                continue
            if section == "REQUEST INFO" and line.startswith("Timestamp:"):
                request_time = utc(line.split(maxsplit=1)[1].strip())
            elif section == "REQUEST BODY" and request is None and line.startswith("{"):
                request = json.loads(line)
                if request.get("model") != MODEL:
                    return None
            elif section == "API REQUEST 1":
                if line.startswith("Body:"):
                    expect_body = True
                elif expect_body and line.startswith("{"):
                    upstream = json.loads(line)
                    expect_body = False
            elif section == "API RESPONSE 1":
                if line.startswith("Status:"):
                    status = line.split()[1]
                elif line.startswith("X-Litellm-Version:"):
                    version = line.split()[1]
                elif line.startswith("data: {"):
                    try:
                        chunk = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        streamed += len((choice.get("delta") or {}).get("reasoning_content") or "")
            elif section == "RESPONSE":
                break
    if request is None or upstream is None:
        return None
    assistant = [m for m in upstream.get("messages") or [] if m.get("role") == "assistant"]
    with_reasoning = [m for m in assistant if (m.get("reasoning_content") or "").strip()]
    usage = usage or {}
    return {
        "request_time_utc": request_time,
        "codex_session": (request.get("client_metadata") or {}).get("session_id"),
        "litellm_version": version,
        "status": status,
        "prompt_tokens": usage.get("prompt_tokens"),
        "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
        "assistant_messages": len(assistant),
        "assistant_messages_with_reasoning": len(with_reasoning),
        "reasoning_chars_sent": sum(len(m["reasoning_content"]) for m in with_reasoning),
        "reasoning_chars_streamed": streamed,
        "log_file": path.name,
        "log_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def session_trials() -> dict[str, str]:
    """Map Codex session ids to published trial paths."""
    mapping = {}
    for trajectory in PUBLISHED.glob("**/agent/trajectory.json"):
        if "deepseek-v4p1-flash" not in trajectory.as_posix():
            continue
        data = json.loads(trajectory.read_text())
        if (data.get("agent") or {}).get("name") == "codex":
            mapping[data["session_id"]] = str(trajectory.parent.parent.relative_to(PUBLISHED))
    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    files = sorted(
        p for p in LOGS.glob("v1-responses-*.log")
        if NAME_WINDOW[0] <= p.name[len("v1-responses-"):][:17] < NAME_WINDOW[1]
    )
    with Pool() as pool:
        rows = [row for row in pool.imap(audit, files, chunksize=16) if row]
    trials = session_trials()
    for row in rows:
        row["trial"] = trials.get(row["codex_session"], "")
    rows.sort(key=lambda row: row["request_time_utc"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} DeepSeek Codex requests -> {args.out}")


if __name__ == "__main__":
    main()
