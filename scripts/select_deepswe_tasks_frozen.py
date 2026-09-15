#!/usr/bin/env python3
"""Run PA1 DeepSWE v1.1 task selection against the frozen 2026-08-18 inputs."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import urllib.request
from pathlib import Path


FROZEN_INPUTS = {
    "tasks": {
        "url": "https://deepswe.datacurve.ai/artifacts/v1.1/tasks.json",
        "sha256": "bae967f6472943564c3fc5232fba3c8e0ac465c1be5ccf9dd4895d4ee9df6242",
    },
    "trials": {
        "url": "https://deepswe.datacurve.ai/artifacts/v1.1/trials.json",
        "sha256": "13d6f7563330110231b008ae4eb38e03de24af08acead840de296d1127144971",
    },
    "release": {
        "url": "https://deepswe.datacurve.ai/artifacts/v1.1/release.json",
        "sha256": "0b77963ed8c54ef40c5f744ade178b54bfae2662ed94f9235cee85eb542bdc85",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_input(name: str, cache_dir: Path) -> Path:
    spec = FROZEN_INPUTS[name]
    path = cache_dir / f"{name}.json"
    if not path.exists():
        request = urllib.request.Request(
            spec["url"], headers={"User-Agent": "PA1-DeepSWE-selection/1"}
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            path.write_bytes(response.read())

    actual = sha256(path)
    if actual != spec["sha256"]:
        raise SystemExit(
            f"{name}.json does not match the frozen 2026-08-18 input. "
            f"Expected {spec['sha256']}, got {actual}. Refusing to select tasks from changed data."
        )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache/deepswe-selection-v1.1"),
        help="Directory for the verified DeepSWE input artifacts.",
    )
    parser.add_argument("--output", type=Path, default=Path("data/deepswe_task_selection_v1.1.json"))
    parser.add_argument("--harnesses", type=int, default=4)
    args = parser.parse_args()

    args.cache_dir.mkdir(parents=True, exist_ok=True)
    tasks = ensure_input("tasks", args.cache_dir)
    trials = ensure_input("trials", args.cache_dir)
    release = ensure_input("release", args.cache_dir)

    selector = Path(__file__).with_name("select_deepswe_tasks.py")
    command = [
        sys.executable,
        str(selector),
        "--tasks-json",
        str(tasks),
        "--trials-json",
        str(trials),
        "--release-json",
        str(release),
        "--output",
        str(args.output),
        "--harnesses",
        str(args.harnesses),
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
