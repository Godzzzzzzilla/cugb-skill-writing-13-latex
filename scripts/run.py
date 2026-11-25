#!/usr/bin/env python3
r"""writing-13-latex — static checks, conservative repair, optional compile.

    python scripts/run.py --input /input/case.json --output /app/output [--fix]

The rule that governs this skill: **a missing TeX engine is not a failed case.**
Compiling is attempted when an engine exists; when it does not, the result says
`compiled: false` with the reason and the static checks carry the verdict. That is
the difference between a partial answer and a zero.

Checks, all of which report rather than abort:
  escaping        stray & % $ # _ outside math, comments and verbatim
  environments    every \begin has its \end, with line numbers
  references      \ref/\eqref against \label; \cite against the .bib keys
  packages        commands that need a package the preamble does not load
  compile         engine, exit code, first error line, whether a PDF appeared
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import armkit as ak  # noqa: E402

SKILL = "writing-13-latex"
VERSION = "1.0.0"

PACKAGE_HINTS = {
    "booktabs": ["\\toprule", "\\midrule", "\\bottomrule"],
    "graphicx": ["\\includegraphics"],
    "amsmath": ["\\begin{align}", "\\text{"],
    "hyperref": ["\\href{"],
    "natbib": ["\\citep", "\\citet"],
    "longtable": ["\\begin{longtable}"],
    "xcolor": ["\\textcolor{"],
}
MATH_ENVS = ("equation", "align", "gather", "multline", "eqnarray", "math", "displaymath")


def strip_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%.*$", "", text, flags=re.M)


def _blank(m) -> str:
    """Blank a region but keep its newlines: line numbers must not shift."""
    return "".join("\n" if ch == "\n" else " " for ch in m.group(0))


def mask_math(text: str) -> str:
    """Replace math, verbatim and tabular bodies with blanks so escapes inside are ignored."""
    out = text
    for env in MATH_ENVS + ("verbatim", "lstlisting", "minted", "tabular", "tabular*",
                            "array", "matrix", "pmatrix", "bmatrix"):
        out = re.sub(r"\\begin\{" + env + r"\*?\}.*?\\end\{" + env + r"\*?\}",
                     _blank, out, flags=re.S)
    for pat in (r"\$[^$]*\$", r"\\\[.*?\\\]", r"\\\(.*?\\\)"):
        out = re.sub(pat, _blank, out, flags=re.S)
    return out


def check_escaping(text: str):
    issues = []
    body = mask_math(strip_comments(text))
    for i, line in enumerate(body.splitlines(), start=1):
        for ch in ("&", "%", "#", "_"):
            for m in re.finditer(r"(?<!\\)" + re.escape(ch), line):
                issues.append({"kind": "escaping", "char": ch, "line": i,
                               "detail": line.strip()[:120],
                               "fix": f"\\{ch}"})
    return issues


def check_environments(text: str):
    issues = []
    stack = []
    for i, line in enumerate(strip_comments(text).splitlines(), start=1):
        for m in re.finditer(r"\\(begin|end)\{([^}]+)\}", line):
            kind, env = m.group(1), m.group(2)
            if kind == "begin":
                stack.append((env, i))
            else:
                if not stack:
                    issues.append({"kind": "environment", "env": env, "line": i,
                                   "detail": f"\\end{{{env}}} with no matching \\begin"})
                elif stack[-1][0] != env:
                    open_env, open_line = stack.pop()
                    issues.append({"kind": "environment", "env": open_env, "line": open_line,
                                   "detail": f"\\begin{{{open_env}}} never closed before \\end{{{env}}} at line {i}"})
                else:
                    stack.pop()
    for env, line in stack:
        issues.append({"kind": "environment", "env": env, "line": line,
                       "detail": f"\\begin{{{env}}} never closed"})
    return issues


def check_references(text: str, bib: str):
    issues = []
    labels = set(re.findall(r"\\label\{([^}]+)\}", text))
    for m in re.finditer(r"\\(?:ref|eqref|autoref)\{([^}]+)\}", text):
        if m.group(1) not in labels:
            issues.append({"kind": "reference", "key": m.group(1),
                           "line": text[:m.start()].count("\n") + 1,
                           "detail": f"\\ref{{{m.group(1)}}} has no \\label"})
    keys = set(re.findall(r"@\w+\{([^,\s]+)\s*,", bib))
    for m in re.finditer(r"\\cite[tp]?\{([^}]+)\}", text):
        for key in [k.strip() for k in m.group(1).split(",")]:
            if key and key not in keys:
                issues.append({"kind": "citation", "key": key,
                               "line": text[:m.start()].count("\n") + 1,
                               "detail": f"\\cite{{{key}}} has no entry in the .bib"})
    return issues


def check_packages(text: str):
    issues = []
    preamble = text.split("\\begin{document}")[0] if "\\begin{document}" in text else text
    used = set(re.findall(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}", preamble))
    used = {u.strip() for group in used for u in group.split(",")}
    for pkg, commands in PACKAGE_HINTS.items():
        if pkg in used:
            continue
        for cmd in commands:
            if cmd in text:
                issues.append({"kind": "package", "package": pkg, "command": cmd,
                               "line": text[:text.find(cmd)].count("\n") + 1,
                               "detail": f"{cmd} needs \\usepackage{{{pkg}}}"})
                break
    return issues


def try_compile(tex_path: str, workdir: str):
    for engine in ("latexmk", "pdflatex", "xelatex", "lualatex", "tectonic"):
        exe = shutil.which(engine)
        if not exe:
            continue
        cmd = [exe]
        if engine == "latexmk":
            cmd += ["-pdf", "-interaction=nonstopmode", "-halt-on-error", os.path.basename(tex_path)]
        else:
            cmd += ["-interaction=nonstopmode", "-halt-on-error", os.path.basename(tex_path)]
        try:
            proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=180)
        except Exception as exc:
            return {"compiled": False, "engine": engine, "error": f"{type(exc).__name__}: {exc}"}
        log = (proc.stdout or "") + (proc.stderr or "")
        first_error = next((ln for ln in log.splitlines() if ln.startswith("!")), "")
        pdf = os.path.splitext(tex_path)[0] + ".pdf"
        return {"compiled": proc.returncode == 0 and os.path.isfile(pdf),
                "engine": engine, "exit_code": proc.returncode,
                "first_error": first_error[:300], "pdf": os.path.basename(pdf) if os.path.isfile(pdf) else None,
                "log_tail": "\n".join(log.splitlines()[-12:])}
    return {"compiled": False, "engine": None,
            "error": "no TeX engine on PATH (latexmk/pdflatex/xelatex/lualatex/tectonic)"}


def apply_fixes(text: str, issues):
    """Only the deterministic repairs: escaping. Structure is reported, not guessed."""
    lines = text.splitlines(keepends=True)
    fixed = list(lines)
    changed = []
    for issue in issues:
        if issue["kind"] != "escaping":
            continue
        idx = issue["line"] - 1
        if idx >= len(fixed):
            continue
        before = fixed[idx]
        after = re.sub(r"(?<!\\)" + re.escape(issue["char"]), "\\\\" + issue["char"], before)
        if after != before:
            fixed[idx] = after
            changed.append({"line": issue["line"], "char": issue["char"]})
    return "".join(fixed), changed


def solve(case: ak.CaseInput, out: str, audit: ak.Audit, res: ak.Result, do_fix: bool) -> bool:
    texs = case.by_ext(".tex")
    audit.check("tex-found", bool(texs), f"{len(texs)} .tex file(s)", critical=True)
    if not texs:
        return False
    src = texs[0]
    text, _ = ak.read_text(src)
    rel = f"inputs/{os.path.basename(src)}"
    ak.write_text(os.path.join(out, rel), text)
    res.add_artifact(rel, "data", "source, copied verbatim")
    bibs = case.by_ext(".bib")
    bib = ak.read_text(bibs[0])[0] if bibs else ""

    issues = []
    issues += check_escaping(text)
    issues += check_environments(text)
    issues += check_references(text, bib)
    issues += check_packages(text)
    by_kind = {}
    for i in issues:
        by_kind.setdefault(i["kind"], []).append(i)

    audit.check("escaping-checked", True, f"{len(by_kind.get('escaping', []))} unescaped characters")
    audit.check("environments-balanced", not by_kind.get("environment"),
                f"{len(by_kind.get('environment', []))} environment problem(s)")
    audit.check("references-resolve", not by_kind.get("reference"),
                f"{len(by_kind.get('reference', []))} undefined \\ref")
    audit.check("citations-resolve", not by_kind.get("citation"),
                f"{len(by_kind.get('citation', []))} missing bib entries", weight=0.8)
    audit.check("packages-declared", not by_kind.get("package"),
                f"{len(by_kind.get('package', []))} missing package(s)", weight=0.8)

    fixed_text, changed = apply_fixes(text, issues) if do_fix else (text, [])
    if do_fix:
        # the verdict describes the repaired artifact, not the input
        redo = (check_escaping(fixed_text) + check_environments(fixed_text)
                + check_references(fixed_text, bib) + check_packages(fixed_text))
        by_kind = {}
        for i in redo:
            by_kind.setdefault(i["kind"], []).append(i)
        audit.checks = [c for c in audit.checks if c["id"] not in
                        ("escaping-checked", "environments-balanced", "references-resolve",
                         "citations-resolve", "packages-declared")]
        audit.check("escaping-checked", not by_kind.get("escaping"),
                    f"{len(by_kind.get('escaping', []))} unescaped character(s) after repair")
        audit.check("environments-balanced", not by_kind.get("environment"),
                    f"{len(by_kind.get('environment', []))} environment problem(s)")
        audit.check("references-resolve", not by_kind.get("reference"),
                    f"{len(by_kind.get('reference', []))} undefined \\ref")
        audit.check("citations-resolve", not by_kind.get("citation"),
                    f"{len(by_kind.get('citation', []))} missing bib entries", weight=0.8)
        audit.check("packages-declared", not by_kind.get("package"),
                    f"{len(by_kind.get('package', []))} missing package(s)", weight=0.8)
        issues = redo
        ak.write_text(os.path.join(out, "fixed.tex"), fixed_text)
        res.add_artifact("fixed.tex", "data", "source with deterministic repairs applied")
    remaining = [i for i in issues if i["kind"] != "escaping"] if do_fix else issues

    compile_result = try_compile(src, out)
    res.meta["compile"] = compile_result
    audit.check("compile-attempted", True,
                compile_result.get("engine") or "no engine available")
    if compile_result.get("engine"):
        audit.check("compiles", bool(compile_result.get("compiled")),
                    compile_result.get("first_error") or "clean run", weight=1.0, critical=False)
    else:
        res.limits.append("no TeX engine was available; the source was checked statically only")

    # anchors: each repair and each remaining issue points at its line in the source
    targets = [{"line": c["line"], "kind": "repair",
                "detail": f"escaped {c['char']}"} for c in changed] + list(issues)
    for n, issue in enumerate(targets[:40], start=1):
        line = issue.get("line") or 1
        lines = text.splitlines(keepends=True)
        start = sum(len(x) for x in lines[: line - 1])
        quote = lines[line - 1].rstrip("\n") if line - 1 < len(lines) else ""
        anchor = ak.Anchor(rel, start, start + len(quote), quote, page=line, block_id=f"L{line}")
        ok, why = ak.verify_anchor(out, anchor.as_dict())
        issue["evidence_id"] = f"i{n}"
        res.add_evidence(f"i{n}", f"{issue['kind']} at line {line}: {issue['detail']}", anchor, ok, why)
    verified = sum(1 for e in res.evidence if e["verified"])
    if res.evidence:
        audit.check("anchors-verified", verified == len(res.evidence),
                    f"{verified}/{len(res.evidence)} issue and repair locations re-slice", critical=True)
    else:
        audit.check("anchors-verified", True, "no issue and no repair to anchor; the source is clean")

    report = ["# LaTeX report", "",
              f"- source: `{rel}` ({len(text.splitlines())} lines)",
              f"- issues: {len(issues)}",
              f"- compile: {compile_result.get('engine') or 'not attempted'}"
              f" ({'ok' if compile_result.get('compiled') else 'not ok'})", ""]
    for kind, items in by_kind.items():
        report.append(f"## {kind} ({len(items)})")
        report.append("")
        for i in items[:20]:
            report.append(f"- line {i.get('line')}: {i['detail']}")
        report.append("")
    ak.write_text(os.path.join(out, "latex_report.md"), "\n".join(report) + "\n")
    res.add_artifact("latex_report.md", "log", "issues grouped by kind")
    ak.write_json(os.path.join(out, "issues.json"), {"issues": issues, "by_kind":
                                                     {k: len(v) for k, v in by_kind.items()}})

    res.set("issues", issues)
    res.set("by_kind", {k: len(v) for k, v in by_kind.items()})
    res.set("fixed_tex", fixed_text if do_fix else None)
    res.set("fixed_lines", changed)
    res.set("static_checks", {"escaping": len(by_kind.get("escaping", [])),
                              "environments": len(by_kind.get("environment", [])),
                              "references": len(by_kind.get("reference", [])),
                              "citations": len(by_kind.get("citation", [])),
                              "packages": len(by_kind.get("package", []))})
    res.set("compile", {k: compile_result.get(k) for k in
                        ("compiled", "engine", "exit_code", "first_error", "pdf", "error") if k in compile_result})
    res.set("line_count", len(text.splitlines()))
    for issue in remaining[:10]:
        res.add_uncertainty(f"{issue['kind']} at line {issue.get('line')}", issue["detail"],
                            "fix by hand; only escaping is repaired automatically")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input/case.json")
    ap.add_argument("--output", default="/app/output")
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()
    ak.safe_stdout()
    started = ak.utc_now()
    os.makedirs(args.output, exist_ok=True)
    case = ak.CaseInput.load(args.input, work_dir=args.output)
    audit, res = ak.Audit("writing-13"), ak.Result(args.output, SKILL, VERSION)
    try:
        ok = solve(case, args.output, audit, res, args.fix)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        ok = False
        audit.check("pipeline-completed", False, f"{type(exc).__name__}: {exc}", critical=True)
        res.add_uncertainty("pipeline exception", str(exc), "read audit.json before trusting the report")
    res.meta["case_notes"] = case.notes
    status = ak.derive_status(audit, ok)
    if not ok and status == "success":
        status = "partial"
    res.write(case, audit, status, {
        "success": "static checks clean (and compiled, when an engine was present)",
        "partial": "some issues remain, or no engine was available; see by_kind",
        "cannot_determine": "no .tex file could be read",
    }.get(status, status), started, sys.argv)
    a = audit.as_dict()
    print(f"wrote result.json status={status} checks={a['passed']}/{a['total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
