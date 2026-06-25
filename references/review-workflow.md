# Review workflow (Stage 3)

Three surfaces, routed by confidence. Each writes `status` and `final_replacement` back to
`migration_manifest`. Nothing below 0.85 is applied without a human action.

## Simple (confidence >= 0.85): batch approve

`scripts/review/review_simple.py`. Displays the high-confidence proposals, then one
idempotent `UPDATE` approves the batch:

```sql
UPDATE <catalog>.<schema>.migration_manifest
SET status='approved', final_replacement=proposed_replacement,
    reviewer=current_user(), reviewed_at=current_timestamp()
WHERE confidence >= 0.85 AND status='proposed';
```

This is the "90% auto-approvable in seconds" moment.

## Complex (0.45 to 0.85): diff + override

`scripts/review/review_complex.py`. An `ipywidgets` card per reference: original vs proposed,
an editable `final` field, and Approve / Override buttons. Each button writes one row:

```sql
UPDATE <catalog>.<schema>.migration_manifest
SET status='approved'|'overridden', final_replacement='<edited value>',
    reviewer=current_user(), reviewed_at=current_timestamp()
WHERE manifest_id='<id>';
```

## Very complex (confidence < 0.45): Genie Code skill

`scripts/workspace_skill/hms-migration-review/SKILL.md` is a Genie Code workspace skill. The
reviewer opens Genie Code on an ambiguous `dynamic_mount` / `dynamic_table` row; the skill
loads the manifest row, shows the source cell in context, enumerates candidate namespaces
from the registry/mapping, explains the ambiguity in plain language, recommends a
`final_replacement` (never imposes one), and on the reviewer's confirmation runs the UPDATE
(`overridden`, or `skipped` if it should not migrate).

Install it as a per-user workspace skill (`.assistant/skills/` or your workspace's skill
location) so Genie Code can load it on demand.
