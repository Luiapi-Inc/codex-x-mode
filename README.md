# Codex X Mode

v0.2.22 preserves the intended execution boundary, adds stable public MCP path aliases for the Cloudflare Named Tunnel, and hardens its contract: Codex X Mode is an orchestrator/bridge, while Native Codex owns ChatGPT authentication, `model/list`, reasoning metadata, `thread/start`, `turn/start`, inference, and terminal model identity. Web model exposure is the packaged registry intersected with Native Codex models for the signed-in account. The packaged aliases currently include `chatgpt-web/5.5`, `chatgpt-web/5.6-luna`, and `chatgpt-web/5.6-sol`; models outside package policy stay hidden. New Web and local tasks both use `codex_app_server`; SIWC/Subscription Sharing and custom Responses-provider routing are legacy-recovery only. The primary route remains headless with no Chromium, Playwright, browser profile, browser daemon, `codex-chatgpt-web`, or second connector. Exact terminal model identity remains fail-closed when absent or rerouted.

Private plugin สำหรับ ChatGPT/Codex ที่รวม **skill + MCP tools + bridge runtime** ไว้ใน package เดียว ไม่ต้องพึ่ง plugin หรือ skill อื่นเพื่อทำ Backend/Dispatch workflow หลัก

| โหมด | ใช้เมื่อ | Native MCP ที่ใช้ |
| --- | --- | --- |
| Backend | รับ model turn ที่ Codex local provider รอคำตอบอยู่ | `codex_x_claim_backend_turn` → `codex_x_read_backend_context` → `codex_x_complete_backend_turn` / `codex_x_cancel_backend_turn` |
| Dispatch | ส่งงานที่ผู้ใช้อนุญาตให้ Codex ทำใน project allowlist | `codex_x_create_task` → `codex_x_read_task` → `codex_x_continue_task` / `codex_x_cancel_task` |

มีทั้งหมด 13 tools พร้อม 2 resources และ 2 prompts. ทั้ง Web และ local dispatch อ่าน model catalog จาก Native Codex app-server; Web surface ใช้ packaged Web-model registry ตัดกับ Native Codex `model/list` เพื่อ expose เฉพาะ alias ที่ package รองรับและบัญชี Native Codex มองเห็น. Package รุ่นถัดไปสามารถเพิ่ม model ใหม่ เช่น Pro ผ่าน registry โดยไม่แก้ routing core. Catalog visibility ยังไม่ใช่ inference proof; `inference_verified` ต้องมี completed turn และ terminal model identity ตรงกันโดยไม่มี reroute

Web dispatch ผ่าน fixture/package validation แล้ว แต่ยังไม่ผ่าน clean-install self-contained acceptance หรือ real Web → Bridge → Native Codex → ChatGPT Web terminal verification. สถานะจึงยังใช้ `web_executor: implemented_unverified` และ `live_codex_verified=false`. การเลือก model ใน bridge ไม่เปลี่ยน model ของบทสนทนา ChatGPT และการอัปโหลด plugin ไม่ deploy bridge

v0.2.5 adds a bundled Python MCP client, read-only readiness CLI and real
HTTP/stdio integration tests. HTTP accepts modern requests and stateless
initialize-handshake clients. See [web connection](server/WEB-CONNECTION.md):
public HTTPS deployment and ChatGPT acceptance are separate from package
publication; mobile availability requires verification in the actual host.

## Local setup

Plugin มี authoritative portable `mcp.json` ที่ประกาศ MCP สอง surface จาก runtime เดียวกันและติดตั้ง MCP identities มากับ Plugin โดยตรง ไม่ต้องเพิ่ม MCP ซ้ำใน user-level Codex config: `codex-x-mode` ที่ loopback `/mcp` และ `codex-x-app` ที่ loopback `/codex-x-app/mcp`. ทั้งคู่ไม่ฝัง credential; compatibility `.mcp.json` อ้าง `CODEX_X_MCP_TOKEN` เป็น `bearer_token_env_var`. Execution host expose remote aliases ผ่าน Cloudflare Named Tunnel ที่ `https://codex-x-mode.lott0.online/mode/mcp` และ `https://codex-x-mode.lott0.online/app/mcp`; remote authentication remains fail-closed and no static bearer secret is shipped in Plugin files. `codex-x-app` ใช้ Skill `codex-x-app-tool` ที่ ship ในโปรเจกต์นี้เองและ map ไป Native Codex app-server protocol โดยตรง ไม่พึ่ง `codex-app-tools@openai-bundled` ที่ runtime.

```bash
cd <plugin-root>/server
python3 -m bridge setup --project demo --cwd /absolute/path/to/repo
python3 -m bridge mcp-stdio
```

Standalone stdio ยังใช้ได้เมื่อไม่มี bridge instance อื่นถือ config lock. ค่า config ปกติอยู่ที่ `~/.config/codex-x-mode/bridge-private.json` (mode 600) และเก็บ `gpt_key`, `provider_key`, `mcp_key` แยกกัน หากมี `bridge-private.json` ใน current directory จะใช้เพื่อ backward compatibility; override ได้ด้วย `CODEX_X_MODE_CONFIG`.

## Codex model picker

MCP `tools/list` ไม่ได้ register model เข้า Codex host picker. `python3 -m bridge codex-catalog` ยังคงเป็น compatibility utility สำหรับสร้าง alias catalog จาก metadata ของ Codex ที่ติดตั้งอยู่ แต่ไม่ใช่ inference provider. ตั้งแต่ v0.2.20 `python3 -m bridge codex` บังคับใช้ Native Codex `openai` provider โดยตรงและไม่ตั้ง `custom_gpt_bridge`, ไม่ inject bridge provider secret และไม่ route inference กลับเข้า `/v1` ของ Codex X Mode.

Codex Desktop ไม่ควรถูกบังคับให้ใช้ `custom_gpt_bridge` หรือ user-level alias catalog เพื่อ inference. Native Codex auth/provider เป็น source of truth สำหรับการรันโมเดล; Codex X Mode ใช้ alias เฉพาะใน orchestration surface และ map ไป exact Native Codex model ID ก่อน execution. Inference PASS ต้องมี terminal model identity ตรง exact model และไม่มี reroute.

## Native MCP surface

- `codex_x_status`
- `codex_x_list_projects`
- `codex_x_list_models`
- `codex_x_list_project_directory`
- `codex_x_read_project_file`
- `codex_x_create_task`
- `codex_x_read_task`
- `codex_x_continue_task`
- `codex_x_cancel_task`
- `codex_x_claim_backend_turn`
- `codex_x_read_backend_context`
- `codex_x_complete_backend_turn`
- `codex_x_cancel_backend_turn`

Project reads walk from an opened project-root directory descriptor, reject traversal and symlink components, and keep subsequent opens anchored to the validated directories. File reads enforce the byte limit on the opened descriptor; directory scans inspect at most `limit + 1` entries. `workspace-write` ต้องถูกเปิดใน config ของ project นั้นก่อน

Backend recovery ใน v0.2.2 ใช้ stable `request_key` สำหรับ **ทั้ง successful และ null claim result**: retry key เดิมจะไม่สามารถไปจับ turn ที่เข้าคิวภายหลังได้ และทุก context read ต้องส่ง lease ของ turn ที่ claim ไว้ก่อนเสมอ

Task ที่มี execution state `unknown` ยังคงอยู่เพื่อ reconcile; read-only tasks ทำต่อได้ภายใต้ sandbox ส่วน workspace writes ถูก block เฉพาะเมื่อ resource identity ชนกับ unknown possible writer

## HTTP MCP / GPT Actions

`python3 -m bridge serve` เปิด loopback service ที่ `127.0.0.1:8240`:

- `/mcp` — Codex X Mode MCP `2026-07-28`, Bearer `mcp_key`
- `/codex-x-app/mcp` — Codex X App MCP, Bearer `mcp_key`; native-backed thread tools for Codex CLI 0.160.1
- Actions routes เช่น `/status`, `/projects`, `/models`, `/tasks`, `/backend/...` — Bearer `gpt_key`
- private Codex Responses provider `/v1/...` — Bearer `provider_key`

สำหรับ web/mobile ต้อง deploy/reverse-proxy bridge ไป HTTPS endpoint ที่ผู้ใช้ควบคุม แล้ว bind endpoint จริงใน host configuration. Package นี้ **ไม่ invent หรือฝัง public URL** และ account upload เพียงอย่างเดียวไม่ทำให้ local stdio process เข้าถึงได้จาก web/mobile

สร้าง OpenAPI หลังมี HTTPS endpoint จริง:

```bash
python3 -m bridge schema --url https://YOUR-ACTUAL-BRIDGE-HOST --output openapi.json
```

อย่า expose `/v1/` ออก public proxy และอย่าใส่ keys/leases ใน Instructions, Knowledge, chat หรือ user-visible logs

## Custom GPT

`skills/codex-x-mode/assets/custom-gpt-instructions.txt` เป็น Instructions สำหรับ Custom GPT แยกต่างหาก Custom GPT ควรใช้ native bridge Actions/MCP ของ Codex X Mode; adapters ภายนอกเป็น optional compatibility เท่านั้น ไม่ใช่ dependency ของ workflow นี้

## Verification

See `server/VALIDATION.md` and `docs/evidence/2026-10-05-v0.2.13-headless.md` for v0.2.13 package evidence. Package/unit/integration validation and a live read-only account-catalog probe are green, but full architecture acceptance is still incomplete until a real harmless terminal task succeeds through the deployed Web connector with exact underlying model identity and no reroute. Mobile follows the same remote MCP/tunnel path and requires an actual ChatGPT Mobile smoke before being marked verified. Package publication is separate from runtime deployment.
