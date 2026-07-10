# ADR 0001: Deterministic v2 Snapshot Workflow

- **Status**: Accepted
- **Date**: 2026-07-10

## Context

In version 1 (v1), integration with music providers, file pairing, session state, and XML writing were tightly coupled and mixed across the CLI and HTTP routes. This design led to several key issues:
- Previews could diverge from the final applied changes.
- Rigid schemas for music providers made adding new platforms difficult.
- In-memory session state was lost during serverless cold starts on Vercel.

## Decision

To address these limitations, we designed a deterministic snapshot-based workflow for v2:

- **Decoupled Engine**: The `SourceRegistry` and `MatchingEngine` manage the provider dependency graph (DAG), global concurrency limits, per-source semaphores, caching, and partial provider failures.
- **Unified Pairing**: The `PairingPlan` is the sole pairing implementation. Pair IDs are derived from normalized TTML filenames, and file integrity checks rely on content hashes.
- **Immutable Snapshot**: The preview step generates and stores an immutable `PreviewSnapshot` containing all candidates and metadata contributions. The subsequent verification (ChangePlan) and write (apply) phases read directly from this snapshot, making zero external provider API calls.
- **Pure Planner**: The `TtmlPlanner` is pure and side-effect-free. Preview and application phases share identical text output and SHA-256 validation. The `TtmlWriter` ensures atomic writes and creates backups (`.bak`).
- **Application Boundaries**: The `SessionWorkflow` defines the application layer boundary. Session metadata is persisted via the `SessionRepository`, while uploads, snapshots, and final results are stored in the `ArtifactStore`.
- **Flexible Contract**: The public HTTP surface is exposed exclusively via `/api/v2`, representing provider results as extensible maps instead of fixed fields.
- **Frontend Gateway**: The web application communicates using generated OpenAPI DTOs, adapting them to the frontend domain models at the gateway boundary, and holding temporary user choices in a `ReviewDraft`.

## Consequences

- **Breaking Changes**: Version 1 sessions and payload schemas are deprecated and not migrated.
- **Strict Verification**: Application writes will fail immediately if the session snapshot is missing, stale, or has a mismatched hash.
- **Resilient Retrievals**: A failure or rate-limit at a single music provider is isolated as a warning for that specific source, without blocking other providers or pairs.
- **Serverless Ready**: Session execution can transition smoothly between different Vercel serverless instances, as the state is persisted externally via Redis (KV) and Blob storage.
- **Easier Expansion**: Integrating a new music provider only requires implementing its `SourceAdapter` and API models, without database schema migrations.
