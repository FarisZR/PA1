#!/usr/bin/env python3
"""Generate the few benchmark files that contain deployment-specific URLs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

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

# Ports Pier's egress Squid allows out of a trial container. Anything else is
# denied by the generated `acl Safe_ports port 80 443` rule regardless of the
# domain allowlist, so a misconfigured bridge URL must fail generation rather
# than every Codex trial.
PIER_EGRESS_PORTS = {"http": 80, "https": 443}
# CLIProxyAPI refuses to serve /v1 while any of its shipped example keys are
# configured, so a copied placeholder must not reach the generated config.
CLIPROXY_EXAMPLE_KEYS = {"your-api-key-1", "your-api-key-2", "your-api-key-3"}


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


def validate_bridge_url(url: str) -> str:
    """Reject bridge URLs that Pier's egress policy cannot reach."""
    parsed = urlparse(url)
    if parsed.scheme not in PIER_EGRESS_PORTS:
        raise SystemExit(
            f"CODEX_CLIPROXY_BASE_URL must be http:// or https://, got {url!r}"
        )
    host = (parsed.hostname or "").rstrip(".")
    if not host:
        raise SystemExit(f"CODEX_CLIPROXY_BASE_URL has no host: {url!r}")
    # Pier derives the Squid allowlist from this URL's hostname, and a dotless
    # bare name is discarded there, which would deny every Codex request.
    if "." not in host and host != "localhost":
        raise SystemExit(
            f"CODEX_CLIPROXY_BASE_URL host {host!r} is a dotless bare name. Pier "
            "drops it from the egress allowlist; use an IP address or an FQDN."
        )
    port = parsed.port or PIER_EGRESS_PORTS[parsed.scheme]
    if port != PIER_EGRESS_PORTS[parsed.scheme]:
        raise SystemExit(
            f"CODEX_CLIPROXY_BASE_URL port {port} is unreachable from a trial "
            "container. Pier's egress proxy allows only HTTP on 80 and HTTPS "
            "on 443."
        )
    if not parsed.path.rstrip("/").endswith("/v1"):
        raise SystemExit(
            f"CODEX_CLIPROXY_BASE_URL should end in /v1, got {url!r}"
        )
    return url.rstrip("/")


def yaml_quote(value: str) -> str:
    """Quote one scalar for the generated CLIProxyAPI config."""
    return json.dumps(value)


def cliproxy_model_block(entry: dict[str, object]) -> list[str]:
    """Render a CLIProxyAPI model from its generated Codex catalog entry.

    Deriving the reasoning levels from the Codex catalog rather than restating
    them is the point of this function. CLIProxyAPI snaps an incoming
    ``reasoning.effort`` to the nearest level it knows about, and its default
    level set is low/medium/high, so a model left undeclared would silently
    downgrade PA1's ``max`` requests to ``high``.
    """
    levels = [
        str(level["effort"])
        for level in entry["supported_reasoning_levels"]  # type: ignore[index]
    ]
    slug = str(entry["slug"])
    lines = [
        f"      - name: {yaml_quote(slug)}",
        f"        alias: {yaml_quote(slug)}",
        f"        display-name: {yaml_quote(str(entry['display_name']))}",
        f"        max-context-length: {int(entry['context_window'])}",
        "        input-modalities: ["
        + ", ".join(str(modality) for modality in entry["input_modalities"])  # type: ignore[index]
        + "]",
        "        thinking:",
        "          levels: [" + ", ".join(yaml_quote(level) for level in levels) + "]",
    ]
    return lines


def cliproxy_config(
    *,
    bridge_api_key: str,
    litellm_url: str,
    litellm_api_key: str,
    thirdparty_entries: list[dict[str, object]],
    opus_entry: dict[str, object] | None,
    anthropic_api_key: str | None,
    request_log: bool,
) -> str:
    """Render the CLIProxyAPI deployment config for the Codex bridge."""
    if bridge_api_key in CLIPROXY_EXAMPLE_KEYS:
        raise SystemExit(
            "CODEX_CLIPROXY_API_KEY is one of CLIProxyAPI's example keys; "
            "CLIProxyAPI would disable every /v1 route. Choose your own value."
        )

    lines = [
        "# GENERATED by benchmark/scripts/prepare_configs.py - do not edit.",
        "# Contains live credentials. benchmark/generated/ is ignored by Git.",
        'host: ""',
        "port: 8317",
        'auth-dir: "/root/.cli-proxy-api"',
        "",
        "# Credential Codex presents to this bridge. The upstream credentials",
        "# below never leave the runner host.",
        "api-keys:",
        f"  - {yaml_quote(bridge_api_key)}",
        "",
        "# The management API stays off entirely while secret-key is empty.",
        "remote-management:",
        "  allow-remote: false",
        '  secret-key: ""',
        "",
        "debug: false",
        f"request-log: {'true' if request_log else 'false'}",
        "# commercial-mode would disable request logging, which the acceptance",
        "# tests rely on to inspect the translated upstream body.",
        "commercial-mode: false",
        "usage-statistics-enabled: false",
        "",
        "# --- Benchmark integrity ---------------------------------------------",
        "# This layer must be a protocol translator and nothing else. Every",
        "# feature below would otherwise change what PA1 measures: retries and",
        "# cooldowns hide gateway faults and add unattributed spend, and the",
        "# quota fallbacks can silently answer with a different model.",
        "request-retry: 0",
        "max-retry-credentials: 0",
        "max-retry-interval: 0",
        "disable-cooling: true",
        "transient-error-cooldown-seconds: -1",
        "quota-exceeded:",
        "  switch-project: false",
        "  switch-preview-model: false",
        "  antigravity-credits: false",
        "routing:",
        '  strategy: "round-robin"',
        "  session-affinity: false",
        "codex:",
        "  identity-confuse: false",
        "  # Buffering the handshake exists to enable transparent credential",
        "  # failover, which PA1 must not have.",
        "  stream-bootstrap-buffering: false",
        "  # PA1's third-party Codex profile is Multi-Agent V1.",
        "  optimize-multi-agent-v2: false",
        "",
        "# --- Third-party models via the existing LiteLLM gateway --------------",
        "openai-compatibility:",
        '  - name: "litellm"',
        f"    base-url: {yaml_quote(litellm_url.rstrip('/'))}",
        "    api-key-entries:",
        f"      - api-key: {yaml_quote(litellm_api_key)}",
        "    models:",
    ]
    for entry in thirdparty_entries:
        lines.extend(cliproxy_model_block(entry))

    if opus_entry is not None:
        if not anthropic_api_key:
            raise SystemExit("Missing required environment variable: ANTHROPIC_API_KEY")
        levels = [
            str(level["effort"])
            for level in opus_entry["supported_reasoning_levels"]  # type: ignore[index]
        ]
        lines.extend(
            [
                "",
                "# --- Claude Opus 5 straight to the Anthropic API ----------------------",
                "# base-url is omitted on purpose: CLIProxyAPI then targets",
                "# api.anthropic.com directly, never the shared LiteLLM gateway.",
                "# Do not add a cloak block or fingerprint-profile here; those",
                "# would rewrite Codex's system prompt.",
                "claude-api-key:",
                f"  - api-key: {yaml_quote(anthropic_api_key)}",
                "    models:",
                f"      - name: {yaml_quote(str(opus_entry['slug']))}",
                f"        alias: {yaml_quote(str(opus_entry['slug']))}",
                f"        display-name: {yaml_quote(str(opus_entry['display_name']))}",
                f"        max-context-length: {int(opus_entry['context_window'])}",
                "        thinking:",
                "          levels: ["
                + ", ".join(yaml_quote(level) for level in levels)
                + "]",
            ]
        )

    return "\n".join(lines) + "\n"


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
        help="also generate the deferred Codex Opus catalog and bridge route",
    )
    args = parser.parse_args()
    if args.env_file:
        load_env_file(args.env_file)

    validate_claude_output_policy()

    litellm_url = require("LITELLM_OPENAI_BASE_URL")
    litellm_api_key = require("LITELLM_API_KEY")
    bridge_url = validate_bridge_url(require("CODEX_CLIPROXY_BASE_URL"))
    bridge_api_key = require("CODEX_CLIPROXY_API_KEY")
    request_log = os.environ.get("CODEX_CLIPROXY_REQUEST_LOG", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }

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
    thirdparty_entries = [
        deepseek_codex_entry(sol_profile),
        kimi_codex_entry(sol_profile),
    ]
    catalog_json = json.dumps({"models": thirdparty_entries}, indent=2) + "\n"

    # Retained as the control for issue #31: this is the direct corporate-gateway
    # Codex route that Fireworks rejects. No current job references it.
    litellm_toml = provider_toml("litellm", "LiteLLM", litellm_url, "LITELLM_API_KEY")
    # The route every third-party Codex job actually uses.
    bridge_toml = provider_toml(
        "cliproxy", "CLIProxyAPI", bridge_url, "CODEX_CLIPROXY_API_KEY"
    )

    opus_entry = opus_codex_entry(sol_profile) if args.include_opus else None
    opus_catalog_json = (
        json.dumps({"models": [opus_entry]}, indent=2) + "\n"
        if opus_entry is not None
        else None
    )
    bridge_config = cliproxy_config(
        bridge_api_key=bridge_api_key,
        litellm_url=litellm_url,
        litellm_api_key=litellm_api_key,
        thirdparty_entries=thirdparty_entries,
        opus_entry=opus_entry,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip() or None,
        request_log=request_log,
    )

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    (GENERATED_DIR / "cliproxy-logs").mkdir(exist_ok=True)
    for obsolete in (
        "pi.yaml",
        "codex-provenance.json",
        "codex-opus.toml",
    ):
        (GENERATED_DIR / obsolete).unlink(missing_ok=True)

    for path, contents in rendered_models:
        path.write_text(contents)
        print(f"Wrote {path}")

    for name, contents in (
        ("codex-litellm.toml", litellm_toml),
        ("codex-cliproxy.toml", bridge_toml),
        ("codex-thirdparty-models.json", catalog_json),
    ):
        path = GENERATED_DIR / name
        path.write_text(contents)
        print(f"Wrote {path}")

    bridge_config_path = GENERATED_DIR / "cliproxy-config.yaml"
    # Tighten the mode before the credentials are written, not after.
    bridge_config_path.touch(mode=0o600, exist_ok=True)
    bridge_config_path.chmod(0o600)
    bridge_config_path.write_text(bridge_config)
    print(f"Wrote {bridge_config_path}")

    opus_catalog_path = GENERATED_DIR / "codex-opus-models.json"
    if opus_catalog_json is None:
        opus_catalog_path.unlink(missing_ok=True)
        return
    opus_catalog_path.write_text(opus_catalog_json)
    print(f"Wrote {opus_catalog_path}")


if __name__ == "__main__":
    main()
