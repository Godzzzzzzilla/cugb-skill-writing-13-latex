# result.json

- `issues[]` — `{kind, line, detail, char?, key?, env?, package?, evidence_id}`.
  `kind` ∈ `escaping`, `environment`, `reference`, `citation`, `package`, `repair`.
- `by_kind` — counts per kind (after repair, when `--fix` was used).
- `fixed_tex` — the repaired source, or `null`.
- `fixed_lines[]` — `{line, char}` for each repair applied.
- `static_checks` — counts per class.
- `compile` — `{compiled, engine, exit_code?, first_error?, pdf?, error?}`.
  `engine: null` means no engine was found; that is not an error.
- `line_count`.

`status: success` requires the repaired (or original) source to be clean on all
five static classes. A compile failure lowers it to `partial` but still returns
everything else.
