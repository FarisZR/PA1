# OpenCode V2 verification report

Revisions: Pier `99327bd04f95e7dc618cb764f422ac8ee8b24816` (base
`13db00f92a4d02a92d7dea17df5dc5e5ef074b30`), PA1 implementation
`a914a71b4726809f9e42dcfb3575e12669b29dc9` (base
`329241b6ac4d71c1681c2e968895451cf9e5794c`), and OpenCode source tag
`v2.0.3` at `d44b52ca66b6bf69626c0384626d1a9cd9555977`. The frozen
`@opencode/cli-linux-x64@2.0.3` archive SHA-256 is
`4b8c2cad67297c715adff18a569c8808b22fe23c7197fd1775bc11cbfa04022d`.

The final Pier revision passed 34/34 offline executable assertions, 65 focused
adapter tests, 127 required existing regressions, and the complete 405-test
Pier suite. The actual-binary control confirmed that `limit.output: 54321`
alone emitted no output-cap field. The model-body override emitted exactly
`max_tokens: 8192`, `reasoning_effort: low`, and no `thinking` field. Native
compaction, retry/failure retention, slow streaming, nested/background trees,
server death, and simultaneous isolated trials are covered offline.

The retained final live cycle passed 15/15 assertions through the authenticated
PA1 gateway route `accounts/fireworks/models/glm-5p3-flash`. It forwarded 12
requests: the small response used 256 completion tokens and finished `length`;
one large response used 8,192 completion tokens and finished `length`. The
repair task earned reward 1 with one unique child and three delegation calls,
including resume of that child. Its reconciled tree used 61,141 prompt, 504
completion, 43,008 cached, 8 reasoning, and 0 cache-write tokens; normalized
cost was $0.00426219. Across all three cycles the ledger recorded 24 forwarded
requests, no outstanding reservation, and $0.0244688 estimated spend under the
$2 cap.

Cycle 1 failed recorder URL wiring before forwarding anything. Cycle 2 passed
14 assertions but its 256-cap response stopped naturally at 97 tokens rather
than truncating. Both failures are retained. The successful third cycle used
the pre-review runner SHA
`46aee595fa7086980ecfba7ffea19cc03b747465d336b11932d64f6aafb56e62`.
The final diff was not submitted to a fourth paid cycle because the required
shared three-cycle cap had been reached; final lifecycle and cleanup changes
were rerun offline instead.

Remaining gates are explicit. The gateway does not publish the route's
131,072-token output ceiling, so only the 8,192-token acceptance cap is proven.
Per-response route IDs establish Fireworks routing, but available metadata does
not independently prove that hidden gateway retries/fallbacks are disabled.
OpenCode 2.0.3 also collapses absent provider usage into an all-zero normalized
object. Its observed native retry delays were seconds, so PA1 #37's roughly
three-minute policy goal is not satisfied. GLM acceptance does not prove the
other staged primary transports. The complete assertion-by-assertion record is
in `opencode-v2-verification.json`.
