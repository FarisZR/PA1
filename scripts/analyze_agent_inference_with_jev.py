#!/usr/bin/env python3
"""Audit benchmark agent trajectories with TypeSafe's Jev model.

This script is deliberately an audit aid, not a replacement for the raw
trajectory or deterministic runner evidence.  It preserves local heuristic
signals alongside Jev's typed judgments so that a reviewer can distinguish:

* evidence of inference/transport trouble (garbled output, SSE failures, etc.),
* evidence of harness/environment trouble (timeouts, missing observations, or
  failed execution plumbing),
* ordinary command/test failures, and
* coherent but unsuccessful model work.

Jev receives bounded excerpts.  The complete source files remain untouched and
are referenced by path and SHA-256 fingerprint in the output.  The script
analyzes every agent step and every tool call in each selected run.  Multiple
questions for a batch are sent in one System One request; questions in one
request are independent, as required by the TypeSafe programming model.

Dependency:
    pip install typesafe-sdk

Example (do not run automatically):
    source .bashrc
    python scripts/analyze_agent_inference_with_jev.py

The default output is data/jev_agent_inference_audit.json.  Use --fresh to
ignore an existing output file; otherwise completed matching records are
reused so an interrupted audit can resume without repeating API calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from typesafe_sdk import Choice, Noul, RetryPolicy, Score, TypeSafeClient
except ImportError as exc:  # pragma: no cover - exercised when dependency is absent
    raise SystemExit(
        "Missing dependency: install it with `pip install typesafe-sdk` "
        "before running this script."
    ) from exc


DEFAULT_MODEL_DIRS = (
    "deepseek-v4p1-flash",
    "glm-5.3-flash",
)
DEFAULT_OUTPUT = Path("data/jev_agent_inference_audit.json")
DEFAULT_BATCH_SIZE = 5
DEFAULT_STEP_CHARS = 6_000
DEFAULT_TOOL_CHARS = 5_000
DEFAULT_LOG_CHARS = 1_500
LONG_GAP_SECONDS = 300.0
REVIEW_PROBABILITY = 0.65

# These patterns are evidence collectors only.  They do not by themselves
# prove that the model or harness failed.
TRANSPORT_PATTERNS = (
    r"idle timeout waiting for SSE",
    r"stream closed before response\.completed",
    r"stream disconnected",
    r"SSE",
    r"response\.completed",
    r"previous message got garbled",
    r"garbled",
    r"reconnect",
    r"connection reset",
    r"connection refused",
    r"broken pipe",
    r"unexpected EOF",
    r"APIConnection",
    r"APITimeout",
)
HARNESS_PATTERNS = (
    r"AgentTimeoutError",
    r"asyncio\.wait_for",
    r"docker",
    r"container.*(?:not found|exited|missing)",
    r"no such container",
    r"permission denied",
    r"timed out",
    r"timeout",
    r"missing observation",
    r"tool call.*(?:incomplete|interrupted)",
)
ORDINARY_TOOL_PATTERNS = (
    r"FAIL(?:ED)?",
    r"exit(?:ed)? with code [1-9]",
    r"exit code [1-9]",
    r"syntax error",
    r"undefined:",
    r"command not found",
    r"test(s)? failed",
    r"no test files",
)
GARBLED_PATTERNS = (
    r"garbled",
    r"truncated",
    r"malformed",
    r"incomplete response",
    r"my previous message",
    r"stream closed",
    r"stream disconnected",
)


def now_utc() -> str:
    return datetime.now().astimezone().isoformat()


def json_default(value: Any) -> str:
    return repr(value)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=json_default)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def excerpt(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = canonical_json(value)
    if len(text) <= limit:
        return text
    head = max(1, limit - 120)
    return text[:head] + f"\n...[truncated; original_chars={len(text)}]..."


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def regex_hits(text: str, patterns: Iterable[str]) -> list[str]:
    hits: list[str] = []
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            hits.append(pattern)
    return hits


def pattern_text(value: Any) -> str:
    """Serialize source content for pattern checks without adding excerpt markers."""

    if value is None:
        return ""
    return value if isinstance(value, str) else canonical_json(value)


def normalize_for_repetition(value: Any) -> str:
    """Create a conservative signature for repeated tool-call detection.

    Exact command text is retained apart from whitespace and volatile request
    identifiers.  This intentionally avoids aggressive normalization: a false
    negative is preferable to claiming two different commands were repeated.
    """

    text = canonical_json(value)
    text = re.sub(r"(?:call|api_call|codex_turn|request)[_-][A-Za-z0-9-]+", "<id>", text)
    text = re.sub(r"\b[0-9a-f]{16,}\b", "<hex>", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path: Path, limit: int = DEFAULT_LOG_CHARS) -> str:
    if not path.exists():
        return ""
    try:
        return excerpt(path.read_text(encoding="utf-8", errors="replace"), limit)
    except OSError as exc:
        return f"[could not read {path}: {exc}]"


def answer_to_dict(answer: Any) -> dict[str, Any]:
    if hasattr(answer, "model_dump"):
        return answer.model_dump(mode="json")
    if hasattr(answer, "dict"):
        return answer.dict()
    if isinstance(answer, dict):
        return answer
    return {"value": repr(answer)}


def response_metadata(response: Any) -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    return {
        "model": getattr(response, "model", None),
        "request_id": getattr(response, "request_id", None),
        "usage": answer_to_dict(usage) if usage is not None else None,
    }


def selected_result_summary(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    agent_result = result.get("agent_result")
    agent_result = agent_result if isinstance(agent_result, dict) else {}
    exception_info = result.get("exception_info")
    exception_info = exception_info if isinstance(exception_info, dict) else {}
    verifier_result = result.get("verifier_result")
    verifier_result = verifier_result if isinstance(verifier_result, dict) else {}
    rewards = verifier_result.get("rewards")
    rewards = rewards if isinstance(rewards, dict) else {}
    return {
        "agent_steps": agent_result.get("n_agent_steps"),
        "input_tokens": agent_result.get("n_input_tokens"),
        "cache_tokens": agent_result.get("n_cache_tokens"),
        "output_tokens": agent_result.get("n_output_tokens"),
        "reasoning_output_tokens": agent_result.get("reasoning_output_tokens"),
        "summarization_count": agent_result.get("summarization_count"),
        "peak_context_tokens": agent_result.get("peak_context_tokens"),
        "exception_type": exception_info.get("exception_type"),
        "exception_message": excerpt(exception_info.get("exception_message"), 1_000),
        "reward": rewards.get("reward"),
        "f2p_passed": rewards.get("f2p_passed"),
        "p2p_passed": rewards.get("p2p_passed"),
    }


def collect_run_log_evidence(run_dir: Path) -> dict[str, Any]:
    """Collect bounded, local evidence without sending complete logs to Jev."""

    files = [
        run_dir / "exception.txt",
        run_dir / "trial.log",
        run_dir / "agent" / "codex.txt",
        run_dir / "agent" / "pi.txt",
        run_dir / "verifier" / "run.log",
    ]
    counts: Counter[str] = Counter()
    samples: list[str] = []
    for path in files:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            hits = regex_hits(line, TRANSPORT_PATTERNS + HARNESS_PATTERNS + ORDINARY_TOOL_PATTERNS)
            for hit in hits:
                counts[hit] += 1
            if hits and len(samples) < 8:
                samples.append(f"{path.name}: {excerpt(line, DEFAULT_LOG_CHARS)}")
    return {
        "pattern_counts": dict(counts),
        "samples": samples,
        "exception_excerpt": read_text(run_dir / "exception.txt"),
    }


def observation_text(step: dict[str, Any], limit: int) -> str:
    observation = step.get("observation")
    if observation is None:
        return ""
    return excerpt(observation, limit)


def extract_call_records(step: dict[str, Any], step_index: int, tool_chars: int) -> list[dict[str, Any]]:
    calls = step.get("tool_calls")
    if not isinstance(calls, list):
        return []
    details = step.get("extra", {}).get("tool_call_details", {})
    details = details if isinstance(details, dict) else {}
    observation = step.get("observation")
    results = observation.get("results", []) if isinstance(observation, dict) else []
    results = results if isinstance(results, list) else []
    by_call_id = {
        item.get("source_call_id"): item
        for item in results
        if isinstance(item, dict) and item.get("source_call_id")
    }

    records: list[dict[str, Any]] = []
    for call_index, call in enumerate(calls, start=1):
        if not isinstance(call, dict):
            call = {"raw": call}
        call_id = call.get("tool_call_id") or f"call_{step_index}_{call_index}"
        call_detail = details.get(call_id, {})
        call_detail = call_detail if isinstance(call_detail, dict) else {}
        result = by_call_id.get(call_id)
        result_content = result.get("content") if isinstance(result, dict) else None
        raw_arguments = call.get("arguments", {})
        signature_value = {
            "function_name": call.get("function_name"),
            "arguments": raw_arguments,
        }
        text_for_patterns = " ".join(
            [
                pattern_text(call.get("function_name")),
                pattern_text(raw_arguments),
                pattern_text(result_content),
            ]
        )
        transport_hits = regex_hits(text_for_patterns, TRANSPORT_PATTERNS)
        harness_hits = regex_hits(text_for_patterns, HARNESS_PATTERNS)
        ordinary_hits = regex_hits(text_for_patterns, ORDINARY_TOOL_PATTERNS)
        records.append(
            {
                "call_index": call_index,
                "call_id": call_id,
                "function_name": call.get("function_name"),
                "arguments": excerpt(raw_arguments, tool_chars),
                "observation": excerpt(result_content, tool_chars),
                "status": call_detail.get("status"),
                "has_observation": result is not None,
                "signature": normalize_for_repetition(signature_value),
                "local_signals": {
                    "transport_patterns": transport_hits,
                    "harness_patterns": harness_hits,
                    "ordinary_tool_patterns": ordinary_hits,
                    "missing_observation": result is None,
                    "status_not_completed": bool(call_detail.get("status") not in (None, "completed")),
                },
            }
        )
    return records


def agent_steps_from_trajectory(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    steps = trajectory.get("steps", []) if isinstance(trajectory, dict) else []
    return [
        step for step in steps if isinstance(step, dict) and step.get("source") == "agent"
    ]


def make_run_record(
    run_dir: Path,
    repo_root: Path,
    step_chars: int,
    tool_chars: int,
) -> dict[str, Any]:
    result_path = run_dir / "result.json"
    trajectory_path = run_dir / "agent" / "trajectory.json"
    result = load_json(result_path) if result_path.exists() else {}
    trajectory = load_json(trajectory_path) if trajectory_path.exists() else {}
    agent_steps = agent_steps_from_trajectory(trajectory)
    run_relative = str(run_dir.relative_to(repo_root)) if run_dir.is_relative_to(repo_root) else str(run_dir)
    run_id = run_relative
    run_evidence = collect_run_log_evidence(run_dir)
    run_summary = selected_result_summary(result)
    config = result.get("config") if isinstance(result, dict) else {}
    config = config if isinstance(config, dict) else {}
    agent_config = config.get("agent") if isinstance(config.get("agent"), dict) else {}
    environment_config = config.get("environment") if isinstance(config.get("environment"), dict) else {}
    verifier_config = config.get("verifier") if isinstance(config.get("verifier"), dict) else {}
    config_summary = {
        "timeout_multiplier": config.get("timeout_multiplier"),
        "agent": {
            "name": agent_config.get("name"),
            "model_name": agent_config.get("model_name"),
            "kwargs": agent_config.get("kwargs", {}),
            "override_timeout_sec": agent_config.get("override_timeout_sec"),
        },
        "environment_type": environment_config.get("type"),
        "verifier_override_timeout_sec": verifier_config.get("override_timeout_sec"),
    }
    patch_path = run_dir / "artifacts" / "model.patch"
    run_metadata = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "model": trajectory.get("agent", {}).get("model_name") if isinstance(trajectory.get("agent"), dict) else None,
        "agent": trajectory.get("agent", {}).get("name") if isinstance(trajectory.get("agent"), dict) else None,
        "trajectory_schema": trajectory.get("schema_version"),
        "source_files": {
            "result": str(result_path),
            "trajectory": str(trajectory_path),
            "exception": str(run_dir / "exception.txt"),
            "codex_log": str(run_dir / "agent" / "codex.txt"),
            "pi_log": str(run_dir / "agent" / "pi.txt"),
        },
        "model_patch_bytes": patch_path.stat().st_size if patch_path.exists() else None,
        "config_summary": config_summary,
        "result_summary": run_summary,
        "log_evidence": run_evidence,
    }

    previous_time: datetime | None = None
    step_records: list[dict[str, Any]] = []
    for step_index, step in enumerate(agent_steps, start=1):
        timestamp = step.get("timestamp")
        parsed_time = parse_timestamp(timestamp)
        gap = (parsed_time - previous_time).total_seconds() if parsed_time and previous_time else None
        if parsed_time:
            previous_time = parsed_time
        message = step.get("message", "")
        reasoning = step.get("reasoning_content", "")
        call_records = extract_call_records(step, step_index, tool_chars)
        step_text = " ".join(
            [
                pattern_text(message),
                pattern_text(reasoning),
                pattern_text(step.get("observation")),
            ]
        )
        step_records.append(
            {
                "item_id": f"{run_id}:step:{step.get('step_id', step_index)}",
                "step_id": step.get("step_id", step_index),
                "sequence_index": step_index,
                "timestamp": timestamp,
                "agent_message": excerpt(message, step_chars),
                "reasoning": excerpt(reasoning, step_chars),
                "observation": observation_text(step, tool_chars),
                "tool_calls": call_records,
                "local_signals": {
                    "long_gap_seconds": gap if gap is not None and gap >= LONG_GAP_SECONDS else None,
                    "transport_patterns": regex_hits(step_text, TRANSPORT_PATTERNS),
                    "harness_patterns": regex_hits(step_text, HARNESS_PATTERNS),
                    "garbled_patterns": regex_hits(step_text, GARBLED_PATTERNS),
                    "ordinary_tool_patterns": regex_hits(step_text, ORDINARY_TOOL_PATTERNS),
                    "no_tool_call": not bool(call_records),
                },
                "jev": None,
                "source_fingerprint": sha256_text(canonical_json(step)),
            }
        )

    # Add current normalized repetition streaks after all calls are known.
    streak = 0
    previous_signature = None
    for step_record in step_records:
        for call in step_record["tool_calls"]:
            if call["signature"] == previous_signature:
                streak += 1
            else:
                streak = 1
                previous_signature = call["signature"]
            call["local_signals"]["repetition_streak"] = streak
            call["local_signals"]["repeated_no_progress_candidate"] = streak >= 3
        step_record["local_signals"]["repetition_streak"] = max(
            [call["local_signals"].get("repetition_streak", 0) for call in step_record["tool_calls"]] or [0]
        )
    run_metadata["trajectory_agent_steps"] = len(step_records)
    run_metadata["trajectory_tool_calls"] = sum(len(step["tool_calls"]) for step in step_records)
    run_metadata["steps"] = step_records
    return run_metadata


def state_for_batch(run: dict[str, Any], batch: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "run": {
            "run_id": run["run_id"],
            "model": run.get("model"),
            "agent": run.get("agent"),
            "result_summary": run.get("result_summary", {}),
            "log_evidence": run.get("log_evidence", {}),
        },
        "agent_steps": [
            {
                "item_id": step["item_id"],
                "step_id": step["step_id"],
                "sequence_index": step["sequence_index"],
                "timestamp": step["timestamp"],
                "agent_message": step["agent_message"],
                "reasoning": step["reasoning"],
                "observation": step["observation"],
                "local_signals": step["local_signals"],
                "tool_calls": [
                    {
                        "item_id": f"{step['item_id']}:tool:{call['call_index']}",
                        "call_index": call["call_index"],
                        "call_id": call["call_id"],
                        "function_name": call["function_name"],
                        "arguments": call["arguments"],
                        "observation": call["observation"],
                        "status": call["status"],
                        "has_observation": call["has_observation"],
                        "signature": excerpt(call["signature"], DEFAULT_TOOL_CHARS),
                        "local_signals": call["local_signals"],
                    }
                    for call in step["tool_calls"]
                ],
            }
            for step in batch
        ],
        "interpretation_note": (
            "The local_signals are deterministic observations from stored artifacts, "
            "not ground truth. Judge whether each target shows a non-representative "
            "inference, transport, harness, or tool failure. Coherent unsuccessful "
            "coding is ordinary_model_struggle, not automatically a harness failure."
        ),
    }


def step_questions(step: dict[str, Any]) -> dict[str, Any]:
    target = step["item_id"]
    return {
        f"{target}:primary": Choice(
            instructions={
                "target_item_id": target,
                "question": "What best characterizes this agent step as evidence?",
            },
            criteria={
                "ordinary_model_struggle": "The trace is coherent and the model is simply exploring, reasoning, or making an incomplete implementation without clear external failure.",
                "inference_or_transport": "The model response or inference stream appears garbled, truncated, disconnected, replayed, or otherwise corrupted.",
                "harness_or_environment": "The runner, container, timeout, filesystem, or tool plumbing appears to have caused the problem independently of the model's coding quality.",
                "ordinary_tool_or_test_failure": "A tool ran normally and reported a code, test, or command failure; this is not itself evidence of broken inference or harness behavior.",
                "ambiguous": "The evidence is insufficient or supports multiple explanations equally.",
            },
        ),
        f"{target}:review": Noul(
            instructions={
                "target_item_id": target,
                "question": "Should this agent step be flagged for human review as potentially non-representative because of inference, transport, harness, or tool-execution problems?",
            },
            criteria={
                "true": "There is concrete or strongly suggestive evidence of a non-representative setup, transport, harness, or tool problem.",
                "false": "The step is ordinary coherent model work or an ordinary code/test failure without such evidence.",
            },
        ),
        f"{target}:message_review": Noul(
            instructions={
                "target_item_id": target,
                "question": "Should the agent's visible message or reasoning content in this step be flagged for review as potentially corrupted by inference or transport problems?",
            },
            criteria={
                "true": "The message itself is garbled, truncated, malformed, disconnected, empty with no coherent accompanying content, or explicitly being redone after a stream failure.",
                "false": "The message is coherent, even if the coding decision is incomplete or unsuccessful.",
            },
        ),
        f"{target}:message_garbled": Noul(
            instructions={
                "target_item_id": target,
                "question": "Is the agent message or reasoning trace itself garbled, truncated, malformed, or a clear response-stream fragment?",
            },
        ),
        f"{target}:garbled": Noul(
            instructions={
                "target_item_id": target,
                "question": "Does this step contain evidence that the model response or reasoning trace was garbled, truncated, malformed, disconnected, or had to be redone because of streaming/inference corruption?",
            },
        ),
        f"{target}:repeat": Noul(
            instructions={
                "target_item_id": target,
                "question": "Is this step part of a repetitive no-progress sequence rather than a purposeful iteration?",
            },
            criteria={
                "true": "The same or substantively equivalent tool action is repeated without new evidence or progress.",
                "false": "The action is new, purposeful, or supported by changing evidence.",
            },
        ),
        f"{target}:severity": Score(
            instructions={
                "target_item_id": target,
                "question": "How strongly should this step be prioritized for review of possible non-representative inference or harness behavior?",
            },
            criteria=[
                "No actionable anomaly",
                "Contextual review: weak or ambiguous signal",
                "High-priority review: concrete anomaly that may affect interpretation",
                "Strong evidence of a non-representative inference, transport, or harness failure",
            ],
        ),
    }


def call_questions(step: dict[str, Any], call: dict[str, Any]) -> dict[str, Any]:
    target = f"{step['item_id']}:tool:{call['call_index']}"
    return {
        f"{target}:primary": Choice(
            instructions={
                "target_call_id": target,
                "question": "What best characterizes this tool call and its result?",
            },
            criteria={
                "ordinary_tool_success": "The tool ran and returned a normal result; no relevant anomaly is visible.",
                "ordinary_tool_or_test_failure": "The tool ran normally but reported a command, code, or test failure.",
                "harness_or_inference_failure": "The call or result shows transport corruption, incomplete execution, timeout, missing output, or runner/environment failure.",
                "repetitive_no_progress": "The call repeats an equivalent previous action without meaningful new evidence or progress.",
                "ambiguous": "The available call and result evidence is insufficient to decide.",
            },
        ),
        f"{target}:review": Noul(
            instructions={
                "target_call_id": target,
                "question": "Should this tool call be flagged for human review because it may reflect inference, transport, harness, or tool-execution failure rather than ordinary coding difficulty?",
            },
            criteria={
                "true": "There is concrete or strongly suggestive evidence that the call is non-representative or should not be treated as ordinary model performance.",
                "false": "The call is ordinary tool use or an ordinary code/test failure with no setup or transport concern.",
            },
        ),
        f"{target}:harness": Noul(
            instructions={
                "target_call_id": target,
                "question": "Does this tool call show a harness, environment, transport, or inference failure?",
            },
            criteria={
                "true": "The call is incomplete, missing its observation, timed out at the runner, disconnected, garbled, or failed because of execution plumbing.",
                "false": "The call completed normally, including when it intentionally reports a code or test failure.",
            },
        ),
        f"{target}:ordinary_failure": Noul(
            instructions={
                "target_call_id": target,
                "question": "Did this call complete normally but report an ordinary command, compilation, test, or code failure?",
            },
            criteria={
                "true": "The command executed and its output reports a normal code/test/command failure.",
                "false": "It succeeded, was incomplete, or the failure is instead a harness/transport/inference problem.",
            },
        ),
        f"{target}:repeat": Noul(
            instructions={
                "target_call_id": target,
                "question": "Is this call a repetitive no-progress action relative to the other calls shown in this batch and its local repetition signals?",
            },
            criteria={
                "true": "It repeats an equivalent action without changing evidence or advancing the work.",
                "false": "It is new, purposeful, or changes the evidence/state.",
            },
        ),
    }


def build_batch_questions(batch: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, tuple[str, str | None]]]:
    questions: dict[str, Any] = {}
    bindings: dict[str, tuple[str, str | None]] = {}
    for step in batch:
        step_id = step["item_id"]
        for question_id, question in step_questions(step).items():
            questions[question_id] = question
            bindings[question_id] = (step_id, None)
        for call in step["tool_calls"]:
            call_id = f"{step_id}:tool:{call['call_index']}"
            for question_id, question in call_questions(step, call).items():
                questions[question_id] = question
                bindings[question_id] = (step_id, call_id)
    return questions, bindings


def analyze_response(response: Any, bindings: dict[str, tuple[str, str | None]]) -> dict[str, Any]:
    answers = getattr(response, "answers", {})
    answers = answers if isinstance(answers, dict) else {}
    by_step: dict[str, dict[str, Any]] = {}
    by_call: dict[str, dict[str, Any]] = {}
    for question_id, (step_id, call_id) in bindings.items():
        answer = answer_to_dict(answers.get(question_id)) if question_id in answers else {"missing": True}
        target = by_call if call_id is not None else by_step
        target_id = call_id if call_id is not None else step_id
        target.setdefault(target_id, {})[question_id.rsplit(":", 1)[-1]] = answer
    metadata = response_metadata(response)
    return {"metadata": metadata, "step_answers": by_step, "call_answers": by_call}


def numeric_noul(answer: dict[str, Any] | None) -> float | None:
    value = answer.get("noul") if isinstance(answer, dict) else None
    return float(value) if isinstance(value, (int, float)) else None


def jev_review_required(jev: dict[str, Any] | None) -> bool:
    if not isinstance(jev, dict):
        return False
    for key in ("review", "message_review", "message_garbled", "garbled", "repeat", "harness", "ordinary_failure"):
        probability = numeric_noul(jev.get(key))
        if key != "ordinary_failure" and probability is not None and probability >= REVIEW_PROBABILITY:
            return True
    primary = jev.get("primary")
    if isinstance(primary, dict):
        choice = primary.get("choice")
        probabilities = primary.get("probabilities", {})
        if choice in {"inference_or_transport", "harness_or_environment", "harness_or_inference_failure", "repetitive_no_progress"}:
            chosen_probability = probabilities.get(choice)
            if isinstance(chosen_probability, (int, float)) and chosen_probability >= 0.5:
                return True
    severity = jev.get("severity")
    if isinstance(severity, dict) and isinstance(severity.get("score"), (int, float)):
        return float(severity["score"]) >= 1.5
    return False


def add_jev_results_to_run(run: dict[str, Any], result: dict[str, Any]) -> None:
    step_answers = result.get("step_answers", {})
    call_answers = result.get("call_answers", {})
    for step in run["steps"]:
        step_jev = step_answers.get(step["item_id"])
        if step_jev is not None:
            step["jev"] = step_jev
        for call in step["tool_calls"]:
            call_id = f"{step['item_id']}:tool:{call['call_index']}"
            call_jev = call_answers.get(call_id)
            if call_jev is not None:
                call["jev"] = call_jev
        step["review_required"] = bool(
            step.get("local_signals", {}).get("long_gap_seconds")
            or step.get("local_signals", {}).get("transport_patterns")
            or step.get("local_signals", {}).get("harness_patterns")
            or step.get("local_signals", {}).get("garbled_patterns")
            or step.get("local_signals", {}).get("repetition_streak", 0) >= 3
            or jev_review_required(step.get("jev"))
            or any(jev_review_required(call.get("jev")) for call in step["tool_calls"])
        )
    run["last_jev_batch"] = result.get("metadata")


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, default=json_default)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def existing_runs_by_id(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        value = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    runs = value.get("runs", []) if isinstance(value, dict) else []
    return {run.get("run_id"): run for run in runs if isinstance(run, dict) and run.get("run_id")}


def merge_reusable_jev(current: dict[str, Any], previous: dict[str, Any] | None) -> int:
    if not previous:
        return 0
    previous_steps = {step.get("item_id"): step for step in previous.get("steps", []) if isinstance(step, dict)}
    reused = 0
    for step in current["steps"]:
        old = previous_steps.get(step["item_id"])
        if not old or old.get("source_fingerprint") != step.get("source_fingerprint"):
            continue
        if old.get("jev") is not None:
            step["jev"] = old["jev"]
            reused += 1
        old_calls = {call.get("call_index"): call for call in old.get("tool_calls", []) if isinstance(call, dict)}
        for call in step["tool_calls"]:
            old_call = old_calls.get(call["call_index"])
            if old_call and old_call.get("jev") is not None:
                call["jev"] = old_call["jev"]
    return reused


def pending_steps(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for step in run["steps"]
        if step.get("jev") is None
        or any(call.get("jev") is None for call in step.get("tool_calls", []))
    ]


def recompute_summary(audit: dict[str, Any]) -> None:
    counts: Counter[str] = Counter()
    total_steps = total_calls = flagged_steps = flagged_calls = pending_steps_count = pending_calls = 0
    for run in audit.get("runs", []):
        for step in run.get("steps", []):
            step["review_required"] = bool(
                step.get("local_signals", {}).get("long_gap_seconds")
                or step.get("local_signals", {}).get("transport_patterns")
                or step.get("local_signals", {}).get("harness_patterns")
                or step.get("local_signals", {}).get("garbled_patterns")
                or step.get("local_signals", {}).get("repetition_streak", 0) >= 3
                or jev_review_required(step.get("jev"))
                or any(jev_review_required(call.get("jev")) for call in step.get("tool_calls", []))
            )
            total_steps += 1
            if step.get("review_required"):
                flagged_steps += 1
            if step.get("jev") is None:
                pending_steps_count += 1
            for key in (
                "transport_patterns",
                "harness_patterns",
                "garbled_patterns",
                "ordinary_tool_patterns",
            ):
                counts.update(step.get("local_signals", {}).get(key, []))
            for call in step.get("tool_calls", []):
                total_calls += 1
                if jev_review_required(call.get("jev")) or call.get("local_signals", {}).get("repeated_no_progress_candidate"):
                    flagged_calls += 1
                if call.get("jev") is None:
                    pending_calls += 1
                for key in ("transport_patterns", "harness_patterns", "ordinary_tool_patterns"):
                    counts.update(call.get("local_signals", {}).get(key, []))
    audit["summary"] = {
        "runs": len(audit.get("runs", [])),
        "agent_steps": total_steps,
        "tool_calls": total_calls,
        "steps_flagged_for_review": flagged_steps,
        "tool_calls_flagged_for_review": flagged_calls,
        "steps_without_jev_result": pending_steps_count,
        "tool_calls_without_jev_result": pending_calls,
        "local_pattern_counts": dict(counts),
    }


def select_run_dirs(args: argparse.Namespace, repo_root: Path) -> list[Path]:
    if args.run:
        candidates = [Path(value) for value in args.run]
    else:
        candidates = [repo_root / args.runs_root / model_dir for model_dir in args.model_dir]
    run_dirs: set[Path] = set()
    for candidate in candidates:
        candidate = candidate if candidate.is_absolute() else repo_root / candidate
        if (candidate / "result.json").exists() and (candidate / "agent" / "trajectory.json").exists():
            run_dirs.add(candidate.resolve())
            continue
        if not candidate.exists():
            print(f"warning: run root does not exist: {candidate}", file=sys.stderr)
            continue
        for result_path in sorted(candidate.glob("*/result.json")):
            run_dir = result_path.parent
            if (run_dir / "agent" / "trajectory.json").exists():
                run_dirs.add(run_dir.resolve())
    return sorted(run_dirs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        action="append",
        default=None,
        help="Model run directory under benchmark/runs; repeat for multiple models. Defaults to DeepSeek and GLM.",
    )
    parser.add_argument(
        "--run",
        action="append",
        help="Analyze one exact run directory; repeat as needed. Overrides --model-dir.",
    )
    parser.add_argument("--runs-root", default="benchmark/runs", help="Root containing model run directories.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--step-chars", type=int, default=DEFAULT_STEP_CHARS)
    parser.add_argument("--tool-chars", type=int, default=DEFAULT_TOOL_CHARS)
    parser.add_argument("--fresh", action="store_true", help="Do not reuse completed records from --output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        raise SystemExit(
            "TYPESAFE_API_KEY is not set. Load it before running, for example: `source .bashrc`."
        )
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")
    if args.model_dir is None:
        args.model_dir = list(DEFAULT_MODEL_DIRS)

    repo_root = Path(__file__).resolve().parents[1]
    output_path = args.output if args.output.is_absolute() else repo_root / args.output
    run_dirs = select_run_dirs(args, repo_root)
    if not run_dirs:
        raise SystemExit("No runs found. Check --model-dir, --run, and benchmark/runs.")

    previous = {} if args.fresh else existing_runs_by_id(output_path)
    audit: dict[str, Any] = {
        "schema_version": "jev-agent-inference-audit-v1",
        "generated_at": now_utc(),
        "updated_at": now_utc(),
        "source_root": str(repo_root),
        "jev_model": "jev-latest",
        "excerpt_limits": {
            "step_chars": args.step_chars,
            "tool_chars": args.tool_chars,
            "log_chars": DEFAULT_LOG_CHARS,
        },
        "interpretation": (
            "Jev judgments are a second-model review signal, not ground truth. "
            "The report retains deterministic local signals and raw artifact paths; "
            "a reviewer should inspect flagged records before excluding a run."
        ),
        "runs": [],
    }

    for run_dir in run_dirs:
        print(f"loading {run_dir}", file=sys.stderr)
        run = make_run_record(
            run_dir,
            repo_root,
            step_chars=args.step_chars,
            tool_chars=args.tool_chars,
        )
        if not args.fresh:
            reused = merge_reusable_jev(run, previous.get(run["run_id"]))
            if reused:
                print(f"  reused Jev results for {reused} step(s)", file=sys.stderr)
        audit["runs"].append(run)

    # Persist the complete local inventory before making network calls.  If the
    # process is interrupted, the inventory and any completed batches remain.
    recompute_summary(audit)
    atomic_write_json(output_path, audit)

    retry = RetryPolicy(
        max_retries=4,
        backoff_initial=1.0,
        backoff_max=20.0,
        timeout=120.0,
    )
    with TypeSafeClient(model="jev-latest", retry=retry, timeout=120.0) as client:
        for run in audit["runs"]:
            pending = pending_steps(run)
            for offset in range(0, len(pending), args.batch_size):
                batch = pending[offset : offset + args.batch_size]
                # If a previous partial result filled the batch while another
                # operation was running, avoid sending it again.
                batch = [
                    step
                    for step in batch
                    if step.get("jev") is None
                    or any(call.get("jev") is None for call in step.get("tool_calls", []))
                ]
                if not batch:
                    continue
                questions, bindings = build_batch_questions(batch)
                state = state_for_batch(run, batch)
                print(
                    f"Jev {run['run_id']}: steps {batch[0]['sequence_index']}–{batch[-1]['sequence_index']} "
                    f"({len(questions)} questions)",
                    file=sys.stderr,
                )
                try:
                    response = client.system_one(
                        model="jev-latest",
                        state=state,
                        questions=questions,
                    )
                    parsed = analyze_response(response, bindings)
                    add_jev_results_to_run(run, parsed)
                except Exception as exc:  # preserve inventory and continue to the next batch
                    error_result = {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "question_count": len(questions),
                    }
                    for step in batch:
                        step["jev_error"] = error_result
                        for call in step.get("tool_calls", []):
                            call.setdefault("jev_error", error_result)
                    run.setdefault("jev_errors", []).append(error_result)
                    print(
                        f"warning: Jev request failed for {run['run_id']} batch: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                audit["updated_at"] = now_utc()
                recompute_summary(audit)
                atomic_write_json(output_path, audit)

    audit["updated_at"] = now_utc()
    recompute_summary(audit)
    atomic_write_json(output_path, audit)
    print(f"wrote {output_path}", file=sys.stderr)
    print(json.dumps(audit["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
