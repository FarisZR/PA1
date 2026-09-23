#!/usr/bin/env python3
"""Compare base/head PDFs and mark visually changed pages in a review PDF.

This is a safety net for changes that can alter rendered output without editing
QMD source directly, such as chart data, analysis scripts, bibliography, or
layout configuration. It is deliberately used only when source QMD did not
change, avoiding page-reflow noise from ordinary prose edits.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import fitz
import numpy as np


def render_gray(page: fitz.Page) -> np.ndarray:
    pix = page.get_pixmap(
        matrix=fitz.Matrix(1.0, 1.0),
        colorspace=fitz.csGRAY,
        alpha=False,
    )
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def page_diff_ratio(base_page: fitz.Page, head_page: fitz.Page) -> float:
    base = render_gray(base_page)
    head = render_gray(head_page)
    if base.shape != head.shape:
        return 1.0

    delta = np.abs(base.astype(np.int16) - head.astype(np.int16))
    changed = delta > 12
    return float(changed.mean())


def changed_pages(
    base_pdf: Path,
    head_pdf: Path,
    *,
    min_changed_ratio: float,
) -> tuple[list[int], dict[str, float]]:
    base = fitz.open(base_pdf)
    head = fitz.open(head_pdf)
    try:
        changed: set[int] = set()
        ratios: dict[str, float] = {}
        shared = min(base.page_count, head.page_count)

        for index in range(shared):
            ratio = page_diff_ratio(base[index], head[index])
            ratios[str(index + 1)] = ratio
            if ratio >= min_changed_ratio:
                changed.add(index + 1)

        if head.page_count > base.page_count:
            for index in range(base.page_count, head.page_count):
                changed.add(index + 1)
                ratios[str(index + 1)] = 1.0

        # If the head became shorter, flag its last surviving page because the
        # removed pages have no page in the review PDF on which to draw a marker.
        if base.page_count > head.page_count and head.page_count:
            changed.add(head.page_count)
            ratios[str(head.page_count)] = max(
                ratios.get(str(head.page_count), 0.0),
                1.0,
            )

        return sorted(changed), ratios
    finally:
        base.close()
        head.close()


def overlay_page_markers(review_pdf: Path, pages: list[int]) -> None:
    if not pages:
        return

    document = fitz.open(review_pdf)
    try:
        for page_number in pages:
            if page_number < 1 or page_number > document.page_count:
                continue
            page = document[page_number - 1]
            width = page.rect.width
            marker = fitz.Rect(width - 8, 34, width - 3, 112)
            page.draw_rect(
                marker,
                color=None,
                fill=(0.749, 0.529, 0.0),
                overlay=True,
            )

        temp_path = review_pdf.with_suffix(".marked.pdf")
        document.save(temp_path, garbage=4, deflate=True)
    finally:
        document.close()

    os.replace(temp_path, review_pdf)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--head", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--min-changed-ratio",
        type=float,
        default=0.00005,
        help="Fraction of changed pixels needed to flag a page (default: 0.00005)",
    )
    args = parser.parse_args()

    pages, ratios = changed_pages(
        args.base,
        args.head,
        min_changed_ratio=args.min_changed_ratio,
    )
    overlay_page_markers(args.review, pages)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest["visual_changed_pages"] = pages
    manifest["visual_page_diffs"] = ratios
    args.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if pages:
        print("Visually changed page(s): " + ", ".join(map(str, pages)))
    else:
        print("No visually changed pages exceeded the threshold.")


if __name__ == "__main__":
    main()
