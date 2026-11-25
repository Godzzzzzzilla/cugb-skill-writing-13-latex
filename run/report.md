# writing-13-latex — case demo-latex-01

- status: **success** (static checks clean (and compiled, when an engine was present))
- checks: 8/8 passed, ratio=1.0
- evidence: 3 anchored claims
- finished: 2026-11-25T07:55:13Z

## Result

```json
{
 "issues": [
  {
   "kind": "escaping",
   "char": "&",
   "line": 6,
   "detail": "\\title{Sparse Routing & Long Contexts}",
   "fix": "\\&",
   "evidence_id": "i1"
  },
  {
   "kind": "escaping",
   "char": "&",
   "line": 11,
   "detail": "We study sparse routing & its effect on long contexts, with a budget of 32k tokens.",
   "fix": "\\&",
   "evidence_id": "i2"
  },
  {
   "kind": "escaping",
   "char": "&",
   "line": 34,
   "detail": "\\caption{Cost relative to dense attention & measured on one A100.}",
   "fix": "\\&",
   "evidence_id": "i3"
  }
 ],
 "by_kind": {
  "escaping": 3
 },
 "fixed_tex": null,
 "fixed_lines": [],
 "static_checks": {
  "escaping": 3,
  "environments": 0,
  "references": 0,
  "citations": 0,
  "packages": 0
 },
 "compile": {
  "compiled": false,
  "engine": null,
  "error": "no TeX engine on PATH (latexmk/pdflatex/xelatex/lualatex/tectonic)"
 },
 "line_count": 45
}
```

## Evidence

| id | claim | locator | ok |
|---|---|---|---|
| i1 | escaping at line 6: \title{Sparse Routing & Long Contexts} | `p.6 inputs/paper.tex[91:130]` | ✓ |
| i2 | escaping at line 11: We study sparse routing & its effect on long cont | `p.11 inputs/paper.tex[181:265]` | ✓ |
| i3 | escaping at line 34: \caption{Cost relative to dense attention & measu | `p.34 inputs/paper.tex[695:762]` | ✓ |

## Uncertainty

- **escaping at line 6** — \title{Sparse Routing & Long Contexts} (resolve: fix by hand; only escaping is repaired automatically)
- **escaping at line 11** — We study sparse routing & its effect on long contexts, with a budget of 32k tokens. (resolve: fix by hand; only escaping is repaired automatically)
- **escaping at line 34** — \caption{Cost relative to dense attention & measured on one A100.} (resolve: fix by hand; only escaping is repaired automatically)

## Limits

- no TeX engine was available; the source was checked statically only
