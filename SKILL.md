---
name: latex-check-repair
description: Check a LaTeX source for escaping errors, unbalanced environments, undefined references and citations, and undeclared packages; repair the deterministic ones and compile when an engine exists. Use when asked to fix, compile or review a .tex file.
---

# LaTeX check, repair, compile — without ever failing the case

The rule that governs everything here: **a missing compiler is not a failed
case.** If no TeX engine is on `PATH`, the result says `compiled: false` with the
engine list it tried, and the static checks carry the verdict. A bundle that
rejects an input because it could not compile is worth zero; a bundle that
reports what it checked is worth its score.

## One command

**Where to run it.** The scripts resolve their own paths, so they work from
any working directory. If your shell starts in `/app/work` (the contract's work
directory), call the absolute path of the script —
`python3 /skills/weekly-skill/scripts/run.py --input /input/case.json --output /app/output`
— or `cd` into the folder holding this `SKILL.md` first and use the relative form
below.

```bash
python3 scripts/run.py --input /input/case.json --output /app/output          # check
python3 scripts/run.py --input /input/case.json --output /app/output --fix    # check + repair
```

With `--fix` the verdict describes the **repaired** artifact: the checks are run
again on `fixed.tex`, so `status: success` means the file really is clean.

## What is checked

| check | what it finds | repaired? |
|---|---|---|
| `escaping` | `& % # _` outside math, comments, verbatim and tabular | **yes** |
| `environments` | `\begin` without `\end`, wrong nesting, with line numbers | no — reported |
| `references` | `\ref`/`\eqref` keys with no `\label` | no |
| `citations` | `\cite` keys with no `.bib` entry | no |
| `packages` | `\toprule` without `booktabs`, `\includegraphics` without `graphicx`, … | no |
| `compile` | engine, exit code, first `!` line, whether a PDF appeared | attempted |

Only escaping is repaired automatically. Closing an environment or inventing a
label requires knowing the author's intent; guessing it would be worse than
reporting it.

## Details that matter

- **Masking preserves line numbers.** Math, verbatim and tabular regions are
  blanked character-for-character with newlines kept. Replacing a multi-line
  region with spaces shifts every later line number and makes the fix target the
  wrong line — a bug this skill had and `selftest.py` now guards.
- **`&` inside `tabular` is a column separator, not an escape error.** The region
  is masked before the escape scan.
- **Anchors point at lines.** Every repair and every remaining issue carries the
  source line and a character range; `verify.py` re-slices them.

## Failure handling

| situation | behaviour |
|---|---|
| no TeX engine | `compiled: false`, engines listed, static checks decide the status |
| compile error | `first_error` quoted, `status: partial`, log tail kept |
| no `.tex` file | `cannot_determine` |
| broken beyond repair | issues listed with lines, `fixed.tex` still written for the repaired parts |

## Limits

- It does not parse the LaTeX grammar; it scans masked lines.
- A table written without `tabular` will have its `&` escaped — correct LaTeX,
  but it breaks the intended table, so add the environment.
- Only the first `.tex` file is analysed.
