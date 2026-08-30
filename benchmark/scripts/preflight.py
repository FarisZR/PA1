#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyYAML==6.0.3"]
# ///
"""Validate the frozen PA1 benchmark configuration before paid runs."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import tomllib
import yaml
from prepare_codex_configs import CODEX_FALLBACK_PROMPT_SHA256, CODEX_SOURCE_TAG

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
EXPECTED_HARNESS_MODELS = {
    "claude-code.yaml": {
        "anthropic/claude-opus-5",
        "gpt-5.6-luna[1m]",
        "deepseek-v4-flash-0731[1m]",
        "kimi-k3[1m]",
    },
    "codex.yaml": {
        "openai/gpt-5.6-luna",
        "anthropic/claude-opus-5",
        "deepseek/deepseek-v4-flash-0731",
        "kimi/kimi-k3",
    },
    "opencode-v2.yaml": {
        "anthropic/claude-opus-5",
        "litellm/gpt-5.6-luna",
        "litellm/deepseek-v4-flash-0731",
        "litellm/kimi-k3",
    },
    "pi.yaml": {
        "anthropic/claude-opus-5",
        "openai/gpt-5.6-luna",
        "deepseek/deepseek-v4-flash",
        "moonshotai/kimi-k3",
    },
}
EXPECTED_CODEX_CATALOG = {
    "claude-opus-5": ("medium", 1_000_000),
    "deepseek-v4-flash-0731": ("max", 1_000_000),
    "kimi-k3": ("max", 1_048_576),
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
    try:
        port = parsed.port
    except ValueError as exc:
        raise SystemExit(f"Invalid endpoint port in {value!r}: {exc}") from exc
    if port is None:
        port = {"http": 80, "https": 443}.get(parsed.scheme.lower())
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        port,
        parsed.path.rstrip("/"),
    )


def validate_endpoint(name: str, value: str) -> None:
    """Require an HTTP(S) endpoint and forbid cleartext remote credentials."""
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or not hostname:
        raise SystemExit(f"{name} must be an absolute HTTP(S) URL with a hostname")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise SystemExit(f"{name} has an invalid port: {exc}") from exc
    if parsed.username is not None or parsed.password is not None:
        raise SystemExit(f"{name} must not contain URL-embedded credentials")
    if scheme == "https":
        return

    is_loopback = hostname.lower() == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise SystemExit(f"{name} must use HTTPS unless it targets loopback")


def validate_matrix(path: Path, config: dict) -> None:
    """Validate the common four-model, ten-task, concurrency-ten job shape."""
    if config.get("n_concurrent_trials") != 10:
        raise SystemExit(f"{path}: n_concurrent_trials must be 10")
    agents = config.get("agents", [])
    if len(agents) != 4:
        raise SystemExit(f"{path}: expected 4 agents, found {len(agents)}")
    expected_models = EXPECTED_HARNESS_MODELS.get(path.name)
    if expected_models is not None:
        models = [agent.get("model_name") for agent in agents]
        if len(set(models)) != len(models) or set(models) != expected_models:
            raise SystemExit(f"{path}: configured model set does not match the freeze")
    datasets = config.get("datasets", [])
    task_names = datasets[0].get("task_names", []) if len(datasets) == 1 else []
    if (
        len(task_names) != len(EXPECTED_TASKS)
        or len(set(task_names)) != len(task_names)
        or set(task_names) != EXPECTED_TASKS
    ):
        raise SystemExit(
            f"{path}: selected DeepSWE task set does not match the frozen 10 tasks"
        )


def validate_deepswe_tasks(config: dict) -> None:
    """Ensure every frozen task exists in the checkout Pier will load."""
    dataset = config["datasets"][0]
    tasks_root = (BENCHMARK_DIR.parent / dataset["path"]).resolve()
    if not tasks_root.is_dir():
        raise SystemExit(
            f"DeepSWE tasks directory does not exist: {tasks_root}; clone the pinned checkout first"
        )
    missing = sorted(
        task_id
        for task_id in EXPECTED_TASKS
        if not (tasks_root / task_id / "task.toml").is_file()
    )
    if missing:
        raise SystemExit(f"Pinned DeepSWE checkout is missing tasks: {', '.join(missing)}")


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


def validate_codex_generated_catalog() -> None:
    """Reject stale Codex metadata generated for a different frozen build."""
    expected_tag = f"rust-v{EXPECTED_HARNESS_VERSIONS['codex.yaml']}"
    if CODEX_SOURCE_TAG != expected_tag:
        raise SystemExit(
            f"Codex generator source tag {CODEX_SOURCE_TAG!r} does not match "
            f"frozen harness version {expected_tag!r}"
        )

    catalog_path = GENERATED_DIR / "codex-thirdparty-models.json"
    provenance_path = GENERATED_DIR / "codex-provenance.json"
    for path in (catalog_path, provenance_path):
        if not path.exists():
            raise SystemExit(f"Missing {path}; run prepare_codex_configs.py first")

    try:
        catalog = json.loads(catalog_path.read_text())
        provenance = json.loads(provenance_path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid generated Codex JSON: {exc}") from exc

    if provenance != {
        "codex_source_tag": CODEX_SOURCE_TAG,
        "fallback_prompt_sha256": CODEX_FALLBACK_PROMPT_SHA256,
    }:
        raise SystemExit(
            "Generated Codex provenance does not match the frozen Codex source tag; "
            "rerun prepare_codex_configs.py"
        )

    models = catalog.get("models") if isinstance(catalog, dict) else None
    if not isinstance(models, list):
        raise SystemExit("Generated Codex model catalog must contain a models list")
    by_slug = {
        model.get("slug"): model for model in models if isinstance(model, dict)
    }
    if set(by_slug) != set(EXPECTED_CODEX_CATALOG):
        raise SystemExit("Generated Codex model catalog has an unexpected model set")

    for slug, (effort, context_window) in EXPECTED_CODEX_CATALOG.items():
        model = by_slug[slug]
        instructions = model.get("base_instructions")
        if not isinstance(instructions, str):
            raise SystemExit(f"{slug}: generated Codex base instructions are missing")
        digest = hashlib.sha256(instructions.encode()).hexdigest()
        if digest != CODEX_FALLBACK_PROMPT_SHA256:
            raise SystemExit(
                f"{slug}: generated Codex fallback instructions do not match "
                f"{CODEX_SOURCE_TAG}"
            )
        if model.get("default_reasoning_level") != effort:
            raise SystemExit(f"{slug}: generated Codex reasoning effort is stale")
        if model.get("context_window") != context_window:
            raise SystemExit(f"{slug}: generated Codex context window is stale")


def validate_codex_provider_toml(
    path: Path,
    *,
    provider_id: str,
    expected_base_url: str,
    expected_env_key: str,
) -> None:
    """Validate one generated Codex provider against the current endpoints."""
    if not path.exists():
        raise SystemExit(f"Missing {path}; run prepare_codex_configs.py first")
    try:
        config = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"Invalid generated Codex TOML in {path}: {exc}") from exc
    if config.get("model_provider") != provider_id:
        raise SystemExit(f"{path}: stale Codex model_provider")
    provider = config.get("model_providers", {}).get(provider_id, {})
    if normalized_endpoint(provider.get("base_url", "")) != normalized_endpoint(
        expected_base_url
    ):
        raise SystemExit(f"{path}: generated Codex base URL is stale")
    if provider.get("env_key") != expected_env_key:
        raise SystemExit(f"{path}: generated Codex credential variable is stale")
    if provider.get("wire_api") != "responses":
        raise SystemExit(f"{path}: Codex provider must use the Responses wire API")


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
    validate_endpoint("LITELLM_OPENAI_BASE_URL", litellm_base_url)
    validate_endpoint("CODEX_OPUS_RESPONSES_BASE_URL", opus_bridge_url)
    for key in ("ANTHROPIC_API_KEY", "CODEX_OPUS_RESPONSES_API_KEY"):
        if not os.environ.get(key, "").strip():
            raise SystemExit(f"{key} is required")

    source_configs = {
        path.name: read_yaml(path) for path in sorted(CONFIG_DIR.glob("*.yaml"))
    }
    for name, config in source_configs.items():
        validate_matrix(CONFIG_DIR / name, config)
        validate_harness_version(CONFIG_DIR / name, config)

    validate_deepswe_tasks(source_configs["codex.yaml"])
    validate_claude_code(source_configs["claude-code.yaml"])
    validate_codex_generated_catalog()
    validate_codex_provider_toml(
        GENERATED_DIR / "codex-litellm.toml",
        provider_id="litellm",
        expected_base_url=litellm_base_url,
        expected_env_key="LITELLM_API_KEY",
    )
    validate_codex_provider_toml(
        GENERATED_DIR / "codex-opus.toml",
        provider_id="anthropic_responses",
        expected_base_url=opus_bridge_url,
        expected_env_key="CODEX_OPUS_RESPONSES_API_KEY",
    )
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
    print(f"Codex metadata: generated from and verified against {CODEX_SOURCE_TAG}")
    print("Claude Code Luna: [1m] alias with local 272000-token compaction window")
    print("Pi: native OpenAI/DeepSeek/Moonshot model metadata with LiteLLM routing")
    print("Pricing: frozen upstream model prices; no Fireworks normalization")
    print(
        "Codex Opus: dedicated bridge has one direct Anthropic route and is separate from shared LiteLLM"
    )


if __name__ == "__main__":
    main()
