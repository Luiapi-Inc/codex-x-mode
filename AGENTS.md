# AGENTS.md

## Authority

Read `docs/CODEX_X_MODE_SOURCE_OF_TRUTH.md` before changing dispatch, model routing, MCP, release, deployment, or acceptance behavior.

## Non-negotiable architecture

Web-originated execution must remain:

```text
ChatGPT Web -> Codex X Mode bridge -> Native Codex app-server -> exact chatgpt-web/<version> -> ChatGPT Web backend
```

Do not route Web-originated tasks to `chatgpt_plan`.
Do not silently substitute a non-`chatgpt-web/*` model.

## Self-contained boundary

The runtime must not require another ChatGPT Plugin, external Tool, external Skill, or external MCP package. Required Skill/Tool/MCP/model-routing/validation behavior must live in this repository.

Using local development tooling to inspect or edit this repository does not make that tooling a runtime dependency.

## Engineering workflow

Requirement -> Plan -> Implementation -> Test -> Evidence -> Review

- inspect source/evidence first
- make the smallest safe change
- avoid unrelated refactors
- add/update tests for behavior changes
- preserve idempotency and unknown-task safety
- never replay an ambiguous `turn/start`
- never claim PASS without executed evidence
- keep secrets and machine-local state out of Git

## Release rule

`0.2.11` is a bootstrap/reference checkpoint only and is not architecture acceptance.
Architecture corrections must ship as a new semver release (`>=0.2.12`).
