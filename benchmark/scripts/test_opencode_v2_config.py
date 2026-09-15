"""Deterministic checks for the PA1 OpenCode V2 config branch.

These tests only render temporary deployment files.  They never read
``benchmark/env.local`` and never contact a gateway.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parent
BENCHMARK = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
prepare_configs = importlib.import_module("prepare_configs")
verify_opencode_v2 = importlib.import_module("verify_opencode_v2")


def _initialize_spend_ledger(path: str) -> dict:
    return verify_opencode_v2.SpendLedger(Path(path), 2.0).snapshot()


class OpenCodeV2ConfigTests(unittest.TestCase):
    def test_binary_stage_is_atomic_and_repairs_a_corrupt_cache(self) -> None:
        payload = b"pinned-opencode-test-binary"
        with tempfile.TemporaryDirectory(prefix="pa1-opencode-binary-") as tmp:
            root = Path(tmp)
            tarball = root / "opencode.tgz"
            member = tarfile.TarInfo("package/bin/opencode")
            member.size = len(payload)
            with tarfile.open(tarball, "w:gz") as archive:
                archive.addfile(member, io.BytesIO(payload))

            with (
                mock.patch.object(
                    verify_opencode_v2,
                    "OFFLINE_CLI_TARBALL_SHA256",
                    hashlib.sha256(tarball.read_bytes()).hexdigest(),
                ),
                mock.patch.object(
                    verify_opencode_v2,
                    "OFFLINE_CLI_BINARY_SHA256",
                    hashlib.sha256(payload).hexdigest(),
                ),
                mock.patch.object(
                    verify_opencode_v2, "find_pinned_tarball", return_value=tarball
                ),
            ):
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(lambda _: verify_opencode_v2.stage_binary(root), range(4)))
                binary = root / "opencode-v2-bin"
                self.assertEqual(binary.read_bytes(), payload)

                binary.write_bytes(b"corrupt")
                verify_opencode_v2.stage_binary(root)
                self.assertEqual(binary.read_bytes(), payload)

    def test_staged_profiles_use_builtin_models_with_transport_overrides(self) -> None:
        prepare_configs.validate_staged_opencode_v2_profiles()
        luna = (
            BENCHMARK / "deferred" / "opencode-v2" / "luna.yaml"
        ).read_text()
        self.assertIn("model_name: openai/gpt-5.6-luna", luna)
        self.assertIn("variant: max", luna)
        self.assertIn("baseURL: '{env:LITELLM_OPENAI_BASE_URL}'", luna)
        self.assertNotIn("models:", luna)

    def test_staged_profile_rejects_contradictory_inherited_limits(self) -> None:
        rendered = """
providers:
  litellm:
    package: '@opencode-ai/ai/providers/openai/responses'
    models:
      gpt-5.6-luna:
        limit:
          context: 272000
          input: 922000
          output: 128000
"""
        with self.assertRaisesRegex(SystemExit, "built-in openai/gpt-5.6-luna"):
            prepare_configs.validate_staged_opencode_v2_profile(
                Path("luna.yaml"),
                rendered,
                "openai/gpt-5.6-luna",
                require_input=True,
            )

    def test_model_limits_do_not_fall_through_to_sibling_model(self) -> None:
        rendered = """
providers:
  litellm:
    models:
      selected-model:
        name: Selected
      sibling-model:
        limit:
          context: 1000
          output: 100
"""
        with self.assertRaisesRegex(SystemExit, "selected-model.*missing limit"):
            prepare_configs._model_limit_values(rendered, "selected-model")

    def test_live_budget_cap_rejects_nonfinite_nonpositive_and_above_two(self) -> None:
        self.assertEqual(verify_opencode_v2.approved_max_cost("2"), 2.0)
        for value in ("nan", "inf", "0", "-1", "2.000001"):
            with (
                self.subTest(value=value),
                self.assertRaises(argparse.ArgumentTypeError),
            ):
                verify_opencode_v2.approved_max_cost(value)

    def test_acceptance_template_has_explicit_chat_output_override(self) -> None:
        source = BENCHMARK / "configs" / "opencode-v2" / "glm-5.3-flash-acceptance.yaml"
        rendered = prepare_configs.render_model_config(
            source, "https://gateway.example/v1", expected_sentinels=1
        )
        prepare_configs.validate_opencode_v2_config(source, rendered)
        self.assertIn("limit:\n                  context: 1048576", rendered)
        self.assertIn("reasoningEffort: low", rendered)
        self.assertIn("body:\n                  max_tokens: 8192", rendered)
        self.assertIn('version: "2.0.3"', rendered)
        self.assertIn("opencode_v2_checksums:", rendered)
        self.assertIn(
            "linux-arm64: bc35547e678c68aaec1b2aa1623d1d77"
            "ec2585db6204574724826e40f20a7693",
            rendered,
        )
        self.assertIn("input: 0.15", rendered)
        self.assertNotIn("thinking:", rendered)
        self.assertNotIn("__LITELLM_OPENAI_BASE_URL__", rendered)

    def test_generator_writes_nested_opencode_file_without_touching_repo(self) -> None:
        old_generated = prepare_configs.GENERATED_DIR
        old_argv = sys.argv[:]
        old_env = os.environ.copy()
        try:
            with tempfile.TemporaryDirectory(prefix="pa1-opencode-config-") as tmp:
                target = Path(tmp) / "generated"
                env_file = Path(tmp) / "env.local"
                env_file.write_text(
                    "\n".join(
                        [
                            "LITELLM_OPENAI_BASE_URL=https://gateway.example/v1",
                            "LITELLM_API_KEY=test-gateway-key",
                            "CODEX_CLIPROXY_BASE_URL=https://bridge.example/v1",
                            "CODEX_CLIPROXY_API_KEY=test-bridge-key",
                        ]
                    )
                    + "\n"
                )
                prepare_configs.GENERATED_DIR = target
                sys.argv = [
                    "prepare_configs.py",
                    "--env-file",
                    str(env_file),
                ]
                prepare_configs.main()
                generated = target / "opencode-v2" / "glm-5.3-flash-acceptance.yaml"
                self.assertTrue(generated.exists())
                contents = generated.read_text()
                self.assertIn("baseURL: https://gateway.example/v1", contents)
                self.assertIn("reasoningEffort: low", contents)
                self.assertIn("restrict_model: true", contents)
                self.assertIn("opencode_v2_checksums:", contents)
                self.assertNotIn("thinking:", contents)
                smoke = target / "opencode-v2" / "smoke.yaml"
                self.assertTrue(smoke.exists())
                smoke_contents = smoke.read_text()
                self.assertIn("model_name: litellm/glm-5p3-flash", smoke_contents)
                self.assertIn("model_name: openai/gpt-5.6-luna", smoke_contents)
                self.assertIn("variant: low", smoke_contents)
                self.assertNotIn("gpt-5.6-luna:\n", smoke_contents)
                self.assertNotIn("__LITELLM_OPENAI_BASE_URL__", smoke_contents)
        finally:
            prepare_configs.GENERATED_DIR = old_generated
            sys.argv = old_argv
            os.environ.clear()
            os.environ.update(old_env)

    def test_transparent_recorder_reserves_before_forwarding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pa1-opencode-recorder-") as tmp:
            root = Path(tmp)
            fake_dir = root / "fake"
            fake_dir.mkdir()
            provider = verify_opencode_v2.FakeProvider(fake_dir)
            ledger = verify_opencode_v2.SpendLedger(root / "ledger.json", 2.0)
            recorder = verify_opencode_v2.TransparentRecorder(
                upstream=f"http://127.0.0.1:{provider.port}",
                api_key="fake-key",
                ca_file="",
                record_dir=root / "records",
                ledger=ledger,
                context_limit=100,
                route_output_limit=1000,
                input_rate=1e-7,
                output_rate=5e-7,
                cache_read_rate=1e-8,
                cache_creation_rate=0.0,
            )
            try:
                missing_cap = urllib.request.Request(
                    f"http://127.0.0.1:{recorder.port}/v1/chat/completions",
                    data=json.dumps({"model": "glm-5p3-flash"}).encode(),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(missing_cap, timeout=10)
                self.assertEqual(rejected.exception.code, 429)
                rejected.exception.close()

                accepted = urllib.request.Request(
                    f"http://127.0.0.1:{recorder.port}/v1/chat/completions",
                    data=json.dumps(
                        {
                            "model": "glm-5p3-flash",
                            "reasoning_effort": "low",
                            "max_tokens": 128,
                            "stream": True,
                        }
                    ).encode(),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(accepted, timeout=10) as response:
                    self.assertIn(b"[DONE]", response.read())
                state = ledger.snapshot()
                self.assertEqual(state["forwarded"], 1)
                self.assertEqual(state["reservations"], [])
                expected = 10 * 1e-7 + 2 * 1e-8 + 34 * 5e-7
                self.assertAlmostEqual(state["spent_usd"], expected)
                self.assertEqual(len(recorder.records()), 1)
                self.assertEqual(len(provider.requests()), 1)
            finally:
                recorder.stop()
                provider.stop()

    def test_spend_ledger_cycle_initialization_is_cross_process_locked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pa1-opencode-ledger-") as tmp:
            path = Path(tmp) / "ledger.json"
            with concurrent.futures.ProcessPoolExecutor(max_workers=2) as pool:
                states = list(pool.map(_initialize_spend_ledger, [str(path)] * 2))
            state = json.loads(path.read_text())
            self.assertEqual(state["cycles"], 2)
            self.assertEqual(state["forwarded"], 0)
            self.assertEqual(max(item["cycles"] for item in states), 2)

    def test_transparent_recorder_keeps_full_reservation_for_missing_usage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="pa1-opencode-recorder-") as tmp:
            root = Path(tmp)
            fake_dir = root / "fake"
            fake_dir.mkdir()
            provider = verify_opencode_v2.FakeProvider(fake_dir)
            provider.scenario("usage-missing")
            ledger = verify_opencode_v2.SpendLedger(root / "ledger.json", 2.0)
            recorder = verify_opencode_v2.TransparentRecorder(
                upstream=f"http://127.0.0.1:{provider.port}",
                api_key="fake-key",
                ca_file="",
                record_dir=root / "records",
                ledger=ledger,
                context_limit=100,
                route_output_limit=1000,
                input_rate=1e-7,
                output_rate=5e-7,
                cache_read_rate=1e-8,
                cache_creation_rate=0.0,
            )
            try:
                request = urllib.request.Request(
                    f"http://127.0.0.1:{recorder.port}/v1/chat/completions",
                    data=json.dumps(
                        {
                            "model": "glm-5p3-flash",
                            "reasoning_effort": "low",
                            "max_tokens": 128,
                            "stream": True,
                        }
                    ).encode(),
                    headers={"content-type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    response.read()
                state = ledger.snapshot()
                expected = 100 * 1e-7 + 128 * 5e-7
                self.assertAlmostEqual(state["spent_usd"], expected)
                self.assertFalse(recorder.records()[0]["usage_complete"])
            finally:
                recorder.stop()
                provider.stop()

    def test_request_reconciliation_requires_unique_time_and_usage_match(self) -> None:
        result = {
            "records": [
                {
                    "request_id": "req-1",
                    "started_at": 10.0,
                    "finished_at": 11.0,
                    "usage": {"prompt_tokens": 15, "completion_tokens": 9},
                },
                {
                    "request_id": "req-2",
                    "started_at": 20.0,
                    "finished_at": 21.0,
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                },
            ],
            "sessions": [
                {
                    "session": {"id": "ses-root"},
                    "messages": [
                        {
                            "type": "assistant",
                            "id": "msg-1",
                            "time": {"created": 10000, "completed": 11000},
                            "tokens": {
                                "input": 10,
                                "output": 7,
                                "reasoning": 2,
                                "cache": {"read": 3, "write": 2},
                            },
                        },
                        {
                            "type": "assistant",
                            "id": "msg-2",
                            "time": {"created": 20000, "completed": 21000},
                            "tokens": {
                                "input": 4,
                                "output": 2,
                                "reasoning": 0,
                                "cache": {"read": 0, "write": 0},
                            },
                        },
                    ],
                }
            ],
        }
        reconciled, mappings = verify_opencode_v2._reconcile_records(result)
        self.assertTrue(reconciled)
        self.assertEqual(
            [(item["message_id"], item["request_id"]) for item in mappings],
            [("msg-1", "req-1"), ("msg-2", "req-2")],
        )

        incomplete = json.loads(json.dumps(result))
        incomplete["records"][0]["usage"] = {}
        reconciled, _ = verify_opencode_v2._reconcile_records(incomplete)
        self.assertFalse(reconciled)

        result["records"].append(dict(result["records"][0], request_id="req-ambiguous"))
        reconciled, _ = verify_opencode_v2._reconcile_records(result)
        self.assertFalse(reconciled)

    def test_luna_profile_keeps_builtin_identity_and_gateway_override(self) -> None:
        contents = (BENCHMARK / "deferred" / "opencode-v2" / "luna.yaml").read_text()
        self.assertIn("model_name: openai/gpt-5.6-luna", contents)
        self.assertIn("env:\n          - LITELLM_API_KEY", contents)
        self.assertIn("baseURL: '{env:LITELLM_OPENAI_BASE_URL}'", contents)
        self.assertNotIn("models:", contents)


if __name__ == "__main__":
    unittest.main()
