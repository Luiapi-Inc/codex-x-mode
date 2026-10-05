# Codex X Mode

v0.2.11 integrates Sign in with ChatGPT (SIWC) credentials and dispatch routing,
and makes the authenticated HTTP MCP bridge compatible with Secure MCP Tunnel
no-OAuth discovery by returning 404 for unadvertised MCP/OAuth GET probes.
HTTP MCP/Actions tasks use the ChatGPT-plan Responses provider; local stdio
tasks keep the Codex app-server backend. Backend choice is server-controlled.
New web tasks require protected credentials and an exact token-scoped model
selection before queue acceptance. Retries recover the original job first.

Private plugin สำหรับ ChatGPT/Codex ที่รวม **skill + MCP tools + bridge runtime** ไว้ใน package เดียว ไม่ต้องพึ่ง plugin หรือ skill อื่นเพื่อทำ Backend/Dispatch workflow หลัก

| โหมด | ใช้เมื่อ | Native MCP ที่ใช้ |
| --- | --- | --- |
| Backend | รับ model turn ที่ Codex local provider รอคำตอบอยู่ | `codex_x_claim_backend_turn` → `codex_x_read_backend_context` → `codex_x_complete_backend_turn` / `codex_x_cancel_backend_turn` |
| Dispatch | ส่งงานที่ผู้ใช้อนุญาตให้ Codex ทำใน project allowlist | `codex_x_create_task` → `codex_x_read_task` → `codex_x_continue_task` / `codex_x_cancel_task` |

มีทั้งหมด 13 tools พร้อม 2 resources และ 2 prompts. Web อ่าน model catalog ของ account ด้วย OAuth access token; local stdio อ่าน Codex app-server catalog. Catalog ไม่ยืนยัน inference entitlement; `inference_verified` ต้องมี completed turn และ terminal model identity ตรงกันโดยไม่มี reroute

SIWC และ web dispatch เชื่อมแล้วใน source แต่ยังไม่ผ่าน real OpenAI inference หรือ public HTTPS acceptance. สถานะใช้ `web_executor: implemented_unverified`. การเลือก model ใน bridge ไม่เปลี่ยน model ของบทสนทนา ChatGPT และการอัปโหลด plugin ไม่ deploy bridge

v0.2.5 adds a bundled Python MCP client, read-only readiness CLI and real
HTTP/stdio integration tests. HTTP accepts modern requests and stateless
initialize-handshake clients. See [web connection](server/WEB-CONNECTION.md):
public HTTPS deployment and ChatGPT acceptance are separate from package
publication; mobile availability requires verification in the actual host.

## Local setup

Plugin มี `mcp.json` ที่รัน bundled stdio server จาก `server/` โดยตรงใน host ที่รองรับ local MCP process เช่น Codex/Desktop host ที่ติดตั้ง plugin แบบ local

```bash
cd <plugin-root>/server
python3 -m bridge setup --project demo --cwd /absolute/path/to/repo
python3 -m bridge mcp-stdio
```

ค่า config ปกติอยู่ที่ `~/.config/codex-x-mode/bridge-private.json` (mode 600) และเก็บ `gpt_key`, `provider_key`, `mcp_key` แยกกัน หากมี `bridge-private.json` ใน current directory จะใช้เพื่อ backward compatibility; override ได้ด้วย `CODEX_X_MODE_CONFIG`

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

- `/mcp` — MCP `2026-07-28`, Bearer `mcp_key`
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

See `server/VALIDATION.md` for v0.2.11 validation and `server/SIWC.md` for
authorization, protected import, routing and recovery. Real OpenAI OAuth,
real Codex inference, public HTTPS acceptance and Web → Codex → Web remain
**NOT RUN**.
Package publication is separate from bridge deployment; the connected service
was last observed at 0.2.3 with `unknown_projects=["smoke"]`.
