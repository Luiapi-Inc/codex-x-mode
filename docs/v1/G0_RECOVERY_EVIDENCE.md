# G0 — Recovery and Git Cleanup Evidence

Date: 2026-10-10 (Asia/Bangkok)
Scope: repository hygiene, verified backup, controlled SQLite recovery and v1 documentation baseline. No production deployment.

## Git outcomes

- Local and GitHub remote heads: `main` and `archive/v0.2.23-legacy` only.
- Legacy archive SHA: `4bc8407d2f8694e0ea746f74a8b294cccc26dbd9`.
- Baseline main before v1 docs: `aec8c903fa4c45f159b586779169868e2f23af44`.
- Safety backup: `~/codex-x-mode-v1-safety-20261010/all-refs.bundle`.
- Bundle verify: PASS; SHA-256 `65e1bd11d39037d5f6657be3333a94db43a7b38b9d19b23884fa9f913979169a`.
- Historical worktrees had patch/untracked backups, including full snapshots for dirty worktrees 06 and 09.
- One detached worktree `/Users/luiapi/.codex/worktrees/aefa/codex-x-mode` remains in use by active Node processes; do not remove while active.
- Unrelated main WIP retained untouched: `.gitignore`, `agent_docs/`, `agents/`.

## Writer reconciliation

- Task: `task_96251e21c3cd454c97bb2c392f9b7ebe`
- Exact thread: `01a11f99-0e6b-7ec1-88b1-74576b781ec6`
- Exact turn: `01a11f99-0ea1-7d82-87a7-92c88c04849f`
- Prior durable state: `unknown`, `workspace-write`.
- Source inspection: the old bridge persisted `codex_status=completed` only after receiving a matching Native `turn/completed` event. Terminal model field was missing, so `model_identity_verified=false`, `inference_verified=false`.
- Native `thread/read` from the standard home returned `thread not loaded`; model identity was not fabricated.
- Database backed up *before* guarded CAS repair: `~/codex-x-mode-v1-safety-20261010/runtime-sqlite-pre-reconcile-20261010.sqlite3` (SQLite `integrity_check=ok`).
- SQLite backup SHA-256: `69db83aed5237b47db1c84a748cb7f02d51df415c09b017204881f231429c434`.
- Guarded `BEGIN IMMEDIATE` transition: `unknown → failed`, only for exact matching task, resource, thread and turn with recorded terminal completion, absent model proof and no reroutes. Added reconciliation object to original result. **Not claimed as successful execution**.
- Subsequent live status verified `unknown_projects=[]` and four remaining read-only unknown tasks; `write_conflict=false`.

## Baseline validation

- Isolated staging path: `~/codex-x-mode-v1-staging-20261010`.
- Staging regression tests: 132/132 PASS (Python unittest).
- Staging Python compile check: PASS.
- v1 docs patch applied after `git apply --check` PASS. Patch SHA-256 `924c43743e8c7061e4ada796454fd4c1cefec03aa7de4851ff2e89e5daa75ccf`.
- Do not treat these baseline checks as v1 feature acceptance.
