# G2 — Terminal Evidence and Writer Claim Separation

Date: 2026-10-10 (Asia/Bangkok)
Scope: development main only; production runtime has not been deployed.

## Defect

The bridge persisted a matching Native turn/completed event yet left tasks unknown when terminal model identity was missing, incorrectly retaining a workspace-write conflict even after the owned app-server exited.

## Correctness contract

Terminal execution identity and final model/acceptance identity are separate. After matching native completion and verified owned process-group shutdown, the writer may be released while success remains unaccepted. A transport failure or unverified process shutdown must retain unknown and block a conflicting writer.

## Implementation

- core.py: transactional Store.finalize_unverified_terminal_task with exact thread/turn IDs, Native terminal status and unverified model proof; preserves result and appends reconciliation metadata.
- codex.py: invoke only after AppServer.close() returns exactly True; no replay, no inference proof fabricated.
- test_native_web_dispatch.py: RED then GREEN, covers confirmed shutdown, unconfirmed shutdown blocking, provider terminal error, and unchanged verified success.

## Evidence

- RED test reproduced the defect: unknown != failed.
- Focused tests: 19/19 PASS.
- Full regression suite and deterministic bundle SHA-256 recorded in execution log.
- No live Web/Mobile E2E or production deployment performed.

## Residual risk

The functionality is not yet deployed, and a real Native app-server E2E + release review remains required. Model identity missing still forbids a successful acceptance verdict.
