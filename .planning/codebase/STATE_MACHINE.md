# State Machine

This state machine models the intended request lifecycle for `worthless/` and labels each state by current implementation status.

```mermaid
stateDiagram-v2
    [*] --> KeyUnprotected

    KeyUnprotected --> KeySplit : split_key() [Implemented]
    KeySplit --> ServerShardStored : ShardRepository.store() [Implemented]
    KeySplit --> ClientShardStored : client-side shard_a persistence [Missing]
    ServerShardStored --> Enrolled : enrollment coordinator joins both sides [Missing]
    ClientShardStored --> Enrolled : enrollment coordinator joins both sides [Missing]

    Enrolled --> RequestReceived : proxy/CLI entrypoint receives API call [Missing]
    RequestReceived --> ProviderResolved : get_adapter(path) [Implemented once proxy exists]
    ProviderResolved --> GateEvaluated : policy/rules engine [Missing]
    GateEvaluated --> RequestDenied : cap/rate/model rule fails [Missing]
    GateEvaluated --> ServerShardLoaded : retrieve encrypted shard_b [Implemented]
    ServerShardLoaded --> KeyReconstructed : reconstruct_key() [Implemented]
    KeyReconstructed --> UpstreamPrepared : adapter.prepare_request() [Implemented]
    UpstreamPrepared --> UpstreamCalled : direct HTTP call with full key [Missing]
    UpstreamCalled --> StreamingRelay : relay_response(streaming) [Implemented]
    UpstreamCalled --> BufferedRelay : relay_response(non-streaming) [Implemented]
    StreamingRelay --> KeyZeroed : secure_key() / zeroing boundary [Implemented]
    BufferedRelay --> KeyZeroed : secure_key() / zeroing boundary [Implemented]
    KeyZeroed --> UsageRecorded : metering/persistence [Missing]
    UsageRecorded --> ResponseReturned : proxy returns downstream response [Missing]
    RequestDenied --> ResponseReturned
    ResponseReturned --> [*]
```

## State Notes

- `KeySplit`, `ServerShardStored`, `KeyReconstructed`, `UpstreamPrepared`, and the relay states are backed by code and tests today.
- `Enrolled`, `RequestReceived`, `GateEvaluated`, `UpstreamCalled`, `UsageRecorded`, and `ResponseReturned` are still roadmap states.
- `ProviderResolved` is logically implemented through `get_adapter()`, but only becomes a real runtime state once a proxy handler exists.

## Current Practical Reality

Today the repository supports only partial state-machine fragments:

1. crypto fragment
   `KeyUnprotected -> KeySplit -> KeyReconstructed -> KeyZeroed`

2. storage fragment
   `KeySplit -> ServerShardStored`

3. adapter fragment
   `RequestReceived -> ProviderResolved -> UpstreamPrepared -> relay_response`

The missing application layer is what turns those fragments into one coherent running system.
