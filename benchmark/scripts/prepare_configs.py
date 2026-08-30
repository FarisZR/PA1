#!/usr/bin/env python3
"""Generate the few benchmark files that contain deployment-specific URLs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.request import urlopen

BENCHMARK_DIR = Path(__file__).resolve().parents[1]
GENERATED_DIR = BENCHMARK_DIR / "generated"
PI_CONFIG_TEMPLATE = BENCHMARK_DIR / "configs" / "pi.yaml"
PI_BASE_URL_SENTINEL = "__LITELLM_OPENAI_BASE_URL__"

CODEX_VERSION = "0.151.0"
CODEX_PROMPT_URL = (
    "https://raw.githubusercontent.com/openai/codex/"
    f"rust-v{CODEX_VERSION}/codex-rs/models-manager/prompt.md"
)
CODEX_PROMPT_SHA256 = "ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807"

# DeepSeek's official Codex integration embeds the complete model catalog in its
# setup script. We parse it as data; the script is never executed.
DEEPSEEK_CODEX_SETUP_URL = (
    "https://cdn.deepseek.com/api-docs/codex-deepseek-setup-en.sh"
)
DEEPSEEK_CODEX_SETUP_SHA256 = (
    "92d72d7027d0f800318d816bf0c12c64c14844ec7f86170f2fc63f7e7254901c"
)

# Kimi's official Codex guide uses CC Switch. This is the exact catalog template
# used by the current CC Switch Kimi preset; we override only Kimi-specific
# capabilities documented by that preset/guide.
CC_SWITCH_COMMIT = "d8065cc628fcd373d00c4363d718095f19e78c9e"
CC_SWITCH_TEMPLATE_URL = (
    "https://raw.githubusercontent.com/farion1231/cc-switch/"
    f"{CC_SWITCH_COMMIT}/src-tauri/src/resources/gpt5_5_template.json"
)
CC_SWITCH_TEMPLATE_SHA256 = (
    "711db8a980e873152498cd601e31166348c685e37c305d0855dbb2e9f6867a52"
)


def load_env_file(path: Path) -> None:
    """Load the dotenv subset used by benchmark/env.local."""
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise SystemExit(f"Invalid env line in {path}: {raw_line!r}")
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key.strip()] = value


def require(name: str) -> str:
    """Return one required non-empty environment variable."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def fetch_verified(url: str, expected_sha256: str) -> bytes:
    """Fetch a frozen reference and reject content drift."""
    with urlopen(url, timeout=30) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        raise SystemExit(
            f"Pinned source changed: {url}\nexpected {expected_sha256}\nactual   {digest}"
        )
    return data


def provider_toml(provider_id: str, name: str, base_url: str, env_key: str) -> str:
    """Render a minimal Codex Responses provider for a deployment URL."""
    return "\n".join(
        [
            'preferred_auth_method = "apikey"',
            'forced_login_method = "api"',
            f"model_provider = {json.dumps(provider_id)}",
            "",
            f"[model_providers.{provider_id}]",
            f"name = {json.dumps(name)}",
            f"base_url = {json.dumps(base_url.rstrip('/'))}",
            'wire_api = "responses"',
            f"env_key = {json.dumps(env_key)}",
            "requires_openai_auth = false",
            "supports_websockets = false",
            "",
        ]
    )


def deepseek_codex_entry() -> dict[str, object]:
    """Return DeepSeek V4 Flash official Codex metadata under the PA1 alias."""
    script = fetch_verified(
        DEEPSEEK_CODEX_SETUP_URL, DEEPSEEK_CODEX_SETUP_SHA256
    ).decode()
    match = re.search(
        r"<<'CODEX_MODELS_JSON'\n(?P<json>\{.*?\})\nCODEX_MODELS_JSON",
        script,
        re.DOTALL,
    )
    if not match:
        raise SystemExit("Could not extract DeepSeek's official Codex model catalog")
    catalog = json.loads(match.group("json"))
    entry = next(
        model for model in catalog["models"] if model["slug"] == "deepseek-v4-flash"
    )

    # Our gateway exposes the benchmark checkpoint under an explicit 0731 alias.
    # Everything else is DeepSeek's official Codex profile verbatim.
    entry["slug"] = "deepseek-v4-flash-0731"
    entry["display_name"] = "DeepSeek-V4-Flash 0731"
    return entry


def kimi_codex_entry() -> dict[str, object]:
    """Return the Kimi K3 Codex profile used by its documented CC Switch setup."""
    template = json.loads(
        fetch_verified(CC_SWITCH_TEMPLATE_URL, CC_SWITCH_TEMPLATE_SHA256).decode()
    )
    template.update(
        {
            "slug": "kimi-k3",
            "display_name": "Kimi K3",
            "description": "Kimi K3",
            "context_window": 1_048_576,
            "max_context_window": 1_048_576,
            "input_modalities": ["text", "image"],
            "default_reasoning_level": "max",
            "supported_reasoning_levels": [
                {
                    "effort": "low",
                    "description": "Fast responses with lighter reasoning",
                },
                {
                    "effort": "high",
                    "description": "Greater reasoning depth for complex problems",
                },
                {
                    "effort": "max",
                    "description": "Maximum reasoning depth for the hardest problems",
                },
            ],
            "priority": 99,
        }
    )
    return template


def opus_codex_entry() -> dict[str, object]:
    """Return generic Codex metadata for Opus behind the direct bridge."""
    instructions = fetch_verified(CODEX_PROMPT_URL, CODEX_PROMPT_SHA256).decode()
    return {
        "slug": "claude-opus-5",
        "display_name": "Claude Opus 5",
        "description": "Claude Opus 5",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [
            {"effort": "medium", "description": "Benchmark reasoning effort"}
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
        "base_instructions": instructions,
        "include_skills_usage_instructions": False,
        "include_plugin_usage_instructions": False,
        "include_apps_usage_instructions": False,
        "supports_reasoning_summary_parameter": True,
        "supports_reasoning_summaries": True,
        "default_reasoning_summary": "auto",
        "support_verbosity": False,
        "default_verbosity": None,
        "apply_patch_tool_type": None,
        "web_search_tool_type": "text",
        "truncation_policy": {"mode": "bytes", "limit": 10000},
        "supports_image_detail_original": False,
        "context_window": 1_000_000,
        "max_context_window": 1_000_000,
        "auto_compact_token_limit": None,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": ["text", "image"],
        "supports_search_tool": False,
        "use_responses_lite": False,
        "node_repl_auto_review_required": False,
        "node_repl_disabled": False,
        "auto_review_model_override": None,
        "model_specialty": None,
        "tool_mode": None,
    }


def render_pi_config(base_url: str) -> str:
    """Insert the existing LiteLLM URL into Pi nested provider overrides."""
    template = PI_CONFIG_TEMPLATE.read_text()
    if template.count(PI_BASE_URL_SENTINEL) != 2:
        raise SystemExit(f"Expected two {PI_BASE_URL_SENTINEL} placeholders")
    return template.replace(PI_BASE_URL_SENTINEL, base_url.rstrip("/"))


def main() -> None:
    """Generate endpoint-dependent Pi and Codex configuration files."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.env_file:
        load_env_file(args.env_file)

    litellm_url = require("LITELLM_OPENAI_BASE_URL")
    opus_bridge_url = require("CODEX_OPUS_RESPONSES_BASE_URL")
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    (GENERATED_DIR / "codex-provenance.json").unlink(missing_ok=True)

    pi_path = GENERATED_DIR / "pi.yaml"
    litellm_path = GENERATED_DIR / "codex-litellm.toml"
    opus_path = GENERATED_DIR / "codex-opus.toml"
    catalog_path = GENERATED_DIR / "codex-thirdparty-models.json"

    pi_path.write_text(render_pi_config(litellm_url))
    litellm_path.write_text(
        provider_toml("litellm", "LiteLLM", litellm_url, "LITELLM_API_KEY")
    )
    opus_path.write_text(
        provider_toml(
            "anthropic_responses",
            "Direct Anthropic via Responses bridge",
            opus_bridge_url,
            "CODEX_OPUS_RESPONSES_API_KEY",
        )
    )
    catalog = {
        "models": [opus_codex_entry(), deepseek_codex_entry(), kimi_codex_entry()]
    }
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")

    for path in (pi_path, litellm_path, opus_path, catalog_path):
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
