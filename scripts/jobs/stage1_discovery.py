"""Stage 1: Discovery.

Enumerates the workspace assets in scope and records the mount points that
Stage 2 will need to resolve dynamic mount references.

Outputs:
  * <catalog>.<schema>.mount_point_registry   (mount_path, adls_target, hms_namespace, discovered_at)
  * confirms notebook count under demo_root (upserts demo_asset_inventory if empty)

Mounts: in a real workspace this reads `dbutils.fs.mounts()`. On serverless or when
run locally that call is unavailable, so a deterministic synthetic mount set (matching
the seeded demo notebooks) is used instead. The fallback is logged, never silent.

Generic and parameterized: publishable as part of the agent skill.
"""
from __future__ import annotations

import argparse
import os


# Synthetic mount set representing what dbutils.fs.mounts() would return in a
# real HMS-era workspace. Generic, non-PII; aligns with the seeded notebooks.
SYNTHETIC_MOUNTS = [
    ("/mnt/sales-raw", "abfss://sales-raw@STORAGE_ACCOUNT.dfs.core.windows.net/", "sales_db"),
    ("/mnt/finance-raw", "abfss://finance-raw@STORAGE_ACCOUNT.dfs.core.windows.net/", "finance_db"),
    ("/mnt/risk-raw", "abfss://risk-raw@STORAGE_ACCOUNT.dfs.core.windows.net/", "risk_db"),
    ("/mnt/landing", "abfss://landing@STORAGE_ACCOUNT.dfs.core.windows.net/", "ops_db"),
]


def discover_mounts() -> tuple[list[tuple[str, str, str]], str]:
    """Return (mounts, source). Tries dbutils; falls back to the synthetic set."""
    try:
        mounts = dbutils.fs.mounts()  # type: ignore  # noqa: F821
        rows = [(m.mountPoint, m.source, "") for m in mounts
                if m.mountPoint.startswith("/mnt/")]
        if rows:
            return rows, "dbutils.fs.mounts()"
    except Exception:
        pass
    return SYNTHETIC_MOUNTS, "synthetic (dbutils.fs.mounts unavailable)"


def _sql_escape(s: str) -> str:
    return s.replace("'", "''")


def run(catalog: str, schema: str, demo_root: str, profile: str | None,
        warehouse_id: str | None) -> None:
    if profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    if not warehouse_id:
        warehouse_id = next(iter(w.warehouses.list())).id

    def sql(stmt: str):
        return w.statement_execution.execute_statement(
            statement=stmt, warehouse_id=warehouse_id, wait_timeout="50s")

    # 1) Enumerate notebooks under demo_root (recursive).
    def list_notebooks(path: str) -> list[str]:
        out: list[str] = []
        for obj in w.workspace.list(path):
            if str(obj.object_type).endswith("NOTEBOOK") or obj.object_type and obj.object_type.value == "NOTEBOOK":
                out.append(obj.path)
            elif obj.object_type and obj.object_type.value == "DIRECTORY":
                out.extend(list_notebooks(obj.path))
        return out

    notebooks = list_notebooks(demo_root)
    print(f"Discovered {len(notebooks)} notebooks under {demo_root}")

    # 2) Discover mounts and (over)write the registry.
    mounts, source = discover_mounts()
    print(f"Discovered {len(mounts)} mounts via {source}")
    sql(f"TRUNCATE TABLE {catalog}.{schema}.mount_point_registry")
    values = ",\n".join(
        f"('{_sql_escape(mp)}','{_sql_escape(tgt)}','{_sql_escape(ns)}',current_timestamp())"
        for (mp, tgt, ns) in mounts
    )
    sql(f"INSERT INTO {catalog}.{schema}.mount_point_registry VALUES\n{values}")
    print(f"Wrote {len(mounts)} rows to mount_point_registry")

    # 3) If inventory is empty (job-only run without the seed step), backfill notebook rows.
    inv = f"{catalog}.{schema}.demo_asset_inventory"
    cnt = list(sql(f"SELECT count(*) AS c FROM {inv}").result.data_array or [["0"]])[0][0]
    if str(cnt) == "0" and notebooks:
        vals = ",\n".join(
            f"('notebook','{_sql_escape(p)}','{_sql_escape(p.split('/')[-1])}','unknown',NULL,current_timestamp())"
            for p in notebooks
        )
        sql(f"INSERT INTO {inv} VALUES\n{vals}")
        print(f"Backfilled {len(notebooks)} notebook rows into {inv}")
    else:
        print(f"demo_asset_inventory already populated ({cnt} rows); no backfill")


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
    ap.add_argument("--demo-root", dest="demo_root")
    ap.add_argument("--profile")
    ap.add_argument("--warehouse-id", dest="warehouse_id")
    a = ap.parse_args()
    run(
        catalog=_param(a.catalog, "catalog"),
        schema=_param(a.schema, "schema"),
        demo_root=_param(a.demo_root, "demo_root", "/Workspace/Shared/hms_uc_migration_demo/demo_notebooks"),
        profile=a.profile,
        warehouse_id=a.warehouse_id,
    )
