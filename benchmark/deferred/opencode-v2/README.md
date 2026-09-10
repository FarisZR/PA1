# Deferred OpenCode 2

OpenCode 2 is blocked on PA1 issue #9 / Pier V2 support and is not part of the
current run. Its staged jobs are split per model so the same model-priority
ordering can be used when the adapter is ready:

1. `kimi-k3.yaml`
2. `deepseek-v4-flash.yaml`
3. `glm-5.3-flash.yaml`
4. `luna.yaml`
5. `opus.yaml` (also subject to the separate Opus deferral)

The GLM job keeps OpenCode's built-in `zai/glm-5.3-flash` profile and overrides
only its API endpoint and transport model ID for the LiteLLM gateway.

Do not run these files with the current Pier `opencode` adapter; that adapter
still targets OpenCode 1 rather than the pinned `@opencode-ai/cli` V2 package.
