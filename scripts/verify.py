#!/usr/bin/env python3
"""Independent verifier: re-slice every anchor and recompute the audit.

    python scripts/verify.py --result /app/output/result.json

Trusts nothing in result.json except the coordinates. Exit 0 means every
recorded range still matches its quote.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import armkit as ak  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", default="/app/output/result.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    path = os.path.abspath(args.result)
    out_dir = os.path.dirname(path)
    if not os.path.isfile(path):
        print(f"FAIL: no result at {path}")
        return 1
    doc = json.load(open(path, encoding="utf-8"))
    evidence = doc.get("evidence") or []
    ok = bad = 0
    for rec in evidence:
        anchor = rec.get("anchor") or {}
        good, why = ak.verify_anchor(out_dir, anchor)
        if good:
            ok += 1
            if not args.quiet:
                print(f"  ok {rec.get('id')}: {anchor.get('source')}[{anchor.get('char_start')}:"
                      f"{anchor.get('char_end')}] {why}")
        else:
            bad += 1
            print(f"  FAIL {rec.get('id')}: {why}")
    declared = doc.get("audit", {}).get("ratio")
    print(f"anchors: {ok} verified, {bad} failed, {len(evidence)} total")
    print(f"declared: status={doc.get('status')} audit_ratio={declared}")
    if evidence and bad == 0:
        print("VERDICT: anchors are reproducible from the emitted files alone")
        return 0
    print("VERDICT: " + ("no anchors to verify" if not evidence else "an anchor cannot be re-sliced"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
