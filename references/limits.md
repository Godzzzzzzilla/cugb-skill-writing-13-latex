# Limits

| condition | behaviour |
|---|---|
| table without `tabular` | its `&` are escaped; the table breaks — add the environment |
| `\input`/`\include` of other files | not followed; only the first `.tex` is read |
| macro-defined environments | not resolved |
| engine present but package missing | compile fails, `first_error` quoted |
| non-ASCII source without `inputenc`/XeLaTeX | not detected statically |
