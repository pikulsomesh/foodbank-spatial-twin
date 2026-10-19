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
| `figures/twin_alignment.jpg` | Figure 5, top view of the composed `twin.usda` with the operations footprints, from PR 8 |
| `figures/demo_overview.jpg`, `demo_aisle.jpg` | Figure 6, demo-site screenshots from PR 4 |
| `figures/photoreal_before_after.jpg` | Figure 4, world-model capture vs. locally trained 3DGS, from PR 6 |
| `figures/splat_vs_blender.jpg` | Figure 7, CPU splat renderer vs. Blender Cycles from the same cameras, from PR 5 |

## Build

```bash
sudo apt-get install texlive-latex-extra texlive-fonts-recommended texlive-bibtex-extra   # once; bibtex-extra has biblatex-nejm
cd paper
pdflatex main && bibtex main && pdflatex main && pdflatex main
```

The template uses biblatex with `backend=bibtex`, so it runs BibTeX rather than Biber. BibTeX mode always logs "There were undefined references" and "Please (re)run BibTeX"; the template's own sample does the same, and the citations and reference list render correctly. If a stale `main.bbl` from another bibliography package is present, delete it first.

## Placeholders

Every value not yet measured is a red `\TBD{...}` in the PDF (20 at present). None are estimates. Items that wait on AWS say "pending AWS". To list them: `grep -n TBD main.tex`. Before submission, change `\drafttrue` to `\draftfalse` near the top of `main.tex`; the build then stops at the first unresolved placeholder.

| Where | What it needs | Comes from |
|---|---|---|
| Abstract, Results 2.6, Table 3 | Monthly AWS cost and telemetry latency (marked "pending AWS") | Deployed backend |
| Results 2.4 | Training steps and time for the racking 3DGS zone; sorting room and market results | Photoreal thread (PR 6) |
| Table 3 | Generation time, fusion time, hold-out fidelity, frame rates | A successful generate run; a timed fuse; a hardware-GPU browser test |
| Results 2.7 | Public URL, load time and frame rate | Live demo site once deployed |
| Results 2.9, Figure 8, Methods: Visualization | Omniverse version, splat rendering check, screenshot, frame rate | Rented RTX GPU (plan in PR 5's `docs/RENDERING.md`) |
| Methods: Statistical Analysis | Held-out frame count, dispersion statistic, pose method | Evaluation run |
| Human Studies, Acknowledgments | Permission from the food bank and KXXV | Somesh |
| Supplementary | Final demo recording (a draft exists in `demo-site/demo-draft.mp4`); Omniverse movie | Demo and renderer work |

Filled from measured facts on 2026-10-19: frame counts, splat counts, SPZ and USDZ sizes and credit costs (PR 2), frame extraction time (88 s, re-run locally), SPZ to USDZ conversion time (5.2 s and 3.9 s, re-run locally), Figures 3 to 6 (PR 2 previews, PR 6 before/after, PR 4 screenshots, PR 5 renders), the locally trained racking zone (PR 6), the Kit license wording (kit-app-template README) and two cited sentences on food bank operations, and the operations layer (Results 2.7, Table 4, Methods: Operations model) from PR 8, with weekday ranges computed by running its simulator over 1 September to 26 October 2026, and the single composed stage (`twin.usda`, Figure 5) from PR 8 commit d0a3d76. The case study is framed as a representative Texas food bank with a provenance table, and the Discussion carries a list-price cost estimate for running on real floor data (`docs/COST_ESTIMATE.md`); the measured AWS bill of the demo backend is still pending.

The source video appears to be KXXV's tour of the Central Texas Food Bank's new Waco hub (KXXV is a Waco station and the lower-third reads "Feed the Need"); the paper says "appears to be". Confirm before submission.

## Before submitting

1. Verify every entry in `refs.bib`, especially `ctfb_impact_fy24` and `fa_meal`, which point at home pages rather than the specific report pages, against its source (authors, venue, pages, DOI), and add access dates to the web entries that lack one.
2. Get permission from the Central Texas Food Bank and the video's rights holder (25 News KXXV) before publishing renders of the reconstruction.
3. The AI-use declaration in the Acknowledgments is confirmed by the author. AI tools cannot be listed as authors on arXiv or in most journals.
4. For arXiv, suggested categories are primary `cs.CV`, cross-listed to `cs.GR` and `cs.CY`. A first-time submitter in a category may need an endorsement. Upload `main.tex`, `refs.bib`, the generated `main.bbl` and `figures/`. The line numbers are a journal-review convention; remove `\linenumbers` for the arXiv version if preferred.
