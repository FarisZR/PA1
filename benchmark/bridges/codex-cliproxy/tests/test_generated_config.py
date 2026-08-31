#!/usr/bin/env python3
"""Acceptance test for what prepare_configs.py actually emits for the bridge.

The translation contract test builds its own inline config, so nothing else
covers the generator's output. A mistake there would reach a benchmark job
silently: the reasoning levels in particular decide whether PA1's `max` effort
survives or is snapped down to `high` before it leaves the bridge.

This generates a config from a throwaway environment into a temporary directory
(never touching benchmark/generated/), boots the pinned image against it, and
checks that CLIProxyAPI both accepts the file and serves the expected models.

Usage:  python3 benchmark/bridges/codex-cliproxy/tests/test_generated_config.py
Requires Docker. Exits non-zero on the first failed check.
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BRIDGE_DIR = TESTS_DIR.parent
BENCHMARK_DIR = BRIDGE_DIR.parents[1]
COMPOSE_FILE = BRIDGE_DIR / "compose.yaml"

BRIDGE_KEY = "pa1-generated-config-test-key"
GATEWAY_KEY = "pa1-generated-config-upstream-key"
GATEWAY_URL = "https://gateway.invalid/v1"
BRIDGE_URL = "http://172.17.0.1/v1"
ANTHROPIC_KEY = "sk-ant-pa1-generated-config-test"
CONTAINER = "pa1-generated-config-test"
HOST_PORT = 18998

failures: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    """Record one acceptance assertion without aborting the run."""
    if condition:
        print(f"  PASS  {label}")
        return
    failures.append(label)
    print(f"  FAIL  {label}" + (f"\n        {detail}" if detail else ""))


def bridge_image() -> str:
    """Read the pinned image reference straight out of compose.yaml."""
    match = re.search(r"^\s*image:\s*(\S+)\s*$", COMPOSE_FILE.read_text(), re.M)
    if not match:
        raise SystemExit(f"No image: line found in {COMPOSE_FILE}")
    return match.group(1)


def run(*args: str, check_rc: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one docker command."""
    result = subprocess.run(args, capture_output=True, text=True)
    if check_rc and result.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed:\n{result.stderr}")
    return result


def generate(target: Path, env_file: Path, include_opus: bool) -> Path:
    """Run the real generator into `target` instead of benchmark/generated."""
    sys.path.insert(0, str(BENCHMARK_DIR / "scripts"))
    prepare_configs = importlib.import_module("prepare_configs")
    importlib.reload(prepare_configs)
    prepare_configs.GENERATED_DIR = target
    argv = ["prepare_configs.py", "--env-file", str(env_file)]
    if include_opus:
        argv.append("--include-opus")
    old_argv, sys.argv = sys.argv, argv
    try:
        prepare_configs.main()
    finally:
        sys.argv = old_argv
    return target / "cliproxy-config.yaml"


def write_env(path: Path, include_opus: bool) -> None:
    """Write a throwaway env file with no real credentials in it."""
    lines = [
        f"LITELLM_OPENAI_BASE_URL={GATEWAY_URL}",
        f"LITELLM_API_KEY={GATEWAY_KEY}",
        "LITELLM_ANTHROPIC_BASE_URL=https://gateway.invalid",
        f"CODEX_CLIPROXY_BASE_URL={BRIDGE_URL}",
        f"CODEX_CLIPROXY_API_KEY={BRIDGE_KEY}",
        "CODEX_CLIPROXY_REQUEST_LOG=false",
    ]
    if include_opus:
        lines.append(f"ANTHROPIC_API_KEY={ANTHROPIC_KEY}")
    path.write_text("\n".join(lines) + "\n")


def static_checks(config: str, target: Path, include_opus: bool) -> None:
    """Assert the invariants that decide what the benchmark measures."""
    label = "opus" if include_opus else "base"

    check(
        not re.findall(
            r"__[A-Z0-9_]+__",
            "\n".join(
                line for line in config.splitlines() if not line.lstrip().startswith("#")
            ),
        ),
        f"{label}: no unresolved template placeholders",
    )
    check(
        f'"{BRIDGE_KEY}"' in config and f'"{GATEWAY_KEY}"' in config,
        f"{label}: credentials substituted into the config",
    )
    check(
        f'base-url: "{GATEWAY_URL}"' in config,
        f"{label}: gateway URL substituted",
    )

    # The reason this whole test exists. CLIProxyAPI snaps an unknown effort to
    # the nearest level it knows, so losing `max` here would silently downgrade
    # every third-party Codex request.
    for model in ("deepseek-v4-flash", "kimi-k3", "glm-5p3"):
        block = config.split(f'- name: "{model}"', 1)
        levels = re.search(r"levels: \[([^\]]*)\]", block[1]) if len(block) > 1 else None
        check(
            levels is not None and '"max"' in levels.group(1),
            f'{label}: {model} declares "max" reasoning',
            levels.group(0) if levels else "model block not found",
        )

    for setting in (
        "request-retry: 0",
        "disable-cooling: true",
        "switch-preview-model: false",
        "stream-bootstrap-buffering: false",
    ):
        check(setting in config, f"{label}: transparency setting {setting!r} present")

    mode = (target / "cliproxy-config.yaml").stat().st_mode & 0o777
    check(mode == 0o600, f"{label}: config written mode 0600", oct(mode))

    toml = (target / "codex-cliproxy.toml").read_text()
    check(
        f'base_url = "{BRIDGE_URL}"' in toml
        and 'env_key = "CODEX_CLIPROXY_API_KEY"' in toml
        and 'wire_api = "responses"' in toml,
        f"{label}: Codex provider TOML points at the bridge",
        toml,
    )

    if include_opus:
        check("claude-api-key:" in config, "opus: Anthropic route present")
        opus_block = config.split("claude-api-key:", 1)[1]
        check(
            "base-url:" not in opus_block,
            "opus: no base-url, so it targets api.anthropic.com directly",
            opus_block[:300],
        )
        check(
            "cloak" not in opus_block and "fingerprint-profile" not in opus_block,
            "opus: no cloaking, so Codex's system prompt is preserved",
        )
    else:
        check(
            "claude-api-key:" not in config,
            "base: no Anthropic route without --include-opus",
        )


def boot_check(config_path: Path) -> None:
    """Boot the pinned image on the generated file and query its model list."""
    run("docker", "rm", "-f", CONTAINER, check_rc=False)
    run(
        "docker", "run", "-d", "--name", CONTAINER,
        "-p", f"127.0.0.1:{HOST_PORT}:8317",
        "-v", f"{config_path}:/CLIProxyAPI/config.yaml:ro",
        bridge_image(),
    )
    models = None
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{HOST_PORT}/v1/models",
                headers={"Authorization": f"Bearer {BRIDGE_KEY}"},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                models = json.loads(response.read().decode())
            break
        except Exception:  # noqa: BLE001 - the bridge is simply not up yet
            time.sleep(1)

    check(
        models is not None,
        "CLIProxyAPI accepts the generated config and serves /v1/models",
        run("docker", "logs", CONTAINER, check_rc=False).stderr[-1500:],
    )
    if models is None:
        return

    served = sorted(entry["id"] for entry in models.get("data", []))
    check(
        served == ["deepseek-v4-flash", "glm-5p3", "kimi-k3"],
        "bridge serves exactly the three benchmark models",
        str(served),
    )

    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{HOST_PORT}/v1/models",
                headers={"Authorization": "Bearer wrong-key"},
            ),
            timeout=5,
        )
        check(False, "bridge rejects an unknown API key", "request succeeded")
    except urllib.error.HTTPError as error:
        check(error.code == 401, "bridge rejects an unknown API key", f"HTTP {error.code}")
    except Exception as error:  # noqa: BLE001
        check(False, "bridge rejects an unknown API key", str(error))


def main() -> int:
    """Run the generator acceptance suite and return a process exit code."""
    if shutil.which("docker") is None:
        raise SystemExit("docker is required to run this acceptance test")
    workdir = Path(tempfile.mkdtemp(prefix="pa1-generated-config-"))
    try:
        for include_opus in (False, True):
            title = "with --include-opus" if include_opus else "default"
            print(f"\n=== generated config, {title} ===")
            target = workdir / ("opus" if include_opus else "base")
            target.mkdir()
            env_file = workdir / f"env-{target.name}"
            write_env(env_file, include_opus)
            config_path = generate(target, env_file, include_opus)
            static_checks(config_path.read_text(), target, include_opus)
            if not include_opus:
                print("\n=== the pinned image booting on that exact file ===")
                boot_check(config_path)
    finally:
        run("docker", "rm", "-f", CONTAINER, check_rc=False)
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All generated-config checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
