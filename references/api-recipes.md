# API recipes

All workspace access uses `databricks-sdk` (`WorkspaceClient`). Select the profile via the
`DATABRICKS_CONFIG_PROFILE` env var rather than `WorkspaceClient(profile=...)`: if two
profiles share a host, the SDK's CLI token helper cannot disambiguate the `profile=` form,
but the env var drives both host and token resolution cleanly.

```python
import os
os.environ["DATABRICKS_CONFIG_PROFILE"] = "<your_profile>"   # local; omit in a job
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
warehouse_id = next(iter(w.warehouses.list())).id            # or pass explicitly
```

## Enumerate notebooks (Stage 1)

```python
def list_notebooks(path):
    out = []
    for obj in w.workspace.list(path):
        if obj.object_type and obj.object_type.value == "NOTEBOOK":
            out.append(obj.path)
        elif obj.object_type and obj.object_type.value == "DIRECTORY":
            out.extend(list_notebooks(obj.path))
    return out
```

Mounts: `dbutils.fs.mounts()` in a real workspace. On serverless that is unavailable, so fall
back to a known mount set and **log the fallback** (never silent).

## Export / re-import notebook source (Stages 2 and 4)

```python
from databricks.sdk.service.workspace import ExportFormat, ImportFormat, Language
import base64

src = base64.b64decode(w.workspace.export(path=p, format=ExportFormat.SOURCE).content).decode()
# ... patch src ...
w.workspace.import_(path=p, content=base64.b64encode(src.encode()).decode(),
                    format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=True)
```

## Back up before patching (Stage 4, reversibility)

```python
import io
w.files.upload(f"/Volumes/<cat>/<sch>/backups/{safe_name}__{stamp}.py",
               io.BytesIO(src.encode()), overwrite=True)
```

Create the volume first: `CREATE VOLUME IF NOT EXISTS <cat>.<sch>.backups`.

## Patch order

When a notebook has several references, replace the **longest** `original_reference` first to
avoid partial-overlap corruption. The current implementation uses literal `str.replace`; a
tokenizer-based replace is a possible future hardening for adversarial source.

## SQL from scripts

Use the Statement Execution API: `w.statement_execution.execute_statement(statement=...,
warehouse_id=..., wait_timeout="50s")`. Do not run user-data strings through Python
`str.format` (references can contain literal `{}`); build SQL with f-strings over known table
names and escape single quotes in values.
