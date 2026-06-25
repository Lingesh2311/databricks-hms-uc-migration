---
name: databricks-hms-uc-migration
description: "Migrate Hive Metastore (HMS) references inside user assets (notebook source code, hard-coded /mnt mount paths, and dashboard queries) to Unity Catalog. Use when a UC table migration is done but notebooks and dashboards still reference hive_metastore tables or /mnt paths, when patching those references at scale, or when you need a confidence-scored, human-reviewed, audited rewrite. Complements UCX (which migrates the metastore objects: tables, grants, table mapping); this patches the code that references them. Not for migrating the tables themselves."
compatibility: Requires databricks CLI (>= v0.292.0), Python 3.10+, sqlglot, databricks-sdk, mlflow
metadata:
  version: "0.1.0"
parent: databricks-core
---

# HMS to UC User-Asset Migration

UC migration has two halves. UCX and the Databricks PS package migrate the **metastore
objects**: tables, grants, and the HMS to UC table mapping. They do not patch the **user
assets**: the notebooks, dashboards, and ad-hoc code that still hard-code
`hive_metastore.*` table names and `/mnt/...` paths. When the metastore moves but the code
does not, the code breaks. This skill automates that second half: discover the references,
propose UC replacements with explainable confidence scores, route them through a tiered
human-review gate, patch the assets in place (reversibly), and validate with an audit trail.

Everything runs inside the customer workspace. No external egress.

## When to use

- A UC table migration (e.g. via UCX) is done or in progress, and notebooks/dashboards still
  reference `hive_metastore` tables or `/mnt/` mount paths.
- You need to patch those references at scale with review and an audit trail, not by hand.
- You need to prove to a governance/risk team that every change was reviewed and reconciled.

Do NOT use this to migrate the tables themselves (use UCX). This patches the code only.

## The 5 stages

| Stage | Script | What it does |
|-------|--------|--------------|
| 1 Discovery | `scripts/jobs/stage1_discovery.py` | Enumerate notebooks, mounts, dashboards; write `mount_point_registry` |
| 2 Scan + Propose | `scripts/jobs/stage2_scan_propose.py` + `scripts/scanner/` | Parse source (sqlglot + ast), propose UC refs, score confidence; write `migration_manifest` |
| 3 Human Review | `scripts/review/` | Simple (SQL batch-approve), complex (ipywidgets diff/override), very-complex (Genie Code skill) |
| 4 Apply | `scripts/jobs/stage4_apply.py` | Back up to a UC Volume, patch source, re-import |
| 5 Validate + Learn | `scripts/jobs/stage5_validate.py` | Reconcile pre/post count + schema; log decisions to MLflow; flag mismatches |

## Prerequisites

1. A UC catalog and schema for the control tables. Create the 4 tables (see
   `references/pipeline-stages.md` for DDL): `hms_to_uc_mapping`, `mount_point_registry`,
   `demo_asset_inventory`, `migration_manifest`.
2. Seed `hms_to_uc_mapping` with your HMS to UC answer key. In production this is fed by
   UCX's table-mapping output (the integration seam with the migration factory); for a demo,
   seed it directly.
3. A SQL warehouse and the Python deps (`sqlglot`, `databricks-sdk`, `mlflow`).

## Workflow

1. **Set up control tables and mapping** (see `references/pipeline-stages.md`).
2. **Optional: seed synthetic demo assets** with `scripts/setup/seed_demo_assets.py` to try
   the pipeline before pointing it at real assets.
3. **Run Stage 1** to populate `mount_point_registry`.
4. **Run Stage 2** to populate `migration_manifest` (status `proposed`). The scanner is pure
   Python; see `references/scanner-rules.md` and `references/confidence-model.md`.
5. **Review** (`references/review-workflow.md`): batch-approve high confidence, diff/override
   medium, use the Genie Code skill for ambiguous cases.
6. **Run Stage 4** to patch + re-import approved/overridden references (reversible via Volume
   backups). See `references/api-recipes.md`.
7. **Run Stage 5** to reconcile and log the audit (`references/mlflow-audit.md`).

## Parameters

Every script reads `--catalog`, `--schema`, and (where relevant) `--demo-root`,
`--warehouse-id`, `--profile`. As a Databricks job, the same values come from notebook task
`base_parameters` / widgets. Nothing customer-specific is hardcoded.

## Principles

- **Explainable, not ML.** Confidence is rule-derived so every proposal is justifiable in a
  regulated-bank review. See `references/confidence-model.md`.
- **Nothing auto-applies below 0.85** without a human approval.
- **Reversible.** Apply backs up original source to a UC Volume before patching.
- **No false-negative silence.** References that cannot be resolved statically (dynamic
  mounts, dynamically-built table names) are flagged for review, never skipped.
- **Audited.** Every decision is an MLflow run.

## Testing

`scripts/scanner/` is pure Python with no Databricks dependency. Run `pytest tests/` to
exercise the extraction and scoring rules offline before running anything in a workspace.

## Reference guides

- `references/pipeline-stages.md` - control-table DDL + each stage's entry/exit
- `references/scanner-rules.md` - how SQL and Python references are extracted
- `references/confidence-model.md` - the scoring tiers and routing thresholds
- `references/review-workflow.md` - the three review surfaces
- `references/api-recipes.md` - Workspace export/import + backup recipes
- `references/mlflow-audit.md` - decision logging + reconcile
