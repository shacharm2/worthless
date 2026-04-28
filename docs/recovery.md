---
title: "Recovery"
description: "How to recover when your .env is corrupted, deleted, or overwritten."
---

# Recovery

Worthless does not back up your `.env` file. Locking replaces the real key in `.env` with shard A (which looks like a real key) and stores shard B in the local database; if your `.env` is corrupted, deleted, or overwritten, Worthless cannot reconstruct it for you. Keep your own backup — a password manager, an encrypted secrets vault, or a private file kept outside the repo all work. If you have replacement bytes ready, `worthless restore <path>` reads them from stdin and writes them atomically to the target file (e.g. `cat saved.env | worthless restore .env`); this only restores file contents, it does not regenerate keys.
