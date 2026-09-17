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

Kimi, DeepSeek, GLM, and Luna use the `max` variant through the adapter's
`kwargs.variant: max`; the adapter emits OpenCode's native
`provider/model#max` selection internally. Direct-Anthropic Opus remains
`medium`. The tiny GLM/Luna smoke and dedicated GLM delegation smoke under
`benchmark/configs/opencode-v2/` use `low`; both are intentionally separate
from the primary jobs.

Kimi uses its built-in Chat Completions profile and Luna retains the built-in
OpenAI Responses profile. OpenCode 2.0.4's frozen catalogue predates both the
GLM 5.3 Flash and DeepSeek V4.1 Flash entries, so those profiles explicitly
copy their current models.dev metadata while overriding only the gateway model
ID, endpoint, key, and required output cap. Every profile keeps one effective
`reasoningEffort` per variant and an explicit model-body output cap.
Luna's inherited limits are `context: 1050000`, `input: 922000`, and
`output: 128000`, matching the current models.dev profile. Although the live
models.dev catalogue now lists `zai/glm-5.3-flash`, the pinned OpenCode 2.0.4
binary still embeds the older catalogue and runs with model fetching disabled.
GLM therefore uses the canonical `zai/glm-5.3-flash` identity with an explicit
profile and provenance rather than inventing a LiteLLM identity; its profile
and provenance are in `benchmark/references/opencode-v2-glm-5.3-flash.json`.
Opus has no gateway base URL and remains on the direct Anthropic API route.

Web search is disabled in every staged and smoke OpenCode V2 profile
except Kimi K3, which explicitly selects the native `random` web-search
provider. This policy is tracked in [#48](https://github.com/FarisZR/PA1/issues/48).

Do not run a staged job until `benchmark/scripts/verify_opencode_v2.py` has
passed its offline checks and the per-model live/provider gate is recorded.

Issue links: [#41](https://github.com/FarisZR/PA1/issues/41) adapter
implementation, [#48](https://github.com/FarisZR/PA1/issues/48) web-search
policy, [#40](https://github.com/FarisZR/PA1/issues/40)
max-output regression (explicit model-body override), [#37](https://github.com/FarisZR/PA1/issues/37)
transport-retry policy, [#9](https://github.com/FarisZR/PA1/issues/9) V2 decision.
