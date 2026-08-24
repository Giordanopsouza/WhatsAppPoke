---
id: 043-owned-gmail-tools
feature: integrations
status: done
---

# Owned Gmail tools with code-enforced send confirmation

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Replace the Execution Agent's Gmail MCP surface with small owned business
tools over the authenticated proxy. Reads and drafts execute directly;
send is staged and requires a later WhatsApp confirmation.

## Acceptance criteria
- [x] Execution registry exposes owned typed tools only when Gmail is
      active for the contact: `search_emails`, `get_email`,
      `create_email_draft`, and `stage_send_email`.
- [x] Search supports bounded query/date/pagination inputs and returns
      compact metadata. It never puts full HTML bodies from a result list
      into model context.
- [x] Fetch returns one normalized message/thread with bounded plain-text
      content and stable provider ids needed by later actions.
- [x] Draft creation validates recipient, subject, body, and optional
      thread id; provider-specific payload construction stays behind the
      tool function.
- [x] `stage_send_email` stores the exact draft id + recipient/subject
      summary in `pending_action`, including source Interaction/execution
      id, payload hash, and expiry. It does not send.
- [x] A confirmation service claims one pending action only after an
      explicit later inbound, verifies kind/hash/expiry/contact, executes
      the fixed send operation, and reaches a terminal state atomically.
- [x] Same-turn “write and send it” does not count as later confirmation.
      Ambiguous yes/no with multiple pending actions asks which action.
- [x] No direct-send, reply, delete, trash, arbitrary proxy, or remote
      Composio schema is registered.
- [x] Tool results and telemetry contain no auth token and no unbounded
      email body. PII bodies are not logged.
- [x] Unit/contract tests cover disconnected Gmail, search normalization,
      HTML bounding, draft, expired/wrong-contact confirmation, duplicate
      confirmation, send failure/release policy, and successful later send.
- [x] Legacy Gmail MCP tests remain until task 047 cutover; new tests
      prove the owned path without a live Composio/Gmail call.

## Out of scope
- Gmail watcher / proactive important-email classification.
- Reply-to-thread and destructive operations.
- Multiple Gmail accounts per contact.
- Calendar tools.

## Depends on
- 039 (`pending_action` evolution if required).
- 041.
- 042.
- ADR 0015.

## Log
### [PA] 2026-08-15 15:22 — Grooming
The LLM sees business operations, not Composio action names. Sending is
a state transition authorized by a later inbound, never a prompt rule.

### [SWE] 2026-08-15 20:45 — Migration preflight and implementation
Target contract: Gmail-scoped Execution runs receive only four stable owned
business tools. Search and fetch call fixed Gmail endpoints through the
contact's verified Composio account, normalize provider payloads, and bound
all model-visible content. Draft creation owns MIME construction. Staging
persists the exact draft id, recipient/subject summary, payload hash, expiry,
and source Interaction/Execution ids without sending.

The legacy `composio_mcp.py`, Gmail remote schemas, tool allowlist, prompt
pins, and unflagged classifier/worker path remain only as the task 047
rollback bridge. New Gmail code is forbidden from importing MCP, registering
remote action names, exposing endpoint/method/body arguments, or providing
send/reply/delete/trash operations. Task 047 removes that bridge.
`tests/test_owned_gmail_tools.py`, `tests/test_composio_proxy.py`, and
`tests/test_execution_orchestration.py` enforce the registry, fixed-request,
bounded-body, staged-write, later-inbound, contact/hash/expiry, ambiguity,
duplicate, retry-release, and no-live-provider boundaries.

The Interaction Agent interprets natural-language confirmation from the
current user message, matching OpenPoke's conversational routing without a
hardcoded phrase parser. Its `confirm_email_send` orchestration tool is
available only on a real user inbound; an Execution-result re-entry is refused.
A same inbound therefore cannot stage and confirm its own send. Different
pending action kinds may coexist; a bare confirmation with more than one asks
which action, while the database claim atomically checks contact, kind, action
id, hash, expiry, state, and distinct turn. The model chooses when to attempt
confirmation, but cannot bypass that exact-draft state boundary.

### [Tester] 2026-08-15 20:48 — Passed
`uv run pytest` passed: 214 tests. `git diff --check` also passed. No live
Composio, Gmail, Gemini, or Twilio call ran.
