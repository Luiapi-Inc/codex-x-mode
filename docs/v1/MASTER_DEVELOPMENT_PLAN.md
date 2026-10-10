# Codex X Mode v1.0 — Clean-Slate Master Development Plan

Date: 2026-10-10 (Asia/Bangkok)
Status: V1 DESIGN BASELINE ON `main` — IMPLEMENTATION IN PROGRESS; NO RELEASE AUTHORIZATION
Repository: `Luiapi-Inc/codex-x-mode`
Target Git shape: `main` plus `archive/v0.2.23-legacy`.

## 1. Product decision

Build Codex X Mode v1 as a personal agentic coding control plane. **Every technical detail from v0.x may be redesigned or removed.** The legacy branch is kept only for historical recovery. No requirement to keep 13+8 tools, legacy route names, UI/plugin identity details, or v0 model aliases.

Current live service must not be treated as v1 acceptance. v1 is only ready after executable tests and actual connector evidence.

## 2. Architecture

```text
ChatGPT Web / Mobile
    │  1 authenticated MCP connection
    ▼
Codex X Gateway ─ Tool Registry ─ Authorization / Audit
    │
    ├─ Project/Workspace Service ─ Scoped reads and approved edits
    ├─ Durable Run Manager ─ SQLite ─ Claims / Evidence / Events
    ├─ Native Codex Provider ─ app-server / exact selected models
    ├─ Serena Provider [optional, independent lifecycle]
    └─ Workspace Agent Provider [optional, gated by API proof]

Browser ─ Cloudflare Access + MFA ─ Admin API
                                     │
                         Config / Policy / Recovery
                                     │
                         Same application state store
```

Prefer a **modular monolith** over multiple daemons: reuse proven Python bridge code where useful, SQLite for transactions, and React/TypeScript/Vite static dashboard. No Kubernetes, SaaS tenancy or large event fabric.

## 3. Tool boundary

A v1 tool serves a user job rather than reproducing historical MCP names. Proposed groups:

- Project: list/inspect/read
- Runs: create/get/list/cancel/reconcile
- Native Codex: model catalog, thread operations and turns
- Workspace: preview-edit and authorized apply-edit
- Providers: capabilities/health (optional providers advertise only implemented operations)

Names are proposals pending MCP schema discovery and compatibility tests. Every tools/call operation authenticates the caller and rechecks fine-grained project/tool/mutation permission; discoverability is not authorization. Read-only inspection never accidentally dispatches an execution task.

## 4. Execution and model identity

- ChatGPT Direct means tool orchestration by the active ChatGPT conversation, *not* a new background ChatGPT instance.
- Native Codex receives selected executable model and supported effort; collect requested, selected, terminal model identities and reroute evidence independently.
- Serena supports optional symbol navigation, diagnostics and scoped patches. Its absence cannot disable Core.
- External Workspace/Agent API is optional; publish capability only after entitlement, run identity, result channel, cancellation behavior and independent evidence are verified.
- No implicit provider fallback. Prefer explicit user route for first v1 milestone.

## 5. Durable run and concurrency contract

Persist: `run_id`, `request_key`, `request_digest`, `attempt_id`, `provider_id`, `provider_run_id`, `project_id`, `resource_id`, `scope`, `work_contract_digest`, `state`, `revision`, `evidence_refs`.

The state machine differentiates queued, submitting, running, verifying, succeeded, cancelled, failed, blocked and **unknown**. Record the request before provider submission. An ambiguous request is not automatically retried. Resource writer ownership is serialized transactionally and survives restart. Cancel-request is not terminal evidence. Successful provider exit without accepted artifacts is still unverified.

## 6. Admin and configuration

Separate Admin API authentication from MCP authentication. Remote admin requires Cloudflare Access with operator allowlist and MFA; application must verify trusted identity claims and authorize privileged actions itself. No raw terminal or arbitrary filesystem endpoint, and no bridge secret returned to browser.

Configuration pipeline: validate schema → policy validation → preview diff → operator confirmation → revision CAS → atomic commit → reload → health check → audit or rollback. Active jobs and writer claims constrain configuration changes.

Dashboard pages: Health, Projects, MCP, Models, Providers, Jobs & Recovery, Configuration and Audit. Begin with polling; add SSE only if observable operational value warrants it.

## 7. Delivery roadmap

| Gate | Work | Required executable proof |
|---|---|---|
| G0 — Recovery | Verified WIP backup, merge/cull old worktrees, reconcile unknown writer | Source and task evidence; no conflicting writer |
| G1 — v1 Contracts | MCP/Admin schemas, auth boundaries, state machine, UI contract | Reviewed versioned specs |
| G2 — Kernel | App services for identity, policy, config, SQLite jobs/claims | Unit + transaction + negative authorization tests |
| G3 — Gateway/Native | One MCP, independent provider adapters | Real client discovery and read-only Native Codex E2E |
| G4 — Direct/Serena | Scoped reads/edits, optional symbols | Hash guard, diff/test receipts; Serena-down test |
| G5 — Dashboard | Local/remote UI, config and audit | Browser auth negatives, config rollback |
| G6 — Optional Agent | Feasibility/entitlement/result handoff | E2E verified result or explicit defer |
| G7 — Release | Security/failure injection, Web/Mobile, clean install | Version/hash parity, rollback drill, explicit GO |

Workstream concurrency: start with 2–3 non-overlapping write owners at most; independent QA verifies actual tool evidence. Do not allow agents to edit the same state database, route file, migration or public manifest concurrently.

## 8. Acceptance and release

- One ChatGPT Web and Mobile connector actually discovers/calls configured tools.
- Native inference is verified with exact terminal model identity, or reports unverified.
- Unauthorized project write, auth bypass, path escape, stale config revision and replay fail closed.
- Disconnection after submission does not duplicate side effects.
- Unknown writer remains blocked until authoritative reconciliation.
- Optional provider crash does not affect Gateway/Core.
- Browser setup/remote admin uses real identity validation and negative tests.
- Reproducible build, clean install, package hash, rollback and operational evidence are captured.
- Release/deployment occurs only after a separately authorized GO.

## 9. Current operational caveat (2026-10-10)

The previously blocked task `task_96251e21c3cd454c97bb2c392f9b7ebe` was reconciled on 2026-10-10 using its recorded matching Native `turn/completed` event. Because terminal model identity was absent, it remains **not accepted** and was marked `failed` with preserved result and explicit reconciliation evidence. The previous writer conflict cleared; four read-only unknown tasks remain, without write claims.

This document is now the v1 plan in `main`. Production still runs the previous runtime until a separate release GO.
