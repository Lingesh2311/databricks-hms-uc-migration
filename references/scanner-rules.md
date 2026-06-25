# Scanner rules

The scanner (`scripts/scanner/core.py`) is pure Python: `sqlglot` for SQL, the stdlib `ast`
module for Python. It takes notebook source plus the mapping and mount registry (injected as
plain data) and returns one row per HMS reference. No Databricks dependency, so it is unit
tested offline (`tests/test_scanner.py`).

## Cell splitting

`split_cells(source)` splits SOURCE-format notebooks on the `# COMMAND ----------` delimiter
and classifies each cell as `sql` (a `%sql` magic, including the `# MAGIC %sql` form) or
`python`. SQL magic prefixes (`# MAGIC `) are stripped to recover runnable SQL.

## SQL cells

Parsed with `sqlglot.parse(text, dialect="databricks")`. The scanner walks for `exp.Table`
nodes and captures any table that has a database part (`db.name`). Emits `sql_table_ref`
with `namespace = db`, `table = name`.

## Python cells

Parsed with `ast`. The scanner detects:

| Pattern | reference_kind |
|---------|----------------|
| `spark.table("db.t")`, `spark.read.table("db.t")` (constant) | `py_table_ref` |
| `spark.sql("... FROM db.t ...")` (constant string, re-parsed via sqlglot) | `py_table_ref` |
| String literal matching `/mnt/<name>/...` or `dbfs:/mnt/...` | `mount_path_literal` |
| f-string building a `/mnt/...` path | `dynamic_mount` |
| `spark.table(...)` / `spark.sql(...)` with a non-constant arg (f-string, concat, variable) | `dynamic_table` |

`dynamic_mount` and `dynamic_table` cannot be resolved statically, so they are flagged for
human review rather than skipped. This is deliberate: silent skips are false negatives that
erode trust in a migration tool.

## Resolution

`mount_path_literal` and `dynamic_mount` are cross-referenced against `mount_point_registry`
(mount path -> namespace) and then `hms_to_uc_mapping` (namespace -> UC target). Table refs
look up `(namespace, table)` directly in the mapping.

## Extending

To support a new pattern, add a failing test in `tests/test_scanner.py` first (the module is
TDD-developed), then extend `_extract_python` or the SQL walk. Keep the scanner pure (no
Databricks imports) so it stays offline-testable.
