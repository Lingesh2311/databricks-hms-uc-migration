# Databricks notebook source
# MAGIC %md
# MAGIC # Stage 3 Review: Simple path (high confidence)
# MAGIC
# MAGIC High-confidence proposals (confidence >= 0.85) are static references with an exact
# MAGIC mapping hit. They are reviewable in bulk: eyeball the table, then run one UPDATE to
# MAGIC approve the whole batch. This is the "90% auto-approvable" moment of the demo.

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "demo_hms_uc_migration")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
manifest = f"{catalog}.{schema}.migration_manifest"
print("Reviewing:", manifest)

# COMMAND ----------

# MAGIC %md ## 1. Inspect the high-confidence proposals

# COMMAND ----------

display(spark.sql(f"""
  SELECT asset_path, cell_index, original_reference, proposed_replacement, confidence
  FROM {manifest}
  WHERE confidence >= 0.85 AND status = 'proposed'
  ORDER BY asset_path, cell_index
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Batch-approve
# MAGIC Accept each proposal as the `final_replacement`, stamp the reviewer and time.
# MAGIC Only rows still `proposed` are touched, so this cell is safe to re-run.

# COMMAND ----------

spark.sql(f"""
  UPDATE {manifest}
  SET status = 'approved',
      final_replacement = proposed_replacement,
      reviewer = current_user(),
      reviewed_at = current_timestamp()
  WHERE confidence >= 0.85 AND status = 'proposed'
""")

# COMMAND ----------

# MAGIC %md ## 3. Confirm the new status distribution

# COMMAND ----------

display(spark.sql(f"SELECT status, count(*) AS n FROM {manifest} GROUP BY status ORDER BY status"))
