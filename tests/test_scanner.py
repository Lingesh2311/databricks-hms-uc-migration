"""Scanner rule tests (TDD, written before implementation).

The scanner is pure Python (sqlglot + ast). Mapping and mount registry are passed
in as plain data structures (dependency injection) so these tests need no Databricks.
"""
from scanner import core


# --- fixtures: the migration "answer key" and mount registry, as plain data ---

MAPPING = core.Mapping(
    tables={
        ("sales_db", "transactions"): "main.uc_migration_target.transactions",
        ("sales_db", "customers"): "main.uc_migration_target.customers",
        ("finance_db", "ledger"): "main.uc_migration_target.ledger",
        ("risk_db", "exposures"): "main.uc_migration_target.exposures",
    },
    namespaces={
        "sales_db": ("main", "uc_migration_target"),
        "finance_db": ("main", "uc_migration_target"),
        "risk_db": ("main", "uc_migration_target"),
    },
)

MOUNTS = {
    "/mnt/sales-raw": "sales_db",
    "/mnt/finance-raw": "finance_db",
    "/mnt/risk-raw": "risk_db",
}

NB = "# Databricks notebook source"
SEP = "\n\n# COMMAND ----------\n\n"


# --------------------------- split + classify cells --------------------------- #

def test_split_cells_separates_on_command_delimiter():
    src = NB + SEP + "print(1)" + SEP + "print(2)"
    cells = core.split_cells(src)
    assert len(cells) == 2
    assert cells[0].index == 0 and cells[1].index == 1


def test_sql_magic_cell_classified_as_sql():
    src = NB + SEP + "# MAGIC %sql\n# MAGIC SELECT 1"
    cells = core.split_cells(src)
    assert cells[0].language == "sql"
    assert "SELECT 1" in cells[0].code


def test_plain_python_cell_classified_as_python():
    src = NB + SEP + "x = spark.table('a.b')"
    cells = core.split_cells(src)
    assert cells[0].language == "python"


# --------------------------- reference extraction --------------------------- #

def test_extract_static_sql_table_ref():
    src = NB + SEP + "# MAGIC %sql\n# MAGIC SELECT * FROM risk_db.exposures"
    refs = core.extract_references(src)
    kinds = {r.reference_kind for r in refs}
    assert "sql_table_ref" in kinds
    r = next(r for r in refs if r.reference_kind == "sql_table_ref")
    assert r.namespace == "risk_db" and r.table == "exposures"


def test_extract_spark_read_table():
    src = NB + SEP + 'df = spark.read.table("sales_db.transactions")'
    refs = core.extract_references(src)
    r = next(r for r in refs if r.reference_kind == "py_table_ref")
    assert r.namespace == "sales_db" and r.table == "transactions"


def test_extract_spark_sql_string_literal():
    src = NB + SEP + 'spark.sql("SELECT customer_id FROM sales_db.customers")'
    refs = core.extract_references(src)
    r = next(r for r in refs if r.reference_kind == "py_table_ref")
    assert r.namespace == "sales_db" and r.table == "customers"


def test_extract_static_mount_literal():
    src = NB + SEP + 'df = spark.read.parquet("/mnt/sales-raw/2024/transactions")'
    refs = core.extract_references(src)
    r = next(r for r in refs if r.reference_kind == "mount_path_literal")
    assert r.original_reference.startswith("/mnt/sales-raw")


def test_extract_dynamic_mount_fstring():
    src = NB + SEP + 'mnt = "sales-raw"\ndf = spark.read.parquet(f"/mnt/{mnt}/daily")'
    refs = core.extract_references(src)
    kinds = {r.reference_kind for r in refs}
    assert "dynamic_mount" in kinds


def test_extract_dynamic_table_fstring_in_spark_table():
    src = NB + SEP + 'db = "risk_db"\nspark.table(f"{db}.gl_accounts")'
    refs = core.extract_references(src)
    assert any(r.reference_kind == "dynamic_table" for r in refs)


def test_extract_dynamic_table_fstring_in_spark_sql():
    src = NB + SEP + 'tbl = "sales_db.orders"\nspark.sql(f"SELECT * FROM {tbl}")'
    refs = core.extract_references(src)
    assert any(r.reference_kind == "dynamic_table" for r in refs)


def test_extract_dynamic_table_string_concat():
    src = NB + SEP + 'ns = get_namespace()\nspark.table(ns + ".transactions")'
    refs = core.extract_references(src)
    assert any(r.reference_kind == "dynamic_table" for r in refs)


def test_propose_dynamic_table_is_low_confidence():
    ref = core.Reference(0, "dynamic_table", 'f"{db}.gl_accounts"', None, None)
    p = core.propose(ref, MAPPING, MOUNTS)
    assert p.confidence < 0.45 and p.tier == "very_complex"
    assert p.proposed_replacement is None


def test_clean_cell_yields_no_references():
    src = NB + SEP + "x = 1 + 1\nprint(x)"
    refs = core.extract_references(src)
    assert refs == []


# --------------------------- propose + score --------------------------- #

def test_propose_static_table_ref_is_high_confidence():
    ref = core.Reference(0, "sql_table_ref", "risk_db.exposures", "risk_db", "exposures")
    p = core.propose(ref, MAPPING, MOUNTS)
    assert p.proposed_replacement == "main.uc_migration_target.exposures"
    assert p.confidence >= 0.85 and p.tier == "simple"


def test_propose_static_mount_literal_is_high_confidence():
    ref = core.Reference(0, "mount_path_literal", "/mnt/sales-raw/2024", None, None)
    p = core.propose(ref, MAPPING, MOUNTS)
    assert p.confidence >= 0.85 and p.tier == "simple"
    # Mount literals map to a UC Volumes path so Apply can substitute cleanly,
    # preserving the sub-path and sanitizing the mount name (sales-raw -> sales_raw).
    assert p.proposed_replacement == "/Volumes/main/uc_migration_target/sales_raw/2024"


def test_propose_dynamic_mount_single_candidate_is_medium():
    ref = core.Reference(0, "dynamic_mount", 'f"/mnt/{mnt}/daily"', None, None)
    p = core.propose(ref, MAPPING, MOUNTS)
    assert 0.45 <= p.confidence < 0.85 and p.tier == "complex"


def test_propose_unknown_namespace_is_low_confidence():
    ref = core.Reference(0, "sql_table_ref", "mystery_db.foo", "mystery_db", "foo")
    p = core.propose(ref, MAPPING, MOUNTS)
    assert p.confidence < 0.45 and p.tier == "very_complex"
    assert p.proposed_replacement is None


# --------------------------- end-to-end scan --------------------------- #

def test_scan_source_returns_rows_for_all_refs():
    src = (NB + SEP + 'spark.read.table("sales_db.transactions")'
           + SEP + "# MAGIC %sql\n# MAGIC SELECT * FROM finance_db.ledger")
    rows = core.scan_source(src, MAPPING, MOUNTS)
    assert len(rows) == 2
    assert all(row.confidence > 0 for row in rows)
    assert {row.namespace for row in rows} == {"sales_db", "finance_db"}
