# System Diagram

This diagram shows the full intended `worthless/` system while distinguishing what exists in the repository from what is still planned.

Legend:
- `[Implemented]` present in `src/` or validated by tests
- `[Missing]` planned in `.planning/ROADMAP.md` but not yet implemented

```mermaid
flowchart LR
    A["Developer App / SDK Client [Missing]"] --> B["CLI / local routing surface [Missing]"]
    B --> C["Proxy request handler [Missing]"]
    C --> D["Rules engine / gate-before-reconstruct [Missing]"]
    D --> E["ShardRepository (SQLite + Fernet) [Implemented]"]
    D --> F["Adapter registry and provider transforms [Implemented]"]
    E --> G["reconstruct_key + secure_key [Implemented]"]
    G --> H["Upstream HTTP caller [Missing]"]
    F --> H
    H --> I["OpenAI API [Target]"]
    H --> J["Anthropic API [Target]"]

    K["split_key / SplitResult [Implemented]"] --> L["Enrollment orchestration [Missing]"]
    L --> E
    L --> M["Client-side shard_a storage [Missing]"]
    M --> B
```

## As-Built Component Map

```mermaid
flowchart TD
    subgraph CurrentRepo["Current repo implementation"]
        C1["crypto/splitter.py"]
        C2["crypto/types.py"]
        S1["storage/schema.py"]
        S2["storage/repository.py"]
        A1["adapters/types.py"]
        A2["adapters/openai.py"]
        A3["adapters/anthropic.py"]
        A4["adapters/registry.py"]
    end

    C1 --> C2
    S2 --> S1
    A2 --> A1
    A3 --> A1
    A4 --> A2
    A4 --> A3
```

## What Is Implemented

- split/reconstruct/zero lifecycle in `src/worthless/crypto/`
- encrypted `shard_b` persistence in `src/worthless/storage/`
- OpenAI and Anthropic request/response transforms in `src/worthless/adapters/`
- SSE passthrough behavior validated by `tests/test_streaming.py`

## What Is Missing

- a real request entry point
- enrollment flow joining crypto and storage
- client-side shard storage
- gate-before-reconstruct policy layer
- upstream call execution with reconstructed keys
- CLI UX
- security posture document
