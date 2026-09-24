# Review PDF for pull requests

The `Paper PDF preview` workflow publishes two PDFs for every pull request: the
normal paper and a review copy that shows the pull request's changes as a diff.

## What the review PDF shows

- Text added by the PR is highlighted green; removed text is struck through in red.
- Additions and removals made by the latest commit are blue and purple instead.
- A bar in the left margin spans every changed block, with a label (`edited`,
  `added`, `removed`) at its start. An edited block's bar is blue when the
  latest commit edited it.
- New figures, and figures whose rendered image changed, are framed: red for the
  PR, blue when the latest commit changed them.
- Table cells and captions, including `tbl-cap`/`fig-cap` cell options, are
  diffed like text.
- The page after the table of contents lists every change with its section and
  a link to its page.

"Latest commit" is the PR head compared with its first parent. When the PR has a
single commit, every change counts as the latest commit's.

## How it works

1. `build_review_pdf.py` renders the PR head, the merge base, and the head's
   parent. Each render uses `dump_ast.lua` at Quarto's `pre-ast` stage to write
   the executed Pandoc AST to `.review/<version>.json` and keep its generated
   images under `.review/media/`.
2. `diff_ast.py` compares the three ASTs block by block and word by word and
   writes the head document with Typst markup around each change to
   `.review/merged.json`. Images are compared by their rasterised pixels, so a
   figure is marked only when its rendered appearance changed.
3. The review render loads the merged AST through `load_merged_ast.lua`, and
   `review-preamble.typ` defines the colours and markers.

Because the diff runs on the rendered document rather than the `.qmd` source,
changes in data or analysis scripts appear wherever they change a generated
table, number, or figure.

## Running locally

With the paper and review dependencies installed:

```bash
python scripts/review/build_review_pdf.py --base origin/main
```

This writes `_output/pa1.pdf`, `_output/pa1-review.pdf`, and
`.review/manifest.json`. The current checkout must be the PR head.
