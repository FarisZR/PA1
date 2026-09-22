"""Load the canonical PA1 benchmark observations used by paper analysis.

Primary comparative analysis uses Pier's final trial by default. Sparse exceptions
are declared in data/benchmark-results/primary-attempt-overrides.json. An override
selects an earlier recorded attempt when Pier repeated a trial for a cause that
belongs to the model/harness observation rather than an invalidating transport
failure.

This module is the single selection layer for paper tables and figures. It also
prefers result.corrected.json over result.json for measurement corrections.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_RESULTS = ROOT / "data" / "benchmark-results"
OVERRIDES_PATH = BENCHMARK_RESULTS / "primary-attempt-overrides.json"


class PrimaryAttemptError(RuntimeError):
    """Raised when the primary-attempt manifest or published evidence is inconsistent."""


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_result(trial_dir: Path) -> dict[str, Any]:
    """Load a trial, preferring the corrected sidecar when one exists."""
    corrected = trial_dir / "result.corrected.json"
    path = corrected if corrected.exists() else trial_dir / "result.json"
    if not path.exists():
        raise PrimaryAttemptError(f"Missing result file for trial: {trial_dir}")
    return _load_json(path)


def task_name(result: dict[str, Any]) -> str:
    value = result.get("task_name")
    if not value:
        raise PrimaryAttemptError("Result has no task_name")
    return str(value).split("/")[-1]


def harness_name(result: dict[str, Any]) -> str:
    agent = (result.get("config") or {}).get("agent") or {}
    value = agent.get("name")
    if not value:
        raise PrimaryAttemptError("Result has no config.agent.name")
    return str(value)


def _load_overrides() -> dict[tuple[str, str, str], dict[str, Any]]:
    manifest = _load_json(OVERRIDES_PATH)
    if manifest.get("version") != 1:
        raise PrimaryAttemptError(
            f"Unsupported primary-attempt override version: {manifest.get('version')}"
        )
    if manifest.get("default") != "pier-final":
        raise PrimaryAttemptError(
            f"Unsupported primary-attempt default: {manifest.get('default')}"
        )

    overrides: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in manifest.get("overrides", []):
        key = (entry["job"], entry["harness"], entry["task"])
        if key in overrides:
            raise PrimaryAttemptError(f"Duplicate primary-attempt override: {key}")
        overrides[key] = entry
    return overrides


def iter_primary_trials(job_dir: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Yield one canonical observation per Pier final trial in a published job.

    Final trial directories define the observation set. For each observation, the
    sparse override manifest may select an earlier attempt from .retry-attempts.
    Jobs without overrides are therefore parsed exactly as Pier published them.
    """
    job_dir = Path(job_dir)
    job = job_dir.name
    overrides = _load_overrides()

    final_dirs = sorted(
        path.parent for path in job_dir.glob("*/result.json")
        if path.parent.name != ".retry-attempts"
    )
    seen: set[tuple[str, str]] = set()
    used_override_keys: set[tuple[str, str, str]] = set()

    for final_dir in final_dirs:
        final_result = load_result(final_dir)
        task = task_name(final_result)
        harness = harness_name(final_result)
        observation_key = (harness, task)
        if observation_key in seen:
            raise PrimaryAttemptError(
                f"Duplicate final observation in {job}: {harness}/{task}"
            )
        seen.add(observation_key)

        key = (job, harness, task)
        entry = overrides.get(key)
        selected_dir = final_dir
        if entry is not None:
            selected_dir = job_dir / entry["primary_attempt"]
            if not selected_dir.is_dir():
                raise PrimaryAttemptError(
                    f"Override for {key} points to missing directory: {selected_dir}"
                )
            selected_result = load_result(selected_dir)
            if task_name(selected_result) != task:
                raise PrimaryAttemptError(
                    f"Override for {key} points to task {task_name(selected_result)}"
                )
            if harness_name(selected_result) != harness:
                raise PrimaryAttemptError(
                    f"Override for {key} points to harness {harness_name(selected_result)}"
                )
            used_override_keys.add(key)

        yield selected_dir, load_result(selected_dir)

    unused = {
        key for key in overrides
        if key[0] == job and key not in used_override_keys
    }
    if unused:
        raise PrimaryAttemptError(
            f"Overrides for {job} do not match a final observation: {sorted(unused)}"
        )


def validate_all_jobs(results_root: Path = BENCHMARK_RESULTS) -> dict[str, int]:
    """Validate the manifest against every published job it references."""
    overrides = _load_overrides()
    jobs = sorted({key[0] for key in overrides})
    observations = 0
    applied = 0

    for job in jobs:
        job_dir = Path(results_root) / job
        if not job_dir.is_dir():
            raise PrimaryAttemptError(f"Override references missing job: {job}")
        rows = list(iter_primary_trials(job_dir))
        observations += len(rows)
        applied += sum(1 for key in overrides if key[0] == job)

    return {
        "jobs_with_overrides": len(jobs),
        "observations_checked": observations,
        "overrides_applied": applied,
    }


if __name__ == "__main__":
    print(json.dumps(validate_all_jobs(), indent=2))
