# CLIProxyAPI #5659 backport

## Decision

PA1 keeps CLIProxyAPI on the same upstream base used by the initial benchmark,
`v7.2.146` (`d31b15916d15b550bbf388fd6da4a47d4d864109`), and applies exactly one
upstream behavioral patch:

- upstream fix: `c8ecb4f3c972664aae802e21c6a6743d5f6bd80a`
- fork: `FZR-forks/CLIProxyAPI`
- fork branch: `pa1/v7.2.146-5659`
- cherry-picked fix commit: `bf3c396d`
- packaging/workflow commit: `2e7c8121`
- image: `ghcr.io/fzr-forks/cliproxyapi:7.2.146-pa1-5659`
- pinned image digest: `sha256:26de0755cf37765291e149590e13ee354010c8caa7b25ec3981827f2d606d6dc`

The later fork commit that adds the GHCR build workflow is packaging only; the
runtime source delta from upstream `v7.2.146` remains the single backport above.

## Problem and trigger

Codex 0.151.0 talks OpenAI Responses to CLIProxyAPI. The benchmark's
Fireworks-backed models are exposed to CLIProxyAPI through an OpenAI-compatible
Chat Completions stream, where reasoning and visible text are separate fields:

```json
{"delta":{"reasoning_content":"...thinking..."}}
{"delta":{"content":"...visible answer..."}}
```

Normally those arrive in separate SSE chunks. The #5659 bug is triggered only
when one upstream chunk contains both fields with non-empty values, for example:

```json
{"delta":{"reasoning_content":"last thought","content":"Answer"}}
```

In `v7.2.146` the translator handles `content` first. That closes the current
reasoning item, opens the message item, and only then notices the reasoning text
from the same chunk. It therefore opens another reasoning item after the
message has already begun. The resulting Responses event order is malformed.

The upstream fix only changes the order within such a chunk: process
`reasoning_content` first, then `content`. Reasoning-only, content-only, and
reasoning-plus-tool-call chunks keep the same behavior.

## Observed effect and benchmark risk

The upstream #5659 reproduction showed truncated Codex display output after the
mixed chunk, but the complete assistant message was still present in Codex's
session file. The demonstrated defect is therefore Responses event ordering and
client presentation, not model-text loss. No upstream evidence showed hidden
reasoning being converted into visible answer text.

Malformed event ordering is still undesirable for an agent benchmark because a
client could in principle attach meaning to item boundaries. The backport is
used to remove that known protocol defect without importing unrelated proxy
changes.

For the already completed Kimi K3 run, the CLIProxy access logs were inspected
for the exact trigger. The count of streamed chunks with simultaneous non-empty
`content` and `reasoning_content` was **0**. Consequently the patched code path
would not have been exercised by the observed Kimi traffic. This is why the
historical Kimi result is retained rather than rerun.

For later runs, including DeepSeek V4.1 Flash, the patched image is used
preemptively. The upstream issue was reproduced with DeepSeek V4 Flash, so PA1
does not assume that provider chunk boundaries will always keep the two fields
separate.

## Why not upgrade CLIProxyAPI

The first released upstream version containing `c8ecb4f3` is `v7.2.158`, but
`v7.2.158` is 184 commits ahead of `v7.2.146`. Those commits include unrelated
Codex/OpenAI-compatibility changes such as tool and request translation fixes.
Moving to that release would therefore change a larger part of the transport
layer than the defect requires and would weaken comparability with the Kimi
run.

The selected approach keeps the experimental delta narrow and auditable:

1. start from the exact original `v7.2.146` source;
2. cherry-pick the upstream #5659 fix only;
3. run the upstream translator regression tests;
4. build the fork as a pinned GHCR image;
5. keep the existing PA1 bridge configuration and Codex harness version
   unchanged.

## Verification

- The upstream fix cherry-picked cleanly onto `v7.2.146`.
- `go test ./internal/translator/openai/openai/responses` passed on the patched
  source. The upstream commit includes regression tests for a chunk containing
  both fields and checks strict `reasoning -> message` output ordering.
- GitHub Actions built and pushed the patched source as
  `ghcr.io/fzr-forks/cliproxyapi:7.2.146-pa1-5659`.
- The image is pinned in `compose.yaml` by OCI digest, so later rebuilds cannot
  silently change benchmark behavior.
- PA1's deterministic bridge translation test passed against the pinned image.
- PA1's generated-config test also passed and booted this exact image on both
  the default and deferred-Opus generated configurations.

Upstream issue: `router-for-me/CLIProxyAPI#5659`.
