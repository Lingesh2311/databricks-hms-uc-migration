# Validate + Learn (Stage 5)

`scripts/jobs/stage5_validate.py` does two things: reconcile the migrated tables and log the
review decisions to MLflow.

## Reconcile

For each distinct `final_replacement` UC table referenced by an `applied` manifest row, the
job compares the pre-migration source against the UC target on:

- **row count** (`SELECT count(*)`), and
- **column set** (`information_schema.columns`).

A mismatch flags the related manifest rows `status='failed'`, so the pipeline proves nothing
broke and catches it when something did. Implemented in SQL (warehouse-runnable); equivalent
to a PySpark count/schema diff if you prefer to run it on a cluster.

```sql
-- counts
SELECT count(*) FROM <src>;
SELECT count(*) FROM <target>;
-- columns
SELECT column_name FROM <catalog>.information_schema.columns
WHERE table_schema='<schema>' AND table_name='<table>' ORDER BY column_name;
```

Pairing convention in the demo: a target `....uc_migration_target.<t>` reconciles against a
source `<catalog>.<schema>.src_<t>`. Adapt the pairing to your real source locations.

## MLflow decision log

Every reviewed manifest row (status not `proposed`) becomes one MLflow run; a final summary
run records the totals.

```python
import mlflow
mlflow.set_tracking_uri("databricks")
mlflow.set_experiment(f"/Users/{w.current_user.me().user_name}/hms_uc_migration_audit")

for row in reviewed_rows:
    with mlflow.start_run(run_name=f"decision-{row.manifest_id[:8]}"):
        mlflow.log_params({"asset_path": row.asset_path, "reference_kind": row.reference_kind,
                           "status": row.status, "reviewer": row.reviewer})
        mlflow.log_metric("confidence", float(row.confidence))

with mlflow.start_run(run_name="summary"):
    mlflow.log_metric("decisions_logged", n_decisions)
    mlflow.log_metric("tables_matched", n_matched)
    mlflow.log_metric("tables_failed", n_failed)
```

The experiment is the audit trail: who approved what, at what confidence, when, and whether
it reconciled. That is what a governance/risk team signs off on.
