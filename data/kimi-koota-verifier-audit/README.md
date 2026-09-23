# Kimi K3 Pi `koota` verifier timeout

The Kimi K3 Pi trial on `koota-composite-trait-aspects`
(`data/benchmark-results/kimi-k3/koota-composite-trait-aspects__JWZnkA9/`)
has no reward: its verifier timed out after 1,800 s. This directory holds the
evidence that the timeout is an endless loop caused by the submitted code, not
an infrastructure fault. The results chapter summarizes it in "Kimi K3 > Task
success".

| File | Content |
| --- | --- |
| `model.patch` | The model's submitted diff, unchanged from the trial's `artifacts/model.patch` (SHA-256 `9e07de19447ed8f812688ee75ffac2f4045a52b5915b6e29f497c3564ca055f3`) |

Reproduce with `scripts/reproduce_kimi_koota_hang.sh`, which needs the task's
built environment image and the pinned DeepSWE task directory. It applies the
patch and the task's hidden `test.patch` in a container without network access
and runs the new test file under a time limit.

## Findings (replayed 2026-09-23)

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
are final under the retry rule, and no correction changes the published
result.
