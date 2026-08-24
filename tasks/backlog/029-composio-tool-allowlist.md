---
id: 029-composio-tool-allowlist
feature: integrations
status: in-review
---

# Composio tool enable allowlist

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Replace the Direct Tools **disable** catalog dump with an **enable-only**
allowlist at MCP session creation (ADR 0009). Connected contacts stop
shipping ~180 Composio schemas (~150k input tokens) into Gemini. Email
send is `GMAIL_SEND_DRAFT` only: always create a draft, show it on
WhatsApp, wait for an explicit yes on a **later** turn, then send.

## Locked allowlist

Exact slugs. Session create rejects unknown names — if Composio renamed
one, fix the list (do not silently drop the toolkit). Do **not** add
siblings “just in case.”

| Toolkit | Enable | Product |
|---|---|---|
| `gmail` | `GMAIL_FETCH_EMAILS` | buscar |
| | `GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID` | ler |
| | `GMAIL_CREATE_EMAIL_DRAFT` | rascunho |
| | `GMAIL_SEND_DRAFT` | enviar o rascunho já confirmado |
| `googlecalendar` | `GOOGLECALENDAR_LIST_CALENDARS` | listar agendas |
| | `GOOGLECALENDAR_EVENTS_LIST` | listar eventos |
| | `GOOGLECALENDAR_CREATE_EVENT` | criar evento |
| `googlesheets` | `GOOGLESHEETS_SEARCH_SPREADSHEETS` | achar planilha |
| | `GOOGLESHEETS_GET_SHEET_NAMES` | abas (cadeia de ID pro range) |
| | `GOOGLESHEETS_VALUES_GET` | ler range |
| | `GOOGLESHEETS_SPREADSHEETS_VALUES_APPEND` | append |
| | `GOOGLESHEETS_VALUES_UPDATE` | update |
| `notion` | `NOTION_SEARCH_NOTION_PAGE` | search |
| | `NOTION_FETCH_DATA` | fetch (lista/itens) |
| | `NOTION_FETCH_BLOCK_CONTENTS` | fetch (conteúdo da página) |
| | `NOTION_CREATE_NOTION_PAGE` | criar página |
| `googledrive` | `GOOGLEDRIVE_FIND_FILE` | search |
| | `GOOGLEDRIVE_GET_FILE_METADATA` | metadata |
| `trello` | `TRELLO_GET_MEMBERS_ME_BOARDS` | achar board |
| | `TRELLO_GET_BOARDS_LISTS_BY_ID_BOARD` | listas (cadeia de ID pro card) |
| | `TRELLO_GET_BOARDS_CARDS_BY_ID_BOARD` | ler cards |
| | `TRELLO_ADD_CARDS` | criar card |
| | `TRELLO_UPDATE_CARDS_BY_ID_CARD` | mover / renomear / due |
| `clickup` | `CLICKUP_GET_AUTHORIZED_TEAMS_WORKSPACES` | workspace |
| | `CLICKUP_GET_FILTERED_TEAM_TASKS` | buscar tarefas (traz `list_id`) |
| | `CLICKUP_CREATE_TASK` | criar tarefa |
| | `CLICKUP_UPDATE_TASK` | status / due |

**Never enable:** `GMAIL_SEND_EMAIL`, `GMAIL_REPLY_TO_THREAD`, any
delete/trash slug. Direct send without a draft must be impossible at
the session filter, not only in the prompt.

**Trello / ClickUp:** added during implementation — a connectable app
with no tools is a dead end in the chat. ClickUp skips the
space/folder/list walk on purpose: `GET_FILTERED_TEAM_TASKS` already
returns the `list_id` that `CREATE_TASK` needs, and the full hierarchy
would spend the turn's `request_limit=6` before the reply. Cost: a list
with no tasks yet is not addressable by name.

A toolkit with no allowlist is still **omitted** from `toolkits=` /
`tools=` (empty `enable` under Direct Tools can dump the whole
catalog) — the guard stays for the next provider someone registers.

## Send contract (prompt, not `pending_action`)

1. Pediu pra mandar e-mail → `GMAIL_CREATE_EMAIL_DRAFT` nesta turno.
2. Mostrar no WhatsApp: para, assunto, resumo do corpo. Pedir sim/não.
3. Só no turno **seguinte**, depois de um sim explícito →
   `GMAIL_SEND_DRAFT` com o `draft_id` da proposta.
4. Sem sim → não chama send. “manda” na mesma frase do rascunho não
   conta como confirmação.

Hard HITL via `pending_action` (task 011, unwired after 025) is **out
of scope**. Structural guarantee this task: no `GMAIL_SEND_EMAIL`.

## Acceptance criteria
- [x] `ENABLED_TOOLS_BY_TOOLKIT` in `app/integrations/composio_policy.py`
      matches the table above. `tools_session_config(toolkits)` returns
      `{slug: {"enable": [...]}}` only for toolkits in the map with a
      non-empty list. Drop `DENIED_TOOLS_BY_TOOLKIT` and
      `tools_disable_config`.
- [x] `create_mcp_session` passes that config (no `disable` key).
      `SESSION_PRESET_DIRECT_TOOLS`, `manage_connections=False`,
      `user_id=str(contact_id)` unchanged.
- [x] `is_denied_composio_tool` stays as a **test-only** matcher:
      `GMAIL_SEND_EMAIL`, `GMAIL_REPLY_*`, delete/trash still match;
      `GMAIL_SEND_DRAFT` does **not**. Assert every enabled slug fails
      the matcher.
- [x] Tests: `client.create` kwargs for `["gmail", "notion"]` contain
      only the locked enable lists; `GMAIL_SEND_EMAIL` absent;
      `GMAIL_SEND_DRAFT` present; `["trello"]` alone → no trello
      enable (and trello not in `toolkits` if that is how omit works).
      Rewrite `test_create_mcp_session_passes_deny_list_and_tenancy`.
- [x] `app/agent/system_prompt.md`: e-mail pode buscar/ler/rascunhar/
      enviar; enviar só depois de rascunho + sim num turno seguinte.
      Deletes continuam bloqueados. Não nomear slugs no prompt.
- [x] ADR 0009 / glossary already describe enable-only; tweak if the
      code names differ. Do not implement the agent/subagent split.
- [x] Trello + ClickUp have their own small enable lists; every provider
      in `PROVIDERS` has one (unit test asserts the sets match).
- [ ] Manual: contact with Gmail+Agenda+Sheets+Notion — Logfire
      `chat gemini-*` `function_tools` count is the allowlist + local
      tools (~25), not ~191; `"oi"` is thousands of input tokens, not
      ~157k. Draft-then-confirm send works; a same-turn “escreve e
      manda” must not call `GMAIL_SEND_DRAFT` before a later yes.

## Out of scope
- Chat agent vs execution subagent (ADR 0009 later).
- `MCPToolset.defer_loading()` / Composio meta-tools.
- Rewiring `pending_action` / `confirm_pending_action`.
- Drive file write, Calendar update/delete, Gmail labels/filters, Notion
  databases schema, ClickUp space/folder/list browsing, Trello
  checklists/labels/members.
- Prompt-caching the tool prefix.

## Depends on
- **024** / **025** (done): MCP session + Composio-only tools.
- **0009** (docs): enable-only policy.

## Log
### [PA] 2026-08-15 15:22 — Superseded architecture
ADR 0015 replaces MCP catalogs with owned business tools over Composio
managed auth. Keep this safety layer until task 047 removes the legacy
MCP runtime after Gmail/Calendar cutover.

### [PA] 2026-08-12 21:15 — Grooming
Allowlist locked from product cut (Gmail search/read/draft/send-draft,
Agenda list/create, Sheets find/read/append/update, Notion
search/fetch/create page, Drive search/metadata). Deny-list removed
from the session. Send confirmation is prompt + no `GMAIL_SEND_EMAIL`;
`pending_action` left for a later task.

### [PA] 2026-08-12 — Implementation
`ENABLED_TOOLS_BY_TOOLKIT` + `tools_session_config` / `allowlisted_toolkits`
replace the deny-list; `is_denied_composio_tool` is now test-only and lets
`GMAIL_SEND_DRAFT` through. Toolkits with no list are dropped before
session create (`build_mcp_toolset` returns None when nothing remains), and
`inject_connected_integrations` tells the model an inert app has no actions
so it stops promising Trello/ClickUp work.

Verified live against Composio (contact 3, every registered toolkit): all
27 slugs resolve in the catalog and the MCP session lists exactly those 27.
With 8 local tools that is 35 function tools per turn instead of 191 —
~130 KB of schema for a contact connected to everything, against ~690 KB.
Remaining: the manual Logfire token check on a real WhatsApp turn and the
draft-then-confirm send walkthrough.

Trello and ClickUp got lists after grooming (see Locked allowlist). Since
no registered provider is inert now, the prompt says nothing about apps
without actions; `test_every_connectable_provider_has_an_allowlist` is the
tripwire if someone adds a provider without slugs.
