# OpenCode V2 verification report

Revisions: Pier `3c062526f03ed9c1df83326e32399739e0a604c0` (base
`13db00f92a4d02a92d7dea17df5dc5e5ef074b30`), PA1 implementation
`614dbaa7f02cec3e50e36a99b75be33a3312ca1c` (base
`329241b6ac4d71c1681c2e968895451cf9e5794c`), and OpenCode source tag
`v2.0.3` at `d44b52ca66b6bf69626c0384626d1a9cd9555977`. The frozen
`@opencode/cli-linux-x64@2.0.3` archive SHA-256 is
`4b8c2cad67297c715adff18a569c8808b22fe23c7197fd1775bc11cbfa04022d`;
the arm64 archive SHA-256 is
`bc35547e678c68aaec1b2aa1623d1d77ec2585db6204574724826e40f20a7693`.

The final Pier revision passed 34/34 offline executable assertions, 91 focused
adapter tests, 127 required existing regressions, and the complete 431-test
Pier suite. The actual-binary control
confirmed that `limit.output: 54321`
alone emitted no output-cap field. The model-body override emitted exactly
`max_tokens: 8192`, `reasoning_effort: low`, and no `thinking` field. Native
compaction, retry/failure retention, slow streaming, nested/background trees,
server death, and simultaneous isolated trials are covered across 31 offline
fake-provider requests.

The review-response revision additionally shell-quotes install URLs, creates
the prebuilt runtime directory as root, includes remote MCP hosts in filtered
egress, forwards ambient config templates and missing Google/Llama variables,
rejects cleartext remote provider URLs, bounds raw polling evidence by count
and bytes, rejects unexpected server death as complete collection, filters
malformed session records, and prevents incomplete children from inheriting a
root-only completeness marker. The latest review moved the release and
target-specific checksums into PA1 configuration, preserved explicit
model-level transport bodies, made unrestricted per-agent models usable, and
made restricted provenance mismatches hard failures while retaining raw usage
evidence. The final offline artifact SHA-256 is
`0cbf5540bdd2d12963823b07368a0ed5d838ac234b72475ac07f56058db8797e`;
the runner SHA-256 is
`0c5b782ee4de11b06aecf4fd6fb2e26c8268345d0f4f446eeb6c630ba38d1483`.

The retained final live cycle passed 15/15 assertions through the authenticated
PA1 gateway route `accounts/fireworks/models/glm-5p3-flash`. It forwarded 12
requests: the small response used 256 completion tokens and finished `length`;
one large response used 8,192 completion tokens and finished `length`. The
repair task earned reward 1 with one unique child and three delegation calls,
including resume of that child. Its reconciled tree used 61,141 prompt, 504
completion, 43,008 cached, 8 reasoning, and 0 cache-write tokens; normalized
cost was $0.00426219. Across all three cycles the ledger recorded 24 forwarded
requests, no outstanding reservation, and a historical $0.0244688 estimate.
That estimate used the published input/output rates before the final verifier
required an explicit cache-creation rate, so it is not accepted as proof of the
$2 budget gate.

Cycle 1 failed recorder URL wiring before forwarding anything. Cycle 2 passed
14 assertions but its 256-cap response stopped naturally at 97 tokens rather
than truncating. Both failures are retained. The successful third cycle used
the pre-review runner SHA
`46aee595fa7086980ecfba7ffea19cc03b747465d336b11932d64f6aafb56e62`.
The final diff was not submitted to a fourth paid cycle because the required
shared three-cycle cap had been reached; final lifecycle, cleanup, budget,
usage-completeness, egress, and route-provenance changes were rerun offline or
covered by deterministic regressions instead.

Remaining gates are explicit. The gateway does not publish the route's
131,072-token output ceiling, so only the 8,192-token acceptance cap is proven.
It also omits a cache-creation rate. The final verifier therefore blocks before
creating a ledger cycle or forwarding a request instead of assuming the
surcharge is zero; the required live budget assertion remains blocked.
Per-response route IDs establish Fireworks routing, but available metadata does
not independently prove that hidden gateway retries/fallbacks are disabled.
OpenCode 2.0.3 also collapses absent provider usage into an all-zero normalized
object. Its observed native retry delays were seconds, so PA1 #37's roughly
three-minute policy goal is not satisfied. GLM acceptance does not prove the
other staged primary transports. The complete assertion-by-assertion record is
in `opencode-v2-verification.json`.
