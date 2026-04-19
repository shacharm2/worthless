# Snapshot Schema

## Linear snapshots (`linear-v11-*.json`, `linear-v20-*.json`)

```json
{
  "project": "string",
  "project_id": "uuid",
  "fetched_at": "YYYY-MM-DD",
  "issues": [
    {
      "id": "uuid",
      "identifier": "WOR-NNN",
      "title": "string",
      "state": { "name": "string" },
      "priority": 0,
      "sortOrder": 0.0,
      "parent": { "id": "uuid", "identifier": "WOR-NNN", "title": "string" } | null,
      "project": { "id": "uuid", "name": "string" } | null,
      "projectMilestone": { "id": "uuid", "name": "string" } | null,
      "labels": { "nodes": [{ "name": "string" }] }
    }
  ]
}
```

## Beads snapshot (`beads-*.json`)

Array of issue objects as returned by `bd list --json`.

## Notes

- Snapshots are inputs to `scripts/roadmap.py` — do not hand-edit
- Re-fetch with Linear API + `bd list --json` after any structural changes
- `parentId: null` means top-level issue (no parent epic)
