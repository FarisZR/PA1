# GLM-5.3-Flash rerun outcome audit

Evidence for the three `oxvg-structural-selector-preservation` trials of the
GLM-5.3-Flash rerun whose recorded outcome needed an explanation beyond
`result.json`. The results chapter summarizes them in "GLM-5.3-Flash > Task
success". No finding changes a published result. The raw logs remain under
`benchmark/runs/glm-5.3-flash-rerun/` and are not tracked by Git.

| File | Content |
| --- | --- |
| `pi-oxvg-last-steps.json` | The last two agent steps of the Pi trajectory for `oxvg`, unchanged (source trajectory SHA-256 `4353c32c292d2f75e866ae5c4cd16e723632ca052ee35b8e3eed70a379e50aec`) |
| `oxvg-codex-compile-errors.txt` | Verifier status lines and compiler error lines of the Codex trial, deduplicated, up to the failure list (source `verifier/test-stdout.txt` SHA-256 `54b598f1c0aadb26576753ffd1ac67c808ebbefabf7e791593abbd753efafdc8`) |
| `oxvg-claude-code-compile-errors.txt` | The same for the Claude Code trial (source SHA-256 `aea42c836570dbe3c8d001aa0a9275b701bf110eedf622ae4997c75b211d455c`) |

## Pi: agent timeout on a shell command that never returned

Trial `data/benchmark-results/glm-5.3-flash/oxvg-structural-selector-preserv__KSNdDdc/`.
The last agent step, at 18:05:19 UTC, is one `bash` call that edits a source
file and then runs `cargo build` and the `oxvg` binary through pipes. It has no
tool result. Pi's `bash` tool has no default time limit, and the model set
none, so the trial waited until the three-hour agent timeout at 20:27 UTC
(`exception.txt`: `Agent execution timed out after 10800.0 seconds`). The trial
log contains no gateway or rate-limit error. Which part of the command
kept running cannot be determined, because no process trace was retained.
Nothing was committed: `artifacts/model.patch` is empty, and the verifier
graded the unchanged repository (`no model.patch submitted — grading pristine
base state`). It therefore passed all 62 previously passing tests and none of
the six fail-to-pass tests. This is the same failure mode as the Kimi K3 Pi
`scriggo` trial (`../kimi-k3-pi-audit/`).

## Claude Code and Codex: patches that do not compile

Trials `oxvg-structural-selector-preserv__n9S6Ky5` (Claude Code) and
`oxvg-structural-selector-preserv__Vo2eUNL` (Codex). Both patches applied, but
`cargo test` failed to compile the `oxvg_optimiser` crate in both verifier modes
(`rc=101`). No test ran, so the verifier counted all 68 tests, including the 62
previously passing ones, as failed. The Codex patch constructs a
`PartialSelector` with a `features` field that its own struct definition lacks
(six compiler errors). The Claude Code patch declares a struct with two lifetime
parameters and implements it with three (two compiler errors). Both are
outcomes of the evaluated systems, not verifier or infrastructure faults.
