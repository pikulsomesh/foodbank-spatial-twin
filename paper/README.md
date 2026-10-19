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

## Build

```bash
sudo apt-get install texlive-latex-extra texlive-fonts-recommended texlive-bibtex-extra   # once; bibtex-extra has biblatex-nejm
cd paper
pdflatex main && bibtex main && pdflatex main && pdflatex main
```

The template uses biblatex with `backend=bibtex`, so it runs BibTeX rather than Biber. BibTeX mode always logs "There were undefined references" and "Please (re)run BibTeX"; the template's own sample does the same, and the citations and reference list render correctly. If a stale `main.bbl` from another bibliography package is present, delete it first.

## Placeholders

Every value not yet measured is a red `\TBD{...}` in the PDF (40 at present). None are estimates. To list them: `grep -n TBD main.tex`. Before submission, change `\drafttrue` to `\draftfalse` near the top of `main.tex`; the build then stops at the first unresolved placeholder.

| Where | What it needs | Comes from |
|---|---|---|
| Abstract, Results 2.3 to 2.6, Table 3 | Time, credits, file sizes, fidelity, latency, AWS cost, frame rates | The racking-aisle run (video-to-twin thread) and the deployed backend |
| Methods: World-model reconstruction | Which World Labs route produced the twin, its tasks and credits | Video-to-twin thread |
| Results 2.5, Methods: Serverless data backend | First real data connector, or a statement that evaluation is simulated; anchor calibration | Backend work |
| Results 2.6, Figure 4, Methods: Visualization | Omniverse version, splat rendering check, screenshot, Blender spike result | Renderer work |
| Figure 3 | Renders of observed vs. extrapolated regions | Video-to-twin thread |
| Methods: Statistical Analysis | Number of held-out frames, dispersion statistic, pose estimation method | Evaluation run |
| Back matter | Acknowledgements with permission; supplementary movie | Somesh |
| Introduction | Cited sentences on food bank operations; Kit license confirmation | Literature check |

## Before submitting

1. Verify every entry in `refs.bib` against its source (authors, venue, pages, DOI), and add access dates to the web entries that lack one.
2. Get permission from the Central Texas Food Bank and the video's rights holder (25 News KXXV) before publishing renders of the reconstruction.
3. The AI-use declaration in the Acknowledgments is confirmed by the author. AI tools cannot be listed as authors on arXiv or in most journals.
4. For arXiv, suggested categories are primary `cs.CV`, cross-listed to `cs.GR` and `cs.CY`. A first-time submitter in a category may need an endorsement. Upload `main.tex`, `refs.bib`, the generated `main.bbl` and `figures/`. The line numbers are a journal-review convention; remove `\linenumbers` for the arXiv version if preferred.
