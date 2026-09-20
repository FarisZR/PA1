#!/usr/bin/env python3
"""Report Fireworks rate-limit headroom for a model, and the concurrency it allows.

Fireworks enforces token-per-minute limits per account and per model, and the
effective limit adapts with recent usage, so the headroom a job starts with is
not the headroom a previous job finished with. This reads the current effective
limits straight from the gateway and converts them into a trial count using
per-trial demand measured from a previous run.

Usage:
  python3 benchmark/scripts/check_rate_headroom.py --env-file benchmark/env.local
  python3 benchmark/scripts/check_rate_headroom.py --model glm-5p3-flash --watch 60
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
import uuid
from pathlib import Path

# Median per-trial demand, tokens/min. GLM and Kimi are measured directly from
# PA1 runs on this harness set. DeepSeek has no full-job measurement here, so it
# is GLM's measured rate scaled by 2.05x, the DeepSeek/GLM prompt-TPM ratio in
# upstream DeepSWE v1.1 data restricted to PA1's ten tasks. Replace with a
# direct measurement after the first full DeepSeek job.
PER_TRIAL_TPM = {
    "glm-5p3-flash": {"prompt": 736_678, "uncached": 43_148, "generated": 2_262},
    "deepseek-v4p1-flash": {"prompt": 1_510_190, "uncached": 88_453, "generated": 4_637},
    "kimi-k3": {"prompt": 356_251, "uncached": 6_922, "generated": 2_191},
}
HEADERS = {
    "prompt": "llm_provider-x-ratelimit-limit-tokens-prompt",
    "uncached": "llm_provider-x-ratelimit-limit-tokens-uncached-prompt",
    "generated": "llm_provider-x-ratelimit-limit-tokens-generated",
}
REMAINING = {
    "prompt": "llm_provider-x-ratelimit-remaining-tokens-prompt",
    "uncached": "llm_provider-x-ratelimit-remaining-tokens-uncached-prompt",
    "generated": "llm_provider-x-ratelimit-remaining-tokens-generated",
}


def load_env(path: Path) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def probe(base: str, key: str, model: str) -> dict[str, str]:
    # The nonce matters. A repeated payload is served from the gateway's cache
    # without an upstream call, and the response then carries no
    # Llm_provider-X-Ratelimit-* headers at all, which reads as "no limits
    # reported" rather than as a cache hit.
    nonce = uuid.uuid4()
    body = {"model": model, "messages": [{"role": "user", "content": f"ping {nonce}"}],
            "max_tokens": 1}
    request = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return {k.lower(): v for k, v in response.headers.items()}


def report(model: str, headers: dict[str, str]) -> None:
    demand = PER_TRIAL_TPM.get(model)
    over = headers.get("llm_provider-x-ratelimit-over-limit")
    print(f"\n{model}   over-limit={over or 'n/a'}")
    print(f"  {'metric':<11}{'limit TPM':>14}{'remaining':>14}{'used':>7}{'max trials':>12}")
    caps: dict[str, float] = {}
    for metric, header in HEADERS.items():
        limit = int(headers.get(header) or 0)
        if not limit:
            continue
        remaining = int(headers.get(REMAINING[metric]) or limit)
        used = 100 * (limit - remaining) / limit
        cell = ""
        if demand:
            caps[metric] = limit / demand[metric]
            cell = f"{caps[metric]:.1f}"
        print(f"  {metric:<11}{limit:>14,}{remaining:>14,}{used:>6.1f}%{cell:>12}")
    if caps:
        binding = min(caps, key=caps.get)
        print(f"  -> binding metric: {binding}; safe concurrency now "
              f"{int(caps[binding] * 0.8)} (80% of {caps[binding]:.1f})")
    if not demand:
        print("  (no per-trial demand on file for this model; limits only)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path("benchmark/env.local"))
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--watch", type=int, metavar="SECONDS",
                        help="re-probe on this interval until interrupted")
    args = parser.parse_args()

    if args.env_file.exists():
        load_env(args.env_file)
    base = os.environ.get("LITELLM_OPENAI_BASE_URL")
    key = os.environ.get("LITELLM_API_KEY")
    if not (base and key):
        raise SystemExit("LITELLM_OPENAI_BASE_URL and LITELLM_API_KEY must be set")

    models = args.models or list(PER_TRIAL_TPM)
    while True:
        print(f"--- {time.strftime('%H:%M:%S')} ---")
        for model in models:
            try:
                report(model, probe(base, key, model))
            except Exception as exc:  # noqa: BLE001 - operator-facing tool
                print(f"\n{model}: probe failed: {exc}")
        if not args.watch:
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
