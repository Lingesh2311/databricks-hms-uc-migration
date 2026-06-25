"""Stage 2: Scan + Propose.

Loads the seeded mapping and mount registry, exports every inventoried notebook's
source via the Workspace API, runs the pure scanner over it, and writes one
migration_manifest row per HMS reference found (status = 'proposed').

Generic and parameterized: publishable as part of the agent skill. The scanning
logic lives in src/scanner (pure, unit-tested); this job is the Databricks glue.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys

# Make the pure scanner package importable both locally and as a job task.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from scanner import core  # noqa: E402


def _sql(s: str) -> str:
    return s.replace("'", "''")


def _lit(v):
    return "NULL" if v is None else f"'{_sql(str(v))}'"


def load_mapping(sql, base: str) -> core.Mapping:
    rows = sql(f"SELECT hms_namespace, hms_table, uc_catalog, uc_schema, uc_table "
               f"FROM {base}.hms_to_uc_mapping").result.data_array or []
    mapping = core.Mapping()
    for ns, tbl, cat, sch, uctbl in rows:
        if ns and sch:
            mapping.namespaces[ns] = (cat, sch)
        if ns and tbl and uctbl:
            mapping.tables[(ns, tbl)] = f"{cat}.{sch}.{uctbl}"
    return mapping


def load_mounts(sql, base: str) -> dict:
    rows = sql(f"SELECT mount_path, hms_namespace FROM {base}.mount_point_registry").result.data_array or []
    return {mp: ns for mp, ns in rows}


def run(catalog: str, schema: str, profile: str | None, warehouse_id: str | None) -> None:
    if profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.workspace import ExportFormat

    w = WorkspaceClient()
    if not warehouse_id:
        warehouse_id = next(iter(w.warehouses.list())).id

    base = f"{catalog}.{schema}"

    def sql(stmt: str):
        return w.statement_execution.execute_statement(
            statement=stmt, warehouse_id=warehouse_id, wait_timeout="50s")

    mapping = load_mapping(sql, base)
    mounts = load_mounts(sql, base)
    print(f"Loaded mapping: {len(mapping.tables)} tables, {len(mapping.namespaces)} namespaces; "
          f"{len(mounts)} mounts")

    notebooks = [row[0] for row in (
        sql(f"SELECT path FROM {base}.demo_asset_inventory WHERE asset_type='notebook'")
        .result.data_array or [])]
    print(f"Scanning {len(notebooks)} notebooks")

    manifest_rows = []
    for path in notebooks:
        exported = w.workspace.export(path=path, format=ExportFormat.SOURCE)
        source = base64.b64decode(exported.content).decode()
        for r in core.scan_source(source, mapping, mounts):
            mid = hashlib.md5(f"{path}:{r.cell_index}:{r.original_reference}".encode()).hexdigest()
            manifest_rows.append((
                mid, "notebook", path, r.cell_index, r.original_reference,
                r.reference_kind, r.proposed_replacement, r.confidence,
            ))

    print(f"Found {len(manifest_rows)} references")
    sql(f"TRUNCATE TABLE {base}.migration_manifest")
    values = ",\n".join(
        f"({_lit(mid)},{_lit(at)},{_lit(ap)},{ci},{_lit(orig)},{_lit(kind)},"
        f"{_lit(prop)},{conf},'proposed',NULL,NULL,NULL,NULL,NULL,current_timestamp())"
        for (mid, at, ap, ci, orig, kind, prop, conf) in manifest_rows
    )
    sql(f"INSERT INTO {base}.migration_manifest VALUES\n{values}")
    print(f"Wrote {len(manifest_rows)} rows to migration_manifest")


def _param(args_val, widget_name, default=None):
    if args_val is not None:
        return args_val
    try:
        return dbutils.widgets.get(widget_name)  # type: ignore  # noqa: F821
    except Exception:
        return default


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog")
    ap.add_argument("--schema")
    ap.add_argument("--profile")
    ap.add_argument("--warehouse-id", dest="warehouse_id")
    a = ap.parse_args()
    run(
        catalog=_param(a.catalog, "catalog"),
        schema=_param(a.schema, "schema"),
        profile=a.profile,
        warehouse_id=a.warehouse_id,
    )
