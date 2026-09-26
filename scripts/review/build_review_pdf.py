#!/usr/bin/env python3
"""Render the paper and a review PDF that shows the PR's changes as a diff.

Steps:

1. Render the current checkout (the PR head) normally, which also writes
   _output/pa1.pdf, while dumping its executed Pandoc AST.
2. Render the PR base (merge base with the target branch) and the state before
   the latest commit in temporary worktrees, dumping their ASTs the same way.
3. Diff the three ASTs with scripts/review/diff_ast.py.
4. Render the merged AST to _output/pa1-review.pdf.

Run from the repository root:  python scripts/review/build_review_pdf.py --base origin/main
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / ".review"
DUMP_PROFILE = """filters:
  - at: pre-ast
    path: scripts/review/dump_ast.lua
"""
REVIEW_PROFILE = """project:
  render:
    - pa1-review.qmd
filters:
  - at: pre-ast
    path: scripts/review/load_merged_ast.lua
format:
  typst:
    include-in-header: scripts/review/review-preamble.typ
    # Quarto decides this from the source file, which here is only the stub.
    shift-heading-level-by: 0
"""
# Placeholder source; load_merged_ast.lua replaces its whole AST. Quarto only
# emits Typst code-highlighting definitions when the source contains code,
# hence the inline code span.
REVIEW_STUB = """---
title: Review copy
---

Placeholder for the merged review document with `code`.
"""


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()


def union_bibliography(commits: list[str]) -> Path:
    """Write head's references.bib plus entries only older commits still have.

    Deleted text stays visible in the review PDF, including citations whose
    bibliography entries this PR removed; without them Typst cannot compile.
    """

    head_text = (ROOT / "references.bib").read_text(encoding="utf-8")
    entry = re.compile(r"^@\w+\{([^,\s]+),.*?^\}\s*$", re.M | re.S)
    keys = {m.group(1) for m in entry.finditer(head_text)}
    extra = []
    for commit in commits:
        shown = subprocess.run(["git", "show", f"{commit}:references.bib"], cwd=ROOT,
                               text=True, capture_output=True)
        for m in entry.finditer(shown.stdout if shown.returncode == 0 else ""):
            if m.group(1) not in keys:
                keys.add(m.group(1))
                extra.append(m.group(0))
    path = REVIEW / "references-union.bib"
    path.write_text(head_text.rstrip() + "\n\n" + "\n\n".join(extra) + "\n", encoding="utf-8")
    return path


def point_bibliography(node: object, bib: str) -> object:
    """Replace the references.bib path in raw Typst bibliography calls."""

    if isinstance(node, str):
        return node.replace('#bibliography("references.bib"', f'#bibliography("{bib}"')
    if isinstance(node, list):
        return [point_bibliography(item, bib) for item in node]
    if isinstance(node, dict):
        return {key: point_bibliography(value, bib) for key, value in node.items()}
    return node


def render_with_dump(checkout: Path, tag: str) -> None:
    """Render pa1.qmd in checkout and write .review/<tag>.json plus its images."""

    media = REVIEW / "media" / tag
    media.mkdir(parents=True, exist_ok=True)
    if checkout != ROOT:
        # Older commits may predate the review filter; always use the head's.
        (checkout / "scripts" / "review").mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts/review/dump_ast.lua", checkout / "scripts/review/dump_ast.lua")
    (checkout / "_quarto-reviewdump.yml").write_text(DUMP_PROFILE, encoding="utf-8")

    env = dict(
        os.environ,
        REVIEW_AST_OUT=str(REVIEW / f"{tag}.json"),
        REVIEW_MEDIA_DIR=str(media),
        REVIEW_MEDIA_REF=f".review/media/{tag}",
    )
    log = REVIEW / f"render-{tag}.log"
    print(f"Rendering {tag} ...", flush=True)
    with log.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            ["quarto", "render", "pa1.qmd", "--profile", "reviewdump"],
            cwd=checkout,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    if result.returncode != 0 or not (REVIEW / f"{tag}.json").exists():
        sys.stdout.write(log.read_text(encoding="utf-8"))
        raise SystemExit(f"Rendering {tag} failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="target branch commit or ref")
    parser.add_argument("--head", default="HEAD", help="PR head; must be checked out")
    args = parser.parse_args()

    head = git("rev-parse", args.head)
    if head != git("rev-parse", "HEAD"):
        raise SystemExit("--head must be the checked-out commit")
    base = git("merge-base", args.base, head)

    # The latest commit's own changes are measured against its first parent,
    # as long as that parent is still part of this PR.
    prev: str | None = None
    parent = git("rev-parse", f"{head}^1")
    if parent != base and subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, parent], cwd=ROOT
    ).returncode == 0:
        prev = parent

    if REVIEW.exists():
        shutil.rmtree(REVIEW)
    REVIEW.mkdir()

    worktrees = {"base": base, **({"prev": prev} if prev else {})}
    for tag, commit in worktrees.items():
        path = REVIEW / "worktrees" / tag
        subprocess.run(["git", "worktree", "remove", "--force", str(path)], cwd=ROOT, capture_output=True)
        git("worktree", "add", "--detach", str(path), commit)

    prev_failed: str | None = None
    try:
        jobs = [(ROOT, "head")] + [(REVIEW / "worktrees" / tag, tag) for tag in worktrees]
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {tag: pool.submit(render_with_dump, path, tag) for path, tag in jobs}
            for tag, future in futures.items():
                try:
                    future.result()
                except SystemExit:
                    # An intermediate commit that does not render (e.g. a citation removed one
                    # commit before its last use) only loses the latest-commit layer.
                    if tag != "prev":
                        raise
                    print(f"Previous commit {prev[:7]} did not render; building the review without it.", flush=True)
                    prev_failed, prev = prev, None
    finally:
        for tag in worktrees:
            git("worktree", "remove", "--force", str(REVIEW / "worktrees" / tag))
        (ROOT / "_quarto-reviewdump.yml").unlink(missing_ok=True)

    diff_args = [
        sys.executable,
        str(ROOT / "scripts/review/diff_ast.py"),
        "--base", str(REVIEW / "base.json"),
        "--head", str(REVIEW / "head.json"),
        "--root", str(ROOT),
        "--output", str(REVIEW / "merged.json"),
        "--manifest", str(REVIEW / "manifest.json"),
        "--base-label", base[:7],
        "--head-label", head[:7],
    ]
    if prev:
        diff_args += ["--prev", str(REVIEW / "prev.json"), "--prev-label", prev[:7]]
    subprocess.run(diff_args, cwd=ROOT, check=True)
    if prev_failed:
        manifest_path = REVIEW / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["prev_render_failed"] = prev_failed[:7]
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    merged_path = REVIEW / "merged.json"
    bib = union_bibliography([base] + ([prev] if prev else []))
    merged = point_bibliography(json.loads(merged_path.read_text(encoding="utf-8")),
                                bib.relative_to(ROOT).as_posix())
    merged_path.write_text(json.dumps(merged), encoding="utf-8")

    (ROOT / "pa1-review.qmd").write_text(REVIEW_STUB, encoding="utf-8")
    (ROOT / "_quarto-review.yml").write_text(REVIEW_PROFILE, encoding="utf-8")
    print("Rendering review PDF ...", flush=True)
    subprocess.run(
        ["quarto", "render", "pa1-review.qmd", "--profile", "review"],
        cwd=ROOT,
        check=True,
        env=dict(os.environ, REVIEW_MERGED_AST=str(REVIEW / "merged.json")),
    )
    if not (ROOT / "_output/pa1-review.pdf").stat().st_size:
        raise SystemExit("Review PDF is empty")


if __name__ == "__main__":
    main()
