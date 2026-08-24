0015. Owned business tools over Composio managed auth
Status: Accepted
Date: 2026-08-15

Context
ADR 0009 reduced the Composio MCP surface with an enable allowlist, and
ADR 0012 moved that surface behind a nested exec agent. The result still
creates an MCP session, imports remote schemas, pays tool-router
attachment cost, and depends on Composio action shapes inside the model
contract. Production traces show the execution path, not chat, is the
dominant latency problem.

The product needs Composio for per-contact OAuth, refresh, revocation,
and connected-account management. It does not need Composio to define
the LLM-facing schemas or choose which remote actions exist.

Composio's current Python SDK supports authenticated proxy execution:
our backend chooses a fixed toolkit endpoint and method while Composio
injects credentials for a selected connected account. Reading raw access
tokens is not the default security model and would require custom OAuth
configuration.

Decision
Keep Composio as the managed-auth provider and remove Composio MCP from
agent execution.

Authentication invariants remain:

- Composio `user_id` is always `str(contact_id)`;
- signed `/connect/{toolkit}` links remain contact-scoped and one-time;
- credentials remain at Composio;
- `integration.external_account_id` stores the selected `ca_…` id;
- the first release supports one active account per contact + toolkit;
- disconnect and account deletion continue to revoke the Composio
  connected account.

Implement LLM-facing tools as owned, typed Python business functions.
Each function owns:

- a stable Pydantic schema and narrow business name;
- validation and contact timezone handling;
- a fixed provider endpoint + HTTP method;
- compact normalized output, never raw unbounded provider payloads;
- Logfire spans without message bodies, tokens, or provider secrets;
- code-enforced read/write and confirmation policy;
- deterministic tests with the authenticated proxy mocked.

Owned tool implementations call the Composio authenticated proxy using
the contact's `external_account_id`. No generic proxy, URL, endpoint,
method, or arbitrary headers/body tool is exposed to Interaction or
Execution.

Execution receives only owned tools for integrations currently active
for that contact. Disconnected toolkits contribute no schemas. The first
migration slice is Gmail and Google Calendar:

- Gmail read, fetch, draft, and confirmed draft send;
- Calendar list/read and confirmed event creation.

Drive, Sheets, Notion, Trello, and ClickUp remain connectable only until
their owned tools are implemented in later tasks. The Interaction Agent
must not claim it can operate an app merely because an old integration
row exists.

Sensitive writes use `pending_action` as the security boundary. A tool
stages the exact normalized payload, source turn, hash, expiry, and kind.
A separate later inbound must explicitly confirm it before a claimed
action executes. Prompt text cannot bypass this state machine. Generic
delete/trash/reply operations do not exist in the first registry.

Remove after the owned tools cut over:

- `MCPToolset` construction and attach hooks;
- Composio session tool allowlist/deny-list policy;
- remote tool names from prompts and tests;
- `ask_execution`'s MCP session setup;
- Gmail argument pins that existed only to constrain remote schemas.

Consequences
Positive:
- small, stable tool schemas reduce model context and attachment cost;
- provider payloads are normalized before entering model context;
- security is determined by available callables and state, not a remote
  catalog or prompt;
- tool contracts are unit-testable without an MCP server;
- Composio still owns OAuth refresh and revocation.

Negative:
- we own provider endpoint mappings, pagination, errors, and API drift;
- every new operation requires code and tests;
- authenticated proxy calls still depend on Composio availability;
- existing non-Gmail/Calendar integrations temporarily have no execution
  tools after the cutover;
- a Composio SDK or provider API change can break a wrapper.

Rejected alternatives
- Load every Composio tool directly: recreates schema/token cost and
  gives the model actions the product did not design.
- Keep MCP with a smaller allowlist: does not remove router/session
  overhead or remote schema ownership.
- Read raw OAuth tokens and call providers directly: expands secret
  handling and needs custom OAuth configuration without a current benefit.
- Expose a generic authenticated proxy tool: makes endpoint and method
  selection a model-controlled security boundary.
- Prompt-only send confirmation: cannot guarantee that sensitive writes
  wait for a later inbound.

Supersedes
- ADR 0009.
- ADR 0012's Composio MCP execution details.
- ADR 0008 only where it says SaaS tools are supplied by MCP. ADR 0008's
  Composio-only authentication/connect decision remains accepted.

Implementation
Tasks 042, 043, and 044.
