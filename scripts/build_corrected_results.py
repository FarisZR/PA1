#!/usr/bin/env python3
"""Correct known measurement errors in the published Pier result/trajectory files.

``result.json`` and ``agent/trajectory.json`` under ``data/benchmark-results/``
are the files to use for analysis. Where a recorded value is known to be wrong
or missing, this script writes the corrected values to those canonical names
and keeps Pier's unmodified output beside them:

    <trial>/result.json                     corrected (canonical, use this)
    <trial>/result.original.json            unmodified Pier output (only where corrected)
    <trial>/agent/trajectory.json           corrected (canonical, use this)
    <trial>/agent/trajectory.original.json  unmodified Pier output (only where rebuilt)

Corrected files use exactly the same format as the Pier originals; only the
corrected values differ. Each job directory gets a ``corrections.json`` listing
every change (field, original, corrected, source, reason). The script always
reads the ``*.original.json`` file when it exists, so it can be re-run safely.

The normal trial directory also always holds the canonical attempt. Pier keeps
its final trial there, which is wrong where it retried a failure of the
evaluated model or harness (see ``FIRST_ATTEMPT_CANONICAL``). For those trials
the first attempt is published as the trial and Pier's retry is kept as
evidence under ``.retry-attempts/<trial>/attempt-2``:

    <trial>/                                canonical attempt (use this)
    .retry-attempts/<trial>/attempt-*/      non-canonical attempts (overhead)

Corrections (see "Measurement corrections" in the results chapter):

1. Codex context metrics. Upstream Pier derives ``peak_context_tokens`` and,
   when a rollout has no explicit compaction events, ``summarization_count``
   from ``last_token_usage.total_tokens``. That total includes the call's
   output tokens, so long reasoning outputs look like context growth followed
   by a compaction. Both values are recomputed from the per-call input tokens
   in the committed trajectory and checked against the explicit compaction
   events in the raw Codex rollouts.

2. OpenCode V2 withheld usage totals. The PA1 adapter withholds token and cost
   aggregates when it cannot prove complete session collection (timeouts, CLI
   exits). The totals are taken from OpenCode's own session record in the raw
   ``opencode-v2-sessions.jsonl`` dump. The same record is checked against
   Pier's totals on every attempt where Pier did report them.

3. OpenCode V2 JSONL reader. The PA1 adapter read the session dump with
   ``str.splitlines()``, which also splits on U+0085 inside JSON strings, and
   so discarded a valid session record. Affected trajectories are rebuilt
   offline with Pier's own converter and a newline-only reader. No model is
   called; only the recorded dump is read.

4. Canonical attempts. Pier retries every exception type that is not excluded,
   and a non-zero harness exit is one type whatever its cause. Eight trials were
   therefore retried after the model or harness failed, not after a transport
   or gateway fault. Their first attempt is the observation and becomes the trial.

5. Ineligible Kimi Claude Code run. The gateway lowered the intended max effort
   to high and omitted prior reasoning on later turns. Its ten trial directories
   and Pier's uncorrected 30-trial job summary are archived under
   ``data/benchmark-results/.excluded/kimi-k3/``. The normal Kimi trial glob
   then contains only the twenty Pi and Codex observations.

Regeneration needs the raw run workspace (``benchmark/runs/``, not tracked by
Git) and, for correction 3, the pinned Pier checkout:

    python3 scripts/build_corrected_results.py \
        --pier-python ~/pier/.venv/bin/python
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "data" / "benchmark-results"
RAW = ROOT / "benchmark" / "runs"
# glm-5.3-sub is an unfiltered, abandoned snapshot and is not corrected.
JOBS = (
    "kimi-k3",
    "luna",
    "deepseek-v4p1-flash",
    "glm-5.3-flash",
    "opencode-v2-luna",
    "opencode-v2-deepseek-v4p1-flash",
)
# Trials that Pier retried after a failure of the evaluated model or harness
# (issue #95). Retries after transport or gateway faults (Kimi K3 Pi, GLM-5.3-Flash
# Codex, and the DeepSeek V4.1 Flash context-window rejections) are not listed:
# there Pier's final trial is the observation.
OPENCODE_EXIT = (
    "OpenCode finished and passed after recovering from a transient stream error, but "
    "OpenCode 2.0.8's non-interactive CLI kept exit status 1, so Pier repeated the trial"
)
QUESTION_TOOL = (
    "The model asked for a Git author identity through OpenCode's interactive question "
    "tool, which a non-interactive run cannot answer; a failure of the evaluated system"
)
FIRST_ATTEMPT_CANONICAL = {
    "opencode-v2-deepseek-v4p1-flash": {
        "fastapi-implicit-head-options__5UY3Gb3": OPENCODE_EXIT,
        "katex-multicolumn-array-spans__73HZQ6c": OPENCODE_EXIT,
        "koota-composite-trait-aspects__KKujPmj": OPENCODE_EXIT,
        "scriggo-method-declarations__7mKVSxh": OPENCODE_EXIT,
    },
    "opencode-v2-luna": {
        "effect-sse-httpapi-streaming__rL66U8M": QUESTION_TOOL,
        "expr-try-catch-errors__Uvvi6Eh": QUESTION_TOOL,
        "katex-multicolumn-array-spans__zipARnF": QUESTION_TOOL,
        "oxvg-structural-selector-preserv__mGjKfoa": QUESTION_TOOL,
    },
}
KIMI_CLAUDE_TRIALS = frozenset({
    "boa-hierarchical-evaluation-canc__245SQfJ",
    "csstree-shorthand-expansion-comp__6vuvpdq",
    "effect-sse-httpapi-streaming__PnvLNXA",
    "expr-try-catch-errors__xdh2hSz",
    "fastapi-implicit-head-options__CdKbHQt",
    "katex-multicolumn-array-spans__LfhLMSd",
    "koota-composite-trait-aspects__FBs3PJg",
    "oxvg-structural-selector-preserv__USS6tzH",
    "python-statemachine-state-data-s__rPBVs54",
    "scriggo-method-declarations__hFMoy8t",
})
KIMI_EXCLUSION_REASON = (
    "LiteLLM translated Claude Code's requested max reasoning effort to high "
    "and omitted earlier reasoning from subsequent model requests (PA1 #108)"
)
# Same threshold as upstream Pier's token-drop heuristic, applied to input only.
COMPACTION_DROP_TOKENS = 10_000
REFERENCE = "chapters/_04-results.qmd#sec-measurement-corrections"

RECONVERT = r"""
import json, sys
from pathlib import Path
from pier.agents.installed import opencode_v2 as oc
from pier.models.agent.context import AgentContext

def read_jsonl(path):
    if not path.exists():
        return []
    records = []
    for line in path.read_text().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            records.append({"type": "cli-stdout", "raw": line})
            continue
        if isinstance(record, dict):
            records.append(record)
    return records

oc.OpenCodeV2._read_jsonl = staticmethod(read_jsonl)
trial = Path(sys.argv[1])
agent_cfg = json.loads((trial / "config.json").read_text())["agent"]
keep = ("variant", "restrict_model", "version", "model_catalog_file",
        "opencode_v2_config", "opencode_v2_checksums")
agent = oc.OpenCodeV2(
    logs_dir=trial,
    model_name=agent_cfg["model_name"],
    **{k: v for k, v in agent_cfg["kwargs"].items() if k in keep},
)
agent.populate_context_post_run(AgentContext())
"""


class ValidationError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def dump(path: Path, data: dict[str, Any], indent: int) -> None:
    # Same serialization as Pier: ASCII-escaped, no trailing newline.
    path.write_text(json.dumps(data, indent=indent))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def original(path: Path) -> Path:
    """Return Pier's unmodified file: ``<name>.original.json`` if present, else ``path``."""
    kept = path.with_name(path.stem + ".original.json")
    return kept if kept.exists() else path


def write_corrected(path: Path, data: dict[str, Any], indent: int) -> None:
    """Keep Pier's original bytes as ``<name>.original.json`` and write ``data`` to ``path``."""
    kept = path.with_name(path.stem + ".original.json")
    if not kept.exists():
        path.rename(kept)
    dump(path, data, indent)


def restore(path: Path) -> None:
    """Undo an earlier correction that no longer applies."""
    kept = path.with_name(path.stem + ".original.json")
    if kept.exists():
        kept.replace(path)


def attempts(job: Path) -> list[Path]:
    finals = sorted(p.parent for p in job.glob("*/result.json"))
    retries = sorted(p.parent for p in job.glob(".retry-attempts/*/attempt-*/result.json"))
    return finals + retries


def exclude_kimi_claude(job: Path) -> list[dict[str, str]]:
    """Archive the fixed ineligible run, leaving only eligible Kimi trials by default."""
    archive = PUBLISHED / ".excluded" / job.name
    excluded = archive / "claude-code"
    live_names = {p.parent.name for p in job.glob("*/result.json")}
    archived_names = {p.parent.name for p in excluded.glob("*/result.json")}
    if archived_names - KIMI_CLAUDE_TRIALS:
        raise ValidationError(f"Unexpected Kimi trials in {excluded}: {archived_names - KIMI_CLAUDE_TRIALS}")
    if (live_names | archived_names) & KIMI_CLAUDE_TRIALS != KIMI_CLAUDE_TRIALS:
        raise ValidationError("The ten known Kimi Claude Code trials are not all present")
    if live_names & archived_names:
        raise ValidationError(f"Kimi trials present in both locations: {live_names & archived_names}")
    if len(live_names | archived_names) != 30:
        raise ValidationError("Expected 30 completed Kimi K3 trials before exclusion")

    for name in sorted(KIMI_CLAUDE_TRIALS):
        trial = job / name
        target = excluded / name
        source = trial if trial.is_dir() else target
        result = load(source / "result.json")
        if result["config"]["agent"]["name"] != "claude-code":
            raise ValidationError(f"{source}: expected Claude Code")
        if source == trial:
            excluded.mkdir(parents=True, exist_ok=True)
            trial.rename(target)

    for trial in job.glob("*/result.json"):
        if load(trial)["config"]["agent"]["name"] == "claude-code":
            raise ValidationError(f"{trial}: unlisted Kimi Claude Code observation")
    if len(list(job.glob("*/result.json"))) != 20:
        raise ValidationError("Expected exactly twenty eligible Kimi Pi/Codex trials")

    summary = job / "result.json"
    archived_summary = archive / "pier-job-summary.json"
    if summary.exists() and archived_summary.exists():
        raise ValidationError("Kimi Pier job summary exists in both locations")
    if summary.exists():
        archive.mkdir(parents=True, exist_ok=True)
        summary.rename(archived_summary)
    if not archived_summary.exists() or load(archived_summary)["n_total_trials"] != 30:
        raise ValidationError("The unfiltered Kimi Pier job summary is missing")

    return [
        {
            "trial": name,
            "harness": "claude-code",
            "published": str((excluded / name).relative_to(PUBLISHED)),
            "reason": KIMI_EXCLUSION_REASON,
        }
        for name in sorted(KIMI_CLAUDE_TRIALS)
    ]


def agent_steps(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in trajectory.get("steps", []) if s.get("source") == "agent"]


# --- canonical attempts ------------------------------------------------------


def pier_path(job_name: str, rel: Path) -> Path:
    """Map a published attempt back to its location in Pier's run layout."""
    selected = FIRST_ATTEMPT_CANONICAL.get(job_name, {})
    if len(rel.parts) == 1 and rel.name in selected:
        return Path(".retry-attempts", rel.name, "attempt-1")
    if rel.parts[0] == ".retry-attempts" and rel.parts[1] in selected and rel.name == "attempt-2":
        return Path(rel.parts[1])
    return rel


def attempt_summary(published: Path, job: Path, raw_job: Path) -> dict[str, Any]:
    rel = published.relative_to(job)
    pier = pier_path(job.name, rel)
    result = load(original(published / "result.json"))
    raw = load(raw_job / pier / "result.json")
    if result["id"] != raw["id"]:
        raise ValidationError(f"{published}: attempt {result['id']} != Pier {pier} {raw['id']}")
    return {
        "published": str(rel),
        "pier": str(pier),
        "id": result["id"],
        "reward": ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
        "exception": (result.get("exception_info") or {}).get("exception_type"),
    }


def select_first_attempts(job: Path, raw_job: Path) -> list[dict[str, Any]]:
    """Publish the first attempt as the trial and Pier's retry as attempt-2."""
    selections = []
    for name, reason in FIRST_ATTEMPT_CANONICAL.get(job.name, {}).items():
        trial = job / name
        first = job / ".retry-attempts" / name / "attempt-1"
        retry = job / ".retry-attempts" / name / "attempt-2"
        if first.is_dir():
            if retry.exists():
                raise ValidationError(f"{retry}: already exists")
            trial.rename(retry)
            first.rename(trial)
        elif not (trial.is_dir() and retry.is_dir()):
            raise ValidationError(f"{trial}: no retried attempt to select")
        canonical = attempt_summary(trial, job, raw_job)
        if canonical["exception"] != "NonZeroAgentExitCodeError":
            raise ValidationError(f"{trial}: first attempt ended with {canonical['exception']}")
        selections.append({
            "trial": name,
            "canonical": canonical,
            "pier_final": attempt_summary(retry, job, raw_job),
            "reason": reason,
        })
    return selections


# --- Codex -------------------------------------------------------------------


def codex_input_metrics(trajectory: dict[str, Any]) -> tuple[int | None, int]:
    series = [
        s["metrics"]["prompt_tokens"]
        for s in agent_steps(trajectory)
        if (s.get("metrics") or {}).get("prompt_tokens") is not None
    ]
    drops, window_peak = 0, None
    for tokens in series:
        if window_peak is not None and tokens < window_peak - COMPACTION_DROP_TOKENS:
            drops += 1
            window_peak = tokens
        else:
            window_peak = tokens if window_peak is None else max(window_peak, tokens)
    return (max(series) if series else None), drops


def codex_rollout_compactions(raw_trial: Path) -> int | None:
    sessions = raw_trial / "agent" / "sessions"
    if not sessions.is_dir():
        return None
    count = 0
    for rollout in sessions.rglob("*.jsonl"):
        with rollout.open() as handle:
            for line in handle:
                if '"compacted"' not in line and "context_compacted" not in line:
                    continue
                event = json.loads(line)
                payload = event.get("payload") or {}
                if event.get("type") == "compacted" or (
                    isinstance(payload, dict) and payload.get("type") == "context_compacted"
                ):
                    count += 1
    return count


def correct_codex(trial: Path, raw_trial: Path, result: dict[str, Any]) -> list[dict[str, Any]]:
    trajectory = load(original(trial / "agent" / "trajectory.json"))
    peak, compactions = codex_input_metrics(trajectory)
    events = codex_rollout_compactions(raw_trial)
    if events is None:
        raise ValidationError(f"{trial}: raw Codex rollout not found under {raw_trial}")
    if events != compactions:
        raise ValidationError(
            f"{trial}: input-drop compactions {compactions} != rollout events {events}"
        )
    agent_result = result["agent_result"]
    reason = (
        "Upstream Pier derives Codex context metrics from last_token_usage.total_tokens, "
        "which includes output tokens; recomputed from per-call input tokens"
    )
    source = "agent/trajectory.json steps[*].metrics.prompt_tokens; checked against raw rollout compaction events"
    changes = []
    for field, value in (("peak_context_tokens", peak), ("summarization_count", compactions)):
        if agent_result.get(field) != value:
            changes.append({
                "field": f"agent_result.{field}",
                "original": agent_result.get(field),
                "corrected": value,
                "reason": reason,
                "source": source,
            })
            agent_result[field] = value
    return changes


# --- OpenCode V2 --------------------------------------------------------------


def opencode_session_totals(raw_trial: Path) -> dict[str, Any]:
    dump_path = raw_trial / "agent" / "opencode-v2" / "opencode-v2-sessions.jsonl"
    if not dump_path.exists():
        raise ValidationError(f"missing OpenCode session dump: {dump_path}")
    records = [
        json.loads(line)
        for line in dump_path.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]
    totals = {"input": 0, "cached": 0, "output": 0, "cost": 0.0}
    sessions = []
    for record in records:
        session = record["session"]
        tokens = session.get("tokens") or {}
        cache = tokens.get("cache") or {}
        totals["input"] += tokens.get("input", 0) + cache.get("read", 0) + cache.get("write", 0)
        totals["cached"] += cache.get("read", 0)
        totals["output"] += tokens.get("output", 0) + tokens.get("reasoning", 0)
        totals["cost"] += session.get("cost") or 0.0
        sessions.append({"id": session.get("id"), "outcome": session.get("outcome"), "tokens": tokens})
    totals["sessions"] = sessions
    totals["dump"] = str(dump_path.relative_to(ROOT))
    totals["dump_sha256"] = sha256(dump_path)
    return totals


def rebuild_opencode_trajectory(raw_trial: Path, pier_python: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "trial"
        work.mkdir()
        shutil.copytree(raw_trial / "agent" / "opencode-v2", work / "opencode-v2")
        shutil.copy(raw_trial / "config.json", work / "config.json")
        subprocess.run([pier_python, "-c", RECONVERT, str(work)], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return load(work / "trajectory.json")


def correct_opencode(
    trial: Path, raw_trial: Path, result: dict[str, Any], pier_python: str | None
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any]]:
    agent_result = result["agent_result"]
    totals = opencode_session_totals(raw_trial)
    audit = {k: totals[k] for k in ("input", "cached", "output", "cost", "dump", "dump_sha256")}
    audit["session_outcomes"] = [s["outcome"] for s in totals["sessions"]]

    if agent_result.get("n_input_tokens") is not None:
        recorded = (agent_result["n_input_tokens"], agent_result["n_cache_tokens"], agent_result["n_output_tokens"])
        recovered = (totals["input"], totals["cached"], totals["output"])
        if recorded != recovered:
            raise ValidationError(f"{trial}: Pier totals {recorded} != session totals {recovered}")
        audit["validated_against_pier"] = True
        return [], None, audit

    trajectory = load(original(trial / "agent" / "trajectory.json"))
    changes: list[dict[str, Any]] = []
    corrected_trajectory = None
    extra = trajectory["final_metrics"].get("extra") or {}
    if extra.get("collection_errors") and len(trajectory["steps"]) <= 1:
        if pier_python is None:
            raise ValidationError(f"{trial}: stub trajectory needs --pier-python to rebuild")
        corrected_trajectory = rebuild_opencode_trajectory(raw_trial, pier_python)
        changes.append({
            "field": "agent/trajectory.json",
            "original": f"{len(trajectory['steps'])} step stub",
            "corrected": f"{len(corrected_trajectory['steps'])} steps (original kept as agent/trajectory.original.json)",
            "reason": (
                "PA1 OpenCode V2 adapter read the session dump with str.splitlines(), which splits "
                "on U+0085 inside JSON strings; rebuilt offline with Pier's converter and a "
                "newline-only reader"
            ),
            "source": totals["dump"],
            "source_sha256": totals["dump_sha256"],
        })
        trajectory = corrected_trajectory
        extra = trajectory["final_metrics"].get("extra") or {}

    reason = (
        "PA1 OpenCode V2 adapter withholds aggregates when session collection is not provably "
        "complete (timeout or CLI exit); taken from OpenCode's own session record"
    )
    values = {
        "n_input_tokens": totals["input"],
        "n_cache_tokens": totals["cached"],
        "n_output_tokens": totals["output"],
        "cost_usd": round(totals["cost"], 10),
        "peak_context_tokens": extra.get("peak_context_tokens"),
        "summarization_count": extra.get("summarization_count"),
        "n_agent_steps": sum(1 for s in agent_steps(trajectory)),
    }
    for field, value in values.items():
        if agent_result.get(field) != value:
            from_session = field in ("n_input_tokens", "n_cache_tokens", "n_output_tokens", "cost_usd")
            source = (
                totals["dump"] + " session.tokens / session.cost"
                if from_session
                else "trajectory final_metrics / steps"
            )
            changes.append({
                "field": f"agent_result.{field}",
                "original": agent_result.get(field),
                "corrected": value,
                "reason": reason,
                "source": source,
            })
            agent_result[field] = value
    return changes, corrected_trajectory, audit


# --- driver -------------------------------------------------------------------


def process_job(job_name: str, pier_python: str | None) -> dict[str, Any]:
    job = PUBLISHED / job_name
    raw_job = RAW / job_name
    manifest: dict[str, Any] = {
        "job": job_name,
        "reference": REFERENCE,
        "attempt_selection": select_first_attempts(job, raw_job),
        "attempts": [],
    }
    for trial in attempts(job):
        rel = trial.relative_to(job)
        raw_trial = raw_job / pier_path(job_name, rel)
        result = load(original(trial / "result.json"))
        harness = result["config"]["agent"]["name"]
        corrected_trajectory = None
        audit = None
        if harness == "codex":
            changes = correct_codex(trial, raw_trial, result)
        elif harness == "opencode-v2":
            changes, corrected_trajectory, audit = correct_opencode(trial, raw_trial, result, pier_python)
        else:
            continue
        entry: dict[str, Any] = {"attempt": str(rel), "harness": harness, "changes": changes}
        if audit is not None:
            entry["opencode_session_record"] = audit
        manifest["attempts"].append(entry)
        result_path = trial / "result.json"
        trajectory_path = trial / "agent" / "trajectory.json"
        if changes:
            write_corrected(result_path, result, indent=4)
        else:
            restore(result_path)
        if corrected_trajectory is not None:
            write_corrected(trajectory_path, corrected_trajectory, indent=2)
        else:
            restore(trajectory_path)
    if job_name == "kimi-k3":
        manifest["excluded_runs"] = exclude_kimi_claude(job)
    (job / "corrections.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pier-python", help="Python of the pinned Pier checkout (for trajectory rebuilds).")
    args = parser.parse_args()
    if not RAW.is_dir():
        sys.exit(f"raw run workspace not found: {RAW}")
    for job_name in JOBS:
        manifest = process_job(job_name, args.pier_python)
        changed = [a for a in manifest["attempts"] if a["changes"]]
        print(f"{job_name}: {len(manifest['attempts'])} attempts checked, {len(changed)} corrected")


if __name__ == "__main__":
    main()
