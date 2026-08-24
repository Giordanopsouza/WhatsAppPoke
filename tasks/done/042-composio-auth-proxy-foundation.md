---
id: 042-composio-auth-proxy-foundation
feature: integrations
status: done
---

# Composio managed-auth proxy foundation

## Migration preflight

Before implementation, inspect the relevant sections of `docs/plan.md`, the governing ADRs, this task, and its directly dependent or consuming tasks. Record:

- target end-state and contracts introduced here;
- legacy code allowed only as a temporary rollback bridge;
- legacy imports, data paths, and behaviors forbidden in new code;
- the task that removes each temporary bridge;
- an architecture test or CI check that enforces the boundary.

## Scope
Add the backend-only **direct** authenticated-proxy adapter and owned-tool
registry foundation. The adapter calls `composio.tools.proxy(...)` with the
selected connected-account ID; it does not create an MCP or tool-router
session for proxy calls. Keep the existing Connect flow; do not expose a
generic proxy tool or remove the legacy MCP path until cutover.

## Acceptance criteria
- [x] Composio SDK wrapper verifies that the active
      `integration.external_account_id` belongs to
      `user_id=str(contact_id)` and the requested toolkit, then passes that
      exact account ID to `composio.tools.proxy(...)`.
- [x] A backend adapter accepts a fixed toolkit, endpoint, method, and
      normalized request from trusted code and calls the current Composio
      authenticated proxy API.
- [x] Adapter rejects missing/revoked/mismatched connected accounts
      before network execution.
- [x] Endpoint, method, headers, account id, and arbitrary body are not
      model arguments and no generic callable is registered with Pydantic.
- [x] Adapter normalizes Composio/provider errors into typed,
      non-sensitive failures. Logs include contact id, toolkit, owned tool
      name, status, and duration, never tokens or message bodies.
- [x] Owned-tool registry builds Pydantic callables from app-connected
      toolkits only. Gmail and Calendar may register empty placeholders
      until 043/044; other providers register nothing.
- [x] Existing Composio authorize/connect/disconnect/account-delete flow
      and one-account-per-contact+toolkit constraint continue unchanged.
- [x] Tests mock the current Composio SDK: correct direct-proxy account
      selection, revoked account rejection, endpoint not model-controlled,
      error redaction, and disconnected toolkit contributes no schema.
- [x] Dependency/API usage is verified against current Composio docs and
      pinned through the existing uv lockfile if a package update is
      required.

## Out of scope
- Gmail and Calendar operations (043/044).
- Raw token retrieval or custom OAuth applications.
- Generic GET/read-only escape hatch.
- Removing `composio_mcp.py` and allowlist policy (047).

## Depends on
- ADR 0015.

## Log
### [PA] 2026-08-15 15:22 — Grooming
Composio owns OAuth/refresh; our code owns schemas and fixed provider
calls. Proxy transport is infrastructure and never becomes an agent tool.

### [SWE] 2026-08-15 20:10 — Migration preflight and implementation
The target contract is a backend-only direct proxy that verifies the local
active integration pointer and Composio account tenancy before passing that
exact `ca_…` id to `composio.tools.proxy(...)`. The current MCP/session and
allowlist code remains solely as a rollback bridge through task 047, which
removes it. New owned-tool code must not import MCP, remote schemas, or the
allowlist policy, and must not make endpoint, method, headers, account id, or
body model-controlled. `tests/test_composio_proxy.py` enforces the direct
account selection, rejection, redaction, and schema-boundary contract.

Composio was upgraded and locked at 0.19.0 because the prior 0.18.2 SDK did
not expose the documented direct `tools.proxy(...)` API. Gmail and Calendar
have schema-less owned-registry placeholders; tasks 043 and 044 add their
business callables respectively.

### [Tester] 2026-08-15 20:12 — Passed
`uv run pytest` passed: 202 tests.
