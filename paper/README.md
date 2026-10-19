# Paper: From Video to Digital Twin

**Title:** From Video to Digital Twin: Accelerating Spatial Twins with World Models and NVIDIA Omniverse

`main.tex` is an arXiv preprint draft structured around the project's three steps: video to twin with a world model (Section 4), existing data on AWS (Section 5), NVIDIA Omniverse as the visualisation layer (Section 6), with OpenUSD as the open contract between them. The case study (Section 7) is a food bank warehouse inspired by, not replicating, the Central Texas Food Bank.

## Build

```bash
sudo apt-get install texlive-latex-extra texlive-fonts-recommended latexmk   # once
cd paper && latexmk -pdf main.tex
```

Standard packages only (TikZ for Figure 1, natbib with `plainnat`), so it builds on arXiv as is.

## Placeholders

Every value not yet measured is a red `\TBD{...}` in the PDF. None are estimates. To find them: `grep -n TBD main.tex`. To check that none remain before submission, change `\drafttrue` to `\draftfalse` near the top of `main.tex`; the build then stops at the first unresolved placeholder.

Main groups of placeholders:

| Where | What it needs | Comes from |
|---|---|---|
| Abstract, Table 4 | Time, credits, file sizes, fidelity, latency, AWS cost, frame rates | The racking-aisle run (video-to-twin thread) and the deployed backend |
| Section 4.2 | Which World Labs route produced the twin (Marble world vs. posed RGB-D plus local fusion) | Video-to-twin thread |
| Section 5 | First real data connector, or a statement that evaluation is simulated | Backend work |
| Section 6, Figure 3 | Omniverse version, splat rendering check, screenshot, Blender spike result | Renderer work |
| Section 7, Figure 4 | Renders of observed vs. extrapolated regions | Video-to-twin thread |
| Front matter | Surname, affiliation, email, date, acknowledgements | Somesh |
| Section 2 | Cited paragraph on food bank operations; Kit license confirmation | Literature check |

`figures/contact_sheet.jpg` is the eight input frames, copied from `data/ctfb-warehouse/` in PR 2.

## Before submitting to arXiv

1. Verify every entry in `refs.bib` against its source (authors, venue, pages). Web entries were taken from the URLs in `docs/ARCHITECTURE.md` and the main README.
2. Get permission from the Central Texas Food Bank and the video's rights holder (25 News KXXV) before publishing renders of the reconstruction.
3. Follow arXiv's policy on AI-assisted writing in the acknowledgements; AI tools cannot be listed as authors.
4. Suggested categories: primary `cs.CV`, cross-list `cs.GR` and `cs.CY`. A first-time submitter in a category may need an endorsement.
5. Upload `main.tex`, `refs.bib`, the generated `main.bbl` and `figures/`.
