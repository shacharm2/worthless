# WOR-207: Documentation sync — remove decoy/shard-A file references

> "The code is ahead of the docs."

The security MODEL is correct (SR-09 already says "Authorization header only"). But the docs haven't caught up to the code.

## What

Single sweep through all .md files replacing stale references:
- "decoy" → "shard-A in .env"
- "shard-A file" / "shard_a_dir" → removed
- "x-worthless-key" / "x-worthless-shard-a" → "Authorization: Bearer"
- "disk fallback" → removed

## Files to update

1. `SECURITY_POSTURE.md` — references x-worthless-key, x-worthless-shard-a, shard-A file storage, decoy system
2. `CLAUDE.md` — "Three architectural invariants" section, product description may reference decoys or shard-A files, "Services and languages" table mentions Reconstruction reading shard-A
3. `.claude/module_ir.md` — per-module IR for crypto/, proxy/, storage/ describes old shard-A flow
4. `AGENTS.md` — agent architecture notes may reference old flow
5. `.planning/PROJECT.md` — project context
6. `.planning/ROADMAP.md` — phase descriptions may reference decoys
7. `TESTING.md` — testing lanes reference decoy-related test expectations

## AC

`grep -ri "x-worthless-key\|x-worthless-shard-a\|shard_a_dir\|shard.a.file\|make_decoy\|decoy.system" *.md .claude/ .planning/` returns zero matches (excluding the audit doc itself).
