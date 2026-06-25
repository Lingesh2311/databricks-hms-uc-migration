"""Stage 4: Apply.

For every manifest row that a reviewer approved or overrode, this job:
  1. backs up the original notebook source to a UC Volume (reversible),
  2. patches the source by replacing each original_reference with its final_replacement,
  3. re-imports the patched notebook via the Workspace API (overwrite),
  4. stamps the manifest rows status='applied', applied_at, backup_path.

Notebooks are patched once each (all of a notebook's approved references at once).
Generic and parameterized: publishable as part of the agent skill.

NOTE: Lakeview/legacy dashboard patching is a planned extension; this job currently
applies notebook references only (the manifest holds no dashboard rows yet).
"""
from __future__ import annotations

import argparse
import base64
import io
import os
from collections import defaultdict
from datetime import datetime, timezone


def _sql(s: str) -> str:
    return s.replace("'", "''")


def run(catalog: str, schema: str, profile: str | None, warehouse_id: str | None) -> None:
    if profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.workspace import ExportFormat, ImportFormat, Language

    w = WorkspaceClient()
    if not warehouse_id:
        warehouse_id = next(iter(w.warehouses.list())).id
    base = f"{catalog}.{schema}"
    vol_dir = f"/Volumes/{catalog}/{schema}/backups"

    def sql(stmt: str):
        return w.statement_execution.execute_statement(
            statement=stmt, warehouse_id=warehouse_id, wait_timeout="50s")

    # Ensure the backup volume exists.
    sql(f"CREATE VOLUME IF NOT EXISTS {base}.backups")

    rows = sql(f"""
        SELECT manifest_id, asset_path, original_reference, final_replacement
        FROM {base}.migration_manifest
        WHERE asset_type='notebook' AND status IN ('approved','overridden')
              AND final_replacement IS NOT NULL
    """).result.data_array or []

    by_notebook: dict[str, list] = defaultdict(list)
    for mid, path, orig, final in rows:
        by_notebook[path].append((mid, orig, final))
    print(f"Applying {len(rows)} references across {len(by_notebook)} notebooks")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    applied = 0
    for path, edits in by_notebook.items():
        exported = w.workspace.export(path=path, format=ExportFormat.SOURCE)
        source = base64.b64decode(exported.content).decode()

        # 1) back up original to the volume
        safe = path.strip("/").replace("/", "__")
        backup_path = f"{vol_dir}/{safe}__{stamp}.py"
        w.files.upload(backup_path, io.BytesIO(source.encode()), overwrite=True)

        # 2) patch (literal replace, longest original first to avoid partial overlaps)
        patched = source
        for _mid, orig, final in sorted(edits, key=lambda e: -len(e[1])):
            patched = patched.replace(orig, final)

        # 3) re-import (overwrite)
        w.workspace.import_(path=path, content=base64.b64encode(patched.encode()).decode(),
                            format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=True)

        # 4) stamp manifest
        ids = ",".join(f"'{mid}'" for mid, _, _ in edits)
        sql(f"""
            UPDATE {base}.migration_manifest
            SET status='applied', applied_at=current_timestamp(),
                backup_path='{_sql(backup_path)}'
            WHERE manifest_id IN ({ids})
        """)
        applied += len(edits)
        print(f"  patched {path} ({len(edits)} refs); backup {backup_path}")

    print(f"Applied {applied} references; backups under {vol_dir}")


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
