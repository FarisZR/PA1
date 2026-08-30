#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Validate the frozen PA1 benchmark configuration before paid runs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml


BENCHMARK_DIR = Path(__file__).resolve().parents[1]
CONFIG_DIR = BENCHMARK_DIR / "configs"
GENERATED_DIR = BENCHMARK_DIR / "generated"
PRICING_PATH = BENCHMARK_DIR / "pricing.yaml"
OPUS_BRIDGE_CONFIG = BENCHMARK_DIR / "bridges" / "codex-opus" / "config.yaml"
EXPECTED_TASKS = {
    "expr-try-catch-errors",
    "katex-multicolumn-array-spans",
    "python-statemachine-state-data-scoping",
    "oxvg-structural-selector-preservation",
    "effect-sse-httpapi-streaming",
    "scriggo-method-declarations",
    "csstree-shorthand-expansion-compression",
    "fastapi-implicit-head-options",
    "boa-hierarchical-evaluation-cancellation",
    "koota-composite-trait-aspects",
}
EXPECTED_HARNESS_VERSIONS = {
    "claude-code.yaml": "2.1.251",
    "codex.yaml": "0.151.0",
    "opencode-v2.yaml": "0.0.0-beta-18684",
    "pi.yaml": "0.84.4",
}


def load_env_file(path: Path) -> None:
    """Load the dotenv subset used by benchmark/env.local into the process."""
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid env line in {path}: {raw_line!r}")
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key.strip()] = value


def read_yaml(path: Path) -> dict:
    """Read one YAML mapping and reject non-mapping documents."""
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"Expected YAML mapping in {path}")
    return data


def agent_by_model(config: dict, model_name: str) -> dict:
    """Return the unique agent entry with the requested model name."""
    matches = [
        agent
        for agent in config.get("agents", [])
        if agent.get("model_name") == model_name
    ]
    if len(matches) != 1:
        raise SystemExit(f"Expected one agent for {model_name!r}, found {len(matches)}")
    return matches[0]


def normalized_endpoint(value: str) -> tuple[str, str, int | None, str]:
    """Normalize a URL enough to detect accidental bridge/proxy reuse."""
    parsed = urlsplit(value.rstrip("/"))
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port,
        parsed.path.rstrip("/"),
    )


def validate_matrix(path: Path, config: dict) -> None:
    """Validate the common four-model, ten-task, concurrency-ten job shape."""
    if config.get("n_concurrent_trials") != 10:
        raise SystemExit(f"{path}: n_concurrent_trials must be 10")
    agents = config.get("agents", [])
    if len(agents) != 4:
        raise SystemExit(f"{path}: expected 4 agents, found {len(agents)}")
    datasets = config.get("datasets", [])
    if len(datasets) != 1 or set(datasets[0].get("task_names", [])) != EXPECTED_TASKS:
        raise SystemExit(
            f"{path}: selected DeepSWE task set does not match the frozen 10 tasks"
        )


def validate_harness_version(path: Path, config: dict) -> None:
    """Ensure every model cell uses the harness version frozen on 2026-08-30."""
    expected = EXPECTED_HARNESS_VERSIONS[path.name]
    versions = {
        str(agent.get("kwargs", {}).get("version")) for agent in config.get("agents", [])
    }
    if versions != {expected}:
        raise SystemExit(
            f"{path}: expected every agent to use harness version {expected}, "
            f"found {sorted(versions)}"
        )


def validate_claude_code(config: dict) -> None:
    """Ensure Claude Code uses 1M-class aliases and the local 272k Luna cap."""
    luna = agent_by_model(config, "gpt-5.6-luna[1m]")
    if str(luna.get("env", {}).get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")) != "272000":
        raise SystemExit(
            "Claude Code Luna must set CLAUDE_CODE_AUTO_COMPACT_WINDOW=272000"
        )
    agent_by_model(config, "deepseek-v4-flash-0731[1m]")
    agent_by_model(config, "kimi-k3[1m]")
    agent_by_model(config, "anthropic/claude-opus-5")


def validate_pi(config: dict, expected_base_url: str) -> None:
    """Ensure Pi keeps native provider IDs, metadata, and generated proxy routing."""
    agent_by_model(config, "anthropic/claude-opus-5")
    agent_by_model(config, "openai/gpt-5.6-luna")
    deepseek = agent_by_model(config, "deepseek/deepseek-v4-flash")
    kimi = agent_by_model(config, "moonshotai/kimi-k3")

    for agent, provider in ((deepseek, "deepseek"), (kimi, "moonshotai")):
        provider_cfg = (
            agent.get("kwargs", {})
            .get("pi_config", {})
            .get("providers", {})
            .get(provider, {})
        )
        if provider_cfg.get("baseUrl", "").rstrip("/") != expected_base_url.rstrip("/"):
            raise SystemExit(
                f"Pi {provider} provider is not routed to LITELLM_OPENAI_BASE_URL"
            )
        if "models" in provider_cfg:
            raise SystemExit(
                f"Pi {provider} provider must not redefine built-in models/pricing"
            )


def validate_pricing(config: dict) -> None:
    """Ensure normalized benchmark prices use upstream model providers only."""
    models = config.get("models", {})
    expected = {
        "claude-opus-5": "anthropic",
        "gpt-5.6-luna": "openai",
        "deepseek-v4-flash-0731": "deepseek",
        "kimi-k3": "moonshotai",
    }
    for model, provider in expected.items():
        entry = models.get(model, {})
        if entry.get("provider") != provider:
            raise SystemExit(f"{model}: pricing provider must be {provider}")
    if "fireworks" in PRICING_PATH.read_text().lower():
        raise SystemExit("benchmark/pricing.yaml must not use Fireworks pricing")


def validate_opus_bridge_config(config: dict) -> None:
    """Ensure the Codex bridge has one direct Anthropic Opus route."""
    model_list = config.get("model_list", [])
    if len(model_list) != 1:
        raise SystemExit("Codex Opus bridge must expose exactly one model route")
    route = model_list[0]
    params = route.get("litellm_params", {})
    if route.get("model_name") != "claude-opus-5":
        raise SystemExit("Codex Opus bridge must expose only claude-opus-5")
    if params.get("model") != "anthropic/claude-opus-5":
        raise SystemExit("Codex Opus bridge upstream must be anthropic/claude-opus-5")
    if params.get("api_key") != "os.environ/ANTHROPIC_API_KEY":
        raise SystemExit(
            "Codex Opus bridge must authenticate upstream with ANTHROPIC_API_KEY"
        )
    master_key = config.get("general_settings", {}).get("master_key")
    if master_key != "os.environ/CODEX_OPUS_RESPONSES_API_KEY":
        raise SystemExit(
            "Codex Opus bridge inbound auth must use CODEX_OPUS_RESPONSES_API_KEY"
        )


def validate_bridge(shared_proxy_url: str, opus_bridge_url: str) -> None:
    """Reject accidental reuse of the shared LiteLLM endpoint for Codex Opus."""
    if normalized_endpoint(shared_proxy_url) == normalized_endpoint(opus_bridge_url):
        raise SystemExit(
            "Codex Opus bridge must be separate from the shared LiteLLM gateway"
        )


def main() -> None:
    """Run all static preflight checks and print a compact success summary."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args()
    load_env_file(args.env_file)

    litellm_base_url = os.environ.get("LITELLM_OPENAI_BASE_URL", "").strip()
    opus_bridge_url = os.environ.get("CODEX_OPUS_RESPONSES_BASE_URL", "").strip()
    if not litellm_base_url or not opus_bridge_url:
        raise SystemExit(
            "LITELLM_OPENAI_BASE_URL and CODEX_OPUS_RESPONSES_BASE_URL are required"
        )
    for key in ("ANTHROPIC_API_KEY", "CODEX_OPUS_RESPONSES_API_KEY"):
        if not os.environ.get(key, "").strip():
            raise SystemExit(f"{key} is required")

    source_configs = {
        path.name: read_yaml(path) for path in sorted(CONFIG_DIR.glob("*.yaml"))
    }
    for name, config in source_configs.items():
        validate_matrix(CONFIG_DIR / name, config)
        validate_harness_version(CONFIG_DIR / name, config)

    validate_claude_code(source_configs["claude-code.yaml"])
    validate_pricing(read_yaml(PRICING_PATH))
    validate_opus_bridge_config(read_yaml(OPUS_BRIDGE_CONFIG))
    validate_bridge(litellm_base_url, opus_bridge_url)

    generated_pi_path = GENERATED_DIR / "pi.yaml"
    if not generated_pi_path.exists():
        raise SystemExit(
            "Missing benchmark/generated/pi.yaml; run prepare_codex_configs.py first"
        )
    generated_pi = read_yaml(generated_pi_path)
    validate_matrix(generated_pi_path, generated_pi)
    validate_pi(generated_pi, litellm_base_url)

    print("Benchmark preflight passed: 4 harness configs, 10 tasks, concurrency 10")
    print("Harness versions: frozen to npm releases available on 2026-08-30")
    print("Claude Code Luna: [1m] alias with local 272000-token compaction window")
    print("Pi: native OpenAI/DeepSeek/Moonshot model metadata with LiteLLM routing")
    print("Pricing: frozen upstream model prices; no Fireworks normalization")
    print(
        "Codex Opus: dedicated bridge has one direct Anthropic route and is separate from shared LiteLLM"
    )


if __name__ == "__main__":
    main()
