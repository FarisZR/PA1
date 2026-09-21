# OpenCode V2 primary jobs

These files are the active per-model primary profiles for Pier's
`opencode-v2` adapter. The adapter, model isolation, transport behavior, and
provider paths have been validated for the current benchmark setup. OpenCode V2
runs as the fourth harness in a separate per-model job. The profile order is:

1. `kimi-k3.yaml`
2. `deepseek-v4p1-flash.yaml`
3. `glm-5.3-sub.yaml` (current direct Z.AI route)
4. `glm-5.3-flash.yaml` (historical Fireworks route)
5. `luna.yaml`
6. `opus.yaml` (also subject to the separate Opus deferral)

Kimi, DeepSeek, GLM, and Luna use the `max` variant through the adapter's
`kwargs.variant: max`; the adapter emits OpenCode's native
`provider/model#max` selection internally. Direct-Anthropic Opus uses
`medium`. The tiny current-routes smoke covers direct Z.AI GLM, CLIProxyAPI
Luna, and LiteLLM-backed DeepSeek at `low`. The dedicated GLM delegation smoke
also uses `low` and remains intentionally separate from the primary jobs.

Luna retains the built-in OpenAI Responses profile and redirects only its
endpoint/key to the shared CLIProxyAPI instance backed by the ChatGPT
subscription. The current smoke uses the same route. DeepSeek's smoke leg uses
the same LiteLLM/Fireworks transport override as its primary profile, while
GLM's smoke leg uses the direct Z.AI Coding Plan endpoint from
`glm-5.3-sub.yaml`. Kimi K3, DeepSeek V4.1 Flash, and GLM keep their canonical OpenCode model identities
(`moonshotai/kimi-k3`, `deepseek/deepseek-v4p1-flash`, and
`zai/glm-5.3-flash`). Their provider configs override only the transport
package, gateway model ID, endpoint, key, and required output cap to use the
Fireworks route. The frozen catalogue therefore keeps the upstream metadata
profile and model identity instead of creating synthetic `fireworks/*`
models. For GLM, the Fireworks transport also avoids the Z.AI-specific
OpenAI-compatible thinking injection that conflicts with `reasoningEffort`
(PA1 [#53](https://github.com/FarisZR/PA1/issues/53)). Every profile keeps one
effective `reasoningEffort` per variant and an explicit model-body output cap.
Luna's models.dev profile inherits `context: 1050000` and `input: 922000`,
but the primary profile overrides them to `context: 272000`, `input: 144000`,
`output: 128000`. PA1 [#17](https://github.com/FarisZR/PA1/issues/17) holds
Luna to 272,000 tokens in every harness, the frozen catalogue prices input
above 272,000 at 2x, and `benchmark/pricing.yaml` models no such tier, so
inheriting the full window would under-cost this harness rather than measure
it. The pinned OpenCode 2.0.8 run uses the committed catalogue with model
fetching disabled.

The current GLM subscription job is `glm-5.3-sub.yaml`. It keeps the
same upstream `zai/glm-5.3-flash` identity but uses OpenCode's native Z.AI
provider with `ZHIPU_API_KEY` mapped from `ZAI_API_KEY` and overrides only
the endpoint to `https://api.z.ai/api/coding/paas/v4`, which is required for a
Coding Plan credential. It intentionally does not carry the Fireworks package,
`canonical: fireworks`, `glm-5p3-flash` transport alias, or the Fireworks-only
reasoning compatibility overrides.

GLM therefore keeps the upstream `zai/glm-5.3-flash` metadata and identity
while using the Fireworks transport; its profile and provenance are in
`benchmark/references/opencode-v2-glm-5.3-flash.json`. The GLM profiles also
set `providers.zai.canonical: fireworks`: OpenCode 2.0.8 injects the Z.AI-only
`tool_stream: true` extension whenever the resolved transport provider is
zai/zhipuai and tools are present, and the Fireworks-backed gateway route
rejects it with HTTP 400. The flag keys off `canonical ?? providerID` and has
no model-level override, so redirecting `canonical` at the transport is the
only way to keep the canonical benchmark identity.
Opus has no gateway base URL and uses the validated direct Anthropic API route.

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
