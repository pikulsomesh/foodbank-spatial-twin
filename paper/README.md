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
| `figures/contact_sheet.jpg` | Figure 2, the eight input frames, from PR 2 |
| `figures/racking_preview.jpg`, `site_preview.jpg` | Figure 3, Spark renders of the world-model zones, from PR 2 |
| `figures/photoreal_before_after.jpg` | Figure 4, world-model capture vs. locally trained 3DGS, from PR 6 |
| `figures/photoreal_four_zones.jpg` | Figure 5, the four locally trained zones in the browser, from PR 6 |
| `figures/arch_aws.pdf` (`.png`) | Figure 6, the AWS architecture (draw.io) |
| `figures/twin_alignment.jpg` | Figure 7, alignment check of the composed stage, from PR 8 |
| `figures/facility_cycles.jpg`, `before_after_phone.jpg` | Figure 8, the modeled facility and the phone before/after, from PR 9 |
| `figures/facility_desktop.jpg` | Figure 9, the browser viewer on the modeled facility, from PR 9 |
| `figures/splat_vs_blender.jpg` | Figure 10, CPU splat renderer vs. Blender Cycles, from PR 5 |
| `figures/arch_azure.pdf`, `arch_gcp.pdf` (`.png`) | Figures 12 and 13 (appendix), the Azure and Google Cloud designs |
| `diagrams/arch_*.drawio`, `diagrams/make_diagrams.py`, `diagrams/gcp_icons.json` | Editable draw.io sources and the script that writes them |

The diagrams are generated, then exported with draw.io desktop (26.2.15, run headless with `xvfb-run`); the commands are in the docstring of `diagrams/make_diagrams.py`. Edit the `.drawio` files in draw.io directly for one-off changes, or the script to change all three clouds together.

## Build

```bash
sudo apt-get install texlive-latex-extra texlive-fonts-recommended texlive-bibtex-extra   # once; bibtex-extra has biblatex-nejm
cd paper
pdflatex main && bibtex main && pdflatex main && pdflatex main
```

The template uses biblatex with `backend=bibtex`, so it runs BibTeX rather than Biber. BibTeX mode always logs "There were undefined references" and "Please (re)run BibTeX"; the template's own sample does the same, and the citations and reference list render correctly. If a stale `main.bbl` from another bibliography package is present, delete it first.

## Placeholders

Every value not yet measured is a red `\TBD{...}` in the PDF (10 at present). None are estimates. To list them: `grep -n TBD main.tex`. Before submission, change `\drafttrue` to `\draftfalse` near the top of `main.tex`; the build then stops at the first unresolved placeholder.

| Where | What it needs | Who or what unblocks it |
|---|---|---|
| Results 2.6, measurements table (2 rows) | Measured monthly AWS cost and telemetry latency | AWS deployment of the SAM stack |
| Results 2.9, measurements table | Load time and frame rate on a laptop and a phone with a hardware GPU | Somesh, on his own devices (5 minutes) |
| Results 2.10, Figure 11, measurements table, Methods, Movie S3 | Omniverse version, splat rendering check, screenshot, frame rate, recording | A rented RTX GPU (about $3 for an hour; plan in `docs/RENDERING.md`) |
| Movie S1 | Final recording on the modeled facility | Can be recorded headless with `scripts/record_demo.mjs` |

Resolved without new data on 2026-10-24: the abstract's cloud cost is now the list-price estimate (marked as an estimate); generation time is "n/a" because both runs failed; fusion time and hold-out fidelity are reported as not measured, in the table caption and the Discussion, and Statistical Analysis now describes what was actually measured; the demo URL is filled.

The source video appears to be KXXV's tour of the Central Texas Food Bank's new Waco hub (KXXV is a Waco station and the lower-third reads "Feed the Need"); the paper says "appears to be". Confirm before submission.

## Before submitting

1. Verify every entry in `refs.bib`, especially `ctfb_impact_fy24` and `fa_meal`, which point at home pages rather than the specific report pages, against its source (authors, venue, pages, DOI), and add access dates to the web entries that lack one.
2. Somesh decided (2026-10-24) that no permission is sought: the Acknowledgments credit KXXV's public video tour as inspiration and say neither organization was involved. The video is publicly available, which is not the same as public domain; renders derived from its frames remain the author's call.
3. The AI-use declaration in the Acknowledgments is confirmed by the author. AI tools cannot be listed as authors on arXiv or in most journals.
4. For arXiv, suggested categories are primary `cs.CV`, cross-listed to `cs.GR` and `cs.CY`. A first-time submitter in a category may need an endorsement. Upload `main.tex`, `refs.bib`, the generated `main.bbl` and `figures/`. The line numbers are a journal-review convention; remove `\linenumbers` for the arXiv version if preferred.
