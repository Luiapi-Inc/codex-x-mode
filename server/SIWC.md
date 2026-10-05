# Legacy SIWC / Subscription Sharing support

SIWC is **not** the primary inference path from v0.2.20 onward. New HTTP/Web and local dispatch both execute through Native Codex app-server, which owns ChatGPT authentication, model discovery, and inference. This module remains only for reconciliation of persisted legacy tasks and optional operator tooling. A caller-supplied backend is still rejected, and new work must never select `chatgpt_plan` or `chatgpt_web_headless`.

## Operator commands

Start from this package's server directory:

```bash
python3 -m bridge setup --project demo --cwd /absolute/path/to/repository
python3 -m bridge siwc-host-id
python3 -m bridge siwc-login
python3 -m bridge siwc-status
```

Setup persists a stable UUIDv4 host ID and an owner-only credential path. Login
opens the system browser with a loopback callback on the same computer. It
uses fresh PKCE, state and nonce, verifies the ID-token signature/claims and
granted plan scopes, then saves the issued client ID and credentials atomically.
Returning login reuses the saved registration and rejects a different account.
Each bridge configuration represents one selected account registration; use a
separate config/credential file for another account or workspace.

For a self-hosted VM, perform authorization locally with this same tool and
transfer the protected credential file through an authorized secure channel.
On the VM, create its host ID first and import without copying the laptop ID:

```bash
python3 -m bridge --config /absolute/vm/private.json siwc-host-id
python3 -m bridge --config /absolute/vm/private.json siwc-import --file /absolute/protected/import.siwc.json
```

Import requires a mode-600 regular owner-held file, a current verifiable ID
token and matching registration/subject. If the retained ID token expired,
reauthorize locally before import. This command does not transfer credentials
or deploy anything. Never put tokens in chat, command arguments, source control
or public logs. Retain protected runtime files across restarts.

## Exact model selection

Read the active backend's catalog through GET /models or codex_x_list_models.
HTTP uses the same account token for catalog and child inference. Only
visibility=list slugs can be selected; display names are presentation labels.
Send `model_version` as an exact packaged `chatgpt-web/*` alias, or use the family alias `chatgpt-web`. The private config key `chatgpt_web_default_model` (or `setup --model-version ALIAS`) is preferred when that alias is entitled; otherwise the bridge deterministically falls back to the highest-priority packaged alias visible in the authorized account catalog. There is no API-key billing fallback.

New task acceptance snapshots the selected model and issued account/client
identity, without tokens. A repeated request_key first recovers that snapshot
after verifying original input; changed prompt/model/backend conflicts.
Follow-ups preserve the actual parent selection and cannot switch backend or
account registration. Workers recheck account/model before launching the child.

## Token lifecycle and provenance

Credentials are owner-only and read through descriptors without symlinks.
Refresh is serialized across threads/processes per credential file. Replacement
access/refresh/ID tokens and expiry are stored together, with subject/client
binding and grant checks. Revoked refresh clears tokens and requires login.
The bridge restarts app-server per dispatched turn with the renewed token.

Only the child process receives ACCESS_TOKEN. It uses the documented public
Responses provider with requires_openai_auth=false, HTTP/SSE and zero provider
retries. Shell environment policy excludes the token from agent subprocesses;
known credential values are also redacted from stored terminal results.
Actual shell filtering/sandbox behavior needs verification with the live CLI.

A completed turn is accepted only with exact terminal model identity and no
reroute. Ambiguous execution remains unknown and is never replayed. Unknown
possible writers block only conflicting canonical resources; read-only and
independent roots remain available. Catalog listing is not entitlement proof.

## Live acceptance

The published package does not deploy the bridge. Before live inference,
confirm hosted-app eligibility, connect the authorized runtime, reconcile any
unknown writer on its resource, authorize SIWC in the browser, select an
account-listed model, and capture completed real model provenance.

Official references:

- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
- https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
- https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server
- https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations

Local fake-provider tests prove client behavior, not OpenAI authorization,
entitlement, public TLS deployment or ChatGPT host acceptance.
