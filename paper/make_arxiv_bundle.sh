#!/usr/bin/env bash
# Builds arxiv-submission.zip: main.tex, refs.bib, the generated main.bbl, 00README.json
# (pins TeX Live 2023, whose biblatex reads the bbl format 3.2 written here) and only the
# figures main.tex includes. Usage: paper/make_arxiv_bundle.sh [output.zip]
set -euo pipefail
cd "$(dirname "$0")"
out="$(realpath -m "${1:-arxiv-submission.zip}")"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/figures"
cp main.tex refs.bib 00README.json "$stage/"
for f in $(grep -oE 'includegraphics(\[[^]]*\])?\{[^}]+\}' main.tex | sed -E 's/.*\{(.*)\}/\1/') overview.tikz; do
  cp "figures/$f" "$stage/figures/"
done
(cd "$stage" && pdflatex -interaction=nonstopmode main >/dev/null && bibtex main >/dev/null \
  && pdflatex -interaction=nonstopmode main >/dev/null && pdflatex -interaction=nonstopmode main >/dev/null)
rm -f "$out"
(cd "$stage" && zip -qrX "$out" 00README.json main.tex main.bbl refs.bib figures)
echo "wrote $out"
