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
- In `data/benchmark-results/`, each trial directory (`<job>/*/result.json`) holds the one canonical attempt for that trial; use only these for comparative tables, figures, success rates, and cost. `.retry-attempts/` holds non-canonical attempts (experimental overhead), and the job-level `<job>/result.json` is Pier's uncorrected run summary; do not use either for comparative results. `.excluded/` and `.superseded/` hold observations removed from the comparative data (see `data/benchmark-results/README.md`); use them only as diagnostic evidence.
- DeepSeek V4.1 Flash's normal trial directory holds one canonical Codex, Pi, and Claude Code trial per task. All ten Pi trials come from the complete `deepseek-pi-rerun`; the original Pi trials (six without earlier reasoning, #111) are under `.superseded/issue-111/deepseek-v4p1-flash/pi/` and the budget-stopped first rerun under `.excluded/deepseek-v4p1-flash/pi-rerun-budget-crash/`. Read them only for the reasoning-history comparison in the results chapter.
- GLM-5.3-Flash's normal trial directories (`glm-5.3-flash/`, `opencode-v2-glm-5.3-flash/`) contain only the second Fireworks run (Pier jobs `glm-5.3-flash-rerun` and `opencode-v2-glm-5.3-flash-rerun`). The first run, which lost the model's earlier reasoning, and the incomplete direct Z.AI snapshot are under `data/benchmark-results/.excluded/glm-5.3-flash/` for the route comparison only; `scripts/build_corrected_results.py` enforces this layout.
- GPT-5.6 Luna's normal OpenCode V2 directory (`opencode-v2-luna/`) contains only the rerun without an input limit (Pier job `opencode-v2-luna-272k`). The first run, which compacted at about 124,000 tokens, is under `data/benchmark-results/.excluded/luna/opencode-v2-input-144k/` for the early-compaction comparison only; `scripts/build_corrected_results.py` enforces this layout.
- Kimi K3's normal trial directories contain only the eligible Pi and Codex runs. Its ten ineligible Claude Code trials and the unfiltered Pier job summary are under `data/benchmark-results/.excluded/kimi-k3/` for audit only. Never include `.excluded/` in comparative analysis; `scripts/build_corrected_results.py` enforces this layout on regeneration.

## Rendering and review

- After modifying the paper, run `quarto render pa1.qmd`.
- Resolve render errors and warnings that indicate an actual configuration or content problem before finishing.
- For the normal writing workflow, use `quarto preview pa1.qmd`.
- Keep changes small and reviewable.
- Do not rewrite unrelated sections without being asked.

## DHBW formatting

- `pa1.qmd` includes the DHBW title-page fields, bilingual abstract headings, Roman-numbered preliminary pages, and main-text numbering restarted at Arabic page 1.
- Replace the visible title-page TODOs (student ID, course, supervisors, and submission date) before submission. Add the work period and dual partner only if applicable.
- Final declaration wording and title-page styling beyond the required fields remain subject to confirmation; keep TODOs for unknown details rather than inventing them.
