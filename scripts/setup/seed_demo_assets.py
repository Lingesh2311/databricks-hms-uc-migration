"""Seed synthetic demo assets for the HMS to UC user-asset migration tool.

Generates notebooks across three confidence tiers (simple / complex / very_complex)
plus a few legacy-dashboard query specs, imports the notebooks into the workspace,
and records every asset in <catalog>.<schema>.demo_asset_inventory.

Generic and parameterized by design (no customer values hardcoded) so the same
script is publishable as part of the databricks-hms-uc-migration agent skill.

Runs two ways:
  * Locally:   python seed_demo_assets.py --profile <your_profile> --catalog ... --schema ... --demo-root ...
  * As a job:  notebook task passes base_parameters; dbutils widgets are read.

The notebook BUILDER (build_demo_notebooks) is a pure function with no Databricks
dependency, so it is unit-testable offline and safe to publish.
"""
from __future__ import annotations

import argparse
import base64
import os
from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# Pure builder (no Databricks imports): unit-testable, publishable.
# --------------------------------------------------------------------------- #

CELL = "\n\n# COMMAND ----------\n\n"
HEADER = "# Databricks notebook source"


@dataclass
class DemoNotebook:
    name: str
    tier: str               # simple | complex | very_complex
    hms_ref_count: int      # references the scanner is expected to find
    source: str             # full SOURCE-format notebook text


def _nb(name: str, tier: str, hms_ref_count: int, cells: list[str]) -> DemoNotebook:
    body = CELL.join(cells)
    return DemoNotebook(name, tier, hms_ref_count, f"{HEADER}\n\n{body}\n")


def build_demo_notebooks() -> list[DemoNotebook]:
    """Return a deterministic list of synthetic notebooks across the 3 tiers."""
    nbs: list[DemoNotebook] = []

    # ---- SIMPLE: static table refs and static mount literals (score > 0.85) ----
    nbs.append(_nb("simple_01_read_table", "simple", 1, [
        "# MAGIC %md\n# MAGIC ## Daily sales load\nprint('start')",
        'df = spark.read.table("sales_db.transactions")\ndisplay(df.limit(10))',
    ]))
    nbs.append(_nb("simple_02_spark_table", "simple", 1, [
        'ledger = spark.table("finance_db.ledger")\nledger.count()',
    ]))
    nbs.append(_nb("simple_03_sql_magic", "simple", 1, [
        "# MAGIC %sql\n# MAGIC SELECT * FROM risk_db.exposures WHERE as_of_date = current_date()",
    ]))
    nbs.append(_nb("simple_04_spark_sql", "simple", 1, [
        'df = spark.sql("SELECT customer_id, name FROM sales_db.customers")\ndf.show()',
    ]))
    nbs.append(_nb("simple_05_join", "simple", 2, [
        "# MAGIC %sql\n# MAGIC SELECT o.order_id, c.name\n"
        "# MAGIC FROM sales_db.orders o JOIN sales_db.customers c ON o.customer_id = c.customer_id",
    ]))
    nbs.append(_nb("simple_06_static_mount", "simple", 1, [
        'df = spark.read.parquet("/mnt/sales-raw/2024/transactions")\ndf.printSchema()',
    ]))
    nbs.append(_nb("simple_07_gl", "simple", 1, [
        'spark.sql("SELECT account, balance FROM finance_db.gl_accounts").display()',
    ]))
    nbs.append(_nb("simple_08_campaigns", "simple", 1, [
        'c = spark.read.table("marketing_db.campaigns")\nc.groupBy("channel").count().show()',
    ]))
    nbs.append(_nb("simple_09_events", "simple", 1, [
        "# MAGIC %sql\n# MAGIC SELECT event_type, count(*) FROM ops_db.events GROUP BY event_type",
    ]))
    nbs.append(_nb("simple_10_rates", "simple", 1, [
        'rates = spark.table("reference_db.currency_rates")\nrates.show()',
    ]))
    nbs.append(_nb("simple_11_employees", "simple", 1, [
        'spark.sql("SELECT dept, count(*) FROM hr_db.employees GROUP BY dept").display()',
    ]))
    nbs.append(_nb("simple_12_var", "simple", 1, [
        'v = spark.read.table("risk_db.var_results")\nv.agg({"var_99": "max"}).show()',
    ]))

    # ---- COMPLEX: dynamic mounts / variable-built refs (score 0.45 to 0.85) ----
    nbs.append(_nb("complex_01_fstring_mount", "complex", 1, [
        'mnt = "sales-raw"\ndf = spark.read.parquet(f"/mnt/{mnt}/daily")\ndf.count()',
    ]))
    nbs.append(_nb("complex_02_concat_path", "complex", 1, [
        'base = "/mnt/finance-raw"\npath = base + "/ledger"\n'
        'df = spark.read.format("delta").load(path)',
    ]))
    nbs.append(_nb("complex_03_var_table", "complex", 1, [
        'db = "risk_db"\ndf = spark.sql(f"SELECT * FROM {db}.var_results")\ndf.show()',
    ]))
    nbs.append(_nb("complex_04_loop_mounts", "complex", 2, [
        'for sub in ["2023", "2024"]:\n'
        '    spark.read.parquet(f"/mnt/sales-raw/{sub}").createOrReplaceTempView(f"t_{sub}")',
    ]))
    nbs.append(_nb("complex_05_param_db", "complex", 1, [
        'schema_name = "finance_db"\n'
        'spark.table(f"{schema_name}.gl_accounts").write.mode("overwrite").saveAsTable("tmp_gl")',
    ]))
    nbs.append(_nb("complex_06_format_load", "complex", 1, [
        'mount = "risk-raw"\nlocation = "/mnt/%s/exposures" % mount\nspark.read.load(location)',
    ]))
    nbs.append(_nb("complex_07_dbfs_prefix", "complex", 1, [
        'p = "dbfs:/mnt/finance-raw/gl"\ndf = spark.read.parquet(p)\ndf.show()',
    ]))
    nbs.append(_nb("complex_08_widget_table", "complex", 1, [
        'tbl = dbutils.widgets.get("table")  # default "sales_db.orders"\n'
        'spark.sql(f"SELECT * FROM {tbl}").display()',
    ]))

    # ---- VERY_COMPLEX: conditional/ambiguous logic (score < 0.45) ----
    nbs.append(_nb("verycomplex_01_env_mount", "very_complex", 1, [
        'env = dbutils.widgets.get("env")\n'
        '# mount name is only known at runtime; ambiguous which namespace\n'
        'df = spark.read.parquet(f"/mnt/{env}-raw/landing")',
    ]))
    nbs.append(_nb("verycomplex_02_branching_db", "very_complex", 2, [
        'region = dbutils.widgets.get("region")\n'
        'if region == "EU":\n    db = "finance_db"\nelse:\n    db = "risk_db"\n'
        'spark.table(f"{db}.gl_accounts")',
    ]))
    nbs.append(_nb("verycomplex_03_config_driven", "very_complex", 1, [
        'cfg = {"src": "ops_db"}  # often loaded from external config\n'
        'name = cfg["src"] + ".events"\nspark.sql(f"SELECT * FROM {name}")',
    ]))
    nbs.append(_nb("verycomplex_04_helper_indirection", "very_complex", 1, [
        'def load(ns):\n    return spark.table(ns + ".transactions")\n'
        '# caller passes namespace from elsewhere\nload(get_namespace())',
    ]))

    return nbs


def build_dashboard_specs() -> list[dict]:
    """Legacy-dashboard widget query specs (HMS-referencing). Stored for the scanner."""
    return [
        {"name": "sales_overview", "tier": "simple",
         "queries": ["SELECT channel, sum(amount) FROM sales_db.transactions GROUP BY channel"]},
        {"name": "finance_ledger", "tier": "simple",
         "queries": ["SELECT account, sum(balance) FROM finance_db.gl_accounts GROUP BY account"]},
        {"name": "risk_var", "tier": "complex",
         "queries": ["SELECT as_of_date, max(var_99) FROM risk_db.var_results GROUP BY as_of_date"]},
    ]


# --------------------------------------------------------------------------- #
# Side effects (Databricks): import notebooks + write inventory.
# --------------------------------------------------------------------------- #

def _sql_escape(s: str) -> str:
    return s.replace("'", "''")


def seed(catalog: str, schema: str, demo_root: str, profile: str | None,
         warehouse_id: str | None, local_dir: str, local_only: bool) -> None:
    notebooks = build_demo_notebooks()
    dashboards = build_dashboard_specs()

    # 1) Write notebooks locally (reproducible + publishable artifact).
    for nb in notebooks:
        d = os.path.join(local_dir, nb.tier)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{nb.name}.py"), "w") as f:
            f.write(nb.source)
    print(f"Wrote {len(notebooks)} notebooks to {local_dir}")

    if local_only:
        print("local-only: skipping workspace import + inventory write")
        return

    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.workspace import ImportFormat, Language

    # Select profile via env var. Passing profile= to WorkspaceClient sets the host but
    # leaves the CLI token helper unable to disambiguate when two profiles share a host;
    # DATABRICKS_CONFIG_PROFILE drives both host and token resolution cleanly.
    if profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
    w = WorkspaceClient()

    # 2) Import notebooks into the workspace.
    w.workspace.mkdirs(demo_root)
    inventory: list[tuple] = []
    for nb in notebooks:
        path = f"{demo_root}/{nb.tier}/{nb.name}"
        w.workspace.mkdirs(f"{demo_root}/{nb.tier}")
        w.workspace.import_(
            path=path,
            content=base64.b64encode(nb.source.encode()).decode(),
            format=ImportFormat.SOURCE,
            language=Language.PYTHON,
            overwrite=True,
        )
        inventory.append(("notebook", path, nb.name, nb.tier, nb.hms_ref_count))
    print(f"Imported {len(notebooks)} notebooks under {demo_root}")

    for d in dashboards:
        inventory.append(("dashboard", f"{demo_root}/dashboards/{d['name']}",
                          d["name"], d["tier"], len(d["queries"])))

    # 3) Write demo_asset_inventory via the SQL Statement Execution API.
    fqn = f"{catalog}.{schema}.demo_asset_inventory"
    values = ",\n".join(
        f"('{a}','{_sql_escape(p)}','{_sql_escape(n)}','{t}',{c},current_timestamp())"
        for (a, p, n, t, c) in inventory
    )
    stmt = f"INSERT INTO {fqn} VALUES\n{values}"
    if not warehouse_id:
        warehouse_id = next(iter(w.warehouses.list())).id
    w.statement_execution.execute_statement(statement=stmt, warehouse_id=warehouse_id,
                                            wait_timeout="50s")
    print(f"Inserted {len(inventory)} rows into {fqn}")


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
    ap.add_argument("--local-dir", dest="local_dir",
                    default=os.path.join(os.path.dirname(__file__), "generated_notebooks"))
    ap.add_argument("--local-only", action="store_true")
    a = ap.parse_args()

    seed(
        catalog=_param(a.catalog, "catalog"),
        schema=_param(a.schema, "schema"),
        demo_root=_param(a.demo_root, "demo_root", "/Workspace/Shared/hms_uc_migration_demo/demo_notebooks"),
        profile=a.profile,
        warehouse_id=a.warehouse_id,
        local_dir=a.local_dir,
        local_only=a.local_only,
    )
