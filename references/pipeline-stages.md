# Pipeline stages and control tables

All control tables live in one UC schema (`<catalog>.<schema>`). Create them once.

## Control-table DDL

```sql
CREATE SCHEMA IF NOT EXISTS <catalog>.<schema>;

-- The migration "answer key": HMS namespace/table -> UC target. In production this is
-- populated from UCX table-mapping output; for a demo, seed it directly.
CREATE TABLE IF NOT EXISTS <catalog>.<schema>.hms_to_uc_mapping (
  hms_namespace STRING, hms_table STRING,
  uc_catalog STRING, uc_schema STRING, uc_table STRING,
  mount_path STRING, notes STRING) USING DELTA;

-- One row per mount point (Stage 1 writes this).
CREATE TABLE IF NOT EXISTS <catalog>.<schema>.mount_point_registry (
  mount_path STRING, adls_target STRING, hms_namespace STRING,
  discovered_at TIMESTAMP) USING DELTA;

-- Metadata for each asset in scope.
CREATE TABLE IF NOT EXISTS <catalog>.<schema>.demo_asset_inventory (
  asset_type STRING, path STRING, name STRING, tier STRING,
  hms_ref_count INT, seeded_at TIMESTAMP) USING DELTA;

-- One row per HMS reference found (the heart of the pipeline).
CREATE TABLE IF NOT EXISTS <catalog>.<schema>.migration_manifest (
  manifest_id STRING, asset_type STRING, asset_path STRING, cell_index INT,
  original_reference STRING, reference_kind STRING, proposed_replacement STRING,
  confidence DOUBLE, status STRING, final_replacement STRING, reviewer STRING,
  reviewed_at TIMESTAMP, applied_at TIMESTAMP, backup_path STRING,
  scanned_at TIMESTAMP) USING DELTA;
```

`status`: `proposed` -> `approved` / `overridden` / `skipped` -> `applied` / `failed`.
`reference_kind`: `sql_table_ref`, `py_table_ref`, `mount_path_literal`, `dynamic_mount`,
`dynamic_table`, `dashboard_query`.

## Stage entry/exit

| Stage | Entry | Exit |
|-------|-------|------|
| 1 Discovery | Control tables exist | `mount_point_registry` populated; assets enumerated |
| 2 Scan + Propose | Stage 1 done; mapping seeded | `migration_manifest` has one `proposed` row per reference, all tiers represented |
| 3 Human Review | Stage 2 done | Reviewed rows have `status` and `final_replacement` set |
| 4 Apply | Approved/overridden rows exist | Assets patched + re-imported; `applied_at`, `backup_path` set |
| 5 Validate | Stage 4 done | Reconcile run; mismatches `failed`; MLflow runs logged |

Run order: 1 -> 2 -> (3, human) -> 4 -> 5. Stages 1, 2, 4, 5 are serverless jobs; Stage 3
is interactive notebooks plus a Genie Code skill, run by a human reviewer.
