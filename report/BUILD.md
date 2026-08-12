# Build

## Output

- Artifact: `report/quantum_centauro_report.pdf`
- Expected length: approximately 8--12 readable pages.
- Scope: local document compilation only. Building this report does not start reconstruction or the local selector.

## Build

From the repository root:

```sh
make -C report
```

Or from `report/`:

```sh
latexmk -pdf -bibtex- -interaction=nonstopmode -halt-on-error -file-line-error \
  -jobname=quantum_centauro_report main.tex
```

## Inspect

```sh
pdfinfo report/quantum_centauro_report.pdf
pdftotext report/quantum_centauro_report.pdf -
pdftoppm -png -r 144 report/quantum_centauro_report.pdf report/page
```

Inspect every rendered page and the extracted text. Confirm that the report contains no private absolute paths, obsolete branding, or unsupported claims. Generated inspection images are temporary and should not be retained.

## Recorded Checks

`evidence/verification.json` distinguishes historical local unit verification from current safe checks. It records the exact focused pytest command, shell syntax validation, bundled-evidence notebook execution, and report build status. ShellCheck was unavailable locally; CI runs it when available.
