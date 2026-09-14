"""Deterministic checks for the PA1 OpenCode V2 config branch.

These tests only render temporary deployment files.  They never read
``benchmark/env.local`` and never contact a gateway.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
BENCHMARK = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
prepare_configs = importlib.import_module("prepare_configs")
verify_opencode_v2 = importlib.import_module("verify_opencode_v2")


class OpenCodeV2ConfigTests(unittest.TestCase):
    def test_acceptance_template_has_explicit_chat_output_override(self) -> None:
        source = BENCHMARK / "configs" / "opencode-v2" / "glm-5.3-flash-acceptance.yaml"
        rendered = prepare_configs.render_model_config(
            source, "https://gateway.example/v1", expected_sentinels=1
        )
        prepare_configs.validate_opencode_v2_config(source, rendered)
        self.assertIn("limit:\n                  context: 1048576", rendered)
        self.assertIn("reasoningEffort: low", rendered)
        self.assertIn("body:\n                  max_tokens: 8192", rendered)
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
                self.assertNotIn("thinking:", contents)
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
                self.assertEqual(len(recorder.records()), 1)
                self.assertEqual(len(provider.requests()), 1)
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

        result["records"].append(dict(result["records"][0], request_id="req-ambiguous"))
        reconciled, _ = verify_opencode_v2._reconcile_records(result)
        self.assertFalse(reconciled)


if __name__ == "__main__":
    unittest.main()
