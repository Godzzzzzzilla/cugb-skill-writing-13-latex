#!/usr/bin/env python3
"""Self-test for writing-13-latex."""
from __future__ import annotations
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import armkit as ak
import run as skill
PASS = FAIL = 0
def check(n, c, d=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  ok {n}" + (f" — {d}" if d else ""))
    else: FAIL += 1; print(f"  FAIL {n}" + (f" — {d}" if d else ""))
def main():
    print("writing-13 selftest")
    check("armkit pinned", bool(ak.VERSION), ak.VERSION)
    esc = skill.check_escaping("Text & more.\n\\caption{a & b}\n")
    check("stray & found", len(esc) == 2, str(len(esc)))
    ok_tab = skill.check_escaping("\\begin{tabular}{ll}a & b\\\\\\end{tabular}\n")
    check("& inside tabular ignored", not ok_tab)
    env = skill.check_environments("\\begin{itemize}\n\\item x\n\\end{document}\n")
    check("unclosed environment found", any("never closed" in i["detail"] for i in env), str(len(env)))
    ref = skill.check_references("See \\ref{fig:1}.", "@article{a2020,\n title={T}\n}")
    check("undefined ref found", any(i["kind"] == "reference" for i in ref))
    cit = skill.check_references("See \\cite{missing}.", "@article{a2020,\n title={T}\n}")
    check("missing citation found", any(i["kind"] == "citation" for i in cit))
    pkg = skill.check_packages("\\begin{document}\\toprule\\end{document}")
    check("missing package found", any(i["kind"] == "package" for i in pkg))
    fixed, changed = skill.apply_fixes("A & B\n", [{"kind": "escaping", "line": 1, "char": "&"}])
    check("escape repaired", "\\&" in fixed and len(changed) == 1, fixed.strip())
    with tempfile.TemporaryDirectory() as td:
        case = ak.CaseInput.load(os.path.join(ROOT, "examples", "case.json"))
        audit, res = ak.Audit("t"), ak.Result(td, "writing-13")
        ok = skill.solve(case, td, audit, res, do_fix=True)
        doc = res.write(case, audit, ak.derive_status(audit, ok), "t", ak.utc_now(), ["selftest"])
        check("demo completes", ok)
        check("status success", doc["status"] == "success", doc["status"])
        check("escapes repaired", len(doc["result"]["fixed_lines"]) == 3, str(doc["result"]["fixed_lines"]))
        check("fixed.tex written", os.path.isfile(os.path.join(td, "fixed.tex")))
        check("no engine is not a failure", doc["result"]["compile"]["compiled"] is False
              and doc["status"] == "success", str(doc["result"]["compile"]))
        bad = [e for e in doc["evidence"] if not ak.verify_anchor(td, e["anchor"])[0]]
        check("anchors re-slice", not bad, f"{len(doc['evidence'])} anchors")
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "in"))
        src = open(os.path.join(ROOT, "tests", "broken.tex"), encoding="utf-8").read()
        open(os.path.join(td, "in", "broken.tex"), "w", encoding="utf-8").write(src)
        open(os.path.join(td, "in", "refs.bib"), "w", encoding="utf-8").write("@article{v2022,\ntitle={T}\n}")
        json.dump({"id": "broken"}, open(os.path.join(td, "in", "case.json"), "w", encoding="utf-8"))
        case = ak.CaseInput.load(os.path.join(td, "in", "case.json"))
        audit, res = ak.Audit("t2"), ak.Result(os.path.join(td, "o"), "writing-13")
        skill.solve(case, os.path.join(td, "o"), audit, res, do_fix=False)
        kinds = res.payload.get("by_kind", {})
        check("broken fixture: environments", kinds.get("environment", 0) >= 1, str(kinds))
        check("broken fixture: reference", kinds.get("reference", 0) >= 1, str(kinds))
        check("broken fixture: citation", kinds.get("citation", 0) >= 1, str(kinds))
        check("broken fixture: package", kinds.get("package", 0) >= 1, str(kinds))
        check("broken fixture still returns a result", res.payload.get("line_count", 0) > 10)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1
if __name__ == "__main__":
    raise SystemExit(main())
