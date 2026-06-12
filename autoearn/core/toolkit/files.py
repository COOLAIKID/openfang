"""File toolkit — safe reads/writes confined to the project ``output/`` tree.

All paths are resolved relative to ``output/`` and validated to prevent traversal
outside it, so agents can persist and revisit their work without touching the
rest of the machine. Supports text, JSON, CSV append, and listing.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUT = ROOT / "output"


class PathError(ValueError):
    pass


def _resolve(rel: str) -> Path:
    OUTPUT.mkdir(exist_ok=True)
    target = (OUTPUT / rel).resolve()
    if not str(target).startswith(str(OUTPUT.resolve())):
        raise PathError(f"path '{rel}' escapes the output directory")
    return target


def save_text(rel_path: str, content: str) -> str:
    target = _resolve(rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Saved {len(content)} chars to output/{rel_path}"


def read_text(rel_path: str, max_chars: int = 8000) -> str:
    target = _resolve(rel_path)
    if not target.exists():
        return f"ERROR: output/{rel_path} does not exist."
    return target.read_text(encoding="utf-8", errors="ignore")[:max_chars]


def save_json(rel_path: str, obj) -> str:
    target = _resolve(rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    return f"Saved JSON to output/{rel_path}"


def append_csv_row(rel_path: str, row: list) -> str:
    target = _resolve(rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    csv.writer(buf).writerow(row)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(buf.getvalue())
    return f"Appended row to output/{rel_path}"


def list_output(subdir: str = "") -> str:
    base = _resolve(subdir) if subdir else OUTPUT
    if not base.exists():
        return "(empty)"
    files = [str(p.relative_to(OUTPUT)) for p in base.rglob("*") if p.is_file()]
    return "\n".join(sorted(files)[:200]) if files else "(empty)"
