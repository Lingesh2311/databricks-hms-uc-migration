# Databricks notebook source
# MAGIC %md
# MAGIC # Stage 3 Review: Complex path (medium confidence)
# MAGIC
# MAGIC Medium-confidence proposals (0.45 to 0.85) are dynamic mounts and other references
# MAGIC the scanner could not pin down with certainty. Each gets a proper review card:
# MAGIC original reference, the proposed replacement, and an editable field so the reviewer
# MAGIC can Approve the proposal or Override it with a corrected target. Each action writes
# MAGIC back to the manifest immediately.

# COMMAND ----------

# MAGIC %pip install ipywidgets
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "demo_hms_uc_migration")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
manifest = f"{catalog}.{schema}.migration_manifest"

# COMMAND ----------

import ipywidgets as widgets
from IPython.display import display

rows = spark.sql(f"""
  SELECT manifest_id, asset_path, cell_index, original_reference,
         proposed_replacement, confidence, reference_kind
  FROM {manifest}
  WHERE confidence >= 0.45 AND confidence < 0.85 AND status = 'proposed'
  ORDER BY confidence
""").collect()


def _write(manifest_id: str, final_replacement: str, status: str):
    safe = final_replacement.replace("'", "''")
    spark.sql(f"""
      UPDATE {manifest}
      SET status = '{status}',
          final_replacement = '{safe}',
          reviewer = current_user(),
          reviewed_at = current_timestamp()
      WHERE manifest_id = '{manifest_id}'
    """)


def render_card(row):
    header = widgets.HTML(
        f"<b>{row.asset_path}</b> (cell {row.cell_index}) "
        f"&middot; {row.reference_kind} &middot; confidence {row.confidence}")
    original = widgets.HTML(f"<code>original:</code> <code>{row.original_reference}</code>")
    edit = widgets.Text(value=row.proposed_replacement or "",
                        description="final:", layout=widgets.Layout(width="600px"))
    status_out = widgets.Output()
    approve = widgets.Button(description="Approve", button_style="success")
    override = widgets.Button(description="Override", button_style="warning")

    def on_approve(_):
        _write(row.manifest_id, edit.value, "approved")
        with status_out:
            print("approved ->", edit.value)

    def on_override(_):
        _write(row.manifest_id, edit.value, "overridden")
        with status_out:
            print("overridden ->", edit.value)

    approve.on_click(on_approve)
    override.on_click(on_override)
    return widgets.VBox([header, original, edit,
                         widgets.HBox([approve, override]), status_out])


print(f"{len(rows)} medium-confidence references to review")
for r in rows:
    display(render_card(r))

# COMMAND ----------

# MAGIC %md ## Progress

# COMMAND ----------

display(spark.sql(f"SELECT status, count(*) AS n FROM {manifest} GROUP BY status ORDER BY status"))
