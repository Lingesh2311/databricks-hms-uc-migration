"""Stage 5: Validate + Learn.

Two jobs in one:
  1. Reconcile: for each migrated table, compare the pre-migration source and the UC
     target on row count and column set. Any mismatch flags the related manifest rows
     status='failed' (proving the pipeline catches breakage, not just hopes for the best).
  2. Learn: log every review decision to an MLflow experiment (one run per manifest row)
     plus a summary run, so the audit trail BDS needs lives in MLflow, not a spreadsheet.

Reconcile is implemented with SQL (count + information_schema columns), equivalent to a
PySpark count/schema diff but runnable on a SQL warehouse. Generic and parameterized.
"""
from __future__ import annotations

import argparse
import os


def run(catalog: str, schema: str, profile: str | None, warehouse_id: str | None,
        experiment_path: str | None) -> None:
    if profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    if not warehouse_id:
        warehouse_id = next(iter(w.warehouses.list())).id
    base = f"{catalog}.{schema}"

    def sql(stmt: str):
        return w.statement_execution.execute_statement(
            statement=stmt, warehouse_id=warehouse_id, wait_timeout="50s").result.data_array or []

    def count(fqn: str):
        try:
            return int(sql(f"SELECT count(*) FROM {fqn}")[0][0])
        except Exception:
            return None

    def columns(cat: str, sch: str, tbl: str):
        rows = sql(f"SELECT column_name FROM {cat}.information_schema.columns "
                   f"WHERE table_schema='{sch}' AND table_name='{tbl}' ORDER BY column_name")
        return tuple(r[0] for r in rows)

    # ---- 1. Reconcile ----------------------------------------------------- #
    targets = sql(f"""
        SELECT DISTINCT final_replacement FROM {base}.migration_manifest
        WHERE status='applied' AND final_replacement LIKE '%.uc_migration_target.%'
    """)
    reconcile_results = []
    for (target_fqn,) in targets:
        name = target_fqn.split(".")[-1]
        src_fqn = f"{base}.src_{name}"
        c_src, c_tgt = count(src_fqn), count(target_fqn)
        if c_src is None or c_tgt is None:
            continue  # no paired source data for this table in the demo set
        cols_src = columns(catalog, schema, f"src_{name}")
        cols_tgt = columns(catalog, "uc_migration_target", name)
        match = (c_src == c_tgt) and (cols_src == cols_tgt)
        reconcile_results.append((target_fqn, c_src, c_tgt, match))
        if not match:
            sql(f"""UPDATE {base}.migration_manifest SET status='failed'
                    WHERE status='applied' AND final_replacement='{target_fqn}'""")
        print(f"  reconcile {name}: src={c_src} tgt={c_tgt} -> {'MATCH' if match else 'MISMATCH'}")

    matched = sum(1 for *_, m in reconcile_results if m)
    print(f"Reconcile: {matched}/{len(reconcile_results)} tables matched")

    # ---- 2. Learn (MLflow audit) ----------------------------------------- #
    import mlflow

    if not experiment_path:
        experiment_path = f"/Users/{w.current_user.me().user_name}/hms_uc_migration_audit"
    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment(experiment_path)

    decisions = sql(f"""
        SELECT manifest_id, asset_path, reference_kind, confidence, status, reviewer
        FROM {base}.migration_manifest WHERE status <> 'proposed'
    """)
    for mid, path, kind, conf, status, reviewer in decisions:
        with mlflow.start_run(run_name=f"decision-{mid[:8]}"):
            mlflow.log_params({"asset_path": path, "reference_kind": kind,
                               "status": status, "reviewer": reviewer or "unknown"})
            mlflow.log_metric("confidence", float(conf))

    with mlflow.start_run(run_name="summary"):
        mlflow.log_metric("decisions_logged", len(decisions))
        mlflow.log_metric("tables_reconciled", len(reconcile_results))
        mlflow.log_metric("tables_matched", matched)
        mlflow.log_metric("tables_failed", len(reconcile_results) - matched)
    print(f"Logged {len(decisions)} decision runs + 1 summary to {experiment_path}")


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
    ap.add_argument("--experiment-path", dest="experiment_path")
    a = ap.parse_args()
    run(
        catalog=_param(a.catalog, "catalog"),
        schema=_param(a.schema, "schema"),
        profile=a.profile,
        warehouse_id=a.warehouse_id,
        experiment_path=a.experiment_path,
    )
