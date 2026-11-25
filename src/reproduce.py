#!/usr/bin/env python3
"""Rebuild execution/results from the bundled demo case.

    python3 src/reproduce.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable


def run(cmd, cwd):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    print(f"$ {' '.join(cmd)}\n{(proc.stdout or '').rstrip()}\n[exit {proc.returncode}]")
    return proc.returncode


def main() -> int:
    out = os.path.join(ROOT, "execution", "results", "app_output")
    os.makedirs(os.path.join(ROOT, "execution", "results"), exist_ok=True)
    rc = run([PY, "scripts/run.py", "--input", "examples/case.json", "--output", out], ROOT)
    rc |= run([PY, "scripts/verify.py", "--result", os.path.join(out, "result.json")], ROOT)
    summary = {"app_output": out, "exit": rc}
    if os.path.isfile(os.path.join(out, "result.json")):
        doc = json.load(open(os.path.join(out, "result.json"), encoding="utf-8"))
        summary.update({"status": doc.get("status"),
                        "audit": doc.get("audit", {}).get("ratio"),
                        "evidence": len(doc.get("evidence", []))})
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
