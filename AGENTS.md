<!-- codex-workflow-user-managed-start -->
# AGENTS.md

## Workflow Principles

- Keep modules cohesive, interfaces explicit, coupling minimal, and behavior
  testable, replaceable, and reusable.
- Define proportionate acceptance and verification before implementation. Never
  weaken coverage, assertions, or failure visibility to save time or tokens.
- Avoid unnecessary process or safeguards; preserve unrelated user work and use
  verified facts in durable documentation.

## Route Selection

Select one route: **Light** works directly with minimal context;
**Medium** keeps planning, diagnosis, implementation, and verification with the
main agent and uses bounded read-only discovery, solution research, and
documentation support from `~/.codex/codex_workflow/medium_route.md`;
**Heavy** delegates bounded production, verification, documentation, context
exploration, and solution research under
`~/.codex/codex_workflow/heavy_route.md`.

Follow the user's route selection. Use Light when none is selected; do not infer
Medium or Heavy. Keep the route until the user changes it or the session ends.

## Rollout Efficiency

Batch independent reads, searches, metadata checks, and other known-input
operations. Keep dependencies and overlapping mutations sequential. In Medium
or Heavy, dispatch independent workers together, wait for the relevant set, and
synthesize their reports once. Workers return compact evidence-linked reports
through their parent-child result channel; Explorer owns bounded context
discovery.
For difficult or broad questions, prefer parallel workers (e.g., 2, 3, or more)
when independent angles can improve coverage and result quality. The main
chooses roles and counts per bounded question; a narrow question can use one
worker. Give workers addressing the same question complementary angles and
compare the assigned set's evidence before deciding.

## Platform Paths

Interpret `/` as a platform-neutral separator and translate paths for the
current operating system and shell.

## Lifecycle Commands

When the user's trimmed message matches one of the following command forms,
read and follow the corresponding guide. Forms without placeholders must match
exactly.

- codex_workflow --install
  Guide:  ~/.codex/codex_workflow/operate/install.md.

- codex_workflow --update
  Guide:  ~/.codex/codex_workflow/operate/update.md.

- codex_workflow --check-update
  Guide:  ~/.codex/codex_workflow/operate/check_update.md.

- codex_workflow --version
  Guide:  ~/.codex/codex_workflow/operate/version.md.

- codex_workflow --remove
  Guide: ~/.codex/codex_workflow/operate/remove.md.
<!-- codex-workflow-user-managed-end -->

# Codex X Mode v1 — Repository Instructions

## Mission and completion

- Deliver a working personal agentic coding control plane for one developer/operator, ready to propose Release GO under the current Master Plan.
- Required outcomes: one authenticated ChatGPT Web/Mobile MCP connection; verified Native Codex execution; scoped ChatGPT Direct reads/edits; durable runs and recovery; optional Serena with failure isolation; a real React/TypeScript/Vite dashboard backed by the Python Admin API; validated atomic configuration with rollback; secure local/remote access; reproducible packaging and acceptance evidence.
- The optional Workspace/Agent provider is capability-gated. Verify entitlement, real run identity, result retrieval and cancellation behavior before advertising it. If unavailable, record the exact blocker and explicit defer under the Master Plan; do not fabricate execution or silently substitute a provider.
- Implementation and acceptance must both be complete before claiming v1 ready. Keep failed, blocked, unknown and unverified outcomes visible. Preparing a release does not authorize deploying or publishing it.

## Authority and workflow

- v1 development uses `main` and `docs/v1/MASTER_DEVELOPMENT_PLAN.md` as the current design specification.
- The `archive/v0.2.23-legacy` branch is historical reference only. v0.x APIs, tool counts, routes and model mappings are not mandatory v1 behavior.
- Work in this order: Requirement → Plan → Implementation → Tests → Evidence → Review.
- Inspect source and active worktrees before changes; preserve user WIP.
- Keep changes narrow, test behavior changes, and avoid speculative refactors.

## Leadership and execution

- Continue from the latest verified checkpoint and existing implementation. Revalidate changed Git, task and runtime state after an interruption; do not repeat completed investigations without a concrete reason.
- The ongoing v1 mission uses the user's selected Heavy Route. Follow its role boundaries and the current host's delegation limits. Do not apply that selection to unrelated work in a new session unless selected there.
- The leader owns architecture, dependencies, interfaces, assignment, integration and acceptance. Assign bounded implementation and independent verification packages when supported. Never report workers as active unless actual dispatch succeeded.
- Start with at most 2–3 non-overlapping write owners, adjusted to actual capacity. Use at most one Senior Executor for a difficult package. If delegation is unavailable, report the limitation and continue only work permitted by the active instructions.
- Every worker receives a unique Task ID, objective, dependencies, exclusive files/resources, acceptance criteria, required checks and deliverable. Reports identify actual changes, commands/results, evidence and blockers. Workers must preserve others' edits and cannot expand ownership or change shared contracts without coordination.
- Use isolated worktrees for risky implementation. Worktree separation does not isolate state databases, ports, external services or writer claims. Give shared routers, migrations, public manifests and generated bundles one coordinated owner.
- Select the next highest-priority independent package after integrating verified work. A blocker stops only dependent or conflicting work. Routine implementation decisions and already-authorized checks do not need repeated user confirmation.
- Preserve the approved product scope. Prefer the modular Python monolith, SQLite and static React dashboard described in the Master Plan; Node is a build-time dependency. Do not add unnecessary infrastructure or workflow gates.
- Reuse validation evidence only while it matches the code and relevant environment. Rerun checks when changes, failures or unresolved risks require it, rather than to repeat a status report.

## Runtime and security

- Expose a single, curated ChatGPT MCP gateway. Keep the Admin API separate from MCP permissions.
- Native Codex controls Native Codex inference; optional providers cannot impersonate it.
- Discover executable models and supported reasoning effort from the active Native Codex API. Preserve requested, selected and terminal identity plus reroute evidence separately; a thread default, answer text or successful exit is not terminal identity proof.
- Route selection must not silently substitute models or providers.
- ChatGPT Direct is orchestration by the active conversation, not a hidden background copy. Enforce scoped reads/edits, preview and hash/revision guards where applicable. Optional provider failure must not disable Gateway/Core.
- Authenticate and authorize every mutation server-side. Enforce project allowlists and canonical path containment.
- Before non-idempotent execution, persist intent and acquire appropriate resource ownership transactionally.
- Unknown execution after a lost response must be reconciled; do not replay, forcibly release claims, or overwrite shared workspace state on assumption.
- Preserve durable tasks, audit trails, secrets isolation, exact execution receipts, and acceptance evidence.
- A provider reporting `completed` is not proof that the accepted work contract passed.
- Never expose secrets, unrestricted shell/filesystem tools, internal provider APIs or state DB publicly.
- Remote Admin uses Cloudflare Access, operator allowlist and MFA, with origin-side identity validation and authorization. MCP authentication remains separate. Never send the bridge credential to the browser or infer remote MCP connectivity from Dashboard login.
- Configuration changes follow schema/policy validation, redacted diff, operator confirmation, revision CAS, atomic apply, health verification, audit and rollback. Respect active jobs and writer claims during changes.

## Verification and reporting

- Define observable acceptance before implementation. For behavior changes, establish failing regression coverage where feasible, implement, verify and review before integration. Do not weaken tests or replace runtime behavior with mocks in the product.
- Run focused regressions and the existing full suite as appropriate. The Python full-suite command is `cd server && python3 -m unittest discover -s tests -q`; use frontend checks declared in `dashboard/package.json` when changing the dashboard.
- Test negative authorization, canonical path containment, idempotency, transaction ownership, lost-response handling and restart recovery for the boundaries changed. Validate bundle reproducibility, clean installation and rollback before release.
- Distinguish unit/fixture tests, local browser checks, live Native inference, terminal model identity and actual remote Web/Mobile E2E. Each passing claim names executed evidence for that level.
- Keep progress and evidence concise with one canonical home per fact. Report checkpoint, integrated commits, actual checks/results, blockers and the next action. Do not invent percentages, test counts, agent activity or deployment status.

## Development and release boundaries

- Implementation uses isolated tests/worktrees for risky work, and preserves any active workspace writer claims.
- Do not deploy, restart production services, alter Cloudflare, publish Plugins, delete unrelated files or migrate live databases without explicit authorization.
- Native tool/model inventory and API support must be verified against the active installed versions.
- Every milestone needs actual executed test evidence. Unit tests are not a substitute for Web/Mobile remote E2E.
- Never claim a release is complete without independent acceptance and an explicit GO.
