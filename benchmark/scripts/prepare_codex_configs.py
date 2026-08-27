#!/usr/bin/env python3
"""Generate Codex provider TOML from benchmark environment variables.

Pier resolves ${VAR} templates in an agent's ``env`` mapping, but it does not
interpolate arbitrary ``config_toml_file`` contents.  Keeping the generated
TOML out of Git lets the benchmark use environment-specific endpoints without
embedding them in the committed Pier job configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.request import urlopen


BENCHMARK_DIR = Path(__file__).resolve().parents[1]
GENERATED_DIR = BENCHMARK_DIR / "generated"
CODEX_SOURCE_TAG = "rust-v0.150.1"
CODEX_FALLBACK_PROMPT_URL = (
    "https://raw.githubusercontent.com/openai/codex/"
    f"{CODEX_SOURCE_TAG}/codex-rs/models-manager/prompt.md"
)
CODEX_FALLBACK_PROMPT_SHA256 = (
    "ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807"
)


def load_env_file(path: Path) -> None:
    """Load the small dotenv subset used by benchmark/env.local."""
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise ValueError(f"Invalid env line in {path}: {raw_line!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def toml_string(value: str) -> str:
    return json.dumps(value)


def provider_toml(*, provider_id: str, name: str, base_url: str, env_key: str) -> str:
    return "\n".join(
        [
            f"model_provider = {toml_string(provider_id)}",
            "",
            f"[model_providers.{provider_id}]",
            f"name = {toml_string(name)}",
            f"base_url = {toml_string(base_url.rstrip('/'))}",
            'wire_api = "responses"',
            f"env_key = {toml_string(env_key)}",
            "requires_openai_auth = false",
            "supports_websockets = false",
            "",
        ]
    )


def fetch_codex_fallback_instructions() -> str:
    with urlopen(CODEX_FALLBACK_PROMPT_URL, timeout=30) as response:
        raw = response.read()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CODEX_FALLBACK_PROMPT_SHA256:
        raise SystemExit(
            "Pinned Codex fallback prompt hash mismatch: "
            f"expected {CODEX_FALLBACK_PROMPT_SHA256}, got {digest}"
        )
    return raw.decode("utf-8")


def codex_model_entry(
    *,
    slug: str,
    display_name: str,
    effort: str,
    context_window: int,
    input_modalities: list[str],
    base_instructions: str,
) -> dict[str, object]:
    """Build fallback-style Codex metadata with only benchmark-specific limits."""
    return {
        "slug": slug,
        "display_name": display_name,
        "description": f"Benchmark metadata for {display_name}",
        "default_reasoning_level": effort,
        "supported_reasoning_levels": [
            {"effort": effort, "description": "Benchmark reasoning effort"}
        ],
        "shell_type": "unified_exec",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 99,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "default_service_tier": None,
        "availability_nux": None,
        "upgrade": None,
        "model_messages": None,
        "base_instructions": base_instructions,
        "include_skills_usage_instructions": False,
        "include_plugin_usage_instructions": False,
        "include_apps_usage_instructions": False,
        "supports_reasoning_summary_parameter": True,
        "default_reasoning_summary": "auto",
        "support_verbosity": False,
        "default_verbosity": None,
        "apply_patch_tool_type": None,
        "web_search_tool_type": "text",
        "truncation_policy": {"mode": "bytes", "limit": 10000},
        "supports_image_detail_original": False,
        "context_window": context_window,
        "max_context_window": context_window,
        "auto_compact_token_limit": None,
        "comp_hash": None,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": input_modalities,
        "supports_search_tool": False,
        "use_responses_lite": False,
        "node_repl_auto_review_required": False,
        "node_repl_disabled": False,
        "auto_review_model_override": None,
        "model_specialty": None,
        "tool_mode": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional dotenv file to load before reading the endpoint variables.",
    )
    args = parser.parse_args()

    if args.env_file:
        load_env_file(args.env_file)

    litellm_base_url = require("LITELLM_OPENAI_BASE_URL")
    opus_base_url = require("CODEX_OPUS_RESPONSES_BASE_URL")
    base_instructions = fetch_codex_fallback_instructions()

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    (GENERATED_DIR / "codex-litellm.toml").write_text(
        provider_toml(
            provider_id="litellm",
            name="LiteLLM third-party models",
            base_url=litellm_base_url,
            env_key="LITELLM_API_KEY",
        )
    )
    (GENERATED_DIR / "codex-opus.toml").write_text(
        provider_toml(
            provider_id="anthropic_responses",
            name="Anthropic upstream via Responses bridge",
            base_url=opus_base_url,
            env_key="CODEX_OPUS_RESPONSES_API_KEY",
        )
    )
    catalog = {
        "models": [
            codex_model_entry(
                slug="claude-opus-5",
                display_name="Claude Opus 5",
                effort="medium",
                context_window=1_000_000,
                input_modalities=["text", "image"],
                base_instructions=base_instructions,
            ),
            codex_model_entry(
                slug="deepseek-v4-flash-0731",
                display_name="DeepSeek V4 Flash 0731",
                effort="max",
                context_window=1_040_000,
                input_modalities=["text"],
                base_instructions=base_instructions,
            ),
            codex_model_entry(
                slug="kimi-k3",
                display_name="Kimi K3",
                effort="max",
                context_window=1_040_000,
                input_modalities=["text", "image"],
                base_instructions=base_instructions,
            ),
        ]
    }
    (GENERATED_DIR / "codex-thirdparty-models.json").write_text(
        json.dumps(catalog, indent=2) + "\n"
    )

    print(f"Wrote {GENERATED_DIR / 'codex-litellm.toml'}")
    print(f"Wrote {GENERATED_DIR / 'codex-opus.toml'}")
    print(f"Wrote {GENERATED_DIR / 'codex-thirdparty-models.json'}")


if __name__ == "__main__":
    main()
