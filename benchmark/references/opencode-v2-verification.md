# OpenCode V2 verification report

Revisions: Pier `635593f6d832176fb2a9def75cc04a266f751f9c` (base
`13db00f92a4d02a92d7dea17df5dc5e5ef074b30`), PA1 configuration fix
`b561f2602bbe1c2c0a8b7bbc76d211e33cda8dd7` (base
`329241b6ac4d71c1681c2e968895451cf9e5794c`), and OpenCode source tag
`v2.0.3` at `d44b52ca66b6bf69626c0384626d1a9cd9555977`. The frozen
`@opencode/cli-linux-x64@2.0.3` archive SHA-256 is
`4b8c2cad67297c715adff18a569c8808b22fe23c7197fd1775bc11cbfa04022d`;
the arm64 archive SHA-256 is
`bc35547e678c68aaec1b2aa1623d1d77ec2585db6204574724826e40f20a7693`.

The final Pier revision passed 38/38 offline executable assertions, 120 focused
adapter tests, 127 required existing regressions, and the complete 460-test
Pier suite. The actual-binary control
confirmed that `limit.output: 54321`
alone emitted no output-cap field. The model-body override emitted exactly
`max_tokens: 8192`, `reasoning_effort: low`, and no `thinking` field. Native
compaction, retry/failure retention, slow streaming, nested/background trees,
server death, and simultaneous isolated trials are covered across 34 offline
fake-provider requests. Actual-binary Responses probes also passed for the
staged Kimi K3, DeepSeek V4.1 Flash, and GPT-5.6 Luna profiles with their
primary `max` reasoning setting and configured output ceilings.

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
evidence. It also forwards temporary Bedrock session credentials and redacts
config-referenced credential values from Pier debug metadata without changing
the child process environment, prevents floating `latest` installs from
reusing a stale Docker image while keeping pinned cache identity stable, and
installs Python for the packaged runner in minimal task images. Provider
allowlisting now follows every enabled agent model, while restricted trials
derive that set only after model pinning; a regression proves a conflicting
child provider cannot widen a restricted trial. The
final offline artifact SHA-256 is
`6c33a27d8185974d51000f8b6110c78329924866547aa18e42aaa7223f24edf0`;
the runner SHA-256 is
`80cffb2334e7bba6529bea495dc2f6b58a82196570ebe346a82f9525a9c08f97`.

The configuration review found that the three staged Responses profiles named
an entrypoint absent from OpenCode 2.0.3 and that Luna inherited a contradictory
922,000-token input limit. They now use the frozen binary's built-in
`@opencode-ai/ai/providers/openai/responses` entrypoint; Luna explicitly resolves
to context/input/output limits of 272,000/144,000/128,000. The offline verifier's
shared binary cache is now locked, atomically populated, and checked against the
frozen executable digest on every use.

The follow-up review also covered inherited top-level model providers in the
egress allowlist and repeated session/message cursors. The former now includes
the top-level provider even when an enabled agent omits its own model; the latter
raises a collection error instead of silently truncating a looping page stream.

The Copilot review hardening additionally deduplicates provenance checks before
restriction validation, rejects provider URLs without hostnames, requires an
explicitly true collection manifest, bounds CLI output capture, batches active
session checks per snapshot, transfers config JSON outside logged shell commands,
and excludes credential stores and symlinks from preserved private state.

The Opus review follow-up keeps the frozen V2 accounting contract (`output` is
visible output and reasoning is separate), while fixing the verified lifecycle
and configuration defects. Collection gaps now retain a successful paid run but
withhold complete aggregates; settlement is bounded and backed off; retained CLI
IDs participate in server-validated root selection; missing provenance remains
unknown while observed contamination still fails. Skills use the isolated V2
config directory, list-contained environment templates are forwarded/redacted,
partial URL templates are rejected, and runner stdin/tee cleanup is deterministic.
Additional regressions cover malformed API records, missing variants, stale root
candidates, and readiness errors whose process cleanup also fails.

The pinned-binary usage reconciliation now records the disputed token contract
directly. The fake provider reports `completion_tokens: 34` with 5 reasoning
tokens; OpenCode 2.0.3 persists 29 visible-output tokens plus 5 reasoning tokens;
Pier emits exactly 34 ATIF completion tokens. Thus adding the two persisted,
mutually exclusive categories reconstructs the provider total once. Empty
collection now also writes an error-level, metrics-incomplete stub trajectory
with retained collection diagnostics, and withheld usage remains null in
`AgentContext` instead of being coerced to zero.

A separate tiny Pier task passed end to end through the configured gateway on
both acceptance-only Low profiles on Pier `635593f`: GLM-5.3-Flash used 18,065
prompt, 8,192 cached, and 100 completion tokens at normalized cost $0.00177671;
GPT-5.6 Luna used 11,169 prompt, 5,508 cached, and 150 completion tokens at
normalized cost $0.00170511. Both
earned reward 1, completed collection without errors, and left no owned server
or container. This direct-gateway smoke validates the two runtime transports;
it did not add independent per-request upstream provenance beyond the retained
GLM recorder acceptance below.

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
