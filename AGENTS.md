# Instructions for coding agents

## Project structure

- The paper is written entirely in Quarto Markdown (`.qmd`).
- `pa1.qmd` is the root document and the only document rendered directly by the project.
- Chapter source files are under `chapters/` and are included by `pa1.qmd`.
- Figures belong under `figures/`.
- Raw or generated benchmark data belongs under `data/`.
- Analysis and data-generation scripts belong under `scripts/`.
- Keep source files plain-text, small, and easy for coding agents to edit.

## Writing and citations

- Write the paper in English.
- Use IEEE citations and preserve Quarto citation syntax.
- Never fabricate sources or citations.
- Every cited source must exist in `references.bib`.
- Citations are expected to be inserted from Zotero through the Quarto/VS Code Visual Editor workflow and stored in `references.bib`.
- Preserve Quarto labels and cross-references, including `#fig-...`, `#tbl-...`, `@fig-...`, and `@tbl-...`.
- The setup demonstration figure and table are not research results and must remain clearly marked as demo content until replaced or removed.

## Figures, data, and scripts

- Prefer SVG or PDF for generated charts where appropriate.
- Do not manually alter generated benchmark figures if a script is responsible for them.
- Do not invent benchmark results, company facts, observations, or definitions.
- Keep raw data separate from derived outputs and document the provenance of results.
- In `data/benchmark-results/`, always read `result.json` and `agent/trajectory.json`; they contain the corrected values. `*.original.json` files are unmodified Pier output kept only as evidence and must not be used for analysis or figures (see `data/benchmark-results/README.md`).
- For every comparative benchmark table, figure, aggregate, success rate, cost calculation, token calculation, or cache calculation, select observations through `scripts/benchmark_results.py`. Do not glob final trial directories or `.retry-attempts/` directly.
- `data/benchmark-results/primary-attempt-overrides.json` is a sparse selection manifest: Pier's final trial is the default, and only exceptional observations select an earlier recorded attempt. Jobs and observations without an override must remain unchanged.
- The override manifest selects which recorded attempt is analyzed; it does not correct metric values. After attempt selection, use the normal corrected `result.json` and `agent/trajectory.json` files as described above.
- Do not move, rename, or copy retry attempts out of `.retry-attempts/` to influence analysis. Their location is preserved execution evidence.
- Run `python3 scripts/benchmark_results.py` after changing benchmark evidence or the override manifest. Validation must pass before those data are used in paper figures or tables.

## Rendering and review

- After modifying the paper, run `quarto render pa1.qmd`.
- Resolve render errors and warnings that indicate an actual configuration or content problem before finishing.
- For the normal writing workflow, use `quarto preview pa1.qmd`.
- Keep changes small and reviewable.
- Do not rewrite unrelated sections without being asked.

## Deferred formatting

- DHBW-specific title-page styling, Roman page numbering, declarations, and other complex front-matter formatting are intentionally deferred.
- Keep clearly marked TODOs for deferred requirements rather than introducing custom Typst hacks prematurely.
