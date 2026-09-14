# Staged OpenCode V2 jobs

These files are reviewable per-model primary profiles for the new Pier
`opencode-v2` adapter. They remain outside the current primary batch until the
corresponding model launch gate is explicitly approved; adding them here does
not change benchmark selection or start spending. The staged order is:

1. `kimi-k3.yaml`
2. `deepseek-v4p1-flash.yaml`
3. `glm-5.3-flash.yaml`
4. `luna.yaml`
5. `opus.yaml` (also subject to the separate Opus deferral)

All current primary profiles use the `max` variant through the model selection
(`provider/model#max`), including GLM. The acceptance-only profile in
`benchmark/configs/opencode-v2/glm-5.3-flash-acceptance.yaml` is the sole
`low` configuration. It is intentionally separate from the primary jobs.

Kimi and DeepSeek retain their staged OpenAI-compatible Responses transport
and use `max_output_tokens`; GLM uses Chat Completions with `max_tokens`.
Every profile keeps a single `reasoningEffort` and an explicit model-body
output cap. GLM is absent from the frozen 2.0.3 models.dev catalogue, so its
committed profile and provenance are in
`benchmark/references/opencode-v2-glm-5.3-flash.json`. Luna retains the
OpenAI-compatible Responses route. Opus has no gateway base URL and remains on
the direct Anthropic API route.

Do not run a staged job until `benchmark/scripts/verify_opencode_v2.py` has
passed its offline checks and the per-model live/provider gate is recorded.

Issue links: [#41](https://github.com/FarisZR/PA1/issues/41) adapter
implementation and websearch disable, [#40](https://github.com/FarisZR/PA1/issues/40)
max-output regression (explicit model-body override), [#37](https://github.com/FarisZR/PA1/issues/37)
transport-retry policy, [#9](https://github.com/FarisZR/PA1/issues/9) V2 decision.
