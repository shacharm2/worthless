# State Machines

## Enrollment / lock flow

```mermaid
stateDiagram-v2
    [*] --> ScanEnv
    ScanEnv --> DetectKey
    DetectKey --> SplitKey
    SplitKey --> StoreShardB
    StoreShardB --> PersistLocalShard
    PersistLocalShard --> RewriteEnvWithDecoy
    RewriteEnvWithDecoy --> RecordEnrollment
    RecordEnrollment --> Protected
    Protected --> [*]
```

Important note:

- the current implementation is multi-step and has intermediate states; storage and rewrite operations do not happen as one atomic black box

## Request handling flow

```mermaid
stateDiagram-v2
    [*] --> ParseAlias
    ParseAlias --> ExtractShardA
    ExtractShardA --> EvaluateRules
    EvaluateRules --> Denied: rule blocks
    EvaluateRules --> FetchEncryptedShard: allowed
    FetchEncryptedShard --> DecryptShard
    DecryptShard --> ReconstructKey
    ReconstructKey --> RelayUpstream
    RelayUpstream --> RecordUsage
    RecordUsage --> ZeroMutableBuffers
    ZeroMutableBuffers --> [*]
    Denied --> [*]
```

Important note:

- the key security invariant is gate-before-reconstruct

## Daemon lifecycle

```mermaid
stateDiagram-v2
    [*] --> Stopped
    Stopped --> Starting: up
    Starting --> Running
    Running --> Reporting: status
    Reporting --> Running
    Running --> Stopping: down
    Stopping --> Stopped
```

## Wrap lifecycle

```mermaid
stateDiagram-v2
    [*] --> SpawnEphemeralProxy
    SpawnEphemeralProxy --> InjectBaseUrls
    InjectBaseUrls --> SpawnChild
    SpawnChild --> ChildRunning
    ChildRunning --> ChildExited
    ChildExited --> CleanupProxy
    CleanupProxy --> [*]
```

## Recovery and deletion

```mermaid
stateDiagram-v2
    [*] --> Protected
    Protected --> RestorePlaintext: unlock
    Protected --> DeleteArtifacts: revoke
    RestorePlaintext --> PlaintextRestored
    DeleteArtifacts --> Revoked
    PlaintextRestored --> [*]
    Revoked --> [*]
```

Important note:

- `unlock` intentionally reintroduces plaintext
- `revoke` is a deletion-oriented path with best-effort local wipe semantics
