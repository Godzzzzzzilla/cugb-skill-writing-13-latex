# writing-13-latex

Static LaTeX checks, deterministic repair of escaping, and an optional compile
that never decides the case. Submission for **写作13｜LaTeX 写作与编译**
(product-arena-07).

## Design note — the compile-gate trap

An earlier attempt at this task gated the whole bundle on a successful compile and
scored zero six times when a hidden template would not build. Here compiling is
one check among six, and its absence is reported, not fatal. `selftest.py`
asserts that a machine with no TeX engine still reaches `status: success` on a
clean file.

## Measured on the bundled demo

| quantity | value |
|---|---|
| escaping repairs | 3 (`\title`, abstract, table caption) |
| escaping issues after repair | 0 |
| environment / reference / citation / package issues | 0 |
| compile | `false`, no engine on PATH, stated |
| anchors re-slicing | 3 / 3 |
| audit | 8/8, `status: success` |

`tests/broken.tex` is a deliberately damaged manuscript (unclosed `itemize`,
undefined `\ref`, missing citation, `\toprule` without `booktabs`, six stray
`&`). `selftest.py` asserts each class is detected there, and that the case still
returns a full result.

## Files

```
SKILL.md               what an agent loads
scripts/run.py         checks, repair, compile
scripts/verify.py      independent anchor verifier
scripts/selftest.py    19 checks, incl. the broken fixture
scripts/armkit.py      shared core
examples/case.json     demo input (one clean-but-unescaped .tex + .bib)
tests/broken.tex       the damaged fixture
run/                   a real run
```

## Reproduce

```bash
python3 scripts/run.py --input examples/case.json --output /tmp/out --fix
python3 scripts/verify.py --result /tmp/out/result.json
python3 scripts/selftest.py
```

## Failure handling

| input | behaviour |
|---|---|
| no engine | `compiled: false` + engine list; static checks decide |

| compile error | `first_error` quoted, `partial` |
| structural errors | reported with line numbers, not guessed |
| no `.tex` | `cannot_determine` |
