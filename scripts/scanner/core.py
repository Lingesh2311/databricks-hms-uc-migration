"""Core scanner: extract HMS references from notebook source and propose UC targets.

Pure Python (sqlglot + stdlib ast). Mapping and mount registry are injected as plain
data, so this module is unit-testable offline and publishable in the agent skill.

Confidence model (deterministic, explainable: see docs/DESIGN.md):
  static table ref with exact mapping hit ......... 0.95  simple
  static mount-path literal resolvable ............ 0.90  simple
  dynamic mount (registry non-empty) .............. 0.70  complex
  no mapping hit / unknown namespace .............. 0.20  very_complex
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

MOUNT_RE = re.compile(r"^(?:dbfs:)?(/mnt/[^/\"']+)")
_MAGIC = re.compile(r"^#\s*MAGIC\s?", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class Cell:
    index: int
    language: str   # "sql" | "python"
    code: str


@dataclass
class Reference:
    cell_index: int
    reference_kind: str   # sql_table_ref | py_table_ref | mount_path_literal | dynamic_mount
    original_reference: str
    namespace: str | None = None
    table: str | None = None


@dataclass
class Proposal:
    proposed_replacement: str | None
    confidence: float
    tier: str


@dataclass
class ScanRow:
    cell_index: int
    reference_kind: str
    original_reference: str
    namespace: str | None
    table: str | None
    proposed_replacement: str | None
    confidence: float
    tier: str


@dataclass
class Mapping:
    tables: dict = field(default_factory=dict)       # (namespace, table) -> "cat.sch.tbl"
    namespaces: dict = field(default_factory=dict)   # namespace -> (catalog, schema)


# --------------------------------------------------------------------------- #
# Cell splitting + classification
# --------------------------------------------------------------------------- #

def split_cells(source: str) -> list[Cell]:
    raw = source
    if raw.lstrip().startswith("# Databricks notebook source"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
    chunks = re.split(r"\n#\s*COMMAND\s*-+\n", raw)
    cells: list[Cell] = []
    for i, chunk in enumerate(chunks):
        text = chunk.strip("\n")
        if not text.strip():
            continue
        lines = text.splitlines()
        is_sql = any("%sql" in ln for ln in lines[:2])
        if is_sql:
            code_lines = []
            for ln in lines:
                stripped = _MAGIC.sub("", ln)
                if stripped.strip().lower().startswith("%sql"):
                    continue
                code_lines.append(stripped)
            cells.append(Cell(len(cells), "sql", "\n".join(code_lines).strip()))
        else:
            cells.append(Cell(len(cells), "python", text))
    return cells


# --------------------------------------------------------------------------- #
# Reference extraction
# --------------------------------------------------------------------------- #

def _tables_from_sql(sql_text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        trees = sqlglot.parse(sql_text, dialect="databricks")
    except Exception:
        return out
    for tree in trees:
        if tree is None:
            continue
        for tbl in tree.find_all(exp.Table):
            db = tbl.db
            name = tbl.name
            if db and name:
                out.append((db, name))
    return out


def _is_mount(s: str) -> bool:
    return bool(MOUNT_RE.match(s))


def _extract_python(code: str, cell_index: int) -> list[Reference]:
    refs: list[Reference] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return refs

    fstring_const_ids: set[int] = set()

    for node in ast.walk(tree):
        # f-strings building a mount path -> dynamic_mount
        if isinstance(node, ast.JoinedStr):
            literal = "".join(
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            for v in node.values:
                if isinstance(v, ast.Constant):
                    fstring_const_ids.add(id(v))
            if "/mnt/" in literal:
                refs.append(Reference(cell_index, "dynamic_mount",
                                      literal or "f-string mount", None, None))

        # call-based table refs: spark.table(...), spark.read.table(...), spark.sql(...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in ("table", "sql") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if attr == "table" and "." in arg.value and not _is_mount(arg.value):
                        ns, _, tb = arg.value.partition(".")
                        refs.append(Reference(cell_index, "py_table_ref", arg.value, ns, tb))
                    elif attr == "sql":
                        for ns, tb in _tables_from_sql(arg.value):
                            refs.append(Reference(cell_index, "py_table_ref",
                                                  f"{ns}.{tb}", ns, tb))
                else:
                    # Non-constant table identifier or query (f-string, concat, variable):
                    # cannot resolve statically, so flag for human review.
                    try:
                        label = ast.unparse(arg)
                    except Exception:
                        label = f"spark.{attr}(dynamic)"
                    refs.append(Reference(cell_index, "dynamic_table", label, None, None))

    # standalone mount-path string literals
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in fstring_const_ids:
            if _is_mount(node.value):
                refs.append(Reference(cell_index, "mount_path_literal", node.value, None, None))
    return refs


def extract_references(source: str) -> list[Reference]:
    refs: list[Reference] = []
    for cell in split_cells(source):
        if cell.language == "sql":
            for ns, tb in _tables_from_sql(cell.code):
                refs.append(Reference(cell.index, "sql_table_ref", f"{ns}.{tb}", ns, tb))
        else:
            refs.extend(_extract_python(cell.code, cell.index))
    return refs


# --------------------------------------------------------------------------- #
# Proposal + scoring
# --------------------------------------------------------------------------- #

def _tier(conf: float) -> str:
    if conf >= 0.85:
        return "simple"
    if conf >= 0.45:
        return "complex"
    return "very_complex"


def _mount_prefix(path: str) -> str | None:
    m = MOUNT_RE.match(path)
    return m.group(1) if m else None


def propose(ref: Reference, mapping: Mapping, mounts: dict) -> Proposal:
    kind = ref.reference_kind

    if kind in ("sql_table_ref", "py_table_ref"):
        key = (ref.namespace, ref.table)
        if key in mapping.tables:
            return Proposal(mapping.tables[key], 0.95, _tier(0.95))
        if ref.namespace in mapping.namespaces:
            cat, sch = mapping.namespaces[ref.namespace]
            return Proposal(f"{cat}.{sch}.{ref.table}", 0.60, _tier(0.60))
        return Proposal(None, 0.20, _tier(0.20))

    if kind == "mount_path_literal":
        prefix = _mount_prefix(ref.original_reference)
        ns = mounts.get(prefix) if prefix else None
        if ns and ns in mapping.namespaces:
            cat, sch = mapping.namespaces[ns]
            name = prefix.split("/")[-1].replace("-", "_")  # sales-raw -> sales_raw
            rest = ref.original_reference[len(prefix):]      # preserve sub-path
            return Proposal(f"/Volumes/{cat}/{sch}/{name}{rest}", 0.90, _tier(0.90))
        return Proposal(None, 0.20, _tier(0.20))

    if kind == "dynamic_mount":
        if mounts:
            return Proposal(None, 0.70, _tier(0.70))
        return Proposal(None, 0.20, _tier(0.20))

    if kind == "dynamic_table":
        return Proposal(None, 0.30, _tier(0.30))

    return Proposal(None, 0.20, _tier(0.20))


def scan_source(source: str, mapping: Mapping, mounts: dict) -> list[ScanRow]:
    rows: list[ScanRow] = []
    for ref in extract_references(source):
        p = propose(ref, mapping, mounts)
        rows.append(ScanRow(
            ref.cell_index, ref.reference_kind, ref.original_reference,
            ref.namespace, ref.table, p.proposed_replacement, p.confidence, p.tier,
        ))
    return rows
