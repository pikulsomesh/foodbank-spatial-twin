# Paper: From Video to Digital Twin

**Title:** From Video to Digital Twin: Accelerating Spatial Twins with World Models and NVIDIA Omniverse
**Author:** Somesh Mohapatra, Independent Researcher (pikulsomesh@gmail.com)

`main.tex` follows the Research Future LaTeX template supplied by the author: `authblk` title block, line numbers, 1.5 spacing, `biblatex` with the `nejm` style, and the section order Introduction, Results, Discussion, Materials and Methods, then the back matter (Acknowledgments with the AI-use declaration, Author Contributions, Funding, Competing interests, Data Availability, Supplementary Materials).

The content is organised around the project's three steps: video to twin with a world model, existing data on AWS, and NVIDIA Omniverse as the visualization layer, with OpenUSD as the open contract between them. The case study is a food bank warehouse inspired by, not replicating, the Central Texas Food Bank.

## Files

| File | What |
|---|---|
| `main.tex` | Manuscript |
| `refs.bib` | References (biblatex; web sources are `@online` with `url`/`urldate`) |
| `figures/overview.tikz` | Figure 1, the pipeline diagram (TikZ, included with `\input`) |
| `figures/contact_sheet.jpg` | Figure 2, the eight input frames, copied from `data/ctfb-warehouse/` in PR 2 |
| `figures/racking_preview.jpg`, `site_preview.jpg` | Figure 3, Spark renders of the fused zones, from PR 2 |
| `figures/demo_overview.jpg`, `demo_aisle.jpg` | Figure 4, demo-site screenshots from PR 4 |

## Build

```bash
sudo apt-get install texlive-latex-extra texlive-fonts-recommended texlive-bibtex-extra   # once; bibtex-extra has biblatex-nejm
cd paper
pdflatex main && bibtex main && pdflatex main && pdflatex main
```

The template uses biblatex with `backend=bibtex`, so it runs BibTeX rather than Biber. BibTeX mode always logs "There were undefined references" and "Please (re)run BibTeX"; the template's own sample does the same, and the citations and reference list render correctly. If a stale `main.bbl` from another bibliography package is present, delete it first.

## Placeholders

Every value not yet measured is a red `\TBD{...}` in the PDF (27 at present). None are estimates. To list them: `grep -n TBD main.tex`. Before submission, change `\drafttrue` to `\draftfalse` near the top of `main.tex`; the build then stops at the first unresolved placeholder.

| Where | What it needs | Comes from |
|---|---|---|
| Abstract, Results 2.5, Table 3 | Monthly AWS cost and telemetry latency | Deployed backend |
| Table 3 | Generation time, fusion and conversion time, hold-out fidelity, frame rates | A successful generate run; a timed fuse; a hardware-GPU browser test |
| Results 2.6 | Public URL, load time and frame rate | Live demo site once deployed |
| Results 2.7, Figure 5, Methods: Visualization | Omniverse version, splat rendering check, screenshot, Blender result | Renderer work |
| Methods: Serverless data backend | Anchor calibration against the stage | Backend and renderer work |
| Methods: Statistical Analysis | Held-out frame count, dispersion statistic, pose method | Evaluation run |
| Back matter | Acknowledgements with permission; Movies S1 and S2 | Somesh, demo video |
| Introduction | Cited sentences on food bank operations; Kit license confirmation | Literature check |

Filled from measured facts on 2026-10-19: frame counts, splat counts, SPZ and USDZ sizes and credit costs (from PR 2's `docs/CTFB_WAREHOUSE_RUN.md` and committed files), frame extraction time (re-run locally, 88 s), and Figures 3 and 4 (Spark previews from PR 2, demo-site screenshots from PR 4).

## Before submitting

1. Verify every entry in `refs.bib` against its source (authors, venue, pages, DOI), and add access dates to the web entries that lack one.
2. Get permission from the Central Texas Food Bank and the video's rights holder (25 News KXXV) before publishing renders of the reconstruction.
3. The AI-use declaration in the Acknowledgments is confirmed by the author. AI tools cannot be listed as authors on arXiv or in most journals.
4. For arXiv, suggested categories are primary `cs.CV`, cross-listed to `cs.GR` and `cs.CY`. A first-time submitter in a category may need an endorsement. Upload `main.tex`, `refs.bib`, the generated `main.bbl` and `figures/`. The line numbers are a journal-review convention; remove `\linenumbers` for the arXiv version if preferred.
