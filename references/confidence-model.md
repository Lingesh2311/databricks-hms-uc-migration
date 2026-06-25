# Confidence model

Confidence is **deterministic and rule-derived**, never an ML score. In a regulated bank a
platform team must defend every automated change; "the model said 0.8" is not an audit
answer, but "static table ref with an exact mapping hit, therefore 0.95" is. The trade is a
little recall for total explainability, which is the right trade for the buyer.

## Scoring rules (`propose` in `scripts/scanner/core.py`)

| Condition | Score | Tier |
|-----------|-------|------|
| Static table ref (`sql_table_ref` / `py_table_ref`) with exact `hms_to_uc_mapping` hit | 0.95 | simple |
| Static mount-path literal resolvable via registry + mapping | 0.90 | simple |
| Table ref whose namespace is known but table is not mapped | 0.60 | complex |
| Dynamic mount (`dynamic_mount`), registry non-empty | 0.70 | complex |
| Dynamic table (`dynamic_table`), name built at runtime | 0.30 | very_complex |
| No mapping hit / unknown namespace | 0.20 | very_complex |

## Proposed replacement shapes

- Table refs -> `uc_catalog.uc_schema.uc_table` (from the mapping).
- Mount literals -> a UC Volumes path `/Volumes/<cat>/<sch>/<sanitized_mount>/<subpath>`,
  preserving the sub-path and sanitizing the mount name (`sales-raw` -> `sales_raw`), so the
  Apply stage can substitute it cleanly into `spark.read.parquet(...)`.
- Dynamic refs -> no proposed replacement; the reviewer supplies `final_replacement`.

## Routing thresholds

```
confidence >= 0.85   -> simple        (SQL batch-approve)
0.45 to 0.85         -> complex       (ipywidgets diff + override)
confidence < 0.45    -> very_complex  (Genie Code assisted review)
```

Tune the thresholds and per-rule scores in `core.py` (`propose` and `_tier`). Keep them
explainable: each rule should map to a one-sentence justification a reviewer can read.
