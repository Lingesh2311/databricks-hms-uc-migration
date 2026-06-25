# databricks-hms-uc-migration

A toolkit and agent skill to migrate **user assets** from Hive Metastore (HMS) to Unity
Catalog (UC): the notebooks, dashboards, and scripts that still reference `hive_metastore`
table names and `/mnt/...` mount paths after the underlying tables have moved.

It does not migrate tables. Moving tables, grants, and the metastore mapping is the other
half of UC migration, handled by table-migration tooling. This toolkit complements that work
and patches the code that references those tables: it discovers the references, proposes UC
replacements with explainable confidence scores, routes them through a tiered human review,
applies the approved changes in place (reversibly), and reconciles the result with an audit
trail.

Everything runs inside your own workspace. No external egress.

## The five stages

| Stage | Script | What it does |
|-------|--------|--------------|
| 1. Discover | `scripts/jobs/stage1_discovery.py` | Inventory notebooks, dashboards, and mount points |
| 2. Scan and propose | `scripts/jobs/stage2_scan_propose.py` + `scripts/scanner/` | Parse each asset, find HMS references, propose UC targets, score confidence |
| 3. Review | `scripts/review/` + Genie Code skill | Human approval, routed by confidence |
| 4. Apply | `scripts/jobs/stage4_apply.py` | Back up, patch, and re-import the assets |
| 5. Validate and learn | `scripts/jobs/stage5_validate.py` | Reconcile before and after; log every decision |

## How it works

The scanner is pure Python: `sqlglot` for SQL cells, the standard library `ast` for Python
cells. Confidence is rule-based and explainable, not a model, so every proposal maps to a
stated reason and nothing below 0.85 is applied without a human approving it. References built
at runtime (dynamic mount paths, table names assembled from variables) are flagged for review
rather than skipped, so the tool never silently misses code it cannot resolve.

## Quickstart

```bash
pip install databricks-sdk sqlglot mlflow ipywidgets

# 1. create the control tables (see references/pipeline-stages.md for DDL)
# 2. seed your HMS-to-UC mapping
# 3. (optional) seed a synthetic estate to trial the flow:
python3 scripts/setup/seed_demo_assets.py --profile <profile> \
  --catalog <catalog> --schema <schema> \
  --demo-root /Workspace/Shared/hms_uc_migration_demo/demo_notebooks

# 4. run the stages
python3 scripts/jobs/stage1_discovery.py    --profile <profile> --catalog <catalog> --schema <schema>
python3 scripts/jobs/stage2_scan_propose.py --profile <profile> --catalog <catalog> --schema <schema>
# review (stage 3, interactive), then:
python3 scripts/jobs/stage4_apply.py        --profile <profile> --catalog <catalog> --schema <schema>
python3 scripts/jobs/stage5_validate.py     --profile <profile> --catalog <catalog> --schema <schema>
```

Run the same stages as workspace jobs via the bundle, or locally via a CLI profile as shown.
Every script reads `--catalog`, `--schema`, and where relevant `--demo-root`,
`--warehouse-id`, and `--profile`. Nothing environment-specific is hardcoded.

## Repository layout

```
SKILL.md            Agent skill entry point (parent: databricks-core)
references/          Stage details, scanner rules, confidence model, review, API recipes, audit
scripts/
  scanner/           Pure-Python reference extraction and scoring (sqlglot + ast)
  jobs/              The five stage jobs
  review/            Stage 3 review notebooks
  setup/             Synthetic estate generator
  workspace_skill/   Genie Code review skill for the judgement tier
tests/               Scanner unit tests (test-first)
docs/                Operational runbook (HTML)
```

## Testing

The scanner has no Databricks dependency, so its rules run offline:

```bash
pytest tests/
```

## Documentation

- `SKILL.md` and `references/` are the full operating guide.
- `docs/HMS_UC_MIGRATION_RUNBOOK.html` is a step-by-step operational runbook.

## License

Apache License 2.0. See `LICENSE`.
