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

## Rendering and review

- After modifying the paper, run `quarto render pa1.qmd`.
- Resolve render errors and warnings that indicate an actual configuration or content problem before finishing.
- For the normal writing workflow, use `quarto preview pa1.qmd`.
- Keep changes small and reviewable.
- Do not rewrite unrelated sections without being asked.

## Deferred formatting

- DHBW-specific title-page styling, Roman page numbering, declarations, and other complex front-matter formatting are intentionally deferred.
- Keep clearly marked TODOs for deferred requirements rather than introducing custom Typst hacks prematurely.
