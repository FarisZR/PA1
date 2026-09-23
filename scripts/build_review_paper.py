#!/usr/bin/env python3
"""Generate a review-only Quarto source with margin markers for PR changes.

The normal paper source is never modified. Changed Markdown blocks are copied to
temporary review sources and prefixed with non-flowing Typst margin markers.

Red left-margin bars indicate source edits. Amber left-margin bars indicate
rendered blocks whose data or analysis dependencies changed even though the QMD
block itself did not.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
QMD_GLOBS = ("pa1.qmd", "chapters/*.qmd")

SOURCE_MARKER = """```{=typst}
#place(dx: -18pt, dy: 2pt)[
  #rect(width: 3pt, height: 22pt, radius: 1.5pt, fill: rgb("#d1242f"))
]
```

"""

GENERATED_MARKER = """```{=typst}
#place(dx: -18pt, dy: 2pt)[
  #rect(width: 3pt, height: 22pt, radius: 1.5pt, fill: rgb("#bf8700"))
]
```

"""


@dataclass(frozen=True)
class Block:
    start: int
    end: int
    text: str
    kind: str


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def changed_paths(base: str, head: str) -> list[str]:
    output = run_git("diff", "--name-only", f"{base}...{head}")
    return [line.strip() for line in output.splitlines() if line.strip()]


def changed_qmd_ranges(
    base: str, head: str
) -> tuple[dict[str, list[tuple[int, int]]], dict[str, list[int]]]:
    """Return changed head line ranges and deletion anchors for paper QMD files."""

    output = run_git(
        "diff",
        "--unified=0",
        "--no-color",
        f"{base}...{head}",
        "--",
        *QMD_GLOBS,
    )

    ranges: dict[str, list[tuple[int, int]]] = {}
    deletions: dict[str, list[int]] = {}
    current_file: str | None = None

    file_re = re.compile(r"^\+\+\+ b/(.+)$")
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

    for line in output.splitlines():
        file_match = file_re.match(line)
        if file_match:
            current_file = file_match.group(1)
            continue

        hunk_match = hunk_re.match(line)
        if not hunk_match or current_file is None:
            continue

        new_start = int(hunk_match.group(1))
        new_count = int(hunk_match.group(2) or "1")
        if new_count == 0:
            deletions.setdefault(current_file, []).append(max(1, new_start))
        else:
            ranges.setdefault(current_file, []).append(
                (new_start, new_start + new_count - 1)
            )

    return ranges, deletions


def yaml_front_matter_end(lines: list[str]) -> int:
    if not lines or lines[0].strip() != "---":
        return 0
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return index + 1
    return 0


def markdown_blocks(text: str) -> tuple[list[Block], int]:
    """Split QMD into reviewable blocks while keeping fenced code intact."""

    lines = text.splitlines(keepends=True)
    front_end = yaml_front_matter_end(lines)
    blocks: list[Block] = []
    i = front_end

    fence_start_re = re.compile(r"^\s*([~`]{3,})")

    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue

        start = i
        stripped = lines[i].lstrip()
        fence_match = fence_start_re.match(lines[i])

        if fence_match:
            fence = fence_match.group(1)
            fence_char = fence[0]
            fence_len = len(fence)
            i += 1
            while i < len(lines):
                candidate = lines[i].strip()
                if (
                    candidate
                    and set(candidate) == {fence_char}
                    and len(candidate) >= fence_len
                ):
                    i += 1
                    break
                i += 1
            kind = "fence"
        elif stripped.startswith("#"):
            i += 1
            kind = "heading"
        elif stripped.startswith("<!--"):
            i += 1
            while i < len(lines) and "-->" not in lines[i - 1]:
                i += 1
            kind = "comment"
        else:
            i += 1
            while i < len(lines):
                if not lines[i].strip():
                    break
                if fence_start_re.match(lines[i]):
                    break
                if lines[i].lstrip().startswith("#"):
                    break
                i += 1
            kind = "block"

        end = max(start, i - 1)
        blocks.append(
            Block(
                start=start + 1,
                end=end + 1,
                text="".join(lines[start:i]),
                kind=kind,
            )
        )

    return blocks, front_end


def intersects(block: Block, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start <= block.end and end >= block.start for start, end in ranges)


def nearest_block_index(blocks: list[Block], line: int) -> int | None:
    if not blocks:
        return None

    for index, block in enumerate(blocks):
        if block.start <= line <= block.end:
            return index
        if block.start > line:
            return index
    return len(blocks) - 1


def visible_generated_cell(block: Block) -> bool:
    if block.kind != "fence":
        return False
    if not re.match(r"^\s*```\{(?:python|r|julia)\}", block.text):
        return False
    return (
        re.search(
            r"^#\|\s*include:\s*false\s*$",
            block.text,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        is None
    )


def chapter_dependency_changed(path: str, text: str, changed: set[str]) -> bool:
    """Conservatively detect changed inputs used by a chapter's generated output."""

    if "scripts/figures.py" in changed and "scripts.figures" in text:
        return True

    for changed_path in changed:
        if changed_path.startswith("scripts/") and changed_path.endswith(".py"):
            module = changed_path[:-3].replace("/", ".")
            if module in text:
                return True

        if changed_path.startswith("data/"):
            if changed_path in text:
                return True
            if (
                changed_path.startswith("data/benchmark-results/")
                and "data/benchmark-results" in text
            ):
                return True

        if changed_path == "benchmark/pricing.yaml" and changed_path in text:
            return True

    return False


def explicit_static_figure_reference(block: Block, changed: set[str]) -> bool:
    figure_paths = [item for item in changed if item.startswith("figures/")]
    return any(
        figure_path in block.text or Path(figure_path).name in block.text
        for figure_path in figure_paths
    )


def marker_for(kind: str) -> str:
    return SOURCE_MARKER if kind == "source" else GENERATED_MARKER


def annotate_text(
    *,
    path: str,
    text: str,
    line_ranges: list[tuple[int, int]],
    deletion_anchors: list[int],
    changed: set[str],
) -> tuple[str, list[dict[str, object]]]:
    lines = text.splitlines(keepends=True)
    blocks, front_end = markdown_blocks(text)

    source_indexes: set[int] = set()
    generated_indexes: set[int] = set()

    for index, block in enumerate(blocks):
        if block.kind != "comment" and intersects(block, line_ranges):
            source_indexes.add(index)

    for anchor in deletion_anchors:
        index = nearest_block_index(blocks, anchor)
        if index is not None and blocks[index].kind != "comment":
            source_indexes.add(index)

    # YAML metadata cannot contain raw Typst. Mark the first body block instead.
    front_matter_changed = any(
        start <= front_end and end >= 1 for start, end in line_ranges
    ) or any(anchor <= front_end for anchor in deletion_anchors)
    if front_end and front_matter_changed:
        index = nearest_block_index(blocks, front_end + 1)
        if index is not None:
            source_indexes.add(index)

    dependency_changed = chapter_dependency_changed(path, text, changed)
    for index, block in enumerate(blocks):
        if block.kind == "comment" or index in source_indexes:
            continue
        if dependency_changed and visible_generated_cell(block):
            generated_indexes.add(index)
        elif explicit_static_figure_reference(block, changed):
            generated_indexes.add(index)

    by_start = {block.start: index for index, block in enumerate(blocks)}
    result: list[str] = []
    markers: list[dict[str, object]] = []

    for line_no, line in enumerate(lines, start=1):
        index = by_start.get(line_no)
        if index is not None:
            kind: str | None = None
            if index in source_indexes:
                kind = "source"
            elif index in generated_indexes:
                kind = "generated"

            if kind is not None:
                result.append(marker_for(kind))
                block = blocks[index]
                markers.append(
                    {
                        "file": path,
                        "start_line": block.start,
                        "end_line": block.end,
                        "kind": kind,
                    }
                )
        result.append(line)

    return "".join(result), markers


def render_input_changed(paths: set[str]) -> bool:
    exact = {
        "_quarto.yml",
        "references.bib",
        "ieee.csl",
        "requirements.txt",
        "benchmark/pricing.yaml",
    }
    return any(
        path in exact
        or path.startswith("data/")
        or path.startswith("figures/")
        or (path.startswith("scripts/") and path.endswith(".py"))
        for path in paths
    )


def qmd_source_changed(paths: set[str]) -> bool:
    return any(
        path == "pa1.qmd"
        or (path.startswith("chapters/") and path.endswith(".qmd"))
        for path in paths
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="Base commit/ref for the pull request")
    parser.add_argument("--head", default="HEAD", help="Head commit/ref (default: HEAD)")
    parser.add_argument("--output-root", default="pa1-review.qmd")
    parser.add_argument("--work-dir", default=".review")
    args = parser.parse_args()

    changed_list = changed_paths(args.base, args.head)
    changed = set(changed_list)
    line_ranges, deletions = changed_qmd_ranges(args.base, args.head)

    work_dir = ROOT / args.work_dir
    if work_dir.exists():
        shutil.rmtree(work_dir)
    (work_dir / "chapters").mkdir(parents=True, exist_ok=True)

    source_paths = [ROOT / "pa1.qmd", *sorted((ROOT / "chapters").glob("*.qmd"))]
    all_markers: list[dict[str, object]] = []
    annotated: dict[str, str] = {}

    for source_path in source_paths:
        relative = source_path.relative_to(ROOT).as_posix()
        source_text = source_path.read_text(encoding="utf-8")
        review_text, markers = annotate_text(
            path=relative,
            text=source_text,
            line_ranges=line_ranges.get(relative, []),
            deletion_anchors=deletions.get(relative, []),
            changed=changed,
        )
        annotated[relative] = review_text
        all_markers.extend(markers)

    review_root = annotated["pa1.qmd"]
    review_root = re.sub(
        r"(\{\{<\s*include\s+)chapters/",
        r"\1.review/chapters/",
        review_root,
    )
    (ROOT / args.output_root).write_text(review_root, encoding="utf-8")

    for relative, review_text in annotated.items():
        if not relative.startswith("chapters/"):
            continue
        destination = work_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(review_text, encoding="utf-8")

    qmd_changed = qmd_source_changed(changed)
    render_changed = render_input_changed(changed)
    manifest = {
        "base": args.base,
        "head": args.head,
        "changed_paths": changed_list,
        "qmd_source_changed": qmd_changed,
        "render_input_changed": render_changed,
        # Visual page comparison is intentionally disabled when QMD changed:
        # text reflow would otherwise turn ordinary source edits into noisy page diffs.
        "visual_compare_eligible": render_changed and not qmd_changed,
        "source_marker_count": sum(item["kind"] == "source" for item in all_markers),
        "generated_marker_count": sum(
            item["kind"] == "generated" for item in all_markers
        ),
        "markers": all_markers,
        "deletion_anchors": deletions,
        "visual_changed_pages": [],
        "visual_page_diffs": {},
    }

    (work_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(
        "Review source generated: "
        f"{manifest['source_marker_count']} source marker(s), "
        f"{manifest['generated_marker_count']} generated-output marker(s)."
    )
    if manifest["visual_compare_eligible"]:
        print("Rendered-page comparison is eligible for this PR.")


if __name__ == "__main__":
    main()
