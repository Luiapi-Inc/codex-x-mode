---
name: codex-x-app-tool
description: Use when the user wants Codex app thread orchestration through the bundled codex-x-app MCP surface, including listing, reading, creating, forking, messaging, titling, archiving, or waiting on Native Codex threads.
---

# Codex X App Tool

Use the bundled `codex-x-app` MCP server shipped with Codex X Mode for Native Codex thread orchestration.

## Runtime boundary

- This Skill and its MCP implementation are part of the Codex X Mode repository and release package.
- Do not require or invoke the external `codex-app-tools@openai-bundled` package at runtime.
- Native Codex owns authentication, thread/turn execution, model selection, and inference.
- Codex X Mode only adapts MCP calls to Native Codex app-server protocol and enforces fail-closed safety.
- The supported protocol baseline for this release is `codex-cli 0.160.1`.

## Available tools

The `codex-x-app` namespace exposes the native-backed subset that Codex 0.160.1 supports directly:

- `list_threads`
- `read_thread`
- `create_thread`
- `fork_thread`
- `send_message_to_thread`
- `set_thread_title`
- `set_thread_archived`
- `wait_threads`

Do not claim support for `automation_update`, `set_thread_pinned`, or `handoff_thread` unless a future Native Codex protocol version provides a real backend primitive and the repository adds tested support.

## Operating rules

- Prefer read-only thread creation unless the caller explicitly requests workspace writes and the selected project allows them.
- Never silently route a thread to a custom provider, SIWC, Subscription Sharing, browser automation, or another plugin.
- Reject non-native/custom-provider threads rather than mutating them through this surface.
- For active turns, `send_message_to_thread` may steer the active turn when the Native Codex protocol supports it; otherwise start a new turn.
- Preserve exact thread identity across follow-up calls.
- The runtime owns one lazy Native Codex `app-server --stdio` process and reuses it across tool calls; no daemon/proxy or browser dependency is required.
- Treat app-server or protocol errors as failures; do not fabricate success.
