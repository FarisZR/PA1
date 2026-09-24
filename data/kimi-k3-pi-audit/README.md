# Kimi K3 Pi failure audit

Evidence for two Kimi K3 Pi trials whose recorded outcome needed an
explanation beyond `result.json`. The results chapter summarizes both in
"Kimi K3 > Task success". Neither finding changes a published result.

| File | Content |
| --- | --- |
| `koota-model.patch` | The model's submitted diff for `koota`, unchanged from the trial's `artifacts/model.patch` (SHA-256 `9e07de19447ed8f812688ee75ffac2f4045a52b5915b6e29f497c3564ca055f3`) |
| `scriggo-session-tail.jsonl` | The last three records of the Pi session log for `scriggo`, unchanged (source log SHA-256 `901e3e5c7ddaa046928b116643dfd99f810b4148a62e808c11734ba064880ac2`) |

## `koota`: verifier timeout from an endless loop

The trial `data/benchmark-results/kimi-k3/koota-composite-trait-aspects__JWZnkA9/`
has no reward: its verifier timed out after 1,800 s. The timeout is an endless
loop caused by the submitted code, not an infrastructure fault.

Reproduce with `scripts/reproduce_kimi_koota_hang.sh`, which needs the task's
built environment image and the pinned DeepSWE task directory. It applies the
patch and the task's hidden `test.patch` in a container without network access
and runs the new test file under a time limit.

### Findings (replayed 2026-09-23)

1. **The verifier hung on the new tests.** The retained verifier log shows the
   existing core and React suites finishing, then no output from
   `vitest run tests/aspect.test.ts` until the 1,800 s limit. The offline replay
   behaves the same and is stopped by the 120 s limit (exit status 124) without
   printing a single test result.
2. **The hang is a busy loop.** The vitest worker ran at about 98% CPU. The
   loop is synchronous, so vitest's per-test timeout cannot interrupt it.
3. **Location.** Pausing the worker through the Node inspector (`SIGUSR1`, then
   `Debugger.pause`) returned the same frame on every sample:
   `createQueryInstance` in `packages/core/src/query/query.ts:400`, called by
   `world.query(Added(Movable))` at `tests/aspect.test.ts:348` ("should not
   match entity that already had all constituents"). In the paused frame,
   `mask = 1073741824` (2^30), `type = 'add'`, and 41 traits were registered in
   the test world.
4. **Mechanism.** The loop is `for (let bit = 1; bit <= mask; bit <<= 1)`, which
   is unchanged upstream koota code (line 372 of `query.ts` at the task's base
   commit). JavaScript shifts are 32-bit, so after `bit = 2^30` the shift gives
   `-2^31` and then `0`. Both stay `<= mask`, and `0 << 1` stays `0`. The loop
   ends only while the tracked trait's bit is below 2^30. The model's solution
   registers a hidden marker trait for every `createAspect` call
   (`packages/core/src/aspect/aspect.ts` in the patch). The hidden tests create
   new aspects in each test on one shared world, so the marker traits
   accumulate until one of them receives bit 2^30.
5. **Controls.** Each `describe` block passes when run alone (all 51 tests).
   The hang needs the earlier blocks to register enough traits first. The
   task's reference solution passes all 51 tests in the same image and
   replay (`--reference`). The Codex trial on the same task passed the
   verifier.

The trial is therefore a failure of the evaluated system. Verifier timeouts
are final under the retry rule.

## `scriggo`: agent timeout from a shell command without a time limit

The trial `data/benchmark-results/kimi-k3/scriggo-method-declarations__PkULDqE/`
reached the three-hour agent timeout (`AgentTimeoutError`) and passed 0 of 48
fail-to-pass tests. The timeout was not caused by the gateway or the model
stream.

1. **Active work ended after 75 minutes.** Agent execution ran from
   22:28:53 to 01:28:53 UTC. The session log records 297 model responses up to
   23:43:47. Response latency during that time was normal: the mean gap between
   model responses (14.3 s) lies within the range of the other Kimi Pi trials
   (12.3 to 19.2 s). The log contains no HTTP error, terminated stream, retry,
   or context-size error.
2. **The last command never returned.** At 23:43:47 the model started its own
   comparison script over Go's entire `fixedbugs` test directory
   (`./harness '/app/test/compare/testdata/github.com-golang-go/fixedbugs/*.go' ...`,
   last record of `scriggo-session-tail.jsonl`). The previous run of the same
   script on a small subset (`bug28*.go`) returned within a second. No tool
   result follows the final call. Pier's cancellation traceback in the
   retained `exception.txt` waits in `process.communicate()`. The child process
   that blocked is not recoverable from the retained logs.
3. **No time limit applied.** Pi 0.84.4's `bash` tool documents its `timeout`
   parameter as "Timeout in seconds (optional, no default timeout)"
   (`dist/core/tools/bash.js` in the npm package). None of the model's 263
   `bash` calls in this trial set it.
4. **Why the verifier found nothing.** The task asks the agent to commit its
   work when done. The model created a branch but had not committed when the
   trial ended. Pier's collect hook takes `git diff <base> HEAD`, so
   `artifacts/model.patch` is empty and the verifier graded the unchanged
   repository. The 0/48 score therefore reflects the lack of a submitted
   patch, not the quality of the uncommitted changes. Shortly before the last
   command, the model had reported all existing tests passing and a first
   method-declaration test from Go's suite matching (`bug281.go`, second
   record of the tail).

The published trajectory shows the gap but not the final call: Pier's Pi
converter keeps only calls with a result, so its last step is at 23:43:31.
This is an outcome of the evaluated model--harness system under the benchmark
rules; no correction applies.

`boa` and `effect`, the other Pi trials with no fail-to-pass test passed,
ended normally: the agent finished on its own, and the verifier ran.
