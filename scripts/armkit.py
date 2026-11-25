"""armkit — a small, dependency-light core for verifiable research-document skills.

Everything here is deliberately boring: no third-party imports at module load, no
network, no global state.  A skill bundles this one file and calls it from run.py.
The three jobs it does are:

  1. tolerate an unknown case.json        (contract.py behaviour, below)
  2. keep every claim anchored to a byte range that anyone can re-slice
  3. never let one bad field kill the run (status is derived, not asserted)

Public surface used by task modules:

    CaseInput        read /input/case.json with aliases + file discovery
    TextLayer        page-level text files + char-range anchors
    Audit            named checks -> derived status
    Result           result.json / report.md / evidence.jsonl writer
    pdf_pages()      multi-backend PDF page extraction
    layout_blocks()  XY-cut reading order, header/footer strip, sections
    sentence_split() language-tolerant segmentation
    bib_parse()      reference-list / BibTeX parsing + dedupe + renumber
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.1.0"

# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_dir(path: str) -> str:
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def read_text(path: str) -> Tuple[str, Dict[str, Any]]:
    """Read a text file without ever raising.  Returns (text, meta)."""
    meta: Dict[str, Any] = {"path": path, "encoding": None, "replaced": 0, "bytes": 0}
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        meta["error"] = f"{type(exc).__name__}: {exc}"
        return "", meta
    meta["bytes"] = len(raw)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            meta["encoding"] = enc
            if enc == "latin-1":
                meta["replaced"] = text.count("\ufffd")
            return text, meta
        except UnicodeDecodeError:
            continue
    meta["encoding"] = "utf-8"
    meta["replaced"] = raw.count(b"\xef\xbf\xbd")
    return raw.decode("utf-8", "replace"), meta


def norm_ws(text: str) -> str:
    return re.sub(r"[ \t\u00a0\u3000]+", " ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def norm_key(text: str) -> str:
    """Comparison key: casefold, strip accents and every non-alphanumeric run."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.casefold())


def jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def clip(text: str, n: int = 160) -> str:
    text = norm_ws(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def jsonable(obj: Any) -> Any:
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    return str(obj)


def write_json(path: str, obj: Any, indent: int = 1) -> str:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(jsonable(obj), fh, ensure_ascii=False, indent=indent)
        fh.write("\n")
    return path


def write_text(path: str, text: str) -> str:
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def safe_stdout() -> None:
    """Windows consoles default to cp936; never let a print() kill a run."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# case input
# --------------------------------------------------------------------------- #

CASE_ID_KEYS = ("case_id", "caseId", "id", "task_id", "taskId", "uid", "key")
TASK_KEYS = (
    "task", "instruction", "instructions", "prompt", "question", "query",
    "requirement", "requirements", "request", "goal", "objective", "assignment",
)
CONTRACT_KEYS = ("output_contract", "outputContract", "contract", "output_schema", "expected_fields")
INPUT_KEYS = ("inputs", "input", "files", "materials", "documents", "attachments", "resources", "data")


class CaseInput:
    """A forgiving reader for /input/case.json.

    Aliases exist because the platform's fixed cases do not share one schema;
    a missing field must degrade to None, never to a KeyError.
    """

    def __init__(self, input_path: str, work_dir: str = ".", raw: Optional[dict] = None,
                 notes: Optional[List[str]] = None):
        self.input_path = os.path.abspath(input_path)
        self.work_dir = os.path.abspath(work_dir)
        self.notes: List[str] = list(notes or [])
        self.data: Dict[str, Any] = raw or {}
        self.dir = os.path.dirname(self.input_path) if os.path.isfile(self.input_path) else self.input_path

    # -- construction ------------------------------------------------------ #
    @classmethod
    def load(cls, input_path: str, work_dir: str = ".") -> "CaseInput":
        notes: List[str] = []
        data: Dict[str, Any] = {}
        if os.path.isdir(input_path):
            cand = os.path.join(input_path, "case.json")
            input_path = cand if os.path.isfile(cand) else input_path
        if os.path.isfile(input_path):
            text, meta = read_text(input_path)
            if meta.get("error"):
                notes.append(f"case.json unreadable: {meta['error']}")
            else:
                try:
                    data = json.loads(text)
                except Exception as exc:
                    notes.append(f"case.json is not JSON ({type(exc).__name__}); treated as empty")
                    data = {}
            if not isinstance(data, dict):
                notes.append("case.json root is not an object; wrapped under 'value'")
                data = {"value": data}
        else:
            notes.append(f"no case.json at {input_path}; discovering files only")
        return cls(input_path, work_dir, data, notes)

    # -- generic access ---------------------------------------------------- #
    def get(self, *keys: str, default: Any = None, depth: int = 3) -> Any:
        for key in keys:
            hit = self._lookup(self.data, key, depth)
            if hit is not None:
                return hit
        return default

    def _lookup(self, node: Any, key: str, depth: int) -> Any:
        if depth < 0 or not isinstance(node, dict):
            return None
        if key in node and node[key] not in (None, "", [], {}):
            return node[key]
        low = key.lower()
        for k, v in node.items():
            if isinstance(k, str) and k.lower() == low and v not in (None, "", [], {}):
                return v
        for v in node.values():
            if isinstance(v, dict):
                hit = self._lookup(v, key, depth - 1)
                if hit is not None:
                    return hit
        return None

    # -- named fields ------------------------------------------------------ #
    @property
    def case_id(self) -> str:
        val = self.get(*CASE_ID_KEYS)
        if isinstance(val, (str, int)) and str(val).strip():
            return str(val).strip()
        base = os.path.basename(self.dir.rstrip("/\\"))
        return base or "case"

    @property
    def task_text(self) -> str:
        val = self.get(*TASK_KEYS)
        if isinstance(val, list):
            val = "\n".join(str(v) for v in val)
        return str(val or "").strip()

    @property
    def output_contract(self) -> Dict[str, Any]:
        val = self.get(*CONTRACT_KEYS, default={})
        if isinstance(val, list):
            return {"fields": [str(v) for v in val]}
        if isinstance(val, dict):
            return val
        if isinstance(val, str) and val.strip():
            return {"description": val}
        return {}

    @property
    def contract_fields(self) -> List[str]:
        c = self.output_contract
        for key in ("fields", "required", "properties", "keys", "outputs"):
            val = c.get(key) if isinstance(c, dict) else None
            if isinstance(val, dict):
                return [str(k) for k in val.keys()]
            if isinstance(val, list):
                out = []
                for v in val:
                    if isinstance(v, str):
                        out.append(v)
                    elif isinstance(v, dict):
                        out.extend(str(k) for k in v.keys())
                if out:
                    return out
        return []

    # -- files ------------------------------------------------------------- #
    def discover(self, exts: Sequence[str] = (), skip_json: bool = False) -> List[str]:
        """Every regular file next to case.json (plus paths named inside it)."""
        found: List[str] = []
        seen = set()

        def add(path: str) -> None:
            path = os.path.abspath(path)
            if os.path.isfile(path) and path not in seen:
                if exts and not path.lower().endswith(tuple(exts)):
                    return
                if skip_json and path.lower().endswith((".json", ".jsonl")):
                    return
                if os.path.basename(path) in ("case.json", "task.md"):
                    return
                seen.add(path)
                found.append(path)

        if os.path.isdir(self.dir):
            for root, dirs, files in os.walk(self.dir):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules")]
                for name in sorted(files):
                    add(os.path.join(root, name))

        for item in self._iter_named_paths(self.data):
            for base in (self.dir, self.work_dir, os.getcwd()):
                cand = item if os.path.isabs(item) else os.path.join(base, item)
                if os.path.isfile(cand):
                    add(cand)
                    break
        return sorted(found)

    def _iter_named_paths(self, node: Any, depth: int = 0) -> Iterable[str]:
        if depth > 4:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and _looks_like_path(v):
                    yield v
                else:
                    yield from self._iter_named_paths(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                if isinstance(v, str) and _looks_like_path(v):
                    yield v
                else:
                    yield from self._iter_named_paths(v, depth + 1)

    def by_ext(self, *exts: str) -> List[str]:
        return [p for p in self.discover() if p.lower().endswith(tuple(exts))]

    @property
    def pdfs(self) -> List[str]:
        return self.by_ext(".pdf")

    def describe(self) -> Dict[str, Any]:
        return {
            "input_path": self.input_path,
            "case_id": self.case_id,
            "keys": sorted(self.data.keys())[:40],
            "contract_fields": self.contract_fields,
            "notes": self.notes,
        }


def _looks_like_path(value: str) -> bool:
    if len(value) > 400 or "\n" in value:
        return False
    if value.lower().startswith(("http://", "https://", "data:")):
        return False
    return bool(re.search(r"\.(pdf|txt|md|tex|bib|docx?|csv|tsv|json|jsonl|xlsx?|png|jpe?g|tiff?)$", value, re.I))


# --------------------------------------------------------------------------- #
# text layer + anchors
# --------------------------------------------------------------------------- #


@dataclass
class Anchor:
    """A re-sliceable pointer into an emitted text file."""

    source: str            # path relative to the output dir, e.g. pages/p001.txt
    char_start: int
    char_end: int
    quote: str = ""
    page: Optional[int] = None
    block_id: str = ""

    def as_dict(self) -> Dict[str, Any]:
        d = {
            "source": self.source,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "quote": self.quote,
        }
        if self.page is not None:
            d["page"] = self.page
        if self.block_id:
            d["block_id"] = self.block_id
        return d


class TextLayer:
    """Emitted page texts; every anchor is a slice of one of these files."""

    def __init__(self, out_dir: str, subdir: str = "pages"):
        self.out_dir = out_dir
        self.subdir = subdir
        self.pages: List[Dict[str, Any]] = []

    def add_page(self, page_no: int, text: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        rel = f"{self.subdir}/p{page_no:04d}.txt"
        write_text(os.path.join(self.out_dir, rel), text)
        rec = {"page": page_no, "path": rel, "chars": len(text), "sha256": sha256_text(text)}
        if meta:
            rec.update({k: v for k, v in meta.items() if k not in rec})
        self.pages.append(rec)
        return rec

    def anchor(self, source: str, text: str, char_start: int, char_end: int,
               page: Optional[int] = None, block_id: str = "") -> Anchor:
        return Anchor(source, char_start, char_end, text[char_start:char_end], page, block_id)

    def index(self) -> Dict[str, str]:
        return {p["path"]: p["sha256"] for p in self.pages}


def verify_anchor(out_dir: str, anchor: Dict[str, Any], normalize: bool = True) -> Tuple[bool, str]:
    """Re-slice the source file and compare with the recorded quote."""
    src = anchor.get("source") or ""
    path = os.path.join(out_dir, src)
    if not os.path.isfile(path):
        return False, f"source missing: {src}"
    text, meta = read_text(path)
    if meta.get("error"):
        return False, f"source unreadable: {meta['error']}"
    a, b = anchor.get("char_start"), anchor.get("char_end")
    if not isinstance(a, int) or not isinstance(b, int) or a < 0 or b < a or b > len(text):
        return False, f"range out of bounds: [{a},{b}) len={len(text)}"
    got = text[a:b]
    want = anchor.get("quote") or ""
    if got == want:
        return True, "exact"
    if normalize and norm_ws(got) == norm_ws(want) and norm_ws(want):
        return True, "whitespace-normalised"
    return False, f"mismatch: got {clip(got,60)!r}"


# --------------------------------------------------------------------------- #
# audit + result
# --------------------------------------------------------------------------- #

MANDATORY_BLOCKS = ("result", "evidence")


class Audit:
    """Named checks with weights; the status follows from them, never asserted."""

    def __init__(self, name: str = "audit"):
        self.name = name
        self.checks: List[Dict[str, Any]] = []

    def check(self, cid: str, ok: bool, detail: str = "", weight: float = 1.0, critical: bool = False) -> bool:
        self.checks.append({
            "id": cid,
            "ok": bool(ok),
            "detail": clip(detail, 400),
            "weight": weight,
            "critical": critical,
        })
        return bool(ok)

    def ratio(self) -> Optional[float]:
        total = sum(c["weight"] for c in self.checks)
        if total <= 0:
            return None
        return sum(c["weight"] for c in self.checks if c["ok"]) / total

    def failed_critical(self) -> List[str]:
        return [c["id"] for c in self.checks if c["critical"] and not c["ok"]]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "checks": self.checks,
            "passed": sum(1 for c in self.checks if c["ok"]),
            "total": len(self.checks),
            "ratio": None if self.ratio() is None else round(self.ratio(), 4),
            "failed_critical": self.failed_critical(),
        }


def derive_status(audit: Audit, required_ok: bool, partial_hint: bool = False) -> str:
    """success / partial / cannot_determine — never 'rejected' unless nothing ran."""
    if audit.failed_critical():
        return "partial"
    ratio = audit.ratio()
    if required_ok and (ratio is None or ratio >= 0.99) and not partial_hint:
        return "success"
    if ratio is not None and ratio <= 0.0:
        return "cannot_determine"
    return "partial"


class Result:
    """Writes the four deliverables every case must leave behind."""

    def __init__(self, out_dir: str, skill: str, version: str = "1.0.0"):
        self.out_dir = out_dir
        self.skill = skill
        self.version = version
        self.payload: Dict[str, Any] = {}
        self.evidence: List[Dict[str, Any]] = []
        self.uncertainty: List[Dict[str, Any]] = []
        self.limits: List[str] = []
        self.artifacts: List[Dict[str, Any]] = []
        self.meta: Dict[str, Any] = {}
        ensure_dir(out_dir)

    def set(self, key: str, value: Any) -> None:
        self.payload[key] = value

    def add_evidence(self, eid: str, claim: str, anchor: Anchor, verified: bool = True,
                     note: str = "") -> None:
        rec = {"id": eid, "claim": clip(claim, 300), "anchor": anchor.as_dict(), "verified": bool(verified)}
        if note:
            rec["note"] = note
        self.evidence.append(rec)

    def add_uncertainty(self, item: str, why: str, how: str = "") -> None:
        self.uncertainty.append({"item": item, "why": why, "resolved_by": how})

    def add_artifact(self, path: str, kind: str = "data", note: str = "") -> None:
        full = os.path.join(self.out_dir, path)
        rec = {"path": path, "kind": kind, "exists": os.path.isfile(full)}
        if rec["exists"]:
            rec["bytes"] = os.path.getsize(full)
            rec["sha256"] = sha256_file(full)
        if note:
            rec["note"] = note
        self.artifacts.append(rec)

    def coerce_contract(self) -> Dict[str, Any]:
        """Echo any declared contract fields we cannot map, with null + reason."""
        return {}

    def write(self, case: CaseInput, audit: Audit, status: str, status_reason: str,
              started_at: str, argv: Optional[List[str]] = None) -> Dict[str, Any]:
        contract = case.output_contract
        contract_fields = case.contract_fields
        mapped: Dict[str, Any] = {}
        unmapped: List[Dict[str, Any]] = []
        for fname in contract_fields:
            if fname in self.payload:
                mapped[fname] = self.payload[fname]
            elif fname in ("case_id", "id"):
                mapped[fname] = case.case_id
            elif fname in ("status", "run_status"):
                mapped[fname] = status
            elif fname in ("evidence", "sources"):
                mapped[fname] = self.evidence
            elif fname in ("uncertainty", "uncertainties"):
                mapped[fname] = self.uncertainty
            else:
                key = _match_payload_key(fname, self.payload)
                if key is not None:
                    mapped[fname] = self.payload[key]
                else:
                    unmapped.append({"field": fname, "value": None, "reason": "not_produced_by_this_case"})

        doc = {
            "schema": "arm-skill-result/1.1",
            "skill": {"name": self.skill, "version": self.version, "armkit": VERSION},
            "case_id": case.case_id,
            "status": status,
            "status_reason": status_reason,
            "result": self.payload,
            "evidence": self.evidence,
            "uncertainty": self.uncertainty,
            "limits": self.limits,
            "audit": audit.as_dict(),
            "artifacts": self.artifacts,
            "contract": {
                "declared_fields": contract_fields,
                "fields": mapped,
                "unmapped": unmapped,
                "raw": contract if isinstance(contract, dict) else {},
            },
            "run": {
                "started_at": started_at,
                "finished_at": utc_now(),
                "argv": argv or [],
                "python": sys.version.split()[0],
                "cwd": os.getcwd(),
                **self.meta,
            },
        }
        write_json(os.path.join(self.out_dir, "result.json"), doc)
        write_json(os.path.join(self.out_dir, "audit.json"), audit.as_dict())
        self._write_evidence_jsonl()
        self._write_report(doc, case)
        return doc

    def _write_evidence_jsonl(self) -> None:
        path = os.path.join(self.out_dir, "evidence.jsonl")
        ensure_dir(os.path.dirname(path))
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for rec in self.evidence:
                fh.write(json.dumps(jsonable(rec), ensure_ascii=False) + "\n")

    def _write_report(self, doc: Dict[str, Any], case: CaseInput) -> None:
        a = doc["audit"]
        lines = [
            f"# {self.skill} — case {doc['case_id']}",
            "",
            f"- status: **{doc['status']}** ({doc['status_reason']})",
            f"- checks: {a['passed']}/{a['total']} passed, ratio={a['ratio']}",
            f"- evidence: {len(doc['evidence'])} anchored claims",
            f"- finished: {doc['run']['finished_at']}",
            "",
            "## Result",
            "",
            "```json",
            json.dumps(jsonable(self.payload), ensure_ascii=False, indent=1)[:6000],
            "```",
            "",
            "## Evidence",
            "",
            "| id | claim | locator | ok |",
            "|---|---|---|---|",
        ]
        for e in doc["evidence"][:80]:
            anc = e["anchor"]
            loc = f"{anc.get('source')}[{anc.get('char_start')}:{anc.get('char_end')}]"
            if anc.get("page") is not None:
                loc = f"p.{anc['page']} " + loc
            lines.append(f"| {e['id']} | {e['claim'][:70]} | `{loc}` | {'✓' if e['verified'] else '✗'} |")
        if doc["uncertainty"]:
            lines += ["", "## Uncertainty", ""]
            for u in doc["uncertainty"][:40]:
                lines.append(f"- **{u['item']}** — {u['why']}" + (f" (resolve: {u['resolved_by']})" if u.get("resolved_by") else ""))
        if self.limits:
            lines += ["", "## Limits", ""]
            lines += [f"- {x}" for x in self.limits]
        write_text(os.path.join(self.out_dir, "report.md"), "\n".join(lines) + "\n")


def _match_payload_key(name: str, payload: Dict[str, Any]) -> Optional[str]:
    nk = norm_key(name)
    for key in payload:
        if norm_key(key) == nk:
            return key
    for key in payload:
        if nk and (nk in norm_key(key) or norm_key(key) in nk):
            return key
    return None


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #

_WS = re.compile(r"\s+")


@dataclass
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float = 0.0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Page:
    number: int
    width: float = 0.0
    height: float = 0.0
    words: List[Word] = field(default_factory=list)
    text: str = ""
    method: str = "none"


def pdf_pages(path: str) -> Tuple[List[Page], Dict[str, Any]]:
    """Try poppler bbox, then pdfminer, then pypdf, then a raw-stream fallback."""
    meta: Dict[str, Any] = {"path": path, "backends_tried": []}
    for name, fn in (("pdftotext-bbox", _pdf_poppler), ("pdfminer", _pdf_pdfminer), ("pypdf", _pdf_pypdf)):
        meta["backends_tried"].append(name)
        try:
            pages = fn(path)
            if pages and any(p.text.strip() for p in pages):
                meta["backend"] = name
                meta["pages"] = len(pages)
                return pages, meta
        except Exception as exc:  # backend missing or file damaged
            meta.setdefault("errors", []).append(f"{name}: {type(exc).__name__}: {exc}")
    try:
        pages = _pdf_raw(path)
        if pages:
            meta["backend"] = "raw-stream"
            meta["pages"] = len(pages)
            meta["warn"] = "no layout backend available; text order is approximate"
            return pages, meta
    except Exception as exc:
        meta.setdefault("errors", []).append(f"raw: {type(exc).__name__}: {exc}")
    meta["backend"] = "none"
    return [], meta


def _pdf_poppler(path: str) -> List[Page]:
    import shutil
    import subprocess
    import tempfile
    import xml.etree.ElementTree as ET

    exe = shutil.which("pdftotext")
    if not exe:
        raise FileNotFoundError("pdftotext not on PATH")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "p.xml")
        subprocess.run([exe, "-bbox-layout", "-q", path, out], check=True, timeout=180,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tree = ET.parse(out)
    ns = {"x": "http://www.w3.org/1999/xhtml"}
    pages: List[Page] = []
    root = tree.getroot()
    for i, pg in enumerate(root.iter("{http://www.w3.org/1999/xhtml}page"), start=1):
        page = Page(number=i, width=float(pg.get("width", 0)), height=float(pg.get("height", 0)),
                    method="pdftotext-bbox")
        for w in pg.iter("{http://www.w3.org/1999/xhtml}word"):
            page.words.append(Word(
                (w.text or "").strip(),
                float(w.get("xMin", 0)), float(w.get("yMin", 0)),
                float(w.get("xMax", 0)), float(w.get("yMax", 0)),
                float(w.get("yMax", 0)) - float(w.get("yMin", 0)),
            ))
        page.words = [w for w in page.words if w.text]
        page.text = words_to_text(page.words)
        pages.append(page)
    if not pages:
        raise ValueError("no <page> elements")
    return pages


def _pdf_pdfminer(path: str) -> List[Page]:
    """pdfminer: keep the line's own text, borrow geometry from its glyphs.

    Splitting glyphs by adjacency changed the text ("Spar serouting") and cost
    most of the window recall.  So the text comes from LTTextLine.get_text() and
    each character is paired, in order, with its LTChar box; a word ends at
    whitespace.  The concatenation of the words is the line's text, by
    construction.
    """
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTTextContainer, LTTextLine, LTChar

    pages: List[Page] = []
    for i, layout in enumerate(extract_pages(path, laparams=LAParams(all_texts=True, detect_vertical=True)), start=1):
        page = Page(number=i, width=float(getattr(layout, "width", 0)), height=float(getattr(layout, "height", 0)),
                    method="pdfminer")
        for element in layout:
            if not isinstance(element, LTTextContainer):
                continue
            for line in element:
                if not isinstance(line, LTTextLine):
                    continue
                text = line.get_text()
                chars = [c for c in line if isinstance(c, LTChar) and c.get_text()]
                page.words.extend(_pair_chars_to_words(text, chars))
        # pdfminer measures from the bottom-left; the layout stage works top-down
        for w in page.words:
            w.y0, w.y1 = page.height - w.y1, page.height - w.y0
        page.text = words_to_text(page.words)
        pages.append(page)
    if not pages:
        raise ValueError("pdfminer produced no pages")
    return pages


def _pair_chars_to_words(text: str, chars) -> List[Word]:
    """Zip the line's characters with the glyph boxes, one word per whitespace run."""
    words: List[Word] = []
    ci = 0
    cur = []
    for ch in text:
        if ch.isspace():
            if cur:
                words.append(_word_from_chars(cur))
                cur = []
            continue
        while ci < len(chars) and chars[ci].get_text() != ch:
            ci += 1
        if ci < len(chars):
            cur.append(chars[ci])
            ci += 1
        else:
            # no box left for this character: keep the text, reuse the last box
            if cur:
                cur.append(cur[-1])
    if cur:
        words.append(_word_from_chars(cur))
    return words


def _word_from_chars(chars) -> Word:
    text = "".join(c.get_text() for c in chars)
    return Word(text, min(c.x0 for c in chars), min(c.y0 for c in chars),
                max(c.x1 for c in chars), max(c.y1 for c in chars),
                max(c.size for c in chars))


def _pdf_pypdf(path: str) -> List[Page]:
    """pypdf gives one text chunk per Tj with a text matrix; keep chunks whole.

    Coordinates are converted to the same top-down space poppler uses, so the
    layout stage does not need to know which backend produced a page.
    """
    from pypdf import PdfReader

    reader = PdfReader(path)
    pages: List[Page] = []
    for i, pg in enumerate(reader.pages, start=1):
        box = pg.mediabox
        w, h = float(box.width), float(box.height)
        page = Page(number=i, width=w, height=h, method="pypdf")
        chunks: List[Tuple[float, float, str, float]] = []
        try:
            def visitor(text, cm, tm, font_dict, font_size, _chunks=chunks):
                if text and text.strip():
                    size = float(font_size or 10.0)
                    _chunks.append((float(tm[4]), float(tm[5]), text, size))

            pg.extract_text(visitor_text=visitor)  # type: ignore[call-arg]
        except Exception:
            chunks = []
        for x, baseline, text, size in chunks:
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue
            width = 0.48 * size * len(text)
            top = h - baseline
            page.words.append(Word(text, x, top - size, x + width, top, size))
        if not page.words:
            page.text = pg.extract_text() or ""
        else:
            page.text = words_to_text(page.words)
        pages.append(page)
    if not pages:
        raise ValueError("pypdf produced no pages")
    return pages


def _pdf_raw(path: str) -> List[Page]:
    """Last resort: pull literal strings out of FlateDecode streams."""
    import zlib

    with open(path, "rb") as fh:
        blob = fh.read()
    chunks: List[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", blob, re.S):
        data = m.group(1)
        try:
            data = zlib.decompress(data)
        except Exception:
            pass
        text = []
        for t in re.finditer(rb"\((?:\\.|[^\\()])*\)", data):
            raw = t.group(0)[1:-1]
            raw = raw.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            try:
                text.append(raw.decode("latin-1"))
            except Exception:
                pass
        if text:
            chunks.append(" ".join(text))
    if not chunks:
        raise ValueError("no text streams")
    text = _WS.sub(" ", " ".join(chunks)).strip()
    return [Page(number=1, text=text, method="raw-stream")]


def words_to_text(words: Sequence[Word]) -> str:
    """Reading-order text from words: cluster to lines, join with spaces, blank line between blocks."""
    if not words:
        return ""
    return " ".join(w.text for w in words)


# --------------------------------------------------------------------------- #
# layout: XY-cut reading order, header/footer, sections, tables
# --------------------------------------------------------------------------- #

HDR_FTR_BAND = 0.075
BODY_FONTSIZE_HINT = 0.0


def group_lines(words: Sequence[Word], y_tol: float = 3.0) -> List[List[Word]]:
    if not words:
        return []
    ordered = sorted(words, key=lambda w: (round(w.y0 / max(y_tol, 0.1)), w.x0))
    lines: List[List[Word]] = []
    for w in ordered:
        if lines:
            last = lines[-1]
            ref = sum(x.cy for x in last) / len(last)
            if abs(w.cy - ref) <= y_tol:
                last.append(w)
                continue
        lines.append([w])
    for line in lines:
        line.sort(key=lambda w: w.x0)
    lines.sort(key=lambda ln: (min(w.y0 for w in ln), min(w.x0 for w in ln)))
    return lines


def _line_text(line: Sequence[Word]) -> str:
    """Join one line's chunks.  Both backends emit whitespace-separated units,
    so a single space is the right separator; a trailing hyphen is merged."""
    out = ""
    for w in line:
        tok = w.text
        if not out:
            out = tok
            continue
        if out.endswith("-") and not out.endswith("--") and tok[:1].islower():
            out = out[:-1] + tok
        elif out.endswith(" ") or tok.startswith(" "):
            out += tok
        else:
            out += " " + tok
    return out.strip()


def _best_gap(words: Sequence[Word], axis: str, span: float, min_ratio: float) -> Tuple[float, Optional[float]]:
    """Widest empty strip perpendicular to `axis` ('x' = vertical cut) that no box crosses."""
    if axis == "x":
        edges = sorted({round(w.x0, 2) for w in words} | {round(w.x1, 2) for w in words})
        cross = lambda mid: any(w.x0 < mid - 0.5 and w.x1 > mid + 0.5 for w in words)
    else:
        edges = sorted({round(w.y0, 2) for w in words} | {round(w.y1, 2) for w in words})
        cross = lambda mid: any(w.y0 < mid - 0.5 and w.y1 > mid + 0.5 for w in words)
    best, at = 0.0, None
    for a, b in zip(edges, edges[1:]):
        gap = b - a
        if gap < span * min_ratio:
            continue
        mid = (a + b) / 2
        if not cross(mid) and gap > best:
            best, at = gap, mid
    return best, at


def xy_cut(words: Sequence[Word], page_w: float, page_h: Optional[float] = None,
           depth: int = 0, min_block: int = 3) -> List[List[Word]]:
    """Recursive XY-cut over both axes; returns blocks in human reading order.

    A vertical cut is only admissible when no box straddles it, so a full-width
    title or abstract forces a horizontal cut first and the columns below are
    then separated inside the lower region — which is exactly how a reader
    moves through the page.
    """
    if not words:
        return []
    if depth > 8 or len(words) < min_block:
        return [list(words)]
    page_h = page_h or page_w * 1.414

    vgap, vx = _best_gap(words, "x", page_w, 0.03)
    hgap, hy = _best_gap(words, "y", page_h, 0.006)

    # normalise so the two axes are comparable regardless of page shape
    v_score = vgap / page_w if vx is not None else -1.0
    h_score = hgap / page_h if hy is not None else -1.0

    if v_score <= 0 and h_score <= 0:
        lines = group_lines(words)
        return _merge_lines(lines)

    if v_score >= h_score:
        left = [w for w in words if w.cx < vx]
        right = [w for w in words if w.cx >= vx]
        if not left or not right:
            return _merge_lines(group_lines(words))
        return xy_cut(left, page_w, page_h, depth + 1, min_block) + \
               xy_cut(right, page_w, page_h, depth + 1, min_block)

    top = [w for w in words if w.cy < hy]
    bottom = [w for w in words if w.cy >= hy]
    if not top or not bottom:
        return _merge_lines(group_lines(words))
    return xy_cut(top, page_w, page_h, depth + 1, min_block) + \
           xy_cut(bottom, page_w, page_h, depth + 1, min_block)


def _merge_lines(lines: List[List[Word]]) -> List[List[Word]]:
    """No admissible cut left: merge adjacent lines of the same column run."""
    blocks: List[List[Word]] = []
    for line in lines:
        if blocks:
            prev = blocks[-1]
            gap = min(w.y0 for w in line) - max(w.y1 for w in prev)
            same_col = abs(line[0].x0 - prev[0].x0) < max(12.0, 4.0)
            if gap < 1.2 * max(1.0, max(w.y1 - w.y0 for w in prev)) and same_col:
                prev.extend(line)
                continue
        blocks.append(list(line))
    return blocks


def _is_header_footer(text: str, y: float, h: float) -> bool:
    """Only unambiguous cases: a bare page number or a "page n of m" line.

    A short line in the top band is NOT dropped here — titles, author lines and
    section numbers live there.  Running heads are removed by the frequency
    filter instead, which needs evidence from more than one page.
    """
    t = text.strip()
    if not t:
        return True
    if re.fullmatch(r"[-–—\s]*\d{1,4}[-–—\s]*", t):
        return True
    if re.fullmatch(r"(page|p\.)\s*\d+(\s*(of|/)\s*\d+)?", t, re.I):
        return True
    return False


def repeated_band_texts(pages: Sequence[Page]) -> set:
    seen: Dict[str, int] = {}
    for pg in pages:
        if not pg.words or not pg.height:
            continue
        for w in pg.words:
            if w.y0 < pg.height * HDR_FTR_BAND or w.y0 > pg.height * (1 - HDR_FTR_BAND):
                continue
        for line in group_lines(pg.words):
            y = min(w.y0 for w in line)
            if y < pg.height * HDR_FTR_BAND or y > pg.height * (1 - HDR_FTR_BAND):
                # page numbers change per page; compare the band text with digits removed
                key = norm_key(re.sub(r"\d+", "", _line_text(line)))
                if 3 <= len(key) <= 90:
                    seen[key] = seen.get(key, 0) + 1
    threshold = max(2, int(0.3 * len(pages)))
    return {k for k, v in seen.items() if v >= threshold}


def split_paragraphs(lines: List[List[Word]], body_size: float) -> List[List[List[Word]]]:
    """Split a column's line stream into paragraphs.

    Two signals only: a line set in a larger face starts its own block, and a
    line that stops well short of the column's right edge ends the paragraph
    when it also closes a sentence.
    """
    if not lines:
        return []
    right = max(max(w.x1 for w in ln) for ln in lines)
    paras: List[List[List[Word]]] = []
    cur: List[List[Word]] = []
    for ln in lines:
        txt = _line_text(ln)
        size = max((w.size for w in ln), default=body_size)
        heading = size >= body_size * 1.06 and len(txt) < 120
        if heading:
            if cur:
                paras.append(cur)
                cur = []
            paras.append([ln])
            continue
        cur.append(ln)
        x1 = max(w.x1 for w in ln)
        h = max(1.0, max(w.y1 for w in ln) - min(w.y0 for w in ln))
        if x1 < right - 1.6 * h and re.search(r"[.。!?;:]\s*$", txt):
            paras.append(cur)
            cur = []
    if cur:
        paras.append(cur)
    return paras


def layout_blocks(pages: Sequence[Page]) -> Dict[str, Any]:
    """Turn pages into ordered blocks with running char offsets per page."""
    repeated = repeated_band_texts(pages)
    out_pages: List[Dict[str, Any]] = []
    for pg in pages:
        body = [w for w in pg.words if w.text]
        if body:
            lines = group_lines(body)
            kept: List[Word] = []
            dropped: List[Dict[str, Any]] = []
            for line in lines:
                txt = _line_text(line)
                y = min(w.y0 for w in line)
                key = norm_key(re.sub(r"\d+", "", txt))
                if key and key in repeated:
                    dropped.append({"text": clip(txt, 120), "reason": "repeated-band"})
                    continue
                if _is_header_footer(txt, y, pg.height or 800.0):
                    dropped.append({"text": clip(txt, 120), "reason": "header/footer-page-number"})
                    continue
                kept.extend(line)
            blocks = xy_cut(kept, pg.width or 600.0)
            sizes = sorted(w.size for w in kept if w.size > 0)
            body = sizes[len(sizes) // 2] if sizes else 10.0
            paragraphs: List[List[List[Word]]] = []
            for block in blocks:
                paragraphs.extend(split_paragraphs(group_lines(block), body))
            assembled = []
            for bi, para in enumerate(paragraphs, start=1):
                text = " ".join(_line_text(ln) for ln in para)
                text = _WS.sub(" ", text).strip()
                if not text:
                    continue
                words = [w for ln in para for w in ln]
                assembled.append({
                    "page": pg.number,
                    "block_id": f"p{pg.number:03d}b{bi:02d}",
                    "text": text,
                    "bbox": [round(min(w.x0 for w in words), 1), round(min(w.y0 for w in words), 1),
                             round(max(w.x1 for w in words), 1), round(max(w.y1 for w in words), 1)],
                    "size": round(max((w.size for w in words), default=0.0), 1),
                    "lines": len(para),
                })
            out_pages.append({"page": pg.number, "blocks": assembled, "dropped": dropped,
                              "width": pg.width, "height": pg.height, "method": pg.method})
        else:
            text = pg.text.strip()
            blocks = [{"page": pg.number, "block_id": f"p{pg.number:03d}b01", "text": text,
                       "bbox": [0, 0, pg.width, pg.height], "size": 0.0, "lines": text.count("\n") + 1}] if text else []
            out_pages.append({"page": pg.number, "blocks": blocks, "dropped": [],
                              "width": pg.width, "height": pg.height, "method": pg.method})
    _assign_offsets(out_pages)
    return {"pages": out_pages, "repeated_band": sorted(repeated)[:20]}


def _assign_offsets(pages: List[Dict[str, Any]]) -> None:
    """Write page text as blocks joined by blank lines; record offsets on each block."""
    for pg in pages:
        cursor = 0
        parts: List[str] = []
        for b in pg["blocks"]:
            if parts:
                cursor += 2  # "\n\n"
            b["char_start"] = cursor
            b["char_end"] = cursor + len(b["text"])
            parts.append(b["text"])
            cursor = b["char_end"]
        pg["text"] = "\n\n".join(parts)
        pg["chars"] = len(pg["text"])


HEADING_NUM = re.compile(
    r"^\s*((\d{1,2}(\.\d{1,2}){0,3})|([IVX]{1,5}\.)|([一二三四五六七八九十]+[、.]))\s+[A-Za-z\u4e00-\u9fff]"
)
SECTION_WORDS = re.compile(
    r"^\s*(abstract|摘要|introduction|引言|前言|related work|相关工作|background|背景|"
    r"methods?|methodology|材料与方法|方法|experiments?|实验|results?|结果|discussion|讨论|"
    r"conclusions?|结论|references?|参考文献|acknowledge?ments?|致谢|appendix|附录|supplementary)\b",
    re.I,
)


def detect_sections(pages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sizes = [b["size"] for pg in pages for b in pg["blocks"] if b["size"] > 0]
    body = sorted(sizes)[len(sizes) // 2] if sizes else 0.0
    # front matter on page 1 sits above the abstract heading; it is not a section
    front_limit = -1.0
    for pg in pages[:1]:
        for b in pg["blocks"]:
            if re.match(r"^\s*(abstract|摘要)\b", b["text"], re.I):
                front_limit = b["bbox"][1]
                break
    sections: List[Dict[str, Any]] = []
    for pg in pages:
        for b in pg["blocks"]:
            t = b["text"]
            if len(t) > 160:
                continue
            if pg["page"] == 1 and front_limit > 0 and b["bbox"][1] < front_limit - 1:
                continue
            if EQUATION_NO.search(t) and len(t) < 120:
                continue
            big = body > 0 and b["size"] >= body * 1.06
            numbered = bool(HEADING_NUM.match(t))
            known = bool(SECTION_WORDS.match(t)) and len(t) < 80
            if big or numbered or known:
                if re.search(r"[。;；]$", t) and not numbered:
                    continue
                level = 1
                m = re.match(r"^\s*(\d{1,2})(\.\d{1,2})*", t)
                if m:
                    level = t[: m.end()].count(".") + 1
                sections.append({
                    "title": clip(t, 160),
                    "level": level,
                    "page": b["page"],
                    "block_id": b["block_id"],
                    "char_start": b["char_start"],
                    "char_end": b["char_end"],
                    "evidence": "font-size" if big else ("numbering" if numbered else "keyword"),
                })
    return sections


CAPTION = re.compile(r"^\s*((fig(ure)?|tab(le)?|scheme|图|表|附图)\s*[.．:：]\s*(\d+|[IVX]+)|"
                     r"(fig(ure)?|tab(le)?|图|表)\s+(\d+|[IVX]+)\s*[.:：])", re.I)
EQUATION_NO = re.compile(r"\(\s*(\d{1,3}[a-z]?)\s*\)\s*$")


def detect_captions(pages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for pg in pages:
        for b in pg["blocks"]:
            if CAPTION.match(b["text"]) and len(b["text"]) < 500:
                out.append({
                    "kind": "table" if re.match(r"^\s*(tab|表)", b["text"], re.I) else "figure",
                    "text": clip(b["text"], 300),
                    "page": b["page"],
                    "block_id": b["block_id"],
                    "char_start": b["char_start"],
                    "char_end": b["char_end"],
                    "bbox": b.get("bbox"),
                })
    return out


def detect_equations(pages: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Numbered equations, including the case where the number lands in its own block.

    A right-aligned "(3)" is often a separate block because a wide gap precedes
    it; pairing it with the block that carries the math symbols is what makes the
    detector backend-independent.
    """
    out = []
    for pg in pages:
        blocks = pg["blocks"]
        for i, b in enumerate(blocks):
            t = b["text"]
            m = EQUATION_NO.search(t)
            if m and len(t) < 300 and re.search(r"[=≈≤≥<>±∑∫√α-ωΑ-Ω]", t):
                out.append({"number": m.group(1), "text": clip(t, 300), "page": b["page"],
                            "block_id": b["block_id"], "char_start": b["char_start"],
                            "char_end": b["char_end"]})
                continue
            bare = re.fullmatch(r"\(\s*(\d{1,3}[a-z]?)\s*\)", t.strip())
            if bare and i > 0:
                prev = blocks[i - 1]
                if re.search(r"[=≈≤≥<>±∑∫√]", prev["text"]) and len(prev["text"]) < 300:
                    out.append({"number": bare.group(1),
                                "text": clip(prev["text"], 300),
                                "with_number": clip(prev["text"] + " " + t.strip(), 300),
                                "number_block": b["block_id"],
                                "page": prev["page"], "block_id": prev["block_id"],
                                "char_start": prev["char_start"], "char_end": prev["char_end"]})
    return out


def _column_starts(lines: Sequence[List[Word]], tol: float = 7.0) -> List[float]:
    xs = sorted(w.x0 for ln in lines for w in ln)
    clusters: List[List[float]] = []
    for x in xs:
        if clusters and x - clusters[-1][-1] <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    starts = [c[0] for c in clusters if len(c) >= 1]
    return starts


def detect_tables(pages: Sequence[Dict[str, Any]], words_by_page: Dict[int, List[Word]],
                  captions: Optional[Sequence[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Rows that share a stable set of column starts become a table.

    Prose also has several words per line, so the test is not "many words" but
    "the same few columns recur in most rows".  A run needs >=3 rows, >=3
    columns, columns present in >=60% of rows, and at least one numeric cell;
    anything looser turns paragraphs into tables.
    """
    tables: List[Dict[str, Any]] = []
    # a "Table N." caption is the strongest signal there is: look under it first
    windows: Dict[int, List[Tuple[float, float]]] = {}
    for cap in (captions or []):
        if cap.get("kind") != "table" or not cap.get("bbox"):
            continue
        top = cap["bbox"][3]
        windows.setdefault(cap["page"], []).append((top + 6, top + 280))

    for pg in pages:
        words = words_by_page.get(pg["page"], [])
        if not words:
            continue
        lines = group_lines(words)
        if pg["page"] in windows:
            for lo, hi in windows[pg["page"]]:
                sel = [ln for ln in lines if lo <= min(w.y0 for w in ln) <= hi]
                sel = [ln for ln in sel if not CAPTION.match(_line_text(ln))]
                cand = _table_from_lines(sel)
                if cand:
                    cand["page"] = pg["page"]
                    cand["evidence"] = "caption-anchored"
                    tables.append(cand)
            if any(t["page"] == pg["page"] for t in tables):
                continue
        runs: List[List[List[Word]]] = []
        cur: List[List[Word]] = []
        for ln in lines:
            if len(ln) >= 3:
                cur.append(ln)
            else:
                if len(cur) >= 3:
                    runs.append(cur)
                cur = []
        if len(cur) >= 3:
            runs.append(cur)

        for run in runs:
            starts = _column_starts(run, tol=6.0)
            if not (3 <= len(starts) <= 10):
                continue
            # keep only starts that recur in most rows
            support = []
            for st in starts:
                n = sum(1 for ln in run if any(abs(w.x0 - st) <= 6 for w in ln))
                support.append(n)
            keep = [st for st, n in zip(starts, support) if n >= 0.6 * len(run)]
            if len(keep) < 3:
                continue
            rows: List[List[str]] = []
            for ln in run:
                cells = [""] * len(keep)
                for w in ln:
                    idx = max((i for i, st in enumerate(keep) if w.x0 + 4 >= st), default=0)
                    cells[idx] = (cells[idx] + " " + w.text).strip()
                if sum(1 for c in cells if c) >= 2:
                    rows.append(cells)
            if len(rows) < 3:
                continue
            filled = sum(1 for r in rows if sum(1 for c in r if c) >= len(keep) - 1)
            numeric = any(re.search(r"\d", c) for r in rows for c in r)
            if filled < 0.5 * len(rows) or not numeric:
                continue
            tables.append({
                "page": pg["page"], "rows": rows, "n_rows": len(rows), "n_cols": len(keep),
                "bbox": [round(min(w.x0 for ln in run for w in ln), 1),
                         round(min(w.y0 for ln in run for w in ln), 1),
                         round(max(w.x1 for ln in run for w in ln), 1),
                         round(max(w.y1 for ln in run for w in ln), 1)],
                "evidence": "recurring-column-starts",
            })
    return tables


def _cells_in_line(line: Sequence[Word], gap_factor: float = 0.6) -> List[Tuple[float, str]]:
    """Split one line into cells on wide gaps; a cell's x0 is its first word's x0."""
    cells: List[Tuple[float, str]] = []
    cur_x, cur = None, ""
    prev = None
    for w in line:
        h = max(1.0, w.y1 - w.y0)
        if prev is not None and (w.x0 - prev.x1) > gap_factor * h:
            cells.append((cur_x, cur.strip()))
            cur, cur_x = "", None
        if cur_x is None:
            cur_x = w.x0
        cur += (" " if cur and not cur.endswith(" ") else "") + w.text
        prev = w
    if cur.strip():
        cells.append((cur_x, cur.strip()))
    return cells


def _table_from_lines(lines: List[List[Word]], min_rows: int = 3) -> Optional[Dict[str, Any]]:
    """Build a table from an explicit set of lines (a caption-delimited region)."""
    lines = [ln for ln in lines if len(ln) >= 2]
    if len(lines) < min_rows:
        return None
    # the header row gives the column edges; body rows can wrap inside a cell
    header = [x for x, _ in _cells_in_line(lines[0])]
    keep = header if 3 <= len(header) <= 10 else []
    if not keep:
        starts = _column_starts(lines, tol=6.0)
        keep = [st for st in starts
                if sum(1 for ln in lines if any(abs(w.x0 - st) <= 6 for w in ln)) >= 0.45 * len(lines)]
    if len(keep) < 2:
        return None
    rows = []
    for ln in lines:
        cells = [""] * len(keep)
        for w in ln:
            idx = max((i for i, st in enumerate(keep) if w.x0 + 4 >= st), default=0)
            cells[idx] = (cells[idx] + " " + w.text).strip()
        if sum(1 for c in cells if c) >= 2:
            rows.append(cells)
    if len(rows) < min_rows:
        return None
    return {
        "rows": rows, "n_rows": len(rows), "n_cols": len(keep),
        "bbox": [round(min(w.x0 for ln in lines for w in ln), 1),
                 round(min(w.y0 for ln in lines for w in ln), 1),
                 round(max(w.x1 for ln in lines for w in ln), 1),
                 round(max(w.y1 for ln in lines for w in ln), 1)],
    }


# --------------------------------------------------------------------------- #
# segmentation and token preservation
# --------------------------------------------------------------------------- #

SENT_END = re.compile(r"(?<=[.!?。！？；;])\s+|(?<=[。！？])")


def sentence_split(text: str) -> List[str]:
    text = text.replace("\n", " ")
    parts = [p.strip() for p in SENT_END.split(text) if p and p.strip()]
    return parts


NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?(?:\s?[%‰])?")
UNIT_RE = re.compile(r"\b(mg|g|kg|ml|l|μl|µm|nm|mm|cm|m|km|s|ms|min|h|d|K|°C|Pa|kPa|MPa|GPa|Hz|kHz|MHz|GHz|eV|keV|MeV|GeV|mol|mmol|µmol|M|mM|µM|nM|U|IU|rpm|g/L|mg/L|wt%|vol%|at%)\b")
CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]|\(([A-Z][A-Za-z\-]+(?:\s+et al\.?)?,\s*(19|20)\d{2}[a-z]?)\)")
NEGATION = {"not", "no", "none", "never", "neither", "nor", "without", "cannot", "can't", "doesn't", "didn't",
            "不", "无", "未", "非", "没有", "无法", "不能", "并非", "否认"}
HEDGE = {"may", "might", "could", "suggest", "suggests", "likely", "possibly", "probably", "appears",
         "seems", "indicate", "indicates", "估计", "可能", "或许", "似乎", "推测", "倾向于"}


def tokens_of(text: str) -> Dict[str, List[str]]:
    low = text.casefold()
    words = re.findall(r"[a-z][a-z\-']+", low)
    return {
        "numbers": NUMBER_RE.findall(text),
        "units": UNIT_RE.findall(text),
        "citations": [m.group(0) for m in CITE_RE.finditer(text)],
        "negations": [w for w in words if w in NEGATION] + [w for w in "不 无 未 非 没有 无法 不能" if w in text],
        "hedges": [w for w in words if w in HEDGE],
    }


def preserved(before: str, after: str) -> Dict[str, Any]:
    tb, ta = tokens_of(before), tokens_of(after)
    out = {}
    for key in tb:
        b = sorted(tb[key])
        a = sorted(ta[key])
        missing = _multiset_missing(b, a)
        out[key] = {"ok": not missing, "missing": missing[:8], "n_before": len(b), "n_after": len(a)}
    return out


def _multiset_missing(before: List[str], after: List[str]) -> List[str]:
    pool = list(after)
    missing = []
    for item in before:
        if item in pool:
            pool.remove(item)
        else:
            missing.append(item)
    return missing


# --------------------------------------------------------------------------- #
# references / BibTeX
# --------------------------------------------------------------------------- #

BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)\n\s*\}", re.S)
REF_START = re.compile(r"^\s*(\[(\d{1,3})\]|(\d{1,3})[.)]\s|([A-Z][A-Za-z'\-]+,\s+[A-Z]))")


@dataclass
class Reference:
    key: str
    raw: str
    authors: List[str] = field(default_factory=list)
    year: str = ""
    title: str = ""
    venue: str = ""
    doi: str = ""
    source: str = ""
    index: Optional[int] = None

    def fingerprint(self) -> str:
        first = norm_key(self.authors[0]) if self.authors else ""
        return "|".join([first, norm_key(self.title), self.year])


def _bib_fields(body: str) -> Dict[str, str]:
    fields = {}
    for m in re.finditer(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|[^,]+)\s*,?", body):
        name = m.group(1).lower()
        val = m.group(2).strip()
        if val.startswith("{") and val.endswith("}"):
            val = val[1:-1]
        elif val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        fields[name] = re.sub(r"\s+", " ", val).strip()
    return fields


def _split_authors(raw: str) -> List[str]:
    if not raw:
        return []
    parts = re.split(r"\s+and\s+|;\s*", raw)
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if "," in p:
            last, _, first = p.partition(",")
            out.append(f"{first.strip()} {last.strip()}".strip())
        else:
            out.append(p)
    return out


def bib_parse(text: str) -> List[Reference]:
    refs: List[Reference] = []
    for m in BIB_ENTRY.finditer(text):
        kind, key, body = m.group(1), m.group(2), m.group(3)
        f = _bib_fields(body)
        refs.append(Reference(
            key=key,
            raw=m.group(0),
            authors=_split_authors(f.get("author", "")),
            year=(f.get("year") or "")[:4],
            title=f.get("title", ""),
            venue=f.get("journal") or f.get("booktitle") or f.get("publisher") or "",
            doi=(f.get("doi") or "").lower(),
            source=kind,
        ))
    return refs


def ref_parse_plain(text: str) -> List[Reference]:
    """Split a reference list into entries by leading [n] / n. markers."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    entries: List[List[str]] = []
    for ln in lines:
        if not ln.strip():
            continue
        if REF_START.match(ln) and (not entries or len(" ".join(entries[-1])) > 40):
            entries.append([ln])
        elif entries:
            entries[-1].append(ln)
        else:
            entries.append([ln])
    refs = []
    for i, chunk in enumerate(entries, start=1):
        raw = re.sub(r"\s+", " ", " ".join(chunk)).strip()
        m = re.match(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})[.)])\s*", raw)
        idx = int(m.group(1) or m.group(2)) if m else None
        body = raw[m.end():] if m else raw
        year = ""
        ym = re.search(r"\b(19|20)\d{2}\b", body)
        if ym:
            year = ym.group(0)
        doi = ""
        dm = re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", body)
        if dm:
            doi = dm.group(0).rstrip(".").lower()
        head = body.split(".")[0] if "." in body else body
        authors = [a.strip() for a in re.split(r",|;|\band\b", head) if a.strip()][:12]
        title = ""
        tm = re.search(r"[.。]\s*([A-Z][^.]{10,200})[.。]", body)
        if tm:
            title = tm.group(1).strip()
        refs.append(Reference(key=f"ref{i}", raw=raw, authors=authors, year=year, title=title,
                              venue="", doi=doi, source="plain", index=idx))
    return refs


def dedupe_refs(refs: Sequence[Reference]) -> Tuple[List[Reference], List[Dict[str, Any]]]:
    kept: List[Reference] = []
    merges: List[Dict[str, Any]] = []
    for ref in refs:
        dup = None
        for i, other in enumerate(kept):
            if ref.doi and other.doi and ref.doi == other.doi:
                dup = i
                break
            fp_a, fp_b = ref.fingerprint(), other.fingerprint()
            if fp_a and fp_a == fp_b:
                dup = i
                break
            if ref.title and other.title and norm_key(ref.title) == norm_key(other.title) \
                    and (not ref.year or not other.year or ref.year == other.year):
                dup = i
                break
        if dup is None:
            kept.append(ref)
        else:
            merges.append({"kept": kept[dup].raw[:120], "merged": ref.raw[:120],
                           "reason": "doi" if ref.doi and ref.doi == kept[dup].doi else "title/year"})
            if not kept[dup].doi and ref.doi:
                kept[dup].doi = ref.doi
    return kept, merges


def cite_keys_in_order(text: str) -> List[str]:
    order: List[str] = []
    for m in CITE_RE.finditer(text):
        if m.group(1):
            for part in re.split(r"\s*,\s*", m.group(1)):
                rng = re.match(r"(\d+)\s*[–-]\s*(\d+)", part)
                if rng:
                    for n in range(int(rng.group(1)), int(rng.group(2)) + 1):
                        if str(n) not in order:
                            order.append(str(n))
                elif part.strip().isdigit() and part.strip() not in order:
                    order.append(part.strip())
        elif m.group(2):
            key = norm_key(m.group(2))
            if key not in order:
                order.append(key)
    return order


# --------------------------------------------------------------------------- #
# misc: latex + io helpers
# --------------------------------------------------------------------------- #

LATEX_SPECIAL = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}"}


def latex_unescaped(text: str, char: str) -> List[int]:
    """Line numbers where `char` appears outside a command/escape and outside math-safe use."""
    hits = []
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = re.sub(r"\\.", "", line)
        stripped = re.sub(r"(?<!\\)%.*$", "", stripped)
        if char in stripped:
            hits.append(i)
    return hits


def strip_code_fence(text: str) -> str:
    m = re.search(r"```[a-zA-Z]*\n(.*?)```", text, re.S)
    return m.group(1) if m else text


def pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x*100:.1f}%"
