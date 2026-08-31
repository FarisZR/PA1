#!/usr/bin/env python3
"""Generate the few benchmark files that contain deployment-specific URLs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parents[1]
GENERATED_DIR = BENCHMARK_DIR / "generated"
CONFIG_DIR = BENCHMARK_DIR / "configs"
CURRENT_MODEL_CONFIGS = {
    "kimi-k3.yaml": 1,
    "deepseek-v4-flash.yaml": 1,
    "luna.yaml": 0,
}
PI_BASE_URL_SENTINEL = "__LITELLM_OPENAI_BASE_URL__"
CLAUDE_OUTPUT_OVERRIDE = "CLAUDE_CODE_MAX_OUTPUT_TOKENS"

CODEX_MODELS_PATH = BENCHMARK_DIR / "references" / "codex-rust-v0.151.0-models.json"
CODEX_MODELS_SHA256 = "eb0d7b9a5dcaf103895c5f8a14c16b269df46e039b375a55ba97f6238542d2ed"
CODEX_BASE_PROFILE = "gpt-5.6-sol"


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


def load_codex_sol_profile() -> dict[str, object]:
    """Load and verify the vendored Codex 0.151.0 GPT-5.6 Sol model profile."""
    data = CODEX_MODELS_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != CODEX_MODELS_SHA256:
        raise SystemExit(
            f"Frozen Codex model catalog changed: {CODEX_MODELS_PATH}\n"
            f"expected {CODEX_MODELS_SHA256}\nactual   {digest}"
        )
    catalog = json.loads(data)
    try:
        profile = next(
            model for model in catalog["models"] if model["slug"] == CODEX_BASE_PROFILE
        )
    except (KeyError, StopIteration) as exc:
        raise SystemExit(
            f"Missing {CODEX_BASE_PROFILE} in frozen Codex catalog"
        ) from exc
    # Verify the frozen source is the intended upstream Sol profile; third-party
    # entries explicitly override this to V1 in third_party_codex_entry().
    if profile.get("multi_agent_version") != "v2":
        raise SystemExit("Frozen GPT-5.6 Sol profile is not Multi-Agent V2")
    return profile


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


def third_party_codex_entry(
    sol_profile: dict[str, object],
    *,
    slug: str,
    display_name: str,
    description: str,
    context_window: int,
    input_modalities: list[str],
    default_reasoning_level: str,
    supported_reasoning_levels: list[dict[str, str]],
    supports_image_detail_original: bool,
) -> dict[str, object]:
    """Clone Sol and change only third-party identity/model metadata."""
    entry = copy.deepcopy(sol_profile)
    entry.update(
        {
            "slug": slug,
            "display_name": display_name,
            "description": description,
            "context_window": context_window,
            "max_context_window": context_window,
            "input_modalities": input_modalities,
            "default_reasoning_level": default_reasoning_level,
            "supported_reasoning_levels": supported_reasoning_levels,
            "supports_image_detail_original": supports_image_detail_original,
            # Third-party routes use Codex Multi-Agent V1 and normal Responses.
            # Multi-Agent V2 and Responses Lite are OpenAI-only compatibility paths.
            "multi_agent_version": "v1",
            "use_responses_lite": False,
        }
    )
    return entry


def deepseek_codex_entry(sol_profile: dict[str, object]) -> dict[str, object]:
    """Return DeepSeek metadata on top of the frozen GPT-5.6 Sol profile."""
    return third_party_codex_entry(
        sol_profile,
        slug="deepseek-v4-flash",
        display_name="DeepSeek V4 Flash 0731",
        description="DeepSeek V4 Flash 0731",
        context_window=1_048_576,
        input_modalities=["text"],
        default_reasoning_level="high",
        supported_reasoning_levels=[
            {
                "effort": "low",
                "description": "Fast responses with lighter reasoning",
            },
            {
                "effort": "high",
                "description": "Extra high reasoning depth for complex problems",
            },
            {
                "effort": "max",
                "description": "Maximum reasoning depth for the hardest problems",
            },
        ],
        supports_image_detail_original=False,
    )


def kimi_codex_entry(sol_profile: dict[str, object]) -> dict[str, object]:
    """Return Kimi K3 metadata on top of the frozen GPT-5.6 Sol profile."""
    return third_party_codex_entry(
        sol_profile,
        slug="kimi-k3",
        display_name="Kimi K3",
        description="Kimi K3",
        context_window=1_048_576,
        input_modalities=["text", "image"],
        default_reasoning_level="max",
        supported_reasoning_levels=[
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
        supports_image_detail_original=True,
    )


def opus_codex_entry(sol_profile: dict[str, object]) -> dict[str, object]:
    """Return Opus metadata on top of the frozen GPT-5.6 Sol profile."""
    return third_party_codex_entry(
        sol_profile,
        slug="claude-opus-5",
        display_name="Claude Opus 5",
        description="Claude Opus 5",
        context_window=1_000_000,
        input_modalities=["text", "image"],
        default_reasoning_level="medium",
        supported_reasoning_levels=[
            {"effort": "medium", "description": "Benchmark reasoning effort"}
        ],
        supports_image_detail_original=False,
    )


def render_model_config(path: Path, base_url: str, expected_sentinels: int) -> str:
    """Insert the LiteLLM URL and reject stale/missing Pi endpoint sentinels."""
    template = path.read_text()
    actual_sentinels = template.count(PI_BASE_URL_SENTINEL)
    if actual_sentinels != expected_sentinels:
        raise SystemExit(
            f"{path}: expected {expected_sentinels} {PI_BASE_URL_SENTINEL} "
            f"placeholder(s), found {actual_sentinels}"
        )
    rendered = template.replace(PI_BASE_URL_SENTINEL, base_url.rstrip("/"))
    if PI_BASE_URL_SENTINEL in rendered:
        raise SystemExit(f"{path}: unresolved {PI_BASE_URL_SENTINEL} placeholder")
    return rendered


def validate_claude_output_policy() -> None:
    """Keep Claude Code on its native per-model output-token behavior."""
    offenders = [
        path.name
        for path in sorted(CONFIG_DIR.glob("*.yaml"))
        if CLAUDE_OUTPUT_OVERRIDE in path.read_text()
    ]
    if offenders:
        joined = ", ".join(offenders)
        raise SystemExit(
            f"Remove {CLAUDE_OUTPUT_OVERRIDE} from benchmark configs: {joined}. "
            "PA1 intentionally leaves Claude Code's output limit unset."
        )


def main() -> None:
    """Generate endpoint-dependent Pi and Codex configuration files."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--include-opus",
        action="store_true",
        help="also generate the deferred Codex Opus bridge config/catalog",
    )
    args = parser.parse_args()
    if args.env_file:
        load_env_file(args.env_file)

    validate_claude_output_policy()

    litellm_url = require("LITELLM_OPENAI_BASE_URL")
    opus_bridge_url = (
        require("CODEX_OPUS_RESPONSES_BASE_URL") if args.include_opus else None
    )

    # Build and validate every output before touching benchmark/generated. A bad
    # frozen catalog or template therefore cannot leave a half-updated run set.
    rendered_models: list[tuple[Path, str]] = []
    for name, expected_sentinels in CURRENT_MODEL_CONFIGS.items():
        source = CONFIG_DIR / name
        destination = GENERATED_DIR / name
        rendered_models.append(
            (
                destination,
                render_model_config(source, litellm_url, expected_sentinels),
            )
        )

    sol_profile = load_codex_sol_profile()
    catalog = {
        "models": [deepseek_codex_entry(sol_profile), kimi_codex_entry(sol_profile)]
    }
    litellm_toml = provider_toml("litellm", "LiteLLM", litellm_url, "LITELLM_API_KEY")
    catalog_json = json.dumps(catalog, indent=2) + "\n"

    opus_toml = None
    opus_catalog_json = None
    if opus_bridge_url is not None:
        opus_toml = provider_toml(
            "anthropic_responses",
            "Direct Anthropic via Responses bridge",
            opus_bridge_url,
            "CODEX_OPUS_RESPONSES_API_KEY",
        )
        opus_catalog_json = (
            json.dumps({"models": [opus_codex_entry(sol_profile)]}, indent=2) + "\n"
        )

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    for obsolete in ("pi.yaml", "codex-provenance.json"):
        (GENERATED_DIR / obsolete).unlink(missing_ok=True)

    for path, contents in rendered_models:
        path.write_text(contents)
        print(f"Wrote {path}")

    litellm_path = GENERATED_DIR / "codex-litellm.toml"
    catalog_path = GENERATED_DIR / "codex-thirdparty-models.json"
    litellm_path.write_text(litellm_toml)
    catalog_path.write_text(catalog_json)
    print(f"Wrote {litellm_path}")
    print(f"Wrote {catalog_path}")

    opus_path = GENERATED_DIR / "codex-opus.toml"
    opus_catalog_path = GENERATED_DIR / "codex-opus-models.json"
    if opus_toml is None or opus_catalog_json is None:
        opus_path.unlink(missing_ok=True)
        opus_catalog_path.unlink(missing_ok=True)
        return

    opus_path.write_text(opus_toml)
    opus_catalog_path.write_text(opus_catalog_json)
    print(f"Wrote {opus_path}")
    print(f"Wrote {opus_catalog_path}")


if __name__ == "__main__":
    main()
