# OpenCode V2 primary jobs

These files contain the active per-model primary profiles for Pier's
`opencode-v2` adapter together with retained GLM profiles from the excluded
attempt. OpenCode V2 runs as the fourth harness in a separate per-model job.
The active profile order is:

1. `kimi-k3.yaml`
2. `deepseek-v4p1-flash.yaml`
3. `luna.yaml`
4. `opus.yaml`

Kimi, DeepSeek, and Luna use the `max` variant through the adapter's
`kwargs.variant: max`; direct-Anthropic Opus uses `medium`. The current
smoke covers CLIProxyAPI-backed Luna and LiteLLM-backed DeepSeek at `low`.

`glm-5.3-sub.yaml`, `glm-5.3-flash.yaml`, and
`delegation-smoke.yaml` are retained only to reproduce the excluded GLM
attempt. They are not part of the current benchmark matrix, are not generated
by `prepare_configs.py`, and are not executed by the current smoke tests.

Luna retains the built-in OpenAI Responses profile and redirects only its
endpoint/key to the shared CLIProxyAPI instance backed by the ChatGPT
subscription. DeepSeek keeps its canonical
`deepseek/deepseek-v4p1-flash` identity while using the LiteLLM/Fireworks
transport override. Kimi K3 keeps its canonical `moonshotai/kimi-k3`
identity. Opus has no gateway base URL and uses the validated direct Anthropic
API route.

Restricted trials install the committed frozen catalog and narrow it to the
selected model and variant before starting OpenCode. This is required by 2.0.8:
the native subagent tool can select any model exposed by the catalog, so agent
configuration pinning alone is not a model-isolation boundary.

Web search is disabled in every primary and smoke OpenCode V2 profile
except Kimi K3, which explicitly selects the native `random` web-search
provider. This policy is tracked in [#48](https://github.com/FarisZR/PA1/issues/48).

Before a full primary run after changing the runner, provider, or model
configuration, rerun `benchmark/scripts/verify_opencode_v2.py` and the relevant
cheap live/provider gate.

Issue links: [#41](https://github.com/FarisZR/PA1/issues/41) adapter
implementation, [#48](https://github.com/FarisZR/PA1/issues/48) web-search
policy, [#40](https://github.com/FarisZR/PA1/issues/40)
max-output regression (explicit model-body override), [#37](https://github.com/FarisZR/PA1/issues/37)
transport-retry policy, [#9](https://github.com/FarisZR/PA1/issues/9) V2 decision.
