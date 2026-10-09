# Codex X Mode v1.0 — Master Development Plan

**วันที่:** 2026-10-09
**สถานะ:** APPROVED SCOPE / READY FOR IMPLEMENTATION PLANNING (ยังไม่เริ่มเขียนโค้ด)
**Repository:** Luiapi-Inc/codex-x-mode
**เครื่องพัฒนา:** /Users/luiapi/codex-x-mode
**เจ้าของระบบ:** Single Operator — เขียนเอง ใช้เอง
**วิธีทำงาน:** Heavy Route, Main Coordinator 1 ตัว, 12 Workstreams, เริ่ม 3–6 Workers พร้อมกันตามทรัพยากรจริง

> เอกสารนี้เป็นแผนพัฒนา v1.0 ฉบับหลัก ไม่ใช่แผนซ่อม v0.2.x, ไม่ใช่คำสั่งให้ Dispatch หรือ Deploy และไม่ใช่หลักฐานว่าฟีเจอร์ v1 ติดตั้งแล้ว

## 1. เป้าหมาย v1 — ไม่เปลี่ยนขอบเขต

สร้าง Codex X Mode เดิมให้เป็นแพลตฟอร์มส่วนตัวที่ ChatGPT เชื่อม **MCP เพียงตัวเดียว**, ใช้ Core + Codex X App, สั่ง Native Codex, ใช้ Serena ช่วยอ่าน/แก้โค้ด, รองรับ ChatGPT Direct และ ChatGPT Agent Dispatch, ติดตามงานอย่างทนทาน และบริหารผ่าน **Local/Remote Web Dashboard**.

**ต้องมี:** Single MCP Gateway; 13 Core tools + 8 Codex X App tools ที่ทำงานได้จริง; Native Codex execution; optional Serena; ChatGPT Direct; Agent Dispatch ที่ผ่าน provider eligibility/ผลลัพธ์จริง; Durable Jobs & Recovery; Dashboard + Config Management; Secure Remote; Web/Mobile E2E; release/rollback.

**ไม่ทำใน v1:** multi-tenant, RBAC หลายระดับสำหรับหลายองค์กร, billing, mission/swarm autopilot เต็มระบบ, Kubernetes, database server ใหม่, cloud inference host ใหม่, UI plugin แยกตัว, การบังคับพึ่ง Hermes-GPT/codex_workflow/Serena ภายนอกเพื่อให้ Core ทำงาน.

## 2. Architecture ที่เลือก

    ChatGPT Web / Mobile
              |
       ONE MCP CONNECTION
              |
    /mode/mcp — Unified Gateway
       |       |        |
     Core   Codex App   Optional Serena / Direct Tools
       \       |        /
        Tool Router + Policy
              |
      Durable Dispatch Manager
         |             |
     Native Codex   ChatGPT Agent Provider (optional / capability-gated)
              |
       SQLite Job & Evidence State

    Local Browser ----------> Dashboard (static React)
    Remote Browser -> Cloudflare Access -> Dashboard
                                      |
                               Python Admin API
                                      |
                       Config / Policy / Audit / State

- ChatGPT-facing canonical endpoint: https://codex-x-mode.lott0.online/mode/mcp. ช่วงเปลี่ยนผ่าน /app/mcp เป็น compatibility alias ได้ แต่ไม่บังคับให้ ChatGPT ต่อสอง MCP.
- แยก **MCP execution plane** ออกจาก **Admin control plane**; ใช้สถานะ/นโยบายร่วมกันแต่ไม่ใช้ credentials ร่วมกันโดยปริยาย.
- ไม่บังคับ browser automation, external MCP/plugin/skill เป็น Core Runtime dependency.
- Native Codex เป็นเจ้าของการยืนยันตัวตน, model/list, reasoning effort, thread/turn, inference และ terminal identity ของเส้นทาง Native Codex.
- ChatGPT Direct = ChatGPT ในบทสนทนาปัจจุบันคิดและเรียกเครื่องมือเอง ไม่ใช่ background copy ของแชตนี้.
- ChatGPT Agent Dispatch ต้องมี provider ที่เข้าถึงได้จริง มี auth, provider run ID, terminal evidence และ result retrieval/callback ที่พิสูจน์ได้; ถ้ายังไม่มีต้องแสดง BLOCKED ไม่แอบ route ไป provider อื่น.

## 3. Dashboard — ทำด้วยอะไรและใช้งานอย่างไร

| ส่วน | Stack / วิธี |
|---|---|
| UI | React + TypeScript + Vite |
| Styles | Tailwind CSS; shadcn/ui ใช้เฉพาะชิ้นที่ช่วยลดงาน |
| Admin Backend | Python HTTP Bridge เดิม; ไม่ตั้ง backend ใหม่อีกตัว |
| Config | JSON config แบบ versioned schema + validation + atomic write/rollback; ห้ามฝัง secrets |
| Durable state | SQLite เดิม + migration เท่าที่จำเป็น |
| Live status | polling ก่อน; เพิ่ม SSE เมื่อจำเป็นจริง |
| Remote | Cloudflare Tunnel + Access, allowlist **ผู้ใช้งานคนเดียว**, MFA |
| Build | Node ใช้ build frontend เท่านั้น; deploy static assets ภายใน package/runtime |
| Local | localhost/loopback เท่านั้น |
| URL remote ที่เสนอ | admin.codex-x-mode.lott0.online (ยังไม่ได้ตั้งค่า) |

**หน้าที่ต้องมีใน v1:** Overview/Health, MCP Tools & Connections, Native Models, Projects & Write Permissions, Providers (Serena/ChatGPT Agent), Jobs/Unknown/Recovery, Configuration & Apply/Rollback, Audit/Errors.

**UX ที่ต้องใช้จริง:** เข้า Dashboard -> เห็นสถานะ -> แก้ค่า -> Validate -> Preview diff -> Apply -> ตรวจ Health -> ถ้าพัง Rollback. Action ที่เสี่ยงต่อไฟล์/credential ต้องยืนยันก่อนสั่ง. ไม่มี wizard หลายขั้นหรือ role management ที่ไม่จำเป็นสำหรับ single operator.

**Remote Security:** Cloudflare Access ป้องกันหน้าเว็บและ Admin API; origin ต้องตรวจ identity ที่เชื่อถือได้ฝั่ง server, ป้องกัน CSRF/การปลอม header, secret อยู่ฝั่ง host เท่านั้น. ไม่ expose Admin API แบบ anonymous และไม่ส่ง static bearer bridge key ไป browser. Remote MCP สำหรับ ChatGPT ต้องตรวจ secure tunnel หรือ OAuth ที่ host รองรับ **แยกจาก** browser Access; การเข้า Dashboard ผ่านได้ไม่พิสูจน์ว่า ChatGPT MCP เชื่อมได้.

## 4. Functional contracts ที่ต้องตกลงก่อน coding

**MCP Registry:** tools/list และ tools/call รวม Core + App ผ่าน gateway เดียว; tools ต้องไม่ชื่อชนกัน; คงชื่อ Core เดิม; App ใช้ namespace ชัดเจน; Serena/Agent อาจซ่อนเมื่อ disabled; route inspection เป็น read-only เสมอ.

**Projects/Models:** project allowlist, canonical resource ID, read-only/workspace-write แยกชัด; model alias ของ Web route map ไป exact Native Codex model ที่บัญชีแสดงจริง; ห้าม silent fallback และต้องตรวจ terminal model proof.

**Durable Dispatch record ขั้นต่ำ:** dispatch_id, request_key (idempotency), attempt_id, provider_id, project_id, canonical_resource_id, scope, provider_task_id, immutable work_contract_digest, state, evidence refs, created/updated time, acceptance verdict.

**Job states:** queued -> running -> verifying -> verified; ทางแยก failed / blocked / reconciling / cancelled. Unknown execution ไม่ใช่ failed. Timeout/cancel request ไม่ยืนยันว่าหยุดเขียน. ห้าม replay งานที่อาจยังรัน. Workspace-write claim ของ resource เดียวกันต้องมีเจ้าของเดียวจนกว่า terminal proof ตรวจแล้ว.

**Admin API contracts:** read health/tools/projects/models/providers/jobs/config และ admin-only mutate config/provider/project/reconcile (อย่างหลังต้องมีหลักฐาน). Every mutation: authorized -> validate -> persist atomically -> apply -> verify -> audit -> rollback on failure. Endpoint names และ JSON schema จริงให้ W04/W05/W09 ตกลงกันก่อนลงมือ.

**Provider contracts:** health, capability discovery, invoke, status/readback, cancel if supported, result, evidence; ไม่แปลงข้อจำกัดของ provider เป็นความสามารถที่ไม่มี. Serena อาจเป็น optional CLI/MCP adapter; ถ้า Serena down Core/App ยังต้องใช้งานได้.

## 5. 12 Workstreams — แบ่งแล้วทำได้จริง

| ID | เจ้าของงาน | พื้นที่แก้หลักเมื่ออนุญาต | สิ่งส่งมอบ / Done |
|---|---|---|---|
| W01 | Baseline & Recovery | อ่าน WIP, runtime, task evidence; ไม่แก้ production จน writer cleared | baseline, blocker list, authoritative write reconciliation, migration risk |
| W02 | MCP Gateway | gateway registry + shared MCP adapter | discover/call Core + App จาก connector เดียว, no collisions |
| W03 | Codex X App | app-server adapter | 8 thread tools, compatible error/status/thread semantics |
| W04 | Remote Auth & Security | auth/policy module | remote deny-by-default, single operator identity, scoped writes |
| W05 | Config & Durable State | config module, persistence migrations | schema, atomic apply/rollback, jobs, claim + crash recovery |
| W06 | Serena | Serena adapter | symbols, references, diagnostics, authorized edit; optional health |
| W07 | ChatGPT Direct | direct-tool boundaries | ChatGPT edit/read contract, no privilege escalation |
| W08 | Dashboard Frontend | dashboard/** | React UI, responsive/mobile browser, all v1 pages |
| W09 | Admin API & Remote | admin endpoint module, tunnel integration specification | secure API, identity verification, local/remote access |
| W10 | ChatGPT Agent Adapter | optional Agent provider module | capability/eligibility, dispatch/readback/result/reconcile |
| W11 | Work Contract / Events | dispatch contract/evidence/events modules | immutable contract, approval evidence, cursor events, idempotency |
| W12 | Independent QA & Release | new verification suites, release checklist | contract/security/browser/Web/Mobile/rollback evidence |

**Ownership:** ให้แต่ละ worker ถือสิทธิ์แก้ไฟล์ไม่ซ้ำกัน. Shared router (เช่น HTTP entrypoint), public manifests, SQLite migrations, generated bundles, deploy config และ release tags ให้ Coordinator / integration owner รวมเมื่อจบงาน; ห้าม W02/W09/W05 แก้ shared entrypoint พร้อมกัน. Worktree แยก branch เป็นเพียง isolation ของไฟล์ ไม่ใช่ของ DB, port หรือ remote.

**Heavy Route roles:** Executor implement, Tester ทดสอบแยก, Explorer/Investigator หาข้อมูล, Archivist สรุปเฉพาะหลักฐานจริง; Main Coordinator ตัดสินใจเรื่อง interfaces, conflicts, integration, acceptance. Senior Executor ใช้ได้สูงสุด 1 ตัวสำหรับโจทย์ยาก; จำนวน Workers รวมไม่มีเพดานถาวรจากแผน แต่เริ่ม **3–6 concurrent** ตาม capacity ของ Codex host.

## 6. Development Waves — ทำงานขนาน ไม่รอ G0 ทุกเรื่อง

| Wave | งานขนานที่เริ่มได้ | ต้องพร้อมก่อน | ผลจบรอบ |
|---|---|---|---|
| A — Contract & UI Design | W02, W04, W05, W06, W08, W10, W11 (3–6 read-only workers) | repo docs / architecture | agreed MCP/Admin/Provider contracts, Dashboard screens, write ownership |
| B — Core/Foundation | W02 + W03; W04 + W05; W06; W08 UI scaffold | write-conflict cleared for mutable paths, contracts agreed | unified gateway, state/auth core, optional Serena, UI skeleton |
| C — Providers & Dashboard | W07; W08; W09; W10; W11 | B interfaces and corresponding APIs ready | direct tools, full Dashboard, remote auth, Agent adapter, events |
| D — Verify & Release | W12 independent testers + Coordinator integrate | all implemented scopes ready | Web/Mobile E2E evidence, clean install, rollback, v1 release decision |

W01 runtime recovery เป็น workstream คู่ขนานตั้งแต่ Wave A: ไม่เป็นเหตุให้ UI, diagram, schema หรือ independent read-only work หยุด แต่ **ห้าม** competing write บน repository จน unknown writer ของ resource นั้น reconciled.

## 7. Gates แบบสั้น — เหลือ 3 ตัวที่มีผลจริง

**Gate A — Contracts Ready:** one-MCP registry, namespaces, provider interface, state/claim schema, API and UI wireframes ตกลงร่วมกัน; change impact + ownership แยกชัด.

**Gate B — Safe To Write/Integrate:** unknown workspace-write reconciled ด้วย authoritative evidence; WIP preserved; worktree/file/resource ownership ไม่ชน; admin remote auth ไม่ถูกลดความปลอดภัย. ถ้าไม่ผ่านให้ทำ design/independent read-only ต่อได้ แต่ห้าม source writes ที่ชน.

**Gate C — v1 Release:** signed-off tests and evidence: Core 13 + App 8; one ChatGPT MCP; native exact-model proof; actual Web and Mobile smoke; Serena failure isolation; dashboard local/remote; config rollback; single-user remote authentication; no unauthorized write; durable restart/reconcile; clean package; source/runtime/hosted version aligned; rollback rehearsed. ต้องอนุมัติ release/deploy แยก.

ไม่เพิ่มขั้นตอนพิธีการอื่น เว้นแต่พบความเสี่ยงที่ทดสอบแล้วแก้ไม่ได้.

## 8. Work Package Template สำหรับสั่ง Sub-agent

- Task ID / Workstream / ชื่อเรื่อง / เจ้าของ
- Goal และ expected user-visible result
- Inputs: exact docs, commit/branch, contracts, dependencies
- Exclusive paths/resources: allowed และ forbidden
- Required implementation / tests; acceptance criteria
- Permissions: read-only หรือ workspace-write ที่ได้รับอนุญาตเท่านั้น
- Report: changed files, test commands/results, evidence IDs, known failures, next dependency
- Do not spawn another worker or change API contract/permissions without Coordinator approval

**Dispatch rules:** batched independent packages only; no worker sharing mutable files; never treat a report as independent proof; verify before merging. Concurrency is adjustable, not a guarantee that the current connected MCP can spawn this many Agents.

## 9. Acceptance tests ที่ห้ามข้าม

| Area | Proof |
|---|---|
| MCP | Web and Mobile use one connector, Core 13 + App 8 tools discover/call successfully |
| Native Codex | real harmless Web read-only task ends with matching requested/selected/terminal model; no reroute |
| Serena | healthy symbolic navigation/edit test, disabled/unavailable does not break Core |
| ChatGPT Direct | scoped reads/edits, no unauthorized workspace write |
| Agent Dispatch | real authorized provider run ID, status, evidence/result return; or explicit blocker, never fake PASS |
| Dashboard | local + remote login, phone layout, health/tasks, config preview/apply/rollback |
| Security | anonymous and wrong identity blocked; project write scope enforced; no secrets in browser/logs |
| Durable jobs | request_key retry safe, unknown writer blocks conflict, restart and reconnect preserve state |
| Release | full tests, build reproducibility, docs, version alignment, rollback, actual Mobile smoke |

## 10. Start here — next implementation order

1. Coordinator freezes contracts and exclusive ownership; W01 investigates runtime/unknown writer in parallel, not as the whole project.
2. Authorize and launch Wave A with 3–6 available Sub-agents for **read-only** contracts/design; compare reports and choose interfaces.
3. Once Gate B is satisfied, authorize bounded implementation packages in Wave B/C; keep dirty v0.2.x WIP untouched until independently integrated.
4. W12 tests each increment; release only at Gate C with evidence.

**สิ่งที่ทำในเอกสารชุดนี้:** เพิ่มแผน v1 ฉบับเดียวลง main ตามคำสั่งผู้ใช้. **ไม่ได้:** แก้ production code, เปลี่ยน config, สร้าง sub-agents, ปลด unknown writer, deploy runtime หรืออ้างว่า v1 ผ่านการทดสอบแล้ว.
